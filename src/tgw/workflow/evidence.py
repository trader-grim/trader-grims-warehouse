"""Pure evidence source functions for coding and TGW workflow conditions.

Each function takes raw, observable data and returns an EvidenceAssertion —
never reads ambient state, never mutates anything.  The contracts module
provides the immutable types (EvidenceAssertion, EvidenceReference,
FingerprintResult).
"""

from __future__ import annotations

from typing import Any

from tgw.workflow_kernel.contracts import EvidenceAssertion, EvidenceReference, FingerprintResult

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ref(condition_id: str, source_class: str, *, generation: str = "1") -> EvidenceReference:
    """Create a standard evidence reference bound to a condition."""
    return EvidenceReference(
        identity=condition_id,
        source_class=source_class,
        source_generation=generation,
    )


# ---------------------------------------------------------------------------
# Generic factory
# ---------------------------------------------------------------------------

def assert_condition(
    condition_id: str,
    value: Any,
    *,
    reasons: tuple[str, ...] = (),
    evidence: tuple[EvidenceReference, ...] = (),
) -> EvidenceAssertion:
    """Build an EvidenceAssertion from an already-computed FingerprintResult.

    This is the generic fallback — callers that have already decided the
    result pass it directly.
    """
    if not isinstance(value, FingerprintResult):
        raise TypeError(
            f"value must be a FingerprintResult, got {type(value).__name__}"
        )
    return EvidenceAssertion(
        condition_id=condition_id,
        result=value,
        reasons=reasons,
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Coding evidence sources
# ---------------------------------------------------------------------------

def assert_tested(
    pytest_exit_code: int | None,
    summary: str,
) -> EvidenceAssertion:
    """Evidence that a code change passed its test suite.

    * TRUE  — exit_code 0
    * FALSE — exit_code non-zero (tests failed)
    * UNKNOWN — no exit_code (no test run attempted)
    """
    _cond = "tested"
    _ev = (_ref(_cond, "pytest"),)
    if pytest_exit_code is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no test run attempted",), ())
    if pytest_exit_code == 0:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, (summary,), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, (f"exit_code={pytest_exit_code}; {summary}",), _ev)


def assert_linted(ruff_exit_code: int | None) -> EvidenceAssertion:
    """Evidence that code passes lint checks.

    * TRUE  — exit_code 0
    * FALSE — exit_code non-zero
    * UNKNOWN — no exit_code
    """
    _cond = "linted"
    _ev = (_ref(_cond, "ruff"),)
    if ruff_exit_code is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no lint run attempted",), ())
    if ruff_exit_code == 0:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("ruff passed clean",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, (f"ruff exit_code={ruff_exit_code}",), _ev)


def assert_reviewed(review_verdict: str | None) -> EvidenceAssertion:
    """Evidence that code has been reviewed.

    * TRUE  — 'approved'
    * FALSE — 'changes_requested'
    * UNKNOWN — None or unrecognised verdict
    """
    _cond = "reviewed"
    _ev = (_ref(_cond, "code_review"),)
    if review_verdict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no review verdict available",), ())
    if review_verdict == "approved":
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("review approved",), _ev)
    if review_verdict == "changes_requested":
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("changes requested",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, (f"unrecognised verdict: {review_verdict}",), _ev)


def assert_controller_verified(controller_pass: bool | None) -> EvidenceAssertion:
    """Evidence that a controller-level verification passed.

    * TRUE  — controller_pass is True
    * FALSE — controller_pass is False
    * UNKNOWN — controller_pass is None
    """
    _cond = "controller_verified"
    _ev = (_ref(_cond, "controller"),)
    if controller_pass is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("controller verification not run",), ())
    if controller_pass:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("controller verification passed",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("controller verification failed",), _ev)


def assert_implemented(git_diff_nonempty: bool | None) -> EvidenceAssertion:
    """Evidence that the implementation exists (non-empty diff).

    * TRUE  — diff is non-empty
    * FALSE — diff is empty (no changes)
    * UNKNOWN — cannot determine
    """
    _cond = "implemented"
    _ev = (_ref(_cond, "git_diff"),)
    if git_diff_nonempty is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("cannot determine diff status",), ())
    if git_diff_nonempty:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("non-empty diff present",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("empty diff — no changes",), _ev)


# ---------------------------------------------------------------------------
# TGW item evidence sources
# ---------------------------------------------------------------------------

def _has_image_field(item_dict: dict[str, Any]) -> bool:
    """Check whether the item dict contains a non-empty image/photo field."""
    for key in ("image", "images", "photos"):
        val = item_dict.get(key)
        if val:
            if isinstance(val, str) and val.strip():
                return True
            if isinstance(val, (list, tuple)) and len(val) > 0:
                return True
    return False


