"""PP-WORKFLOW-001 / Todo #1967 — condition-derived photo-state convergence.

Offline coverage for the reconcile that pins ebay_photos / ebay_offer.photo_urls
/ ebay_submitted to the live listing imageUrls without touching listing content,
and for the terminal-listing gate that stops resync_photos from ever dispatching
an upload/stage for a sold item.
"""

from __future__ import annotations

import copy

import pytest

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


@pytest.fixture
def sku_dir(tmp_path):
    """A SKU photo directory with two real files, primary first."""
    d = tmp_path / "tgw201809090907247"
    d.mkdir()
    (d / "001.jpg").write_bytes(b"one")
    (d / "002.jpg").write_bytes(b"two")
    return d


def _sold_item(sku_dir):
    return {
        "sku": "tgw201809090907247",
        "status": "In Stock",  # never reconciled to the eBay sellout
        "title": "Santa Fe Decal",
        "description": "original description body",
        "photo_order": ["001.jpg", "002.jpg"],
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
            {"local": str(sku_dir / "001.jpg"), "url": _DIVERGED[0],
             "provider_effect_id": "abc"},
            {"local": str(sku_dir / "002.jpg"), "url": _DIVERGED[1],
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


def test_live_image_urls_reads_the_mirror(sku_dir):
    assert live_image_urls(_sold_item(sku_dir)) == _LIVE
    assert live_image_urls({"ebay_live": {}}) == []
    assert live_image_urls({}) == []


def test_listing_photo_terminal_true_for_sold_listing_with_stale_local_status(sku_dir):
    assert listing_photo_terminal(_sold_item(sku_dir)) is True


def test_listing_photo_terminal_false_for_a_live_available_item(sku_dir):
    item = _sold_item(sku_dir)
    item["ebay_listing"]["status"] = "Active"
    item["draft_listing"]["quantity"] = 1
    assert listing_photo_terminal(item) is False


def test_listing_photo_terminal_false_for_an_ended_but_unsold_listing(sku_dir):
    # #1967 review: only 'Sold' is terminal — an ended-but-unsold listing is
    # relist-eligible and must stay in the listing pipeline.
    item = _sold_item(sku_dir)
    item["ebay_listing"]["status"] = "Ended"
    item["draft_listing"]["quantity"] = 1
    assert listing_photo_terminal(item) is False


def test_reconcile_pins_every_photo_projection_to_live(sku_dir):
    item = _sold_item(sku_dir)
    before = copy.deepcopy(item)
    patch = reconcile_photo_state_to_live(item, sku_dir)

    assert set(patch) == {"ebay_photos", "ebay_offer", "ebay_submitted"}
    assert [e["url"] for e in patch["ebay_photos"]] == _LIVE
    # local file mapping and per-entry metadata are preserved, only url realigned
    assert [e["local"] for e in patch["ebay_photos"]] == [
        e["local"] for e in before["ebay_photos"]
    ]
    assert patch["ebay_photos"][0]["provider_effect_id"] == "abc"
    assert patch["ebay_offer"]["photo_urls"] == _LIVE
    assert patch["ebay_offer"]["price_comps"] == {"n": 3}  # untouched
    assert (
        patch["ebay_submitted"]["inventory_item"]["product"]["imageUrls"] == _LIVE
    )
    # input dict is not mutated
    assert item == before


def test_reconcile_aligns_by_local_file_order_not_raw_list_order(sku_dir):
    # ebay_photos rows in the *opposite* order to photo_order (a partial upload
    # that never ran the _reorder step). Each row must still get the live URL
    # for its own file's display position — the pairing _photo_sync_state gates
    # on — not live_urls[i] by raw list index.
    item = _sold_item(sku_dir)
    item["ebay_photos"] = [
        {"local": str(sku_dir / "002.jpg"), "url": _DIVERGED[1]},
        {"local": str(sku_dir / "001.jpg"), "url": _DIVERGED[0]},
    ]
    patch = reconcile_photo_state_to_live(item, sku_dir)
    by_local = {
        e["local"].rsplit("/", 1)[-1]: e["url"] for e in patch["ebay_photos"]
    }
    assert by_local == {"001.jpg": _LIVE[0], "002.jpg": _LIVE[1]}


def test_reconcile_never_touches_listing_content(sku_dir):
    item = _sold_item(sku_dir)
    patch = reconcile_photo_state_to_live(item, sku_dir)
    assert "draft_listing" not in patch
    assert "title" not in patch and "description" not in patch
    assert patch["ebay_offer"]["price"] == 11.99
    assert patch["ebay_submitted"]["inventory_item"]["condition"] == "USED_EXCELLENT"
    assert (
        patch["ebay_submitted"]["inventory_item"]["product"]["title"]
        == "Santa Fe Decal"
    )


def test_reconcile_is_a_noop_when_already_converged(sku_dir):
    item = _sold_item(sku_dir)
    for entry, url in zip(item["ebay_photos"], _LIVE):
        entry["url"] = url
    item["ebay_offer"]["photo_urls"] = list(_LIVE)
    item["ebay_submitted"]["inventory_item"]["product"]["imageUrls"] = list(_LIVE)
    assert reconcile_photo_state_to_live(item, sku_dir) == {}


def test_reconcile_refuses_when_no_live_photo_set(sku_dir):
    item = _sold_item(sku_dir)
    item["ebay_live"] = {"inventory_item": {"product": {}}}
    assert reconcile_photo_state_to_live(item, sku_dir) == {}


def test_reconcile_refuses_when_draft_imageurls_also_diverged(sku_dir):
    # #1967 review: acceptance requires draft_listing.imageUrls == live too, and
    # this module never rewrites listing content — refuse rather than emit a
    # false "reconciled" result.
    item = _sold_item(sku_dir)
    item["draft_listing"]["imageUrls"] = list(_DIVERGED)
    assert reconcile_photo_state_to_live(item, sku_dir) == {}


def test_reconcile_refuses_on_photo_count_mismatch(sku_dir):
    item = _sold_item(sku_dir)
    item["ebay_photos"] = item["ebay_photos"][:1]  # 1 hosted row vs 2 live urls
    assert reconcile_photo_state_to_live(item, sku_dir) == {}


def test_reconcile_refuses_when_a_hosted_row_has_no_matching_local_file(sku_dir):
    # count lines up (2 rows / 2 live urls / 2 files) but one row points at a
    # file that is not in the item's photo set -> not a clean 1:1, refuse.
    item = _sold_item(sku_dir)
    item["ebay_photos"][1]["local"] = str(sku_dir / "ghost.jpg")
    assert reconcile_photo_state_to_live(item, sku_dir) == {}


def test_reconcile_refuses_on_duplicate_local_rows(sku_dir):
    item = _sold_item(sku_dir)
    item["ebay_photos"][1]["local"] = item["ebay_photos"][0]["local"]
    assert reconcile_photo_state_to_live(item, sku_dir) == {}
