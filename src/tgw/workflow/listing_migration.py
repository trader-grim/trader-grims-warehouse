"""Observation-only decisions for bounded listing-pipeline migration seams."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from tgw.workflow_kernel.contracts import (
    EffectClass,
    FingerprintResult,
    GoalProfile,
    RuntimeWorkGraph,
    TreatmentContract,
    TreatmentDisposition,
)
from tgw.workflow_kernel.evaluator import evaluate
from tgw.workflow_kernel.scheduler import DispatchResult, dispatch_treatment

from .item_snapshot import build_item_snapshot
from .operator_authority import (
    get_authority,
    issue_or_reuse_authority,
    listing_content_identity,
    validate_authority,
)
from .profiles import TGW_EBAY_IDENTIFIED
from .treatments import (
    AI_IDENTIFY,
    EBAY_PUBLISH,
    EBAY_STAGE,
    EBAY_UPLOAD,
    TGW_TREATMENTS,
)

EVALUATOR_VERSION = "pp-workflow-phase3-bundle/v1"
_GOAL_SCOPE_CEILINGS = {
    "tgw.ebay_identified": (),
    "tgw.ebay_staged": ("upload", "stage", "force-restage"),
    "tgw.ebay_listable": ("upload", "stage", "publish", "force-restage"),
    "tgw.ebay_withdrawn": ("withdraw",),
}
_GOAL_SCOPE_DEFAULTS = {
    "tgw.ebay_identified": (),
    "tgw.ebay_staged": ("upload", "stage"),
    "tgw.ebay_listable": ("upload", "stage", "publish"),
    "tgw.ebay_withdrawn": ("withdraw",),
}


class WithdrawalProjectionHeld(RuntimeError):
    """Provider withdrawal succeeded but its canonical result is not complete."""

    def __init__(self, effect_id: str, receipt) -> None:
        super().__init__(
            f"withdrawal provider effect {effect_id} projection is "
            f"{receipt.status.lower()}: {receipt.detail or 'no detail'}"
        )
        self.effect_id = effect_id
        self.receipt = receipt


def _bound_item_document(
    item_path: str | Path,
    *,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
) -> dict[str, Any]:
    """Freeze one exact item document and enforce an optional generation."""
    if item_document is None:
        item = json.loads(Path(item_path).read_text(encoding="utf-8"))
    else:
        item = json.loads(
            json.dumps(
                item_document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    if not isinstance(item, dict):
        raise ValueError("item document must be a JSON object")
    if expected_generation is not None:
        from tgw.item_mutation import item_generation

        observed = item_generation(item)
        if observed != expected_generation:
            raise ValueError(
                "item generation conflict: "
                f"expected {expected_generation}, observed {observed}"
            )
    return item


def _require_current_generation(
    item_path: str | Path,
    expected_generation: str,
) -> dict[str, Any]:
    """Recheck pathname truth at an authority/effect transition."""
    return _bound_item_document(
        item_path,
        expected_generation=expected_generation,
    )


def _environment_bound_provider_identity(
    provider_identity: str,
    ebay_environment: str,
) -> str:
    """Bind authority identity whenever a closed eBay environment is supplied."""
    if not ebay_environment:
        return provider_identity
    from tgw.config import bind_ebay_provider_identity

    return bind_ebay_provider_identity(provider_identity, ebay_environment)


def _require_provider_target_environment(
    provider_block: Mapping[str, Any],
    ebay_environment: str,
    block_name: str,
) -> None:
    """Require an item provider target to belong to the selected environment.

    Existing production items predate environment markers, so an untagged
    provider block retains its historical production meaning.  Sandbox has no
    such legacy namespace: every target must be explicitly stamped before an
    operator withdrawal can reach an eBay endpoint.
    """
    marker = provider_block.get("ebay_environment")
    if marker is None:
        if ebay_environment == "production":
            return
        raise ValueError(
            f"untagged legacy {block_name} target is production-only"
        )
    if marker != ebay_environment:
        raise ValueError(
            f"{block_name} target environment {marker!r} does not match "
            f"selected eBay environment {ebay_environment!r}"
        )


def approved_authority_scopes(goal: GoalProfile, requested: tuple[str, ...]) -> tuple[str, ...]:
    ceiling = _GOAL_SCOPE_CEILINGS.get(goal.identity, ())
    chosen = (tuple(sorted(set(requested))) if requested
              else _GOAL_SCOPE_DEFAULTS.get(goal.identity, ()))
    if not set(chosen).issubset(ceiling):
        raise ValueError(f"authority scopes exceed goal ceiling for {goal.identity}")
    return chosen


def _evaluator_authorized_scopes(scopes: list[str]) -> tuple[str, ...]:
    """Map stricter execution scopes onto their prerequisite conditions."""
    mapped = set(scopes)
    if "force-restage" in mapped:
        mapped.add("stage")
    return tuple(sorted(mapped))


def _authoritative_stage_lookup(item: dict, provider_identity: str):
    if not provider_identity:
        return None
    from tgw.provider_effects import ProviderEffectConflict, lookup_authoritative_stage_receipt
    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), dict) else {}

    def lookup(sku):
        effect_id = offer.get("provider_effect_id")
        observation_id = offer.get("legacy_stage_observation_id")
        content_identity = offer.get("stage_content_identity")
        offer_id = offer.get("offer_id")
        if effect_id and observation_id:
            return None
        markers = (content_identity, offer_id)
        if not all(isinstance(value, str) and value.strip() for value in markers):
            return None
        if isinstance(effect_id, str) and effect_id.strip():
            try:
                return lookup_authoritative_stage_receipt(
                    sku=sku, provider_effect_id=effect_id,
                    stage_content_identity=content_identity, offer_id=offer_id,
                    expected_provider_identity=provider_identity,
                )
            except ProviderEffectConflict:
                return None
        if isinstance(observation_id, str) and observation_id.strip():
            from tgw.provider_observations import (
                ProviderObservationConflict,
                lookup_authoritative_legacy_stage_receipt,
            )
            try:
                return lookup_authoritative_legacy_stage_receipt(
                    observation_id=observation_id, sku=sku, offer_id=offer_id,
                    provider_identity=provider_identity,
                    content_identity=content_identity,
                )
            except ProviderObservationConflict:
                return None
        return None

    return lookup

# Frozen Phase 3 inventory. ``migrate`` entries choose pipeline movement;
# ``retained-derived`` entries invalidate rebuildable projections;
# ``entrypoint-authority`` entries begin from an explicit operator request;
# ``scheduler-timer`` entries are recurring timing, not condition successors.
PHASE3_SUCCESSOR_INVENTORY: tuple[tuple[str, str, str, str], ...] = (
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/bundle_intake.py", "_enqueue_downstream", "thumbnail_gen", "retained-derived"),
    ("src/tgw/workers/ebay_upload.py", "handle", "ebay_upload", "migrate"),
    ("src/tgw/workers/ebay_publish.py", "handle", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/ebay_publish.py", "handle", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_stage.py", "handle", "ebay_sync", "migrate"),
    ("src/tgw/workers/ebay_price.py", "handle", "catalog_rebuild", "retained-derived"),
    ("src/tgw/workers/ebay_dole.py", "handle", "ebay_publish", "migrate"),
    ("src/tgw/workers/ebay_dole.py", "run", "ebay_dole", "scheduler-timer"),
    ("src/tgw/workers/ebay_dole.py", "_reschedule", "ebay_dole", "scheduler-timer"),
    ("src/tgw/workers/ebay_sync.py", "run", "ebay_sync", "scheduler-timer"),
    ("src/tgw/workers/ebay_sync.py", "_reschedule", "ebay_sync", "scheduler-timer"),
    ("src/tgw/api.py", "cmd_hint", "ai_identify", "entrypoint-authority"),
    ("src/tgw/api.py", "cmd_resolve_legacy", "ebay_stage", "entrypoint-authority"),
    ("src/tgw/api.py", "cmd_publish", "ebay_publish", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_enqueue_thumbnail_gen", "thumbnail_gen", "retained-derived"),
    ("src/tgw/http_server.py", "_maybe_early_identify", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "_maybe_session_complete_identify", "ai_identify", "entrypoint-authority"),
    ("src/tgw/http_server.py", "apply_revision", "ebay_sync", "entrypoint-authority"),
    ("src/tgw/http_server.py", "item_action", "ebay_upload", "entrypoint-authority"),
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
    require_current_stage_when_published: bool = False,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
    ebay_environment: str = "",
    ebay_rest_endpoint: str = "",
    ebay_trading_endpoint: str = "",
) -> tuple[GoalRequestResult, str, bool]:
    """Issue/reuse one authenticated exact authority, then evaluate it."""
    if not provider_identity.strip():
        raise ValueError("provider identity is required")
    provider_identity = _environment_bound_provider_identity(
        provider_identity,
        ebay_environment,
    )
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("authority TTL must be between 30 and 900 seconds")
    scopes = approved_authority_scopes(goal, scopes)
    if not scopes:
        return (
            request_item_goal(
                item_path,
                goal,
                enqueue_fn=enqueue_fn,
                item_document=item_document,
                expected_generation=expected_generation,
                operator_identity=operator_identity,
                operator_surface=surface,
                ebay_environment=ebay_environment,
                ebay_rest_endpoint=ebay_rest_endpoint,
            ),
            "",
            False,
        )
    item = _bound_item_document(
        item_path,
        item_document=item_document,
        expected_generation=expected_generation,
    )
    stage_lookup = _authoritative_stage_lookup(item, provider_identity)
    snapshot = build_item_snapshot(
        item_path, goal, treatments=TGW_TREATMENTS,
        stage_receipt_lookup=stage_lookup,
        require_current_stage_when_published=require_current_stage_when_published,
    )
    graph = evaluate(
        snapshot=snapshot, goal=goal, treatments=TGW_TREATMENTS,
        evaluator_version="pp-workflow-operator-goal/v1",
    )
    if expected_generation is not None:
        _require_current_generation(item_path, expected_generation)
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
        operator_identity=operator_identity, operator_surface=surface,
        require_current_stage_when_published=require_current_stage_when_published,
        item_document=item,
        expected_generation=snapshot.generation,
        ebay_environment=ebay_environment,
        ebay_rest_endpoint=ebay_rest_endpoint,
    )
    return result, authority_id, created


def authorize_and_dispatch_force_restage(
    item_path: str | Path, *, operator_identity: str, surface: str,
    provider_identity: str, ttl_seconds: int = 300, enqueue_fn=None,
    issuer=issue_or_reuse_authority, authority_lookup=get_authority,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
    ebay_environment: str = "",
    ebay_rest_endpoint: str = "",
) -> tuple[GoalRequestResult, DispatchResult, str, bool]:
    """Authorize and dispatch only the exact governed force-restage treatment."""
    from .profiles import TGW_EBAY_LISTABLE

    result, authority_id, created = authorize_and_request_item_goal(
        item_path, TGW_EBAY_LISTABLE, operator_identity=operator_identity,
        surface=surface, provider_identity=provider_identity,
        scopes=("force-restage",), ttl_seconds=ttl_seconds,
        enqueue_fn=enqueue_fn, issuer=issuer, authority_lookup=authority_lookup,
        item_document=item_document,
        expected_generation=expected_generation,
        ebay_environment=ebay_environment,
        ebay_rest_endpoint=ebay_rest_endpoint,
    )
    if result.dispatched is not None:
        raise RuntimeError("force-restage admission selected an unexpected local treatment")
    if result.graph.ownership_conflicts or result.graph.reconciliation_gates:
        raise RuntimeError("force-restage admission is blocked by reconciliation")
    dispositions = [
        item for item in result.graph.eligible_treatments
        if (item.treatment_id, item.treatment_version)
        == (EBAY_STAGE.identity, EBAY_STAGE.version)
    ]
    if len(dispositions) != 1:
        raise ValueError("force-restage is not the one eligible stage disposition")
    authority = authority_lookup(authority_id)
    if (authority is None or "force-restage" not in authority.scopes
            or authority.entity_id != result.graph.object_id
            or authority.object_generation != result.graph.object_generation):
        raise RuntimeError("force-restage authority binding changed before dispatch")
    _require_current_generation(item_path, result.graph.object_generation)
    dispatched = dispatch_treatment(
        disposition=dispositions[0], entity_id=result.graph.object_id,
        graph=result.graph,
        payload_extra={
            "origin": "operator", "force": True,
            "operator_identity": operator_identity,
            "operator_surface": surface,
            "operator_authority_id": authority_id,
            "pre_authority_condition_hash": authority.pre_authority_condition_hash,
            **({"ebay_environment": ebay_environment} if ebay_environment else {}),
            **({"ebay_endpoint": ebay_rest_endpoint} if ebay_rest_endpoint else {}),
        },
        enqueue_fn=enqueue_fn,
    )
    if not dispatched.enqueued and dispatched.outcome != "already_dispatched":
        raise RuntimeError("failed to dispatch governed force-restage")
    return result, dispatched, authority_id, created


def authorize_and_dispatch_next_listing_effect(
    item_path: str | Path, *, operator_identity: str, surface: str,
    provider_identity: str, ttl_seconds: int = 300, enqueue_fn=None,
    issuer=issue_or_reuse_authority, authority_lookup=get_authority,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
    ebay_environment: str = "",
    ebay_rest_endpoint: str = "",
    ebay_trading_endpoint: str = "",
) -> tuple[GoalRequestResult, DispatchResult | None, str, bool]:
    """Authorize and dispatch the next exact external listing treatment.

    A listing action may need upload, stage, and publish in sequence.  Each
    provider effect changes the canonical item generation, so this function
    deliberately dispatches only one treatment.  The caller may invoke it
    again after that job succeeds; the second invocation issues an authority
    bound to the new generation instead of pre-enqueuing stale future work.
    """
    from .profiles import TGW_EBAY_LISTABLE

    item = (
        _bound_item_document(
            item_path,
            item_document=item_document,
            expected_generation=expected_generation,
        )
        if item_document is not None or expected_generation is not None
        else None
    )
    result, authority_id, created = authorize_and_request_item_goal(
        item_path, TGW_EBAY_LISTABLE, operator_identity=operator_identity,
        surface=surface, provider_identity=provider_identity,
        # An existing offer is re-staged with ``force=True`` below so the
        # current draft replaces the previously staged content before publish.
        # The provider worker deliberately requires the narrower
        # ``force-restage`` scope for that operation; issuing only ``stage``
        # creates a job which is valid enough to enqueue but impossible to
        # execute.  Bind both possibilities up front because the exact
        # disposition is selected from the same evaluated generation below.
        scopes=("upload", "stage", "publish", "force-restage"),
        ttl_seconds=ttl_seconds,
        enqueue_fn=enqueue_fn, issuer=issuer, authority_lookup=authority_lookup,
        item_document=item,
        expected_generation=expected_generation,
        ebay_environment=ebay_environment,
        ebay_rest_endpoint=ebay_rest_endpoint,
        ebay_trading_endpoint=ebay_trading_endpoint,
    )
    if result.dispatched is not None:
        return result, result.dispatched, authority_id, created
    if item is None:
        item = _bound_item_document(item_path)
    if result.graph.ownership_conflicts or result.graph.reconciliation_gates:
        raise RuntimeError("listing admission is blocked by reconciliation")

    allowed = {
        (EBAY_UPLOAD.identity, EBAY_UPLOAD.version): "upload",
        (EBAY_STAGE.identity, EBAY_STAGE.version): "stage",
        (EBAY_PUBLISH.identity, EBAY_PUBLISH.version): "publish",
    }
    dispositions = [
        disposition for disposition in result.graph.eligible_treatments
        if (disposition.treatment_id, disposition.treatment_version) in allowed
    ]
    if not dispositions:
        return result, None, authority_id, created
    if len(dispositions) != 1:
        names = ", ".join(item.treatment_id for item in dispositions)
        raise RuntimeError(f"listing admission selected multiple external treatments: {names}")

    disposition = dispositions[0]
    scope = allowed[(disposition.treatment_id, disposition.treatment_version)]
    effect_endpoint = (
        ebay_trading_endpoint
        if disposition.treatment_id == EBAY_UPLOAD.identity
        else ebay_rest_endpoint
    )
    authority = authority_lookup(authority_id)
    if (authority is None or scope not in authority.scopes
            or authority.entity_id != result.graph.object_id
            or authority.object_generation != result.graph.object_generation):
        raise RuntimeError("listing authority binding changed before dispatch")

    force = bool(
        disposition.treatment_id == EBAY_STAGE.identity
        and (item.get("ebay_offer") or {}).get("offer_id")
    )
    _require_current_generation(item_path, result.graph.object_generation)
    dispatched = dispatch_treatment(
        disposition=disposition, entity_id=result.graph.object_id,
        graph=result.graph,
        payload_extra={
            "origin": "operator",
            **({"force": True} if force else {}),
            "operator_identity": operator_identity,
            "operator_surface": surface,
            "operator_authority_id": authority_id,
            "pre_authority_condition_hash": authority.pre_authority_condition_hash,
            **({"ebay_environment": ebay_environment} if ebay_environment else {}),
            **({"ebay_endpoint": effect_endpoint} if effect_endpoint else {}),
        },
        enqueue_fn=enqueue_fn,
    )
    if not dispatched.enqueued and dispatched.outcome != "already_dispatched":
        raise RuntimeError(
            f"failed to dispatch governed listing treatment {disposition.treatment_id}"
        )
    return result, dispatched, authority_id, created


def authorize_and_dispatch_update_item(
    item_path: str | Path, *, operator_identity: str, surface: str,
    provider_identity: str, ttl_seconds: int = 300, enqueue_fn=None,
    issuer=issue_or_reuse_authority, authority_lookup=get_authority,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
    ebay_environment: str = "",
    ebay_rest_endpoint: str = "",
    ebay_trading_endpoint: str = "",
) -> tuple[GoalRequestResult, DispatchResult | None, str, bool]:
    """Upload/restage current content without ever granting publication.

    This is the server implementation of the W13 ``Update Item`` command.
    It uses the same evaluator and provider-effect dispatcher as listing, but
    its authority ceiling deliberately excludes ``publish``.
    """
    from .profiles import TGW_EBAY_STAGED

    item = _bound_item_document(
        item_path,
        item_document=item_document,
        expected_generation=expected_generation,
    )
    result, authority_id, created = authorize_and_request_item_goal(
        item_path, TGW_EBAY_STAGED, operator_identity=operator_identity,
        surface=surface, provider_identity=provider_identity,
        scopes=("upload", "stage", "force-restage"),
        ttl_seconds=ttl_seconds, enqueue_fn=enqueue_fn,
        issuer=issuer, authority_lookup=authority_lookup,
        require_current_stage_when_published=True,
        item_document=item,
        expected_generation=expected_generation,
        ebay_environment=ebay_environment,
        ebay_rest_endpoint=ebay_rest_endpoint,
        ebay_trading_endpoint=ebay_trading_endpoint,
    )
    if result.dispatched is not None:
        return result, result.dispatched, authority_id, created
    if result.graph.ownership_conflicts or result.graph.reconciliation_gates:
        raise RuntimeError("update admission is blocked by reconciliation")

    allowed = {
        (EBAY_UPLOAD.identity, EBAY_UPLOAD.version): "upload",
        (EBAY_STAGE.identity, EBAY_STAGE.version): "stage",
    }
    dispositions = [
        disposition for disposition in result.graph.eligible_treatments
        if (disposition.treatment_id, disposition.treatment_version) in allowed
    ]
    if not dispositions:
        return result, None, authority_id, created
    if len(dispositions) != 1:
        names = ", ".join(item.treatment_id for item in dispositions)
        raise RuntimeError(f"update admission selected multiple external treatments: {names}")

    disposition = dispositions[0]
    scope = allowed[(disposition.treatment_id, disposition.treatment_version)]
    effect_endpoint = (
        ebay_trading_endpoint
        if disposition.treatment_id == EBAY_UPLOAD.identity
        else ebay_rest_endpoint
    )
    authority = authority_lookup(authority_id)
    if (authority is None or scope not in authority.scopes
            or "publish" in authority.scopes
            or authority.entity_id != result.graph.object_id
            or authority.object_generation != result.graph.object_generation):
        raise RuntimeError("update authority binding changed before dispatch")
    force = bool(
        disposition.treatment_id == EBAY_STAGE.identity
        and (item.get("ebay_offer") or {}).get("offer_id")
    )
    _require_current_generation(item_path, result.graph.object_generation)
    dispatched = dispatch_treatment(
        disposition=disposition, entity_id=result.graph.object_id,
        graph=result.graph,
        payload_extra={
            "origin": "operator",
            **({"force": True} if force else {}),
            "operator_identity": operator_identity,
            "operator_surface": surface,
            "operator_authority_id": authority_id,
            "pre_authority_condition_hash": authority.pre_authority_condition_hash,
            **({"ebay_environment": ebay_environment} if ebay_environment else {}),
            **({"ebay_endpoint": effect_endpoint} if effect_endpoint else {}),
        },
        enqueue_fn=enqueue_fn,
    )
    if not dispatched.enqueued and dispatched.outcome != "already_dispatched":
        raise RuntimeError(
            f"failed to dispatch governed update treatment {disposition.treatment_id}"
        )
    return result, dispatched, authority_id, created


def authorize_and_execute_end_listing(
    item_path: str | Path,
    *,
    config: dict,
    operator_identity: str,
    surface: str,
    provider_identity: str,
    ttl_seconds: int = 300,
    issuer=issue_or_reuse_authority,
    authority_lookup=get_authority,
    reserve_effect=None,
    finish_effect=None,
    inventory_withdraw=None,
    trading_end=None,
    project_item=None,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
):
    """Evaluate, authorize, reserve, and execute one exact listing withdrawal.

    The provider call is synchronous so an operator gets a definitive result,
    but it is not a legacy direct-effect path: the evaluator selects the
    withdrawal treatment, an exact generation-bound authority is issued, and
    the provider-effect ledger is reserved before any network call.  An
    uncertain response remains ambiguous and cannot be blindly repeated.
    """
    import requests

    from tgw.config import (
        bind_ebay_provider_identity,
        configured_ebay_environment,
        ebay_environment_settings,
    )
    from tgw.provider_effects import (
        ProviderEffectConflict,
        ProviderEffectReconciliationRequired,
        finish_provider_effect,
        reserve_and_begin_authorized_effect,
    )

    from .profiles import TGW_EBAY_WITHDRAWN
    from .treatments import EBAY_WITHDRAW, WITHDRAW_TREATMENTS

    if not provider_identity.strip():
        raise ValueError("provider identity is required")
    ebay_environment = configured_ebay_environment(config)
    ebay_settings = ebay_environment_settings(ebay_environment)
    provider_identity = bind_ebay_provider_identity(
        provider_identity,
        ebay_environment,
    )
    if not 30 <= ttl_seconds <= 900:
        raise ValueError("authority TTL must be between 30 and 900 seconds")
    item_path = Path(item_path)
    item = _bound_item_document(
        item_path,
        item_document=item_document,
        expected_generation=expected_generation,
    )
    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), dict) else {}
    listing = item.get("ebay_listing") if isinstance(item.get("ebay_listing"), dict) else {}
    offer_id = str(offer.get("offer_id") or "").strip()
    listing_id = str(listing.get("listing_id") or "").strip()
    if not offer_id and not listing_id:
        raise ValueError("no provider offer or listing is bound to this item")
    # Validate every provider identifier carried by the item, not only the one
    # chosen by the Inventory-API-first withdrawal branch.  Conflicting target
    # evidence is corruption and must not be hidden by branch precedence.
    if offer_id:
        _require_provider_target_environment(
            offer,
            ebay_environment,
            "ebay_offer",
        )
    if listing_id:
        _require_provider_target_environment(
            listing,
            ebay_environment,
            "ebay_listing",
        )

    base_snapshot = build_item_snapshot(
        item_path,
        TGW_EBAY_WITHDRAWN,
        treatments=WITHDRAW_TREATMENTS,
    )
    base_graph = evaluate(
        snapshot=base_snapshot,
        goal=TGW_EBAY_WITHDRAWN,
        treatments=WITHDRAW_TREATMENTS,
        evaluator_version="operator-listing-withdrawal/v1",
    )
    if not any(
        disposition.treatment_id == EBAY_WITHDRAW.identity
        for disposition in base_graph.waiting_treatments
    ):
        raise ValueError("the evaluator does not observe an active listing to end")
    _require_current_generation(item_path, base_snapshot.generation)

    content_identity = listing_content_identity(item)
    now = datetime.now(UTC)
    authority_id, created = issuer(
        operator_identity=operator_identity,
        surface=surface,
        entity_id=base_snapshot.object_id,
        goal_profile_id=TGW_EBAY_WITHDRAWN.identity,
        goal_profile_version=TGW_EBAY_WITHDRAWN.version,
        object_generation=base_snapshot.generation,
        pre_authority_condition_hash=base_graph.condition_hash,
        content_identity=content_identity,
        provider_identity=provider_identity,
        scopes=("withdraw",),
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )
    authority, detail = validate_authority(
        authority_id,
        entity_id=base_snapshot.object_id,
        goal_profile_id=TGW_EBAY_WITHDRAWN.identity,
        goal_profile_version=TGW_EBAY_WITHDRAWN.version,
        object_generation=base_snapshot.generation,
        pre_authority_condition_hash=base_graph.condition_hash,
        content_identity=content_identity,
        provider_identity=provider_identity,
        scope="withdraw",
        lookup=authority_lookup,
    )
    if authority is None:
        raise RuntimeError(f"withdrawal authority is invalid: {detail}")
    snapshot = build_item_snapshot(
        item_path,
        TGW_EBAY_WITHDRAWN,
        treatments=WITHDRAW_TREATMENTS,
        authorized_scopes=("withdraw",),
        authority_identity=authority_id,
    )
    graph = evaluate(
        snapshot=snapshot,
        goal=TGW_EBAY_WITHDRAWN,
        treatments=WITHDRAW_TREATMENTS,
        evaluator_version="operator-listing-withdrawal/v1",
    )
    dispositions = [
        disposition
        for disposition in graph.eligible_treatments
        if (
            disposition.treatment_id,
            disposition.treatment_version,
        ) == (EBAY_WITHDRAW.identity, EBAY_WITHDRAW.version)
    ]
    if len(dispositions) != 1 or graph.ownership_conflicts or graph.reconciliation_gates:
        raise RuntimeError("withdrawal is not the one exact legal provider treatment")

    operation = "withdraw-offer" if offer_id else "end-fixed-price-item"
    provider_endpoint = (
        ebay_settings["rest_api_root"]
        if offer_id
        else ebay_settings["trading_api_endpoint"]
    )
    request = {
        "offer_id": offer_id,
        "listing_id": listing_id,
        "marketplace_id": str(item.get("marketplace_id") or ""),
        "ebay_environment": ebay_environment,
        "endpoint": provider_endpoint,
    }
    reserve = reserve_effect or reserve_and_begin_authorized_effect
    finish = finish_effect or finish_provider_effect
    authority_binding = {
        "entity_id": base_snapshot.object_id,
        "goal_profile_id": TGW_EBAY_WITHDRAWN.identity,
        "goal_profile_version": TGW_EBAY_WITHDRAWN.version,
        "object_generation": base_snapshot.generation,
        "pre_authority_condition_hash": base_graph.condition_hash,
        "content_identity": content_identity,
        "provider_identity": provider_identity,
    }
    if inventory_withdraw is None:
        from tgw.apis.ebay.client import ebay_post

        def inventory_withdraw(target):
            return ebay_post(
                config,
                f"/sell/inventory/v1/offer/{target}/withdraw",
                {},
            )
    if trading_end is None:
        from tgw.apis.ebay.trading import end_item

        def trading_end(target, marketplace):
            return end_item(
                config,
                target,
                marketplace_id=marketplace or None,
            )

    from tgw.item_mutation import (
        item_write_lock,
        mutate_item,
        resolve_item_mutation_journal_root,
    )

    itemdata_root = Path(config.get("itemdata_root", item_path.parent.parent))
    data_root = Path(config.get("data_root", itemdata_root.parent))
    archive_root = Path(config.get("archive_root", data_root / "ItemArchive"))
    journal_root = resolve_item_mutation_journal_root({
        **config,
        "itemdata_root": itemdata_root,
    })
    sku = base_snapshot.object_id
    if project_item is None:
        from tgw.sqlite_catalog import upsert_catalog_row

        def project_item(projected_sku, document):
            return upsert_catalog_row(config, document)

    def project_withdrawal(effect, ended_at):
        def record_withdrawal(document):
            if str(document.get("sku") or item_path.stem) != sku:
                raise ValueError("authoritative document SKU mismatch")
            updated = json.loads(json.dumps(document, ensure_ascii=False))
            projected_listing = dict(updated.get("ebay_listing") or {})
            projected_listing.update({
                "status": "Ended",
                "listing_status": "ENDED",
                "ended_at": ended_at,
                "provider_effect_id": effect.effect_id,
            })
            projected_offer = dict(updated.get("ebay_offer") or {})
            projected_offer["status"] = "UNPUBLISHED"
            updated["ebay_listing"] = projected_listing
            updated["ebay_offer"] = projected_offer
            updated.pop("catalog_verified", None)
            return updated

        receipt = mutate_item(
            item_path=item_path,
            archive_root=archive_root,
            journal_root=journal_root,
            sku=sku,
            kind="operator-command:end-listing-projection",
            expected_generation=base_snapshot.generation,
            payload={
                "provider_effect_id": effect.effect_id,
                "ended_at": ended_at,
                "offer_id": offer_id,
                "listing_id": listing_id,
            },
            mutate=record_withdrawal,
            project=project_item,
        )
        if receipt.status != "COMMITTED":
            raise WithdrawalProjectionHeld(effect.effect_id, receipt)
        return receipt

    # One shared item lock is the complete external linearization boundary:
    # pathname validation, durable effect admission, synchronous provider
    # dispatch, and the generation-bound canonical result all occur before a
    # competing item/provider-target writer may proceed.
    with item_write_lock(journal_root, sku):
        current = _require_current_generation(item_path, base_snapshot.generation)
        current_offer = (
            current.get("ebay_offer")
            if isinstance(current.get("ebay_offer"), dict)
            else {}
        )
        current_listing = (
            current.get("ebay_listing")
            if isinstance(current.get("ebay_listing"), dict)
            else {}
        )
        if (
            str(current_offer.get("offer_id") or "").strip() != offer_id
            or str(current_listing.get("listing_id") or "").strip() != listing_id
        ):
            raise ValueError("withdrawal provider target changed before effect admission")
        try:
            effect = reserve(
                authority_id=authority_id,
                authority_scope="withdraw",
                authority_binding=authority_binding,
                provider="ebay",
                operation=operation,
                entity_type="item",
                entity_id=sku,
                object_generation=base_snapshot.generation,
                graph_id=graph.graph_id,
                treatment_id=EBAY_WITHDRAW.identity,
                treatment_version=EBAY_WITHDRAW.version,
                condition_hash=graph.condition_hash,
                request=request,
            )
        except ProviderEffectConflict as exc:
            raise RuntimeError(
                f"withdrawal provider effect admission failed: {exc}"
            ) from exc
        except ProviderEffectReconciliationRequired as exc:
            raise RuntimeError(
                f"withdrawal provider effect {exc.record.effect_id} "
                "requires reconciliation"
            ) from exc

        if effect.state == "succeeded" and effect.result:
            if (
                effect.result.get("offer_id") != offer_id
                or effect.result.get("listing_id") != listing_id
                or effect.result.get("ebay_environment") != ebay_environment
                or effect.result.get("endpoint") != provider_endpoint
            ):
                raise RuntimeError(
                    "succeeded withdrawal effect provider evidence mismatch"
                )
            ended_at = str(effect.result.get("ended_at") or "").strip()
            if not ended_at:
                raise RuntimeError(
                    "succeeded withdrawal effect lacks projection timestamp"
                )
            projection_receipt = project_withdrawal(effect, ended_at)
            return graph, effect, authority_id, created, projection_receipt
        if effect.state == "rejected":
            raise RuntimeError("the provider previously rejected this exact withdrawal")

        try:
            if offer_id:
                provider_response = inventory_withdraw(offer_id)
            else:
                provider_response = trading_end(
                    listing_id,
                    request["marketplace_id"],
                )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            terminal = status in {400, 404, 409, 422}
            finished = finish(
                effect.effect_id,
                state="rejected" if terminal else "ambiguous",
                error_detail=f"HTTP {status}: {exc}",
            )
            raise RuntimeError(
                f"provider withdrawal "
                f"{'rejected' if terminal else 'is ambiguous'}; "
                f"effect {finished.effect_id} retained"
            ) from exc
        except Exception as exc:
            finished = finish(
                effect.effect_id,
                state="ambiguous",
                error_detail=f"{type(exc).__name__}: {exc}",
            )
            raise RuntimeError(
                "provider withdrawal outcome is ambiguous; "
                f"effect {finished.effect_id} retained"
            ) from exc
        ended_at = datetime.now(UTC).isoformat()
        result = {
            "offer_id": offer_id,
            "listing_id": listing_id,
            "ended_at": ended_at,
            "ebay_environment": ebay_environment,
            "endpoint": provider_endpoint,
            "provider_response": (
                provider_response if isinstance(provider_response, dict) else {}
            ),
        }
        effect = finish(effect.effect_id, state="succeeded", result=result)
        projection_receipt = project_withdrawal(effect, ended_at)
        return graph, effect, authority_id, created, projection_receipt


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
    require_current_stage_when_published: bool = False,
    item_document: Mapping[str, Any] | None = None,
    expected_generation: str | None = None,
    ebay_environment: str = "",
    ebay_rest_endpoint: str = "",
) -> GoalRequestResult:
    """Evaluate one exact generation and dispatch at most one local treatment.

    External treatments are observations only at this migration seam.  They
    remain held until their provider reservation/reconciliation contracts are
    independently admitted.
    """
    provider_identity = _environment_bound_provider_identity(
        provider_identity,
        ebay_environment,
    )
    item = _bound_item_document(
        item_path,
        item_document=item_document,
        expected_generation=expected_generation,
    )
    if stage_receipt_lookup is None and provider_identity:
        stage_receipt_lookup = _authoritative_stage_lookup(item, provider_identity)
    base_snapshot = build_item_snapshot(
        item_path, goal, treatments=treatments,
        stage_receipt_lookup=stage_receipt_lookup,
        require_current_stage_when_published=require_current_stage_when_published,
    )
    base_graph = evaluate(
        snapshot=base_snapshot, goal=goal, treatments=treatments,
        evaluator_version="pp-workflow-operator-goal/v1",
    )
    content_identity = listing_content_identity(item)
    authorized_scopes: list[str] = []
    for scope in ("upload", "stage", "publish", "force-restage", "withdraw"):
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
        authorized_scopes=_evaluator_authorized_scopes(authorized_scopes),
        authority_identity=authority_id or "",
        stage_receipt_lookup=stage_receipt_lookup,
        require_current_stage_when_published=require_current_stage_when_published,
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
        _require_current_generation(item_path, snapshot.generation)
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
                **({"ebay_environment": ebay_environment} if ebay_environment else {}),
                **({"ebay_endpoint": ebay_rest_endpoint} if ebay_rest_endpoint else {}),
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
