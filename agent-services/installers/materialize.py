"""Materialize shared tgw-plan and Promptcraft adapters without copying policy."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "tgw-agent-service-installation/v1"
TARGETS = ("codex", "claude", "deepseek", "hermes", "isolated-worker")


class InstallError(ValueError):
    pass


@dataclass(frozen=True)
class Adapter:
    capability: str
    source: Path
    destination: Path
    hold_legacy: bool = False


def tree_digest(path: Path) -> str:
    digest = hashlib.sha256()
    files = [item for item in path.rglob("*") if item.is_file() and "__pycache__" not in item.parts]
    for item in sorted(files, key=lambda value: value.relative_to(path).as_posix()):
        relative = item.relative_to(path).as_posix()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _matrix(target: str, *, home: Path, project: Path, source_root: Path) -> list[Adapter]:
    skill = source_root / "agent-services/skills/tgw-plan"
    review = source_root / "agent-services/skills/tgw-review"
    provider = source_root / "agent-services/providers/promptcraft"
    if target == "codex":
        return [
            Adapter("tgw-plan", skill, home / ".codex/skills/tgw-plan"),
            Adapter("tgw-review", review, home / ".codex/skills/tgw-review"),
            Adapter("promptcraft", provider, home / ".codex/providers/promptcraft"),
        ]
    if target == "claude":
        return [
            Adapter("tgw-plan", skill, project / ".claude/skills/tgw-plan", hold_legacy=True),
            Adapter("tgw-review", review, project / ".claude/skills/tgw-review"),
            Adapter("promptcraft", provider, project / ".claude/providers/promptcraft"),
        ]
    if target == "deepseek":
        return [
            Adapter("tgw-plan", skill, home / ".dsh/skills/tgw-plan"),
            Adapter("tgw-review", review, home / ".dsh/skills/tgw-review"),
            Adapter("promptcraft", provider, home / ".dsh/providers/promptcraft"),
        ]
    if target == "hermes":
        return [
            Adapter("tgw-plan", skill, home / ".hermes/skills/tgw-plan"),
            Adapter("promptcraft", provider, home / ".hermes/providers/promptcraft"),
        ]
    if target == "isolated-worker":
        return [
            Adapter(
                "promptcraft-card-handoff",
                provider / "bin/promptcraft-handoff",
                project / ".tgw-worker/bin/promptcraft-handoff",
            )
        ]
    raise InstallError(f"unknown target: {target}")


def _same_link(destination: Path, source: Path) -> bool:
    return destination.is_symlink() and destination.resolve(strict=False) == source


def _contract_receipt_matches(contract: dict[str, Any]) -> bool:
    """Verify the exact actor-contract body before it can drive writes."""
    receipt_hash = contract.get("receipt_hash")
    if not isinstance(receipt_hash, str) or not receipt_hash.startswith("sha256:"):
        return False
    body = dict(contract)
    body.pop("receipt_hash", None)
    body.pop("issuer_public_key", None)
    body.pop("signature", None)
    try:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError):
        return False
    return receipt_hash == "sha256:" + hashlib.sha256(encoded).hexdigest()


def _contract_signature_matches(contract: dict[str, Any], trusted_public_key: str) -> bool:
    if contract.get("issuer_public_key") != trusted_public_key:
        return False
    signature = contract.get("signature")
    if not isinstance(signature, str):
        return False
    signed = dict(contract)
    signed.pop("issuer_public_key", None)
    signed.pop("signature", None)
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(trusted_public_key, validate=True))
        value = base64.b64decode(signature, validate=True)
        if len(value) != 64:
            return False
        key.verify(value, json.dumps(signed, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode())
    except (ValueError, TypeError):
        return False
    except Exception:
        return False
    return True


def materialize(
    target: str,
    *,
    home: str | Path,
    project: str | Path,
    source_root: str | Path,
    apply: bool = False,
) -> dict[str, Any]:
    """Inspect or create the exact adapter matrix for one harness."""

    home_path = Path(home).resolve()
    project_path = Path(project).resolve()
    root = Path(source_root).resolve()
    adapters = _matrix(target, home=home_path, project=project_path, source_root=root)
    initial: list[tuple[Adapter, str]] = []
    for adapter in adapters:
        if not adapter.source.exists():
            raise InstallError(f"canonical source is missing: {adapter.source}")
        destination = adapter.destination
        if _same_link(destination, adapter.source):
            status = "CURRENT"
        elif destination.exists() or destination.is_symlink():
            status = "HELD_LEGACY" if adapter.hold_legacy else "CONFLICT"
        else:
            status = "MISSING"
        initial.append((adapter, status))
    ok = not any(status == "CONFLICT" for _, status in initial)

    actions: list[dict[str, Any]] = []
    created: list[Path] = []
    try:
        for adapter, initial_status in initial:
            destination = adapter.destination
            if initial_status != "MISSING":
                status = initial_status
            elif apply and ok:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(adapter.source, destination, target_is_directory=adapter.source.is_dir())
                created.append(destination)
                status = "INSTALLED"
            elif apply:
                status = "HELD_CONFLICT"
            else:
                status = "WOULD_INSTALL"
            actions.append(
                {
                    "capability": adapter.capability,
                    "source": str(adapter.source),
                    "source_digest": tree_digest(adapter.source) if adapter.source.is_dir() else "sha256:" + hashlib.sha256(adapter.source.read_bytes()).hexdigest(),
                    "destination": str(destination),
                    "status": status,
                }
            )
    except Exception:
        for destination in reversed(created):
            if destination.is_symlink():
                destination.unlink()
        raise
    return {
        "schema": SCHEMA,
        "target": target,
        "mode": "apply" if apply else "dry-run",
        "canonical_source_root": str(root),
        "actions": actions,
        "ok": ok,
        "legacy_held": any(item["status"] == "HELD_LEGACY" for item in actions),
    }


def materialize_fleet(
    actors: dict[str, dict[str, str | Path]], *, source_root: str | Path,
    contracts: dict[str, dict[str, Any]], trusted_contract_public_key: str,
    apply: bool = False, replace_existing: bool = False,
) -> dict[str, Any]:
    """Materialize every READY actor as one local, rollback-safe transaction.

    This installer deliberately owns only canonical adapter links.  It neither
    starts services nor changes MCP registrations: those effects remain a
    separate W18 activation boundary after this receipt has been verified.
    """
    if not actors or set(actors) != set(contracts):
        raise InstallError("fleet actors and contracts must have the same non-empty identities")
    if not isinstance(trusted_contract_public_key, str):
        raise InstallError("fleet contract signer is invalid")
    root = Path(source_root).resolve()
    plans: dict[str, list[tuple[Adapter, str]]] = {}
    for actor in sorted(actors):
        contract = contracts[actor]
        if (
            not isinstance(contract, dict)
            or contract.get("actor") != actor
            or contract.get("status") != "READY"
            or not _contract_receipt_matches(contract)
            or not _contract_signature_matches(contract, trusted_contract_public_key)
        ):
            raise InstallError(f"actor contract is not READY: {actor}")
        entry = actors[actor]
        home, project = entry.get("home"), entry.get("project")
        if not isinstance(home, (str, Path)) or not isinstance(project, (str, Path)):
            raise InstallError(f"actor paths are invalid: {actor}")
        matrix = _matrix(actor, home=Path(home).resolve(), project=Path(project).resolve(), source_root=root)
        states: list[tuple[Adapter, str]] = []
        for adapter in matrix:
            if not adapter.source.exists():
                raise InstallError(f"canonical source is missing: {adapter.source}")
            if _same_link(adapter.destination, adapter.source):
                state = "CURRENT"
            elif adapter.destination.is_symlink() and replace_existing:
                state = "REPLACEABLE"
            elif adapter.destination.exists() or adapter.destination.is_symlink():
                state = "HELD_LEGACY" if adapter.hold_legacy else "CONFLICT"
            else:
                state = "MISSING"
            states.append((adapter, state))
        if any(state == "CONFLICT" for _, state in states):
            raise InstallError(f"actor adapter conflict: {actor}")
        plans[actor] = states

    created: list[tuple[Path, Path]] = []
    backups: list[tuple[Path, Path]] = []
    staged: list[tuple[Adapter, Path, str]] = []
    try:
        if apply:
            for actor in sorted(plans):
                for adapter, state in plans[actor]:
                    if state not in {"MISSING", "REPLACEABLE"}:
                        continue
                    adapter.destination.parent.mkdir(parents=True, exist_ok=True)
                    staged_path = adapter.destination.with_name(f".{adapter.destination.name}.tgw-w18-next")
                    if staged_path.exists() or staged_path.is_symlink():
                        raise InstallError(f"staged fleet path already exists: {staged_path}")
                    os.symlink(adapter.source, staged_path, target_is_directory=adapter.source.is_dir())
                    staged.append((adapter, staged_path, state))
            for adapter, staged_path, state in staged:
                if state == "REPLACEABLE":
                    if not adapter.destination.is_symlink():
                        raise InstallError(f"fleet replacement target changed: {adapter.destination}")
                    backup = adapter.destination.with_name(f".{adapter.destination.name}.tgw-w18-previous")
                    if backup.exists() or backup.is_symlink():
                        raise InstallError(f"fleet rollback path already exists: {backup}")
                    os.replace(adapter.destination, backup)
                    backups.append((adapter.destination, backup))
                os.replace(staged_path, adapter.destination)
                created.append((adapter.destination, adapter.source))
    except Exception:
        for destination, _source in reversed(created):
            if destination.is_symlink():
                destination.unlink()
        for destination, backup in reversed(backups):
            if backup.is_symlink():
                os.replace(backup, destination)
        for _adapter, staged_path, _state in staged:
            if staged_path.is_symlink():
                staged_path.unlink()
        raise

    actor_actions = []
    for actor in sorted(plans):
        actions = []
        for adapter, state in plans[actor]:
            status = (
                "INSTALLED" if apply and state == "MISSING"
                else "REPLACED" if apply and state == "REPLACEABLE"
                else "WOULD_INSTALL" if state == "MISSING"
                else "WOULD_REPLACE" if state == "REPLACEABLE"
                else state
            )
            actions.append({
                "capability": adapter.capability,
                "destination": str(adapter.destination),
                "source": str(adapter.source),
                "source_digest": tree_digest(adapter.source) if adapter.source.is_dir() else "sha256:" + hashlib.sha256(adapter.source.read_bytes()).hexdigest(),
                "status": status,
            })
        actor_actions.append({"actor": actor, "contract_receipt_hash": contracts[actor].get("receipt_hash"), "actions": actions})
    return {
        "schema": "tgw-w18-fleet-adapter-materialization/v1",
        "mode": "apply" if apply else "dry-run",
        "status": "PREPARED" if not apply else "MATERIALIZED_NOT_ACTIVATED",
        "actors": actor_actions,
        "rollback_journal": [
            {
                "destination": str(destination), "materialized_source": str(source),
                "previous": next((str(backup) for replaced, backup in backups if replaced == destination), None),
            }
            for destination, source in created
        ],
        "activation": "declarative-only",
    }


def rollback_fleet(materialization: dict[str, Any]) -> None:
    """Restore the exact links preserved by an unapplied W18 materialization."""
    if materialization.get("schema") != "tgw-w18-fleet-adapter-materialization/v1":
        raise InstallError("fleet rollback materialization schema is invalid")
    journal = materialization.get("rollback_journal")
    if not isinstance(journal, list):
        raise InstallError("fleet rollback journal is invalid")
    for entry in reversed(journal):
        if not isinstance(entry, dict) or set(entry) != {"destination", "materialized_source", "previous"}:
            raise InstallError("fleet rollback journal entry is invalid")
        destination = Path(entry["destination"])
        if entry["previous"] is not None and not isinstance(entry["previous"], str):
            raise InstallError("fleet rollback journal entry is invalid")
        source = Path(entry["materialized_source"])
        if not isinstance(entry["materialized_source"], str) or not destination.is_symlink() or destination.resolve(strict=False) != source:
            raise InstallError("fleet rollback target changed")
        if destination.is_symlink():
            destination.unlink()
        if entry["previous"] is not None:
            previous = Path(entry["previous"])
            if not previous.is_symlink():
                raise InstallError("fleet rollback link is unavailable")
            os.replace(previous, destination)


def main() -> int:
    parser = argparse.ArgumentParser(prog="materialize-agent-services")
    parser.add_argument("target", choices=TARGETS)
    parser.add_argument("--home", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = materialize(
            args.target,
            home=args.home,
            project=args.project,
            source_root=args.source_root,
            apply=args.apply,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["ok"] else 2
    except (InstallError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
