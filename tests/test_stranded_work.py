import subprocess
from pathlib import Path

from tgw.stranded_work import (
    discover_repositories,
    inspect_repository,
    inspect_worktree,
    inventory_environment,
    inventory_worktrees,
)


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _repository(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "fixture@example.invalid")
    _git(path, "config", "user.name", "Fixture")
    (path / "tracked").write_text("same\n")
    _git(path, "add", "tracked")
    _git(path, "commit", "-qm", "fixture")
    return path


def test_uncommitted_implementation_and_receipt_are_stranded(tmp_path: Path):
    repo = _repository(tmp_path / "repo")
    (repo / "src").mkdir()
    (repo / "src" / "plan_graph.py").write_text("def build(): return {}\n")
    (repo / "controller-harness-receipt.json").write_text('{"result":"passed"}\n')

    result = inspect_worktree(repo)

    assert result["classification"] == "STRANDED-WORK"
    assert result["states"]["implemented"] is True
    assert result["states"]["executed"] is True
    assert result["states"]["admitted"] is False
    assert result["cleanup_authorized"] is False


def test_inventory_is_deterministic_and_does_not_modify_worktree(tmp_path: Path):
    repo = _repository(tmp_path / "repo")
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout

    assert inventory_worktrees([repo]) == inventory_worktrees([repo])
    after = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    assert after == before


def test_receipt_only_worktree_is_evidence_residue(tmp_path: Path):
    repo = _repository(tmp_path / "repo")
    (repo / "controller-harness-receipt.json").write_text('{"result":"failed"}\n')

    result = inspect_worktree(repo)

    assert result["classification"] == "EVIDENCE-RESIDUE"
    assert result["states"]["implemented"] is False
    assert result["states"]["executed"] is True


def test_environment_inventory_discovers_nested_repository(tmp_path: Path):
    repo = _repository(tmp_path / "nested" / "repo")

    assert discover_repositories([tmp_path]) == [repo.resolve()]
    assert inspect_repository(repo)["head"]
    inventory = inventory_environment([tmp_path])
    assert inventory["summary"] == {
        "repository_count": 1,
        "worktree_count": 1,
        "stranded_work_count": 0,
        "inaccessible_worktree_count": 0,
        "evidence_residue_count": 0,
    }
