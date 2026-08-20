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


def v2_catalog(executable: Path):
    value = catalog(executable)
    value["schema"] = "tgw-execution-environment-catalog/v2"
    value["actors"]["codex"].update({
        "role": "execution-provider",
        "qualified_roles": ["implementation", "controller-verification", "independent-review"],
    })
    value["profiles"]["development"].update({
        "workspace_root_template": "/opt/TGW/w/attempts/{attempt_id}/development/worktree",
        "cache_root_template": "/var/cache/tgw/attempts/{attempt_id}/development",
    })
    return value


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
    receipt = preflight(catalog=v2_catalog(executable), actor="codex", profile="development", attempt_id="attempt-1")
    assert receipt["result"] == "PASS"
    assert receipt["workspace_root"] == "/opt/TGW/w/attempts/attempt-1/development/worktree"
    assert receipt["cache_roots"] == {"default": "/var/cache/tgw/attempts/attempt-1/development"}


def test_v2_preflight_refuses_fixed_harness_roles_and_volatile_attempt_roots(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    value = v2_catalog(executable)
    value["actors"]["codex"]["role"] = "implementer"
    with pytest.raises(EnvironmentPreflightError, match="role qualification"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")
    value = v2_catalog(executable)
    value["profiles"]["development"]["cache_root_template"] = "/tmp/{attempt_id}"
    with pytest.raises(EnvironmentPreflightError, match="durable attempt root"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")


def test_mobile_v2_preflight_binds_artifacts_caches_environment_and_commands(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    digest = "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest()
    value = v2_catalog(executable)
    value["actors"]["codex"]["permitted_profiles"].append("mobile")
    tool_names = ["flutter", "dart", "java", "gradle", "android-sdkmanager", "android-adb"]
    artifact_names = [
        "flutter-sdk", "dart-sdk", "android-sdk-platform", "android-build-tools", "android-ndk", "android-license",
    ]
    value["profiles"]["mobile"] = {
        "state": "ready-for-preflight",
        "workspace_root_template": "/opt/TGW/w/attempts/{attempt_id}/mobile/worktree",
        "cache_roots": {
            "home": "/var/cache/tgw/attempts/{attempt_id}/mobile/home",
            "pub": "/var/cache/tgw/attempts/{attempt_id}/mobile/pub",
            "gradle": "/var/cache/tgw/attempts/{attempt_id}/mobile/gradle",
            "android_user": "/var/cache/tgw/attempts/{attempt_id}/mobile/android-user",
        },
        "environment": {
            "HOME": "/var/cache/tgw/attempts/{attempt_id}/mobile/home",
            "PUB_CACHE": "/var/cache/tgw/attempts/{attempt_id}/mobile/pub",
            "GRADLE_USER_HOME": "/var/cache/tgw/attempts/{attempt_id}/mobile/gradle",
            "ANDROID_USER_HOME": "/var/cache/tgw/attempts/{attempt_id}/mobile/android-user",
            "ANDROID_HOME": "/nix/store/android-sdk",
            "ANDROID_SDK_ROOT": "/nix/store/android-sdk",
            "JAVA_HOME": "/nix/store/jdk",
        },
        "tools": [{
            "name": name, "store_path": str(executable.parent), "store_path_hash": "a" * 32,
            "executable_path": str(executable), "executable_sha256": digest,
        } for name in tool_names],
        "artifacts": [{
            "name": name, "version": "1", "store_path": str(executable.parent), "store_path_hash": "a" * 32,
            "content_path": str(executable), "content_sha256": digest,
        } for name in artifact_names],
        "verification_commands": [["flutter", "doctor", "--verbose"], ["flutter", "test"]],
    }
    receipt = preflight(catalog=value, actor="codex", profile="mobile", attempt_id="attempt-1")
    assert len(receipt["artifacts"]) == 6
    assert receipt["environment"]["HOME"].endswith("/attempt-1/mobile/home")
    assert receipt["verification_commands"][0] == ["flutter", "doctor", "--verbose"]
    value["profiles"]["mobile"]["artifacts"].pop()
    with pytest.raises(EnvironmentPreflightError, match="artifact set"):
        preflight(catalog=value, actor="codex", profile="mobile", attempt_id="attempt-1")


def test_v3_preflight_binds_complete_local_dynamic_surface_boundary(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    boundary = tmp_path / "candidate"
    component = boundary / "src/tgw/dynamic_surface.py"
    renderer = boundary / "src/tgw/static/plan_console.html"
    component.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    component.write_text("validator-controller-binding\n")
    renderer.write_text("data-only-renderer\n")
    value = v2_catalog(executable)
    value["schema"] = "tgw-execution-environment-catalog/v3"
    value["enforcement_boundary"] = {
        "schema": "tgw-dynamic-surface-enforcement-boundary/v1", "version": "candidate-one",
        "remote_inputs": False, "executable_renderer_inputs": False,
        "components": [{
            "name": "validator-controller-binding", "relative_path": "src/tgw/dynamic_surface.py",
            "content_sha256": "sha256:" + hashlib.sha256(component.read_bytes()).hexdigest(),
            "purpose": "schema validator, controller, and typed handler verifier",
        }],
        "assets": [{
            "name": "data-only-renderer", "relative_path": "src/tgw/static/plan_console.html",
            "content_sha256": "sha256:" + hashlib.sha256(renderer.read_bytes()).hexdigest(),
            "purpose": "allowlisted local renderer",
        }],
    }
    receipt = preflight(
        catalog=value, actor="codex", profile="development", attempt_id="attempt-1",
        boundary_root=boundary,
    )
    assert receipt["enforcement_boundary"]["remote_inputs"] is False
    assert len(receipt["enforcement_boundary"]["components"]) == 2
    renderer.write_text("<script>remote()</script>\n")
    with pytest.raises(EnvironmentPreflightError, match="component mismatch"):
        preflight(
            catalog=value, actor="codex", profile="development", attempt_id="attempt-1",
            boundary_root=boundary,
        )


def test_v3_preflight_refuses_missing_or_unsafe_boundary(tmp_path: Path):
    executable = tmp_path / ("a" * 32 + "-store") / "tool"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\n")
    os.chmod(executable, 0o555)
    value = v2_catalog(executable)
    value["schema"] = "tgw-execution-environment-catalog/v3"
    with pytest.raises(EnvironmentPreflightError, match="boundary is invalid"):
        preflight(catalog=value, actor="codex", profile="development", attempt_id="attempt-1")


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