def assert_item_has_photos(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that an item has photos (image field populated).

    * TRUE    — image/images/photos field is non-empty
    * FALSE   — field present but empty
    * UNKNOWN — item_dict is None or no image field at all
    * NOT_APPLICABLE — item not listed / not an inventory item
    """
    _cond = "item_has_photos"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    # NOT_APPLICABLE: non-listed items
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())
    if _has_image_field(item_dict):
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("image field populated",), _ev)
    for key in ("image", "images", "photos"):
        if key in item_dict:
            return EvidenceAssertion(_cond, FingerprintResult.FALSE, (f"'{key}' field empty",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no image field found",), ())


def assert_photos_uploaded(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that photos have been uploaded to the marketplace.

    Checks ebay_photos or draft_listing.imageUrls.

    * TRUE    — photos uploaded (non-empty URL list)
    * FALSE   — upload attempted but empty/no URLs
    * UNKNOWN — cannot determine
    * NOT_APPLICABLE — not listed
    """
    _cond = "photos_uploaded"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    ebay_photos = item_dict.get("ebay_photos")
    if ebay_photos and isinstance(ebay_photos, (list, tuple)) and len(ebay_photos) > 0:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("ebay_photos present",), _ev)

    draft = item_dict.get("draft_listing") or {}
    image_urls = draft.get("imageUrls") or draft.get("image_urls")
    if image_urls and isinstance(image_urls, (list, tuple)) and len(image_urls) > 0:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("draft_listing.imageUrls present",), _ev)

    if ebay_photos is not None or image_urls is not None:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("photos field present but empty",), _ev)

    return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no photo upload evidence found",), ())


def assert_ai_identified(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that AI identification completed (category + title assigned).

    Checks ebay_category_id + title.

    * TRUE    — ebay_category_id present and title non-empty
    * FALSE   — fields present but empty
    * UNKNOWN — cannot determine
    * NOT_APPLICABLE — not listed
    """
    _cond = "ai_identified"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    cat_id = item_dict.get("ebay_category_id")
    title = item_dict.get("title") or item_dict.get("ebay_title", "")

    has_cat = cat_id is not None and cat_id != "" and cat_id != "0"
    has_title = bool(title and title.strip())

    if has_cat and has_title:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("category and title assigned",), _ev)
    if not has_cat and not has_title:
        if "ebay_category_id" in item_dict or "title" in item_dict or "ebay_title" in item_dict:
            return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("category and title missing",), _ev)
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no ai identification data found",), ())
    if not has_cat:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("ebay_category_id missing",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("title missing",), _ev)


def assert_draft_generated(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that an eBay draft listing was generated.

    Checks draft_listing exists and category != '99' (invalid/missing).

    * TRUE    — draft exists with valid category
    * FALSE   — draft exists but category is '99' (invalid)
    * UNKNOWN — no draft found
    * NOT_APPLICABLE — not listed
    """
    _cond = "draft_generated"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    draft = item_dict.get("draft_listing")
    if draft is None or not isinstance(draft, dict) or len(draft) == 0:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no draft_listing found",), ())

    cat = draft.get("category") or draft.get("ebay_category_id", "")
    if cat == "99" or cat == "" or cat is None:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("draft category invalid (99 or missing)",), _ev)

    return EvidenceAssertion(_cond, FingerprintResult.TRUE, ("draft generated with valid category",), _ev)


