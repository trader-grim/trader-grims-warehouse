"""Git discipline for the continual harness — Todo 1916 leaf 11.1
(LEAF-11-1 work unit L11.1.LEDGER-AND-GIT-DISCIPLINE).

The ledger holds the process; git holds only accepted results. However many
remediation rounds a task took, it lands as exactly one commit on the base
branch — a genuine ancestor, fast-forward only — and its ephemeral worktree
and branch are deleted. The full attempt / remediation / rejected-candidate /
review-finding history stays in the ledger (tgw.development.harness_ledger),
never as git commits or refs.

Human / ad-hoc work is the same shape at lower volume: one clean commit per
change, no permanent per-task ``coding/*`` branches, no "close implementation
candidate" commits.

This module does git only. It never imports the ledger. The orchestrator
records the landing in the ledger and advances the task cursor after
:func:`land_accepted_task` returns.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from tgw.protected_git import protected_git_command, protected_git_environment

_PLAIN_GIT = "/usr/bin/git"

# Paths the harness itself writes into the ephemeral worktree — the job file and
# the session receipts. They must never reach the squashed commit.
_HARNESS_SCRATCH: tuple[str, ...] = (
    ".tgw-harness",
    "implementation-receipt.json",
    "review-receipt.json",
)

# A ref publisher performs the guarded fast-forward advance of the base branch.
# (ref, expected_old_oid, new_oid) -> None; raises on refusal or a lost race.
RefPublisher = Callable[[str, str, str], None]


class HarnessGitError(RuntimeError):
    """A git-discipline operation cannot proceed safely."""


class NotFastForward(HarnessGitError):
    """The base branch moved under the worktree — the land is not a genuine
    fast-forward and must not be forced."""


class NothingToLand(HarnessGitError):
    """The worktree's net tree equals the base tree — no accepted change."""


def _git(
    repository: Path,
    *args: str,
    hooks: bool = False,
    extra_env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> str:
    """Run one git command in ``repository``.

    hooks=False (default) uses the deterministic no-hook, no-config invocation
    for object plumbing. hooks=True runs plain git so the
    ``reference-transaction`` guard (tgw.main_ref_guard) fires — used only for
    the base-branch advance.
    """
    if hooks:
        # Plain git so the reference-transaction guard (tgw.main_ref_guard)
        # evaluates the real caller identity. Keep the caller's environment so
        # the hook runs normally; only pin safe.directory and drop lock waits.
        command = [
            _PLAIN_GIT, "-C", str(repository),
            "-c", f"safe.directory={repository}", *args,
        ]
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    else:
        command = protected_git_command(repository, *args)
        env = dict(protected_git_environment())
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        command,
        cwd=repository,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        timeout=120,
    )
    if result.returncode:
        raise HarnessGitError(
            f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()[-500:]}"
        )
    return result.stdout.strip()


def _rev(repository: Path, spec: str) -> str:
    return _git(repository, "rev-parse", "--verify", spec)


