"""PP-WORKFLOW-001 / Todo #1967 — condition-derived photo-state convergence.

Offline coverage for the reconcile that pins ebay_photos / ebay_offer.photo_urls
/ ebay_submitted to the live listing imageUrls without touching listing content,
and for the terminal-listing gate that stops resync_photos from ever dispatching
an upload/stage for a sold item.
"""

from __future__ import annotations

import copy

from tgw.workflow.photo_reconcile import (
    listing_photo_terminal,
    live_image_urls,
    reconcile_photo_state_to_live,
)

_LIVE = [
    "https://i.ebayimg.com/00/s/x/z/LqUAAOSwwNNnu5PL/$_57.JPG",
    "https://i.ebayimg.com/00/s/x/z/Y~0AAOSw~uZnu5PM/$_57.JPG",
]
_DIVERGED = [
    "https://i.ebayimg.com/00/s/x/z/IdkAAeSwOiBqR2tI/$_12.JPG",
    "https://i.ebayimg.com/00/s/x/z/HqsAAeSwAotqR2tK/$_12.JPG",
]


def _sold_item():
    return {
        "sku": "tgw201809090907247",
        "status": "In Stock",  # never reconciled to the eBay sellout
        "title": "Santa Fe Decal",
        "description": "original description body",
        "ebay_listing": {"listing_id": "227407776039", "status": "Sold"},
        "draft_listing": {
            "title": "Santa Fe Decal",
            "description": "original description body",
            "price": 11.99,
            "imageUrls": list(_LIVE),
            "source": "ebay_live",
        },
        "ebay_offer": {
            "offer_id": "265223002018",
            "price": 11.99,
            "photo_urls": list(_DIVERGED),
            "price_comps": {"n": 3},
        },
        "ebay_photos": [
            {"local": "/data/tgw201809090907247/a.jpg", "url": _DIVERGED[0],
             "provider_effect_id": "abc"},
            {"local": "/data/tgw201809090907247/b.jpg", "url": _DIVERGED[1],
             "provider_effect_id": "def"},
        ],
        "ebay_submitted": {
            "inventory_item": {
                "condition": "USED_EXCELLENT",
                "product": {"title": "Santa Fe Decal", "imageUrls": list(_DIVERGED)},
            },
            "staged_at": "2026-08-31T23:06:24Z",
        },
        "ebay_live": {
            "inventory_item": {"product": {"imageUrls": list(_LIVE),
                                           "title": "Santa Fe Decal"}},
        },
    }


def test_live_image_urls_reads_the_mirror():
    assert live_image_urls(_sold_item()) == _LIVE
    assert live_image_urls({"ebay_live": {}}) == []
    assert live_image_urls({}) == []


def test_listing_photo_terminal_true_for_sold_listing_with_stale_local_status():
    assert listing_photo_terminal(_sold_item()) is True


def test_listing_photo_terminal_false_for_a_live_available_item():
    item = _sold_item()
    item["ebay_listing"]["status"] = "Active"
    item["draft_listing"]["quantity"] = 1
    assert listing_photo_terminal(item) is False


def test_reconcile_pins_every_photo_projection_to_live():
    item = _sold_item()
    before = copy.deepcopy(item)
    patch = reconcile_photo_state_to_live(item)

    assert set(patch) == {"ebay_photos", "ebay_offer", "ebay_submitted"}
    assert [e["url"] for e in patch["ebay_photos"]] == _LIVE
    # local file mapping and per-entry metadata are preserved, only url realigned
    assert [e["local"] for e in patch["ebay_photos"]] == [
        e["local"] for e in before["ebay_photos"]
    ]
    assert patch["ebay_offer"]["photo_urls"] == _LIVE
    assert patch["ebay_offer"]["price_comps"] == {"n": 3}  # untouched
    assert (
        patch["ebay_submitted"]["inventory_item"]["product"]["imageUrls"] == _LIVE
    )
    # input dict is not mutated
    assert item == before


def test_reconcile_never_touches_listing_content():
    item = _sold_item()
    patch = reconcile_photo_state_to_live(item)
    assert "draft_listing" not in patch
    assert "title" not in patch and "description" not in patch
    assert patch["ebay_offer"]["price"] == 11.99
    assert patch["ebay_submitted"]["inventory_item"]["condition"] == "USED_EXCELLENT"
    assert (
        patch["ebay_submitted"]["inventory_item"]["product"]["title"]
        == "Santa Fe Decal"
    )


def test_reconcile_is_a_noop_when_already_converged():
    item = _sold_item()
    for entry, url in zip(item["ebay_photos"], _LIVE):
        entry["url"] = url
    item["ebay_offer"]["photo_urls"] = list(_LIVE)
    item["ebay_submitted"]["inventory_item"]["product"]["imageUrls"] = list(_LIVE)
    assert reconcile_photo_state_to_live(item) == {}


def test_reconcile_refuses_when_no_live_photo_set():
    item = _sold_item()
    item["ebay_live"] = {"inventory_item": {"product": {}}}
    assert reconcile_photo_state_to_live(item) == {}


def test_reconcile_refuses_on_photo_count_mismatch():
    item = _sold_item()
    item["ebay_photos"] = item["ebay_photos"][:1]  # 1 local vs 2 live urls
    assert reconcile_photo_state_to_live(item) == {}


def test_reconcile_refuses_when_a_stray_hosted_row_is_present():
    item = _sold_item()
    item["ebay_photos"].append(
        {"local": "/data/tgw201809090907247/removed.jpg", "url": "https://x/removed"}
    )  # 3 rows, 2 with a real local file, 2 live urls
    # mapped==3 (all have local) != 2 live urls -> refuse
    assert reconcile_photo_state_to_live(item) == {}
