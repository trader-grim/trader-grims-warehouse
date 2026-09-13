"""Config backup/apply/rollback precursor mechanism (todo-2016, round 1).

Small, real, standalone building block toward the restore-point requirement of
``PP-CATIO-RELEASE-001`` Phase 0 ("Freeze and record": define a restore point,
write the "return to current working state" procedure, test it once). That PP
is PROPOSAL, not ratified — this module completes no Todo, no LEAF-11 leaf, no
ratter-graph capability, and no part of the PP itself.

Safety scope: every mutating entry point takes an ``allowlist_root`` and
refuses (raises, writes nothing) any path that does not resolve under it —
including via symlink escapes. The narrow default allowlist is the
in-repo fixture directory ``tests/fixtures/config_agent_demo/``; there is
deliberately no flag or code path here aimed at real host config paths
(``~/.config/sway``, ``~/.config/keyd``, shell rcs, ...). Pointing this at a
real host path is a separate, later, explicit decision.

Durable audit trail: backup/apply/rollback events are recorded with the
existing ``harness_ledger.append`` primitive (no second ledger mechanism).
Ledger writes are best-effort — when the ledger database is unreachable (e.g.
offline tests) the filesystem snapshot stays authoritative for restore and the
ledger error is swallowed, never masking the file operation's outcome.
``list_snapshots`` reads snapshot ids from the ledger (``history``); only when
the ledger itself is unreachable does it fall back to scanning the snapshot
directory, so offline runs stay inspectable.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from tgw.development import harness_ledger

# Dedupe handle for this ad-hoc dispatch; the ledger task row is only an audit
# container, not a claim about any registered Todo/PP/leaf state.
LEDGER_TASK_ID = "todo-2016"

LEDGER_KINDS = ("config_backup", "config_apply", "config_rollback")

DEFAULT_SNAPSHOT_ROOT = Path("/opt/TGW/var/config-agent/snapshots")

SNAPSHOT_ROOT_ENV_VAR = "TGW_CONFIG_AGENT_SNAPSHOT_ROOT"

MANIFEST_NAME = "manifest.json"


class ConfigAgentError(RuntimeError):
    """Base class for config-agent refusals and failures."""


class AllowlistRefusal(ConfigAgentError, ValueError):
    """A path was refused: outside the allowlist root (symlink escapes count)."""


class ConfigWriteMismatchError(ConfigAgentError):
    """Post-write verification failed; the path was rolled back, never left corrupt."""


@dataclasses.dataclass(frozen=True)
class SnapshotFile:
    """One file captured in a snapshot: relative path under the allowlist root."""

    rel: str
    sha256: str
    size: int


@dataclasses.dataclass(frozen=True)
class Snapshot:
    """A restore point: id, creation time, and per-path prior content hashes."""

    snapshot_id: str
    created_at: str
    allowlist_root: str
    files: tuple[SnapshotFile, ...]


def default_allowlist_root() -> Path:
    """The narrow, safe-by-construction default: the in-repo fixture directory."""
    return Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "config_agent_demo"


def resolve_snapshot_root(snapshot_root: Path | None = None) -> Path:
    """Snapshot staging area: explicit arg, else env override, else the default."""
    if snapshot_root is not None:
        return Path(snapshot_root)
    override = os.environ.get(SNAPSHOT_ROOT_ENV_VAR)
    if override:
        return Path(override)
    return DEFAULT_SNAPSHOT_ROOT


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _new_snapshot_id() -> str:
    # Timestamp prefix keeps ids sortable; uuid suffix keeps same-run ids distinct.
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    return f"{ts}-{uuid.uuid4().hex[:12]}"


def _check_under_root(path: Path, root_resolved: Path) -> Path:
    """Resolve `path` and refuse unless it lands under `root_resolved`.

    Symlink escapes count as outside: both sides are fully resolved first.
    Raises AllowlistRefusal (a ValueError) without touching anything.
    """
    resolved = Path(path).resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise AllowlistRefusal(f"refused: {path} resolves outside allowlist root {root_resolved}")
    return resolved


def _record(kind: str, body: dict[str, Any]) -> int | None:
    """Best-effort ledger audit entry; returns seq, or None when unreachable."""
    try:
        harness_ledger.ensure_task(LEDGER_TASK_ID)
        return int(harness_ledger.append(LEDGER_TASK_ID, kind, body))
    except Exception:
        return None


def _snapshot_dir(snapshot_root: Path, snapshot_id: str) -> Path:
    return snapshot_root / snapshot_id


def _manifest_path(snapshot_root: Path, snapshot_id: str) -> Path:
    return _snapshot_dir(snapshot_root, snapshot_id) / MANIFEST_NAME


def _snapshot_to_manifest(snapshot: Snapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "created_at": snapshot.created_at,
        "allowlist_root": snapshot.allowlist_root,
        "files": [{"rel": f.rel, "sha256": f.sha256, "size": f.size} for f in snapshot.files],
    }


def _manifest_to_snapshot(data: dict[str, Any]) -> Snapshot:
    return Snapshot(
        snapshot_id=str(data["snapshot_id"]),
        created_at=str(data["created_at"]),
        allowlist_root=str(data.get("allowlist_root", "")),
        files=tuple(SnapshotFile(rel=str(f["rel"]), sha256=str(f["sha256"]), size=int(f["size"])) for f in data.get("files", [])),
    )


def load_snapshot(snapshot_id: str, snapshot_root: Path | None = None) -> Snapshot:
    """Read a snapshot's manifest; raises FileNotFoundError when unknown."""
    manifest = _manifest_path(resolve_snapshot_root(snapshot_root), snapshot_id)
    if not manifest.is_file():
        raise FileNotFoundError(f"unknown snapshot: {snapshot_id}")
    return _manifest_to_snapshot(json.loads(manifest.read_text(encoding="utf-8")))


