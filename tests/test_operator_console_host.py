import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tgw.operator_console_host import (
    DEFAULT_PLAN_ROOT,
    ConfiguredAuthorityStore,
    configured_console_mount,
    current_plan_commit,
    load_solution,
    plan_root,
)


def _plan(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "plan"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    (root / "README.md").write_text("plan\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "plan"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, text=True, capture_output=True,
    ).stdout.strip()
    return root, commit


def test_standalone_plan_default_and_exact_commit(tmp_path: Path):
    assert plan_root({}) == DEFAULT_PLAN_ROOT
    root, commit = _plan(tmp_path)
    with pytest.raises(RuntimeError, match="exact approved"):
        current_plan_commit(lambda: {"standalone_plan_root": root})
    assert current_plan_commit(lambda: {
        "standalone_plan_root": root, "plan_approved_commit": commit,
    }) == commit

    (root / "README.md").write_text("later Plan state\n")
    subprocess.run(["git", "-C", str(root), "commit", "-qam", "later"], check=True)
    assert current_plan_commit(lambda: {
        "standalone_plan_root": root, "plan_approved_commit": commit,
    }) == commit


def test_current_plan_commit_uses_configured_git_executable(tmp_path: Path):
    root, commit = _plan(tmp_path)
    wrapper = tmp_path / "held-git"
    wrapper.write_text("#!/bin/sh\nexec git \"$@\"\n")
    wrapper.chmod(0o755)
    assert current_plan_commit(lambda: {
        "standalone_plan_root": root,
        "plan_approved_commit": commit,
        "plan_git_path": wrapper,
    }) == commit


def test_solution_loader_fails_closed_and_checks_identity(tmp_path: Path):
    root, commit = _plan(tmp_path)
    identity = "sha256:" + "a" * 64

    def provider():
        return {
            "standalone_plan_root": root,
            "plan_approved_commit": commit,
            "plan_approved_solution_hash": identity,
        }
    with pytest.raises(ValueError, match="unavailable"):
        load_solution(provider, identity)
    with pytest.raises(ValueError, match="invalid"):
        load_solution(provider, "../escape")
    with pytest.raises(ValueError, match="not the approved"):
        load_solution(provider, "sha256:" + "b" * 64)
    directory = root / "plan" / "execution" / "solutions"
    directory.mkdir(parents=True)
    (directory / "governed-platform-solution.json").write_text(json.dumps({
        "solution_hash": identity, "plan_commit": commit,
    }))
    assert load_solution(provider, identity)["solution_hash"] == identity

    (directory / "duplicate.json").write_text(json.dumps({
        "solution_hash": identity, "plan_commit": commit,
    }))
    with pytest.raises(ValueError, match="ambiguous"):
        load_solution(provider, identity)


def test_solution_loader_rejects_an_exact_hash_bound_to_an_unapproved_plan_commit(tmp_path: Path):
    root, approved = _plan(tmp_path)
    identity = "sha256:" + "c" * 64
    directory = root / "plan" / "execution" / "solutions"
    directory.mkdir(parents=True)
    (directory / "solution.json").write_text(json.dumps({
        "solution_hash": identity, "plan_commit": "d" * 40,
    }))
    with pytest.raises(ValueError, match="not bound"):
        load_solution(lambda: {
            "standalone_plan_root": root,
            "plan_approved_commit": approved,
            "plan_approved_solution_hash": identity,
        }, identity)


def test_configured_mount_is_late_bound_and_reuses_auth_functions():
    config = {}

    def operator():
        return "operator"

    def executor():
        return "executor"
    mount = configured_console_mount(
        lambda: config, require_operator=operator, require_executor=executor,
    )
    assert isinstance(mount.store, ConfiguredAuthorityStore)
    assert mount.require_operator is operator
    assert mount.require_executor is executor
    assert mount.execute_effect is not None
    with pytest.raises(RuntimeError, match="not configured"):
        mount.store.list()


def test_canonical_http_app_mounts_console_and_refuses_unpinned_docs():
    from tgw import http_server

    client = TestClient(http_server.app)
    assert client.get("/api/operator-console/discovery").status_code == 401
    site = client.get("/form/plan-authority", follow_redirects=False)
    assert site.status_code == 303
    assert site.headers["location"] == "/login?next=/form/plan-authority"
    with pytest.raises(Exception, match="approved_plan_commit_required"):
        http_server._vault_root()
