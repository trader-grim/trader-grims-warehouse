"""PP-PHOTOSYNC-001 P8 canary probe (todo #1124) — _diff()/aspects coverage.

Code-review fix (2026-07-04): _diff() previously only compared
title/price/photo_count even though both snapshots collected 'aspects' —
aspect-level drift/corruption would silently pass every canary run.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from photosync_canary_probe import _diff, _normalize_aspects  # noqa: E402


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
