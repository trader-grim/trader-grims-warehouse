"""Offline tests for the todo-2016 config backup/apply/rollback mechanism.

Hermetic by design: every test uses tmp_path allowlist/snapshot roots (never
the real fixture dir, never a host config path) and a monkeypatched in-memory
fake for the harness ledger, so no PostgreSQL is required. The fake proves
`list_snapshots` reads from the ledger path rather than an in-memory cache.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tgw.development import config_agent


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _FakeLedger:
    """In-memory stand-in for harness_ledger's ensure/append/history surface."""

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def ensure_task(self, task_id: str, **kwargs):
        return {"task_id": task_id}

    def append(self, task_id: str, kind: str, body: dict, *, actor=None) -> int:
        self.entries.append({"seq": len(self.entries) + 1, "kind": kind, "body": dict(body), "actor": actor})
        return len(self.entries)

    def history(self, task_id: str, *, kinds=None, after_seq: int = 0) -> list[dict]:
        return [
            dict(e)
            for e in self.entries
            if e["seq"] > after_seq and (not kinds or e["kind"] in kinds)
        ]


@pytest.fixture
def fake_ledger(monkeypatch):
    fake = _FakeLedger()
    monkeypatch.setattr(config_agent.harness_ledger, "ensure_task", fake.ensure_task)
    monkeypatch.setattr(config_agent.harness_ledger, "append", fake.append)
    monkeypatch.setattr(config_agent.harness_ledger, "history", fake.history)
    return fake


@pytest.fixture
def roots(tmp_path):
    allow = tmp_path / "allow"
    snaps = tmp_path / "snaps"
    allow.mkdir()
    snaps.mkdir()
    return allow, snaps


def _write(allow: Path, rel: str, content: bytes) -> Path:
    p = allow / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


def test_round_trip_restores_byte_identical(fake_ledger, roots):
    allow, snaps = roots
    a = _write(allow, "app.conf", b"[server]\nport = 8080\n")
    b = _write(allow, "sub/theme.conf", b"[theme]\nname = gray\n")
    before = {p: _sha(p.read_bytes()) for p in (a, b)}

    snap1 = config_agent.backup_paths([a, b], allowlist_root=allow, snapshot_root=snaps)
    snap2 = config_agent.apply_change(a, b"[server]\nport = 9090\n", allowlist_root=allow, snapshot_root=snaps)
    assert snap1 != snap2
    assert a.read_bytes() == b"[server]\nport = 9090\n"

    config_agent.rollback(snap2, snapshot_root=snaps)
    after = {p: _sha(p.read_bytes()) for p in (a, b)}
    assert after == before


def test_apply_outside_allowlist_raises_and_writes_nothing(fake_ledger, roots):
    allow, snaps = roots
    _write(allow, "app.conf", b"inside\n")
    outside = roots[0].parent / "outside" / "evil.conf"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"untouched\n")

    with pytest.raises(ValueError):
        config_agent.apply_change(outside, b"pwned\n", allowlist_root=allow, snapshot_root=snaps)
    with pytest.raises(ValueError):
        config_agent.backup_paths([outside], allowlist_root=allow, snapshot_root=snaps)

    assert outside.read_bytes() == b"untouched\n"
    assert fake_ledger.entries == []
    assert list(snaps.iterdir()) == []


def test_symlink_escape_refused(fake_ledger, roots, tmp_path):
    allow, snaps = roots
    secret = tmp_path / "secret.conf"
    secret.write_bytes(b"top-secret\n")
    link = allow / "link.conf"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unavailable")
    with pytest.raises(ValueError):
        config_agent.backup_paths([link], allowlist_root=allow, snapshot_root=snaps)
    assert secret.read_bytes() == b"top-secret\n"


def test_corrupted_write_detected_rolled_back_and_raises(fake_ledger, roots, monkeypatch):
    allow, snaps = roots
    target = _write(allow, "app.conf", b"original-bytes\n")

    def _truncated(path: Path, data: bytes) -> None:
        Path(path).write_bytes(data[: max(1, len(data) // 2)])

    monkeypatch.setattr(config_agent, "_write_file_bytes", _truncated)

    with pytest.raises(config_agent.ConfigWriteMismatchError):
        config_agent.apply_change(target, b"brand-new-content-12345\n", allowlist_root=allow, snapshot_root=snaps)

    # The file must never end up in the corrupted state.
    assert target.read_bytes() == b"original-bytes\n"
    kinds = [(e["kind"], e["body"].get("outcome")) for e in fake_ledger.entries]
    assert ("config_apply", "write_mismatch") in kinds
    assert any(e["kind"] == "config_rollback" for e in fake_ledger.entries)


def test_snapshots_distinct_and_isolated(fake_ledger, roots):
    allow, snaps = roots
    x = _write(allow, "x.conf", b"x-v1\n")
    y = _write(allow, "y.conf", b"y-v1\n")

    snap_x = config_agent.backup_paths([x], allowlist_root=allow, snapshot_root=snaps)
    x.write_bytes(b"x-v2\n")
    y.write_bytes(b"y-v2\n")
    snap_y = config_agent.backup_paths([y], allowlist_root=allow, snapshot_root=snaps)

    assert snap_x != snap_y
    config_agent.rollback(snap_x, snapshot_root=snaps)
    assert x.read_bytes() == b"x-v1\n"
    assert y.read_bytes() == b"y-v2\n"


def test_list_snapshots_reflects_ledger_not_cache_or_disk(fake_ledger, roots):
    allow, snaps = roots
    target = _write(allow, "app.conf", b"v1\n")

    snap = config_agent.backup_paths([target], allowlist_root=allow, snapshot_root=snaps)
    config_agent.apply_change(target, b"v2\n", allowlist_root=allow, snapshot_root=snaps)
    config_agent.rollback(snap, snapshot_root=snaps)

    # A stray on-disk directory with a manifest but no ledger entry is drift,
    # not truth: the ledger path must exclude it.
    stray = snaps / "20990101T000000000000-deadbeefcafe"
    stray.mkdir()
    (stray / "manifest.json").write_text(json.dumps({"snapshot_id": stray.name, "created_at": "x", "allowlist_root": str(allow), "files": []}))

    listed = config_agent.list_snapshots(snapshot_root=snaps)
    assert stray.name not in listed
    ledger_ids = [e["body"]["snapshot_id"] for e in fake_ledger.entries if "snapshot_id" in e["body"]]
    assert listed == list(dict.fromkeys(ledger_ids))
    assert snap in listed
    # Rollback is audit-only history: the restore point is still listed after use.
    assert len(listed) >= 2
