"""CodingTaskSnapshot builder — inspects a git worktree for coding
condition evidence.

Reads git state, runs pytest/ruff via subprocess, and checks receipt files.
No filesystem writes occur, but subprocess execution may produce side-effect
output (test results, lint reports)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from .contracts import (
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
)

# ---------------------------------------------------------------------------
# Condition checkers — one function per coding condition.
# Each returns (FingerprintResult, reasons, evidence_references).
# ---------------------------------------------------------------------------

CANONICAL_BRANCHES = frozenset({"main", "master"})

_RECEIPT_PATHS: dict[str, str] = {
    "reviewed": "review-receipt.json",
    "controller_verified": "controller-harness-receipt.json",
    "deployed": "deployment-receipt.json",
}


def _git(
    worktree: Path,
    *args: str,
    timeout: int = 30,
) -> tuple[int, str, str]:
    """Run a git command in the worktree, returning (exit_code, stdout, stderr)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(worktree), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git not found"
    except subprocess.TimeoutExpired:
        return -2, "", "git timed out"


def _git_rev_parse(worktree: Path, ref: str) -> Optional[str]:
    """Return the full SHA for `ref`, or None."""
    code, out, _ = _git(worktree, "rev-parse", "--verify", ref)
    return out if code == 0 else None


def _git_branch(worktree: Path) -> Optional[str]:
    """Return the current branch name, or None (detached HEAD / not a repo)."""
    code, out, _ = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
    return out if code == 0 and out != "HEAD" else None


def _git_is_clean(worktree: Path) -> Optional[bool]:
    """Return True if the worktree is clean, False if dirty, None if not a repo."""
    code, out, _ = _git(worktree, "status", "--porcelain")
    if code != 0:
        return None
    return out == ""


def _git_merge_base(worktree: Path, ref_a: str, ref_b: str) -> Optional[str]:
    """Return the merge-base SHA, or None."""
    code, out, _ = _git(worktree, "merge-base", ref_a, ref_b)
    return out if code == 0 else None


def _git_diff_stat(worktree: Path, base_ref: str, head_ref: str) -> Optional[str]:
    """Return `git diff --stat` between two refs, or None."""
    code, out, _ = _git(worktree, "diff", "--stat", f"{base_ref}...{head_ref}")
    return out if code == 0 else None


def _git_log_canonical_contains(
    worktree: Path, commit: str, canonical: str
) -> bool:
    """Return True if `commit` is an ancestor of `canonical`."""
    # Use merge-base --is-ancestor (most reliable method)
    code, _, _ = _git(
        worktree, "merge-base", "--is-ancestor", commit, canonical,
    )
    return code == 0


def _find_canonical_branch(worktree: Path) -> Optional[str]:
    """Locate a tracking remote branch that is main/master."""
    # Try remote tracking branches first
    for remote_branch in ("origin/main", "origin/master"):
        if _git_rev_parse(worktree, remote_branch):
            return remote_branch
    # Fall back to local branches
    for local in ("main", "master"):
        if _git_rev_parse(worktree, local):
            return local
    return None


