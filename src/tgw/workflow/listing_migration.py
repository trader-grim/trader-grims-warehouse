"""Observation-only decisions for bounded listing-pipeline migration seams."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .contracts import (
    EffectClass,
    FingerprintResult,
    GoalProfile,
    RuntimeWorkGraph,
    TreatmentContract,
    TreatmentDisposition,
)
from .evaluator import evaluate
from .item_snapshot import build_item_snapshot
from .operator_authority import (
    get_authority,
    issue_or_reuse_authority,
    listing_content_identity,
    validate_authority,
)
from .profiles import TGW_EBAY_IDENTIFIED
from .scheduler import DispatchResult, dispatch_treatment
from .treatments import AI_IDENTIFY, TGW_TREATMENTS

EVALUATOR_VERSION = "pp-workflow-phase3-bundle/v1"
_GOAL_SCOPE_CEILINGS = {
    "tgw.ebay_identified": (),
    "tgw.ebay_staged": ("upload", "stage"),
    "tgw.ebay_listable": ("upload", "stage", "publish", "force-restage"),
}
_GOAL_SCOPE_DEFAULTS = {
    "tgw.ebay_identified": (),
    "tgw.ebay_staged": ("upload", "stage"),
    "tgw.ebay_listable": ("upload", "stage", "publish"),
}


def approved_authority_scopes(goal: GoalProfile, requested: tuple[str, ...]) -> tuple[str, ...]:
    ceiling = _GOAL_SCOPE_CEILINGS.get(goal.identity, ())
    chosen = (tuple(sorted(set(requested))) if requested
              else _GOAL_SCOPE_DEFAULTS.get(goal.identity, ()))
    if not set(chosen).issubset(ceiling):
        raise ValueError(f"authority scopes exceed goal ceiling for {goal.identity}")
    return chosen


def _authoritative_stage_lookup(item: dict, provider_identity: str):
    if not provider_identity:
        return None
    from tgw.provider_effects import ProviderEffectConflict, lookup_authoritative_stage_receipt
    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), dict) else {}

    def lookup(sku):
        markers = (offer.get("provider_effect_id"),
                   offer.get("stage_content_identity"), offer.get("offer_id"))
        if not all(isinstance(value, str) and value.strip() for value in markers):
            return None
        try:
            return lookup_authoritative_stage_receipt(
                sku=sku, provider_effect_id=markers[0],
                stage_content_identity=markers[1], offer_id=markers[2],
                expected_provider_identity=provider_identity,
            )
        except ProviderEffectConflict:
            return None

    return lookup

# Frozen Phase 3 inventory. ``migrate`` entries choose pipeline movement;
# ``retained-derived`` entries invalidate rebuildable projections;
# ``entrypoint-authority`` entries begin from an explicit operator request;
# ``scheduler-timer`` entries are recurring timing, not condition successors.
PHASE3_SUCCESSOR_INVENTORY: tuple[tuple[str, str, str, str], ...] = (
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "thumbnail_gen", "retained-derived"),
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "ai_identify", "migrate"),
    ("src/tgw/workers/ebay_upload.py", "handle", "ebay_upload", "migrate"),
    ("src/tgw/workers/ebay_publish.py", "handle", "ebay_stage", "migrate"),
    ("src/tgw/workers/ebay_publish.py", "handle", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/ebay_publish.py", "handle", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_stage.py", "handle", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_price.py", "handle", "catalog_rebuild", "retained-derived"),
    ("src/tgw/ebay/sync.py", "enqueue_post_push_sync", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_dole.py", "handle", "ebay_publish", "migrate"),
    ("src/tgw/workers/ebay_dole.py", "run", "ebay_dole", "scheduler-timer"),
    ("src/tgw/workers/ebay_dole.py", "_reschedule", "ebay_dole", "scheduler-timer"),
    ("src/tgw/workers/ebay_sync.py", "run", "ebay_sync", "scheduler-timer"),
    ("src/tgw/workers/ebay_sync.py", "_reschedule", "ebay_sync", "scheduler-timer"),
    ("src/tgw/api.py", "cmd_hint", "ai_identify", "entrypoint-authority"),
    ("src/tgw/api.py", "cmd_resolve_legacy", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/api.py", "cmd_publish", "ebay_publish", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_enqueue_thumbnail_gen", "thumbnail_gen", "retained-derived"),
    ("src/tgw/http_server.py", "patch_item", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_maybe_early_identify", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_maybe_session_complete_identify", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "bulk_action", "ebay_upload", "entrypoint-authority"),
    ("src/tgw/http_server.py", "bulk_action", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/http_server.py", "apply_revision", "ebay_sync", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_price", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_upload", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_publish", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_sync", "entrypoint-authority"),
)

PHASE3_EXPLICIT_EXCLUSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("src/tgw/workers/bundle_intake.py", "_enqueue", "bundle_intake", "intake job creation, not a successor"),
    ("src/tgw/workers/bundle_intake.py", "scan_newitems", "multi_intake", "multi-intake ingress handoff"),
    ("src/tgw/api.py", "cmd_enqueue_sku", "<dynamic>", "explicit manual treatment request"),
    ("src/tgw/api.py", "main", "ebay_publish", "CLI wrapper invokes explicit cmd_publish request"),
)


@dataclass(frozen=True)
class BundleDownstreamDecision:
    graph: RuntimeWorkGraph
    disposition: TreatmentDisposition | None

    @property
    def object_id(self) -> str:
        return self.graph.object_id

    @property
    def object_generation(self) -> str:
        return self.graph.object_generation

    @property
    def graph_id(self) -> str:
        return self.graph.graph_id

    @property
    def enqueue_ai_identify(self) -> bool:
        return self.disposition is not None


def derive_bundle_downstream(item_path: str | Path) -> BundleDownstreamDecision:
    """Derive only the legacy bundle→AI seam from authoritative ItemData.

    This function is pure/read-only and cannot dispatch provider work.  Catalog
    and thumbnail invalidations intentionally remain outside this decision;
    Phase 3 retains those existing derived-projection effects for parity.
    """
    snapshot = build_item_snapshot(
        item_path,
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
    )
    graph = evaluate(
        snapshot=snapshot,
        goal=TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
        evaluator_version=EVALUATOR_VERSION,
    )
    disposition = next(
        (
            item for item in graph.eligible_treatments
            if (item.treatment_id, item.treatment_version)
            == (AI_IDENTIFY.identity, AI_IDENTIFY.version)
        ),
        None,
    )
    return BundleDownstreamDecision(
        graph=graph,
        disposition=disposition,
    )


@dataclass(frozen=True)
class GoalRequestResult:
    graph: RuntimeWorkGraph
    dispatched: DispatchResult | None
    held_external: tuple[str, ...]
    operator_gates: tuple[str, ...]


def authorize_and_request_item_goal(
    item_path: str | Path, goal: GoalProfile, *, operator_identity: str,
    surface: str, provider_identity: str, scopes: tuple[str, ...],
    ttl_seconds: int = 300, enqueue_fn=None, issuer=issue_or_reuse_authority,
    authority_lookup=get_authority,
) -> tuple[GoalRequestResult, str, bool]:
    """Issue/reuse one authenticated exact authority, then evaluate it."""
    if not provider_identity.strip():
        raise ValueError("provider identity is required")
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("authority TTL must be between 30 and 900 seconds")
    scopes = approved_authority_scopes(goal, scopes)
    if not scopes:
        return (request_item_goal(item_path, goal, enqueue_fn=enqueue_fn), "", False)
    item = json.loads(Path(item_path).read_text(encoding="utf-8"))
    stage_lookup = _authoritative_stage_lookup(item, provider_identity)
    snapshot = build_item_snapshot(
        item_path, goal, treatments=TGW_TREATMENTS,
        stage_receipt_lookup=stage_lookup,
    )
    graph = evaluate(
        snapshot=snapshot, goal=goal, treatments=TGW_TREATMENTS,
        evaluator_version="pp-workflow-operator-goal/v1",
    )
    now = datetime.now(UTC)
    authority_id, created = issuer(
        operator_identity=operator_identity, surface=surface,
        entity_id=snapshot.object_id, goal_profile_id=goal.identity,
        goal_profile_version=goal.version, object_generation=snapshot.generation,
        pre_authority_condition_hash=graph.condition_hash,
        content_identity=listing_content_identity(item),
        provider_identity=provider_identity, scopes=scopes, issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    result = request_item_goal(
        item_path, goal, enqueue_fn=enqueue_fn, origin="operator",
        authority_id=authority_id, provider_identity=provider_identity,
        authority_lookup=authority_lookup, stage_receipt_lookup=stage_lookup,
    )
    return result, authority_id, created


def request_item_goal(
    item_path: str | Path,
    goal: GoalProfile,
    *,
    treatments: tuple[TreatmentContract, ...] = TGW_TREATMENTS,
    enqueue_fn=None,
    origin: str = "operator",
    authority_id: str | None = None,
    provider_identity: str = "",
    authority_lookup=get_authority,
    stage_receipt_lookup=None,
    operator_identity: str = "",
    operator_surface: str = "",
) -> GoalRequestResult:
    """Evaluate one exact generation and dispatch at most one local treatment.

    External treatments are observations only at this migration seam.  They
    remain held until their provider reservation/reconciliation contracts are
    independently admitted.
    """
    item = json.loads(Path(item_path).read_text(encoding="utf-8"))
    if stage_receipt_lookup is None and provider_identity:
        stage_receipt_lookup = _authoritative_stage_lookup(item, provider_identity)
    base_snapshot = build_item_snapshot(
        item_path, goal, treatments=treatments,
        stage_receipt_lookup=stage_receipt_lookup,
    )
    base_graph = evaluate(
        snapshot=base_snapshot, goal=goal, treatments=treatments,
        evaluator_version="pp-workflow-operator-goal/v1",
    )
    content_identity = listing_content_identity(item)
    authorized_scopes: list[str] = []
    for scope in ("upload", "stage", "publish", "force-restage"):
        valid, _ = validate_authority(
            authority_id, entity_id=base_snapshot.object_id,
            goal_profile_id=goal.identity, goal_profile_version=goal.version,
            object_generation=base_snapshot.generation,
            pre_authority_condition_hash=base_graph.condition_hash,
            content_identity=content_identity, provider_identity=provider_identity,
            scope=scope, lookup=authority_lookup,
        )
        if valid:
            authorized_scopes.append(scope)
    snapshot = build_item_snapshot(
        item_path, goal, treatments=treatments,
        authorized_scopes=tuple(authorized_scopes),
        authority_identity=authority_id or "",
        stage_receipt_lookup=stage_receipt_lookup,
    )
    graph = evaluate(
        snapshot=snapshot, goal=goal, treatments=treatments,
        evaluator_version="pp-workflow-operator-goal/v1",
    )
    contracts = {(item.identity, item.version): item for item in treatments}
    local = [
        disposition for disposition in graph.eligible_treatments
        if contracts[(disposition.treatment_id, disposition.treatment_version)].effect_class
        is EffectClass.LOCAL
    ]
    fingerprints = {item.condition_id: item.result for item in graph.fingerprints}
    external = tuple(
        contract.identity for contract in treatments
        if contract.effect_class is EffectClass.EXTERNAL
        and set(contract.may_establish).intersection(goal.required)
        and not all(
            fingerprints[condition] in {FingerprintResult.TRUE, FingerprintResult.NOT_APPLICABLE}
            for condition in contract.may_establish
        )
        and all(
            requirement.condition_id.startswith("operator_authorized_")
            or requirement.condition_id == "staged_content_current"
            or fingerprints[requirement.condition_id] in requirement.accepted_results
            for requirement in contract.requires
        )
    )
    gates = tuple(sorted({
        *(f"provider_contract_required:{identity}" for identity in external),
        *(f"reconciliation_required:{identity}" for identity in graph.reconciliation_gates),
    }))
    dispatched = None
    if local and not graph.ownership_conflicts and not graph.reconciliation_gates:
        dispatched = dispatch_treatment(
            disposition=local[0], entity_id=snapshot.object_id, graph=graph,
            payload_extra={
                "origin": origin,
                "pre_authority_condition_hash": base_graph.condition_hash,
                **({"operator_identity": operator_identity}
                   if operator_identity else {}),
                **({"operator_surface": operator_surface}
                   if operator_surface else {}),
                **({"operator_authority_id": authority_id} if authority_id else {}),
            },
            enqueue_fn=enqueue_fn,
        )
        if not dispatched.enqueued and dispatched.outcome != "already_dispatched":
            raise RuntimeError(
                f"failed to dispatch workflow treatment {local[0].treatment_id}"
            )
    return GoalRequestResult(
        graph=graph, dispatched=dispatched, held_external=external,
        operator_gates=gates,
    )
