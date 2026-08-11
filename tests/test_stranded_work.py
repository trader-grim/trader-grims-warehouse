import subprocess
from pathlib import Path

from tgw.stranded_work import inspect_worktree, inventory_worktrees


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_uncommitted_implementation_and_receipt_are_stranded(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "README.md").write_text("fixture\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "fixture")
    (repo / "src").mkdir()
    (repo / "src" / "plan_graph.py").write_text("def build(): return {}\n")
    (repo / "results").mkdir()
    (repo / "results" / "1721-RESULT.md").write_text("110 tests passed\n")

    result = inspect_worktree(repo)

    assert result["classification"] == "STRANDED-WORK"
    assert result["states"] == {
        "designed": False,
        "implemented": True,
        "executed": True,
        "admitted": False,
        "deployed": None,
    }
    assert result["cleanup_authorized"] is False


def test_inventory_is_deterministic_and_does_not_modify_worktree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "Fixture")
    (repo / "tracked").write_text("same\n")
    _git(repo, "add", "tracked")
    _git(repo, "commit", "-qm", "fixture")
    before = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        check=True, capture_output=True,
    ).stdout

    first = inventory_worktrees([repo])
    second = inventory_worktrees([repo])

    assert first == second
    assert subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z"],
        check=True, capture_output=True,
    ).stdout == before