def _check_implemented(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Task branch exists and diff is non-empty."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    branch = _git_branch(worktree)
    if branch is None:
        return FingerprintResult.FALSE, ("detached HEAD — no task branch",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )

    if branch in CANONICAL_BRANCHES:
        return FingerprintResult.FALSE, (
            f"on canonical branch '{branch}' — no task branch",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
        )

    # Find a canonical ancestor to diff against
    canonical = _find_canonical_branch(worktree)
    if canonical is None:
        return FingerprintResult.FALSE, (
            "cannot find canonical branch to diff against",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
            EvidenceReference(
                identity="canonical-not-found",
                source_class="git",
                source_generation="unknown",
            ),
        )

    base = _git_merge_base(worktree, canonical, "HEAD")
    if base is None:
        return FingerprintResult.FALSE, (
            f"no common ancestor with {canonical}",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
            EvidenceReference(
                identity=canonical,
                source_class="git",
                source_generation="canonical",
            ),
        )

    diff = _git_diff_stat(worktree, canonical, "HEAD")
    if diff:
        return FingerprintResult.TRUE, (
            f"branch '{branch}' has changes vs {canonical}",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
                freshness_identity=base,
                supersession_identity=canonical,
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"branch '{branch}' has no changes vs {canonical}",
        ), (
            EvidenceReference(
                identity=head,
                source_class="git",
                source_generation=f"HEAD@{branch}",
            ),
        )


def _check_tested(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Pytest passes."""
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", "-q", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(worktree),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        exit_code = proc.returncode
    except FileNotFoundError:
        return FingerprintResult.UNKNOWN, ("pytest not found",), ()
    except subprocess.TimeoutExpired:
        return FingerprintResult.UNKNOWN, ("pytest timed out",), ()

    identity = hashlib.sha256(
        f"pytest-{worktree}-{exit_code}".encode()
    ).hexdigest()[:16]

    if exit_code == 0:
        return FingerprintResult.TRUE, ("pytest passed",), (
            EvidenceReference(
                identity=identity,
                source_class="pytest",
                source_generation=str(exit_code),
            ),
        )
    elif exit_code == 5:  # no tests collected
        return FingerprintResult.UNKNOWN, ("pytest: no tests collected",), (
            EvidenceReference(
                identity=identity,
                source_class="pytest",
                source_generation=str(exit_code),
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"pytest failed (exit {exit_code})",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="pytest",
                source_generation=str(exit_code),
            ),
        )


def _check_linted(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Ruff passes."""
    try:
        proc = subprocess.run(
            ["python", "-m", "ruff", "check", "."],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(worktree),
        )
        exit_code = proc.returncode
    except FileNotFoundError:
        return FingerprintResult.UNKNOWN, ("ruff not found",), ()
    except subprocess.TimeoutExpired:
        return FingerprintResult.UNKNOWN, ("ruff timed out",), ()

    identity = hashlib.sha256(
        f"ruff-{worktree}-{exit_code}".encode()
    ).hexdigest()[:16]

    if exit_code == 0:
        return FingerprintResult.TRUE, ("ruff passed",), (
            EvidenceReference(
                identity=identity,
                source_class="ruff",
                source_generation=str(exit_code),
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"ruff found issues (exit {exit_code})",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="ruff",
                source_generation=str(exit_code),
            ),
        )


def _check_receipt(
    worktree: Path, condition_id: str
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Check for a receipt file containing PASS status."""
    receipt_rel = _RECEIPT_PATHS.get(condition_id)
    if receipt_rel is None:
        return (
            FingerprintResult.NOT_APPLICABLE,
            (f"no receipt path configured for {condition_id}",),
            (),
        )

    receipt_path = worktree / receipt_rel
    if not receipt_path.is_file():
        return FingerprintResult.FALSE, (
            f"receipt file '{receipt_rel}' not found",
        ), ()

    try:
        content = receipt_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as exc:
        return FingerprintResult.FALSE, (
            f"receipt file unreadable: {exc}",
        ), ()

    status = data.get("status", "").upper()
    identity = hashlib.sha256(content.encode()).hexdigest()[:16]

    if status == "PASS":
        return FingerprintResult.TRUE, (
            f"{condition_id} receipt: PASS",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="receipt",
                source_generation=receipt_rel,
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"{condition_id} receipt: {status} (expected PASS)",
        ), (
            EvidenceReference(
                identity=identity,
                source_class="receipt",
                source_generation=receipt_rel,
            ),
        )


def _check_admitted(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Current commit is on the canonical branch."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    canonical = _find_canonical_branch(worktree)
    if canonical is None:
        return FingerprintResult.UNKNOWN, ("no canonical branch found",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )

    admitted = _git_log_canonical_contains(worktree, head, canonical)
    if admitted:
        return FingerprintResult.TRUE, (
            f"commit {head[:8]} is on {canonical}",
        ), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
            EvidenceReference(
                identity=canonical,
                source_class="git",
                source_generation="canonical",
            ),
        )
    else:
        return FingerprintResult.FALSE, (
            f"commit {head[:8]} not on {canonical}",
        ), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
            EvidenceReference(
                identity=canonical,
                source_class="git",
                source_generation="canonical",
            ),
        )


def _check_committed(
    worktree: Path,
) -> tuple[FingerprintResult, tuple[str, ...], tuple[EvidenceReference, ...]]:
    """Working tree is clean (nothing staged or unstaged)."""
    head = _git_rev_parse(worktree, "HEAD")
    if head is None:
        return FingerprintResult.UNKNOWN, ("not a git repository",), ()

    clean = _git_is_clean(worktree)
    if clean is None:
        return FingerprintResult.UNKNOWN, (
            "cannot determine clean status",
        ), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )
    elif clean:
        return FingerprintResult.TRUE, ("working tree clean",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )
    else:
        return FingerprintResult.FALSE, ("working tree dirty",), (
            EvidenceReference(
                identity=head, source_class="git", source_generation="HEAD"
            ),
        )


# ---------------------------------------------------------------------------
# Condition dispatch table
# ---------------------------------------------------------------------------

_CHECKERS: dict[str, callable] = {
    "implemented": _check_implemented,
    "tested": _check_tested,
    "linted": _check_linted,
    "reviewed": lambda wp: _check_receipt(wp, "reviewed"),
    "controller_verified": lambda wp: _check_receipt(wp, "controller_verified"),
    "admitted": _check_admitted,
    "committed": _check_committed,
    "deployed": lambda wp: _check_receipt(wp, "deployed"),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_coding_snapshot(
    worktree_path: str | Path,
    goal_profile: GoalProfile,
) -> ObjectSnapshot:
    """Build a read-only ObjectSnapshot by checking coding conditions in a
    git worktree.

    For each condition in ``goal_profile.required``, the corresponding
    checker runs (git inspection, pytest, ruff, or receipt-file lookup) and
    produces an EvidenceAssertion. No filesystem writes occur.

    Returns an ObjectSnapshot whose ``object_id`` is the resolved absolute
    path of the worktree and whose ``assertions`` hold one
    EvidenceAssertion per required condition (plus any conditions that
    could not be checked — those produce FingerprintResult.UNKNOWN).
    """
    worktree = Path(worktree_path).resolve()

    object_id = str(worktree)

    # Content-addressed generation: hash of git HEAD + branch + diff content
    head = _git_rev_parse(worktree, "HEAD") or "no-head"
    branch = _git_branch(worktree) or "detached"
    canonical = _find_canonical_branch(worktree)
    if canonical:
        diff_content = _git_diff_stat(worktree, canonical, "HEAD") or ""
    else:
        diff_content = ""
    gen_input = f"{head}|{branch}|{diff_content}".encode()
    generation = hashlib.sha256(gen_input).hexdigest()[:16]

    assertions: list[EvidenceAssertion] = []
    for condition_id in sorted(goal_profile.required):
        checker = _CHECKERS.get(condition_id)
        if checker is None:
            assertions.append(
                EvidenceAssertion(
                    condition_id=condition_id,
                    result=FingerprintResult.UNKNOWN,
                    reasons=(
                        f"no checker registered for '{condition_id}'",
                    ),
                )
            )
            continue

        result, reasons, evidence = checker(worktree)
        assertions.append(
            EvidenceAssertion(
                condition_id=condition_id,
                result=result,
                reasons=reasons,
                evidence=evidence,
            )
        )

    return ObjectSnapshot(
        object_id=object_id,
        generation=generation,
        assertions=tuple(assertions),
    )
