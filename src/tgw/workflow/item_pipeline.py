"""TGW ItemPipeline runner — Phase 4 pipeline migration.

Wires TGW items through the condition-driven coding spine:
  1. build_item_snapshot() → ObjectSnapshot
  2. evaluate() → RuntimeWorkGraph
  3. dispatch_treatment() → DispatchResult

process_item() handles one item JSON; process_items() scans a directory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .contracts import (
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    TreatmentContract,
    TreatmentReceipt,
)
from .evaluator import evaluate
from .item_snapshot import build_item_snapshot
from .scheduler import DispatchResult, dispatch_treatment

log = logging.getLogger(__name__)

EVALUATOR_VERSION = "item-pipeline/v1"


# ── Reason strings ──────────────────────────────────────────────────────
_REASON_SATISFIED = "all goal requirements satisfied — no treatment needed"
_REASON_CONFLICT = "ownership conflict with another eligible treatment"
_REASON_WAITING = "treatment prerequisites not met"
_REASON_NO_TREATMENTS = "no eligible treatments and no waiting treatments"
_REASON_UNKNOWN = "unknown disposition"


@dataclass
class ItemResult:
    """Result of processing one item through the pipeline."""

    sku: str
    generation: str
    dispatched: list[DispatchResult] = field(default_factory=list)
    skipped_waiting: list[str] = field(default_factory=list)
    skipped_conflict: list[str] = field(default_factory=list)
    skipped_satisfied: bool = False
    error: str = ""

    @property
    def dispatched_count(self) -> int:
        return sum(1 for d in self.dispatched if d.enqueued)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_waiting) + len(self.skipped_conflict) + (
            1 if self.skipped_satisfied else 0
        )


@dataclass
class PipelineSummary:
    """Summary of processing multiple items."""

    total: int = 0
    dispatched: int = 0
    skipped_waiting: int = 0
    skipped_conflict: int = 0
    skipped_satisfied: int = 0
    errors: int = 0
    results: list[ItemResult] = field(default_factory=list)


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────


def process_item(
    item_json_path: str | Path,
    goal_profile: GoalProfile,
    treatments: tuple[TreatmentContract, ...],
    *,
    enqueue_fn: Any = None,
    payload_extra: dict[str, Any] | None = None,
) -> ItemResult:
    """Build snapshot, evaluate, dispatch one eligible treatment.

    The pipeline:
      1. Builds an ObjectSnapshot from the item JSON file
      2. Evaluates it against the goal profile and treatment registry
      3. Dispatches the first eligible treatment (if any)
      4. Logs skipped items (all satisfied, waiting, or conflicted)

    Args:
        item_json_path: Path to a TGW item JSON file.
        goal_profile: Goal profile for evaluation.
        treatments: Treatment registry (tuple of TreatmentContract).
        enqueue_fn: Optional enqueue function (injected for testing).
        payload_extra: Extra payload keys to pass to dispatch.

    Returns:
        ItemResult with dispatch/skip/error info.
    """
    path = Path(item_json_path)
    result = ItemResult(sku=path.parent.name, generation="")

    try:
        # Step 1: Build snapshot
        snapshot = build_item_snapshot(str(path), goal_profile)
        result.generation = snapshot.generation

        # Step 2: Evaluate
        graph = evaluate(
            snapshot=snapshot,
            goal=goal_profile,
            treatments=treatments,
            evaluator_version=EVALUATOR_VERSION,
        )

        # Check if all goal requirements are satisfied
        all_satisfied = _all_requirements_satisfied(graph, snapshot)

        if all_satisfied and not graph.unmet_requirements:
            result.skipped_satisfied = True
            log.info(
                "%s: all %d requirements satisfied — skipping",
                snapshot.object_id,
                len(graph.satisfied_requirements),
            )
            return result

        # Step 3: Pick one eligible treatment (first non-conflicting)
        eligible = list(graph.eligible_treatments)
        if eligible:
            # Pick first eligible treatment
            disp = eligible[0]
            dispatch_result = dispatch_treatment(
                disposition=disp,
                entity_id=snapshot.object_id,
                payload_extra=payload_extra,
                enqueue_fn=enqueue_fn,
            )
            result.dispatched.append(dispatch_result)
            log.info(
                "%s: dispatched %s → %s (job %s)",
                snapshot.object_id,
                disp.treatment_id,
                dispatch_result.queue_name,
                dispatch_result.job_id or "none",
            )
        else:
            # No eligible treatments — report waiting/conflicts
            for w in graph.waiting_treatments:
                reasons = "; ".join(w.reasons)
                result.skipped_waiting.append(f"{w.treatment_id}: {reasons}")
                log.info(
                    "%s: waiting %s — %s",
                    snapshot.object_id,
                    w.treatment_id,
                    reasons,
                )

            if not graph.waiting_treatments:
                result.skipped_satisfied = True
                log.info(
                    "%s: no eligible or waiting treatments — all satisfied",
                    snapshot.object_id,
                )

    except Exception as exc:
        result.error = str(exc)
        log.exception("error processing %s: %s", path, exc)

    return result


def process_items(
    itemdata_root: str | Path,
    goal_profile: GoalProfile,
    treatments: tuple[TreatmentContract, ...],
    *,
    limit: int | None = None,
    enqueue_fn: Any = None,
    payload_extra: dict[str, Any] | None = None,
) -> PipelineSummary:
    """Scan ItemData directory, process each item.

    Walks the ItemData root looking for item.json files (one per SKU
    directory), builds snapshots, evaluates, and dispatches eligible
    treatments.

    Args:
        itemdata_root: Root of the ItemData directory tree.
        goal_profile: Goal profile for evaluation.
        treatments: Treatment registry.
        limit: Maximum number of items to process (None = all).
        enqueue_fn: Optional enqueue function (injected for testing).
        payload_extra: Extra payload keys to pass to dispatch.

    Returns:
        PipelineSummary with aggregate counts and per-item results.
    """
    root = Path(itemdata_root)
    summary = PipelineSummary()

    items_processed = 0
    for item_json in sorted(root.rglob("item.json")):
        if limit is not None and items_processed >= limit:
            break

        item_result = process_item(
            item_json,
            goal_profile,
            treatments,
            enqueue_fn=enqueue_fn,
            payload_extra=payload_extra,
        )
        summary.results.append(item_result)
        summary.total += 1
        summary.dispatched += item_result.dispatched_count
        summary.skipped_waiting += len(item_result.skipped_waiting)
        summary.skipped_conflict += len(item_result.skipped_conflict)
        if item_result.skipped_satisfied:
            summary.skipped_satisfied += 1
        if item_result.error:
            summary.errors += 1

        items_processed += 1

    return summary


def evaluate_and_dispatch(
    *,
    snapshot: ObjectSnapshot,
    goal: GoalProfile,
    treatments: tuple[TreatmentContract, ...],
    entity_id: str,
    enqueue_fn: Any = None,
    payload_extra: dict[str, Any] | None = None,
) -> tuple[DispatchResult | None, list[str], list[str], bool]:
    """Core spine: evaluate snapshot, dispatch eligible treatment.

    This is the reusable evaluation+dispatch function called by both
    process_item() and the re-evaluation path (after a worker receipt).

    Returns:
        (dispatch_result_or_none, waiting_reasons, conflict_reasons, all_satisfied)
    """
    graph = evaluate(
        snapshot=snapshot,
        goal=goal,
        treatments=treatments,
        evaluator_version=EVALUATOR_VERSION,
    )

    all_satisfied = _all_requirements_satisfied(graph, snapshot)

    if all_satisfied:
        return None, [], [], True

    eligible = list(graph.eligible_treatments)
    if eligible:
        disp = eligible[0]
        result = dispatch_treatment(
            disposition=disp,
            entity_id=entity_id,
            payload_extra=payload_extra,
            enqueue_fn=enqueue_fn,
        )
        return result, [], [], False

    waiting = [
        f"{w.treatment_id}: {'; '.join(w.reasons)}"
        for w in graph.waiting_treatments
    ]
    return None, waiting, [], not bool(waiting)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _all_requirements_satisfied(graph, snapshot):
    """Check if all required goal conditions are satisfied."""
    unmet = set(graph.unmet_requirements)
    explicit_unmet = {
        r for r, f in graph.explicit_requirements
        if f not in {FingerprintResult.TRUE, FingerprintResult.NOT_APPLICABLE}
    }
    return not (unmet or explicit_unmet)


def build_receipt_from_worker_return(
    worker_return: dict[str, Any],
    *,
    graph_id: str | None = None,
) -> TreatmentReceipt:
    """Normalize a worker's return dict into a structured TreatmentReceipt.

    Called by QueueWorker._process() after handle() returns.
    """
    return TreatmentReceipt.from_worker_return(worker_return)
