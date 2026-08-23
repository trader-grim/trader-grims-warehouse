"""Materialize shared tgw-plan and Promptcraft adapters without copying policy."""

from __future__ import annotations

import argparse
import base64
import copy
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

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
            Adapter("tgw-plan", skill, home / ".claude/skills/tgw-plan"),
            Adapter("tgw-review", review, home / ".claude/skills/tgw-review"),
            Adapter("promptcraft", provider, home / ".claude/providers/promptcraft"),
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


def _file_digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"actor contract source is not a regular file: {path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _replaceable_instruction_file(path: Path) -> bool:
    """Accept only the protected regular-file form used by the bootstrap."""

    try:
        observed = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return (
        stat.S_ISREG(observed.st_mode)
        and observed.st_uid == 0
        and observed.st_mode & 0o222 == 0
    )


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_noreplace(
    source: str | Path,
    destination: str | Path,
    *,
    source_dir_fd: int = -100,
    destination_dir_fd: int = -100,
) -> None:
    """Linux atomic rename which never overwrites a concurrent pathname."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise InstallError("atomic no-replace rename is unavailable")
    result = renameat2(
        ctypes.c_int(source_dir_fd), ctypes.c_char_p(os.fsencode(source)),
        ctypes.c_int(destination_dir_fd), ctypes.c_char_p(os.fsencode(destination)),
        ctypes.c_uint(1),
    )
    if result != 0:
        observed = ctypes.get_errno()
        if observed in {errno.EEXIST, errno.ENOTEMPTY}:
            raise InstallError(f"actor contract path changed concurrently: {destination}")
        raise OSError(observed, os.strerror(observed), str(source), str(destination))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Durably replace one materializer transaction journal."""
    stage = path.with_name(
        f".{path.name}.next-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                json.dumps(
                    value, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, allow_nan=False,
                ).encode() + b"\n"
            )
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), 0, 0)
            os.fsync(handle.fileno())
        os.replace(stage, path)
        _fsync_directory(path.parent)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


