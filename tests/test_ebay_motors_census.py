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
        ('tgwA', 'EBAY_MOTORS', '111'),
        ('tgwB', 'EBAY_US', '222'),
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
        ('tgwA', 'EBAY_MOTORS', '1'),
        ('tgwA', 'EBAY_MOTORS', '2'),
    ]
