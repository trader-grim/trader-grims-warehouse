import os
from pathlib import Path

import pytest

from tgw.environment_preflight import EnvironmentPreflightError, preflight


def catalog(executable: Path):
    return {
        "schema": "tgw-execution-environment-catalog/v1",
        "flake_lock": {"path": "flake.lock", "sha256": "sha256:" + "a" * 64},
        "actors": {"codex": {"enabled": True, "permitted_profiles": ["development"]}},
        "profiles": {"development": {"state": "ready-for-preflight", "tools": [{
            "name": "tool", "store_path": "/nix/store/tool", "store_path_hash": "a" * 32,
            "executable_path": str(executable),
        }]}},
    }


def test_preflight_is_deterministic_and_observes_declared_binary(tmp_path: Path):
    executable = tmp_path / "tool"
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    first = preflight(catalog=catalog(executable), actor="codex", profile="development", attempt_id="attempt-1")
    assert first == preflight(catalog=catalog(executable), actor="codex", profile="development", attempt_id="attempt-1")
    assert first["result"] == "PASS" and first["tools"][0]["observed_sha256"].startswith("sha256:")


@pytest.mark.parametrize("mutation", ["disabled", "missing", "symlink"])
def test_preflight_refuses_nonready_or_unavailable_tool(tmp_path: Path, mutation: str):
    executable = tmp_path / "tool"
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
