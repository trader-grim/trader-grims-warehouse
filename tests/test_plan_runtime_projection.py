import json
import os
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.build_plan_runtime_projection import git_show
from scripts.solve_governed_platform import plan_commit
from tgw.operator_console_host import current_plan_commit, load_solution
from tgw.plan_render import PlanRenderBindingError, approved_render_plan_identity
from tgw.plan_runtime_projection import load_projection, validate_projection

ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"
SOLUTION_HASH = "sha256:ecce15aad2699492c0c5577bff1af7005ffbbec6ae6166b325b34c1cc7e70e9f"
PROJECTION = ROOT / "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-058e2f98.json"


def _value():
    return json.loads(PROJECTION.read_text(encoding="utf-8"))


def test_projection_binds_current_approved_plan_and_complete_solution():
    value = validate_projection(_value(), expected_plan_commit=PLAN_COMMIT)
    assert value["solution"]["complete"] is True
    assert value["solution"]["dispatchable"] is True
    assert value["solution"]["conformance_verified"] is True
    assert value["solution"]["solution_hash"] == SOLUTION_HASH
    assert value["solution"]["closure_hash"] == "sha256:16db00efe71a3c84d27faf012e58e5e664abe47e7eece40f2436dd125943f7bb"


def test_projection_runtime_render_consumers_never_fall_back_to_direct_plan_root(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("direct Plan root was accessed")

    monkeypatch.setattr("tgw.plan_graph.approved_plan_binding", forbidden)
    with pytest.raises(PlanRenderBindingError, match="canonical_plan_context_required"):
        approved_render_plan_identity(
            {
                "plan_projection_path": PROJECTION,
                "standalone_plan_root": "/run/tgw/no-local-plan",
                "plan_approved_commit": PLAN_COMMIT,
                "plan_approved_solution_hash": SOLUTION_HASH,
            }
        )


@pytest.mark.parametrize("lane", ["plan_commit", "solution", "plan_files", "self_hash"])
def test_projection_rejects_rehashed_or_unhashed_authority_mutation(lane):
    value = deepcopy(_value())
    if lane == "plan_commit":
        value["plan_commit"] = "0" * 40
    elif lane == "solution":
        value["solution"]["complete"] = False
    elif lane == "plan_files":
        value["plan_files"][0]["sha256"] = "sha256:" + "0" * 64
    else:
        value["projection_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError):
        validate_projection(value)


def test_held_projection_loader_and_console_host_use_one_exact_projection(tmp_path: Path):
    value = _value()
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    path = root / "projection.json"
    path.write_bytes(PROJECTION.read_bytes())
    path.chmod(0o400)
    config = {
        "plan_projection_path": path,
        "plan_projection_trusted_uid": os.getuid(),
        "plan_projection_root": root,
        "plan_approved_commit": PLAN_COMMIT,
        "plan_approved_solution_hash": value["solution"]["solution_hash"],
    }
    loaded = load_projection(path, expected_plan_commit=config["plan_approved_commit"], trusted_uid=os.getuid(), trusted_root=root)
    assert current_plan_commit(lambda: config) == loaded["plan_commit"]
    assert load_solution(lambda: config, loaded["solution"]["solution_hash"]) == loaded["solution"]
    with pytest.raises(ValueError, match="not the approved"):
        load_solution(lambda: config, "sha256:" + "0" * 64)


def test_projection_loader_rejects_symlink_and_writable_file(tmp_path: Path):
    root = tmp_path / "protected"
    root.mkdir(mode=0o700)
    path = root / "projection.json"
    path.write_bytes(PROJECTION.read_bytes())
    with pytest.raises(ValueError, match="immutable"):
        load_projection(path, trusted_uid=os.getuid(), trusted_root=root)
    path.chmod(0o400)
    alias = root / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(OSError):
        load_projection(alias, trusted_uid=os.getuid(), trusted_root=root)


def test_projection_builder_reads_only_the_exact_full_plan_commit(tmp_path: Path):
    plan_root = tmp_path / "plans"
    plan_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=plan_root, check=True)
    subprocess.run(["git", "config", "user.name", "TGW test"], cwd=plan_root, check=True)
    subprocess.run(["git", "config", "user.email", "test@tgw.invalid"], cwd=plan_root, check=True)
    relative = "plan/PLAN-governed-execution-platform-build.md"
    source = plan_root / relative
    source.parent.mkdir()
    source.write_text("approved bytes\n", encoding="utf-8")
    subprocess.run(["git", "add", relative], cwd=plan_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "approved"], cwd=plan_root, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=plan_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()

    source.write_text("unapproved working-tree bytes\n", encoding="utf-8")

    assert plan_commit(plan_root, commit) == commit
    assert git_show(plan_root, commit, relative) == b"approved bytes\n"
    with pytest.raises(ValueError, match="full Git commit"):
        plan_commit(plan_root, "HEAD")
