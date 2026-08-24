"""Foreman: poll open todos, evaluate each, dispatch one eligible treatment per
generation. Connects the PP-WORKFLOW-001 spine to the live TGW todo tracker.

Pure read-then-act cycle — one call to ``tick()`` polls the tracker, builds
snapshots, evaluates, and dispatches at most one treatment. Calling ``tick()``
again is safe: same generation → same graph_id → skipped (idempotent).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from tgw.workers.coding import validated_coding_worktree
from tgw.development.coding_snapshot import build_coding_snapshot
from tgw.development.plan_binding import MalformedPlanBindingError, parse_plan_binding
from tgw.development.provider_dispatch import ProviderDispatchError, resolve_implementation_adapter
from tgw.workflow_kernel.contracts import (
    GoalProfile,
    RuntimeWorkGraph,
    TreatmentContract,
    TreatmentDisposition,
)
from tgw.workflow_kernel.evaluator import evaluate
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.workflow_kernel.scheduler import DispatchResult, dispatch_treatment
from tgw.development.treatments import CODING_TREATMENTS

log = logging.getLogger(__name__)

EVALUATOR_VERSION = "foreman/v1"

# Active job states — if a job for the same graph_id is in any of these
# states, the todo should be skipped (already dispatched).
_ACTIVE_JOB_STATES = frozenset({"queued", "leased", "running", "retry_wait"})


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForemanConfig:
    """Configuration for one foreman tick.

    Attributes:
        goal_profile: Goal profile used for evaluation.
        treatments: Treatment contracts to evaluate against.
        evaluator_version: Version string baked into every graph.
    """

    goal_profile: GoalProfile = CODING_READY_FOR_IMPLEMENTATION
    treatments: tuple[TreatmentContract, ...] = CODING_TREATMENTS
    evaluator_version: str = EVALUATOR_VERSION
    # The worker and foreman must apply the same canonical-root proof before
    # executing project checks or dispatching a job.
    coding_config: dict[str, Any] = field(default_factory=dict)
    provider_registry_path: str | None = None
    provider_adapters: dict[str, str] | None = None
    # A fixture may prove its clean allocated worktree is genuinely unimplemented
    # against its bound source commit.  Ordinary Todos never consume this field.
    fixture_implementation_baseline_commit: str | None = None


# ---------------------------------------------------------------------------
# Todo record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TodoRecord:
    """A single open todo from the tracker.

    Attributes:
        todo_id: Unique id from the todo table.
        agent: Agent label (``claude``, ``admin``, etc.).
        priority: Priority value.
        body: Text body of the todo.
        worktree: Absolute path to the coding worktree, or ``""``.
    """

    todo_id: int
    agent: str
    priority: int | None
    body: str
    worktree: str = ""
    plan_binding: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Tick result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickResult:
    """Aggregate result of one foreman tick.

    Attributes:
        dispatched: Number of treatments dispatched.
        skipped_waiting: Todos with no eligible treatment (all waiting).
        skipped_conflict: Todos whose eligible treatments had ownership
            conflicts.
        skipped_active: Todos that already have an active job for the
            same graph_id.
        skipped_no_worktree: Todos without a worktree path.
        errors: Count of errors encountered during processing.
    """

    dispatched: int = 0
    skipped_waiting: int = 0
    skipped_conflict: int = 0
    skipped_active: int = 0
    skipped_no_worktree: int = 0
    skipped_terminal: int = 0
    refused_plan_binding: int = 0
    errors: int = 0


@dataclass(frozen=True)
class _EligibleTreatment:
    """An evaluated treatment plus the todo metadata used for admission."""

    todo: TodoRecord
    graph: RuntimeWorkGraph
    disposition: TreatmentDisposition


# ---------------------------------------------------------------------------
# Active-job check
# ---------------------------------------------------------------------------


def _has_active_job(
    graph_id: str,
    check_active_fn: Callable[[str], bool] | None = None,
) -> bool:
    """Return ``True`` if a job with ``graph_id`` is currently active.

    *check_active_fn* is a callable ``(graph_id: str) -> bool`` for testing.
    When ``None``, uses ``tgw.queue.state_machine`` (requires a database).
    """
    if check_active_fn is not None:
        return check_active_fn(graph_id)

    # Lazily import to avoid DB dependency in pure tests.
    from tgw.queue.state_machine import _conn

    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                    SELECT 1 FROM queue_jobs
                     WHERE dedupe_key = %s
                       AND state = ANY(%s::queue_job_state[])
                     LIMIT 1
                    """,
                (graph_id, list(_ACTIVE_JOB_STATES)),
            )
            return cur.fetchone() is not None
    except Exception:
        log.warning(
            "failed to check active jobs for graph_id=%s — treating as inactive",
            graph_id,
            exc_info=True,
        )
        return False