def _load_transaction(path: Path) -> dict[str, Any] | None:
    if path.is_symlink():
        raise InstallError("actor materializer transaction journal is unsafe")
    if not path.exists():
        return None
    if not path.is_file():
        raise InstallError("actor materializer transaction journal is unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError("actor materializer transaction journal is invalid") from exc
    if not isinstance(value, dict):
        raise InstallError("actor materializer transaction journal is invalid")
    return value


def _link_target(path: Path) -> str:
    try:
        return os.readlink(path)
    except OSError as exc:
        raise InstallError(f"actor contract link is unavailable: {path}") from exc


def _regular_snapshot(path: Path, limit: int = 16 * 1024 * 1024) -> tuple[os.stat_result, bytes]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        raise InstallError(f"actor contract regular file is unavailable: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallError(f"actor contract path is not a regular file: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise InstallError(f"actor contract file exceeds its bound: {path}")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev or before.st_ino != after.st_ino
            or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise InstallError(f"actor contract file changed during inspection: {path}")
        return after, b"".join(chunks)
    finally:
        os.close(descriptor)


def _legacy_composite_bytes(path: Path) -> bytes:
    if not path.is_symlink():
        raise InstallError("actor legacy composite preimage is not a symlink")
    target = path.resolve(strict=True)
    observed, raw = _regular_snapshot(target)
    if os.geteuid() == 0 and (
        observed.st_uid != 0 or observed.st_mode & 0o022
        or target == Path("/opt/TGW") or Path("/opt/TGW") not in target.parents
    ):
        raise InstallError("actor legacy composite preimage is not protected")
    return raw


def _preimage(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        observed = path.lstat()
        return {
            "type": "symlink",
            "target": _link_target(path),
            "uid": observed.st_uid,
            "gid": observed.st_gid,
            "mode": stat.S_IMODE(observed.st_mode),
        }
    if not path.exists():
        return {"type": "absent"}
    observed, raw = _regular_snapshot(path)
    if not stat.S_ISREG(observed.st_mode):
        raise InstallError(f"actor contract destination is unsafe: {path}")
    return {
        "type": "file",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "uid": observed.st_uid,
        "gid": observed.st_gid,
        "mode": stat.S_IMODE(observed.st_mode),
    }


def _preimage_at(parent_fd: int, name: str) -> dict[str, Any]:
    """Capture an exact preimage through an already-bound parent directory."""
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return {"type": "absent"}
    if stat.S_ISLNK(observed.st_mode):
        return {
            "type": "symlink",
            "target": os.readlink(name, dir_fd=parent_fd),
            "uid": observed.st_uid,
            "gid": observed.st_gid,
            "mode": stat.S_IMODE(observed.st_mode),
        }
    if not stat.S_ISREG(observed.st_mode):
        raise InstallError(f"actor contract destination is unsafe: {name}")
    stable, raw = _snapshot_at(parent_fd, name)
    return {
        "type": "file",
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "uid": stable.st_uid,
        "gid": stable.st_gid,
        "mode": stat.S_IMODE(stable.st_mode),
    }


def _matches_preimage(path: Path, expected: dict[str, Any]) -> bool:
    kind = expected.get("type")
    if kind == "absent":
        return not path.exists() and not path.is_symlink()
    if kind == "symlink":
        if not path.is_symlink() or _link_target(path) != expected.get("target"):
            return False
        observed = path.lstat()
        if (
            observed.st_uid != expected.get("uid")
            or observed.st_gid != expected.get("gid")
            or stat.S_IMODE(observed.st_mode) != expected.get("mode")
        ):
            return False
        expected_referent = expected.get("referent_sha256")
        return (
            expected_referent is None
            or "sha256:" + hashlib.sha256(_legacy_composite_bytes(path)).hexdigest()
            == expected_referent
        )
    if kind == "file":
        if path.is_symlink() or not path.is_file():
            return False
        try:
            observed, raw = _regular_snapshot(path)
        except InstallError:
            return False
        return (
            "sha256:" + hashlib.sha256(raw).hexdigest()
            == expected.get("sha256")
            and observed.st_uid == expected.get("uid")
            and observed.st_gid == expected.get("gid")
            and stat.S_IMODE(observed.st_mode) == expected.get("mode")
        )
    return False


def _matches_desired(path: Path, effect: dict[str, Any]) -> bool:
    if effect.get("materialization") == "symlink":
        return path.is_symlink() and path.resolve(strict=False) == Path(effect["source"])
    if path.is_symlink() or not path.is_file():
        return False
    try:
        observed, raw = _regular_snapshot(path)
    except InstallError:
        return False
    return (
        "sha256:" + hashlib.sha256(raw).hexdigest()
        == effect.get("desired_sha256")
        and observed.st_uid == effect.get("desired_uid")
        and observed.st_gid == effect.get("desired_gid")
        and stat.S_IMODE(observed.st_mode) == effect.get("desired_mode")
    )


def _entry_exists_at(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _snapshot_at(
    parent_fd: int, name: str, limit: int = 16 * 1024 * 1024
) -> tuple[os.stat_result, bytes]:
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as exc:
        raise InstallError(f"actor contract regular file is unavailable: {name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise InstallError(f"actor contract path is not a regular file: {name}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise InstallError(f"actor contract file exceeds its bound: {name}")
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev or before.st_ino != after.st_ino
            or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise InstallError(f"actor contract file changed during inspection: {name}")
        return after, b"".join(chunks)
    finally:
        os.close(descriptor)


def _legacy_bytes_at(parent_fd: int, name: str) -> bytes:
    target_text = os.readlink(name, dir_fd=parent_fd)
    target = Path(target_text)
    if not target.is_absolute():
        target = (Path(f"/proc/self/fd/{parent_fd}") / target).resolve(strict=True)
    observed, raw = _regular_snapshot(target.resolve(strict=True))
    if os.geteuid() == 0 and (
        observed.st_uid != 0 or observed.st_mode & 0o022
        or target == Path("/opt/TGW") or Path("/opt/TGW") not in target.parents
    ):
        raise InstallError("actor legacy composite preimage is not protected")
    return raw


def _retained_referent_digest_at(parent_fd: int, name: str) -> str:
    """Hash one legacy symlink referent without accepting mutable root input."""
    target_text = os.readlink(name, dir_fd=parent_fd)
    target = Path(target_text)
    if not target.is_absolute():
        target = Path(f"/proc/self/fd/{parent_fd}") / target
    target = target.resolve(strict=True)
    candidates = [target]
    if target.is_dir() and not target.is_symlink():
        candidates.extend(target.rglob("*"))
    digest = hashlib.sha256()
    files: list[Path] = []
    for candidate in candidates:
        observed = candidate.lstat()
        if candidate.is_symlink() or not (
            stat.S_ISREG(observed.st_mode) or stat.S_ISDIR(observed.st_mode)
        ):
            raise InstallError("actor legacy referent contains an unsafe entry")
        if os.geteuid() == 0 and (
            observed.st_uid != 0
            or observed.st_mode & 0o022
            or target == Path("/opt/TGW")
            or Path("/opt/TGW") not in target.parents
        ):
            raise InstallError("actor legacy referent is not protected")
        if stat.S_ISREG(observed.st_mode):
            files.append(candidate)
    if target.is_file():
        _observed, raw = _regular_snapshot(target)
        return "sha256:" + hashlib.sha256(raw).hexdigest()
    for candidate in sorted(
        files, key=lambda item: item.relative_to(target).as_posix()
    ):
        relative = candidate.relative_to(target).as_posix()
        _observed, raw = _regular_snapshot(candidate)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _matches_preimage_at(
    parent_fd: int, name: str, expected: Mapping[str, Any]
) -> bool:
    kind = expected.get("type")
    if kind == "absent":
        return not _entry_exists_at(parent_fd, name)
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if kind == "symlink":
        if not stat.S_ISLNK(observed.st_mode):
            return False
        return (
            os.readlink(name, dir_fd=parent_fd) == expected.get("target")
            and observed.st_uid == expected.get("uid")
            and observed.st_gid == expected.get("gid")
            and stat.S_IMODE(observed.st_mode) == expected.get("mode")
            and (
                expected.get("referent_sha256") is None
                or _retained_referent_digest_at(parent_fd, name)
                == expected.get("referent_sha256")
            )
        )
    if kind == "file":
        try:
            observed, raw = _snapshot_at(parent_fd, name)
        except InstallError:
            return False
        return (
            "sha256:" + hashlib.sha256(raw).hexdigest() == expected.get("sha256")
            and observed.st_uid == expected.get("uid")
            and observed.st_gid == expected.get("gid")
            and stat.S_IMODE(observed.st_mode) == expected.get("mode")
        )
    return False


def _matches_desired_at(parent_fd: int, name: str, effect: Mapping[str, Any]) -> bool:
    if effect.get("materialization") == "symlink":
        try:
            observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            target = os.readlink(name, dir_fd=parent_fd)
        except (FileNotFoundError, OSError):
            return False
        return (
            stat.S_ISLNK(observed.st_mode)
            and target == str(Path(str(effect["source"])))
        )
    try:
        observed, raw = _snapshot_at(parent_fd, name)
    except InstallError:
        return False
    return (
        "sha256:" + hashlib.sha256(raw).hexdigest() == effect.get("desired_sha256")
        and observed.st_uid == effect.get("desired_uid")
        and observed.st_gid == effect.get("desired_gid")
        and stat.S_IMODE(observed.st_mode) == effect.get("desired_mode")
    )


def _destination_parent_identity(parent: Path, allowed_root: Path) -> tuple[int, int]:
    if (
        not parent.is_dir()
        or
        parent.is_symlink()
        or parent.resolve(strict=True) != parent
        or (parent != allowed_root and allowed_root not in parent.parents)
    ):
        raise InstallError("actor contract destination parent escapes its account root")
    descriptor = os.open(
        parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    try:
        observed = os.fstat(descriptor)
        return observed.st_dev, observed.st_ino
    finally:
        os.close(descriptor)


def _write_effect_stage_at(
    parent_fd: int, stage_name: str, effect: dict[str, Any], desired: bytes | None
) -> None:
    if _entry_exists_at(parent_fd, stage_name):
        if not _matches_desired_at(parent_fd, stage_name, effect):
            raise InstallError(f"actor contract staging path changed: {stage_name}")
        return
    if effect.get("materialization") == "symlink":
        os.symlink(
            Path(effect["source"]), stage_name,
            target_is_directory=bool(effect.get("source_is_directory")),
            dir_fd=parent_fd,
        )
        return
    if not isinstance(desired, bytes):
        raise InstallError("actor MCP projection bytes are unavailable")
    descriptor = os.open(
        stage_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_fd,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(desired)
            handle.flush()
            os.fchown(handle.fileno(), int(effect["desired_uid"]), int(effect["desired_gid"]))
            os.fchmod(handle.fileno(), int(effect["desired_mode"]))
            os.fsync(handle.fileno())
    except Exception:
        if _entry_exists_at(parent_fd, stage_name):
            os.unlink(stage_name, dir_fd=parent_fd)
        raise


def _apply_effect(effect: dict[str, Any], desired: bytes | None) -> None:
    destination = Path(effect["destination"])
    stage = Path(effect["stage"])
    backup = Path(effect["backup"]) if effect.get("backup") else None
    preimage = effect["preimage"]
    try:
        resolved_parent = destination.parent.resolve(strict=True)
        allowed_root = Path(effect["allowed_root"]).resolve(strict=True)
    except OSError as exc:
        raise InstallError("actor contract destination parent is unavailable") from exc
    if (
        resolved_parent != destination.parent
        or (resolved_parent != allowed_root and allowed_root not in resolved_parent.parents)
        or destination.parent.is_symlink()
    ):
        raise InstallError("actor contract destination parent escapes its account root")
    parent_fd = os.open(
        destination.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    transaction_fd = os.open(
        stage.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        parent_state = os.fstat(parent_fd)
        transaction_state = os.fstat(transaction_fd)
        if (
            parent_state.st_dev != effect.get("parent_device")
            or parent_state.st_ino != effect.get("parent_inode")
            or transaction_state.st_dev != effect.get("transaction_device")
            or transaction_state.st_ino != effect.get("transaction_inode")
            or transaction_state.st_dev != parent_state.st_dev
        ):
            raise InstallError("actor contract destination parent identity changed")
        destination_name = destination.name
        stage_name = stage.name
        backup_name = backup.name if backup is not None else None
        if backup is not None and backup.parent != stage.parent:
            raise InstallError("actor contract transaction paths cross parents")

        if _matches_desired_at(parent_fd, destination_name, effect):
            if backup_name is not None and not _matches_preimage_at(
                transaction_fd, backup_name, preimage
            ):
                raise InstallError(f"actor contract rollback preimage changed: {backup}")
            if _entry_exists_at(transaction_fd, stage_name):
                if not _matches_desired_at(transaction_fd, stage_name, effect):
                    raise InstallError(f"actor contract staging path changed: {stage}")
                os.unlink(stage_name, dir_fd=transaction_fd)
                os.fsync(transaction_fd)
            return

        _write_effect_stage_at(transaction_fd, stage_name, effect, desired)
        if backup_name is None:
            if not _matches_preimage_at(parent_fd, destination_name, preimage):
                raise InstallError(f"actor contract destination changed concurrently: {destination}")
        elif _matches_preimage_at(parent_fd, destination_name, preimage):
            if _entry_exists_at(transaction_fd, backup_name):
                raise InstallError(f"actor contract rollback path conflicts: {backup}")
            _rename_noreplace(
                destination_name, backup_name,
                source_dir_fd=parent_fd, destination_dir_fd=transaction_fd,
            )
            os.fsync(parent_fd)
            os.fsync(transaction_fd)
            if not _matches_preimage_at(transaction_fd, backup_name, preimage):
                if not _entry_exists_at(parent_fd, destination_name):
                    _rename_noreplace(
                        backup_name, destination_name,
                        source_dir_fd=transaction_fd, destination_dir_fd=parent_fd,
                    )
                    os.fsync(parent_fd)
                    os.fsync(transaction_fd)
                raise InstallError(f"actor contract destination changed concurrently: {destination}")
        elif (
            not _matches_preimage_at(transaction_fd, backup_name, preimage)
            or _entry_exists_at(parent_fd, destination_name)
        ):
            raise InstallError(f"actor contract destination changed concurrently: {destination}")
        _rename_noreplace(
            stage_name, destination_name,
            source_dir_fd=transaction_fd, destination_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
        os.fsync(transaction_fd)
        if not _matches_desired_at(parent_fd, destination_name, effect):
            raise InstallError(f"actor contract materialization verification failed: {destination}")
    finally:
        os.close(transaction_fd)
        os.close(parent_fd)


def _apply_retirement(retirement: Mapping[str, Any]) -> None:
    """Move one contract-owned legacy sibling into the protected transaction."""
    path = Path(str(retirement.get("path")))
    retained = Path(str(retirement.get("retained")))
    preimage = retirement.get("preimage")
    if not isinstance(preimage, Mapping) or preimage.get("type") != "symlink":
        raise InstallError("actor legacy retirement preimage is invalid")
    if retained.parent == path.parent:
        raise InstallError("actor legacy retirement is not external")
    parent_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    transaction_fd = os.open(
        retained.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        parent_state = os.fstat(parent_fd)
        transaction_state = os.fstat(transaction_fd)
        if (
            parent_state.st_dev != retirement.get("parent_device")
            or parent_state.st_ino != retirement.get("parent_inode")
            or transaction_state.st_dev != retirement.get("transaction_device")
            or transaction_state.st_ino != retirement.get("transaction_inode")
            or transaction_state.st_dev != parent_state.st_dev
        ):
            raise InstallError("actor legacy retirement parent identity changed")
        if _matches_preimage_at(transaction_fd, retained.name, preimage):
            if _entry_exists_at(parent_fd, path.name):
                raise InstallError("actor legacy retirement is ambiguous")
            return
        if not _matches_preimage_at(parent_fd, path.name, preimage):
            raise InstallError("actor legacy retirement source changed")
        if _entry_exists_at(transaction_fd, retained.name):
            raise InstallError("actor legacy retirement target conflicts")
        _rename_noreplace(
            path.name,
            retained.name,
            source_dir_fd=parent_fd,
            destination_dir_fd=transaction_fd,
        )
        os.fsync(parent_fd)
        os.fsync(transaction_fd)
        if (
            _entry_exists_at(parent_fd, path.name)
            or not _matches_preimage_at(transaction_fd, retained.name, preimage)
        ):
            raise InstallError("actor legacy retirement verification failed")
    finally:
        os.close(transaction_fd)
        os.close(parent_fd)


def _restore_retirement(retirement: Mapping[str, Any]) -> None:
    """Restore a retired sibling unless its preimage was adopted as a binding."""
    if retirement.get("adopted_by_destination"):
        return
    path = Path(str(retirement.get("path")))
    retained = Path(str(retirement.get("retained")))
    preimage = retirement.get("preimage")
    if not isinstance(preimage, Mapping) or preimage.get("type") != "symlink":
        raise InstallError("actor legacy retirement preimage is invalid")
    parent_fd = os.open(
        path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    )
    transaction_fd = os.open(
        retained.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        parent_state = os.fstat(parent_fd)
        transaction_state = os.fstat(transaction_fd)
        if (
            parent_state.st_dev != retirement.get("parent_device")
            or parent_state.st_ino != retirement.get("parent_inode")
            or transaction_state.st_dev != retirement.get("transaction_device")
            or transaction_state.st_ino != retirement.get("transaction_inode")
            or transaction_state.st_dev != parent_state.st_dev
        ):
            raise InstallError("actor legacy retirement parent identity changed")
        if _matches_preimage_at(parent_fd, path.name, preimage):
            if _entry_exists_at(transaction_fd, retained.name):
                raise InstallError("actor legacy rollback retains an ambiguous copy")
            return
        if _entry_exists_at(parent_fd, path.name):
            raise InstallError("actor legacy rollback destination changed")
        if not _matches_preimage_at(transaction_fd, retained.name, preimage):
            raise InstallError("actor legacy rollback preimage is unavailable")
        _rename_noreplace(
            retained.name,
            path.name,
            source_dir_fd=transaction_fd,
            destination_dir_fd=parent_fd,
        )
        os.fsync(parent_fd)
        os.fsync(transaction_fd)
        if not _matches_preimage_at(parent_fd, path.name, preimage):
            raise InstallError("actor legacy rollback verification failed")
    finally:
        os.close(transaction_fd)
        os.close(parent_fd)


def _bundle_binding_hash(bindings: list[dict[str, str]]) -> str:
    encoded = json.dumps(
        bindings, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


_TOML_TABLE = re.compile(r"^\s*\[.*\]\s*(?:#.*)?$")
_CODEX_CONTEXT_TABLE = re.compile(
    r'^\s*\[\s*mcp_servers\.(?:"tgw-context"|tgw-context)(?:\.env)?\s*\]\s*(?:#.*)?$'
)
_MAX_COMPOSITE_CONFIG = 16 * 1024 * 1024
_LEGACY_RETIREMENT_SUFFIXES = (
    ".tgw-w18-previous",
    ".tgw-w18-previous.pre-a531-preserved-20260823",
)


def _registration_endpoint(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_COMPOSITE_CONFIG:
            raise InstallError("actor MCP registration exceeds its bound")
        if path.suffix == ".toml":
            endpoint = tomllib.loads(raw.decode("utf-8"))["mcp_servers"]["tgw-context"]
        else:
            endpoint = json.loads(raw)["mcpServers"]["tgw-context"]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise InstallError("actor MCP registration fragment is invalid") from exc
    if not isinstance(endpoint, dict):
        raise InstallError("actor MCP registration fragment is invalid")
    return endpoint


def _composite_projection(actor: str, home: Path, destination: Path) -> str | None:
    if actor == "claude" and destination in {
        home / ".claude.json",
        home / ".claude" / ".mcp.json",
    }:
        return "claude-user-json"
    if actor == "codex" and destination in {
        home / ".codex" / "config.toml",
        home / ".tgw" / "codex-home" / "config.toml",
    }:
        return "codex-user-toml"
    if actor == "deepseek" and destination == home / ".dsh" / "cordis.patch.yml":
        return "deepseek-patch-yaml"
    return None


def _read_composite(path: Path) -> bytes:
    if path.is_symlink():
        raise InstallError(f"actor composite MCP store is unsafe: {path}")
    if not path.exists():
        return b""
    if not path.is_file():
        raise InstallError(f"actor composite MCP store is unsafe: {path}")
    raw = path.read_bytes()
    if len(raw) > _MAX_COMPOSITE_CONFIG:
        raise InstallError(f"actor composite MCP store exceeds its bound: {path}")
    return raw


def _project_claude_registration(current: bytes, endpoint: dict[str, Any]) -> bytes:
    try:
        value = json.loads(current) if current else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("Claude user MCP store is invalid") from exc
    if not isinstance(value, dict):
        raise InstallError("Claude user MCP store is invalid")
    servers = value.get("mcpServers")
    if servers is None:
        servers = {}
        value["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise InstallError("Claude user MCP table is invalid")
    servers["tgw-context"] = copy.deepcopy(endpoint)
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()


def _project_codex_registration(current: bytes, endpoint: dict[str, Any], fragment: bytes) -> bytes:
    try:
        content = current.decode("utf-8")
        parsed = tomllib.loads(content) if content else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError("Codex user MCP store is invalid") from exc
    if not isinstance(parsed, dict):
        raise InstallError("Codex user MCP store is invalid")
    expected = copy.deepcopy(parsed)
    servers = expected.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise InstallError("Codex user MCP table is invalid")
    servers["tgw-context"] = copy.deepcopy(endpoint)

    lines = content.splitlines(keepends=True)
    retained: list[str] = []
    matched = False
    skipping = False
    for line in lines:
        if _CODEX_CONTEXT_TABLE.fullmatch(line.rstrip("\r\n")):
            matched = True
            skipping = True
            continue
        if skipping and _TOML_TABLE.fullmatch(line.rstrip("\r\n")):
            skipping = False
        if not skipping:
            retained.append(line)
    existing = parsed.get("mcp_servers", {}).get("tgw-context") if isinstance(parsed.get("mcp_servers"), dict) else None
    if existing is not None and not matched:
        raise InstallError("Codex tgw-context entry uses an unsupported inline layout")
    prefix = "".join(retained).rstrip() + "\n\n" if retained else ""
    merged = prefix.encode() + fragment
    try:
        observed = tomllib.loads(merged.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise InstallError("Codex MCP projection is invalid") from exc
    if observed != expected:
        raise InstallError("Codex MCP projection changed unrelated configuration")
    return merged


def _deepseek_target_rows(raw: bytes) -> list[tuple[int, int, int]]:
    """Return byte/string spans for the unique effective Context insert row.

    PyYAML's compose layer accepts unknown tags without constructing them and
    exposes source marks.  We use it only to validate structure and locate the
    managed row; unrelated YAML, comments, and ``!!js`` values remain byte-for-
    byte untouched.
    """
    try:
        import yaml
    except ImportError as exc:
        raise InstallError("DeepSeek YAML support is unavailable") from exc
    try:
        text = raw.decode("utf-8")
        root = yaml.compose(text, Loader=yaml.BaseLoader)
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InstallError("DeepSeek MCP patch store is invalid") from exc
    if root is None:
        return []
    if not isinstance(root, yaml.SequenceNode):
        raise InstallError("DeepSeek MCP patch root must be a sequence")
    target_rows: list[tuple[int, int, int]] = []
    seen_ids: set[str] = set()
    for operation in root.value:
        if not isinstance(operation, yaml.MappingNode):
            raise InstallError("DeepSeek MCP patch operation is invalid")
        for key, value in operation.value:
            if not isinstance(key, yaml.ScalarNode):
                raise InstallError("DeepSeek MCP patch operation key is invalid")
            if key.value != "insert":
                continue
            if not isinstance(value, yaml.SequenceNode):
                raise InstallError("DeepSeek MCP insert operation is invalid")
            for row in value.value:
                if not isinstance(row, yaml.MappingNode):
                    raise InstallError("DeepSeek MCP insert row is invalid")
                identifiers = [
                    entry_value.value
                    for entry_key, entry_value in row.value
                    if isinstance(entry_key, yaml.ScalarNode)
                    and entry_key.value == "id"
                    and isinstance(entry_value, yaml.ScalarNode)
                ]
                if len(identifiers) != 1 or not identifiers[0]:
                    raise InstallError("DeepSeek MCP insert row identity is invalid")
                identifier = identifiers[0]
                if identifier in seen_ids:
                    raise InstallError(
                        f"DeepSeek MCP insert identity is duplicated: {identifier}"
                    )
                seen_ids.add(identifier)
                if identifier != "tgw-context":
                    continue
                start = row.start_mark.index
                line_start = text.rfind("\n", 0, start) + 1
                prefix = text[line_start:start]
                dash = prefix.rfind("-")
                if dash < 0 or prefix[dash:] != "- ":
                    raise InstallError("DeepSeek MCP target row layout is unsupported")
                indentation = dash
                # Composer marks skip comments and may therefore extend a row
                # through a following, less-indented comment.  Bound the
                # managed splice lexically at the first line which cannot be
                # part of this sequence row, preserving unrelated comments
                # byte-for-byte.
                cursor = text.find("\n", line_start)
                cursor = len(text) if cursor < 0 else cursor + 1
                row_end = cursor
                while cursor < len(text):
                    next_end = text.find("\n", cursor)
                    next_end = len(text) if next_end < 0 else next_end + 1
                    line = text[cursor:next_end]
                    stripped = line.lstrip(" ")
                    leading = len(line) - len(stripped)
                    if stripped.strip() and leading <= indentation:
                        break
                    row_end = next_end
                    cursor = next_end
                target_rows.append((line_start, row_end, indentation))
    if len(target_rows) > 1:
        raise InstallError("DeepSeek MCP target is duplicated")
    return target_rows


def _reindent_yaml_row(row: str, source_indent: int, target_indent: int) -> str:
    lines = row.splitlines(keepends=True)
    rewritten: list[str] = []
    for line in lines:
        if not line.strip():
            rewritten.append(line)
        elif not line.startswith(" " * source_indent):
            raise InstallError("DeepSeek MCP generated row indentation is invalid")
        else:
            rewritten.append(" " * target_indent + line[source_indent:])
    return "".join(rewritten)


def _project_deepseek_registration(current: bytes, fragment: bytes) -> bytes:
    source_rows = _deepseek_target_rows(fragment)
    if len(source_rows) != 1:
        raise InstallError("DeepSeek generated MCP patch must define one target")
    source_text = fragment.decode("utf-8")
    source_start, source_end, source_indent = source_rows[0]
    source_row = source_text[source_start:source_end]
    current_rows = _deepseek_target_rows(current)
    if not current_rows:
        if not current:
            desired = fragment
        else:
            separator = b"" if current.endswith(b"\n") else b"\n"
            desired = current + separator + fragment
    else:
        current_text = current.decode("utf-8")
        target_start, target_end, target_indent = current_rows[0]
        replacement = _reindent_yaml_row(
            source_row, source_indent, target_indent
        )
        desired = (
            current_text[:target_start] + replacement + current_text[target_end:]
        ).encode()
    if len(_deepseek_target_rows(desired)) != 1:
        raise InstallError("DeepSeek MCP projection is ambiguous")
    return desired


def _project_registration(
    projection: str,
    destination: Path,
    source: Path,
    *,
    current_override: bytes | None = None,
) -> tuple[bytes, bytes, bool]:
    current = _read_composite(destination) if current_override is None else current_override
    fragment = source.read_bytes()
    if projection == "deepseek-patch-yaml":
        desired = _project_deepseek_registration(current, fragment)
        return current, desired, current == desired
    endpoint = _registration_endpoint(source)
    if projection == "claude-user-json":
        desired = _project_claude_registration(current, endpoint)
        try:
            value = json.loads(current) if current else {}
            active = value.get("mcpServers", {}).get("tgw-context") if isinstance(value, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            active = None
    elif projection == "codex-user-toml":
        desired = _project_codex_registration(current, endpoint, fragment)
        try:
            value = tomllib.loads(current.decode("utf-8")) if current else {}
            active = value.get("mcp_servers", {}).get("tgw-context") if isinstance(value, dict) else None
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            active = None
    else:
        raise InstallError("actor MCP projection type is invalid")
    return current, desired, active == endpoint


def _materialize_complete_actor_contracts(
    bundle: dict[str, Any], *, source_root: str | Path,
    contracts: dict[str, dict[str, Any]], trusted_contract_public_key: str,
    apply: bool = False, replace_existing: bool = False,
    additional_source_roots: tuple[str | Path, ...] = (),
    transaction_journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Materialize instructions, skills, MCP, launcher and bootstrap as one journal.

    Service/process activation deliberately remains the next step in the same
    W18 quiet refresh transaction.  Returning from this function alone never
    marks an actor active or verified.
    """
    if (
        not isinstance(bundle, dict)
        or set(bundle) != {"schema", "generation", "actors"}
        or bundle.get("schema") != "tgw-complete-actor-contract-bundle/v1"
        or not isinstance(bundle.get("generation"), str)
        or not bundle["generation"].startswith("sha256:")
        or not isinstance(bundle.get("actors"), dict)
        or not bundle["actors"]
        or set(bundle["actors"]) != set(contracts)
    ):
        raise InstallError("complete actor contract bundle is invalid")
    if not isinstance(trusted_contract_public_key, str):
        raise InstallError("fleet contract signer is invalid")
    root = Path(source_root).resolve(strict=True)
    raw_additional_roots = tuple(Path(path) for path in additional_source_roots)
    if any(path.is_symlink() or not path.is_dir() for path in raw_additional_roots):
        raise InstallError("complete actor source root is invalid")
    trusted_roots = (root, *(path.resolve(strict=True) for path in raw_additional_roots))
    journal_path: Path | None = None
    resume_transaction: dict[str, Any] | None = None
    resume_effects: dict[str, Mapping[str, Any]] = {}
    if apply:
        if transaction_journal_path is None:
            raise InstallError("actor materialization requires a durable transaction journal")
        journal_path = Path(transaction_journal_path)
        if (
            not journal_path.is_absolute()
            or journal_path == Path("/tmp")
            or Path("/tmp") in journal_path.parents
            or journal_path.parent.is_symlink()
            or not journal_path.parent.is_dir()
        ):
            raise InstallError("actor materializer transaction journal path is not durable")
        resume_transaction = _load_transaction(journal_path)
        if resume_transaction is not None:
            if (
                resume_transaction.get("schema")
                != "tgw-w18-actor-materializer-transaction/v1"
                or resume_transaction.get("generation") != bundle["generation"]
                or not isinstance(resume_transaction.get("effects"), list)
            ):
                raise InstallError("actor materializer transaction binding differs")
            resume_effects = {
                str(effect.get("destination")): effect
                for effect in resume_transaction["effects"]
                if isinstance(effect, Mapping)
            }
    plans: list[dict[str, Any]] = []
    for actor in sorted(bundle["actors"]):
        contract = contracts[actor]
        if (
            not isinstance(contract, dict)
            or contract.get("actor") != actor
            or contract.get("status") != "READY"
            or not _contract_receipt_matches(contract)
            or not _contract_signature_matches(contract, trusted_contract_public_key)
        ):
            raise InstallError(f"actor contract is not READY: {actor}")
        specification = bundle["actors"][actor]
        if not isinstance(specification, dict) or set(specification) != {"home", "project", "bindings"}:
            raise InstallError(f"actor bundle entry is invalid: {actor}")
        home = Path(str(specification["home"])).resolve()
        project = Path(str(specification["project"])).resolve()
        bindings = specification["bindings"]
        if not isinstance(bindings, list) or not bindings:
            raise InstallError(f"actor bundle has no bindings: {actor}")
        observed: dict[str, dict[str, str]] = {
            "instruction": {}, "skill": {}, "hook": {}, "mcp": {},
            "launcher": {}, "bootstrap": {}, "environment": {},
            "contract": {},
        }
        binding_names: dict[str, set[str]] = {
            key: set() for key in observed
        }
        actor_plans: list[dict[str, Any]] = []
        destinations: set[Path] = set()
        for raw in bindings:
            if (
                not isinstance(raw, dict)
                or set(raw) not in (
                    {"kind", "name", "source", "destination", "sha256"},
                    {"kind", "name", "source", "destination", "sha256", "endpoint"},
                    {
                        "kind", "name", "source", "destination", "sha256",
                        "capability",
                    },
                )
                or ("endpoint" in raw and raw.get("kind") != "mcp")
                or (
                    "capability" in raw
                    and raw.get("kind") not in {"instruction", "skill"}
                )
            ):
                raise InstallError(f"actor binding is invalid: {actor}")
            kind, name = raw["kind"], raw["name"]
            if kind not in observed or not isinstance(name, str) or not name:
                raise InstallError(f"actor binding kind or name is invalid: {actor}")
            source_value = Path(str(raw["source"]))
            source = (
                source_value.resolve(strict=True) if source_value.is_absolute()
                else (root / source_value).resolve(strict=True)
            )
            destination = Path(str(raw["destination"])).absolute()
            if not any(base == source or base in source.parents for base in trusted_roots):
                raise InstallError(f"actor binding source escapes the candidate: {actor}:{name}")
            if not destination.is_absolute() or not any(base == destination or base in destination.parents for base in (home, project)):
                raise InstallError(f"actor binding destination escapes its account roots: {actor}:{name}")
            existing_parent = destination.parent
            while not existing_parent.exists() and not existing_parent.is_symlink():
                existing_parent = existing_parent.parent
            if existing_parent.is_symlink() or existing_parent.resolve(strict=True) != existing_parent:
                raise InstallError(f"actor binding destination parent is unsafe: {actor}:{name}")
            if destination in destinations:
                raise InstallError(f"actor binding destination is duplicated: {actor}:{name}")
            destinations.add(destination)
            digest = tree_digest(source) if source.is_dir() and not source.is_symlink() else _file_digest(source)
            if raw["sha256"] != digest or name in binding_names[kind]:
                raise InstallError(f"actor binding digest or identity is invalid: {actor}:{name}")
            binding_names[kind].add(name)
            endpoint = raw.get("endpoint", name) if kind == "mcp" else None
            capability = (
                raw.get("capability", name)
                if kind in {"instruction", "skill"} else name
            )
            if not isinstance(capability, str) or not capability:
                raise InstallError(
                    f"actor binding capability is invalid: {actor}:{name}"
                )
            if kind == "instruction" and (
                name != "agent-entry-point"
                or capability != "agent-entry-point"
                or not source.is_file()
                or source.is_symlink()
            ):
                raise InstallError(
                    f"actor instruction binding is invalid: {actor}:{name}"
                )
            if kind == "mcp":
                if not isinstance(endpoint, str) or not endpoint:
                    raise InstallError(f"actor MCP endpoint is invalid: {actor}:{name}")
                previous = observed[kind].get(endpoint)
                if previous is not None and previous != digest:
                    raise InstallError(
                        f"actor MCP endpoint materializations differ: {actor}:{endpoint}"
                    )
                observed[kind][endpoint] = digest
            else:
                previous = observed[kind].get(capability)
                if previous is not None and previous != digest:
                    raise InstallError(
                        "actor capability materializations differ: "
                        f"{actor}:{capability}"
                    )
                observed[kind][capability] = digest
            projection = _composite_projection(actor, home, destination) if kind == "mcp" else None
            fixed_backup = destination.with_name(
                f".{destination.name}.tgw-w18-previous"
            )
            legacy_backup = (
                fixed_backup
                if not destination.exists() and not destination.is_symlink()
                and fixed_backup.is_symlink()
                else None
            )
            projected_current: bytes | None = None
            projected_desired: bytes | None = None
            if projection is not None:
                current_override = None
                resume_effect = resume_effects.get(str(destination))
                if (
                    resume_effect is not None
                    and not destination.exists() and not destination.is_symlink()
                ):
                    backup_value = resume_effect.get("backup")
                    backup = Path(str(backup_value)) if isinstance(backup_value, str) else None
                    if backup is None or (not backup.is_symlink() and not backup.is_file()):
                        raise InstallError(
                            f"actor composite MCP transaction preimage is unavailable: {destination}"
                        )
                    current_override = (
                        _legacy_composite_bytes(backup)
                        if backup.is_symlink() else _regular_snapshot(backup)[1]
                    )
                elif legacy_backup is not None:
                    current_override = _legacy_composite_bytes(legacy_backup)
                projected_current, projected_desired, projection_is_current = _project_registration(
                    projection, destination, source, current_override=current_override
                )
                state = "CURRENT" if projection_is_current else "PROJECTABLE"
            elif destination.is_symlink() and destination.resolve(strict=False) == source:
                state = "CURRENT"
            elif destination.is_symlink() and replace_existing:
                state = "REPLACEABLE"
            elif (
                kind == "instruction"
                and replace_existing
                and _replaceable_instruction_file(destination)
            ):
                state = "REPLACEABLE"
            elif (
                replace_existing
                and not destination.exists() and not destination.is_symlink()
                and destination.with_name(
                    f".{destination.name}.tgw-w18-previous"
                ).is_symlink()
            ):
                # Adopt only the historical fixed rollback link as this
                # destination's exact frozen preimage.  Unrelated siblings are
                # never removed or reused.
                state = "REPLACEABLE"
            elif destination.exists() or destination.is_symlink():
                state = "CONFLICT"
            else:
                state = "MISSING"
            actor_plans.append({
                "kind": kind, "name": name, "source": source,
                "destination": destination, "sha256": digest, "state": state,
                "projection": projection,
                "projected_current": projected_current,
                "projected_desired": projected_desired,
                "home": home,
                "project": project,
                "legacy_backup": legacy_backup,
                "endpoint": endpoint,
                "capability": capability,
            })
        local = contract.get("local") if isinstance(contract.get("local"), dict) else {}
        bootstrap_binding = next(
            item for item in actor_plans if item["kind"] == "bootstrap"
        )
        try:
            bootstrap = json.loads(
                bootstrap_binding["source"].read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError(
                f"actor bootstrap receipt is invalid: {actor}"
            ) from exc
        if not isinstance(bootstrap, dict):
            raise InstallError(f"actor bootstrap receipt is invalid: {actor}")
        bootstrap_instructions = bootstrap.get("instructions", {})
        if not isinstance(bootstrap_instructions, dict):
            raise InstallError(
                f"actor bootstrap instruction binding is invalid: {actor}"
            )
        instruction_bindings = {
            item["capability"]: {
                "path": str(item["destination"]),
                "sha256": item["sha256"],
            }
            for item in actor_plans if item["kind"] == "instruction"
        }
        mcp_bindings = [
            {"endpoint": item["endpoint"], "source_sha256": item["sha256"], "destination": str(item["destination"])}
            for item in actor_plans if item["kind"] == "mcp"
        ]
        mcp_bindings.sort(key=lambda item: item["endpoint"])
        if (
            observed["skill"] != local.get("skills")
            or observed["hook"] != local.get("hooks")
            or set(observed["launcher"]) != {"launcher"}
            or observed["launcher"]["launcher"] != local.get("launcher", {}).get("sha256")
            or local.get("launcher", {}).get("path") != str(next(item["destination"] for item in actor_plans if item["kind"] == "launcher"))
            or set(observed["bootstrap"]) != {"bootstrap-receipt"}
            or observed["bootstrap"]["bootstrap-receipt"] != local.get("bootstrap_receipt_hash")
            or instruction_bindings != bootstrap_instructions
            or set(observed["environment"]) != {"environment-catalog"}
            or set(observed["contract"]) != {"actor-contract"}
            or set(observed["mcp"]) != set(local.get("mcp", {}).get("endpoints", []))
            or _bundle_binding_hash(mcp_bindings) != local.get("mcp", {}).get("binding_hash")
        ):
            raise InstallError(f"complete bundle differs from signed actor contract: {actor}")
        environment = next(item for item in actor_plans if item["kind"] == "environment")
        try:
            catalog = json.loads(environment["source"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InstallError(f"actor environment catalog is invalid: {actor}") from exc
        canonical_catalog = json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if "sha256:" + hashlib.sha256(canonical_catalog).hexdigest() != contract.get("catalog_hash"):
            raise InstallError(f"actor environment catalog differs from signed contract: {actor}")
        if any(item["state"] == "CONFLICT" for item in actor_plans):
            raise InstallError(f"actor contract destination conflict: {actor}")
        plans.extend({**item, "actor": actor} for item in actor_plans)

    effects: list[dict[str, Any]] = []
    effect_by_destination: dict[str, dict[str, Any]] = {}
    if apply:
        assert journal_path is not None
        generation_suffix = bundle["generation"].removeprefix("sha256:")
        desired_bindings: list[dict[str, Any]] = []
        desired_bytes: dict[str, bytes] = {}
        for item in plans:
            destination = item["destination"]
            mode = item["projection"] or "symlink"
            desired_sha256 = (
                "sha256:" + hashlib.sha256(item["projected_desired"]).hexdigest()
                if item["projection"] is not None else item["sha256"]
            )
            desired_bindings.append(
                {
                    "actor": item["actor"], "kind": item["kind"],
                    "name": item["name"], "destination": str(destination),
                    "source": str(item["source"]), "materialization": mode,
                    "desired_sha256": desired_sha256,
                    **(
                        {"capability": item["capability"]}
                        if item["kind"] in {"instruction", "skill"} else {}
                    ),
                }
            )
            if item["projection"] is not None:
                desired = item["projected_desired"]
                if not isinstance(desired, bytes):
                    raise InstallError("actor MCP projection bytes are unavailable")
                desired_bytes[str(destination)] = desired
        desired_hash = "sha256:" + hashlib.sha256(
            json.dumps(
                desired_bindings, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode()
        ).hexdigest()
        transaction = resume_transaction
        if transaction is None:
            transaction_key = hashlib.sha256(
                f"{journal_path}:{bundle['generation']}".encode()
            ).hexdigest()
            transaction_root = (
                journal_path.parent / "actor-binding-transactions" / transaction_key
            )
            transaction_root.parent.mkdir(mode=0o700, exist_ok=True)
            transaction_root.mkdir(mode=0o700, exist_ok=True)
            if (
                transaction_root.parent.is_symlink()
                or transaction_root.is_symlink()
                or transaction_root.resolve(strict=True) != transaction_root
            ):
                raise InstallError("actor binding transaction root is unsafe")
            if os.geteuid() == 0:
                for protected in (transaction_root.parent, transaction_root):
                    observed = protected.stat(follow_symlinks=False)
                    if observed.st_uid != 0 or observed.st_mode & 0o022:
                        raise InstallError("actor binding transaction root is not protected")
            transaction_state = transaction_root.stat(follow_symlinks=False)
            retirements: list[dict[str, Any]] = []
            retirement_by_destination: dict[str, dict[str, Any]] = {}
            for item in plans:
                destination = item["destination"]
                allowed_root = (
                    item["home"]
                    if item["home"] == destination or item["home"] in destination.parents
                    else item["project"]
                )
                parent_device, parent_inode = _destination_parent_identity(
                    destination.parent, allowed_root
                )
                parent_fd = os.open(
                    destination.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
                try:
                    parent_state = os.fstat(parent_fd)
                    if (
                        parent_state.st_dev != parent_device
                        or parent_state.st_ino != parent_inode
                    ):
                        raise InstallError(
                            "actor legacy retirement parent identity changed"
                        )
                    prefix = f".{destination.name}.tgw-w18-previous"
                    allowed_names = {
                        f".{destination.name}{suffix}"
                        for suffix in _LEGACY_RETIREMENT_SUFFIXES
                    }
                    unexpected = sorted(
                        name for name in os.listdir(parent_fd)
                        if name.startswith(prefix) and name not in allowed_names
                    )
                    if unexpected:
                        raise InstallError(
                            "actor legacy rollback sibling is not allowlisted: "
                            + unexpected[0]
                        )
                    legacy_entries: list[tuple[Path, dict[str, Any]]] = []
                    for suffix in _LEGACY_RETIREMENT_SUFFIXES:
                        legacy = destination.with_name(
                            f".{destination.name}{suffix}"
                        )
                        preimage = _preimage_at(parent_fd, legacy.name)
                        if preimage.get("type") == "absent":
                            continue
                        if preimage.get("type") != "symlink":
                            raise InstallError(
                                "actor legacy rollback sibling is unsafe: "
                                + legacy.name
                            )
                        preimage["referent_sha256"] = (
                            _retained_referent_digest_at(parent_fd, legacy.name)
                        )
                        legacy_entries.append((legacy, preimage))
                finally:
                    os.close(parent_fd)
                for legacy, preimage in legacy_entries:
                    external = transaction_root / (
                        hashlib.sha256(str(legacy).encode()).hexdigest() + ".legacy"
                    )
                    retirement = {
                        "path": str(legacy), "retained": str(external),
                        "preimage": preimage,
                        "parent_device": parent_device,
                        "parent_inode": parent_inode,
                        "transaction_device": transaction_state.st_dev,
                        "transaction_inode": transaction_state.st_ino,
                        "adopted_by_destination": False,
                    }
                    retirements.append(retirement)
                    if legacy.name == f".{destination.name}.tgw-w18-previous":
                        retirement_by_destination[str(destination)] = retirement
            observations: list[dict[str, Any]] = []
            for item in plans:
                destination = item["destination"]
                allowed_root = (
                    item["home"]
                    if item["home"] == destination or item["home"] in destination.parents
                    else item["project"]
                )
                parent_device, parent_inode = _destination_parent_identity(
                    destination.parent, allowed_root
                )
                if item["state"] not in {"MISSING", "REPLACEABLE", "PROJECTABLE"}:
                    parent_fd = os.open(
                        destination.parent,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    )
                    try:
                        parent_state = os.fstat(parent_fd)
                        if (
                            parent_state.st_dev != parent_device
                            or parent_state.st_ino != parent_inode
                        ):
                            raise InstallError(
                                "actor CURRENT binding parent identity changed"
                            )
                        current_preimage = _preimage_at(parent_fd, destination.name)
                    finally:
                        os.close(parent_fd)
                    observations.append(
                        {
                            "destination": str(destination),
                            "preimage": current_preimage,
                            "parent_device": parent_device,
                            "parent_inode": parent_inode,
                        }
                    )
                    continue
                legacy_backup = item.get("legacy_backup")
                parent_fd = os.open(
                    destination.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
                try:
                    parent_state = os.fstat(parent_fd)
                    if (
                        parent_state.st_dev != parent_device
                        or parent_state.st_ino != parent_inode
                    ):
                        raise InstallError(
                            "actor contract destination parent identity changed"
                        )
                    preimage = _preimage_at(
                        parent_fd,
                        legacy_backup.name
                        if isinstance(legacy_backup, Path)
                        else destination.name,
                    )
                    legacy_referent = (
                        _legacy_bytes_at(parent_fd, legacy_backup.name)
                        if isinstance(legacy_backup, Path)
                        and item["projection"] is not None
                        else None
                    )
                finally:
                    os.close(parent_fd)
                if preimage.get("type") == "symlink" and item["projection"] is not None:
                    preimage["referent_sha256"] = (
                        "sha256:" + hashlib.sha256(legacy_referent).hexdigest()
                    )
                if item["state"] == "MISSING" and preimage.get("type") != "absent":
                    raise InstallError(f"actor contract destination changed concurrently: {destination}")
                if item["state"] != "MISSING" and preimage.get("type") == "absent":
                    raise InstallError(f"actor contract destination changed concurrently: {destination}")
                if (
                    item["kind"] == "instruction"
                    and item["state"] == "REPLACEABLE"
                    and preimage.get("type") == "file"
                    and (
                        preimage.get("uid") != 0
                        or int(preimage.get("mode", 0)) & 0o222
                    )
                ):
                    raise InstallError(
                        f"actor instruction regular file is unsafe: {destination}"
                    )
                if item["projection"] is not None:
                    projected_current = item.get("projected_current")
                    if not isinstance(projected_current, bytes):
                        raise InstallError("actor MCP projection preimage is unavailable")
                    projected_hash = "sha256:" + hashlib.sha256(projected_current).hexdigest()
                    if (
                        (preimage.get("type") == "absent" and projected_current != b"")
                        or (
                            preimage.get("type") == "file"
                            and preimage.get("sha256") != projected_hash
                        )
                        or (
                            preimage.get("type") == "symlink"
                            and preimage.get("referent_sha256") != projected_hash
                        )
                        or preimage.get("type") not in {"absent", "file", "symlink"}
                    ):
                        raise InstallError(
                            f"actor composite MCP store changed concurrently: {destination}"
                        )
                mode = item["projection"] or "symlink"
                desired_sha256 = (
                    "sha256:" + hashlib.sha256(item["projected_desired"]).hexdigest()
                    if item["projection"] is not None else item["sha256"]
                )
                if mode != "symlink":
                    if preimage["type"] == "file":
                        desired_uid = preimage["uid"]
                        desired_gid = preimage["gid"]
                        desired_mode = preimage["mode"]
                    else:
                        home_state = item["home"].stat(follow_symlinks=False)
                        desired_uid = home_state.st_uid
                        desired_gid = home_state.st_gid
                        desired_mode = 0o600
                else:
                    desired_uid = desired_gid = desired_mode = None
                retirement = retirement_by_destination.get(str(destination))
                if isinstance(legacy_backup, Path) and retirement is not None:
                    retirement["adopted_by_destination"] = True
                backup = (
                    Path(str(retirement["retained"]))
                    if isinstance(legacy_backup, Path) and retirement is not None
                    else
                    destination.with_name(
                        f".{destination.name}.tgw-w18-previous-{generation_suffix}"
                    )
                    if preimage["type"] != "absent" else None
                )
                if backup is not None and not (
                    isinstance(legacy_backup, Path) and retirement is not None
                ):
                    backup = transaction_root / (
                        hashlib.sha256(str(destination).encode()).hexdigest()
                        + ".previous"
                    )
                effects.append(
                    {
                        "actor": item["actor"], "kind": item["kind"],
                        "name": item["name"], "destination": str(destination),
                        "source": str(item["source"]),
                        "source_is_directory": item["source"].is_dir(),
                        "allowed_root": str(allowed_root),
                        "parent_device": parent_device,
                        "parent_inode": parent_inode,
                        "transaction_device": transaction_state.st_dev,
                        "transaction_inode": transaction_state.st_ino,
                        "materialization": mode,
                        "desired_sha256": desired_sha256,
                        "desired_uid": desired_uid, "desired_gid": desired_gid,
                        "desired_mode": desired_mode,
                        "preimage": preimage,
                        "stage": str(
                            transaction_root /
                            (hashlib.sha256(str(destination).encode()).hexdigest() + ".next")
                        ),
                        "backup": str(backup) if backup is not None else None,
                        "original_state": item["state"],
                    }
                )
            transaction = {
                "schema": "tgw-w18-actor-materializer-transaction/v1",
                "generation": bundle["generation"],
                "desired_bindings_hash": desired_hash,
                "legacy_retirement_allowlist": list(_LEGACY_RETIREMENT_SUFFIXES),
                "status": "PLANNED",
                "effects": effects,
                "observations": observations,
                "retirements": retirements,
                "retired": [],
                "completed": [],
            }
            # This durable complete preimage/effect plan precedes staging and
            # every destination rename.  A crash is resumed from this file.
            _atomic_json(journal_path, transaction)
        else:
            if (
                transaction.get("schema") != "tgw-w18-actor-materializer-transaction/v1"
                or transaction.get("generation") != bundle["generation"]
                or transaction.get("desired_bindings_hash") != desired_hash
                or transaction.get("legacy_retirement_allowlist")
                != list(_LEGACY_RETIREMENT_SUFFIXES)
                or transaction.get("status") not in {"PLANNED", "APPLYING", "APPLIED"}
                or not isinstance(transaction.get("effects"), list)
                or not isinstance(transaction.get("observations"), list)
                or not isinstance(transaction.get("retirements"), list)
                or not isinstance(transaction.get("retired"), list)
                or not isinstance(transaction.get("completed"), list)
            ):
                raise InstallError("actor materializer transaction binding differs")
            effects = [dict(effect) for effect in transaction["effects"]]
            declared = {str(item["destination"]): item for item in desired_bindings}
            for effect in effects:
                desired = declared.get(str(effect.get("destination")))
                if (
                    desired is None
                    or effect.get("source") != desired["source"]
                    or effect.get("materialization") != desired["materialization"]
                    or effect.get("desired_sha256") != desired["desired_sha256"]
                ):
                    raise InstallError("actor materializer transaction effect differs")
        effect_by_destination = {
            str(effect["destination"]): effect for effect in effects
        }
        retired = set(str(value) for value in transaction.get("retired", []))
        for retirement in transaction.get("retirements", []):
            if (
                not isinstance(retirement, Mapping)
                or not isinstance(retirement.get("path"), str)
                or not isinstance(retirement.get("retained"), str)
            ):
                raise InstallError("actor legacy retirement journal is invalid")
            identity = str(retirement["path"])
            _apply_retirement(retirement)
            if identity not in retired:
                retired.add(identity)
                transaction["status"] = "APPLYING"
                transaction["retired"] = sorted(retired)
                _atomic_json(journal_path, transaction)
        completed = set(str(value) for value in transaction.get("completed", []))
        for effect in effects:
            identity = str(effect["destination"])
            _apply_effect(effect, desired_bytes.get(identity))
            if identity not in completed:
                completed.add(identity)
                transaction["status"] = "APPLYING"
                transaction["completed"] = sorted(completed)
                _atomic_json(journal_path, transaction)
        for observation in transaction["observations"]:
            if not isinstance(observation, Mapping) or not isinstance(
                observation.get("destination"), str
            ) or not isinstance(observation.get("preimage"), dict):
                raise InstallError("actor CURRENT binding changed during materialization")
            observed_destination = Path(observation["destination"])
            try:
                parent_fd = os.open(
                    observed_destination.parent,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
            except OSError as exc:
                raise InstallError("actor CURRENT binding parent changed") from exc
            try:
                parent_state = os.fstat(parent_fd)
                if (
                    parent_state.st_dev != observation.get("parent_device")
                    or parent_state.st_ino != observation.get("parent_inode")
                    or not _matches_preimage_at(
                        parent_fd, observed_destination.name, observation["preimage"]
                    )
                ):
                    raise InstallError(
                        "actor CURRENT binding changed during materialization"
                    )
            finally:
                os.close(parent_fd)
        transaction["status"] = "APPLIED"
        transaction["retired"] = sorted(retired)
        transaction["completed"] = sorted(completed)
        _atomic_json(journal_path, transaction)
        for item in plans:
            effect = effect_by_destination.get(str(item["destination"]))
            item["receipt_state"] = (
                effect.get("original_state") if effect is not None else "CURRENT"
            )
    else:
        for item in plans:
            item["receipt_state"] = item["state"]
    return {
        "schema": "tgw-w18-complete-actor-materialization/v1",
        "generation": bundle["generation"],
        "mode": "apply" if apply else "dry-run",
        "status": "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED" if apply else "PREPARED",
        "actors": sorted(bundle["actors"]),
        "bindings": [
            {
                **{
                    "actor": item["actor"], "kind": item["kind"],
                    "name": item["name"], "source": str(item["source"]),
                    "destination": str(item["destination"]),
                    "sha256": item["sha256"],
                    "materialization": item["projection"] or "symlink",
                    "status": (
                        "CURRENT" if item["receipt_state"] == "CURRENT"
                        else "INSTALLED" if apply and item["receipt_state"] == "MISSING"
                        else "PROJECTED" if apply and item["receipt_state"] == "PROJECTABLE"
                        else "REPLACED" if apply else "WOULD_INSTALL" if item["state"] == "MISSING"
                        else "WOULD_PROJECT" if item["state"] == "PROJECTABLE"
                        else "WOULD_REPLACE"
                    ),
                },
                **(
                    {"endpoint": item["endpoint"]}
                    if item["kind"] == "mcp" else {}
                ),
                **(
                    {"capability": item["capability"]}
                    if item["kind"] in {"instruction", "skill"} else {}
                ),
            }
            for item in plans
        ],
        "transaction_journal": str(journal_path) if journal_path is not None else None,
        "rollback_journal": effects,
        "activation": "required-in-current-quiet-refresh-transaction",
    }


def materialize_complete_actor_contracts(
    bundle: dict[str, Any], *, source_root: str | Path,
    contracts: dict[str, dict[str, Any]], trusted_contract_public_key: str,
    apply: bool = False, replace_existing: bool = False,
    additional_source_roots: tuple[str | Path, ...] = (),
    transaction_journal_path: str | Path | None = None,
) -> dict[str, Any]:
    """Serialize one complete materializer transaction across crash retries."""
    if not apply:
        return _materialize_complete_actor_contracts(
            bundle, source_root=source_root, contracts=contracts,
            trusted_contract_public_key=trusted_contract_public_key,
            apply=False, replace_existing=replace_existing,
            additional_source_roots=additional_source_roots,
            transaction_journal_path=transaction_journal_path,
        )
    if transaction_journal_path is None:
        raise InstallError("actor materialization requires a durable transaction journal")
    journal_path = Path(transaction_journal_path)
    if (
        not journal_path.is_absolute()
        or journal_path.parent.is_symlink()
        or not journal_path.parent.is_dir()
        or journal_path.parent.resolve(strict=True) != journal_path.parent
    ):
        raise InstallError("actor materializer transaction journal path is not durable")
    lock_path = journal_path.with_name(f".{journal_path.name}.lock")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return _materialize_complete_actor_contracts(
            bundle, source_root=source_root, contracts=contracts,
            trusted_contract_public_key=trusted_contract_public_key,
            apply=True, replace_existing=replace_existing,
            additional_source_roots=additional_source_roots,
            transaction_journal_path=journal_path,
        )
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _rollback_complete_actor_contracts(materialization: dict[str, Any]) -> None:
    """Restore every complete actor binding from its exact rollback journal."""
    if materialization.get("schema") != "tgw-w18-complete-actor-materialization/v1":
        raise InstallError("complete actor rollback materialization schema is invalid")
    journal = materialization.get("rollback_journal")
    if not isinstance(journal, list):
        raise InstallError("complete actor rollback journal is invalid")
    transaction_path = materialization.get("transaction_journal")
    transaction: dict[str, Any] | None = None
    path: Path | None = None
    if transaction_path is not None:
        if not isinstance(transaction_path, str):
            raise InstallError("complete actor rollback transaction journal is invalid")
        path = Path(transaction_path)
        transaction = _load_transaction(path)
        if transaction is None or transaction.get("generation") != materialization.get("generation"):
            raise InstallError("complete actor rollback transaction journal is unavailable")
        if transaction.get("status") not in {
            "PLANNED", "APPLYING", "APPLIED", "ROLLING_BACK", "ROLLED_BACK"
        }:
            raise InstallError("complete actor rollback transaction state is invalid")
        if transaction.get("effects") != journal:
            raise InstallError("complete actor rollback journal differs from transaction")
        transaction["status"] = "ROLLING_BACK"
        _atomic_json(path, transaction)
        retirements = transaction.get("retirements")
        if not isinstance(retirements, list):
            raise InstallError("complete actor retirement rollback journal is invalid")
        # Normalize even a PLANNED/partially-applied transaction to its
        # protected retained paths.  The durable ROLLING_BACK intent above
        # makes this crash-retryable and lets one rollback algorithm handle
        # every interruption point.
        for retirement in retirements:
            if not isinstance(retirement, Mapping):
                raise InstallError("complete actor retirement rollback journal is invalid")
            _apply_retirement(retirement)
    for entry in reversed(journal):
        if not isinstance(entry, dict) or not {
            "destination", "source", "backup", "stage", "preimage",
            "materialization", "desired_sha256",
        } <= set(entry):
            raise InstallError("complete actor rollback journal entry is invalid")
        destination = Path(str(entry["destination"]))
        backup = Path(str(entry["backup"])) if entry.get("backup") else None
        stage = Path(str(entry["stage"]))
        preimage = entry["preimage"]
        if not isinstance(preimage, dict):
            raise InstallError("complete actor rollback preimage is invalid")
        parent_fd = os.open(
            destination.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        transaction_fd = os.open(
            stage.parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            parent_state = os.fstat(parent_fd)
            transaction_state = os.fstat(transaction_fd)
            if (
                parent_state.st_dev != entry.get("parent_device")
                or parent_state.st_ino != entry.get("parent_inode")
                or transaction_state.st_dev != entry.get("transaction_device")
                or transaction_state.st_ino != entry.get("transaction_inode")
                or transaction_state.st_dev != parent_state.st_dev
                or (backup is not None and backup.parent != stage.parent)
            ):
                raise InstallError("complete actor rollback parent identity changed")
            destination_name = destination.name
            backup_name = backup.name if backup is not None else None
            if _matches_preimage_at(parent_fd, destination_name, preimage):
                if backup_name is not None and _entry_exists_at(
                    transaction_fd, backup_name
                ):
                    raise InstallError("complete actor rollback retains an ambiguous backup")
            elif _matches_desired_at(parent_fd, destination_name, entry):
                os.unlink(destination_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
                if backup_name is not None:
                    if not _matches_preimage_at(
                        transaction_fd, backup_name, preimage
                    ):
                        raise InstallError("complete actor rollback preimage is unavailable")
                    _rename_noreplace(
                        backup_name, destination_name,
                        source_dir_fd=transaction_fd,
                        destination_dir_fd=parent_fd,
                    )
            elif (
                backup_name is not None
                and not _entry_exists_at(parent_fd, destination_name)
                and _matches_preimage_at(transaction_fd, backup_name, preimage)
            ):
                _rename_noreplace(
                    backup_name, destination_name,
                    source_dir_fd=transaction_fd,
                    destination_dir_fd=parent_fd,
                )
            else:
                raise InstallError("complete actor rollback target changed")
            if _entry_exists_at(transaction_fd, stage.name):
                if not _matches_desired_at(transaction_fd, stage.name, entry):
                    raise InstallError("complete actor rollback staging path changed")
                os.unlink(stage.name, dir_fd=transaction_fd)
            os.fsync(parent_fd)
            os.fsync(transaction_fd)
            if not _matches_preimage_at(parent_fd, destination_name, preimage):
                raise InstallError("complete actor rollback verification failed")
        finally:
            os.close(transaction_fd)
            os.close(parent_fd)
    if transaction is not None and path is not None:
        for retirement in reversed(retirements):
            if not isinstance(retirement, Mapping):
                raise InstallError("complete actor retirement rollback journal is invalid")
            _restore_retirement(retirement)
        transaction["status"] = "ROLLED_BACK"
        _atomic_json(path, transaction)


def rollback_complete_actor_contracts(materialization: dict[str, Any]) -> None:
    transaction_path = materialization.get("transaction_journal")
    if not isinstance(transaction_path, str):
        raise InstallError("complete actor rollback transaction journal is invalid")
    journal_path = Path(transaction_path)
    lock_path = journal_path.with_name(f".{journal_path.name}.lock")
    descriptor = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        _rollback_complete_actor_contracts(materialization)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
