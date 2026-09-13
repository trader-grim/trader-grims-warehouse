"""Offline tests for the todo-2017 NL-driven dotfile agent.

Hermetic by design: every test uses tmp_path as the stand-in "home" (never
the real $HOME) plus a tmp snapshot root, a monkeypatched in-memory fake
for the harness ledger, and an injected deterministic fake proposer — no
live opencode/claude API call, ever. The real CLI-shelling
``propose_new_content`` is exercised manually via scripts/config_nl_agent_demo.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tgw.development import config_agent, config_nl_agent


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
        return [dict(e) for e in self.entries if e["seq"] > after_seq and (not kinds or e["kind"] in kinds)]


@pytest.fixture
def fake_ledger(monkeypatch):
    fake = _FakeLedger()
    monkeypatch.setattr(config_agent.harness_ledger, "ensure_task", fake.ensure_task)
    monkeypatch.setattr(config_agent.harness_ledger, "append", fake.append)
    monkeypatch.setattr(config_agent.harness_ledger, "history", fake.history)
    return fake


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def snaps(tmp_path):
    s = tmp_path / "snaps"
    s.mkdir()
    return s


def _write(home: Path, rel: str, content: bytes) -> Path:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    return p


# --- is_allowed_target ---


def test_allows_bashrc_profile_and_nested_config(home):
    assert config_nl_agent.is_allowed_target(home / ".bashrc", home)
    assert config_nl_agent.is_allowed_target(home / ".profile", home)
    assert config_nl_agent.is_allowed_target(home / ".config" / "foo" / "bar.conf", home)


def test_refuses_outside_home(home, tmp_path):
    outside = tmp_path / "elsewhere" / "x.conf"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"x\n")
    assert not config_nl_agent.is_allowed_target(outside, home)


def test_refuses_unlisted_path_under_home(home):
    _write(home, ".ssh/id_rsa", b"secret\n")
    assert not config_nl_agent.is_allowed_target(home / ".ssh" / "id_rsa", home)
    assert not config_nl_agent.is_allowed_target(home / ".bashrc.backup", home)
    assert not config_nl_agent.is_allowed_target(home, home)


def test_refuses_symlink_escape(home, tmp_path):
    secret = tmp_path / "secret.conf"
    secret.write_bytes(b"top-secret\n")
    link = home / ".config" / "link.conf"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlinks unavailable")
    assert not config_nl_agent.is_allowed_target(link, home)
    assert secret.read_bytes() == b"top-secret\n"


# --- handle_request with injected fake proposer ---


def test_handle_request_success(fake_ledger, home, snaps):
    target = _write(home, ".bashrc", b"export PATH=$PATH:/x\n")
    before = target.read_bytes()

    def fake_propose(current: bytes, request: str, *, path: Path) -> bytes:
        assert current == before
        assert "alias" in request
        return current + b"alias ll='ls -la'\n"

    sid = config_nl_agent.handle_request(
        "add an alias ll for ls -la", target, home=home, allowlist_root=home,
        propose=fake_propose, snapshot_root=snaps,
    )
    assert isinstance(sid, str) and sid
    assert target.read_bytes() == before + b"alias ll='ls -la'\n"
    kinds = [e["kind"] for e in fake_ledger.entries]
    assert "config_backup" in kinds
    assert any(e["kind"] == "config_apply" and e["body"].get("outcome") == "verified" for e in fake_ledger.entries)
    # Rollback via the returned snapshot restores the original bytes.
    config_agent.rollback(sid, snapshot_root=snaps)
    assert target.read_bytes() == before


def test_handle_request_refuses_disallowed_target(fake_ledger, home, snaps):
    target = _write(home, ".ssh/id_rsa", b"secret\n")
    calls: list = []

    def fake_propose(current: bytes, request: str, *, path: Path) -> bytes:
        calls.append(request)
        return b"pwned\n"

    with pytest.raises(ValueError):
        config_nl_agent.handle_request(
            "change this", target, home=home, allowlist_root=home,
            propose=fake_propose, snapshot_root=snaps,
        )
    assert calls == []
    assert target.read_bytes() == b"secret\n"
    assert fake_ledger.entries == []
    assert list(snaps.iterdir()) == []


def test_handle_request_proposer_failure_writes_nothing(fake_ledger, home, snaps):
    target = _write(home, ".profile", b"original\n")

    def bad_propose(current: bytes, request: str, *, path: Path) -> bytes:
        raise config_nl_agent.NLProposeError("model unavailable")

    with pytest.raises(config_nl_agent.NLProposeError):
        config_nl_agent.handle_request(
            "change this", target, home=home, allowlist_root=home,
            propose=bad_propose, snapshot_root=snaps,
        )
    assert target.read_bytes() == b"original\n"
    assert fake_ledger.entries == []
    assert list(snaps.iterdir()) == []


def test_handle_request_missing_file_is_plain_error(fake_ledger, home, snaps):
    target = home / ".config" / "newapp" / "settings.conf"

    def fake_propose(current: bytes, request: str, *, path: Path) -> bytes:
        return b"new\n"

    with pytest.raises(FileNotFoundError):
        config_nl_agent.handle_request(
            "create this", target, home=home, allowlist_root=home,
            propose=fake_propose, snapshot_root=snaps,
        )
    assert not target.exists()
    assert fake_ledger.entries == []