def assert_priced(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that the item has been priced.

    Checks draft_listing.price non-null > 0 and no price_drift.

    * TRUE    — price set and no drift
    * FALSE   — price missing, zero, or negative
    * STALE   — price_drift detected (staged_price != draft price)
    * CONTRADICTORY — pipeline_error contradicts pricing
    * UNKNOWN — cannot determine
    * NOT_APPLICABLE — not listed
    """
    _cond = "priced"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    # Check for pipeline_error that contradicts pricing
    pipeline_error = item_dict.get("pipeline_error")
    if pipeline_error and "price" in str(pipeline_error).lower():
        return EvidenceAssertion(_cond, FingerprintResult.CONTRADICTORY, (f"pipeline_error contradicts pricing: {pipeline_error}",), _ev)

    draft = item_dict.get("draft_listing") or {}
    draft_price = draft.get("price")

    # Check for price_drift (staged_price != draft price)
    staged_price = item_dict.get("staged_price")
    if staged_price is not None and draft_price is not None:
        try:
            if float(staged_price) != float(draft_price):
                return EvidenceAssertion(
                    _cond,
                    FingerprintResult.STALE,
                    (f"price_drift: staged={staged_price} != draft={draft_price}",),
                    _ev,
                )
        except (TypeError, ValueError):
            pass

    if draft_price is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no draft price found",), ())
    try:
        price_val = float(draft_price)
    except (TypeError, ValueError):
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, (f"unparseable draft price: {draft_price}",), ())
    if price_val <= 0:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, (f"draft price not positive: {draft_price}",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.TRUE, (f"priced at {draft_price}",), _ev)


def assert_staged(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that the item has been staged (offer created on marketplace).

    Checks ebay_offer.offer_id.

    * TRUE    — offer_id present
    * FALSE   — ebay_offer present but no offer_id
    * UNKNOWN — no ebay_offer data
    * NOT_APPLICABLE — not listed
    """
    _cond = "staged"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    offer = item_dict.get("ebay_offer")
    if offer is None or not isinstance(offer, dict):
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no ebay_offer data",), ())
    offer_id = offer.get("offer_id")
    if offer_id is not None and offer_id != "":
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, (f"offer_id={offer_id}",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("ebay_offer present but no offer_id",), _ev)


def assert_published(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that the item is published (live on marketplace).

    Recognizes the canonical Inventory and Trading/API live-state shapes.

    * TRUE    — status is 'Active'
    * FALSE   — ebay_listing present but status != Active
    * UNKNOWN — no ebay_listing data
    * NOT_APPLICABLE — not listed
    """
    _cond = "published"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    ebay_listing = item_dict.get("ebay_listing")
    if ebay_listing is None or not isinstance(ebay_listing, dict):
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("no ebay_listing data",), ())
    statuses = (
        ebay_listing.get("status"),
        ebay_listing.get("listing_status"),
        ebay_listing.get("listingStatus"),
    )
    active = next(
        (
            status
            for status in statuses
            if str(status or "").strip().upper() in {"ACTIVE", "PUBLISHED"}
        ),
        None,
    )
    if active is not None:
        return EvidenceAssertion(
            _cond, FingerprintResult.TRUE, (f"listing is {active}",), _ev
        )
    status = next((status for status in statuses if status not in (None, "")), "")
    return EvidenceAssertion(
        _cond,
        FingerprintResult.FALSE,
        (f"listing status is '{status}' (not Active or PUBLISHED)",),
        _ev,
    )


def assert_valid_condition(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that the item condition field is a known canonical value.

    Checks the condition field against the known eBay condition names.

    * TRUE    — condition is a known canonical value
    * FALSE   — condition present but not in known set
    * UNKNOWN — condition field missing, empty, or no item data
    * NOT_APPLICABLE — not listed
    """
    _cond = "valid_condition"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    _KNOWN_CONDITIONS = {
        "new", "new other", "new with defects", "new without tags",
        "manufacturer refurbished", "seller refurbished", "used",
        "for parts or not working", "good", "acceptable",
        "like new", "very good", "new with box",
    }
    cond = (item_dict.get("condition") or "").strip()
    if not cond:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("condition field empty or missing",), _ev)
    if cond.lower() in _KNOWN_CONDITIONS:
        return EvidenceAssertion(_cond, FingerprintResult.TRUE, (f"known condition: {cond}",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.FALSE, (f"unknown condition: {cond}",), _ev)


def assert_valid_category(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that ebay_category_id is a leaf category (not "99" or empty).

    * TRUE    — ebay_category_id present and not "99"
    * FALSE   — ebay_category_id is "99" or empty
    * UNKNOWN — no ebay_category_id field, or no item data
    * NOT_APPLICABLE — not listed
    """
    _cond = "valid_category"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    cat = (item_dict.get("ebay_category_id") or "").strip()
    if not cat:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("ebay_category_id empty or missing",), _ev)
    if cat == "99":
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("ebay_category_id is invalid (99)",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.TRUE, (f"valid category: {cat}",), _ev)


def assert_title_ok(item_dict: dict[str, Any] | None) -> EvidenceAssertion:
    """Evidence that draft_listing.title is present and within 80 characters.

    * TRUE    — title present and 1-80 characters
    * FALSE   — title missing, empty, or > 80 characters
    * UNKNOWN — no item data
    * NOT_APPLICABLE — not listed
    """
    _cond = "title_ok"
    _ev = (_ref(_cond, "item_data"),)
    if item_dict is None:
        return EvidenceAssertion(_cond, FingerprintResult.UNKNOWN, ("item data unavailable",), ())
    listing_status = item_dict.get("listing_status") or item_dict.get("status", "")
    if listing_status in ("not_listed", "unlisted", "archived"):
        return EvidenceAssertion(_cond, FingerprintResult.NOT_APPLICABLE, ("item not listed",), ())

    draft = item_dict.get("draft_listing") or {}
    title = draft.get("title")
    if not isinstance(title, str) or not title.strip():
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, ("title missing or empty",), _ev)
    if len(title) > 80:
        return EvidenceAssertion(_cond, FingerprintResult.FALSE, (f"title too long ({len(title)} chars, max 80)",), _ev)
    return EvidenceAssertion(_cond, FingerprintResult.TRUE, (f"title ok ({len(title)} chars)",), _ev)
