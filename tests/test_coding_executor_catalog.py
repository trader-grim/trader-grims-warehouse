"""The one declarative source of truth for coding executors — LEAF-11-9 W1.

`tgw.coding_executor_catalog` is what harness_session (chain + credential +
binary discovery), harness_cli (binary path), and tgw.model_selector (known
set) all read instead of a hard-coded list. Adding an executor must be a
catalogue edit only.
"""

from __future__ import annotations

import json

import pytest

from tgw import coding_executor_catalog as cec


def _write(tmp_path, executors):
    path = tmp_path / "tgw-coding-executors.json"
    path.write_text(json.dumps({"schema": cec.SCHEMA, "executors": executors}), encoding="utf-8")
    return path


_ENTRY = {
    "binary_name": "acme",
    "install_source": "npm:acme-cli",
    "install_target_path": "/usr/local/bin/acme",
    "runtime_deps": ["node"],
    "verify_cmd": ["acme", "--version"],
    "credential_env": ["ACME_API_KEY"],
    "auth_file": None,
    "enabled": True,
}


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("TGW_CODING_EXECUTORS", raising=False)


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #

def test_committed_default_catalogue_parses_and_has_the_wired_executors():
    specs = cec.load_catalog(cec._REPO_DEFAULT)
    assert {"claude", "codex"} <= set(specs)
    claude = specs["claude"]
    assert claude.binary_name == "claude"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in claude.credential_env
    assert claude.enabled is True
    # every entry is a well-formed spec
    for spec in specs.values():
        assert isinstance(spec.runtime_deps, tuple)
        assert isinstance(spec.credential_env, tuple)
        assert isinstance(spec.enabled, bool)


def test_parse_from_an_explicit_path(tmp_path):
    path = _write(tmp_path, {"acme": _ENTRY})
    specs = cec.load_catalog(path)
    assert specs["acme"].install_source == "npm:acme-cli"
    assert specs["acme"].verify_cmd == ("acme", "--version")


def test_env_override_selects_the_catalogue_file(tmp_path, monkeypatch):
    path = _write(tmp_path, {"acme": _ENTRY})
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(path))
    assert cec.executor_names() == ("acme",)


def test_missing_required_key_is_a_catalog_error(tmp_path):
    bad = {k: v for k, v in _ENTRY.items() if k != "binary_name"}
    with pytest.raises(cec.CatalogError, match="binary_name"):
        cec.load_catalog(_write(tmp_path, {"acme": bad}))


def test_reserved_name_in_the_catalogue_is_rejected(tmp_path):
    for reserved in ("stub", "manual"):
        with pytest.raises(cec.CatalogError, match="reserved"):
            cec.load_catalog(_write(tmp_path, {reserved: _ENTRY}))


def test_malformed_json_is_a_catalog_error(tmp_path):
    path = tmp_path / "tgw-coding-executors.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(cec.CatalogError):
        cec.load_catalog(path)


def test_enabled_only_filter(tmp_path):
    path = _write(tmp_path, {
        "acme": _ENTRY,
        "beta": {**_ENTRY, "binary_name": "beta", "enabled": False},
    })
    assert set(cec.executor_names(path=path)) == {"acme", "beta"}
    assert cec.executor_names(enabled_only=True, path=path) == ("acme",)


# --------------------------------------------------------------------------- #
# binary discovery
# --------------------------------------------------------------------------- #

def test_discover_binary_prefers_the_per_executor_env_var(tmp_path, monkeypatch):
    exe = tmp_path / "acme"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(_write(tmp_path, {"acme": _ENTRY})))
    monkeypatch.setenv(cec.binary_env_var("acme"), str(exe))
    assert cec.discover_binary("acme") == str(exe)


def test_discover_binary_falls_back_to_install_target_and_discovery_paths(tmp_path, monkeypatch):
    target = tmp_path / "bin" / "acme"
    target.parent.mkdir()
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    entry = {**_ENTRY, "install_target_path": str(target), "discovery_paths": []}
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(_write(tmp_path, {"acme": entry})))
    monkeypatch.delenv(cec.binary_env_var("acme"), raising=False)
    monkeypatch.setattr(cec.shutil, "which", lambda _n: None)
    assert cec.discover_binary("acme") == str(target.resolve())


def test_discover_binary_returns_none_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(_write(tmp_path, {"acme": _ENTRY})))
    monkeypatch.delenv(cec.binary_env_var("acme"), raising=False)
    monkeypatch.setattr(cec.shutil, "which", lambda _n: None)
    assert cec.discover_binary("acme") is None


# --------------------------------------------------------------------------- #
# credentials — a miss is a WARN, never a hard failure
# --------------------------------------------------------------------------- #

def test_session_credential_reads_the_named_slot_in_order(tmp_path, monkeypatch):
    entry = {**_ENTRY, "credential_env": ["ACME_PRIMARY", "ACME_FALLBACK"]}
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(_write(tmp_path, {"acme": entry})))
    monkeypatch.setattr(cec, "_source_secrets", lambda: None)
    monkeypatch.delenv("ACME_PRIMARY", raising=False)
    monkeypatch.setenv("ACME_FALLBACK", "sekret")
    assert cec.session_credential("acme") == ("ACME_FALLBACK", "sekret")


def test_credential_ready_is_false_when_no_env_and_no_auth_file(tmp_path, monkeypatch):
    entry = {**_ENTRY, "credential_env": ["ACME_API_KEY"], "auth_file": str(tmp_path / "nope.json")}
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(_write(tmp_path, {"acme": entry})))
    monkeypatch.setattr(cec, "_source_secrets", lambda: None)
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    assert cec.credential_ready("acme") is False


def test_credential_ready_true_from_auth_file_alone(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    entry = {**_ENTRY, "credential_env": ["ACME_API_KEY"], "auth_file": str(auth)}
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(_write(tmp_path, {"acme": entry})))
    monkeypatch.setattr(cec, "_source_secrets", lambda: None)
    monkeypatch.delenv("ACME_API_KEY", raising=False)
    assert cec.credential_ready("acme") is True
