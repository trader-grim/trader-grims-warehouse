"""The continual-harness orchestrator loop — Todo 1916 leaf 11.1.

Runs ON the continual harness; ``harness_ledger`` is its only durable state
(LEAF-11-1 ``harness_topology.orchestrator_runs_on_the_harness``). It takes one
task, hands each coder a FRESH disposable session bound to one worktree, and
drives the bounded implement -> test -> review loop to a mechanical
completion: tests pass and the change fast-forwards the base branch. On accept
the worktree squashes to exactly one commit (``harness_git``) and is deleted.

Model sessions are fast but fallible (RLM-RESEARCH-TAIL-CONCEPTS #14):
correctness is the deterministic substrate — the ledger, the test suite,
``main_ref_guard`` — never a session's word. Independent review is diagnostic:
its blocking findings drive another round while the budget lasts; they do not
themselves hold the land.

Disposable and resumable: kill the orchestrator mid-loop and the next
invocation acquires the (now-expired) cursor lease, reads the ledger cursor,
and continues from that round.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tgw.development import harness_git, harness_ledger

DEFAULT_MAX_ROUNDS = 3
DEFAULT_LEASE_SECONDS = 900


class OrchestratorError(RuntimeError):
    """The orchestrator cannot run this task safely."""


class OrchestratorBusy(OrchestratorError):
    """Another live owner holds the task cursor."""


@dataclass(frozen=True)
class Runners:
    """The disposable-session hooks the orchestrator drives.

    Each is called fresh per round. None of them is trusted for correctness —
    the loop verifies with ``run_tests`` and the fast-forward land.

    prepare_worktree(task_id, base_commit) -> Path
        Return a git worktree branched from ``base_commit`` for this task.
        Called once; the path is remembered in the ledger cursor and reused on
        resume.
    implement(task_id, worktree, round, prior_findings) -> dict
        Do one implementation pass in ``worktree``. Return at least
        ``{"outcome": "ok" | "partial" | "failed", "summary": str}``.
    run_tests(task_id, worktree) -> dict
        Return ``{"passed": bool, "detail": str}``. This is the gate.
    review(task_id, worktree) -> dict
        Independent diagnostic review. Return
        ``{"findings": [{"message": str, "blocking": bool}, ...]}``.
    """

    prepare_worktree: Callable[[str, str], Path]
    implement: Callable[..., dict[str, Any]]
    run_tests: Callable[[str, Path], dict[str, Any]]
    review: Callable[[str, Path], dict[str, Any]]


def _rev(repository: Path, spec: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", spec],
        check=False, text=True, capture_output=True, timeout=30,
        env={"GIT_OPTIONAL_LOCKS": "0", "PATH": "/usr/bin:/bin",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"},
    )
    if result.returncode:
        raise OrchestratorError(f"cannot resolve {spec}: {result.stderr.strip()}")
    return result.stdout.strip()


def run_task(
    task_id: str,
    *,
    repository: Path | str,
    runners: Runners,
    commit_message: str,
    owner: str = "harness",
    base_ref: str = "refs/heads/main",
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ref_publisher: harness_git.RefPublisher | None = None,
    author_name: str = "Continual Harness",
    author_email: str = "harness@tgw-lib",
    trailer_lines: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Drive one task to a mechanical completion, recording every step to the
    ledger. Returns one of:

      {"outcome": "landed", "commit": ..., "rounds": N, ...}
      {"outcome": "already_satisfied", ...}       — no net change; nothing to land
      {"outcome": "blocked", "supervisor_handoff": {...}, ...} — budget exhausted
      {"outcome": "rebind_required", ...}         — base moved; worktree kept

    Raises OrchestratorBusy if another owner holds the cursor.
    """
    repository = Path(repository).resolve(strict=True)
    harness_ledger.ensure_task(task_id)
    acquired = harness_ledger.acquire_cursor(task_id, owner, lease_seconds=lease_seconds)
    if acquired is None:
        raise OrchestratorBusy(f"task {task_id} cursor is held by another owner")
    lease = acquired["lease_id"]
    cursor = dict(acquired["cursor"] or {})

    if acquired["status"] == "done":
        harness_ledger.release_cursor(task_id, owner, lease)
        return {"outcome": "already_done", "task_id": task_id,
                "commit": cursor.get("landed_commit"), "rounds": cursor.get("rounds")}

    base_commit = _rev(repository, base_ref)
    # rounds already completed (persisted at the end of each round); a crash
    # mid-round leaves this unchanged, so the next invocation redoes that round
    # on the same worktree with the same prior findings.
    completed = int(cursor.get("rounds_completed", 0))
    prior_findings: list[dict[str, Any]] = list(cursor.get("prior_findings", []))

    worktree = Path(cursor["worktree"]) if cursor.get("worktree") else None
    if worktree is None or not worktree.exists():
        worktree = Path(runners.prepare_worktree(task_id, base_commit)).resolve()
        cursor.update(worktree=str(worktree), base_commit=base_commit)
        harness_ledger.write_cursor(task_id, owner, lease, cursor=cursor, status="open")

    def _cursor(**changes: Any) -> None:
        cursor.update(changes)
        harness_ledger.write_cursor(task_id, owner, lease, cursor=cursor)

    try:
        while completed < max_rounds:
            round_no = completed + 1
            _cursor(round=round_no, stage="implement")

            impl = dict(runners.implement(
                task_id, worktree, round=round_no, prior_findings=prior_findings,
            ))
            harness_ledger.append(task_id, "attempt", {"round": round_no, "stage": "implement", **impl})
            harness_ledger.renew_cursor(task_id, owner, lease, lease_seconds=lease_seconds)

            if impl.get("outcome") == "failed":
                reason = impl.get("summary") or "implementation failed"
                harness_ledger.append(task_id, "failed_approach", {"round": round_no, "reason": reason})
                prior_findings = [{"message": reason}]
                completed = round_no
                _cursor(rounds_completed=completed, prior_findings=prior_findings, stage="remediate")
                continue

            _cursor(stage="test")
            tests = dict(runners.run_tests(task_id, worktree))
            harness_ledger.append(task_id, "attempt", {"round": round_no, "stage": "test", **tests})
            if not tests.get("passed"):
                detail = tests.get("detail") or "tests failed"
                harness_ledger.append(task_id, "failed_approach", {"round": round_no, "reason": "tests failed", "detail": detail})
                prior_findings = [{"message": f"tests failed: {detail}"}]
                completed = round_no
                _cursor(rounds_completed=completed, prior_findings=prior_findings, stage="remediate")
                continue

            _cursor(stage="review")
            review = dict(runners.review(task_id, worktree))
            findings = [dict(f) for f in review.get("findings", [])]
            for finding in findings:
                harness_ledger.append(task_id, "review_finding", finding)
            blocking = [f for f in findings if f.get("blocking")]
            if blocking:
                prior_findings = findings
                completed = round_no
                if round_no < max_rounds:
                    _cursor(rounds_completed=completed, prior_findings=findings, stage="remediate")
                    continue
                # budget exhausted with a blocking finding unresolved. Review is
                # non-gating, but "still blocking after every round" is a
                # disagreement the loop cannot settle — hand off, do not land.
                _cursor(rounds_completed=completed, prior_findings=findings, stage="handoff")
                break

            # mechanical gate: tests green, no blocking findings -> land.
            _cursor(stage="land")
            try:
                landed = harness_git.land_accepted_task(
                    task_id, repository=repository, worktree=worktree,
                    message=commit_message, base_ref=base_ref,
                    author_name=author_name, author_email=author_email,
                    trailer_lines=trailer_lines, ref_publisher=ref_publisher,
                )
            except harness_git.NothingToLand:
                harness_ledger.append(task_id, "note", {"result": "already satisfied; no net change"})
                harness_ledger.write_cursor(task_id, owner, lease, status="done")
                harness_git.abandon_task(repository=repository, worktree=worktree, base_ref=base_ref)
                return {"outcome": "already_satisfied", "task_id": task_id, "rounds": round_no}
            except harness_git.NotFastForward as exc:
                harness_ledger.append(task_id, "failed_approach", {"round": round_no, "reason": "base moved; rebind required", "detail": str(exc)})
                harness_ledger.write_cursor(task_id, owner, lease, cursor={**cursor, "stage": "rebind"}, status="blocked")
                return {"outcome": "rebind_required", "task_id": task_id, "rounds": round_no, "detail": str(exc)}

            harness_ledger.append(task_id, "next_action", {"landed_commit": landed["commit"], "rounds": round_no})
            harness_ledger.write_cursor(
                task_id, owner, lease,
                cursor={"landed_commit": landed["commit"], "rounds": round_no},
                status="done",
            )
            return {"outcome": "landed", "task_id": task_id, "rounds": round_no, **landed}

        handoff = {
            "rounds": completed,
            "worktree": str(worktree),
            "blocking_findings": prior_findings,
            "next_operator_action": (
                f"unresolved after {completed} rounds; inspect worktree {worktree} "
                f"and the ledger history for task {task_id}"
            ),
        }
        harness_ledger.append(task_id, "next_action", {"supervisor_handoff": handoff})
        harness_ledger.write_cursor(task_id, owner, lease, cursor=cursor, status="blocked")
        return {"outcome": "blocked", "task_id": task_id, "rounds": completed, "supervisor_handoff": handoff}
    finally:
        harness_ledger.release_cursor(task_id, owner, lease)
