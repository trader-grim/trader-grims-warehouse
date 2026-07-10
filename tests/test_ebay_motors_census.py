"""PP-EBAY-MOTORS-001 census from R1.8 capture (todo #1131)."""

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from ebay_motors_census import _iter_offer_records  # noqa: E402


def _write_capture(path, records):
    with open(path, 'ab') as fh:
        for rec in records:
            fh.write(gzip.compress((json.dumps(rec) + '\n').encode('utf-8')))


def test_iter_offer_records_extracts_sku_marketplace_offer_id(tmp_path):
    _write_capture(tmp_path / '2026-07-04.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '111'}]})},
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwB', 'marketplaceId': 'EBAY_US', 'offerId': '222'}]})},
        {'name': 'GET /sell/inventory/v1/inventory_item', 'body': json.dumps({
            'inventoryItems': [{'sku': 'tgwC'}]})},  # not an offer record, must be ignored
    ])
    results = sorted(_iter_offer_records(tmp_path))
    assert results == [
        ('tgwA', 'EBAY_MOTORS', '111', '2026-07-04'),
        ('tgwB', 'EBAY_US', '222', '2026-07-04'),
    ]


def test_iter_offer_records_skips_offers_without_sku_or_marketplace(tmp_path):
    _write_capture(tmp_path / '2026-07-04.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA'}, {'marketplaceId': 'EBAY_US'}]})},
    ])
    assert list(_iter_offer_records(tmp_path)) == []


def test_iter_offer_records_reads_across_multiple_daily_files(tmp_path):
    _write_capture(tmp_path / '2026-07-03.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '1'}]})},
    ])
    _write_capture(tmp_path / '2026-07-04.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '2'}]})},
    ])
    results = sorted(_iter_offer_records(tmp_path))
    assert results == [
        ('tgwA', 'EBAY_MOTORS', '1', '2026-07-03'),
        ('tgwA', 'EBAY_MOTORS', '2', '2026-07-04'),
    ]


# ---------------------------------------------------------------------------
# audit#1143 #1214 — recency + ambiguity fixes in main()'s apply logic
# ---------------------------------------------------------------------------
#
# main() isn't easily unit-testable in isolation (argparse + load_config +
# the fence), so these tests exercise the same recency/ambiguity decision
# logic main() uses, built from _iter_offer_records() output — the actual
# bug lived in how that data was reduced to "which SKUs get patched", not in
# _iter_offer_records() itself.

def _reduce(records):
    """Mirror main()'s reduction of _iter_offer_records() output into
    (sku_marketplaces, sku_latest) — the two structures the fix depends on."""
    from collections import defaultdict
    sku_marketplaces = defaultdict(set)
    sku_latest = {}
    for sku, mkt, _offer_id, capture_date in records:
        sku_marketplaces[sku].add(mkt)
        prev = sku_latest.get(sku)
        if prev is None or capture_date >= prev[0]:
            sku_latest[sku] = (capture_date, mkt)
    return sku_marketplaces, sku_latest


def test_motors_skus_uses_most_recent_capture_not_ever_seen(tmp_path):
    # tgwA was EBAY_MOTORS on 07-03 but relisted to EBAY_US by 07-05 — the
    # old "ever seen as EBAY_MOTORS" logic would still call this a Motors
    # SKU. The fix must not.
    _write_capture(tmp_path / '2026-07-03.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '1'}]})},
    ])
    _write_capture(tmp_path / '2026-07-05.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_US', 'offerId': '1'}]})},
    ])
    records = list(_iter_offer_records(tmp_path))
    _sku_marketplaces, sku_latest = _reduce(records)
    motors_skus = [sku for sku, (_, mkt) in sku_latest.items() if mkt == 'EBAY_MOTORS']
    assert motors_skus == []  # tgwA's LATEST marketplace is EBAY_US, not Motors


def test_ambiguous_cross_marketplace_sku_excluded_from_safe_set(tmp_path):
    # tgwA's most recent capture says EBAY_MOTORS, but it was ALSO seen
    # under EBAY_US at some point — the census itself calls this ambiguous
    # and "needs human review, not auto-resolution". Must be excluded from
    # the safe-to-patch set even though the latest record says Motors.
    _write_capture(tmp_path / '2026-07-03.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_US', 'offerId': '1'}]})},
    ])
    _write_capture(tmp_path / '2026-07-05.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '1'}]})},
    ])
    records = list(_iter_offer_records(tmp_path))
    sku_marketplaces, sku_latest = _reduce(records)
    motors_skus = sorted(sku for sku, (_, mkt) in sku_latest.items() if mkt == 'EBAY_MOTORS')
    multi_marketplace = [sku for sku, mkts in sku_marketplaces.items() if len(mkts) > 1]
    ambiguous = sorted(set(motors_skus) & set(multi_marketplace))
    safe = sorted(set(motors_skus) - set(ambiguous))

    assert motors_skus == ['tgwA']
    assert ambiguous == ['tgwA']
    assert safe == []  # must NOT be silently auto-patched


def test_unambiguous_motors_sku_stays_in_safe_set(tmp_path):
    # Control: a SKU seen ONLY under EBAY_MOTORS across every capture must
    # still be treated as safe to patch — the fix shouldn't over-exclude.
    _write_capture(tmp_path / '2026-07-03.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '1'}]})},
    ])
    _write_capture(tmp_path / '2026-07-05.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/offer', 'body': json.dumps({
            'offers': [{'sku': 'tgwA', 'marketplaceId': 'EBAY_MOTORS', 'offerId': '1'}]})},
    ])
    records = list(_iter_offer_records(tmp_path))
    sku_marketplaces, sku_latest = _reduce(records)
    motors_skus = sorted(sku for sku, (_, mkt) in sku_latest.items() if mkt == 'EBAY_MOTORS')
    multi_marketplace = [sku for sku, mkts in sku_marketplaces.items() if len(mkts) > 1]
    ambiguous = sorted(set(motors_skus) & set(multi_marketplace))
    safe = sorted(set(motors_skus) - set(ambiguous))

    assert safe == ['tgwA']
    assert ambiguous == []
