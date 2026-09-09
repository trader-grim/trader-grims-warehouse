"""Git discipline for the continual harness — Todo 1916 leaf 11.1
(L11.1.LEDGER-AND-GIT-DISCIPLINE acceptance).

  * a task that took N remediation rounds lands as exactly one commit on the
    base branch, a genuine ancestor, with zero per-task branches left behind
  * `git log --oneline` over several accepted tasks reads as one commit each
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tgw.development import harness_git


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=cwd, check=True, text=True, capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    )
    return result.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "base.txt").write_text("base\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def _worktree(repo: Path, name: str) -> Path:
    path = repo.parent / name
    _git(repo, "worktree", "add", "-q", "-b", f"coding/{name}", str(path))
    return path


def _oneline(repo: Path) -> list[str]:
    return _git(repo, "log", "--oneline", "--format=%s").splitlines()


# --------------------------------------------------------------------------- #

def test_multi_round_task_lands_as_one_commit(repo):
    base = _git(repo, "rev-parse", "main")
    wt = _worktree(repo, "todo-42")

    # three "remediation rounds" plus a final uncommitted edit
    for n in range(1, 4):
        (wt / "feature.py").write_text(f"# round {n}\nvalue = {n}\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", f"round {n}")
    (wt / "feature.py").write_text("# final\nvalue = 99\n")

    landed = harness_git.land_accepted_task(
        "todo-42",
        repository=repo,
        worktree=wt,
        message="Todo 42: add the feature",
    )

    # main advanced by exactly one commit, a real child of the old base
    assert _git(repo, "rev-parse", "main^") == base
    assert _git(repo, "rev-parse", "main") == landed["commit"]
    assert _git(repo, "log", "--format=%s", "-1", "main") == "Todo 42: add the feature"
    # the one commit carries the full net tree
    assert (repo / "feature.py").exists() is False  # main worktree not checked out here
    assert _git(repo, "show", "main:feature.py") == "# final\nvalue = 99"
    # ephemeral worktree and branch are gone
    assert not wt.exists()
    assert "coding/todo-42" not in _git(repo, "branch", "--list", "coding/todo-42")
    assert landed["base_commit"] == base


def test_land_excludes_the_harness_scratch_from_the_commit(repo):
    wt = _worktree(repo, "todo-scratch")
    (wt / "feature.py").write_text("value = 1\n")
    # the runners write these into the worktree; they must not land
    (wt / ".tgw-harness").mkdir()
    (wt / ".tgw-harness" / "job.json").write_text('{"task_id": "todo-scratch"}')
    (wt / "implementation-receipt.json").write_text('{"outcome": "satisfied"}')
    (wt / "review-receipt.json").write_text('{"verdict": "PASS"}')

    # a killed coder session leaves its disposable HOME behind (no self-delete)
    sess = wt / ".tgw-harness-claude-abc123" / "home" / ".claude"
    sess.mkdir(parents=True)
    (sess / ".claude.json").write_text('{"projects": {}}')

    landed = harness_git.land_accepted_task(
        "todo-scratch", repository=repo, worktree=wt, message="Todo: feature",
    )
    files = _git(repo, "ls-tree", "-r", "--name-only", landed["commit"]).splitlines()
    assert "feature.py" in files
    assert not any(
        f.startswith(".tgw-harness") or f.endswith("-receipt.json") for f in files
    )
    assert not (wt / ".tgw-harness-claude-abc123").exists()  # purged from disk too


def test_land_refuses_when_base_moved(repo):
    wt = _worktree(repo, "todo-7")
    (wt / "a.txt").write_text("a\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "work")

    # someone else advances main first
    (repo / "other.txt").write_text("other\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "concurrent")
    moved = _git(repo, "rev-parse", "main")

    with pytest.raises(harness_git.NotFastForward):
        harness_git.land_accepted_task("todo-7", repository=repo, worktree=wt, message="m")

    assert _git(repo, "rev-parse", "main") == moved  # untouched
    assert wt.exists()  # worktree preserved for a rebind


def test_nothing_to_land_raises(repo):
    wt = _worktree(repo, "todo-empty")
    with pytest.raises(harness_git.NothingToLand):
        harness_git.land_accepted_task("todo-empty", repository=repo, worktree=wt, message="m")


def test_abandon_removes_worktree_and_branch(repo):
    base = _git(repo, "rev-parse", "main")
    wt = _worktree(repo, "todo-drop")
    (wt / "x.txt").write_text("x\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "abandoned work")

    result = harness_git.abandon_task(repository=repo, worktree=wt)

    assert not wt.exists()
    assert result["branch_deleted"] == "refs/heads/coding/todo-drop"
    assert _git(repo, "branch", "--list", "coding/todo-drop") == ""
    assert _git(repo, "rev-parse", "main") == base


def test_ref_publisher_receives_the_exact_advance(repo):
    base = _git(repo, "rev-parse", "main")
    wt = _worktree(repo, "todo-pub")
    (wt / "p.txt").write_text("p\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "w")

    seen = {}

    def publisher(ref: str, old: str, new: str) -> None:
        seen.update(ref=ref, old=old, new=new)
        _git(repo, "update-ref", ref, new, old)

    landed = harness_git.land_accepted_task(
        "todo-pub", repository=repo, worktree=wt, message="Todo pub",
        ref_publisher=publisher,
    )
    assert seen["ref"] == "refs/heads/main"
    assert seen["old"] == base
    assert seen["new"] == landed["commit"]


def test_git_log_reads_as_one_commit_per_accepted_task(repo):
    for todo in ("todo-1", "todo-2", "todo-3"):
        wt = _worktree(repo, todo)
        (wt / f"{todo}.py").write_text("x = 1\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", "rough draft")
        (wt / f"{todo}.py").write_text("x = 2\n")
        _git(wt, "add", "-A")
        _git(wt, "commit", "-q", "-m", "fix review finding")
        harness_git.land_accepted_task(
            todo, repository=repo, worktree=wt, message=f"{todo}: done",
        )

    assert _oneline(repo) == ["todo-3: done", "todo-2: done", "todo-1: done", "initial"]
    assert _git(repo, "branch", "--list", "coding/*") == ""
    # every landed commit is a linear ancestor — no merges
    assert _git(repo, "log", "--merges", "--oneline") == ""


def test_author_and_trailers(repo):
    wt = _worktree(repo, "todo-attr")
    (wt / "t.txt").write_text("t\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "w")

    harness_git.land_accepted_task(
        "todo-attr", repository=repo, worktree=wt,
        message="Todo attr: subject",
        author_name="Continual Harness", author_email="harness@tgw-lib",
        trailer_lines=("Co-Authored-By: X <x@y>",),
    )
    assert _git(repo, "log", "-1", "--format=%an <%ae>", "main") == "Continual Harness <harness@tgw-lib>"
    assert "Co-Authored-By: X <x@y>" in _git(repo, "log", "-1", "--format=%b", "main")