def _write_file_bytes(path: Path, data: bytes) -> None:
    """Single write choke point for live config files.

    Module-level (not inlined) so failure-injection tests can monkeypatch it to
    simulate a truncated/corrupt write and prove the post-write verification
    catches it. Rollback restores deliberately bypass this helper and write
    directly, so a corrupted writer cannot corrupt the restore either.
    """
    Path(path).write_bytes(data)


def backup_paths(
    paths: list[Path],
    *,
    allowlist_root: Path,
    snapshot_root: Path | None = None,
) -> str:
    """Snapshot the current bytes of `paths`; return the new `snapshot_id`.

    Refuses (raises, writes nothing) when any path resolves outside
    `allowlist_root` or does not exist as a regular file. All paths are
    validated before anything is copied, so a refusal leaves no partial
    snapshot behind.
    """
    root = Path(allowlist_root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"allowlist root does not exist: {allowlist_root}")
    resolved_paths = [_check_under_root(p, root) for p in paths]
    for resolved in resolved_paths:
        if not resolved.is_file():
            raise FileNotFoundError(f"nothing to back up (not a regular file): {resolved}")

    root_out = resolve_snapshot_root(snapshot_root)
    snapshot_id = _new_snapshot_id()
    snapdir = _snapshot_dir(root_out, snapshot_id)
    snapdir.mkdir(parents=True, exist_ok=False)

    entries: list[SnapshotFile] = []
    for resolved in resolved_paths:
        rel = str(resolved.relative_to(root))
        data = resolved.read_bytes()
        dest = snapdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        entries.append(SnapshotFile(rel=rel, sha256=_sha256(data), size=len(data)))

    snapshot = Snapshot(snapshot_id=snapshot_id, created_at=_utcnow_iso(), allowlist_root=str(root), files=tuple(entries))
    (snapdir / MANIFEST_NAME).write_text(json.dumps(_snapshot_to_manifest(snapshot), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _record("config_backup", {"snapshot_id": snapshot_id, "paths": [str(p) for p in resolved_paths], "hashes": {e.rel: e.sha256 for e in entries}})
    return snapshot_id


def apply_change(
    path: Path,
    new_content: bytes,
    *,
    allowlist_root: Path,
    snapshot_root: Path | None = None,
) -> str:
    """Backup `path`, write `new_content`, verify byte-exact, return snapshot id.

    No path is ever written without a fresh snapshot covering it in the same
    operation. After writing, the file is re-read and compared; on any
    mismatch the path is immediately restored from the just-taken snapshot
    and ConfigWriteMismatchError is raised — the file is never left in an
    unverified state.
    """
    if not isinstance(new_content, (bytes, bytearray)):
        raise TypeError("new_content must be bytes")
    root_out = resolve_snapshot_root(snapshot_root)
    # Spec'd call form is backup_paths([path], allowlist_root=allowlist_root);
    # forward snapshot_root only when explicitly set so the fresh snapshot
    # always lands in the same staging area this operation restores from.
    backup_kwargs: dict[str, Any] = {}
    if snapshot_root is not None:
        backup_kwargs["snapshot_root"] = root_out
    snapshot_id = backup_paths([path], allowlist_root=allowlist_root, **backup_kwargs)
    snapshot = load_snapshot(snapshot_id, root_out)

    resolved = _check_under_root(path, Path(allowlist_root).resolve())
    wanted = bytes(new_content)
    _write_file_bytes(resolved, wanted)
    actual = resolved.read_bytes()
    if actual != wanted:
        # Restore bypasses _write_file_bytes on purpose: a faulty writer must
        # not be able to corrupt the restore path as well.
        prior = (_snapshot_dir(root_out, snapshot_id) / snapshot.files[0].rel).read_bytes()
        resolved.write_bytes(prior)
        restored = resolved.read_bytes()
        _record(
            "config_apply",
            {"snapshot_id": snapshot_id, "path": str(resolved), "outcome": "write_mismatch", "restored_ok": restored == prior},
        )
        _record("config_rollback", {"snapshot_id": snapshot_id, "paths": [str(resolved)], "reason": "apply_write_mismatch", "verified": restored == prior})
        raise ConfigWriteMismatchError(f"post-write verification failed for {resolved}; rolled back to snapshot {snapshot_id}")
    _record("config_apply", {"snapshot_id": snapshot_id, "path": str(resolved), "outcome": "verified", "sha256": _sha256(wanted)})
    return snapshot_id


def rollback(snapshot_id: str, snapshot_root: Path | None = None) -> None:
    """Restore every path in `snapshot_id` to its exact prior bytes, verified."""
    root_out = resolve_snapshot_root(snapshot_root)
    snapshot = load_snapshot(snapshot_id, root_out)
    snapdir = _snapshot_dir(root_out, snapshot_id)
    root = Path(snapshot.allowlist_root)
    restored: list[str] = []
    for entry in snapshot.files:
        # Never restore outside the recorded allowlist root, even if a
        # manifest was hand-edited: resolve and re-check each target.
        target = _check_under_root(root / entry.rel, root.resolve())
        prior = (snapdir / entry.rel).read_bytes()
        if _sha256(prior) != entry.sha256:
            raise ConfigAgentError(f"snapshot {snapshot_id} payload for {entry.rel} does not match its manifest; refusing to restore")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(prior)
        if target.read_bytes() != prior:
            raise ConfigAgentError(f"rollback verification failed for {target}")
        restored.append(str(target))
    _record("config_rollback", {"snapshot_id": snapshot_id, "paths": restored, "verified": True})


def list_snapshots(snapshot_root: Path | None = None) -> list[str]:
    """Enumerate snapshot ids from the ledger (source of truth), oldest first.

    Falls back to scanning the snapshot directory only when the ledger itself
    is unreachable (e.g. offline runs with no database); never from an
    in-memory cache, which could drift.
    """
    try:
        entries = harness_ledger.history(LEDGER_TASK_ID, kinds=list(LEDGER_KINDS))
    except Exception:
        root_out = resolve_snapshot_root(snapshot_root)
        if not root_out.is_dir():
            return []
        return sorted(p.name for p in root_out.iterdir() if p.is_dir() and (p / MANIFEST_NAME).is_file())
    seen: list[str] = []
    for entry in entries:
        body = entry.get("body") or {}
        sid = body.get("snapshot_id")
        if isinstance(sid, str) and sid and sid not in seen:
            seen.append(sid)
    return seen