def _worktree_branch(repository: Path, worktree: Path) -> str | None:
    """The branch name checked out in ``worktree``, or None if detached."""
    target = str(Path(worktree).resolve())
    current: str | None = None
    branch: str | None = None
    for line in _git(repository, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            current = str(Path(line[len("worktree "):]).resolve())
            branch = None
        elif line.startswith("branch ") and current == target:
            branch = line[len("branch "):].strip()
    return branch


def _default_ref_publisher(repository: Path) -> RefPublisher:
    def publish(ref: str, old_oid: str, new_oid: str) -> None:
        # hooks=True so main_ref_guard evaluates the caller identity. In the
        # continual harness this process is the sanctioned publisher (db); a
        # phase-0 supervised session passes its own sudo-wrapped publisher.
        _git(repository, "update-ref", ref, new_oid, old_oid, hooks=True)

    return publish


def land_accepted_task(
    task_id: str,
    *,
    repository: Path | str,
    worktree: Path | str,
    message: str,
    base_ref: str = "refs/heads/main",
    author_name: str = "Claude Code",
    author_email: str = "claude@tgw-lib",
    committer_name: str | None = None,
    committer_email: str | None = None,
    trailer_lines: tuple[str, ...] = (),
    ref_publisher: RefPublisher | None = None,
) -> dict[str, Any]:
    """Squash the worktree's net change from ``base_ref`` into one commit,
    fast-forward ``base_ref`` to it, and delete the worktree and its branch.

    Returns the landing record: base commit, the one new commit, its tree, and
    the removed worktree/branch. Raises :class:`NotFastForward` if the base
    moved (never forced), :class:`NothingToLand` if there is no net change.

    ``message`` is the real commit message for the accepted task — not a
    per-round or "candidate" message. ``task_id`` is accepted for symmetry with
    the ledger and for error context; this function does not write the ledger.
    """
    repository = Path(repository).resolve(strict=True)
    worktree = Path(worktree).resolve(strict=True)

    base_oid = _rev(repository, base_ref)
    base_tree = _rev(repository, f"{base_oid}^{{tree}}")

    # Drop the harness's own scratch (job file, session receipts) before it can
    # be staged — it is written into the worktree root and would otherwise land
    # in the squashed tree.
    for rel in _HARNESS_SCRATCH:
        target = worktree / rel
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink()
        _git(worktree, "rm", "-r", "--cached", "--ignore-unmatch", "-q", "--", rel)

    # Fold any uncommitted work in the ephemeral worktree into its history so
    # the net tree is complete. The worktree is about to be deleted, so a
    # throwaway commit on its own branch is harmless.
    if _git(worktree, "status", "--porcelain"):
        _git(worktree, "add", "-A")
        _git(
            worktree, "commit", "--no-verify", "-m", f"wip: {task_id}",
            extra_env=_identity_env(author_name, author_email, committer_name, committer_email),
        )
    wt_head = _rev(worktree, "HEAD")

    merge_base = _git(repository, "merge-base", base_oid, wt_head)
    if merge_base != base_oid:
        raise NotFastForward(
            f"{base_ref} ({base_oid[:12]}) is not an ancestor of the worktree "
            f"({wt_head[:12]}); merge-base is {merge_base[:12]}"
        )

    net_tree = _rev(repository, f"{wt_head}^{{tree}}")
    if net_tree == base_tree:
        raise NothingToLand(f"task {task_id} produced no net change from {base_ref}")

    full_message = message.rstrip("\n")
    if trailer_lines:
        full_message += "\n\n" + "\n".join(trailer_lines)

    squashed = _git(
        repository,
        "commit-tree", net_tree, "-p", base_oid,
        hooks=False,
        extra_env=_identity_env(author_name, author_email, committer_name, committer_email),
        input_text=full_message + "\n",
    )

    publisher = ref_publisher or _default_ref_publisher(repository)
    publisher(base_ref, base_oid, squashed)

    landed_oid = _rev(repository, base_ref)
    if landed_oid != squashed:
        raise HarnessGitError(
            f"{base_ref} is {landed_oid[:12]} after publish, expected {squashed[:12]}"
        )

    branch = _worktree_branch(repository, worktree)
    _git(repository, "worktree", "remove", "--force", str(worktree))
    if branch and branch != base_ref:
        _git(repository, "branch", "-D", branch.removeprefix("refs/heads/"))

    return {
        "task_id": task_id,
        "base_commit": base_oid,
        "commit": squashed,
        "tree": net_tree,
        "base_ref": base_ref,
        "worktree_removed": str(worktree),
        "branch_deleted": branch,
        "squashed_from": wt_head,
    }


def abandon_task(
    *,
    repository: Path | str,
    worktree: Path | str,
    base_ref: str = "refs/heads/main",
) -> dict[str, Any]:
    """Delete an ephemeral worktree and its branch without landing anything.

    The task's history is already in the ledger; git keeps no trace of a
    dropped attempt.
    """
    repository = Path(repository).resolve(strict=True)
    worktree = Path(worktree).resolve(strict=True)
    branch = _worktree_branch(repository, worktree)
    _git(repository, "worktree", "remove", "--force", str(worktree))
    if branch and branch != base_ref:
        _git(repository, "branch", "-D", branch.removeprefix("refs/heads/"))
    return {
        "worktree_removed": str(worktree),
        "branch_deleted": branch,
    }


def _identity_env(
    author_name: str,
    author_email: str,
    committer_name: str | None,
    committer_email: str | None,
) -> dict[str, str]:
    return {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": committer_name or author_name,
        "GIT_COMMITTER_EMAIL": committer_email or author_email,
    }
