"""Tests for `tgw restart-workers` (PP-SHELL-001 convenience wrapper)."""

import subprocess
import types

import tgw.api as api
from tgw.queue import WORKER_QUEUES


def test_autonomous_coding_queues_use_the_canonical_worker_registry():
    assert {"codex-implement", "claude-review", "controller-verify", "hermes-stitch"}.issubset(WORKER_QUEUES)
    assert "operator-admit" not in WORKER_QUEUES


def test_unknown_queue_rejected():
    out = api.cmd_restart_workers(queues=["not_a_queue"])
    assert out["ok"] is False
    assert "unknown queue" in out["error"]


def test_dry_run_emits_command_no_exec(monkeypatch, capsys):
    # If anything tries to actually run a process, fail loudly.
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    out = api.cmd_restart_workers(queues=["ebay_draft"], dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert out["queues"] == ["ebay_draft"]
    assert "tgw-worker@ebay_draft.service" in out["command"]
    assert "tgw-worker@ebay_draft.service" in capsys.readouterr().out


def test_default_restarts_all_canonical_queues(monkeypatch):
    monkeypatch.setattr(api.os, "geteuid", lambda: 0)  # pretend root, no sudo
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[:2] == ["systemctl", "is-active"]:
            units = cmd[2:]
            return types.SimpleNamespace(returncode=0, stdout="\n".join(["active"] * len(units)), stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = api.cmd_restart_workers()
    assert out["ok"] is True
    assert out["used_sudo"] is False
    assert len(out["restarted"]) == len(WORKER_QUEUES)
    assert out["failed"] == []
    # First call is the restart, with one unit per canonical queue
    restart_cmd = calls[0]
    assert restart_cmd[:2] == ["systemctl", "restart"]
    assert len(restart_cmd) - 2 == len(WORKER_QUEUES)


def test_non_root_uses_sudo_and_reports_failures(monkeypatch):
    monkeypatch.setattr(api.os, "geteuid", lambda: 1000)
    seq = []

    def fake_run(cmd, **kw):
        seq.append(cmd)
        if cmd == ["sudo", "-n", "true"]:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "restart" in cmd:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "is-active" in cmd:
            # ebay_draft active, ebay_price failed
            return types.SimpleNamespace(returncode=3, stdout="active\nfailed", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = api.cmd_restart_workers(queues=["ebay_draft", "ebay_price"])
    assert out["used_sudo"] is True
    assert out["restarted"] == ["tgw-worker@ebay_draft.service"]
    assert out["failed"] == ["tgw-worker@ebay_price.service"]
    assert out["ok"] is False
    # the restart command was prefixed with sudo -n
    assert seq[1][:2] == ["sudo", "-n"]


def test_non_root_without_passwordless_sudo(monkeypatch):
    monkeypatch.setattr(api.os, "geteuid", lambda: 1000)

    def fake_run(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="a password is required")
        raise AssertionError("should not reach systemctl when sudo probe fails")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = api.cmd_restart_workers(queues=["echo"])
    assert out["ok"] is False
    assert "passwordless sudo" in out["error"]
    assert out["command"].startswith("sudo systemctl restart")
