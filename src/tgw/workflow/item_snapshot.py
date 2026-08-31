"""Deterministic, read-only ItemData → ObjectSnapshot builder.

Pure read-only: loads an item JSON file from disk, fingerprints every
pipeline-relevant field into EvidenceAssertions, and returns an immutable
ObjectSnapshot keyed by the item's canonical JSON bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from tgw.workflow_kernel.contracts import (
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    TreatmentContract,
)

from .condition_normalization import normalized_condition
from .operator_authority import listing_content_identity

# ---------------------------------------------------------------------------
# Known condition values (canonical eBay condition names, lowercase)
# ---------------------------------------------------------------------------
_KNOWN_CONDITIONS: set[str] = {
    "new",
    "new other",
    "new with defects",
    "new without tags",
    "manufacturer refurbished",
    "seller refurbished",
    "used",
    "for parts or not working",
    "good",
    "acceptable",
    "like new",
    "very good",
    "new with box",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> bytes:
    """Canonical sorted JSON bytes — same contract as evaluator._canonical."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _ref(identity: str = "item_data", source_generation: str = "1") -> EvidenceReference:
    """Single audit-trail reference into the item JSON."""
    return EvidenceReference(
        identity=identity,
        source_class="item_data",
        source_generation=source_generation,
    )


def _assertion(
    condition_id: str,
    result: FingerprintResult,
    *reasons: str,
) -> EvidenceAssertion:
    """Build an assertion with a mandatory paper-trail reference."""
    evidence = (_ref(),) if reasons else ()
    return EvidenceAssertion(
        condition_id=condition_id,
        result=result,
        reasons=reasons,
        evidence=evidence,
    )


