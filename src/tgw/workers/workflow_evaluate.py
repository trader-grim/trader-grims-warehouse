"""Consume durable evidence-change events and re-evaluate authoritative state."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.config import DEFAULT_CONFIG, load_config, sku_json
from tgw.errors import TreatmentFailure
from tgw.item_mutation import item_generation
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
from tgw.workflow.evaluator import evaluate
from tgw.workflow.item_snapshot import build_item_snapshot
from tgw.workflow.operator_authority import (
    get_authority,
    listing_content_identity,
)
from tgw.workflow.profiles import (
    TGW_EBAY_LEGACY_STAGE_ONBOARDED,
    get_profile,
)
from tgw.workflow.scheduler import dispatch_treatment
from tgw.workflow.treatments import LEGACY_STAGE_ONBOARDING_TREATMENTS, TGW_TREATMENTS

QUEUE_NAME = "workflow_evaluate"
EVALUATOR_VERSION = "workflow-evaluate-worker/v1"

_LISTING_CONTINUATION_QUEUES = {
    "normalize-condition": "normalize_condition",
    "ai-identify": "ai_identify",
    "ebay-draft": "ebay_draft",
    "ebay-price": "ebay_price",
    "ebay-upload": "ebay_upload",
    "ebay-stage": "ebay_stage",
}
_LISTING_CONTINUATION_SCOPES = frozenset(("upload", "stage", "publish"))


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


def _provider_identity(config: Mapping[str, Any]) -> str:
    migration = config.get("workflow_migration")
    if migration is None and isinstance(config.get("raw"), Mapping):
        migration = config["raw"].get("workflow_migration")
    migration = migration if isinstance(migration, Mapping) else {}
    value = migration.get("ebay_provider_identity")
    if (not isinstance(value, str) or not value.strip() or value != value.strip()):
        raise _fail("INVALID_PROVIDER_IDENTITY")
    return value


def _validate_listing_continuation(
    *, payload: Mapping[str, Any], origin: Mapping[str, Any], origin_job_id: str,
    entity_id: str, profile_id: str, profile_version: str,
    graph: Any, item: Mapping[str, Any], provider_identity: str,
    origin_lookup: Callable[[str], Mapping[str, Any] | None] | None,
) -> Any:
    """Validate one durable operator listing intent, including retry chains.

    A continuation can be retried after its successor authority was committed.
    In that case the original authority is superseded, so validation follows
    only its explicit ``superseded_by`` chain and requires the current leaf to
    be the exact fresh authority for the newly observed generation.  This
    makes the issue-before-enqueue crash window retryable without accepting an
    unrelated or newly minted operator intent.
    """
    treatment_id = origin.get("treatment_id")
    queue_name = _LISTING_CONTINUATION_QUEUES.get(treatment_id)
    authority_id = payload.get("operator_authority_id")
    if queue_name is None or not isinstance(authority_id, str) or not authority_id:
        raise _fail("INVALID_OPERATOR_CONTINUATION", origin_job_id=origin_job_id)

    lookup = origin_lookup or state_machine.get_job
    durable = lookup(origin_job_id)
    durable_payload = durable.get("payload_json") if isinstance(durable, Mapping) else None
    durable_result = (durable_payload.get("result")
                      if isinstance(durable_payload, Mapping) else None)
    origin_evidence = origin.get("evidence")
    resulting_generation = (
        origin_evidence.get("resulting_generation")
        if isinstance(origin_evidence, Mapping) else None
    )
    exact_payload = {
        "operator_authority_id": authority_id,
        "operator_identity": payload.get("operator_identity"),
        "operator_surface": payload.get("operator_surface"),
        "pre_authority_condition_hash": payload.get("pre_authority_condition_hash"),
        "goal_profile_id": profile_id,
        "goal_profile_version": profile_version,
        "graph_id": payload.get("prior_graph_id"),
        "object_generation": payload.get("prior_object_generation"),
        "treatment_id": treatment_id,
        "treatment_version": "1",
    }
    if (not isinstance(durable, Mapping)
            or str(durable.get("job_id")) != origin_job_id
            or durable.get("state") != "succeeded"
            or durable.get("queue_name") != queue_name
            or durable.get("entity_type") != "item"
            or durable.get("entity_id") != entity_id
            or not isinstance(durable_payload, Mapping)
            or durable_payload.get("sku") != entity_id
            or not isinstance(resulting_generation, str)
            or not resulting_generation
            or resulting_generation != graph.object_generation
            or any(durable_payload.get(key) != value
                   for key, value in exact_payload.items())
            or durable_result != origin):
        raise _fail("UNTRUSTED_OPERATOR_CONTINUATION", origin_job_id=origin_job_id)

    original = get_authority(authority_id)
    expected_original = (
        payload.get("operator_identity"), payload.get("operator_surface"), entity_id,
        profile_id, profile_version, payload.get("prior_object_generation"),
        payload.get("pre_authority_condition_hash"), provider_identity,
    )
    actual_original = (
        getattr(original, "operator_identity", None), getattr(original, "surface", None),
        getattr(original, "entity_id", None), getattr(original, "goal_profile_id", None),
        getattr(original, "goal_profile_version", None),
        getattr(original, "object_generation", None),
        getattr(original, "pre_authority_condition_hash", None),
        getattr(original, "provider_identity", None),
    )
    if (original is None or actual_original != expected_original
            or not _LISTING_CONTINUATION_SCOPES.issubset(set(original.scopes))):
        raise _fail("INVALID_OPERATOR_CONTINUATION", origin_job_id=origin_job_id)

    now = datetime.now(UTC)
    if original.superseded_at is None:
        if now < original.issued_at or now >= original.expires_at:
            raise _fail("EXPIRED_OPERATOR_CONTINUATION", origin_job_id=origin_job_id)
        return original

    # A prior attempt may have durably issued the successor before it crashed.
    # Follow the closed chain, then require its leaf to match current state.
    seen = {original.authority_id}
    current = original
    for _ in range(8):
        successor_id = current.superseded_by
        if not isinstance(successor_id, str) or not successor_id or successor_id in seen:
            raise _fail("INVALID_SUCCESSOR_CONTINUATION", origin_job_id=origin_job_id)
        seen.add(successor_id)
        current = get_authority(successor_id)
        if current is None:
            raise _fail("INVALID_SUCCESSOR_CONTINUATION", origin_job_id=origin_job_id)
        if current.superseded_at is None:
            break
    else:
        raise _fail("INVALID_SUCCESSOR_CONTINUATION", origin_job_id=origin_job_id)

    expected_current = (
        original.operator_identity, original.surface, entity_id, profile_id,
        profile_version, graph.object_generation, graph.condition_hash,
        listing_content_identity(item), provider_identity,
    )
    actual_current = (
        current.operator_identity, current.surface, current.entity_id,
        current.goal_profile_id, current.goal_profile_version,
        current.object_generation, current.pre_authority_condition_hash,
        current.content_identity, current.provider_identity,
    )
    if (actual_current != expected_current
            or not _LISTING_CONTINUATION_SCOPES.issubset(set(current.scopes))
            or now < current.issued_at or now >= current.expires_at):
        raise _fail("INVALID_SUCCESSOR_CONTINUATION", origin_job_id=origin_job_id)
    return current


def evaluate_event(
    job: Mapping[str, Any], config: Mapping[str, Any], *, enqueue_fn: Callable[..., str] | None = None,
    origin_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
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
    origin_claims_isolated = (
        origin.get("treatment_id") == "ebay-onboard-legacy-stage"
        and origin.get("treatment_version") == "1"
    )
    isolated_profile = profile_id == TGW_EBAY_LEGACY_STAGE_ONBOARDED.identity
    if isolated_profile != origin_claims_isolated:
        raise _fail("ISOLATED_ORIGIN_MISMATCH", origin_job_id=origin_job_id)
    isolated = isolated_profile and origin_claims_isolated
    if isolated:
        exact_origin = {
            "receipt_schema_id": "treatment-receipt/v1",
            "treatment_id": "ebay-onboard-legacy-stage",
            "treatment_version": "1",
            "goal_profile_id": profile_id,
            "goal_profile_version": profile_version,
            "entity_id": entity_id,
            "graph_id": payload.get("prior_graph_id"),
            "object_generation": payload.get("prior_object_generation"),
        }
        if (any(origin.get(key) != value for key, value in exact_origin.items())
                or not isinstance(origin.get("condition_hash"), str)
                or not origin["condition_hash"]):
            raise _fail("ISOLATED_ORIGIN_BINDING_MISMATCH", origin_job_id=origin_job_id)
        lookup = origin_lookup or state_machine.get_job
        durable = lookup(origin_job_id)
        durable_payload = durable.get("payload_json") if isinstance(durable, Mapping) else None
        durable_result = (durable_payload.get("result")
                          if isinstance(durable_payload, Mapping) else None)
        durable_bindings = {
            "payload_schema_id": "ebay-onboard-legacy-stage/v1",
            "treatment_id": "ebay-onboard-legacy-stage",
            "treatment_version": "1",
            "goal_profile_id": profile_id,
            "goal_profile_version": profile_version,
            "entity_id": entity_id,
            "sku": entity_id,
            "graph_id": payload.get("prior_graph_id"),
            "object_generation": payload.get("prior_object_generation"),
            "condition_hash": origin.get("condition_hash"),
        }
        if (not isinstance(durable, Mapping) or str(durable.get("job_id")) != origin_job_id
                or durable.get("state") != "succeeded"
                or durable.get("queue_name") != "ebay_onboard_legacy_stage"
                or durable.get("entity_type") != "item"
                or durable.get("entity_id") != entity_id
                or not isinstance(durable_payload, Mapping)
                or any(durable_payload.get(key) != value
                       for key, value in durable_bindings.items())
                or durable_result != origin):
            raise _fail("UNTRUSTED_ISOLATED_ORIGIN", origin_job_id=origin_job_id)
        profile = TGW_EBAY_LEGACY_STAGE_ONBOARDED
        treatments = LEGACY_STAGE_ONBOARDING_TREATMENTS
    else:
        try:
            profile = get_profile(profile_id)
        except KeyError as exc:
            raise _fail("UNKNOWN_PROFILE", profile_id=profile_id) from exc
        treatments = TGW_TREATMENTS
    if not profile_id.startswith("tgw.") or profile.version != profile_version:
        raise _fail("PROFILE_MISMATCH", profile_id=profile_id)

    item_path = sku_json(dict(config), entity_id)
    stage_lookup = None
    marker_generation = None
    if isolated:
        from tgw.workflow.listing_migration import _authoritative_stage_lookup
        item = json.loads(item_path.read_text(encoding="utf-8"))
        marker_generation = item_generation(item)
        migration = config.get("workflow_migration")
        if migration is None and isinstance(config.get("raw"), Mapping):
            migration = config["raw"].get("workflow_migration")
        migration = migration if isinstance(migration, Mapping) else {}
        provider_identity = migration.get("ebay_provider_identity")
        if (not isinstance(provider_identity, str) or not provider_identity.strip()
                or provider_identity != provider_identity.strip()):
            raise _fail("INVALID_PROVIDER_IDENTITY")
        stage_lookup = _authoritative_stage_lookup(item, provider_identity)
    snapshot = build_item_snapshot(
        item_path, profile, treatments=treatments,
        stage_receipt_lookup=stage_lookup,
    )
    if isolated and snapshot.generation != marker_generation:
        raise _fail(
            "CANONICAL_CHANGED_DURING_EVALUATION",
            marker_generation=marker_generation,
            object_generation=snapshot.generation,
        )
    prior_generation = payload.get("prior_object_generation")
    if snapshot.generation == prior_generation:
        raise _fail("EVIDENCE_NOT_CHANGED", object_generation=snapshot.generation)
    graph = evaluate(
        snapshot=snapshot, goal=profile, treatments=treatments,
        evaluator_version=EVALUATOR_VERSION,
    )

    # Continue an explicit governed listing intent after every predecessor,
    # local or external.  This lives on the durable server event path so API,
    # Flutter, and browser callers all get the same upload -> stage -> publish
    # progression.  The durable origin row and complete authority chain are
    # revalidated before the next generation is authorized and dispatched.
    authority_id = payload.get("operator_authority_id")
    treatment_id = origin.get("treatment_id")
    if (not isolated and treatment_id in _LISTING_CONTINUATION_QUEUES
            and isinstance(authority_id, str) and authority_id):
        current_item = json.loads(item_path.read_text(encoding="utf-8"))
        provider_identity = _provider_identity(config)
        authority = _validate_listing_continuation(
            payload=payload, origin=origin, origin_job_id=origin_job_id,
            entity_id=entity_id, profile_id=profile_id,
            profile_version=profile_version, graph=graph, item=current_item,
            provider_identity=provider_identity, origin_lookup=origin_lookup,
        )

        from tgw.workflow.listing_migration import (
            authorize_and_dispatch_next_listing_effect,
        )

        continued, dispatched, successor_authority_id, _ = (
            authorize_and_dispatch_next_listing_effect(
                item_path, operator_identity=authority.operator_identity,
                surface=authority.surface, provider_identity=authority.provider_identity,
                enqueue_fn=enqueue_fn,
            )
        )
        if dispatched is None:
            return _receipt(
                "satisfied", origin_job_id=origin_job_id,
                object_generation=continued.graph.object_generation,
                graph_id=continued.graph.graph_id, continued_from=treatment_id,
                dispatch="none", next_treatment=None, next_job_id=None,
                successor_authority_id=successor_authority_id,
            )
        return _receipt(
            "satisfied", origin_job_id=origin_job_id,
            object_generation=continued.graph.object_generation,
            graph_id=continued.graph.graph_id,
            continued_from=treatment_id,
            dispatch=("enqueued" if dispatched.enqueued else dispatched.outcome),
            next_treatment=dispatched.treatment_id,
            next_job_id=dispatched.job_id,
            successor_authority_id=successor_authority_id,
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

    contracts = {(item.identity, item.version): item for item in treatments}
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
