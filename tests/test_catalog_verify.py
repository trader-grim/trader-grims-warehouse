"""Tests for PP-VERIFY-001 catalog-verify command and PP-DEADLETTER-001 classify_dead_letter."""
import json
from pathlib import Path

from tgw.api import _verify_item, cmd_catalog_verify
from tgw.queue.worker_base import classify_dead_letter

# ---------------------------------------------------------------------------
# classify_dead_letter tests
# ---------------------------------------------------------------------------

def test_classify_dead_letter_token_expired():
    action, delay = classify_dead_letter("eBay token is expired — re-auth required")
    assert action == 'requeue'
    assert delay == 900


def test_classify_dead_letter_no_photos_yet():
    action, delay = classify_dead_letter("RuntimeError: no eBay photo URLs yet for this item")
    assert action == 'requeue'
    assert delay == 600


def test_classify_dead_letter_directory_not_empty():
    action, delay = classify_dead_letter("OSError: [Errno 39] Directory not empty: '/opt/TGW/data/...'")
    assert action == 'requeue'
    assert delay == 30


def test_classify_dead_letter_hard_failure():
    action, delay = classify_dead_letter("HardFailure: no ebay_category_id found for SKU")
    assert action == 'dead_letter'
    assert delay == 0


def test_classify_dead_letter_case_insensitive():
    action, delay = classify_dead_letter("ReadTimeout occurred during Browse API call")
    assert action == 'requeue'


def test_classify_dead_letter_unknown_error():
    action, delay = classify_dead_letter("ValueError: unexpected None in price computation")
    assert action == 'dead_letter'


# ---------------------------------------------------------------------------
# _verify_item tests (unit tests on the rule engine)
# ---------------------------------------------------------------------------

def _make_item(tmp_path: Path, sku: str, doc: dict, photos: int = 1) -> tuple[Path, dict]:
    item_dir = tmp_path / sku
    item_dir.mkdir()
    for i in range(photos):
        (item_dir / f'photo{i}.jpg').write_bytes(b'')
    jf = item_dir / f'{sku}.json'
    jf.write_text(json.dumps(doc), encoding='utf-8')
    return item_dir, doc


def test_verify_clean_item(tmp_path):
    sku = 'tgw202601010000001'
    doc = {
        'sku': sku,
        'title': 'Nice USB Widget 3.0 High Speed',
        'location': 'SHELF01',
        'ebay_category_id': '12345',
        'verified': '20260101',
        '#STATUS': 'In Stock',
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=2)
    viols = _verify_item(sku, item_dir, doc)
    assert viols == [], f'Expected no violations, got: {viols}'


def test_verify_no_title(tmp_path):
    sku = 'tgw202601010000002'
    doc = {'sku': sku, 'title': '', 'location': 'SHELF01'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_title' in rules


def test_verify_template_prefix(tmp_path):
    sku = 'tgw202601010000003'
    doc = {'sku': sku, 'title': 'TEMPLATE:electronics some stuff', 'location': 'A1'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'stale_template_prefix' in rules


def test_verify_no_photo(tmp_path):
    sku = 'tgw202601010000004'
    doc = {'sku': sku, 'title': 'Some Item with No Photo Here', 'location': 'B2'}
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=0)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_photo' in rules


def test_verify_bad_verified_date(tmp_path):
    sku = 'tgw202601010000005'
    doc = {'sku': sku, 'title': 'Valid Title For Testing', 'location': 'C3',
           'verified': 'unverified'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'bad_verified_date' in rules


def test_verify_invalid_category_id(tmp_path):
    sku = 'tgw202601010000006'
    doc = {'sku': sku, 'title': 'Valid Title For Testing', 'location': 'D4',
           'ebay_category_id': 'NOT_NUMERIC'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'invalid_ebay_category' in rules


def test_verify_no_location(tmp_path):
    sku = 'tgw202601010000007'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Here', 'location': ''}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_location' in rules


def test_verify_title_too_short(tmp_path):
    sku = 'tgw202601010000008'
    doc = {'sku': sku, 'title': 'Widget', 'location': 'E5'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'title_too_short' in rules


def test_verify_unknown_status(tmp_path):
    sku = 'tgw202601010000009'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Please', 'location': 'F6',
           '#STATUS': 'MysteryStatus999'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'unknown_status' in rules


# ---------------------------------------------------------------------------
# cmd_catalog_verify integration test
# ---------------------------------------------------------------------------

def test_cmd_catalog_verify(tmp_path, capsys):
    """cmd_catalog_verify scans a mock ItemData tree and reports violations."""
    itemdata = tmp_path / 'ItemData'
    itemdata.mkdir()
    catalog_root = tmp_path / 'catalog'
    catalog_root.mkdir()

    sku_good = 'tgw202601010000010'
    sku_bad  = 'tgw202601010000011'

    # Good item
    d_good = itemdata / sku_good
    d_good.mkdir()
    (d_good / f'{sku_good}.json').write_text(json.dumps({
        'sku': sku_good,
        'title': 'Clean Item No Problems Here',
        'location': 'SHELF01',
        'ebay_category_id': '12345',
    }), encoding='utf-8')
    (d_good / 'photo.jpg').write_bytes(b'')

    # Bad item: no title, no photos, bad category
    d_bad = itemdata / sku_bad
    d_bad.mkdir()
    (d_bad / f'{sku_bad}.json').write_text(json.dumps({
        'sku': sku_bad,
        'title': '',
        'location': '',
        'ebay_category_id': 'BADCAT',
    }), encoding='utf-8')

    cfg = {
        'itemdata_root': itemdata,
        'catalog_root': catalog_root,
        'pretty': True,
    }

    result = cmd_catalog_verify(cfg, min_severity='warning')
    assert result['ok']
    assert result['scanned'] == 2
    assert result['violations'] > 0
    assert 'no_title' in result['by_rule']
    assert 'no_photo' in result['by_rule']
    assert 'no_location' in result['by_rule']
    assert 'invalid_ebay_category' in result['by_rule']
    # Good item should have no violations
    assert sku_good not in result.get('by_rule', {})

    captured = capsys.readouterr()
    assert '# Catalog Verification Report' in captured.out
    assert sku_bad in captured.out
