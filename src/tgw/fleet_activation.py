"""W18 fleet configuration and quiet refresh transactions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any, Callable, Mapping


class FleetActivationError(ValueError):
    pass


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _value_hash(value: Any) -> str:
    return _sha256(_canonical(value))


def _durable_path(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents:
        raise FleetActivationError(f"{label} must be an absolute durable path outside /tmp")
    return path


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    staged = path.with_name(
        f".{path.name}.next-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if staged.exists() and not staged.is_symlink():
            staged.unlink()


def _step(name: str, callback: Callable[..., Mapping[str, Any]], *args: Any, expected: str) -> dict[str, Any]:
    value = callback(*args)
    if not isinstance(value, Mapping) or value.get("status") != expected:
        raise FleetActivationError(f"fleet {name} did not report {expected}")
    return dict(value)


_CHECKPOINT_COLLECTIONS = (
    "live_requests", "role_leases", "rendered_surfaces", "continuations",
)


def _validate_checkpoint(receipt: Mapping[str, Any]) -> None:
    """Require stable identities for every lifecycle object in a quiet checkpoint."""
    if not set(_CHECKPOINT_COLLECTIONS) <= set(receipt):
        raise FleetActivationError("quiet checkpoint omits live lifecycle state")
    for collection in _CHECKPOINT_COLLECTIONS:
        records = receipt[collection]
        if not isinstance(records, list):
            raise FleetActivationError(f"quiet checkpoint {collection} is not a list")
        identities: set[str] = set()
        for record in records:
            identity = record.get("checkpoint_identity") if isinstance(record, Mapping) else None
            if (
                not isinstance(identity, str) or not identity.startswith("sha256:")
                or len(identity) != 71 or identity in identities
            ):
                raise FleetActivationError(f"quiet checkpoint {collection} identity is invalid")
            identities.add(identity)


def _validate_resume(checkpoint: Mapping[str, Any], receipt: Mapping[str, Any]) -> None:
    """Prove every checkpointed object has exactly one legal post-refresh disposition."""
    dispositions = receipt.get("dispositions")
    if not isinstance(dispositions, Mapping) or set(dispositions) != set(_CHECKPOINT_COLLECTIONS):
        raise FleetActivationError("fleet resume omits lifecycle dispositions")
    for collection in _CHECKPOINT_COLLECTIONS:
        expected = {record["checkpoint_identity"] for record in checkpoint[collection]}
        records = dispositions[collection]
        if not isinstance(records, list):
            raise FleetActivationError(f"fleet resume {collection} dispositions are invalid")
        observed: set[str] = set()
        for record in records:
            if not isinstance(record, Mapping) or set(record) != {"checkpoint_identity", "disposition"}:
                raise FleetActivationError(f"fleet resume {collection} disposition is invalid")
            identity, disposition = record["checkpoint_identity"], record["disposition"]
            if identity in observed or disposition not in {"successor", "terminal", "reconcile"}:
                raise FleetActivationError(f"fleet resume {collection} disposition is invalid")
            observed.add(identity)
        if observed != expected:
            raise FleetActivationError(f"fleet resume does not cover every {collection} checkpoint")


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
    staged_paths: list[Path] = []
    receipt_files: list[dict[str, str]] = []
    materialization: dict[str, Any] | None = None
    try:
        for path, expected, desired in normalized:
            staged = path.with_name(f".{path.name}.tgw-w18-next")
            backup = path.with_name(f".{path.name}.tgw-w18-previous")
            if staged.exists() or staged.is_symlink() or backup.exists() or backup.is_symlink():
                raise FleetActivationError(f"configuration transaction path exists: {path}")
            metadata = path.stat()
            # Create privately regardless of the caller's umask; the exact
            # preimage mode is restored only after ownership is correct.
            descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            staged_paths.append(staged)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(desired)
                handle.flush()
                os.fsync(handle.fileno())
            # A privileged fleet operator must not take ownership of a user
            # harness configuration merely by atomically replacing it.
            if hasattr(os, "chown"):
                os.chown(staged, metadata.st_uid, metadata.st_gid)
            os.chmod(staged, metadata.st_mode)
            # Reject a replacement after preflight, before moving its preimage.
            if _sha256(path.read_bytes()) != expected:
                raise FleetActivationError(f"configuration preimage changed: {path}")
            os.replace(path, backup)
            backups.append((path, backup))
            os.replace(staged, path)
            staged_paths.remove(staged)
            receipt_files.append({"path": str(path), "previous_sha256": expected, "current_sha256": _sha256(desired), "rollback": str(backup)})
        materialization = materialize()
        if materialization.get("status") != "MATERIALIZED_NOT_ACTIVATED":
            raise FleetActivationError("adapter materialization was not applied")
    except Exception as original:
        rollback_error = None
        try:
            if materialization is not None:
                rollback_materialization(materialization)
        except Exception as exc:  # preserve original while always restoring configs
            rollback_error = exc
        finally:
            for path, backup in reversed(backups):
                if path.exists() and not path.is_symlink():
                    path.unlink()
                if backup.exists() and not backup.is_symlink():
                    os.replace(backup, path)
            for staged in staged_paths:
                if staged.exists() and not staged.is_symlink():
                    staged.unlink()
        if rollback_error is not None:
            raise FleetActivationError(f"materialization rollback failed: {rollback_error}") from original
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
    rollback_error = None
    try:
        rollback_materialization(materialization)
    except Exception as exc:
        rollback_error = exc
    finally:
        for entry in reversed(receipt["configurations"]):
            if not isinstance(entry, dict) or set(entry) != {"path", "previous_sha256", "current_sha256", "rollback"}:
                raise FleetActivationError("fleet configuration rollback entry is invalid")
            path, backup = Path(entry["path"]), Path(entry["rollback"])
            try:
                matches = _sha256(path.read_bytes()) == entry["current_sha256"] and _sha256(backup.read_bytes()) == entry["previous_sha256"]
            except OSError as exc:
                raise FleetActivationError(f"fleet configuration rollback is unavailable: {path}") from exc
            if not matches:
                raise FleetActivationError(f"fleet configuration rollback binding changed: {path}")
            os.replace(path, path.with_name(f".{path.name}.tgw-w18-rolled-back"))
            os.replace(backup, path)
    if rollback_error is not None:
        raise FleetActivationError(f"materialization rollback failed: {rollback_error}") from rollback_error


def run_fleet_refresh_transaction(
    request: Mapping[str, Any], *, receipt_root: str | Path, lease_path: str | Path,
    checkpoint: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    quiesce: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    rebuild: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    activate: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    restart: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    health: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    verify_actor: Callable[[str, Mapping[str, Any]], Mapping[str, Any]],
    resume: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
    rollback: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Run one serialized checkpoint-to-resume fleet generation change.

    Registered providers own effects.  This controller owns exact generation
    binding, ordering, idempotency, durable journaling, actor-by-actor
    verification, and all-or-quiesced rollback.
    """
    expected_fields = {
        "schema", "transaction_id", "idempotency_key", "predecessor_generation",
        "successor_generation", "revisions", "actors",
    }
    if not isinstance(request, Mapping) or set(request) != expected_fields:
        raise FleetActivationError("fleet refresh request fields are not exact")
    value = dict(request)
    if value["schema"] != "tgw-w18-fleet-refresh-request/v1":
        raise FleetActivationError("fleet refresh request schema is invalid")
    for field in ("transaction_id", "idempotency_key"):
        if not isinstance(value[field], str) or not value[field] or "/" in value[field]:
            raise FleetActivationError(f"fleet {field} is invalid")
    for field in ("predecessor_generation", "successor_generation"):
        generation = value[field]
        if not isinstance(generation, str) or not generation.startswith("sha256:") or len(generation) != 71:
            raise FleetActivationError(f"fleet {field} is invalid")
    if value["predecessor_generation"] == value["successor_generation"]:
        raise FleetActivationError("fleet refresh does not change generation")
    if not isinstance(value["revisions"], Mapping) or not value["revisions"]:
        raise FleetActivationError("fleet revisions are invalid")
    required_revisions = {
        "plan", "solution", "evidence_plan", "evidence_tree", "source",
        "source_tree", "current_plan_sources", "catalog", "bootstrap",
        "broker_policy", "review", "admission",
    }
    if set(value["revisions"]) != required_revisions:
        raise FleetActivationError("fleet revisions are incomplete")
    git_revisions = {
        "plan", "evidence_plan", "evidence_tree", "source", "source_tree"
    }
    for field in git_revisions:
        revision = value["revisions"][field]
        if not isinstance(revision, str) or len(revision) != 40 or any(character not in "0123456789abcdef" for character in revision):
            raise FleetActivationError(f"fleet {field} revision is invalid")
    for field in required_revisions - git_revisions - {"current_plan_sources"}:
        revision = value["revisions"][field]
        if not isinstance(revision, str) or not revision.startswith("sha256:") or len(revision) != 71:
            raise FleetActivationError(f"fleet {field} revision is invalid")
    current_sources = value["revisions"].get("current_plan_sources")
    if (
        not isinstance(current_sources, Mapping)
        or set(current_sources) != {
            "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
            "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
            "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
            "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
        }
        or any(
            not isinstance(item, str)
            or not item.startswith("sha256:")
            or len(item) != 71
            for item in current_sources.values()
        )
    ):
        raise FleetActivationError("fleet current Plan revisions are invalid")
    actors = value["actors"]
    if (
        not isinstance(actors, list) or not actors or actors != sorted(set(actors))
        or not all(isinstance(actor, str) and actor for actor in actors)
    ):
        raise FleetActivationError("fleet actors are invalid")

    root = _durable_path(receipt_root, "receipt root")
    lease = _durable_path(lease_path, "fleet lease")
    root.mkdir(parents=True, exist_ok=True)
    lease.parent.mkdir(parents=True, exist_ok=True)
    receipt_path = root / f"{value['idempotency_key']}.json"
    journal_path = root / f"{value['transaction_id']}.journal.json"
    request_hash = _value_hash(value)
    if receipt_path.is_file() and not receipt_path.is_symlink():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        claimed = receipt.pop("receipt_hash", None)
        if receipt.get("request_hash") != request_hash:
            raise FleetActivationError("fleet idempotency key was reused for another request")
        if claimed != _value_hash(receipt):
            raise FleetActivationError("fleet idempotent receipt hash mismatch")
        return {**receipt, "receipt_hash": claimed}

    descriptor = os.open(lease, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FleetActivationError("fleet refresh lease is already held") from exc
        if journal_path.exists() or journal_path.is_symlink():
            if journal_path.is_symlink() or not journal_path.is_file():
                raise FleetActivationError("fleet refresh journal path is unsafe")
            try:
                existing_journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FleetActivationError(
                    "existing fleet refresh journal is invalid"
                ) from exc
            if (
                not isinstance(existing_journal, Mapping)
                or existing_journal.get("schema")
                != "tgw-w18-fleet-refresh-journal/v1"
                or existing_journal.get("request_hash") != request_hash
                or existing_journal.get("request") != value
            ):
                raise FleetActivationError(
                    "existing fleet refresh journal binding differs"
                )
            journal = dict(existing_journal)
        else:
            journal = {
                "schema": "tgw-w18-fleet-refresh-journal/v1",
                "request_hash": request_hash, "request": value,
                "status": "STARTED", "steps": [], "actor_receipts": {},
            }
            _atomic_json(journal_path, journal)

        def saved_receipt(name: str) -> dict[str, Any] | None:
            for record in reversed(journal["steps"]):
                if record.get("name") == name and isinstance(record.get("receipt"), Mapping):
                    return dict(record["receipt"])
            return None

        def persist_step(name: str, receipt: Mapping[str, Any], status: str) -> dict[str, Any]:
            observed = dict(receipt)
            journal["steps"].append({"name": name, "receipt": observed})
            journal["status"] = status
            _atomic_json(journal_path, journal)
            return observed

        def waiting(direction: str, observed: Mapping[str, Any]) -> dict[str, Any]:
            return {
                "schema": "tgw-w18-fleet-refresh-wait/v1",
                "status": "RESTART_REQUIRED",
                "lifecycle": "WAIT_EXTERNAL_RESTART",
                "direction": direction,
                "request_hash": request_hash,
                "transaction_id": value["transaction_id"],
                "idempotency_key": value["idempotency_key"],
                "pending": observed.get("pending", []),
            }

        def terminal(unsigned: dict[str, Any]) -> dict[str, Any]:
            receipt = {**unsigned, "receipt_hash": _value_hash(unsigned)}
            _atomic_json(receipt_path, receipt)
            journal["status"] = receipt["status"]
            journal["terminal_receipt_hash"] = receipt["receipt_hash"]
            _atomic_json(journal_path, journal)
            return receipt

        checkpoint_receipt = saved_receipt("checkpoint")

        def continue_rollback() -> dict[str, Any]:
            nonlocal checkpoint_receipt
            try:
                observed = rollback(value, journal)
                if not isinstance(observed, Mapping):
                    raise FleetActivationError("fleet rollback returned an invalid receipt")
                if observed.get("status") == "RESTART_REQUIRED":
                    persist_step("rollback-wait", observed, "ROLLBACK_RESTART_REQUIRED")
                    return waiting("rollback", observed)
                if observed.get("status") != "ROLLED_BACK":
                    raise FleetActivationError("fleet rollback did not report ROLLED_BACK")
                rolled_back = persist_step("rollback", observed, "ROLLED_BACK")
                rollback_resume = None
                if checkpoint_receipt is not None:
                    rollback_request = {
                        **value,
                        "successor_generation": value["predecessor_generation"],
                    }
                    rollback_resume = _step(
                        "rollback resume", resume, checkpoint_receipt,
                        rollback_request, expected="RESUMED",
                    )
                    _validate_resume(checkpoint_receipt, rollback_resume)
                    persist_step("rollback-resume", rollback_resume, "ROLLBACK_RESUMED")
                return terminal(
                    {
                        "schema": "tgw-w18-fleet-refresh-receipt/v1",
                        "request_hash": request_hash,
                        "transaction_id": value["transaction_id"],
                        "idempotency_key": value["idempotency_key"],
                        "predecessor_generation": value["predecessor_generation"],
                        "successor_generation": value["successor_generation"],
                        "status": "FAILED_ROLLED_BACK",
                        "steps": journal["steps"],
                        "failure": journal.get("failure", "fleet refresh failed"),
                        "rollback": {"rollback": rolled_back, "resume": rollback_resume},
                    }
                )
            except Exception as rollback_error:
                errors = list(journal.get("rollback_errors", []))
                errors.append(str(rollback_error))
                journal["rollback_errors"] = errors
                journal["status"] = "ROLLBACK_REQUIRED"
                _atomic_json(journal_path, journal)
                return {
                    "schema": "tgw-w18-fleet-refresh-wait/v1",
                    "status": "ROLLBACK_REQUIRED",
                    "lifecycle": "HOLD_QUIESCED_RETRYABLE",
                    "direction": "rollback",
                    "request_hash": request_hash,
                    "transaction_id": value["transaction_id"],
                    "idempotency_key": value["idempotency_key"],
                    "reason": str(rollback_error),
                }

        if journal["status"] in {"ROLLBACK_REQUIRED", "ROLLBACK_RESTART_REQUIRED"}:
            return continue_rollback()

        try:
            if journal["status"] == "STARTED":
                observed_checkpoint = _step(
                    "checkpoint", checkpoint, value, expected="CHECKPOINTED"
                )
                _validate_checkpoint(observed_checkpoint)
                checkpoint_receipt = persist_step(
                    "checkpoint", observed_checkpoint, "CHECKPOINTED"
                )
            if checkpoint_receipt is None:
                raise FleetActivationError("fleet checkpoint receipt is unavailable")
            if journal["status"] == "CHECKPOINTED":
                persist_step(
                    "quiesce",
                    _step(
                        "quiesce", quiesce, checkpoint_receipt,
                        expected="QUIESCED",
                    ),
                    "QUIESCED",
                )
            if journal["status"] == "QUIESCED":
                persist_step(
                    "rebuild", _step("rebuild", rebuild, value, expected="REBUILT"),
                    "REBUILT",
                )
            if journal["status"] == "REBUILT":
                rebuilt = saved_receipt("rebuild")
                if rebuilt is None:
                    raise FleetActivationError("fleet rebuild receipt is unavailable")
                persist_step(
                    "activate",
                    _step("activate", activate, value, rebuilt, expected="ACTIVATED"),
                    "ACTIVATED",
                )
            if journal["status"] in {"ACTIVATED", "RESTART_REQUIRED"}:
                activated = saved_receipt("activate")
                if activated is None:
                    raise FleetActivationError("fleet activation receipt is unavailable")
                observed_restart = restart(activated)
                if not isinstance(observed_restart, Mapping):
                    raise FleetActivationError("fleet restart returned an invalid receipt")
                if observed_restart.get("status") == "RESTART_REQUIRED":
                    persist_step(
                        "restart-wait", observed_restart, "RESTART_REQUIRED"
                    )
                    return waiting("successor", observed_restart)
                if observed_restart.get("status") != "RESTARTED":
                    raise FleetActivationError("fleet restart did not report RESTARTED")
                persist_step("restart", observed_restart, "RESTARTED")
            if journal["status"] == "RESTARTED":
                restarted = saved_receipt("restart")
                if restarted is None:
                    raise FleetActivationError("fleet restart receipt is unavailable")
                observed_health = health(restarted)
                if not isinstance(observed_health, Mapping):
                    raise FleetActivationError("fleet health returned an invalid receipt")
                if observed_health.get("status") == "RESTART_REQUIRED":
                    journal["actor_receipts"] = {}
                    persist_step(
                        "health-wait", observed_health, "RESTART_REQUIRED"
                    )
                    return waiting("successor", observed_health)
                if observed_health.get("status") != "HEALTHY":
                    raise FleetActivationError("fleet health did not report HEALTHY")
                persist_step("health", observed_health, "HEALTHY")
            if journal["status"] in {"HEALTHY", "VERIFYING"}:
                actor_receipts = dict(journal.get("actor_receipts", {}))
                for actor in actors:
                    if actor in actor_receipts:
                        continue
                    observed_verification = verify_actor(actor, value)
                    if not isinstance(observed_verification, Mapping):
                        raise FleetActivationError(
                            f"actor verification {actor} returned an invalid receipt"
                        )
                    if observed_verification.get("status") == "RESTART_REQUIRED":
                        journal["actor_receipts"] = {}
                        persist_step(
                            "verification-wait", observed_verification,
                            "RESTART_REQUIRED",
                        )
                        return waiting("successor", observed_verification)
                    if observed_verification.get("status") != "VERIFIED":
                        raise FleetActivationError(
                            f"actor verification {actor} did not report VERIFIED"
                        )
                    verified = dict(observed_verification)
                    if (
                        verified.get("actor") != actor
                        or verified.get("generation") != value["successor_generation"]
                    ):
                        raise FleetActivationError(
                            f"actor contract verification mismatch: {actor}"
                        )
                    actor_receipts[actor] = verified
                    journal["actor_receipts"] = actor_receipts
                    journal["status"] = "VERIFYING"
                    _atomic_json(journal_path, journal)
                journal["steps"].append(
                    {
                        "name": "actor-verification",
                        "receipts": [actor_receipts[actor] for actor in actors],
                    }
                )
                journal["status"] = "VERIFIED"
                _atomic_json(journal_path, journal)
            if journal["status"] == "VERIFIED":
                resumed = _step(
                    "resume", resume, checkpoint_receipt, value, expected="RESUMED"
                )
                _validate_resume(checkpoint_receipt, resumed)
                persist_step("resume", resumed, "RESUMED")
            if journal["status"] != "RESUMED":
                raise FleetActivationError(
                    f"fleet refresh cannot resume from {journal['status']}"
                )
            return terminal(
                {
                    "schema": "tgw-w18-fleet-refresh-receipt/v1",
                    "request_hash": request_hash,
                    "transaction_id": value["transaction_id"],
                    "idempotency_key": value["idempotency_key"],
                    "predecessor_generation": value["predecessor_generation"],
                    "successor_generation": value["successor_generation"],
                    "status": "VERIFIED_AND_RESUMED",
                    "steps": journal["steps"], "rollback": None,
                }
            )
        except Exception as original:
            journal["status"] = "ROLLBACK_REQUIRED"
            journal["failure"] = str(original)
            _atomic_json(journal_path, journal)
            return continue_rollback()
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
