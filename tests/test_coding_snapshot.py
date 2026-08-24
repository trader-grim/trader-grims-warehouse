"""Tests for CodingTaskSnapshot builder — build_coding_snapshot()."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

# Ensure src/ is on the path so we can import tgw.workflow
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tgw.development.coding_snapshot import (  # noqa: E402
    _CHECKERS,
    CONTROLLER_PYTHON,
    _check_admitted,
    _check_committed,
    _check_implemented,
    _check_receipt,
    _find_canonical_branch,
    _git_branch,
    _git_is_clean,
    _git_rev_parse,
    build_coding_snapshot,
)
from tgw.workflow import (  # noqa: E402
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
)


def test_test_and_lint_checks_use_controller_python(tmp_path):
    """Coding snapshots must not depend on an arbitrary PATH python."""
    completed = subprocess.CompletedProcess([], 0, "", "")
    with patch("tgw.development.coding_snapshot.subprocess.run", return_value=completed) as run:
        _CHECKERS["tested"](tmp_path)
        _CHECKERS["linted"](tmp_path)

    assert run.call_args_list[0].args[0][:2] == [CONTROLLER_PYTHON, "-m"]
    assert run.call_args_list[1].args[0][:2] == [CONTROLLER_PYTHON, "-m"]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(path),
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path),
        check=True,
    )


def _git_commit(path: Path, message: str, *, allow_empty: bool = False) -> str:
    """Make a commit and return its SHA."""
    subprocess.run(["git", "add", "-A"], cwd=str(path), check=True)
    args = ["git", "commit", "-m", message]
    if allow_empty:
        args.insert(2, "--allow-empty")
    subprocess.run(args, cwd=str(path), check=True)
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=str(path),
        check=True,
    )
    return proc.stdout.strip()


def _git_checkout_b(path: Path, branch: str) -> None:
    subprocess.run(["git", "checkout", "-b", branch], cwd=str(path), check=True)


def _git_write_file(path: Path, filename: str, content: str) -> None:
    (path / filename).write_text(content)


def _goal(*conditions: str) -> GoalProfile:
    return GoalProfile(
        identity="coding-goal", version="1", required=conditions
    )


# ---------------------------------------------------------------------------
# Unit: git helpers
# ---------------------------------------------------------------------------

class TestGitHelpers:
    def test_rev_parse_returns_sha_in_repo(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        sha = _git_rev_parse(tmp_path, "HEAD")
        assert sha is not None
        assert len(sha) == 40

    def test_rev_parse_returns_none_outside_repo(self, tmp_path):
        assert _git_rev_parse(tmp_path, "HEAD") is None

    def test_git_branch_returns_branch_name(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        assert _git_branch(tmp_path) == "main"

    def test_git_branch_detached_head(self, tmp_path):
        _git_init(tmp_path)
        sha = _git_commit(tmp_path, "initial", allow_empty=True)
        subprocess.run(
            ["git", "checkout", sha], cwd=str(tmp_path), check=True
        )
        assert _git_branch(tmp_path) is None

    def test_git_is_clean_true(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        assert _git_is_clean(tmp_path) is True

    def test_git_is_clean_false_with_untracked(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        (tmp_path / "new_file.py").write_text("x")
        assert _git_is_clean(tmp_path) is False

    def test_find_canonical_main(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        assert _find_canonical_branch(tmp_path) == "main"

    def test_find_canonical_none(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        subprocess.run(
            ["git", "checkout", "-b", "other"], cwd=str(tmp_path), check=True
        )
        # Delete main so no canonical branch exists
        subprocess.run(
            ["git", "branch", "-D", "main"], cwd=str(tmp_path), check=True
        )
        assert _find_canonical_branch(tmp_path) is None


# ---------------------------------------------------------------------------
# Unit: _check_implemented
# ---------------------------------------------------------------------------

class TestCheckImplemented:
    def test_unknown_when_not_a_repo(self, tmp_path):
        result, reasons, evidence = _check_implemented(tmp_path)
        assert result is FingerprintResult.UNKNOWN
        assert "not a git repository" in reasons[0]

    def test_false_when_detached_head(self, tmp_path):
        _git_init(tmp_path)
        sha = _git_commit(tmp_path, "initial", allow_empty=True)
        subprocess.run(
            ["git", "checkout", sha], cwd=str(tmp_path), check=True
        )
        result, reasons, _ = _check_implemented(tmp_path)
        assert result is FingerprintResult.FALSE
        assert any("detached HEAD" in r for r in reasons)

    def test_false_when_on_main(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        result, reasons, _ = _check_implemented(tmp_path)
        assert result is FingerprintResult.FALSE
        assert any("canonical branch" in r for r in reasons)

    def test_false_when_no_changes(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/add-x")
        result, reasons, _ = _check_implemented(tmp_path)
        assert result is FingerprintResult.FALSE
        assert any("no changes" in r for r in reasons)

    def test_true_when_branch_has_changes(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/add-x")
        _git_write_file(tmp_path, "x.py", "x = 1")
        _git_commit(tmp_path, "add x")
        result, reasons, evidence = _check_implemented(tmp_path)
        assert result is FingerprintResult.TRUE
        assert any("has changes" in r for r in reasons)
        assert len(evidence) > 0

    def test_false_when_no_canonical_branch(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/add-x")
        _git_write_file(tmp_path, "x.py", "x = 1")
        _git_commit(tmp_path, "add x")
        # Remove main branch
        subprocess.run(
            ["git", "branch", "-D", "main"], cwd=str(tmp_path), check=True
        )
        result, reasons, _ = _check_implemented(tmp_path)
        assert result is FingerprintResult.FALSE
        assert any("cannot find canonical" in r for r in reasons)

    def test_source_bound_clean_checkout_is_not_implemented(self, tmp_path):
        _git_init(tmp_path)
        baseline = _git_commit(tmp_path, "bootstrap", allow_empty=True)

        result, reasons, evidence = _check_implemented(tmp_path, baseline)

        assert result is FingerprintResult.FALSE
        assert reasons == ("working tree matches the source-bound commit",)
        assert evidence[0].supersession_identity == baseline

    def test_source_bound_uncommitted_change_is_implemented(self, tmp_path):
        _git_init(tmp_path)
        baseline = _git_commit(tmp_path, "bootstrap", allow_empty=True)
        _git_write_file(tmp_path, "implementation.py", "implemented = True\n")

        result, reasons, evidence = _check_implemented(tmp_path, baseline)

        assert result is FingerprintResult.TRUE
        assert "implementation changes" in reasons[0]
        assert evidence[0].supersession_identity == baseline

    def test_source_bound_head_change_is_unknown(self, tmp_path):
        _git_init(tmp_path)
        baseline = _git_commit(tmp_path, "bootstrap", allow_empty=True)
        _git_commit(tmp_path, "unexpected commit", allow_empty=True)

        result, reasons, _ = _check_implemented(tmp_path, baseline)

        assert result is FingerprintResult.UNKNOWN
        assert "no longer matches" in reasons[0]


# ---------------------------------------------------------------------------
# Unit: _check_admitted
# ---------------------------------------------------------------------------

class TestCheckAdmitted:
    def test_unknown_not_a_repo(self, tmp_path):
        result, reasons, _ = _check_admitted(tmp_path)
        assert result is FingerprintResult.UNKNOWN

    def test_true_when_commit_on_canonical(self, tmp_path):
        _git_init(tmp_path)
        sha = _git_commit(tmp_path, "initial", allow_empty=True)
        result, reasons, _ = _check_admitted(tmp_path)
        assert result is FingerprintResult.TRUE
        assert sha[:8] in reasons[0]

    def test_false_when_commit_not_on_canonical(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/x")
        _git_write_file(tmp_path, "x.py", "x = 1")
        sha = _git_commit(tmp_path, "feature commit")
        result, reasons, _ = _check_admitted(tmp_path)
        assert result is FingerprintResult.FALSE
        assert sha[:8] in reasons[0]


# ---------------------------------------------------------------------------
# Unit: _check_committed
# ---------------------------------------------------------------------------

class TestCheckCommitted:
    def test_unknown_not_a_repo(self, tmp_path):
        result, reasons, _ = _check_committed(tmp_path)
        assert result is FingerprintResult.UNKNOWN

    def test_true_when_clean(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        result, reasons, _ = _check_committed(tmp_path)
        assert result is FingerprintResult.TRUE
        assert "clean" in reasons[0]

    def test_false_when_dirty(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        (tmp_path / "dirty.py").write_text("x")
        result, reasons, _ = _check_committed(tmp_path)
        assert result is FingerprintResult.FALSE
        assert "dirty" in reasons[0]


# ---------------------------------------------------------------------------
# Unit: receipt checks
# ---------------------------------------------------------------------------

class TestCheckReceipt:
    def test_false_when_file_missing(self, tmp_path):
        result, reasons, _ = _check_receipt(tmp_path, "reviewed")
        assert result is FingerprintResult.FALSE
        assert any("not found" in r for r in reasons)

    def test_true_when_pass(self, tmp_path):
        (tmp_path / "review-receipt.json").write_text(
            json.dumps({"status": "PASS", "outcome": "satisfied", "established_conditions": ["reviewed"], "graph_id": "g", "object_id": "o", "object_generation": "x"})
        )
        result, reasons, _ = _check_receipt(tmp_path, "reviewed", "o", "x")
        assert result is FingerprintResult.TRUE
        assert any("PASS" in r for r in reasons)

    def test_false_when_fail(self, tmp_path):
        (tmp_path / "review-receipt.json").write_text(
            json.dumps({"status": "FAIL", "issues": ["bad code"], "graph_id": "g", "object_id": "o", "object_generation": "x"})
        )
        result, reasons, _ = _check_receipt(tmp_path, "reviewed", "o", "x")
        assert result is FingerprintResult.FALSE
        assert any("FAIL" in r for r in reasons)

    def test_not_applicable_unknown_condition(self, tmp_path):
        result, reasons, _ = _check_receipt(tmp_path, "bogus_condition")
        assert result is FingerprintResult.NOT_APPLICABLE

    def test_deployment_receipt(self, tmp_path):
        (tmp_path / "deployment-receipt.json").write_text(
            json.dumps({"status": "PASS", "outcome": "satisfied", "established_conditions": ["deployed"], "graph_id": "g", "object_id": "o", "object_generation": "x"})
        )
        result, reasons, _ = _check_receipt(tmp_path, "deployed", "o", "x")
        assert result is FingerprintResult.TRUE

    def test_controller_harness_receipt(self, tmp_path):
        (tmp_path / "controller-harness-receipt.json").write_text(
            json.dumps({"status": "PASS", "outcome": "satisfied", "established_conditions": ["controller_verified"], "graph_id": "g", "object_id": "o", "object_generation": "x"})
        )
        result, reasons, _ = _check_receipt(tmp_path, "controller_verified", "o", "x")
        assert result is FingerprintResult.TRUE


# ---------------------------------------------------------------------------
# Integration: build_coding_snapshot
# ---------------------------------------------------------------------------

class TestBuildCodingSnapshot:
    def test_returns_object_snapshot(self, tmp_path):
        snapshot = build_coding_snapshot(
            tmp_path, _goal("implemented", "committed")
        )
        assert isinstance(snapshot, ObjectSnapshot)
        assert snapshot.object_id == str(tmp_path.resolve())
        assert len(snapshot.generation) == 16
        assert len(snapshot.assertions) == 2

    def test_non_repo_all_unknown_or_false(self, tmp_path):
        """Outside a repo, git conditions are UNKNOWN, receipts FALSE."""
        snapshot = build_coding_snapshot(
            tmp_path,
            _goal("implemented", "committed", "reviewed", "deployed"),
        )
        by_id = {a.condition_id: a for a in snapshot.assertions}

        assert by_id["implemented"].result is FingerprintResult.UNKNOWN
        assert by_id["committed"].result is FingerprintResult.UNKNOWN
        assert by_id["reviewed"].result is FingerprintResult.FALSE
        assert by_id["deployed"].result is FingerprintResult.FALSE

    def test_feature_branch_receipts(self, tmp_path):
        """Feature branch with clean tree and review receipt."""
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/x")
        _git_write_file(tmp_path, "src.py", "def f(): pass")
        _git_commit(tmp_path, "add feature")
        unbound = build_coding_snapshot(tmp_path, _goal("implemented"))
        (tmp_path / "review-receipt.json").write_text(json.dumps({
            "status": "PASS", "graph_id": "g", "object_id": str(tmp_path.resolve()),
            "object_generation": unbound.generation, "outcome": "satisfied",
            "established_conditions": ["reviewed"],
        }))

        snapshot = build_coding_snapshot(
            tmp_path,
            _goal("implemented", "committed", "reviewed"),
        )
        by_id = {a.condition_id: a for a in snapshot.assertions}

        assert by_id["implemented"].result is FingerprintResult.TRUE
        assert by_id["committed"].result is FingerprintResult.TRUE
        assert by_id["reviewed"].result is FingerprintResult.TRUE

    def test_stale_receipt_does_not_establish_conditions_after_source_change(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/x")
        _git_write_file(tmp_path, "x.py", "x = 1")
        _git_commit(tmp_path, "feature")
        first = build_coding_snapshot(tmp_path, _goal("reviewed", "controller_verified"))
        receipt = {"status": "PASS", "graph_id": "g", "object_id": str(tmp_path.resolve()), "object_generation": first.generation, "outcome": "satisfied"}
        (tmp_path / "review-receipt.json").write_text(json.dumps({**receipt, "established_conditions": ["reviewed"]}))
        (tmp_path / "controller-harness-receipt.json").write_text(json.dumps({**receipt, "established_conditions": ["controller_verified"]}))
        assert all(a.result is FingerprintResult.TRUE for a in build_coding_snapshot(tmp_path, _goal("reviewed", "controller_verified")).assertions)
        _git_write_file(tmp_path, "x.py", "x = 2")
        stale = build_coding_snapshot(tmp_path, _goal("reviewed", "controller_verified"))
        assert all(a.result is FingerprintResult.STALE for a in stale.assertions)

    def test_local_test_and_lint_conditions_use_bound_controller_receipt(
        self,
        tmp_path,
        monkeypatch,
    ):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        before = build_coding_snapshot(tmp_path, _goal("controller_verified"))
        (tmp_path / "controller-harness-receipt.json").write_text(
            json.dumps(
                {
                    "status": "PASS",
                    "outcome": "satisfied",
                    "established_conditions": [
                        "tested",
                        "linted",
                        "controller_verified",
                    ],
                    "graph_id": "controller-graph",
                    "object_id": str(tmp_path.resolve()),
                    "object_generation": before.generation,
                }
            )
        )
        monkeypatch.setitem(
            _CHECKERS,
            "tested",
            lambda *_args: (_ for _ in ()).throw(AssertionError("pytest ran")),
        )
        monkeypatch.setitem(
            _CHECKERS,
            "linted",
            lambda *_args: (_ for _ in ()).throw(AssertionError("ruff ran")),
        )

        snapshot = build_coding_snapshot(
            tmp_path,
            _goal("tested", "linted"),
            receipt_backed_conditions=frozenset({"tested", "linted"}),
        )

        assert all(
            assertion.result is FingerprintResult.TRUE
            for assertion in snapshot.assertions
        )

    def test_stitch_receipt_does_not_dirty_or_change_generation(self, tmp_path):
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        before = build_coding_snapshot(tmp_path, _goal("committed"))
        (tmp_path / "stitch-receipt.json").write_text('{"status":"PASS"}')
        after = build_coding_snapshot(tmp_path, _goal("committed"))
        assert after.generation == before.generation
        assert after.assertions[0].result is FingerprintResult.TRUE

    def test_unknown_condition_is_unknown(self, tmp_path):
        """A condition with no checker returns UNKNOWN."""
        snapshot = build_coding_snapshot(
            tmp_path, _goal("bogus_condition_xyz")
        )
        assert len(snapshot.assertions) == 1
        assert snapshot.assertions[0].result is FingerprintResult.UNKNOWN
        assert "no checker" in snapshot.assertions[0].reasons[0]

    def test_goal_profile_propagates(self, tmp_path):
        """Goal profile identity and version are not in the snapshot itself
        but the condition set determines generation hash."""
        goal = GoalProfile(
            identity="my-goal", version="3", required=("implemented",)
        )
        snapshot = build_coding_snapshot(tmp_path, goal)
        assert len(snapshot.assertions) == 1

    def test_all_eight_known_conditions_have_checkers(self):
        """Verify every coding condition has a registered checker."""
        expected = {
            "implemented",
            "tested",
            "linted",
            "reviewed",
            "controller_verified",
            "admitted",
            "committed",
            "deployed",
        }
        assert set(_CHECKERS.keys()) == expected

    def test_sorted_assertions(self, tmp_path):
        """Assertions are returned in sorted condition_id order."""
        _git_init(tmp_path)
        _git_commit(tmp_path, "initial", allow_empty=True)
        _git_checkout_b(tmp_path, "feature/x")
        _git_write_file(tmp_path, "f.py", "x=1")
        _git_commit(tmp_path, "add")

        snapshot = build_coding_snapshot(
            tmp_path,
            _goal("committed", "implemented", "admitted"),
        )
        ids = [a.condition_id for a in snapshot.assertions]
        assert ids == ["admitted", "committed", "implemented"]

    def test_generation_stable_for_same_conditions(self, tmp_path):
        """Same condition set → same generation hash."""
        s1 = build_coding_snapshot(
            tmp_path, _goal("implemented", "committed")
        )
        s2 = build_coding_snapshot(
            tmp_path, _goal("committed", "implemented")
        )
        assert s1.generation == s2.generation

    def test_generation_differs_for_different_commits(self, tmp_path):
        """Different commits in different repos produce different generation hashes."""
        def _make_repo(parent, name, msg):
            repo = parent / name
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init"], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test"], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "-m", msg], capture_output=True)
            return repo
        r1 = _make_repo(tmp_path, "r1", "first")
        r2 = _make_repo(tmp_path, "r2", "second")
        s1 = build_coding_snapshot(r1, _goal("implemented"))
        s2 = build_coding_snapshot(r2, _goal("implemented"))
        assert s1.generation != s2.generation

    def test_generation_is_source_bound_not_request_branch_bound(self, tmp_path):
        """The same source state can be pre-attested before request provisioning."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)
        _git_write_file(repo, "source.py", "VALUE = 1\n")
        _git_commit(repo, "source")

        request_worktree = tmp_path / "request-worktree"
        subprocess.run(
            [
                "git", "worktree", "add", "-b", "coding/request-123",
                str(request_worktree), "HEAD",
            ],
            cwd=str(repo),
            check=True,
        )

        before = build_coding_snapshot(repo, _goal("committed"))
        provisioned = build_coding_snapshot(
            request_worktree, _goal("committed")
        )
        assert before.generation == provisioned.generation
