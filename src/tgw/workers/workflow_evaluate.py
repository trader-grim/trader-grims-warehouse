"""Consume durable evidence-change events and re-evaluate authoritative state."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.config import DEFAULT_CONFIG, load_config, sku_json
from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import QueueWorker
from tgw.workflow.evaluator import evaluate
from tgw.workflow.item_snapshot import build_item_snapshot
from tgw.workflow.profiles import get_profile
from tgw.workflow.scheduler import dispatch_treatment
from tgw.workflow.treatments import TGW_TREATMENTS

QUEUE_NAME = "workflow_evaluate"
EVALUATOR_VERSION = "workflow-evaluate-worker/v1"


def _receipt(outcome: str, **evidence: Any) -> dict[str, Any]:
    return {
        "event_id": "workflow-evaluate",
        "event_version": "1",
        "outcome": outcome,
        "evidence": evidence,
        "receipt_schema_id": "workflow-evaluation-receipt/v1",
    }


def _fail(reason: str, **evidence: Any) -> TreatmentFailure:
    evidence["reason_code"] = reason
    return TreatmentFailure(f"workflow evaluation failed: {reason}", _receipt("failed", **evidence))


def evaluate_event(
    job: Mapping[str, Any], config: Mapping[str, Any], *, enqueue_fn: Callable[..., str] | None = None,
) -> dict[str, Any]:
    payload = job.get("payload_json")
    if not isinstance(payload, Mapping):
        raise _fail("INVALID_PAYLOAD")
    entity_id = payload.get("entity_id")
    profile_id = payload.get("goal_profile_id")
    profile_version = payload.get("goal_profile_version")
    origin_job_id = payload.get("origin_job_id")
    origin = payload.get("origin_receipt")
    if not all(isinstance(value, str) and value for value in
               (entity_id, profile_id, profile_version, origin_job_id)):
        raise _fail("INVALID_IDENTITY")
    if job.get("entity_type") != "item" or job.get("entity_id") != entity_id:
        raise _fail("ENTITY_MISMATCH", entity_id=entity_id)
    if not isinstance(origin, Mapping) or origin.get("outcome") != "satisfied":
        raise _fail("INVALID_ORIGIN_RECEIPT", origin_job_id=origin_job_id)
    if origin.get("graph_id") != payload.get("prior_graph_id"):
        raise _fail("ORIGIN_GRAPH_MISMATCH", origin_job_id=origin_job_id)
    try:
        profile = get_profile(profile_id)
    except KeyError as exc:
        raise _fail("UNKNOWN_PROFILE", profile_id=profile_id) from exc
    if not profile_id.startswith("tgw.") or profile.version != profile_version:
        raise _fail("PROFILE_MISMATCH", profile_id=profile_id)

    snapshot = build_item_snapshot(
        sku_json(dict(config), entity_id), profile, treatments=TGW_TREATMENTS,
    )
    prior_generation = payload.get("prior_object_generation")
    if snapshot.generation == prior_generation:
        raise _fail("EVIDENCE_NOT_CHANGED", object_generation=snapshot.generation)
    graph = evaluate(
        snapshot=snapshot, goal=profile, treatments=TGW_TREATMENTS,
        evaluator_version=EVALUATOR_VERSION,
    )
    evidence: dict[str, Any] = {
        "origin_job_id": origin_job_id,
        "object_generation": snapshot.generation,
        "graph_id": graph.graph_id,
        "eligible": [item.treatment_id for item in graph.eligible_treatments],
    }
    if graph.reconciliation_gates or graph.ownership_conflicts:
        evidence["dispatch"] = "held_reconciliation"
        return _receipt("satisfied", **evidence)
    if not graph.eligible_treatments:
        evidence["dispatch"] = "none"
        return _receipt("satisfied", **evidence)

    contracts = {(item.identity, item.version): item for item in TGW_TREATMENTS}
    local_dispositions = [
        disposition for disposition in graph.eligible_treatments
        if contracts[(disposition.treatment_id, disposition.treatment_version)].effect_class.value
        == "local"
    ]
    if not local_dispositions:
        external_candidates = [
            disposition.treatment_id for disposition in graph.eligible_treatments
        ]
        evidence["dispatch"] = "held_external"
        evidence["external_candidates"] = external_candidates
        return _receipt("satisfied", **evidence)
    disposition = local_dispositions[0]
    dispatched = dispatch_treatment(
        disposition=disposition, entity_id=entity_id, graph=graph,
        enqueue_fn=enqueue_fn,
    )
    evidence["dispatch"] = "enqueued" if dispatched.enqueued else dispatched.outcome
    evidence["next_treatment"] = disposition.treatment_id
    evidence["next_job_id"] = dispatched.job_id
    return _receipt("satisfied", **evidence)


class WorkflowEvaluateWorker(QueueWorker):
    def handle(self, job: dict[str, Any]) -> dict[str, Any]:
        return evaluate_event(job, self.config)


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-workflow-evaluate-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--queue", default=QUEUE_NAME)
    args = parser.parse_args()
    WorkflowEvaluateWorker(args.queue, load_config(Path(args.config))).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
