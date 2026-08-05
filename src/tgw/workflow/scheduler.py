"""Phase 2 scheduler — dispatches exactly one eligible treatment per
generation-bound work graph.

Pure-function core (dispatch_treatment) + thin convenience wrapper
(build_and_dispatch) that chains coding snapshot → evaluate → dispatch
in one call.
"""

from __future__ import annotations

import logging
from typing import Optional

from .coding_snapshot import build_coding_snapshot
from .contracts import GoalProfile, ObjectSnapshot, RuntimeWorkGraph, TreatmentContract, TreatmentDisposition
from .evaluator import evaluate

log = logging.getLogger(__name__)

EVALUATOR_VERSION = "workflow-scheduler/v2"


def dispatch_treatment(
    snapshot: ObjectSnapshot,
    graph: RuntimeWorkGraph,
    treatments: tuple[TreatmentContract, ...],
    *,
    entity_type: str = "item",
    entity_id: str = "",
) -> Optional[str]:
    """If exactly one eligible treatment exists, enqueue it with
    *graph.graph_id* as the dedupe key (generation bound).

    Returns the enqueued ``job_id`` (UUID string), or ``None`` when:

    * No eligible treatments exist.
    * Ownership conflicts are present.
    * Reconciliation gates are open (ambiguous external effects).
    * A job with the same ``graph_id`` is already enqueued
      (UniqueViolation → idempotent re-dispatch).

    The caller is responsible for running :func:`evaluate` before
    calling this function — the scheduler does **not** re-evaluate;
    it reads the pre-computed :class:`RuntimeWorkGraph`.
    """
    if not graph.eligible_treatments:
        log.debug(
            "dispatch_treatment: no eligible treatments for graph_id=%s",
            graph.graph_id,
        )
        return None

    if graph.ownership_conflicts:
        conflicts_repr = [
            f"{left} ↔ {right}: {', '.join(overlap)}"
            for left, right, overlap in graph.ownership_conflicts
        ]
        log.info(
            "dispatch_treatment: ownership conflicts present for graph_id=%s "
            "— %s",
            graph.graph_id,
            "; ".join(conflicts_repr),
        )
        return None

    if graph.reconciliation_gates:
        log.info(
            "dispatch_treatment: reconciliation gates open for graph_id=%s "
            "— %s",
            graph.graph_id,
            ", ".join(graph.reconciliation_gates),
        )
        return None

    # Eligible treatments are already sorted by (identity, version) by
    # evaluate() — the first entry is the deterministic pick.
    chosen: TreatmentDisposition = graph.eligible_treatments[0]

    if len(graph.eligible_treatments) > 1:
        remaining = [
            f"{d.treatment_id}@{d.treatment_version}"
            for d in graph.eligible_treatments[1:]
        ]
        log.info(
            "dispatch_treatment: dispatching %s@%s (first eligible); "
            "%d additional eligible treatment(s) available but not "
            "dispatched: %s",
            chosen.treatment_id,
            chosen.treatment_version,
            len(remaining),
            ", ".join(remaining),
        )

    # Resolve the treatment contract for handler_family.
    treatment = _lookup_treatment(
        chosen.treatment_id, chosen.treatment_version, treatments,
    )
    if treatment is None:
        log.error(
            "dispatch_treatment: treatment %s@%s is in eligible list "
            "but not in the supplied treatments tuple",
            chosen.treatment_id,
            chosen.treatment_version,
        )
        return None

    payload: dict[str, object] = {
        "graph_id": graph.graph_id,
        "object_id": snapshot.object_id,
        "object_generation": snapshot.generation,
        "goal_profile_id": graph.goal_profile_id,
        "goal_profile_version": graph.goal_profile_version,
        "treatment_id": chosen.treatment_id,
        "treatment_version": chosen.treatment_version,
        "evaluator_version": graph.evaluator_version,
    }

    from tgw.queue import state_machine

    try:
        job_id = state_machine.enqueue_job(
            queue_name=treatment.identity,
            handler_family=treatment.identity,
            payload=payload,
            entity_type=entity_type,
            entity_id=entity_id,
            dedupe_key=graph.graph_id,
        )
        log.info(
            "dispatch_treatment: enqueued job_id=%s queue=%s "
            "treatment=%s@%s graph_id=%s",
            job_id,
            treatment.identity,
            chosen.treatment_id,
            chosen.treatment_version,
            graph.graph_id,
        )
        return job_id
    except Exception as exc:
        # UniqueViolation (or any duplicate-key collision) means this
        # exact graph_id was already enqueued — idempotent re-dispatch.
        if _is_duplicate_key(exc):
            log.info(
                "dispatch_treatment: graph_id=%s already enqueued "
                "(dedupe collision) — idempotent, returning None",
                graph.graph_id,
            )
            return None
        log.error(
            "dispatch_treatment: enqueue failed for graph_id=%s: %s",
            graph.graph_id,
            exc,
        )
        raise


def build_and_dispatch(
    worktree_path: str,
    goal_profile: "GoalProfile",
    treatments: tuple[TreatmentContract, ...],
    *,
    entity_type: str = "item",
    entity_id: str = "",
    evaluator_version: str = EVALUATOR_VERSION,
) -> Optional[str]:
    """Convenience wrapper: build a coding snapshot, evaluate, then dispatch.

    Calls :func:`build_coding_snapshot` → :func:`evaluate` →
    :func:`dispatch_treatment` in sequence and returns the job_id
    (or ``None`` if nothing was dispatched).
    """
    snapshot = build_coding_snapshot(worktree_path, goal_profile)
    graph = evaluate(
        snapshot=snapshot,
        goal=goal_profile,
        treatments=treatments,
        evaluator_version=evaluator_version,
    )
    return dispatch_treatment(
        snapshot=snapshot,
        graph=graph,
        treatments=treatments,
        entity_type=entity_type,
        entity_id=entity_id,
    )


# ── Internal helpers ───────────────────────────────────────────────────────


def _lookup_treatment(
    treatment_id: str,
    treatment_version: str,
    treatments: tuple[TreatmentContract, ...],
) -> Optional[TreatmentContract]:
    """Find a :class:`TreatmentContract` by (identity, version)."""
    for t in treatments:
        if t.identity == treatment_id and t.version == treatment_version:
            return t
    return None


def _is_duplicate_key(exc: Exception) -> bool:
    """Return ``True`` when *exc* is a duplicate-key / uniqueness violation.

    Checks for ``psycopg2.errors.UniqueViolation`` via duck-typing
    (``pgcode`` attribute) so the scheduler does not hard-dep on
    ``psycopg2`` at import time.
    """
    pgcode = getattr(exc, "pgcode", None)
    if pgcode == "23505":
        return True
    # Also handle the case where psycopg2 isn't available — catch by
    # exception class name as a fallback.
    name = type(exc).__qualname__
    if name == "UniqueViolation" or name.endswith(".UniqueViolation"):
        return True
    return False
