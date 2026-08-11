"""Trusted, source-only admission helpers for legacy staged offers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from tgw.apis.ebay.client import ebay_get
from tgw.config import sku_json
from tgw.ebay.sync import _build_offer_bodies
from tgw.item_mutation import item_generation
from tgw.queue import state_machine

from .evaluator import evaluate
from .item_snapshot import build_item_snapshot
from .operator_authority import listing_content_identity
from .profiles import TGW_EBAY_LEGACY_STAGE_ONBOARDED
from .scheduler import DispatchResult, dispatch_treatment
from .treatments import LEGACY_STAGE_ONBOARDING_TREATMENTS

PAYLOAD_SCHEMA = "ebay-onboard-legacy-stage/v1"
EVALUATOR_VERSION = "legacy-stage-onboarding-producer/v1"
QUEUE_NAME = "ebay_onboard_legacy_stage"


def _discover_existing_offer_marketplace(
    config: Mapping[str, Any], *, sku: str, offer_id: str,
) -> str:
    """Read the exact existing offer and return its provider-owned marketplace."""
    observed = ebay_get(
        dict(config),
        f"/sell/inventory/v1/offer/{quote(offer_id, safe='')}",
    )
    if not isinstance(observed, Mapping):
        raise ValueError("existing offer observation is malformed")
    if observed.get("offerId") != offer_id or observed.get("sku") != sku:
        raise ValueError("existing offer observation binding mismatch")
    marketplace = observed.get("marketplaceId")
    if (not isinstance(marketplace, str) or not marketplace.strip()
            or marketplace != marketplace.strip()):
        raise ValueError("existing offer marketplace is absent")
    return marketplace


def request_legacy_stage_onboarding(
    item_path: Path, *, sku: str, config: Mapping[str, Any],
    enqueue_fn: Callable[..., str] | None = None,
) -> DispatchResult:
    """Validate one canonical legacy offer and enqueue its isolated treatment."""
    canonical_path = sku_json(dict(config), sku)
    if Path(item_path).resolve() != canonical_path.resolve():
        raise ValueError("item path does not match canonical SKU path")
    migration = config.get("workflow_migration")
    if migration is None and isinstance(config.get("raw"), Mapping):
        migration = config["raw"].get("workflow_migration")
    migration = migration if isinstance(migration, Mapping) else {}
    consumer_mode = migration.get("ebay_legacy_stage_onboarding_consumer", "off")
    if consumer_mode != "workflow":
        raise ValueError("legacy onboarding consumer is not admitted")
    provider_identity = migration.get("ebay_provider_identity")
    if (not isinstance(provider_identity, str) or not provider_identity.strip()
            or provider_identity != provider_identity.strip()):
        raise ValueError("configured provider identity is required")
    item = json.loads(canonical_path.read_text(encoding="utf-8"))
    if not isinstance(item, dict) or item.get("sku") != sku:
        raise ValueError("canonical item SKU mismatch")
    offer = item.get("ebay_offer")
    if not isinstance(offer, dict):
        raise ValueError("canonical legacy offer is absent")
    offer_id = offer.get("offer_id")
    if not isinstance(offer_id, str) or not offer_id.strip():
        raise ValueError("canonical legacy offer_id is absent")
    authority_markers = (
        "provider_effect_id", "legacy_stage_observation_id",
        "stage_content_identity",
    )
    if any(key in offer for key in authority_markers):
        raise ValueError("canonical offer already has staged authority evidence")
    marketplace = offer.get("marketplace_id") or offer.get("marketplaceId")
    if not isinstance(marketplace, str) or not marketplace.strip():
        marketplace = _discover_existing_offer_marketplace(
            config, sku=sku, offer_id=offer_id,
        )
    generation = item_generation(item)
    content_identity = listing_content_identity(item)
    inventory_body, offer_body = _build_offer_bodies(
        dict(config), sku, item, known_marketplace_id=marketplace,
    )
    snapshot = build_item_snapshot(
        canonical_path, TGW_EBAY_LEGACY_STAGE_ONBOARDED,
    )
    if snapshot.generation != generation:
        raise ValueError("canonical item changed during onboarding evaluation")
    graph = evaluate(
        snapshot=snapshot, goal=TGW_EBAY_LEGACY_STAGE_ONBOARDED,
        treatments=LEGACY_STAGE_ONBOARDING_TREATMENTS,
        evaluator_version=EVALUATOR_VERSION,
    )
    if len(graph.eligible_treatments) != 1 or graph.waiting_treatments:
        raise ValueError("legacy onboarding does not have one legal disposition")
    latest = json.loads(canonical_path.read_text(encoding="utf-8"))
    if (not isinstance(latest, dict) or item_generation(latest) != graph.object_generation
            or listing_content_identity(latest) != content_identity):
        raise ValueError("canonical item changed before onboarding dispatch")
    result = dispatch_treatment(
        disposition=graph.eligible_treatments[0], entity_id=sku, graph=graph,
        payload_extra={
            "payload_schema_id": PAYLOAD_SCHEMA,
            "provider_identity": provider_identity, "offer_id": offer_id,
            "content_identity": content_identity,
            "expected_inventory_item": inventory_body,
            "expected_offer": offer_body,
        },
        enqueue_fn=enqueue_fn,
    )
    if not result.enqueued and result.outcome != "already_dispatched":
        raise RuntimeError("legacy onboarding dispatch failed")
    return result


def inventory_legacy_stage_onboarding(
    *, connection: Any | None = None,
) -> dict[str, dict[str, int]]:
    """Return privacy-safe state counts using one read-only SELECT."""
    def read(con: Any) -> dict[str, dict[str, int]]:
        with con.cursor() as cur:
            cur.execute(
                "SELECT state, CASE WHEN payload_json->>'payload_schema_id'=%s "
                "THEN 'schema_v1' ELSE 'ambiguous' END AS shape, COUNT(*) "
                "FROM queue_jobs WHERE queue_name=%s "
                "GROUP BY state, shape ORDER BY state, shape",
                (PAYLOAD_SCHEMA, QUEUE_NAME),
            )
            counts: dict[str, dict[str, int]] = {}
            for state, shape, count in cur.fetchall():
                counts.setdefault(str(state), {})[str(shape)] = int(count)
            return counts
    if connection is not None:
        return read(connection)
    with state_machine._conn() as con:
        return read(con)
