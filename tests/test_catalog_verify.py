"""Tests for PP-VERIFY-001 catalog-verify command and PP-DEADLETTER-001 classify_dead_letter."""
import json
from pathlib import Path

from tgw.api import _compute_fixes, _strip_template_prefix, _verify_item, cmd_catalog_verify
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


# ---------------------------------------------------------------------------
# New rules: negative_price, inventory_api_no_offer, barcode_lookup_fail
# ---------------------------------------------------------------------------

def test_verify_negative_price_offer(tmp_path):
    sku = 'tgw202601010000020'
    doc = {'sku': sku, 'title': 'Valid Title For Testing This', 'location': 'G7',
           'ebay_offer': {'price': -5.0}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'negative_price' in rules


def test_verify_inventory_api_no_offer(tmp_path):
    sku = 'tgw202601010000021'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Item', 'location': 'H8',
           'ebay_listing': {'api': 'inventory', 'listing_id': '123'}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'inventory_api_no_offer' in rules


def test_verify_inventory_api_with_offer_is_clean(tmp_path):
    sku = 'tgw202601010000022'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Okay', 'location': 'I9',
           'ebay_category_id': '12345',
           'ebay_listing': {'api': 'inventory'},
           'ebay_offer': {'offer_id': 'abc', 'price': 9.99}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'inventory_api_no_offer' not in rules


def test_verify_barcode_lookup_fail(tmp_path):
    sku = 'tgw202601010000023'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Barcodes', 'location': 'J10',
           'upc': '012345678901'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'barcode_lookup_fail' in rules


def test_verify_offline_draft_stall(tmp_path):
    """offline_draft=True on a stale file triggers offline_draft_stall warning."""
    import os
    import time
    sku = 'tgw202601010000025'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Offline', 'location': 'L12',
           'offline_draft': True}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    # Backdate the JSON file's mtime to 3 hours ago
    old_time = time.time() - (3 * 3600)
    os.utime(item_dir / f'{sku}.json', (old_time, old_time))
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'offline_draft_stall' in rules


def test_verify_offline_draft_recent_is_clean(tmp_path):
    """offline_draft=True on a recent file does NOT trigger (draft may still be running)."""
    sku = 'tgw202601010000026'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Draft', 'location': 'M13',
           'offline_draft': True}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    # File was just written — mtime is now
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'offline_draft_stall' not in rules


def test_verify_barcode_with_lookup_is_clean(tmp_path):
    sku = 'tgw202601010000024'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Okay', 'location': 'K11',
           'upc': '012345678901', 'product_lookup': {'source': 'upcitemdb', 'title': 'Test'}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'barcode_lookup_fail' not in rules


# ---------------------------------------------------------------------------
# PP-VERIFY-001 Phase 2: mark_verified hall pass + skip_verified
# ---------------------------------------------------------------------------

def test_mark_verified_writes_hall_pass(tmp_path, capsys):
    """Items with no violations get catalog_verified written when --mark-verified."""
    itemdata = tmp_path / 'ItemData'
    itemdata.mkdir()
    sku = 'tgw202601010000030'
    d = itemdata / sku
    d.mkdir()
    jf = d / f'{sku}.json'
    jf.write_text(json.dumps({
        'sku': sku,
        'title': 'Clean Item For Verification Test',
        'location': 'SHELF01',
        'ebay_category_id': '12345',
    }), encoding='utf-8')
    (d / 'photo.jpg').write_bytes(b'')

    cfg = {'itemdata_root': itemdata, 'pretty': True}
    result = cmd_catalog_verify(cfg, min_severity='warning', mark_verified=True)
    capsys.readouterr()

    assert result['ok']
    assert result['marked_verified'] == 1
    written = json.loads(jf.read_text())
    assert 'catalog_verified' in written
    assert written['catalog_verified']['by'] == 'catalog-verify'
    assert 'ts' in written['catalog_verified']


def test_skip_verified_skips_marked_items(tmp_path, capsys):
    """Items with catalog_verified are skipped when --skip-verified."""
    itemdata = tmp_path / 'ItemData'
    itemdata.mkdir()
    sku = 'tgw202601010000031'
    d = itemdata / sku
    d.mkdir()
    jf = d / f'{sku}.json'
    jf.write_text(json.dumps({
        'sku': sku,
        'title': 'Already Verified Item Test',
        'location': 'SHELF01',
        'catalog_verified': {'ts': '2026-01-01T00:00:00Z', 'by': 'catalog-verify'},
    }), encoding='utf-8')

    cfg = {'itemdata_root': itemdata, 'pretty': True}
    result = cmd_catalog_verify(cfg, min_severity='warning', skip_verified=True)
    capsys.readouterr()

    assert result['ok']
    assert result['scanned'] == 0
    assert result['skipped_verified'] == 1


