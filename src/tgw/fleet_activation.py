"""W18 local fleet configuration transaction.

This module intentionally stops before process/service activation.  It makes
the only permitted local change -- harness configuration plus adapter links --
reversible as one receipt-backed transaction.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Mapping


class FleetActivationError(ValueError):
    pass


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def apply_fleet_configuration(
    configurations: Mapping[str | Path, Mapping[str, Any]],
    *,
    materialize: Callable[[], dict[str, Any]],
    rollback_materialization: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Atomically replace each verified config, then materialize adapters.

    A caller supplies both exact preimage hashes and desired bytes.  A failed
    later replacement or adapter materialization restores every earlier config
    and invokes the adapter rollback journal.  This deliberately has no code
    path for starting processes, registering MCP endpoints, or activating a
    service.
    """
    if not configurations:
        raise FleetActivationError("configuration set is empty")
    normalized: list[tuple[Path, str, bytes]] = []
    for raw_path, binding in sorted(configurations.items(), key=lambda item: str(item[0])):
        path = Path(raw_path)
        if not path.is_absolute() or not path.is_file() or path.is_symlink():
            raise FleetActivationError(f"configuration path is not a regular file: {path}")
        if set(binding) != {"expected_sha256", "desired"}:
            raise FleetActivationError(f"configuration binding is invalid: {path}")
        expected, desired = binding["expected_sha256"], binding["desired"]
        if not isinstance(expected, str) or not isinstance(desired, bytes):
            raise FleetActivationError(f"configuration binding is invalid: {path}")
        if _sha256(path.read_bytes()) != expected:
            raise FleetActivationError(f"configuration preimage changed: {path}")
        normalized.append((path, expected, desired))

    backups: list[tuple[Path, Path]] = []
    receipt_files: list[dict[str, str]] = []
    materialization: dict[str, Any] | None = None
    try:
        for path, expected, desired in normalized:
            staged = path.with_name(f".{path.name}.tgw-w18-next")
            backup = path.with_name(f".{path.name}.tgw-w18-previous")
            if staged.exists() or staged.is_symlink() or backup.exists() or backup.is_symlink():
                raise FleetActivationError(f"configuration transaction path exists: {path}")
            with open(staged, "xb") as handle:
                handle.write(desired)
                handle.flush()
                os.fsync(handle.fileno())
            # Reject a replacement after preflight, before moving its preimage.
            if _sha256(path.read_bytes()) != expected:
                raise FleetActivationError(f"configuration preimage changed: {path}")
            os.replace(path, backup)
            backups.append((path, backup))
            os.replace(staged, path)
            receipt_files.append({"path": str(path), "previous_sha256": expected, "current_sha256": _sha256(desired), "rollback": str(backup)})
        materialization = materialize()
    except Exception:
        if materialization is not None:
            rollback_materialization(materialization)
        for path, backup in reversed(backups):
            if path.exists() and not path.is_symlink():
                path.unlink()
            if backup.exists() and not backup.is_symlink():
                os.replace(backup, path)
        raise
    return {
        "schema": "tgw-w18-fleet-configuration-transaction/v1",
        "status": "CONFIGURED_MATERIALIZED_NOT_SERVICE_ACTIVATED",
        "configurations": receipt_files,
        "materialization": materialization,
        "activation": "operator-directed service/process activation only",
    }


def rollback_fleet_configuration(receipt: Mapping[str, Any], *, rollback_materialization: Callable[[dict[str, Any]], None]) -> None:
    if receipt.get("schema") != "tgw-w18-fleet-configuration-transaction/v1" or not isinstance(receipt.get("configurations"), list):
        raise FleetActivationError("fleet configuration receipt is invalid")
    materialization = receipt.get("materialization")
    if not isinstance(materialization, dict):
        raise FleetActivationError("fleet materialization receipt is invalid")
    rollback_materialization(materialization)
    for entry in reversed(receipt["configurations"]):
        if not isinstance(entry, dict) or set(entry) != {"path", "previous_sha256", "current_sha256", "rollback"}:
            raise FleetActivationError("fleet configuration rollback entry is invalid")
        path, backup = Path(entry["path"]), Path(entry["rollback"])
        if _sha256(path.read_bytes()) != entry["current_sha256"] or _sha256(backup.read_bytes()) != entry["previous_sha256"]:
            raise FleetActivationError(f"fleet configuration rollback binding changed: {path}")
        os.replace(path, path.with_name(f".{path.name}.tgw-w18-rolled-back"))
        os.replace(backup, path)
