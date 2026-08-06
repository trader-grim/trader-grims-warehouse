"""Treatment dispatch scheduler — enqueues workers based on evaluator output.

Phase 2: receives RuntimeWorkGraph from evaluate(), dispatches one eligible
treatment with generation-bound dedupe.
Phase 3-4: receives TreatmentDisposition from evaluate(), maps to
queue worker enqueue calls, and returns structured DispatchResult.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Optional

from tgw.workers.coding import validated_coding_worktree

from .contracts import (
    GoalProfile,
    ObjectSnapshot,
    RuntimeWorkGraph,
    TreatmentContract,
    TreatmentDisposition,
)

log = logging.getLogger(__name__)

EVALUATOR_VERSION = "workflow-scheduler/v2"


# ── Phase 4: DispatchResult dataclass ──────────────────────────────────────

@dataclass
class DispatchResult:
    """Result of dispatching a single treatment."""
    treatment_id: str
    treatment_version: str
    queue_name: str
    entity_id: str
    enqueued: bool
    job_id: str = ""
    outcome: str = "dispatched"
    action_card_required: bool = False


# ── Phase 4: treatment-to-queue mapping ────────────────────────────────────

_TREATMENT_QUEUE_MAP: dict[str, str] = {
    "ai-identify": "ai_identify",
    "ebay-draft": "ebay_draft",
    "ebay-price": "ebay_price",
    "ebay-upload": "ebay_upload",
    "ebay-stage": "ebay_stage",
    "ebay-publish": "ebay_publish",
    "alt-text": "alt_text",
    "catalog-rebuild": "catalog_rebuild",
}

_OPERATOR_ADMIT_PENDING = "operator-admit-pending.json"


def _materialize_operator_admission(
    *, entity_id: str, graph: RuntimeWorkGraph | None, payload_extra: dict[str, Any] | None,
    coding_config: dict[str, Any] | None = None,
) -> bool:
    """Create one durable operator-facing approval request for a graph.

    The notification subsystem is TGW's supported operator Action Console
    seam: its file backend keeps a durable, structured record and configured
    desktop/webhook/email backends surface it.  The worktree marker prevents
    repeated foreman ticks from re-notifying for the same graph.
    """
    if graph is None:
        raise ValueError("operator admission requires its authorizing graph")
    worktree = validated_coding_worktree(entity_id, graph.object_id, coding_config)
    marker = worktree / _OPERATOR_ADMIT_PENDING
    record = {
        "schema_id": "tgw-action-card/operator-admit/v1",
        "state": "pending_approval",
        "treatment_id": "operator-admit",
        "graph_id": graph.graph_id,
        "object_id": graph.object_id,
        "object_generation": graph.object_generation,
        "todo_id": (payload_extra or {}).get("todo_id"),
    }
    try:
        existing = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        existing = None
    except (OSError, json.JSONDecodeError):
        existing = None
    if existing == record:
        return False
    temporary = marker.with_suffix(marker.suffix + ".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, marker)
    from tgw.notify import notify
    notify(
        "Coding admission required",
        f"Todo {record['todo_id']} requires operator admission for {entity_id} (graph {graph.graph_id}).",
        level="warning",
    )
    return True


def _treatment_to_queue(treatment_id: str) -> str:
    """Resolve treatment_id to queue worker name."""
    return _TREATMENT_QUEUE_MAP.get(treatment_id, treatment_id)


# ── dispatch_treatment: unified entry point ────────────────────────────────
#
# The function accepts two calling conventions:
#   Phase 2: dispatch_treatment(snapshot, graph, treatments, *, entity_type, entity_id)
#   Phase 4: dispatch_treatment(*, disposition, entity_id, payload_extra, enqueue_fn)
#
# Detection: if args are present, use Phase 2 path; otherwise Phase 4.


def dispatch_treatment(*args: Any, **kwargs: Any) -> Any:
    """Dispatch an eligible treatment.

    Phase 2 calling convention (positional):
        dispatch_treatment(snapshot, graph, treatments, *, entity_type="item", entity_id="")
        Returns Optional[str] (job_id or None).

    Phase 4 calling convention (keyword-only):
        dispatch_treatment(*, disposition, entity_id, payload_extra=None, enqueue_fn=None)
        Returns DispatchResult.
    """
    if args:
        return _dispatch_treatment_v2(*args, **kwargs)
    return _dispatch_treatment_v4(**kwargs)


# ── Phase 2 implementation ─────────────────────────────────────────────────


def _dispatch_treatment_v2(
    snapshot: ObjectSnapshot,
    graph: RuntimeWorkGraph,
    treatments: tuple[TreatmentContract, ...],
    *,
    entity_type: str = "item",
    entity_id: str = "",
) -> Optional[str]:
    """Phase 2: exactly one eligible treatment → enqueue with graph_id dedupe."""
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
            "dispatch_treatment: ownership conflicts present for graph_id=%s — %s",
            graph.graph_id,
            "; ".join(conflicts_repr),
        )
        return None

    if graph.reconciliation_gates:
        log.info(
            "dispatch_treatment: reconciliation gates open for graph_id=%s — %s",
            graph.graph_id,
            ", ".join(graph.reconciliation_gates),
        )
        return None

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

    # Operator admission is an explicit human authority boundary, not a
    # queue-backed worker.  It may be eligible, but it must never be enqueued.
    if chosen.treatment_id == "operator-admit":
        log.info("operator-admit eligible for graph_id=%s; Action Card required", graph.graph_id)
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

    state_machine = importlib.import_module("tgw.queue.state_machine")

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


# ── Phase 4 implementation ─────────────────────────────────────────────────


def _dispatch_treatment_v4(
    *,
    disposition: TreatmentDisposition,
    entity_id: str,
    graph: RuntimeWorkGraph | None = None,
    payload_extra: dict[str, Any] | None = None,
    enqueue_fn: Any = None,
    coding_config: dict[str, Any] | None = None,
) -> DispatchResult:
    """Phase 4: dispatch one eligible treatment by enqueuing its worker.

    When supplied, *graph* is the evaluator result that authorized the
    dispatch.  Its generation binding and fingerprints travel with the job so
    workers and reviewers can explain why this treatment was selected without
    re-reading mutable ambient state.
    """
    queue_name = _treatment_to_queue(disposition.treatment_id)

    if disposition.treatment_id == "operator-admit":
        # Keep the pure/legacy scheduler call usable; production foreman
        # always supplies graph and therefore materializes the durable card.
        created = graph is not None and _materialize_operator_admission(
            entity_id=entity_id, graph=graph, payload_extra=payload_extra,
            coding_config=coding_config,
        )
        log.info("operator-admit eligible for %s; Action Card required", entity_id)
        return DispatchResult(
            treatment_id=disposition.treatment_id,
            treatment_version=disposition.treatment_version,
            queue_name="",
            entity_id=entity_id,
            enqueued=False,
            outcome="action_card_created" if created else "waiting_action_card_required",
            action_card_required=True,
        )

    if enqueue_fn is None:
        # Lazily import to avoid DB dependency in pure tests
        from tgw.queue.state_machine import enqueue_job

        enqueue_fn = enqueue_job

    payload: dict[str, Any] = {
        "entity_id": entity_id,
        "treatment_id": disposition.treatment_id,
        "treatment_version": disposition.treatment_version,
        "eligibility_reasons": list(disposition.reasons),
    }
    if graph is not None:
        payload.update(
            {
                "graph_id": graph.graph_id,
                "object_id": graph.object_id,
                "object_generation": graph.object_generation,
                "goal_profile_id": graph.goal_profile_id,
                "goal_profile_version": graph.goal_profile_version,
                "evaluator_version": graph.evaluator_version,
                "fingerprints": [
                    {
                        "condition_id": fingerprint.condition_id,
                        "result": fingerprint.result.value,
                        "reasons": list(fingerprint.reasons),
                        "evidence": [asdict(item) for item in fingerprint.evidence],
                    }
                    for fingerprint in graph.fingerprints
                ],
            }
        )
    if payload_extra:
        payload.update(payload_extra)

    try:
        job_id = enqueue_fn(
            queue_name=queue_name,
            handler_family=queue_name,
            payload=payload,
            entity_type="coding_task" if graph is not None else "item",
            entity_id=entity_id,
            dedupe_key=(
                graph.graph_id
                if graph is not None
                else f"{queue_name}:{entity_id}"
            ),
            max_attempts=3,
        )
        log.info(
            "dispatched %s → %s (job %s)",
            disposition.treatment_id,
            queue_name,
            job_id,
        )
        return DispatchResult(
            treatment_id=disposition.treatment_id,
            treatment_version=disposition.treatment_version,
            queue_name=queue_name,
            entity_id=entity_id,
            enqueued=True,
            job_id=job_id,
        )
    except Exception as exc:
        if _is_duplicate_key(exc):
            log.info("dispatch dedupe collision for %s; treating as already dispatched", disposition.treatment_id)
            return DispatchResult(
                treatment_id=disposition.treatment_id,
                treatment_version=disposition.treatment_version,
                queue_name=queue_name,
                entity_id=entity_id,
                enqueued=False,
                outcome="already_dispatched",
            )
        log.exception(
            "failed to dispatch %s → %s",
            disposition.treatment_id,
            queue_name,
        )
        return DispatchResult(
            treatment_id=disposition.treatment_id,
            treatment_version=disposition.treatment_version,
            queue_name=queue_name,
            entity_id=entity_id,
            enqueued=False,
        )


# ── build_and_dispatch convenience wrapper (Phase 2) ───────────────────────


def build_and_dispatch(
    worktree_path: str,
    goal_profile: GoalProfile,
    treatments: tuple[TreatmentContract, ...],
    *,
    entity_type: str = "item",
    entity_id: str = "",
    evaluator_version: str = EVALUATOR_VERSION,
) -> Optional[str]:
    """Convenience wrapper: build a coding snapshot, evaluate, then dispatch."""
    from .coding_snapshot import build_coding_snapshot
    from .evaluator import evaluate

    snapshot = build_coding_snapshot(worktree_path, goal_profile)
    graph = evaluate(
        snapshot=snapshot,
        goal=goal_profile,
        treatments=treatments,
        evaluator_version=evaluator_version,
    )
    return _dispatch_treatment_v2(
        snapshot=snapshot,
        graph=graph,
        treatments=treatments,
        entity_type=entity_type,
        entity_id=entity_id,
    )


# ── Internal helpers (Phase 2) ─────────────────────────────────────────────


def _lookup_treatment(
    treatment_id: str,
    treatment_version: str,
    treatments: tuple[TreatmentContract, ...],
) -> Optional[TreatmentContract]:
    """Find a TreatmentContract by (identity, version)."""
    for t in treatments:
        if t.identity == treatment_id and t.version == treatment_version:
            return t
    return None


def _is_duplicate_key(exc: Exception) -> bool:
    """Return True when exc is a duplicate-key / uniqueness violation.

    Checks for psycopg2.errors.UniqueViolation via duck-typing
    (pgcode attribute) so the scheduler does not hard-dep on
    psycopg2 at import time.
    """
    pgcode = getattr(exc, "pgcode", None)
    if pgcode == "23505":
        return True
    name = type(exc).__qualname__
    if name == "UniqueViolation" or name.endswith(".UniqueViolation"):
        return True
    return False