def test_write_field_clears_catalog_verified(tmp_path):
    """_write_field clears catalog_verified when any other field is updated."""
    from tgw.items import _write_field
    sku = 'tgw202601010000032'
    d = tmp_path / sku
    d.mkdir()
    jf = d / f'{sku}.json'
    jf.write_text(json.dumps({
        'sku': sku, 'title': 'Some Title',
        'catalog_verified': {'ts': '2026-01-01T00:00:00Z', 'by': 'catalog-verify'},
    }), encoding='utf-8')
    cfg = {'itemdata_root': tmp_path}
    _write_field(cfg, sku, 'title', 'New Title')
    doc = json.loads(jf.read_text())
    assert 'catalog_verified' not in doc


def test_write_field_preserves_catalog_verified_when_writing_it(tmp_path):
    """_write_field writing catalog_verified itself does not clear it."""
    from tgw.items import _write_field
    sku = 'tgw202601010000033'
    d = tmp_path / sku
    d.mkdir()
    jf = d / f'{sku}.json'
    jf.write_text(json.dumps({'sku': sku, 'title': 'Some Title'}), encoding='utf-8')
    cfg = {'itemdata_root': tmp_path}
    hall_pass = {'ts': '2026-01-01T00:00:00Z', 'by': 'catalog-verify'}
    _write_field(cfg, sku, 'catalog_verified', hall_pass)
    doc = json.loads(jf.read_text())
    assert doc['catalog_verified'] == hall_pass


# ---------------------------------------------------------------------------
# PP-VERIFY-001 Phase 3: --fix auto-strip stale TEMPLATE: prefix
# ---------------------------------------------------------------------------

def test_strip_template_prefix_basic():
    assert _strip_template_prefix('TEMPLATE: Real Title Here') == 'Real Title Here'
    assert _strip_template_prefix('TEMPLATE:Real Title') == 'Real Title'


def test_strip_template_prefix_case_insensitive():
    assert _strip_template_prefix('template: foo bar') == 'foo bar'
    assert _strip_template_prefix('  TeMpLaTe:  spaced  ') == 'spaced'


def test_strip_template_prefix_no_prefix_returns_none():
    assert _strip_template_prefix('Just A Normal Title') is None


def test_strip_template_prefix_empty_result_returns_none():
    # Stripping leaves nothing — must not write an empty title.
    assert _strip_template_prefix('TEMPLATE:') is None
    assert _strip_template_prefix('TEMPLATE:   ') is None


def test_compute_fixes_finds_template_title():
    fixes = _compute_fixes({'title': 'TEMPLATE:electronics widget'})
    assert len(fixes) == 1
    assert fixes[0]['rule'] == 'stale_template_prefix'
    assert fixes[0]['field'] == 'title'
    assert fixes[0]['after'] == 'electronics widget'


def test_compute_fixes_clean_item_no_fixes():
    assert _compute_fixes({'title': 'A Perfectly Fine Title'}) == []


def _fixture_template_item(tmp_path):
    itemdata = tmp_path / 'ItemData'
    itemdata.mkdir()
    sku = 'tgw202601010000040'
    d = itemdata / sku
    d.mkdir()
    jf = d / f'{sku}.json'
    jf.write_text(json.dumps({
        'sku': sku,
        'title': 'TEMPLATE:electronics Cool Gadget 2000',
        'location': 'SHELF01',
        'ebay_category_id': '12345',
    }), encoding='utf-8')
    (d / 'photo.jpg').write_bytes(b'')
    return {'itemdata_root': itemdata, 'pretty': True}, jf, sku


def test_fix_dry_run_does_not_write(tmp_path, capsys):
    cfg, jf, sku = _fixture_template_item(tmp_path)
    result = cmd_catalog_verify(cfg, min_severity='critical', fix=True)  # no write
    capsys.readouterr()
    assert result['fixes_proposed'] == 1
    assert result['fixes_applied'] == 0
    assert result['fixes'][0]['applied'] is False
    # File unchanged on disk
    assert json.loads(jf.read_text())['title'].startswith('TEMPLATE:')


def test_fix_write_applies(tmp_path, capsys):
    cfg, jf, sku = _fixture_template_item(tmp_path)
    result = cmd_catalog_verify(cfg, min_severity='critical', fix=True, write=True)
    capsys.readouterr()
    assert result['fixes_applied'] == 1
    assert result['fixes'][0]['applied'] is True
    written = json.loads(jf.read_text())
    assert written['title'] == 'electronics Cool Gadget 2000'
    assert not written['title'].upper().startswith('TEMPLATE:')


