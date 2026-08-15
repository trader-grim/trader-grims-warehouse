"""Trusted construction of exact post-push reconciliation work."""
from __future__ import annotations

import json
from pathlib import Path

from tgw.provider_effects import ProviderEffectConflict, resolve_succeeded_provider_effect

from .evaluator import evaluate
from .item_snapshot import build_item_snapshot
from .profiles import TGW_EBAY_RECONCILED
from .scheduler import DispatchResult, dispatch_treatment
from .treatments import EBAY_SYNC_TARGETED


def dispatch_targeted_sync(
    item_path: str | Path, *, source_provider_effect_id: str,
    provider_identity: str, enqueue_fn=None,
) -> DispatchResult:
    """Resolve durable source evidence and enqueue one exactly bound sync."""
    path = Path(item_path)
    item = json.loads(path.read_text(encoding="utf-8"))
    sku = item.get("sku")
    if (not isinstance(sku, str) or not sku.strip()
            or sku != path.parent.name):
        raise ValueError("item document SKU must exactly match its directory")
    source, offer_id = resolve_succeeded_provider_effect(
        provider_effect_id=source_provider_effect_id, sku=sku,
        provider_identity=provider_identity,
    )
    marker_parent = item.get(
        "ebay_offer" if source.operation == "stage-draft" else "ebay_listing"
    )
    marker_parent = marker_parent if isinstance(marker_parent, dict) else {}
    if marker_parent.get("provider_effect_id") != source_provider_effect_id:
        raise ProviderEffectConflict("canonical provider effect marker mismatch")
    canonical_offer = (marker_parent.get("offer_id")
                       or (item.get("ebay_offer") or {}).get("offer_id"))
    if canonical_offer != offer_id:
        raise ProviderEffectConflict("canonical offer marker mismatch")
    snapshot = build_item_snapshot(
        path, TGW_EBAY_RECONCILED, treatments=(EBAY_SYNC_TARGETED,),
        provider_projection_receipt={
            "provider_effect_id": source_provider_effect_id,
            "outcome": "source_succeeded",
        },
    )
    graph = evaluate(
        snapshot=snapshot, goal=TGW_EBAY_RECONCILED,
        treatments=(EBAY_SYNC_TARGETED,),
        evaluator_version="post-push-reconciliation/v1",
    )
    disposition = next(
        (item for item in graph.eligible_treatments
         if item.treatment_id == EBAY_SYNC_TARGETED.identity), None,
    )
    if disposition is None:
        raise RuntimeError("targeted sync is not evaluator-eligible")
    result = dispatch_treatment(
        disposition=disposition, entity_id=sku, entity_type="item", graph=graph,
        payload_extra={
            "payload_schema_id": "ebay-sync-targeted/v1",
            "sku": sku, "provider_effect_id": source_provider_effect_id,
            "provider_identity": provider_identity, "expected_offer_id": offer_id,
            "source_operation": source.operation,
        },
        enqueue_fn=enqueue_fn,
    )
    if not result.enqueued and result.outcome != "already_dispatched":
        raise RuntimeError("failed to dispatch targeted provider reconciliation")
    return result
