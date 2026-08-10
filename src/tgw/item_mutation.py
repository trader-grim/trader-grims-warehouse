"""Generation-fenced, durable transactions for one ItemData document.

This module deliberately stops at the local item/projection boundary.  It does
not enqueue work or call providers.  Callers supply both the pure document
mutation and the synchronous SQLite projection operation.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .items import atomic_write_json

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
DocumentMutator = Callable[[dict[str, JsonValue]], dict[str, JsonValue]]
ProjectionWriter = Callable[[str, dict[str, JsonValue]], None]


@dataclass(frozen=True)
class MutationReceipt:
    operation_id: str
    sku: str
    kind: str
    expected_generation: str
    status: str
    observed_generation: str | None
    resulting_generation: str | None
    detail: str | None
    recorded_at: str


def _json_native(value: Any, path: str = "payload") -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _json_native(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string object key")
            _json_native(child, f"{path}.{key}")
        return
    raise TypeError(f"{path} contains non-JSON value {type(value).__name__}")


def _canonical(value: Any) -> bytes:
    _json_native(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def item_generation(document: Mapping[str, JsonValue]) -> str:
    """Return the evaluator-compatible SHA-256 generation of an item."""
    return hashlib.sha256(_canonical(dict(document))).hexdigest()


def operation_identity(
    *, sku: str, kind: str, expected_generation: str, payload: JsonValue
) -> str:
    """Bind an operation to its exact JSON-native inputs."""
    binding = {
        "expected_generation": expected_generation,
        "kind": kind,
        "payload": payload,
        "sku": sku,
    }
    return hashlib.sha256(_canonical(binding)).hexdigest()


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o660
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _receipt(**values: Any) -> MutationReceipt:
    return MutationReceipt(recorded_at=datetime.now(UTC).isoformat(), **values)


def _record(path: Path, receipt: MutationReceipt) -> MutationReceipt:
    _atomic_json(path, asdict(receipt))
    return receipt


def mutate_item(
    *,
    item_path: str | Path,
    archive_root: str | Path,
    journal_root: str | Path,
    sku: str,
    kind: str,
    expected_generation: str,
    payload: JsonValue,
    mutate: DocumentMutator,
    project: ProjectionWriter,
    operation_id: str | None = None,
) -> MutationReceipt:
    """Apply one locked, generation-fenced local item mutation.

    Exact retries return the already-recorded terminal receipt.  A successful
    canonical write followed by a failed projection is ``REPAIR_REQUIRED``;
    callers must never mistake it for an all-or-nothing rollback.
    """
    item_path = Path(item_path)
    archive_root = Path(archive_root)
    journal_root = Path(journal_root)
    derived_id = operation_identity(
        sku=sku, kind=kind, expected_generation=expected_generation, payload=payload
    )
    selected_id = operation_id or derived_id
    if selected_id != derived_id:
        mismatch_path = journal_root / "conflicts" / f"{hashlib.sha256((selected_id + derived_id).encode()).hexdigest()}.json"
        if mismatch_path.exists():
            return MutationReceipt(**_load_json(mismatch_path))
        return _record(
            mismatch_path,
            _receipt(
                operation_id=selected_id,
                sku=sku,
                kind=kind,
                expected_generation=expected_generation,
                status="CONFLICT",
                observed_generation=None,
                resulting_generation=None,
                detail="operation_id does not match the exact request binding",
            ),
        )

    operation_dir = journal_root / "operations" / selected_id[:2] / selected_id
    intent_path = operation_dir / "intent.json"
    receipt_path = operation_dir / "receipt.json"
    lock_path = journal_root / "locks" / f"{hashlib.sha256(sku.encode()).hexdigest()}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if receipt_path.exists():
            return MutationReceipt(**_load_json(receipt_path))

        binding = {
            "expected_generation": expected_generation,
            "kind": kind,
            "operation_id": selected_id,
            "payload": payload,
            "sku": sku,
        }
        intent: dict[str, Any]
        if intent_path.exists():
            intent = _load_json(intent_path)
            if _canonical(intent["binding"]) != _canonical(binding):
                return _record(
                    receipt_path,
                    _receipt(
                        operation_id=selected_id,
                        sku=sku,
                        kind=kind,
                        expected_generation=expected_generation,
                        status="CONFLICT",
                        observed_generation=None,
                        resulting_generation=None,
                        detail="durable intent does not match request binding",
                    ),
                )
        else:
            intent = {"binding": binding, "recorded_at": datetime.now(UTC).isoformat()}
            _atomic_json(intent_path, intent)

        document = _load_json(item_path)
        if not isinstance(document, dict):
            raise ValueError("item document must be a JSON object")
        observed = item_generation(document)
        if observed != expected_generation:
            # The process may have stopped after the canonical rename but
            # before recording its terminal projection receipt.  A planned
            # resulting generation in the intent makes that seam recoverable
            # without applying the document mutation twice.
            if intent.get("planned_resulting_generation") == observed:
                try:
                    project(sku, document)
                except Exception as exc:
                    return _record(
                        receipt_path,
                        _receipt(
                            operation_id=selected_id,
                            sku=sku,
                            kind=kind,
                            expected_generation=expected_generation,
                            status="REPAIR_REQUIRED",
                            observed_generation=expected_generation,
                            resulting_generation=observed,
                            detail=f"projection recovery failed: {type(exc).__name__}: {exc}",
                        ),
                    )
                return _record(
                    receipt_path,
                    _receipt(
                        operation_id=selected_id,
                        sku=sku,
                        kind=kind,
                        expected_generation=expected_generation,
                        status="COMMITTED",
                        observed_generation=expected_generation,
                        resulting_generation=observed,
                        detail="recovered canonical write and completed projection",
                    ),
                )
            return _record(
                receipt_path,
                _receipt(
                    operation_id=selected_id,
                    sku=sku,
                    kind=kind,
                    expected_generation=expected_generation,
                    status="CONFLICT",
                    observed_generation=observed,
                    resulting_generation=None,
                    detail="expected generation is stale",
                ),
            )

        try:
            updated = mutate(document)
            if not isinstance(updated, dict):
                raise TypeError("mutation must return a JSON object")
            _json_native(updated, "document")
            resulting = item_generation(updated)
            intent["planned_resulting_generation"] = resulting
            _atomic_json(intent_path, intent)
            atomic_write_json(item_path, updated, archive_root=archive_root, sort_keys=True)
        except Exception as exc:
            return _record(
                receipt_path,
                _receipt(
                    operation_id=selected_id,
                    sku=sku,
                    kind=kind,
                    expected_generation=expected_generation,
                    status="FAILED",
                    observed_generation=observed,
                    resulting_generation=None,
                    detail=f"canonical mutation failed: {type(exc).__name__}: {exc}",
                ),
            )

        try:
            project(sku, updated)
        except Exception as exc:
            return _record(
                receipt_path,
                _receipt(
                    operation_id=selected_id,
                    sku=sku,
                    kind=kind,
                    expected_generation=expected_generation,
                    status="REPAIR_REQUIRED",
                    observed_generation=observed,
                    resulting_generation=resulting,
                    detail=f"projection failed: {type(exc).__name__}: {exc}",
                ),
            )
        return _record(
            receipt_path,
            _receipt(
                operation_id=selected_id,
                sku=sku,
                kind=kind,
                expected_generation=expected_generation,
                status="COMMITTED",
                observed_generation=observed,
                resulting_generation=resulting,
                detail=None,
            ),
        )