def test_fix_report_mentions_fixes(tmp_path, capsys):
    cfg, jf, sku = _fixture_template_item(tmp_path)
    cmd_catalog_verify(cfg, min_severity='critical', fix=True)
    out = capsys.readouterr().out
    assert 'FIXES' in out
    assert 'dry-run' in out
    assert sku in out


def test_fix_write_refreshes_violations_no_double_report(tmp_path, capsys):
    """After --fix --write, the fixed violation must NOT also appear as open."""
    cfg, jf, sku = _fixture_template_item(tmp_path)
    result = cmd_catalog_verify(cfg, min_severity='critical', fix=True, write=True)
    out = capsys.readouterr().out
    # violation list + by_rule reflect post-fix state (no stale stale_template_prefix)
    assert 'stale_template_prefix' not in result['by_rule']
    assert result['violations'] == 0
    assert result['fixes_applied'] == 1
    # report shows the fix, but NOT an open '- [ ] stale_template_prefix' for it
    assert 'FIXES' in out
    assert '- [ ] **stale_template_prefix**' not in out


def test_fix_write_mark_verified_combo_marks_fixed_item(tmp_path, capsys):
    """--fix --write --mark-verified: an item whose ONLY problem was auto-fixed
    must get the hall pass (item_viols refreshed before the mark gate)."""
    cfg, jf, sku = _fixture_template_item(tmp_path)
    result = cmd_catalog_verify(cfg, min_severity='critical',
                                fix=True, write=True, mark_verified=True)
    capsys.readouterr()
    assert result['fixes_applied'] == 1
    assert result['marked_verified'] == 1
    doc = json.loads(jf.read_text())
    assert not doc['title'].upper().startswith('TEMPLATE:')
    assert doc.get('catalog_verified', {}).get('by') == 'catalog-verify'


# ---------------------------------------------------------------------------
# New rules: no_price, wrong_condition
# ---------------------------------------------------------------------------

def test_verify_no_price_draft_zero(tmp_path):
    sku = 'tgw202601010000050'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Price', 'location': 'N14',
           'draft_listing': {'condition_id': 3000, 'price': 0}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_price' in rules


def test_verify_no_price_draft_missing(tmp_path):
    sku = 'tgw202601010000051'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Price', 'location': 'N15',
           'draft_listing': {'condition_id': 3000}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_price' in rules


def test_verify_no_price_draft_with_price_is_clean(tmp_path):
    sku = 'tgw202601010000052'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Price', 'location': 'N16',
           'draft_listing': {'condition_id': 3000, 'price': 12.99}}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_price' not in rules


def test_verify_no_price_no_draft_is_clean(tmp_path):
    sku = 'tgw202601010000053'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Price', 'location': 'N17'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'no_price' not in rules


def test_verify_wrong_condition_unknown(tmp_path):
    sku = 'tgw202601010000054'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Cond', 'location': 'O18',
           'condition': 'mint'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'wrong_condition' in rules


def test_verify_wrong_condition_known_is_clean(tmp_path):
    sku = 'tgw202601010000055'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Cond', 'location': 'O19',
           'condition': 'used: excellent'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'wrong_condition' not in rules


def test_verify_wrong_condition_case_insensitive(tmp_path):
    sku = 'tgw202601010000056'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Cond', 'location': 'O20',
           'condition': 'New In Box'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'wrong_condition' not in rules


def test_verify_no_condition_is_clean(tmp_path):
    sku = 'tgw202601010000057'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Cond', 'location': 'O21'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'wrong_condition' not in rules


# ---------------------------------------------------------------------------
# PP-VERIFY-001: leading_space_title rule + auto-fix
# ---------------------------------------------------------------------------

def test_verify_leading_space_title_warns(tmp_path):
    """Title with a leading space triggers leading_space_title warning."""
    sku = 'tgw202601010000060'
    doc = {'sku': sku, 'title': ' Widget With Leading Space Here', 'location': 'P22'}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'leading_space_title' in rules


def test_compute_fixes_leading_space_lstrips_title():
    """_compute_fixes proposes lstrip() fix for a leading-space title."""
    fixes = _compute_fixes({'title': ' Widget With Leading Space Here'})
    assert len(fixes) == 1
    assert fixes[0]['rule'] == 'leading_space_title'
    assert fixes[0]['field'] == 'title'
    assert fixes[0]['after'] == 'Widget With Leading Space Here'
