"""Read-only per-item workflow projection for operator Action Cards."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import EffectClass, FingerprintResult, TreatmentAttempt
from .evaluator import evaluate
from .item_snapshot import build_item_snapshot
from .profiles import TGW_EBAY_LISTABLE
from .treatments import TGW_TREATMENTS

EVALUATOR_VERSION = "workflow-action-card/v1"
_ACTIVE = {"queued", "leased", "running", "retry_wait"}


def _json_value(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def build_item_action_card(
    item_path: str | Path,
    attempts: Sequence[Mapping[str, Any]] = (),
    *,
    provider_identity: str = "",
    reconciled_provider_effect_ids: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Rebuild the current EBAY_LISTABLE graph and join immutable attempts.

    A stage is authoritative only when its durable provider-effect receipt is
    bound to the configured eBay identity.  The operational HTTP projection
    supplies that identity; callers without one intentionally receive a
    ledger-free, read-only graph.
    """
    ambiguities: set[str] = set()
    contracts = {(item.identity, item.version): item for item in TGW_TREATMENTS}
    attempt_rows: list[dict[str, Any]] = []
    for row in attempts:
        payload = row.get("payload_json") if isinstance(row.get("payload_json"), Mapping) else {}
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else None
        treatment_id = payload.get("treatment_id") or (
            result.get("treatment_id") if result else None
        )
        treatment_version = payload.get("treatment_version") or (
            result.get("treatment_version") if result else None
        )
        treatment_key = (treatment_id, treatment_version or "1")
        contract = contracts.get(treatment_key)
        outcome = result.get("outcome") if result else None
        evidence = result.get("evidence") if result else None
        effect_id = evidence.get("provider_effect_id") if isinstance(evidence, Mapping) else None
        reconciled = isinstance(effect_id, str) and effect_id in reconciled_provider_effect_ids
        if (contract and contract.effect_class is EffectClass.EXTERNAL and not reconciled
                and outcome in {
            "ambiguous", "reconciliation_required",
        }):
            ambiguities.update(contract.ownership)
        attempt_rows.append({
            "job_id": str(row.get("job_id") or ""),
            "queue_name": row.get("queue_name"),
            "state": row.get("state"),
            "active": row.get("state") in _ACTIVE,
            "treatment_id": treatment_id,
            "treatment_version": treatment_version,
            "graph_id": payload.get("graph_id") or (result.get("graph_id") if result else None),
            "object_generation": payload.get("object_generation"),
            "condition_hash": payload.get("condition_hash"),
            "attempt_count": row.get("attempt_count"),
            "max_attempts": row.get("max_attempts"),
            "error_detail": row.get("error_detail"),
            "result": dict(result) if result else None,
            "provider_effect_reconciled": reconciled,
            "retry_allowed": False if (
                payload.get("graph_id") or ambiguities
                or (result and result.get("outcome") in {
                    "ambiguous", "reconciliation_required",
                })
            ) else None,
            "not_before": _json_value(row.get("not_before")),
            "created_at": _json_value(row.get("created_at")),
            "updated_at": _json_value(row.get("updated_at")),
            "finished_at": _json_value(row.get("finished_at")),
        })

    stage_receipt_lookup = None
    if provider_identity:
        from .listing_migration import _authoritative_stage_lookup

        item = json.loads(Path(item_path).read_text(encoding="utf-8"))
        stage_receipt_lookup = _authoritative_stage_lookup(item, provider_identity)

    snapshot = build_item_snapshot(
        item_path, TGW_EBAY_LISTABLE, treatments=TGW_TREATMENTS,
        stage_receipt_lookup=stage_receipt_lookup,
        external_effect_ambiguities=tuple(ambiguities),
    )
    preliminary_graph = evaluate(
        snapshot=snapshot, goal=TGW_EBAY_LISTABLE,
        treatments=TGW_TREATMENTS, evaluator_version=EVALUATOR_VERSION,
    )
    immutable_attempts = tuple(
        TreatmentAttempt(
            treatment_id=str(item["treatment_id"]),
            treatment_version=str(item["treatment_version"]),
            object_generation=str(item["object_generation"]),
            condition_hash=str(item["condition_hash"]),
            outcome=str(item["result"]["outcome"]),
            receipt_id=str(item["job_id"]),
        )
        for item in attempt_rows
        if item["state"] not in _ACTIVE
        and item.get("treatment_id")
        and item.get("treatment_version")
        and item.get("object_generation") == snapshot.generation
        and item.get("condition_hash") == preliminary_graph.condition_hash
        and isinstance(item.get("result"), Mapping)
        and item["result"].get("outcome") in {"failed", "partial", "conflict"}
        and (item["treatment_id"], item["treatment_version"]) in contracts
        and item.get("job_id")
    )
    graph = evaluate(
        snapshot=snapshot, goal=TGW_EBAY_LISTABLE,
        treatments=TGW_TREATMENTS, evaluator_version=EVALUATOR_VERSION,
        attempts=immutable_attempts,
    )
    active_keys = {
        (item.get("treatment_id"), item.get("treatment_version"))
        for item in attempt_rows
        if item["active"]
        and item.get("object_generation") == snapshot.generation
        and item.get("condition_hash") == graph.condition_hash
        and (item.get("treatment_id"), item.get("treatment_version")) in contracts
    }
    legal_actions = []
    provider_contract_gates: set[str] = set()
    for disposition in graph.eligible_treatments:
        contract = contracts[(disposition.treatment_id, disposition.treatment_version)]
        if (disposition.treatment_id, disposition.treatment_version) in active_keys:
            continue
        if contract.effect_class is EffectClass.EXTERNAL:
            action = "held_external_contract"
            provider_contract_gates.add(
                f"provider_contract_required:{disposition.treatment_id}"
            )
        else:
            action = "dispatch"
        legal_actions.append({
            "treatment_id": disposition.treatment_id,
            "treatment_version": disposition.treatment_version,
            "effect_class": contract.effect_class.value,
            "action": action,
            "reasons": list(disposition.reasons),
        })
    operator_gates = list(graph.reconciliation_gates)
    operator_gates.extend(provider_contract_gates)
    operator_gates.extend(
        condition for condition, result in graph.explicit_requirements
        if result in {FingerprintResult.UNKNOWN, FingerprintResult.CONTRADICTORY}
    )
    return {
        "schema_version": "workflow-action-card/v1",
        "entity_id": snapshot.object_id,
        "goal": {"id": graph.goal_profile_id, "version": graph.goal_profile_version},
        "object_generation": graph.object_generation,
        "graph_id": graph.graph_id,
        "condition_hash": graph.condition_hash,
        "fingerprints": [
            {
                "condition_id": item.condition_id,
                "result": item.result.value,
                "reasons": list(item.reasons),
                "evidence": [asdict(reference) for reference in item.evidence],
            }
            for item in graph.fingerprints
        ],
        "satisfied_requirements": list(graph.satisfied_requirements),
        "unmet_requirements": list(graph.unmet_requirements),
        "eligible_treatments": [asdict(item) for item in graph.eligible_treatments],
        "waiting_treatments": [asdict(item) for item in graph.waiting_treatments],
        "ownership_conflicts": [list(item) for item in graph.ownership_conflicts],
        "reconciliation_gates": list(graph.reconciliation_gates),
        "operator_gates": sorted(set(operator_gates)),
        "next_event_classes": list(graph.next_event_classes),
        "attempts": attempt_rows,
        "active_attempts": [item for item in attempt_rows if item["active"]],
        "in_progress_treatments": [
            {"treatment_id": treatment_id, "treatment_version": treatment_version}
            for treatment_id, treatment_version in sorted(active_keys)
        ],
        "legal_actions": legal_actions,
        # Replay is never a legal Action Card action. Re-evaluation derives a
        # new treatment after changed evidence or explicit authority.
        "blind_retry_allowed": False,
    }
