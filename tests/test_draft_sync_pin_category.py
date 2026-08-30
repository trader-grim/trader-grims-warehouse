"""todo #1931 (corrected model): Reset Draft is a FULL reverse sync
(live → draft, start-over) — the draft holds what we WANT the offer to become,
so abandoning it means re-pinning everything, category included. Previously
pin_draft_to_live never touched category, so a corrupt draft category (the
persisted "99" sentinel) could not be restored by Reset Draft at all.

These tests cover the pure pin primitive. All eBay API calls are mocked —
tests pass completely offline.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from tgw.draft_sync import pin_draft_to_live


def _live_doc(category_id: str = "262310", ebay_offer_cat: str = "262310") -> Dict[str, Any]:
    return {
        "sku": "tgwRESET1",
        "draft_listing": {"title": "abandoned draft", "category_id": "99"},
        "ebay_offer": {"offer_id": "O1", "status": "PUBLISHED", "category_id": ebay_offer_cat},
        "ebay_live": {
            "offer": {
                "offerId": "O1",
                "categoryId": category_id,
                "pricingSummary": {"price": {"value": "9.99"}},
                "availableQuantity": 1,
            },
            "inventory_item": {
                "product": {"title": "Live Widget", "description": "desc"},
                "condition": "USED_GOOD",
            },
        },
    }


def test_pin_includes_live_category_when_draft_has_sentinel():
    fields = pin_draft_to_live(_live_doc())
    assert fields["draft_listing"]["category_id"] == "262310"


def test_pin_updates_category_when_draft_diverged():
    doc = _live_doc()
    doc["draft_listing"]["category_id"] = "111"
    fields = pin_draft_to_live(doc)
    assert fields["draft_listing"]["category_id"] == "262310"


def test_pin_falls_back_to_ebay_offer_category_when_live_offer_stale():
    # ebay_live.offer is missing categoryId (stale mirror) — the synced
    # ebay_offer.category_id is still eBay-derived truth and must win.
    doc = _live_doc(category_id="")
    fields = pin_draft_to_live(doc)
    assert fields["draft_listing"]["category_id"] == "262310"


def test_pin_keeps_existing_category_when_no_eBay_category_anywhere():
    # Never invented, never cleared: no live category anywhere → the draft's
    # own category is left untouched.
    doc = _live_doc(category_id="", ebay_offer_cat="")
    fields = pin_draft_to_live(doc)
    assert fields["draft_listing"]["category_id"] == "99"


def test_pin_still_raises_without_mirror():
    from tgw.draft_sync import pin_draft_to_live as _pin

    with pytest.raises(ValueError):
        _pin({"sku": "x", "draft_listing": {}})
