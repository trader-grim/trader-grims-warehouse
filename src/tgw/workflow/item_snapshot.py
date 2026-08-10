"""Deterministic, read-only ItemData → ObjectSnapshot builder.

Pure read-only: loads an item JSON file from disk, fingerprints every
pipeline-relevant field into EvidenceAssertions, and returns an immutable
ObjectSnapshot keyed by the item's canonical JSON bytes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .contracts import (
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
)

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


def _has_photos(item: Dict[str, Any]) -> bool:
    """Image field is non-empty, or _images list from get_item is populated."""
    image = _get_str(item, "image")
    if image:
        return True
    images = item.get("_images")
    if isinstance(images, list) and any(isinstance(i, str) and i.strip() for i in images):
        return True
    return False


def _photos_uploaded(item: Dict[str, Any]) -> bool:
    """ebay_photos list or draft_listing.imageUrls list is non-empty."""
    ebay_photos = item.get("ebay_photos")
    if isinstance(ebay_photos, list) and len(ebay_photos) > 0:
        return True
    draft = item.get("draft_listing") or {}
    image_urls = draft.get("imageUrls")
    if isinstance(image_urls, list) and len(image_urls) > 0:
        return True
    return False


def _ai_identified(item: Dict[str, Any]) -> bool:
    """ebay_category_id is set or product_lookup dict is non-empty."""
    if _get_str(item, "ebay_category_id"):
        return True
    product_lookup = item.get("product_lookup")
    if isinstance(product_lookup, dict) and product_lookup:
        return True
    return False


def _draft_generated(item: Dict[str, Any]) -> bool:
    """draft_listing exists, has a title, and category_id is not '99'."""
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
    """ebay_listing.status == 'Active'."""
    listing = item.get("ebay_listing") or {}
    return listing.get("status") == "Active"


def _valid_condition(item: Dict[str, Any]) -> bool:
    """condition field is a known canonical value."""
    cond = _get_str(item, "condition")
    if not cond:
        return False
    return cond.lower() in _KNOWN_CONDITIONS


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
    "item_has_photos": _has_photos,
    "photos_uploaded": _photos_uploaded,
    "ai_identified": _ai_identified,
    "draft_generated": _draft_generated,
    "priced": _priced,
    "staged": _staged,
    "published": _published,
    "valid_condition": _valid_condition,
    "valid_category": _valid_category,
    "title_ok": _title_ok,
}

_ITEM_REASONS: dict[str, tuple[str, str]] = {
    "item_has_photos": ("photos present", "no photos"),
    "photos_uploaded": ("photos uploaded to eBay/draft", "photos not yet uploaded"),
    "ai_identified": ("AI category or product lookup present", "not yet AI-identified"),
    "draft_generated": ("draft with title and category", "no valid draft"),
    "priced": ("price set", "not priced"),
    "staged": ("offer created", "not staged"),
    "published": ("listing active", "not published"),
    "valid_condition": ("known condition", "unknown or missing condition"),
    "valid_category": ("leaf category", "invalid category (99 or missing)"),
    "title_ok": ("title within 80 chars", "title too long or missing"),
}

# Public API
# ---------------------------------------------------------------------------


def build_item_snapshot(
    item_json_path: str | Path,
    goal_profile: GoalProfile,
    *,
    external_effect_ambiguities: tuple[str, ...] = (),
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

    for condition_id in sorted(goal_profile.required):
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
        if checker(item):
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