def _has_terminal_job(
    graph_id: str,
    check_terminal_fn: Callable[[str], bool] | None = None,
) -> bool:
    """Return whether this unchanged graph already reached a durable terminal state."""
    if check_terminal_fn is not None:
        return check_terminal_fn(graph_id)
    from tgw.queue.state_machine import _conn

    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                    SELECT 1 FROM queue_jobs
                     WHERE dedupe_key = %s
                       AND state = ANY(%s::queue_job_state[])
                     LIMIT 1
                """,
                (graph_id, ["succeeded", "failed", "dead_letter"]),
            )
            return cur.fetchone() is not None
    except Exception:
        log.warning("failed to check terminal jobs for graph_id=%s", graph_id, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Todo fetcher
# ---------------------------------------------------------------------------


def _default_fetch_open_todos() -> list[TodoRecord]:
    """Fetch open todos from the TGW todo tracker.

    Uses the MCP ``tgw_get_todo`` API over the database.  Returns a list of
    ``TodoRecord`` instances.
    """

    from tgw.queue.state_machine import _conn

    todos: list[TodoRecord] = []
    try:
        with _conn() as con, con.cursor() as cur:
            cur.execute(
                """
                    SELECT id, agent, priority, body, status_note
                      FROM todo_items
                     WHERE done_at IS NULL
                     ORDER BY priority, id
                    """
            )
            for row in cur.fetchall():
                todo_id, agent, priority, body, status_note = row
                worktree = _extract_worktree(status_note or "") or _extract_worktree(body)
                plan_binding = parse_plan_binding(status_note, todo_id=todo_id)
                todos.append(
                    TodoRecord(
                        todo_id=todo_id,
                        agent=agent or "",
                        priority=priority,
                        body=body or "",
                        worktree=worktree,
                        plan_binding=plan_binding,
                    )
                )
    except MalformedPlanBindingError:
        raise
    except Exception:
        log.exception("failed to fetch open todos from database")
    return todos


def _extract_worktree(body: str) -> str:
    """Extract a worktree path from a todo body string.

    Looks for patterns like ``worktree: /path/to/worktree`` or a prominent
    absolute path starting with ``/``.
    """
    import re

    # Pattern: "worktree:" or "worktree =" followed by a path
    match = re.search(
        r"worktree\s*[:=]\s*(/\S+)", body, re.IGNORECASE
    )
    if match:
        return match.group(1)

    # Fallback: look for a path that looks like a git worktree
    match = re.search(r"(/[^\s,]+/(?:worktrees|checkouts)/[^\s,]+)", body)
    if match:
        return match.group(1)

    return ""


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------


def tick(
    config: ForemanConfig | None = None,
    *,
    limit: int | None = None,
    todo_ids: set[int] | None = None,
    fetch_todos: Callable[[], list[TodoRecord]] | None = None,
    check_active_fn: Callable[[str], bool] | None = None,
    check_terminal_fn: Callable[[str], bool] | None = None,
    enqueue_fn: Any = None,
) -> TickResult:
    """Run one foreman tick.

    Polls open todos, builds a ``CodingTaskSnapshot`` for each with a
    worktree path, evaluates every one against ``CODING_READY_FOR_IMPLEMENTATION``,
    and dispatches **exactly one** eligible treatment.

    Todos that already have an active job (queued/leased/running) with the
    same ``graph_id`` are skipped — this makes ``tick()`` idempotent across
    repeated calls for the same generation.

    Args:
        config: Foreman configuration. Defaults to ``CODING_READY_FOR_IMPLEMENTATION``
            with ``CODING_TREATMENTS``.
        limit: Maximum number of todos to process (for testing / rate-limiting).
        fetch_todos: Callable returning a list of ``TodoRecord``. Override for
            testing to avoid a real database call.
        check_active_fn: Callable ``(graph_id: str) -> bool``. Override for
            testing.
        enqueue_fn: Callable with the same signature as
            ``state_machine.enqueue_job``. Override for testing.

    Returns:
        ``TickResult`` summarising dispatched, skipped, and error counts.
    """
    cfg = config or ForemanConfig()
    fetcher = fetch_todos or _default_fetch_open_todos
    result = TickResult()

    try:
        todos = fetcher()
    except MalformedPlanBindingError:
        log.error("refusing tick due to malformed Plan-bound Todo metadata", exc_info=True)
        return TickResult(refused_plan_binding=1, errors=1)
    except Exception:
        log.exception("todo fetch failed")
        return TickResult(errors=1)

    eligible: list[_EligibleTreatment] = []
    processed = 0
    for todo in todos:
        if todo_ids is not None and todo.todo_id not in todo_ids:
            continue
        if limit is not None and processed >= limit:
            break

        processed += 1
        if not todo.worktree:
            result = replace(result, skipped_no_worktree=result.skipped_no_worktree + 1)
            continue

        try:
            # This proof must precede all snapshot work: snapshot checks run
            # pytest/ruff in the candidate directory.  Never trust the
            # free-text todo path.
            worktree = validated_coding_worktree(
                todo.worktree, todo.worktree, cfg.coding_config,
            )
            fixture_baseline = None
            if cfg.fixture_implementation_baseline_commit is not None:
                binding = todo.plan_binding
                if not isinstance(binding, dict) or not binding.get("fixture_run_id"):
                    raise ProviderDispatchError("fixture implementation baseline requires a fixture-bound Todo")
                if binding.get("source_commit") != cfg.fixture_implementation_baseline_commit:
                    raise ProviderDispatchError("fixture implementation baseline disagrees with Plan binding")
                fixture_baseline = cfg.fixture_implementation_baseline_commit
            snapshot = build_coding_snapshot(
                worktree, cfg.goal_profile, cfg.treatments,
                implementation_baseline_commit=fixture_baseline,
            )
        except Exception:
            log.exception(
                "failed canonical worktree preflight or snapshot for todo %d at %s",
                todo.todo_id,
                todo.worktree,
            )
            result = replace(result, errors=result.errors + 1)
            continue

        canonical_todo = replace(todo, worktree=str(worktree))

        try:
            graph = evaluate(
                snapshot=snapshot,
                goal=cfg.goal_profile,
                treatments=cfg.treatments,
                evaluator_version=cfg.evaluator_version,
            )
        except Exception:
            log.exception(
                "evaluate failed for todo %d", todo.todo_id,
            )
            result = replace(result, errors=result.errors + 1)
            continue

        # Generation-stable dedupe: skip if an active job already exists.
        if _has_active_job(graph.graph_id, check_active_fn):
            result = replace(result, skipped_active=result.skipped_active + 1)
            continue

        # An unchanged graph which already completed, failed, or dead-lettered
        # is durable terminal evidence.  A source/evidence change produces a
        # new graph id and is therefore eligible for a fresh attempt.
        if _has_terminal_job(graph.graph_id, check_terminal_fn):
            result = replace(result, skipped_terminal=result.skipped_terminal + 1)
            continue

        # If no eligible treatments, record waiting reason.
        if not graph.eligible_treatments:
            if graph.waiting_treatments:
                log.info(
                    "todo %d (%s): all %d treatments waiting: %s",
                    todo.todo_id,
                    graph.object_id,
                    len(graph.waiting_treatments),
                    [
                        (w.treatment_id, w.reasons)
                        for w in graph.waiting_treatments
                    ],
                )
            result = replace(result, skipped_waiting=result.skipped_waiting + 1)
            continue

        # Ownership conflicts → skip.
        if graph.ownership_conflicts:
            log.info(
                "todo %d (%s): ownership conflicts: %s",
                todo.todo_id,
                graph.object_id,
                graph.ownership_conflicts,
            )
            result = replace(result, skipped_conflict=result.skipped_conflict + 1)
            continue

        for disposition in graph.eligible_treatments:
            eligible.append(
                _EligibleTreatment(
                    todo=canonical_todo,
                    graph=graph,
                    disposition=disposition,
                )
            )

    if not eligible:
        return result

    # An approved Plan/PP/Todo is the authorization for this local coding
    # sequence.  Review and verification receipts select the next treatment;
    # they do not introduce an intermediate operator-admission wait.
    runnable = sorted(
        eligible,
        key=lambda x: (
            x.todo.priority if x.todo.priority is not None else 999,
            x.todo.todo_id,
            x.disposition.treatment_id,
        ),
    )
    # One *enqueued* job per tick is the rate limit, not one attempted
    # candidate.  Idempotent outcomes and failures must not starve later work.
    for chosen in runnable:
        try:
            payload_extra = {
                "todo_id": chosen.todo.todo_id,
                "todo_priority": chosen.todo.priority,
                "todo_agent": chosen.todo.agent,
                "worktree": chosen.todo.worktree,
                **({"plan_binding": chosen.todo.plan_binding} if chosen.todo.plan_binding is not None else {}),
            }
            disposition = chosen.disposition
            if disposition.treatment_id == "codex-implement":
                adapter = resolve_implementation_adapter(
                    cfg.coding_config,
                    registry_path=cfg.provider_registry_path,
                    adapters=cfg.provider_adapters,
                )
                # Keep the evaluator treatment as the execution adapter while
                # recording the independent neutral role/provider selection.
                if adapter.treatment_id != disposition.treatment_id:
                    raise ProviderDispatchError("implementation adapter disagrees with evaluator treatment")
                payload_extra.update({
                    "coding_role": adapter.role,
                    "selected_provider": adapter.selected_provider,
                    "adapter_treatment_id": adapter.treatment_id,
                    "adapter_queue_name": adapter.queue_name,
                })
            dispatch_result: DispatchResult = dispatch_treatment(
                disposition=disposition,
                entity_id=chosen.graph.object_id,
                entity_type="coding_task",
                graph=chosen.graph,
                payload_extra=payload_extra,
                enqueue_fn=enqueue_fn,
                coding_config=cfg.coding_config,
            )
        except Exception:
            log.exception(
                "dispatch failed for todo %d treatment %s",
                chosen.todo.todo_id,
                chosen.disposition.treatment_id,
            )
            result = replace(result, errors=result.errors + 1)
            continue
        if dispatch_result.enqueued:
            log.info(
                "dispatched %s for todo %d → job %s; evaluator graph %s "
                "recorded in queue payload",
                chosen.disposition.treatment_id,
                chosen.todo.todo_id,
                dispatch_result.job_id,
                chosen.graph.graph_id,
            )
            return replace(result, dispatched=result.dispatched + 1)
        if dispatch_result.outcome == "already_dispatched":
            result = replace(result, skipped_active=result.skipped_active + 1)
        else:
            result = replace(result, errors=result.errors + 1)
    return result
