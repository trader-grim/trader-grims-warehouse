"""PP-PHOTOSYNC-001 P8 canary probe (todo #1124) — _diff()/aspects coverage.

Code-review fix (2026-07-04): _diff() previously only compared
title/price/photo_count even though both snapshots collected 'aspects' —
aspect-level drift/corruption would silently pass every canary run.

audit#1143 #1210: _live_snapshot() stringified price (str(price)) while
_intent_snapshot() left it numeric (draft_listing/ebay_listing store price
as float — confirmed against ebay_stage.py/ebay_sync.py) — _diff() always
reported a spurious price mismatch on every priced item.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from photosync_canary_probe import _diff, _normalize_aspects, _normalize_price  # noqa: E402


def test_normalize_aspects_flattens_list_values_like_ebay_live():
    # ebay_live's inventory_item.product.aspects are list-valued
    assert _normalize_aspects({'Brand': ['Milton Bradley']}) == {'Brand': 'Milton Bradley'}


def test_normalize_aspects_keeps_bare_string_values_like_draft_listing():
    # draft_listing.item_specifics are bare strings
    assert _normalize_aspects({'Brand': 'Milton Bradley'}) == {'Brand': 'Milton Bradley'}


def test_diff_treats_equivalent_aspect_shapes_as_no_mismatch():
    intent = {'title': 't', 'price': '9.99', 'photo_count': 2,
             'aspects': {'Brand': 'Milton Bradley'}}
    live = {'title': 't', 'price': '9.99', 'photo_count': 2,
           'aspects': {'Brand': ['Milton Bradley']}}
    assert _diff(intent, live) == []


def test_diff_catches_real_aspect_drift():
    intent = {'title': 't', 'price': '9.99', 'photo_count': 2,
             'aspects': {'Brand': 'Milton Bradley'}}
    live = {'title': 't', 'price': '9.99', 'photo_count': 2,
           'aspects': {'Brand': ['Hasbro']}}
    mismatches = _diff(intent, live)
    assert len(mismatches) == 1
    assert 'aspects' in mismatches[0]


def test_normalize_price_treats_numeric_and_string_forms_as_equal():
    assert _normalize_price(9.99) == _normalize_price('9.99') == 9.99


def test_normalize_price_none_stays_none():
    assert _normalize_price(None) is None


def test_normalize_price_empty_string_treated_as_unpriced():
    # ISS-011: real item price fields hold '' for unpriced items.
    assert _normalize_price('') is None


def test_normalize_price_garbage_string_does_not_crash():
    # Any other unparseable value must not raise — returned as-is so _diff()
    # still reports a mismatch instead of crashing the whole canary run.
    assert _normalize_price('TBD') == 'TBD'


def test_diff_does_not_crash_on_unpriced_item_with_empty_string_price():
    intent = {'title': 't', 'price': '', 'photo_count': 2, 'aspects': {}}
    live = {'title': 't', 'price': None, 'photo_count': 2, 'aspects': {}}
    assert _diff(intent, live) == []


def test_diff_no_longer_flags_matching_price_across_numeric_types():
    # Regression for #1210: intent (draft_listing.price) is a real float;
    # live (ebay_listing.live_price) is also a float — before the fix,
    # _live_snapshot() stringified it, so this always mismatched.
    intent = {'title': 't', 'price': 9.99, 'photo_count': 2, 'aspects': {}}
    live = {'title': 't', 'price': 9.99, 'photo_count': 2, 'aspects': {}}
    assert _diff(intent, live) == []


def test_diff_still_catches_a_real_price_drift():
    intent = {'title': 't', 'price': 9.99, 'photo_count': 2, 'aspects': {}}
    live = {'title': 't', 'price': 12.50, 'photo_count': 2, 'aspects': {}}
    mismatches = _diff(intent, live)
    assert len(mismatches) == 1
    assert 'price' in mismatches[0]
