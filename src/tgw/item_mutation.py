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
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
DocumentMutator = Callable[[dict[str, JsonValue]], dict[str, JsonValue]]
ProjectionWriter = Callable[[str, dict[str, JsonValue]], Any]
ItemPathResolver = Callable[[str], str | Path]
ProjectionResolver = Callable[[str], ProjectionWriter]
ArchiveRootResolver = Callable[[str], str | Path]


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
    changed: bool = True


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
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _fsync_dir(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _archive_once(archive_root: Path, item_path: Path, operation_id: str) -> Path:
    """Append deterministic pre-publication evidence at most once per operation."""
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_path = archive_root / f"{item_path.stem}.zip"
    member = f"{item_path.name}.operation-{operation_id}"
    if archive_path.exists():
        with zipfile.ZipFile(archive_path, "r") as current:
            if member in current.namelist():
                return archive_path
    mode = (archive_path.stat().st_mode & 0o777) if archive_path.exists() else 0o660
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=archive_root, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as replacement:
            if archive_path.exists():
                with zipfile.ZipFile(archive_path, "r") as current:
                    for info in current.infolist():
                        replacement.writestr(info, current.read(info.filename))
            replacement.write(item_path, arcname=member)
        os.chmod(temporary, mode)
        with temporary.open("rb") as archive_handle:
            os.fsync(archive_handle.fileno())
        os.replace(temporary, archive_path)
        _fsync_dir(archive_path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return archive_path


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _receipt(**values: Any) -> MutationReceipt:
    return MutationReceipt(recorded_at=datetime.now(UTC).isoformat(), **values)


def _record(path: Path, receipt: MutationReceipt) -> MutationReceipt:
    _atomic_json(path, asdict(receipt))
    return receipt


def _operation_dir(journal_root: Path, operation_id: str) -> Path:
    if len(operation_id) != 64 or any(character not in "0123456789abcdef" for character in operation_id):
        raise ValueError("operation_id must be exactly 64 lowercase hexadecimal characters")
    operations_root = (journal_root / "operations").resolve()
    candidate = (operations_root / operation_id[:2] / operation_id).resolve()
    if operations_root not in candidate.parents:
        raise ValueError("operation journal path escapes journal_root")
    return candidate


def _item_lock_path(journal_root: Path, sku: str) -> Path:
    return journal_root / "locks" / f"{hashlib.sha256(sku.encode()).hexdigest()}.lock"


def _next_attempt_path(operation_dir: Path) -> Path:
    attempts = operation_dir / "reconciliation-attempts"
    sequence = max((int(path.stem) for path in attempts.glob("*.json")), default=0) + 1
    return attempts / f"{sequence:06d}.json"


def _projection_ok(result: Any) -> bool:
    if isinstance(result, Mapping):
        return result.get("ok") is True
    return result is not False


def discover_repair_operations(journal_root: str | Path) -> tuple[str, ...]:
    """Return operation IDs unfinished at publication/receipt or needing repair."""
    root = Path(journal_root)
    pending: list[str] = []
    for intent_path in (root / "operations").glob("*/*/intent.json"):
        if not (intent_path.parent / "receipt.json").exists():
            try:
                operation_id = intent_path.parent.name
                if intent_path.parent.resolve() == _operation_dir(root, operation_id):
                    pending.append(operation_id)
            except ValueError:
                continue
    for receipt_path in (root / "operations").glob("*/*/receipt.json"):
        try:
            receipt = MutationReceipt(**_load_json(receipt_path))
            operation_dir = _operation_dir(root, receipt.operation_id)
            if receipt_path.resolve() != (operation_dir / "receipt.json").resolve():
                continue
            attempts = sorted((operation_dir / "reconciliation-attempts").glob("*.json"))
            resolved = bool(attempts and MutationReceipt(**_load_json(attempts[-1])).status == "COMMITTED")
            if receipt.status == "REPAIR_REQUIRED" and not resolved:
                pending.append(receipt.operation_id)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(sorted(set(pending)))


def _reconcile_unfinished(
    *,
    item_path: Path,
    journal_root: Path,
    operation_id: str,
    project: ProjectionWriter,
    archive_root: Path,
) -> MutationReceipt:
    """Finish a durable intent after process death without rerunning its transform."""
    operation_dir = _operation_dir(journal_root, operation_id)
    intent = _load_json(operation_dir / "intent.json")
    binding = intent["binding"]
    identity_binding = {key: binding[key] for key in ("sku", "kind", "expected_generation", "payload")}
    if operation_identity(**identity_binding) != operation_id:
        raise ValueError("unfinished intent binding does not match operation_id")
    planned = intent.get("planned_document")
    planned_generation = intent.get("planned_resulting_generation")
    if not isinstance(planned, dict) or item_generation(planned) != planned_generation:
        raise ValueError("unfinished intent lacks an exact planned document")
    sku = binding["sku"]
    expected = binding["expected_generation"]
    receipt_path = operation_dir / "receipt.json"
    lock_path = _item_lock_path(journal_root, sku)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if receipt_path.exists():
            return MutationReceipt(**_load_json(receipt_path))
        try:
            document = _load_json(item_path)
            if not isinstance(document, dict):
                raise ValueError("item document must be a JSON object")
            observed = item_generation(document)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            return _record(
                receipt_path,
                _receipt(
                    operation_id=operation_id, sku=sku, kind=binding["kind"],
                    expected_generation=expected, status="CONFLICT",
                    observed_generation=None, resulting_generation=planned_generation,
                    detail=f"cannot verify unfinished canonical item: {type(exc).__name__}: {exc}",
                    changed=planned_generation != expected,
                ),
            )
        archive_marker = operation_dir / "archive.json"
        publication_marker = operation_dir / "publication.json"
        if not publication_marker.exists():
            if observed == expected:
                if not archive_marker.exists():
                    _archive_once(archive_root, item_path, operation_id)
                    _atomic_json(
                        archive_marker,
                        {"operation_id": operation_id, "source_generation": observed},
                    )
                _atomic_json(item_path, planned)
                observed = planned_generation
                _atomic_json(
                    publication_marker,
                    {"operation_id": operation_id, "resulting_generation": planned_generation},
                )
            elif observed == planned_generation and archive_marker.exists():
                _atomic_json(
                    publication_marker,
                    {"operation_id": operation_id, "resulting_generation": planned_generation},
                )
            else:
                return _record(
                    receipt_path,
                    _receipt(
                        operation_id=operation_id, sku=sku, kind=binding["kind"],
                        expected_generation=expected, status="CONFLICT",
                        observed_generation=observed, resulting_generation=planned_generation,
                        detail="canonical generation advanced during unfinished reconciliation",
                        changed=planned_generation != expected,
                    ),
                )
        elif observed != planned_generation:
            return _record(
                receipt_path,
                _receipt(
                    operation_id=operation_id, sku=sku, kind=binding["kind"],
                    expected_generation=expected, status="CONFLICT",
                    observed_generation=observed, resulting_generation=planned_generation,
                    detail="published generation no longer canonical during reconciliation",
                    changed=planned_generation != expected,
                ),
            )
        try:
            projection_result = project(sku, planned)
            if not _projection_ok(projection_result):
                raise RuntimeError("projection did not explicitly report success")
            status, detail = "COMMITTED", "fresh process completed unfinished operation"
        except Exception as exc:
            status = "REPAIR_REQUIRED"
            detail = f"unfinished projection failed: {type(exc).__name__}: {exc}"
        return _record(
            receipt_path,
            _receipt(
                operation_id=operation_id, sku=sku, kind=binding["kind"],
                expected_generation=expected, status=status,
                observed_generation=expected, resulting_generation=planned_generation,
                detail=detail,
                changed=planned_generation != expected,
            ),
        )


def reconcile_pending_mutations(
    *,
    journal_root: str | Path,
    item_path_for: ItemPathResolver,
    archive_root_for: ArchiveRootResolver,
    project_for: ProjectionResolver,
) -> tuple[MutationReceipt, ...]:
    """Discover and reconcile all locally repairable projection operations."""
    results: list[MutationReceipt] = []
    root = Path(journal_root)
    for operation_id in discover_repair_operations(root):
        operation_dir = _operation_dir(root, operation_id)
        receipt_path = operation_dir / "receipt.json"
        if receipt_path.exists():
            original = MutationReceipt(**_load_json(receipt_path))
            results.append(
                reconcile_mutation(
                    item_path=item_path_for(original.sku),
                    journal_root=root,
                    operation_id=operation_id,
                    project=project_for(original.kind),
                )
            )
        else:
            intent = _load_json(operation_dir / "intent.json")
            binding = intent["binding"]
            results.append(
                _reconcile_unfinished(
                    item_path=Path(item_path_for(binding["sku"])),
                    journal_root=root,
                    operation_id=operation_id,
                    project=project_for(binding["kind"]),
                    archive_root=Path(archive_root_for(binding["sku"])),
                )
            )
    return tuple(results)


def reconcile_mutation(
    *,
    item_path: str | Path,
    journal_root: str | Path,
    operation_id: str,
    project: ProjectionWriter,
) -> MutationReceipt:
    """Retry only the projection of a ``REPAIR_REQUIRED`` mutation.

    The original terminal receipt is immutable.  Every reconciliation result
    is appended under ``reconciliation-attempts``.  Projection is permitted
    only while the canonical document still has the exact generation written
    by the original operation.
    """
    item_path = Path(item_path)
    journal_root = Path(journal_root)
    operation_dir = _operation_dir(journal_root, operation_id)
    receipt_path = operation_dir / "receipt.json"
    if not receipt_path.exists():
        raise FileNotFoundError(f"no mutation receipt for operation {operation_id}")
    original = MutationReceipt(**_load_json(receipt_path))
    if original.operation_id != operation_id:
        raise ValueError("receipt operation_id does not match its journal location")
    if original.status != "REPAIR_REQUIRED":
        return original

    lock_path = _item_lock_path(journal_root, original.sku)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        existing_attempts = sorted((operation_dir / "reconciliation-attempts").glob("*.json"))
        if existing_attempts:
            latest = MutationReceipt(**_load_json(existing_attempts[-1]))
            if latest.status == "COMMITTED":
                return latest

        attempt_path = _next_attempt_path(operation_dir)
        try:
            document = _load_json(item_path)
            if not isinstance(document, dict):
                raise ValueError("item document must be a JSON object")
            observed = item_generation(document)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            return _record(
                attempt_path,
                _receipt(
                    operation_id=operation_id,
                    sku=original.sku,
                    kind=original.kind,
                    expected_generation=original.expected_generation,
                    status="CONFLICT",
                    observed_generation=None,
                    resulting_generation=original.resulting_generation,
                    detail=f"cannot verify canonical item for reconciliation: {type(exc).__name__}: {exc}",
                    changed=original.changed,
                ),
            )
        if observed != original.resulting_generation:
            return _record(
                attempt_path,
                _receipt(
                    operation_id=operation_id,
                    sku=original.sku,
                    kind=original.kind,
                    expected_generation=original.expected_generation,
                    status="CONFLICT",
                    observed_generation=observed,
                    resulting_generation=original.resulting_generation,
                    detail="canonical generation advanced; projection reconciliation refused",
                    changed=original.changed,
                ),
            )
        try:
            projection_result = project(original.sku, document)
            if not _projection_ok(projection_result):
                raise RuntimeError("projection did not explicitly report success")
        except Exception as exc:
            return _record(
                attempt_path,
                _receipt(
                    operation_id=operation_id,
                    sku=original.sku,
                    kind=original.kind,
                    expected_generation=original.expected_generation,
                    status="REPAIR_REQUIRED",
                    observed_generation=observed,
                    resulting_generation=original.resulting_generation,
                    detail=f"projection reconciliation failed: {type(exc).__name__}: {exc}",
                    changed=original.changed,
                ),
            )
        return _record(
            attempt_path,
            _receipt(
                operation_id=operation_id,
                sku=original.sku,
                kind=original.kind,
                expected_generation=original.expected_generation,
                status="COMMITTED",
                observed_generation=observed,
                resulting_generation=original.resulting_generation,
                detail="projection reconciled; original receipt preserved",
                changed=original.changed,
            ),
        )


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

    operation_dir = _operation_dir(journal_root, selected_id)
    intent_path = operation_dir / "intent.json"
    receipt_path = operation_dir / "receipt.json"
    lock_path = _item_lock_path(journal_root, sku)
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

        try:
            document = _load_json(item_path)
            if not isinstance(document, dict):
                raise ValueError("item document must be a JSON object")
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
            return _record(
                receipt_path,
                _receipt(
                    operation_id=selected_id,
                    sku=sku,
                    kind=kind,
                    expected_generation=expected_generation,
                    status="FAILED",
                    observed_generation=None,
                    resulting_generation=None,
                    detail=f"cannot load canonical item: {type(exc).__name__}: {exc}",
                ),
            )
        observed = item_generation(document)
        if observed != expected_generation:
            # The process may have stopped after the canonical rename but
            # before recording its terminal projection receipt.  A planned
            # resulting generation in the intent makes that seam recoverable
            # without applying the document mutation twice.
            archive_marker = operation_dir / "archive.json"
            if archive_marker.exists() and intent.get("planned_resulting_generation") == observed:
                publication_marker = operation_dir / "publication.json"
                if not publication_marker.exists():
                    _atomic_json(
                        publication_marker,
                        {"operation_id": selected_id, "resulting_generation": observed},
                    )
                try:
                    projection_result = project(sku, document)
                    if not _projection_ok(projection_result):
                        raise RuntimeError("projection did not explicitly report success")
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
            if resulting == observed:
                return _record(
                    receipt_path,
                    _receipt(
                        operation_id=selected_id,
                        sku=sku,
                        kind=kind,
                        expected_generation=expected_generation,
                        status="COMMITTED",
                        observed_generation=observed,
                        resulting_generation=observed,
                        detail="no canonical change",
                        changed=False,
                    ),
                )
            intent["planned_resulting_generation"] = resulting
            intent["planned_document"] = updated
            # Transform and validation are pure.  Persist the complete,
            # replayable intent immediately before the first archive effect,
            # avoiding an unrecoverable half-intent after process death.
            _atomic_json(intent_path, intent)
            archive_marker = operation_dir / "archive.json"
            if not archive_marker.exists():
                if item_path.exists():
                    _archive_once(archive_root, item_path, selected_id)
                _atomic_json(
                    archive_marker,
                    {"operation_id": selected_id, "source_generation": observed},
                )
            _atomic_json(item_path, updated)
            _atomic_json(
                operation_dir / "publication.json",
                {"operation_id": selected_id, "resulting_generation": resulting},
            )
        except Exception as exc:
            # os.replace may have published the canonical document before a
            # directory fsync or publication-marker write reported failure.
            # Classify from persisted truth: once the planned generation is
            # canonical, it is never truthful to call the operation FAILED.
            try:
                persisted = _load_json(item_path)
                persisted_generation = item_generation(persisted) if isinstance(persisted, dict) else None
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                persisted_generation = None
            if persisted_generation == locals().get("resulting") and persisted_generation != observed:
                return _record(
                    receipt_path,
                    _receipt(
                        operation_id=selected_id,
                        sku=sku,
                        kind=kind,
                        expected_generation=expected_generation,
                        status="REPAIR_REQUIRED",
                        observed_generation=observed,
                        resulting_generation=persisted_generation,
                        detail=f"canonical published; terminal publication incomplete: {type(exc).__name__}: {exc}",
                    ),
                )
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
            projection_result = project(sku, updated)
            if not _projection_ok(projection_result):
                raise RuntimeError("projection did not explicitly report success")
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
