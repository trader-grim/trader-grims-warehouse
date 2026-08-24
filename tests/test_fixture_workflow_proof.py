from pathlib import Path
import subprocess
from unittest.mock import patch

from tgw.development.fixture_workflow_proof import run_fixture_proof
from tgw.development.provider_dispatch import ProviderAdapter


def test_fixture_proof_runs_only_isolated_plan_bound_coding_path(tmp_path):
    source = Path(__file__).resolve().parents[1]
    commit = __import__("subprocess").check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    with patch(
        "tgw.development.foreman.resolve_implementation_adapter",
        return_value=ProviderAdapter("implementation", "codex-local-runner", "codex-implement", "codex-implement"),
    ):
        result = run_fixture_proof(source_root=source, fixture_root=tmp_path / "fixture", candidate_commit=commit)
    try:
        binding = result["plan_bound_todo"]["binding"]
        assert result["coding_request"]["payload"]["plan_binding"] == binding
        assert result["coding_execution"]["envelope"]["plan_binding"] == binding
        assert Path(result["receipt"]["path"]).is_file()
        assert result["ordinary_runtime_effects"] == []
    finally:
        subprocess.run(["git", "-C", str(source), "worktree", "remove", "--force", result["allocated_worktree"]["identity"]["worktree"]], check=True)
        subprocess.run(["git", "-C", str(source), "branch", "--delete", "--force", result["allocated_worktree"]["identity"]["branch"]], check=True)
