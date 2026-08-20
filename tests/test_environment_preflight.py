import hashlib
import os
from pathlib import Path

import pytest

from tgw.environment_preflight import EnvironmentPreflightError, preflight


def catalog(executable: Path):
    store = executable.parent
    digest = "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()
    return {
        "schema": "tgw-execution-environment-catalog/v1",
        "flake_lock": {"path": "flake.lock", "sha256": "sha256:" + "a" * 64},
        "actors": {"codex": {"enabled": True, "permitted_profiles": ["development"]}},
        "profiles": {"development": {"state": "ready-for-preflight", "tools": [{
            "name": "tool", "store_path": str(store), "store_path_hash": "a" * 32,
            "executable_path": str(executable), "executable_sha256": digest,
        }]}},
    }


def test_preflight_is_deterministic_and_observes_declared_binary(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    first = preflight(catalog=catalog(executable), actor="codex", profile="development", attempt_id="attempt-1")
    assert first == preflight(catalog=catalog(executable), actor="codex", profile="development", attempt_id="attempt-1")
    assert first["result"] == "PASS" and first["tools"][0]["observed_sha256"].startswith("sha256:")


def test_preflight_accepts_the_extended_v2_catalog(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    value = catalog(executable)
    value["schema"] = "tgw-execution-environment-catalog/v2"
    assert preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")["result"] == "PASS"


@pytest.mark.parametrize("mutation", ["disabled", "missing", "symlink"])
def test_preflight_refuses_nonready_or_unavailable_tool(tmp_path: Path, mutation: str):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    value = catalog(executable)
    if mutation == "disabled":
        value["actors"]["codex"]["enabled"] = False
    elif mutation == "missing":
        executable.unlink()
    else:
        executable.unlink()
        executable.symlink_to("/bin/sh")
    with pytest.raises(EnvironmentPreflightError):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")


def test_preflight_refuses_hash_mismatch_and_store_escape(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    value = catalog(executable)
    value["profiles"]["development"]["tools"][0]["executable_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(EnvironmentPreflightError, match="hash mismatch"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")
    value = catalog(executable)
    outside = tmp_path / "outside"
    outside.write_text("#!/bin/sh\n")
    os.chmod(outside, 0o555)
    executable.unlink()
    executable.symlink_to(outside)
    value["profiles"]["development"]["tools"][0]["executable_sha256"] = "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest()
    with pytest.raises(EnvironmentPreflightError, match="unavailable"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")


def test_preflight_refuses_forged_or_mismatched_store_hash(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    value = catalog(executable)
    value["profiles"]["development"]["tools"][0]["store_path_hash"] = "e" * 32
    with pytest.raises(EnvironmentPreflightError, match="store hash is invalid"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")
    value = catalog(executable)
    value["profiles"]["development"]["tools"][0]["store_path_hash"] = "b" * 32
    with pytest.raises(EnvironmentPreflightError, match="store hash mismatch"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")