def _load_item(path: Path) -> Dict[str, Any]:
    """Read-only load of one item JSON document."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = json.load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level JSON is not an object")
    return raw


def _get_str(item: Dict[str, Any], key: str) -> str:
    """Safely extract a string field, defaulting to ''."""
    val = item.get(key)
    if isinstance(val, str):
        return val.strip()
    return ""


def _has_photos(item: Dict[str, Any], sku_dir: Path | None = None) -> bool:
    """Canonical item evidence identifies at least one local source photo.

    Older ItemData documents do not necessarily carry ``image`` or ``_images``
    even though their SKU directory contains the source photographs.  The HTTP
    item view has always projected those files at read time; the workflow
    snapshot must observe the same source or a visible Prepare Listing button
    can deterministically hold on a false ``no photos`` finding.
    """
    image = _get_str(item, "image")
    if image:
        return True
    images = item.get("_images")
    if isinstance(images, list) and any(isinstance(i, str) and i.strip() for i in images):
        return True
    ebay_photos = item.get("ebay_photos")
    if isinstance(ebay_photos, list) and any(
        isinstance(photo, dict)
        and isinstance(photo.get("local"), str)
        and bool(photo["local"].strip())
        for photo in ebay_photos
    ):
        return True
    if sku_dir is not None:
        from tgw.assets import ordered_photos

        return bool(ordered_photos(item, sku_dir))
    return False


def _photo_sync_state(
    item: Dict[str, Any], sku_dir: Path,
) -> tuple[bool, str, str]:
    """Return exact local→EPS→draft photo convergence evidence.

    A non-empty hosted-photo list is not sufficient: older records commonly
    contain one successful upload beside several photos that never reached
    EPS.  Staging must wait until every current local photo has one URL and the
    draft carries those exact URLs in the same order.  This is intentionally
    the same local source used by ``ebay_upload``.
    """
    from tgw.assets import ordered_photos

    expected = ordered_photos(item, sku_dir)
    expected_keys = [str(path) for path in expected]
    entries = item.get("ebay_photos")
    entries = entries if isinstance(entries, list) else []
    by_local: dict[str, str] = {}
    invalid_or_duplicate = 0
    for entry in entries:
        if not isinstance(entry, dict):
            invalid_or_duplicate += 1
            continue
        local = entry.get("local")
        url = entry.get("url")
        if (not isinstance(local, str) or not local.strip()
                or not isinstance(url, str) or not url.strip()):
            invalid_or_duplicate += 1
            continue
        local_path = Path(local)
        key = str(local_path if local_path.is_absolute() else sku_dir / local_path)
        if key in by_local:
            invalid_or_duplicate += 1
            continue
        by_local[key] = url.strip()

    ordered_urls = [by_local[key] for key in expected_keys if key in by_local]
    draft = item.get("draft_listing")
    draft = draft if isinstance(draft, dict) else {}
    draft_urls = draft.get("imageUrls")
    draft_urls = draft_urls if isinstance(draft_urls, list) else []
    valid_draft_urls = [value for value in draft_urls if isinstance(value, str) and value]
    # ebay_upload no longer persists draft_listing.imageUrls through the
    # machine fence (operator-object command gate, todo #1931): the draft
    # image order is operator-owned state. An EMPTY draft list is therefore
    # not a conflict — ebay_stage and ebay_sync already fall back to
    # ebay_photos in that case — so the photo-sync fingerprint must too.
    # Only a NON-empty draft list that diverges from the ordered EPS order is
    # a real synchronization gap.
    draft_order_mismatch = bool(draft_urls) and (
        valid_draft_urls != ordered_urls or len(valid_draft_urls) != len(draft_urls)
    )
    missing = [Path(key).name for key in expected_keys if key not in by_local]
    extras = sorted(Path(key).name for key in set(by_local).difference(expected_keys))
    exact = bool(expected_keys) and not any((
        missing,
        extras,
        invalid_or_duplicate,
        draft_order_mismatch,
    ))
    state = {
        "local": [Path(key).name for key in expected_keys],
        "hosted": [
            {
                "local": Path(key).name,
                "url_sha256": hashlib.sha256(by_local[key].encode()).hexdigest(),
            }
            for key in expected_keys if key in by_local
        ],
        "draft_url_sha256": [
            hashlib.sha256(value.encode()).hexdigest() for value in valid_draft_urls
        ],
        "invalid_or_duplicate": invalid_or_duplicate,
        "extras": extras,
    }
    fingerprint = hashlib.sha256(_canonical(state)).hexdigest()
    if exact:
        reason = f"photo sync complete: {len(expected_keys)}/{len(expected_keys)} local photos"
    elif not expected_keys:
        reason = "photo sync waiting: no local photos"
    else:
        detail = []
        if missing:
            detail.append(f"{len(missing)} missing EPS URL(s)")
        if extras:
            detail.append(f"{len(extras)} stale hosted photo(s)")
        if invalid_or_duplicate:
            detail.append(f"{invalid_or_duplicate} invalid/duplicate mapping(s)")
        if draft_order_mismatch:
            detail.append("draft image order is not synchronized")
        reason = (
            f"photo sync waiting: {len(ordered_urls)}/{len(expected_keys)} local photos; "
            + "; ".join(detail)
        )
    return exact, reason, fingerprint


def _photos_uploaded(item: Dict[str, Any], sku_dir: Path | None = None) -> bool:
    """Every current local photo has exact EPS and draft-listing evidence."""
    return bool(sku_dir is not None and _photo_sync_state(item, sku_dir)[0])


def _ai_identified(item: Dict[str, Any]) -> bool:
    """ebay_category_id is set or product_lookup dict is non-empty."""
    if item.get("ai_reidentify") is True:
        return False
    if _get_str(item, "ebay_category_id"):
        return True
    product_lookup = item.get("product_lookup")
    if isinstance(product_lookup, dict) and product_lookup:
        return True
    return False


def _draft_generated(item: Dict[str, Any]) -> bool:
    """draft_listing exists, has a title, and category_id is not '99'."""
    if item.get("ai_redraft_requested") is True:
        return False
    draft = item.get("draft_listing")
    if not isinstance(draft, dict):
        return False
    title = draft.get("title")
    if not isinstance(title, str) or not title.strip():
        return False
    cat_id = str(draft.get("category_id", "99"))
    return cat_id != "99"


def _priced(item: Dict[str, Any]) -> bool:
    """draft_listing.price is non-null and > 0."""
    draft = item.get("draft_listing") or {}
    price = draft.get("price")
    if price is None:
        return False
    try:
        return float(price) > 0
    except (TypeError, ValueError):
        return False


def _staged(item: Dict[str, Any]) -> bool:
    """ebay_offer.offer_id is set."""
    offer = item.get("ebay_offer") or {}
    return bool(offer.get("offer_id"))


def _published(item: Dict[str, Any]) -> bool:
    """Return whether the canonical provider projection says the listing is live.

    Inventory API mirrors use ``status=PUBLISHED`` while Trading/API
    observations use either ``status=Active`` or ``listing_status=ACTIVE``.
    These are serializations of the same provider fact.
    """
    listing = item.get("ebay_listing") or {}
    if not isinstance(listing, Mapping):
        return False
    statuses = (
        listing.get("status"),
        listing.get("listing_status"),
        listing.get("listingStatus"),
    )
    return any(
        str(status or "").strip().upper() in {"ACTIVE", "PUBLISHED"}
        for status in statuses
    )


def inventory_available(item: Mapping[str, Any]) -> bool:
    """Return whether an item may enter the eBay listing pipeline.

    A historical ``status=sold`` is authoritative even when an older draft
    still carries a positive quantity.  Restocking therefore requires an
    explicit status change as well as a positive quantity; stale draft data
    must never turn a sold item back into a listing candidate.
    """
    status = str(item.get("status") or "").strip().lower()
    if status in {"sold", "disposed", "archived", "deleted"}:
        return False
    draft = item.get("draft_listing")
    draft = draft if isinstance(draft, Mapping) else {}
    raw_quantity = draft.get("quantity", item.get("quantity", 1))
    if raw_quantity is None:
        raw_quantity = 1
    if isinstance(raw_quantity, bool):
        return False
    try:
        return int(raw_quantity) > 0
    except (TypeError, ValueError):
        return False


def _listing_provider_consistent(item: Dict[str, Any]) -> bool:
    """No active listing conflicts with the canonical Inventory binding."""
    conflict = item.get("ebay_listing_conflict")
    return not (
        isinstance(conflict, dict)
        and conflict.get("kind")
        == "active_trading_listing_differs_from_inventory_binding"
        and str(conflict.get("trading_listing_id") or "").strip()
    )


def _valid_condition(item: Dict[str, Any]) -> bool:
    """condition field is a known canonical value."""
    cond = _get_str(item, "condition")
    if not cond:
        return False
    return cond.lower() in _KNOWN_CONDITIONS


def _condition_normalizable(item: Dict[str, Any]) -> bool:
    """Condition has one explicit deterministic local alias."""
    return normalized_condition(item.get("condition")) is not None


def _valid_category(item: Dict[str, Any]) -> bool:
    """ebay_category_id is a leaf category (not '99' or empty)."""
    cat = _get_str(item, "ebay_category_id")
    if not cat:
        return False
    return cat != "99"


def _title_ok(item: Dict[str, Any]) -> bool:
    """draft_listing.title is present, non-empty, and ≤ 80 characters."""
    draft = item.get("draft_listing") or {}
    title = draft.get("title")
    if not isinstance(title, str):
        return False
    return 1 <= len(title) <= 80


def _check_pipeline_error(item: Dict[str, Any]) -> Optional[EvidenceAssertion]:
    """If pipeline_error is present, expose it as contradictory evidence."""
    error = item.get("pipeline_error")
    if not error:
        return None
    detail = str(error)[:200]
    return EvidenceAssertion(
        condition_id="pipeline_error",
        result=FingerprintResult.TRUE,
        reasons=(f"pipeline_error: {detail}",),
        evidence=(_ref(),),
    )


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Condition dispatch table — maps condition_id to a (checker_fn, TRUE_reason, FALSE_reason) tuple
# ---------------------------------------------------------------------------

_ITEM_CHECKERS: dict[str, callable] = {
    "inventory_available": inventory_available,
    "listing_provider_consistent": _listing_provider_consistent,
    "item_has_photos": _has_photos,
    "photos_uploaded": _photos_uploaded,
    "ai_identified": _ai_identified,
    "draft_generated": _draft_generated,
    "priced": _priced,
    "staged": _staged,
    "published": _published,
    "valid_condition": _valid_condition,
    "condition_normalizable": _condition_normalizable,
    "valid_category": _valid_category,
    "title_ok": _title_ok,
}

_ITEM_REASONS: dict[str, tuple[str, str]] = {
    "inventory_available": (
        "inventory status and quantity permit listing",
        "inventory is terminal or has no available quantity",
    ),
    "listing_provider_consistent": (
        "one authoritative listing binding",
        "active listing conflicts with Inventory binding",
    ),
    "item_has_photos": ("photos present", "no photos"),
    "photos_uploaded": ("photos uploaded to eBay/draft", "photos not yet uploaded"),
    "ai_identified": ("AI category or product lookup present", "not yet AI-identified"),
    "draft_generated": ("draft with title and category", "no valid draft"),
    "priced": ("price set", "not priced"),
    "staged": ("offer created", "not staged"),
    "published": ("listing active", "not published"),
    "valid_condition": ("known condition", "unknown or missing condition"),
    "condition_normalizable": (
        "condition has deterministic alias",
        "condition has no deterministic alias",
    ),
    "valid_category": ("leaf category", "invalid category (99 or missing)"),
    "title_ok": ("title within 80 chars", "title too long or missing"),
}

# Public API
# ---------------------------------------------------------------------------


def build_item_snapshot(
    item_json_path: str | Path,
    goal_profile: GoalProfile,
    *,
    treatments: tuple[TreatmentContract, ...] = (),
    external_effect_ambiguities: tuple[str, ...] = (),
    authorized_scopes: tuple[str, ...] = (),
    authority_identity: str = "",
    stage_receipt_lookup: Callable[[str], Mapping[str, Any] | None] | None = None,
    provider_projection_receipt: Mapping[str, Any] | None = None,
    require_current_stage_when_published: bool = False,
) -> ObjectSnapshot:
    """Build an ObjectSnapshot from one item JSON file, scoped to goal_profile.

    Pure read-only — no filesystem mutation, no network I/O, no ambient state.
    Only the conditions listed in goal_profile.required are evaluated, producing
    one EvidenceAssertion per condition.  The generation hash is SHA-256 of the
    canonical sorted JSON bytes of the item (content-addressed, not goal-specific).

    Returns an immutable ObjectSnapshot suitable for downstream convergence
    evaluation (e.g. evaluate(snapshot=..., goal=..., ...)).
    """
    path = Path(item_json_path)
    item = _load_item(path)

    # Object identity: SKU from the document or parent directory name.
    sku = _get_str(item, "sku") or path.parent.name

    # Generation: SHA-256 of canonical sorted JSON bytes (content-addressed).
    generation = hashlib.sha256(_canonical(item)).hexdigest()

    assertions: List[EvidenceAssertion] = []
    current_content_identity = listing_content_identity(item)

    condition_ids = set(goal_profile.required)
    for treatment in treatments:
        condition_ids.update(requirement.condition_id for requirement in treatment.requires)
        condition_ids.update(treatment.may_establish)

    for condition_id in sorted(condition_ids):
        if condition_id in {"provider_effect_succeeded", "provider_projection_current"}:
            receipt = provider_projection_receipt
            effect_id = receipt.get("provider_effect_id") if isinstance(receipt, Mapping) else None
            if condition_id == "provider_effect_succeeded":
                met = isinstance(effect_id, str) and bool(effect_id.strip())
            else:
                met = (isinstance(receipt, Mapping)
                       and receipt.get("outcome") == "satisfied"
                       and receipt.get("resulting_generation") == generation)
            assertions.append(EvidenceAssertion(
                condition_id=condition_id,
                result=FingerprintResult.TRUE if met else FingerprintResult.UNKNOWN,
                reasons=(("exact provider reconciliation evidence present" if met
                          else "provider reconciliation evidence absent or stale"),),
                evidence=(EvidenceReference(
                    identity=effect_id or "provider-effect:absent",
                    source_class="provider_effect_receipt",
                    source_generation=(receipt.get("resulting_generation", generation)
                                       if isinstance(receipt, Mapping) else generation),
                ),),
            ))
            continue
        if condition_id.startswith("operator_authorized_"):
            scope = condition_id.removeprefix("operator_authorized_").replace("_", "-")
            authorized = scope in authorized_scopes
            assertions.append(EvidenceAssertion(
                condition_id=condition_id,
                result=FingerprintResult.TRUE if authorized else FingerprintResult.FALSE,
                reasons=(("exact operator authority present" if authorized
                          else f"operator authority for {scope} absent"),),
                evidence=(EvidenceReference(
                    identity=authority_identity or "operator-authority:absent",
                    source_class="operator_authority",
                    source_generation=authority_identity or generation,
                ),),
            ))
            continue
        if condition_id == "staged_content_current":
            offer_exists = _staged(item)
            receipt = stage_receipt_lookup(sku) if stage_receipt_lookup else None
            if _published(item) and not require_current_stage_when_published:
                result = FingerprintResult.NOT_APPLICABLE
                reason = "published listing supersedes staged content freshness"
            elif not offer_exists:
                result = FingerprintResult.FALSE
                reason = "item has no staged offer"
            elif (isinstance(receipt, Mapping)
                  and receipt.get("content_identity") == current_content_identity
                  and isinstance(receipt.get("receipt_id"), str)
                  and receipt["receipt_id"].strip()):
                result = FingerprintResult.TRUE
                reason = "authoritative staged content receipt matches current content"
            elif isinstance(receipt, Mapping) and receipt.get("content_identity"):
                result = FingerprintResult.STALE
                reason = "authoritative staged content receipt is stale"
            else:
                result = FingerprintResult.UNKNOWN
                reason = "authoritative staged content receipt absent"
            receipt_id = receipt.get("receipt_id") if isinstance(receipt, Mapping) else None
            assertions.append(EvidenceAssertion(
                condition_id=condition_id, result=result, reasons=(reason,),
                evidence=(EvidenceReference(
                    identity=receipt_id or "staged-content-receipt:absent",
                    source_class="provider_receipt",
                    source_generation=generation,
                    freshness_identity=current_content_identity,
                ),),
            ))
            continue
        if condition_id == "photos_uploaded":
            met, reason, photo_fingerprint = _photo_sync_state(item, path.parent)
            assertions.append(EvidenceAssertion(
                condition_id=condition_id,
                result=(FingerprintResult.TRUE if met else FingerprintResult.FALSE),
                reasons=(reason,),
                evidence=(EvidenceReference(
                    identity=f"photo-sync:{photo_fingerprint}",
                    source_class="item_data_and_local_photos",
                    source_generation=generation,
                    freshness_identity=photo_fingerprint,
                ),),
            ))
            continue
        checker = _ITEM_CHECKERS.get(condition_id)
        if checker is None:
            assertions.append(
                _assertion(
                    condition_id,
                    FingerprintResult.UNKNOWN,
                    f"no checker registered for {condition_id}",
                )
            )
            continue

        reasons = _ITEM_REASONS.get(condition_id, ("checked", "not met"))
        met = (
            checker(item, path.parent)
            if condition_id == "item_has_photos"
            else checker(item)
        )
        if met:
            assertions.append(_assertion(condition_id, FingerprintResult.TRUE, reasons[0]))
        else:
            assertions.append(_assertion(condition_id, FingerprintResult.FALSE, reasons[1]))

    # pipeline_error — contradictory evidence if present (always checked)
    error_assertion = _check_pipeline_error(item)
    if error_assertion is not None:
        assertions.append(error_assertion)

    return ObjectSnapshot(
        object_id=sku,
        generation=generation,
        assertions=tuple(assertions),
        # Provider-effect ambiguity is a separate evidence class.  Callers
        # must supply it from their authoritative operation/receipt ledger;
        # an ItemData field cannot manufacture or clear this gate.
        external_effect_ambiguities=tuple(sorted({
            value.strip()
            for value in external_effect_ambiguities
            if isinstance(value, str) and value.strip()
        })),
    )
