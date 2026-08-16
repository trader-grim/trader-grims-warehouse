import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLAN_ROOT = Path("/opt/TGW/library/plans")
APPROVED_REF = "refs/tgw/approved/GOVERNED-EXECUTION-PLATFORM"


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_luet_conformance", ROOT / "scripts/run_luet_conformance.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_luet_runner_reads_the_catalog_from_the_exact_candidate_tree():
    runner = _runner_module()
    commit, tree, catalog, _graph, plan_commit = runner._bound_candidate_catalog(
        ROOT, "HEAD", plan_repository=PLAN_ROOT, approved_ref=APPROVED_REF,
    )

    assert commit == subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    assert tree == subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True).strip()
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
            repository, "HEAD", plan_repository=PLAN_ROOT, approved_ref=APPROVED_REF,
        )
