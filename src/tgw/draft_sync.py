"""Draft⇄offer lifecycle manager primitives (PP-UIPIPE-001 broker B1a).

The draft is a working surface, not a mirror (see
docs/ai-plans/reconciliation-broker.md, "Draft lifecycle — B0 design
decision"). It legitimately diverges while AI or the operator manipulates
it, and is re-baselined to the live offer at defined points:

  M1/M2  a draft→offer push completes (ebay_publish success) — content is
         equal by construction, so the manager marks the baseline;
  M4     the operator presses Reset Draft ("live is better, start over") —
         pin_draft_to_live() copies the live mirror into the draft;
  S1     (future reconcile worker) unattended drift repair, allowed only
         while draft_listing_state == 'baseline'.

Every other write to draft_listing moves the state to 'editing'
(http_server.patch_item hook) — divergence while editing is intentional
and the manager must not converge over it.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from tgw.ebay.draft_specifics import wrap_ebay_specifics

__all__ = ["baseline_fields", "resolve_pipeline_error", "pin_draft_to_live"]


def baseline_fields(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Fields that mark the draft as re-baselined to the offer."""
    ts = (now or datetime.now(timezone.utc)).isoformat()
    return {"draft_listing_state": "baseline", "baseline_at": ts}


def resolve_pipeline_error(
    pe: Any,
    new_draft: Dict[str, Any],
    *,
    clear_rejections: bool = True,
) -> Any:
    """C11-safe pipeline_error resolution after a draft change.

    Guard findings ({code, detail, ts, source}) persist a real condition —
    cleared only when the new draft resolves it (invariant C11: never
    silently drop an unresolved finding; unknown codes are kept).
    Push rejections (code == 'ebay_rejected', or the legacy
    {worker, error, raw, at} schema) describe an abandoned push of the old
    draft — cleared on a re-pin (clear_rejections=True) but kept on a mere
    draft edit (clear_rejections=False), since editing one field does not
    prove the rejected content was fixed.

    Returns the value pipeline_error should now hold (None = cleared).
    """
    if not isinstance(pe, dict) or not pe:
        return None
    is_rejection = pe.get("code") == "ebay_rejected" or (
        pe.get("error") and not pe.get("code")
    )
    if is_rejection:
        return None if clear_rejections else pe
    if pe.get("code") == "no_price_set":
        return None if new_draft.get("price") is not None else pe
    return pe  # unknown guard finding — keep (C11)


def pin_draft_to_live(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the fence-PATCH fields that re-pin draft_listing to the
    ebay_live mirror (M4 operator reset / S1 drift repair).

    Pure — no I/O; the caller applies the returned fields through the
    fence. Raises ValueError when there is no mirror to pin from.
    """
    live = doc.get("ebay_live") or {}
    live_inv = live.get("inventory_item") or {}
    live_prod = live_inv.get("product") or {}
    live_off = live.get("offer") or {}
    if not live_inv and not live_off:
        raise ValueError("no ebay_live mirror — run Sync from eBay first")

    dl = dict(doc.get("draft_listing") or {})
    if live_prod.get("title"):
        dl["title"] = live_prod["title"]
    if live_prod.get("description"):
        dl["description"] = live_prod["description"]
    if live_prod.get("imageUrls"):
        dl["imageUrls"] = live_prod["imageUrls"]
    if live_prod.get("aspects"):
        # todo #1418: Set B envelope, written via tgw.ebay.draft_specifics — this
        # is a full re-pin to the live mirror (M4/S1), so a full-replace envelope
        # is correct (no history diff here; the live mirror IS the new baseline).
        dl["item_specifics"] = wrap_ebay_specifics({
            k: (v[0] if isinstance(v, list) and v else v)
            for k, v in live_prod["aspects"].items()
        })
    if live_inv.get("condition"):
        dl["condition_enum"] = live_inv["condition"]
    live_price = ((live_off.get("pricingSummary") or {}).get("price") or {}).get("value")
    if live_price is not None:
        try:
            dl["price"] = float(live_price)
        except (TypeError, ValueError):
            pass
    if live_off.get("availableQuantity") is not None:
        dl["quantity"] = live_off["availableQuantity"]
    if live_off.get("listingDescription"):
        dl["listing_description"] = live_off["listingDescription"]
    live_policies = live_off.get("listingPolicies") or {}
    raw_best_offer_terms = (
        live_policies.get("bestOfferTerms")
        if isinstance(live_policies, dict)
        else None
    )
    best_offer_terms = (
        raw_best_offer_terms if isinstance(raw_best_offer_terms, dict) else {}
    )
    live_best_offer_enabled = best_offer_terms.get("bestOfferEnabled")
    if isinstance(live_best_offer_enabled, bool):
        dl["best_offer_enabled"] = live_best_offer_enabled
    for provider_name, draft_name in (
        ("autoAcceptPrice", "best_offer_auto_accept_price"),
        ("autoDeclinePrice", "best_offer_auto_decline_price"),
    ):
        provider_price = best_offer_terms.get(provider_name)
        if isinstance(provider_price, dict):
            provider_price = provider_price.get("value")
        if provider_price not in (None, ""):
            dl[draft_name] = provider_price

    return {
        "draft_listing": dl,
        "revision_draft": None,
        "pipeline_error": resolve_pipeline_error(doc.get("pipeline_error"), dl),
        **baseline_fields(),
    }
