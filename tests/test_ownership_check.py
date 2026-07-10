"""PP-DEPLOY-001 — tests for the read-only UID/ownership audit check.

pwd.getpwnam('tgw') is stubbed so we control the expected UID; tmp dirs created
by the test are owned by the running user, letting us drive both the matching
and drift paths. The check never mutates anything.
"""

import os

import tgw.health as health


class _FakePw:
    def __init__(self, uid):
        self.pw_uid = uid


def _patch_uid(monkeypatch, uid):
    monkeypatch.setattr(health.pwd, "getpwnam", lambda name: _FakePw(uid))


def test_user_not_found(monkeypatch):
    def _raise(name):
        raise KeyError(name)
    monkeypatch.setattr(health.pwd, "getpwnam", _raise)
    out = health.check_ownership({})
    assert out["ok"] is False
    assert "not found" in out["detail"]


def test_uid_below_boundary_reported(monkeypatch):
    _patch_uid(monkeypatch, 500)
    out = health.check_ownership({})  # no roots -> no drift
    assert out["ok"] is True
    assert out["uid"] == 500
    assert out["uid_below_1000"] is True
    assert "migration pending" not in out["detail"]


def test_uid_above_boundary_flagged_but_ok(monkeypatch):
    _patch_uid(monkeypatch, 1001)
    out = health.check_ownership({})
    assert out["ok"] is True  # informational, not a failure
    assert out["uid_below_1000"] is False
    assert "migration pending" in out["detail"]


def test_root_ownership_drift_detected(monkeypatch, tmp_path):
    # Expected uid != the uid that owns the tmp dir (the test user) -> drift.
    _patch_uid(monkeypatch, os.getuid() + 1)
    root = tmp_path / "ItemData"
    root.mkdir()
    out = health.check_ownership({"itemdata_root": str(root)})
    assert out["ok"] is False
    assert any("owned by uid" in d for d in out["drift"])


def test_secrets_clean(monkeypatch, tmp_path):
    _patch_uid(monkeypatch, os.getuid())  # tmp files owned by us -> no owner drift
    sroot = tmp_path / "secrets"
    sroot.mkdir(mode=0o700)
    os.chmod(sroot, 0o700)
    secret = sroot / "ebay-credentials.json"
    secret.write_text("{}")
    os.chmod(secret, 0o600)
    out = health.check_ownership({"secrets_root": str(sroot)})
    assert out["ok"] is True
    assert out["drift"] == []


def test_secrets_file_mode_drift(monkeypatch, tmp_path):
    _patch_uid(monkeypatch, os.getuid())
    sroot = tmp_path / "secrets"
    sroot.mkdir()
    os.chmod(sroot, 0o700)
    secret = sroot / "leaky.json"
    secret.write_text("{}")
    os.chmod(secret, 0o644)  # too open
    out = health.check_ownership({"secrets_root": str(sroot)})
    assert out["ok"] is False
    assert any("leaky.json" in d and "0o600" in d for d in out["drift"])


def test_secrets_dir_mode_drift(monkeypatch, tmp_path):
    _patch_uid(monkeypatch, os.getuid())
    sroot = tmp_path / "secrets"
    sroot.mkdir()
    os.chmod(sroot, 0o755)  # should be 0o700
    out = health.check_ownership({"secrets_root": str(sroot)})
    assert out["ok"] is False
    assert any("0o700" in d for d in out["drift"])


def test_check_ownership_is_in_check_all(monkeypatch):
    # Wired into the aggregate health surface.
    import inspect
    src = inspect.getsource(health.check_all)
    assert "check_ownership(cfg)" in src
