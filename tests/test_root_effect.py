from __future__ import annotations

import json
from pathlib import Path

import pytest

from tgw import root_effect

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _config(tmp_path, **overrides):
    cfg = {
        "schema": "tgw-root-effect-config/v1",
        "receipt_root": str(tmp_path / "receipts"),
        "bootstrap": "/usr/local/sbin/tgw-coding-bootstrap",
        "canonical_repo": str(tmp_path / "repo"),
        "runtime_root": str(tmp_path / "runtime"),
        "postgres_dsn": "dbname=x user=tgw_coding",
        "whitelisted_units": ["tgw-http.service", "tgw-codex-implement-worker.service"],
        "container_runtime": None,
    }
    cfg.update(overrides)
    return cfg


def _dispatch(handlers):
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return handlers.get(argv[0], lambda: _Proc(0, "", ""))()

    runner.calls = calls
    return runner


def test_runtime_install_validates_commit_and_delegates(tmp_path):
    cfg = _config(tmp_path)
    runner = _dispatch({"git": lambda: _Proc(0, COMMIT + "\n", "")})
    result = root_effect.run("runtime-install", {"commit": COMMIT}, cfg, runner=runner)
    assert result["ok"] is True
    assert result["outcome"] == "PASS"
    assert runner.calls[-1] == ["/usr/local/sbin/tgw-coding-bootstrap", "--commit", COMMIT]
    assert any(Path(cfg["receipt_root"]).glob("runtime-install-*.json"))


def test_runtime_install_rejects_non_commit(tmp_path):
    cfg = _config(tmp_path)
    with pytest.raises(root_effect.RootEffectError, match="40-hex"):
        root_effect.run("runtime-install", {"commit": "nope"}, cfg)


def test_runtime_install_requires_canonical_head(tmp_path):
    cfg = _config(tmp_path)
    runner = _dispatch({"git": lambda: _Proc(0, OTHER_COMMIT + "\n", "")})
    with pytest.raises(root_effect.RootEffectError, match="canonical HEAD"):
        root_effect.run("runtime-install", {"commit": COMMIT}, cfg, runner=runner)


def test_service_restart_enforces_whitelist(tmp_path):
    cfg = _config(tmp_path)
    runner = _dispatch({})
    result = root_effect.run("service-restart", {"unit": "tgw-http.service"}, cfg, runner=runner)
    assert result["ok"] is True
    assert runner.calls == [["systemctl", "restart", "tgw-http.service"]]
    with pytest.raises(root_effect.RootEffectError, match="non-whitelisted"):
        root_effect.run("service-restart", {"unit": "evil.service"}, cfg)


def test_database_repair_whitelist(tmp_path):
    cfg = _config(tmp_path)
    runner = _dispatch({"git": lambda: _Proc(0, COMMIT + "\n", "")})
    root_effect.run("database-repair", {"repair": "database"}, cfg, runner=runner)
    assert runner.calls[-1] == [
        "/usr/local/sbin/tgw-coding-bootstrap", "--commit", COMMIT, "--repair", "database",
    ]
    with pytest.raises(root_effect.RootEffectError, match="unknown repair"):
        root_effect.run("database-repair", {"repair": "drop-everything"}, cfg)


def test_container_lifecycle_fail_closed_without_runtime(tmp_path):
    cfg = _config(tmp_path)
    result = root_effect.run("container-lifecycle", {"action": "start", "id": "abc"}, cfg)
    assert result["ok"] is False
    assert result["outcome"] == "FAIL"
    assert "not configured" in result["detail"]


def test_container_lifecycle_validates_action(tmp_path):
    cfg = _config(tmp_path, container_runtime="/usr/bin/podman")
    with pytest.raises(root_effect.RootEffectError, match="refuses action"):
        root_effect.run("container-lifecycle", {"action": "explode", "id": "abc"}, cfg)


def test_restore_from_receipt_delegates_runtime_install(tmp_path):
    cfg = _config(tmp_path)
    receipt = tmp_path / "mat.json"
    receipt.write_text(json.dumps({"commit": COMMIT}), encoding="utf-8")
    runner = _dispatch({"git": lambda: _Proc(0, COMMIT + "\n", "")})
    result = root_effect.run("restore-from-receipt", {"receipt": str(receipt)}, cfg, runner=runner)
    assert result["ok"] is True
    assert runner.calls[-1] == ["/usr/local/sbin/tgw-coding-bootstrap", "--commit", COMMIT]


def test_restore_from_receipt_rejects_missing_commit(tmp_path):
    cfg = _config(tmp_path)
    receipt = tmp_path / "bad.json"
    receipt.write_text(json.dumps({"nothing": "here"}), encoding="utf-8")
    with pytest.raises(root_effect.RootEffectError, match="exact commit"):
        root_effect.run("restore-from-receipt", {"receipt": str(receipt)}, cfg)


def test_recovery_status_reports_degraded(tmp_path):
    cfg = _config(tmp_path)
    runner = _dispatch({
        "git": lambda: _Proc(0, COMMIT + "\n", ""),
        "readlink": lambda: _Proc(0, OTHER_COMMIT + "\n", ""),
        "psql": lambda: _Proc(0, "1\n", ""),
        "systemctl": lambda: _Proc(0, "active\n", ""),
    })
    result = root_effect.run("recovery-status", {}, cfg, runner=runner)
    assert result["outcome"] == "DEGRADED"
    assert result["receipt"].endswith(".json")


def test_load_config_roundtrip(tmp_path):
    cfg_path = tmp_path / "tgw-root-effect.json"
    cfg_path.write_text(json.dumps(_config(tmp_path)), encoding="utf-8")
    loaded = root_effect.load_config(cfg_path)
    assert isinstance(loaded["whitelisted_units"], frozenset)


def test_load_config_rejects_bad_schema(tmp_path):
    cfg_path = tmp_path / "bad.json"
    cfg_path.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
    with pytest.raises(root_effect.RootEffectError, match="schema"):
        root_effect.load_config(cfg_path)
