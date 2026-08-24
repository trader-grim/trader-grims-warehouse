import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAN_ROOT = Path("/opt/TGW/library/plans")
APPROVED_COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_luet_conformance", ROOT / "scripts/run_luet_conformance.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_luet_runner_reads_the_current_catalog_from_the_exact_candidate_tree(tmp_path):
    runner = _runner_module()
    repository = tmp_path / "candidate"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    catalog_path = repository / "agent-services/catalogs/governed-execution-platform-v1.json"
    catalog_path.parent.mkdir(parents=True)
    catalog_path.write_bytes((ROOT / "agent-services/catalogs/governed-execution-platform-v1.json").read_bytes())
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "catalog"], cwd=repository, check=True)
    commit, tree, catalog, _graph, plan_commit = runner._bound_candidate_catalog(
        repository, "HEAD", plan_repository=PLAN_ROOT, approved_ref=APPROVED_COMMIT,
    )

    assert commit == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository, text=True).strip()
    assert tree == subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repository, text=True).strip()
    assert catalog["schema"] == "tgw-plan-provider-catalog/v1"
    assert catalog["plan_commit"] == plan_commit


def test_luet_runner_refuses_a_catalog_not_bound_to_the_approved_plan(tmp_path):
    runner = _runner_module()
    repository = tmp_path / "candidate"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    catalog_path = repository / "agent-services/catalogs/governed-execution-platform-v1.json"
    catalog_path.parent.mkdir(parents=True)
    catalog = json.loads((ROOT / "agent-services/catalogs/governed-execution-platform-v1.json").read_text())
    catalog["plan_commit"] = "a" * 40
    catalog_path.write_text(json.dumps(catalog))
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "catalog"], cwd=repository, check=True)

    with pytest.raises(ValueError, match="approved Plan commit"):
        runner._bound_candidate_catalog(
            repository, "HEAD", plan_repository=PLAN_ROOT, approved_ref=APPROVED_COMMIT,
        )


def test_luet_runner_refuses_a_catalog_with_the_wrong_plan_semantics(tmp_path):
    runner = _runner_module()
    repository = tmp_path / "candidate"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    catalog_path = repository / "agent-services/catalogs/governed-execution-platform-v1.json"
    catalog_path.parent.mkdir(parents=True)
    catalog = json.loads((ROOT / "agent-services/catalogs/governed-execution-platform-v1.json").read_text())
    catalog["plan_id"] = "wrong-plan"
    catalog_path.write_text(json.dumps(catalog))
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "catalog"], cwd=repository, check=True)

    with pytest.raises(ValueError, match="does not derive the approved Plan graph"):
        runner._bound_candidate_catalog(
            repository, "HEAD", plan_repository=PLAN_ROOT, approved_ref=APPROVED_COMMIT,
        )


def test_luet_runner_refuses_a_movable_plan_ref_before_reading_a_catalog(tmp_path):
    runner = _runner_module()
    with pytest.raises(ValueError, match="exact full Git commit"):
        runner._bound_candidate_catalog(
            tmp_path, "HEAD", plan_repository=PLAN_ROOT,
            approved_ref="refs/tgw/approved/GOVERNED-EXECUTION-PLATFORM",
        )
