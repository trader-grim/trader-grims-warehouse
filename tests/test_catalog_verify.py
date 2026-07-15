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


def test_verify_surfaces_persisted_pipeline_error(tmp_path):
    """code-review follow-up: none of the C11 findings persisted by
    http_server.py (location_update_failed, ebay_end_desync,
    revision_sync_not_queued, revision_discard_rebuild_not_queued) were
    queryable by catalog-verify -- surface pipeline_error generically."""
    sku = 'tgw202601010000099'
    doc = {
        'sku': sku, 'title': 'Some Item With A Finding', 'location': 'C3',
        'pipeline_error': {
            'code': 'ebay_end_desync',
            'detail': 'eBay listing ended but local write failed: boom',
            'ts': '2026-07-06T00:00:00+00:00',
            'source': 'bulk_action:ebay_end_listing',
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    viols = _verify_item(sku, item_dir, doc)
    rules = {v['rule'] for v in viols}
    assert 'pipeline_error:ebay_end_desync' in rules


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


# ---------------------------------------------------------------------------
# invariant C12 (todo #1416): field_set_drift detector — the "regularly
# check and repair" data-drift half, complementing the static code-level
# detector (tests/test_invariant_c12_field_set_accessors.py).
# ---------------------------------------------------------------------------

def test_verify_field_set_drift_flagged_when_live_offer_present(tmp_path):
    sku = 'tgw202601010000030'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E5',
        'item_attributes': {'Type': 'Lapel Pin', 'Brand': 'Unbranded'},
        'draft_listing': {'item_specifics': {'Type': 'Brooch', 'Brand': 'Unbranded'}},
        'ebay_offer': {'offer_id': '266061679018'},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    viols = {v['rule']: v for v in _verify_item(sku, item_dir, doc)}
    assert 'field_set_drift' in viols
    assert 'Type' in viols['field_set_drift']['detail']
    assert 'Brand' not in viols['field_set_drift']['detail']


def test_verify_field_set_drift_not_flagged_without_live_offer(tmp_path):
    """A never-published draft's Set A/Set B disagreeing is normal
    pre-publish churn, not a finding — only live items are checked."""
    sku = 'tgw202601010000031'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E5',
        'item_attributes': {'Type': 'Lapel Pin'},
        'draft_listing': {'item_specifics': {'Type': 'Brooch'}},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'field_set_drift' not in rules


def test_verify_field_set_drift_clean_when_sets_agree(tmp_path):
    sku = 'tgw202601010000032'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E5',
        'item_attributes': {'Type': 'Brooch'},
        'draft_listing': {'item_specifics': {'Type': 'Brooch'}},
        'ebay_offer': {'offer_id': '266061679018'},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'field_set_drift' not in rules


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


def test_verify_negative_qty(tmp_path):
    sku = 'tgw202601010000027'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Qty', 'location': 'N14', 'qty': -3}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    viols = _verify_item(sku, item_dir, doc)
    rules = {v['rule'] for v in viols}
    assert 'negative_qty' in rules
    severities = {v['rule']: v['severity'] for v in viols}
    assert severities['negative_qty'] == 'critical'


def test_verify_zero_qty_is_clean(tmp_path):
    sku = 'tgw202601010000028'
    doc = {'sku': sku, 'title': 'Valid Title For Testing Qty Zero', 'location': 'O15', 'qty': 0}
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'negative_qty' not in rules


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


def test_compute_fixes_template_prefix_empty_body_not_fixable():
    """'  TEMPLATE: ' (prefix + empty body) must not emit a leading_space_title fix.

    _strip_template_prefix returns None for an empty body, but the elif branch
    must not fire — lstripping would produce 'TEMPLATE: ', which still triggers
    stale_template_prefix on the next verify pass.
    """
    fixes = _compute_fixes({'title': '  TEMPLATE: '})
    assert fixes == []


# ---------------------------------------------------------------------------
# PP-PHOTOSYNC-001 P7 — truth-audit rules (pipeline claims vs reality)
# ---------------------------------------------------------------------------

def test_photos_short_on_ebay_flags_active_shortfall(tmp_path):
    """The exact s43 bug, made detectable: an Active inventory-API listing
    with fewer live eBay photo URLs than exist on disk."""
    sku = 'tgw202601010000010'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E5',
        'ebay_listing': {'status': 'Active', 'api': 'inventory'},
        'draft_listing': {'imageUrls': ['https://x/1']},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photos_short_on_ebay' in rules


def test_photos_short_on_ebay_ignores_non_active(tmp_path):
    sku = 'tgw202601010000011'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E6',
        'ebay_listing': {'status': 'Draft', 'api': 'inventory'},
        'draft_listing': {'imageUrls': ['https://x/1']},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photos_short_on_ebay' not in rules


def test_photos_short_on_ebay_ignores_items_with_no_recorded_urls(tmp_path):
    """Most of the historical catalog never populated a live-URL field even
    when photos genuinely went live via an older pipeline path (found live,
    2026-07-03 — using ebay_photos as the proxy produced 9,382 false
    positives on the first real run). With no live_photo_urls at all, this
    rule must stay silent rather than assume a shortfall."""
    sku = 'tgw202601010000018'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E10',
        'ebay_listing': {'status': 'Active', 'api': 'inventory'},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photos_short_on_ebay' not in rules


def test_photos_short_on_ebay_prefers_live_photo_index_over_local_mirror(tmp_path):
    """PP-PHOTOSYNC-001 P9 follow-up (todo #1127): when a live_photo_index is
    supplied (built from the R1.8 whole-site capture), it is authoritative —
    even if the local mirror (draft_listing.imageUrls) disagrees."""
    sku = 'tgw202601010000030'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E5',
        'ebay_listing': {'status': 'Active', 'api': 'inventory'},
        'draft_listing': {'imageUrls': ['https://x/1', 'https://x/2', 'https://x/3',
                                        'https://x/4', 'https://x/5', 'https://x/6',
                                        'https://x/7', 'https://x/8', 'https://x/9']},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    # local mirror says 9/9 (clean) but the live capture says only 2 are live
    rules_no_index = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photos_short_on_ebay' not in rules_no_index
    rules_with_index = {v['rule'] for v in _verify_item(sku, item_dir, doc, {sku: 2})}
    assert 'photos_short_on_ebay' in rules_with_index


def test_photos_short_on_ebay_index_present_but_sku_missing_falls_back(tmp_path):
    """A SKU absent from the capture (e.g. new since the snapshot) must fall
    back to the local-mirror method, not silently pass or fail."""
    sku = 'tgw202601010000031'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E5',
        'ebay_listing': {'status': 'Active', 'api': 'inventory'},
        'draft_listing': {'imageUrls': ['https://x/1']},
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc, {'tgw-some-other-sku': 9})}
    assert 'photos_short_on_ebay' in rules  # local mirror: 1 < 9


def test_photo_verify_stale_count_mismatch(tmp_path):
    sku = 'tgw202601010000012'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E7',
        'ebay_listing': {'status': 'Active', 'api': 'inventory',
                         'photo_verify': {'submitted_count': 24, 'confirmed_count': 9}},
        'ebay_photos': [{'local': f'{i}', 'url': f'https://x/{i}'} for i in range(24)],
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=24)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photo_verify_stale' in rules


def test_photo_verify_stale_predates_last_stage(tmp_path):
    """tgw202606021133367's exact bug: photo_verify counts matched (9==9) at
    the time it was written, but a later re-stage made it stale."""
    sku = 'tgw202601010000013'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E8',
        'ebay_listing': {'status': 'Active', 'api': 'inventory',
                         'photo_verify': {'submitted_count': 9, 'confirmed_count': 9,
                                         'verified_at': '2026-07-01T00:00:00+00:00'}},
        'ebay_offer': {'staged_at': '2026-07-03T09:55:00+00:00'},
        'ebay_photos': [{'local': f'{i}', 'url': f'https://x/{i}'} for i in range(9)],
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photo_verify_stale' in rules


def test_photo_verify_fresh_no_violation(tmp_path):
    sku = 'tgw202601010000014'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'E9',
        'ebay_listing': {'status': 'Active', 'api': 'inventory',
                         'photo_verify': {'submitted_count': 9, 'confirmed_count': 9,
                                         'verified_at': '2026-07-03T10:00:00+00:00'}},
        'ebay_offer': {'staged_at': '2026-07-03T09:55:00+00:00'},
        'ebay_photos': [{'local': f'{i}', 'url': f'https://x/{i}'} for i in range(9)],
    }
    item_dir, _ = _make_item(tmp_path, sku, doc, photos=9)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'photo_verify_stale' not in rules


def test_submitted_live_drift_fires_when_live_is_newer(tmp_path):
    """Live pull postdates our submission and the title genuinely differs —
    this IS worth flagging."""
    sku = 'tgw202601010000016'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'F3',
        'ebay_submitted': {
            'staged_at': '2026-07-01T00:00:00+00:00',
            'inventory_item': {'product': {'title': 'Old Title'}},
        },
        'ebay_live': {
            'pulled_at': '2026-07-02T00:00:00+00:00',
            'inventory_item': {'product': {'title': 'New Title'}},
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'submitted_live_drift' in rules


def test_submitted_live_drift_never_fires_when_live_is_older(tmp_path):
    """The exact mistake made on 2026-07-03: comparing a submission against
    an OLDER live snapshot always shows a 'diff' that isn't evidence of
    anything except staleness. This rule must never repeat it."""
    sku = 'tgw202601010000017'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'F1',
        'ebay_submitted': {
            'staged_at': '2026-07-03T15:55:00+00:00',
            'inventory_item': {'product': {'title': "Grant's Blended Scotch"}},
        },
        'ebay_live': {
            'pulled_at': '2026-07-01T15:11:00+00:00',
            'inventory_item': {'product': {'title': "Vintage Grant's Scotch"}},
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'submitted_live_drift' not in rules


def test_submitted_live_drift_flags_aspect_change(tmp_path):
    sku = 'tgw202601010000015'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'F2',
        'ebay_submitted': {
            'staged_at': '2026-07-01T00:00:00+00:00',
            'inventory_item': {'product': {'aspects': {'Color': ['Green']}}},
        },
        'ebay_live': {
            'pulled_at': '2026-07-02T00:00:00+00:00',
            'inventory_item': {'product': {'aspects': {'Color': ['Brown']}}},
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'submitted_live_drift' in rules


# ---------------------------------------------------------------------------
# success_count_contradiction — journald-derived rule (structural test only;
# real journald access is exercised live, not in the unit suite)
# ---------------------------------------------------------------------------

def test_scan_upload_complete_contradictions_flags_shortfall(monkeypatch):
    import subprocess as _subprocess

    from tgw.api import _scan_upload_complete_contradictions

    fake_stdout = '\n'.join([
        json.dumps({'event': 'ebay_upload_complete', 'sku': 'tgw1', 'total': 9,
                   'new': 0, 'to_attempt': 17}),
        json.dumps({'event': 'ebay_upload_complete', 'sku': 'tgw2', 'total': 5,
                   'new': 5, 'to_attempt': 5}),
        'not json at all',
    ])

    class _FakeProc:
        stdout = fake_stdout

    monkeypatch.setattr(_subprocess, 'run', lambda *a, **k: _FakeProc())
    violations = _scan_upload_complete_contradictions(hours=24)
    assert len(violations) == 1
    assert violations[0]['sku'] == 'tgw1'
    assert violations[0]['rule'] == 'success_count_contradiction'


def test_scan_upload_complete_contradictions_ignores_pre_p1_events(monkeypatch):
    """Events logged before the P1 fix have no to_attempt field — not
    comparable, must not false-positive."""
    import subprocess as _subprocess

    from tgw.api import _scan_upload_complete_contradictions

    fake_stdout = json.dumps({'event': 'ebay_upload_complete', 'sku': 'tgw1',
                              'total': 9, 'new': 0})

    class _FakeProc:
        stdout = fake_stdout

    monkeypatch.setattr(_subprocess, 'run', lambda *a, **k: _FakeProc())
    violations = _scan_upload_complete_contradictions(hours=24)
    assert violations == []


def test_scan_upload_complete_contradictions_journalctl_missing_is_safe(monkeypatch):
    import subprocess as _subprocess

    from tgw.api import _scan_upload_complete_contradictions

    def _raise(*a, **k):
        raise FileNotFoundError('journalctl not found')

    monkeypatch.setattr(_subprocess, 'run', _raise)
    assert _scan_upload_complete_contradictions(hours=24) == []


# ---------------------------------------------------------------------------
# PP-PHOTOSYNC-001 P10 — legacy_listing_unrepaired (the "we ignored and did
# not record the error message" fix): a persisted legacy-listing skip must be
# detectable, not just logged and forgotten.
# ---------------------------------------------------------------------------

def test_legacy_listing_never_repaired_is_critical(tmp_path):
    sku = 'tgw202601010000019'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'G1',
        'legacy_listing_blocked': {
            'listing_id': '226700000001', 'item_number': '110000012345',
            'detected_at': '2026-07-03T16:44:00+00:00', 'photo_repair': None,
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'legacy_listing_unrepaired' in rules


def test_legacy_listing_repair_failure_is_critical(tmp_path):
    sku = 'tgw202601010000020'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'G2',
        'legacy_listing_blocked': {
            'listing_id': '226700000001', 'item_number': '110000012345',
            'detected_at': '2026-07-03T16:44:00+00:00',
            'photo_repair': {'ok': False, 'error': 'item suspended'},
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'legacy_listing_unrepaired' in rules


def test_legacy_listing_successful_repair_no_violation(tmp_path):
    sku = 'tgw202601010000021'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'G3',
        'legacy_listing_blocked': {
            'listing_id': '226700000001', 'item_number': '110000012345',
            'detected_at': '2026-07-03T16:44:00+00:00',
            'photo_repair': {'ok': True, 'image_count': 7, 'repaired_at': '2026-07-03T16:45:00+00:00'},
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'legacy_listing_unrepaired' not in rules


def test_legacy_listing_resolved_suppresses_rule(tmp_path):
    """An operator can mark legacy_listing_resolved=True (existing escape
    hatch) once the underlying listing is dealt with some other way — the
    rule must not keep nagging after that."""
    sku = 'tgw202601010000022'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'G4',
        'legacy_listing_resolved': True,
        'legacy_listing_blocked': {
            'listing_id': '226700000001', 'item_number': '110000012345',
            'detected_at': '2026-07-03T16:44:00+00:00', 'photo_repair': None,
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'legacy_listing_unrepaired' not in rules


# ---------------------------------------------------------------------------
# todo #1303 / invariant C11 — ebay_upload_blocked (no-photos-on-disk guard
# now persists a durable finding instead of a log-only skip).
# ---------------------------------------------------------------------------

def test_ebay_upload_no_photos_blocked_is_critical(tmp_path):
    sku = 'tgw202601010000023'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'G5',
        'ebay_upload_blocked': {
            'reason': 'no_photos_on_disk',
            'detected_at': '2026-07-13T00:00:00+00:00',
        },
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'ebay_upload_no_photos_unrepaired' in rules


def test_ebay_upload_no_photos_cleared_suppresses_rule(tmp_path):
    """The worker clears ebay_upload_blocked to None on a subsequent full
    success — the rule must not keep nagging after that."""
    sku = 'tgw202601010000024'
    doc = {
        'sku': sku, 'title': 'Valid Title For Testing', 'location': 'G6',
        'ebay_upload_blocked': None,
    }
    item_dir, _ = _make_item(tmp_path, sku, doc)
    rules = {v['rule'] for v in _verify_item(sku, item_dir, doc)}
    assert 'ebay_upload_no_photos_unrepaired' not in rules


# ---------------------------------------------------------------------------
# PP-PHOTOSYNC-001 P9 follow-up (todo #1127) — _load_live_photo_index
# ---------------------------------------------------------------------------

def _write_capture(path, records):
    import gzip as _gzip
    with open(path, 'ab') as fh:
        for rec in records:
            fh.write(_gzip.compress((json.dumps(rec) + '\n').encode('utf-8')))


def test_load_live_photo_index_reads_fresh_capture(tmp_path):
    from tgw.api import _load_live_photo_index
    capture_root = tmp_path / 'ebay'
    capture_root.mkdir()
    _write_capture(capture_root / '2026-07-04.jsonl.gz', [
        {'name': 'GET /sell/inventory/v1/inventory_item', 'status': 200,
         'body': json.dumps({'inventoryItems': [
             {'sku': 'tgwA', 'product': {'imageUrls': ['a', 'b', 'c']}},
             {'sku': 'tgwB', 'product': {'imageUrls': ['a']}},
         ]})},
        {'name': 'GET /sell/inventory/v1/offer', 'status': 200,
         'body': json.dumps({'offers': [{'sku': 'tgwA'}]})},
    ])
    cfg = {'raw': {'ebay_capture_root': str(capture_root)}}
    index, age_h = _load_live_photo_index(cfg)
    assert index == {'tgwA': 3, 'tgwB': 1}
    assert age_h is not None and age_h < 1


def test_load_live_photo_index_stale_capture_returns_none(tmp_path):
    import os

    from tgw.api import _load_live_photo_index
    capture_root = tmp_path / 'ebay'
    capture_root.mkdir()
    f = capture_root / '2026-07-01.jsonl.gz'
    _write_capture(f, [{'name': 'GET /sell/inventory/v1/inventory_item', 'status': 200,
                        'body': json.dumps({'inventoryItems': [
                            {'sku': 'tgwA', 'product': {'imageUrls': ['a']}}]})}])
    old = 1_783_000_000  # well over 24h before "now" in any plausible test run
    os.utime(f, (old, old))
    cfg = {'raw': {'ebay_capture_root': str(capture_root)}}
    index, age_h = _load_live_photo_index(cfg)
    assert index is None
    assert age_h > 24


def test_load_live_photo_index_missing_root_returns_none(tmp_path):
    from tgw.api import _load_live_photo_index
    cfg = {'raw': {'ebay_capture_root': str(tmp_path / 'nope')}}
    index, age_h = _load_live_photo_index(cfg)
    assert index is None
    assert age_h is None


# ---------------------------------------------------------------------------
# photo_files_readable rule (todo #1154, photo-integrity mitigation leg 1)
# ---------------------------------------------------------------------------

def _write_real_jpeg(path: Path) -> None:
    from PIL import Image
    Image.new("RGB", (8, 8), color="red").save(path, format="JPEG")


def test_photo_decode_check_off_by_default(tmp_path):
    """Every pre-existing caller/test passes no photo_decode_cache -- the
    rule must not fire (existing fixtures use empty-byte .jpg files)."""
    item_dir, doc = _make_item(tmp_path, 'tgw1', {'title': 'Widget', 'location': 'A1'}, photos=1)
    viols = _verify_item('tgw1', item_dir, doc)
    assert 'photo_files_readable' not in {v['rule'] for v in viols}


def test_photo_decode_check_flags_corrupt_file(tmp_path):
    item_dir, doc = _make_item(tmp_path, 'tgw2', {'title': 'Widget', 'location': 'A1'}, photos=0)
    (item_dir / 'photo0.jpg').write_bytes(b'not a real jpeg')
    cache: dict = {}
    viols = _verify_item('tgw2', item_dir, doc, photo_decode_cache=cache)
    rules = {v['rule'] for v in viols}
    assert 'photo_files_readable' in rules
    assert cache  # populated


def test_photo_decode_check_passes_real_jpeg(tmp_path):
    item_dir, doc = _make_item(tmp_path, 'tgw3', {'title': 'Widget', 'location': 'A1'}, photos=0)
    _write_real_jpeg(item_dir / 'photo0.jpg')
    cache: dict = {}
    viols = _verify_item('tgw3', item_dir, doc, photo_decode_cache=cache)
    assert 'photo_files_readable' not in {v['rule'] for v in viols}


def test_photo_decode_cache_skips_pil_on_unchanged_file(tmp_path, monkeypatch):
    """A second check of the SAME (size,mtime) file must not touch PIL at
    all -- the whole point of the cache is to make repeat catalog-verify
    passes cheap."""
    from tgw.api import _check_photo_readable

    photo_path = tmp_path / 'photo.jpg'
    _write_real_jpeg(photo_path)
    cache: dict = {}

    assert _check_photo_readable(photo_path, cache) is None  # first call: real decode

    opens = {'n': 0}
    real_open = __import__('PIL.Image', fromlist=['Image']).open

    def _counting_open(*a, **k):
        opens['n'] += 1
        return real_open(*a, **k)

    monkeypatch.setattr('PIL.Image.open', _counting_open)
    assert _check_photo_readable(photo_path, cache) is None  # cache hit
    assert opens['n'] == 0


def test_photo_decode_cache_redecodes_when_file_changes(tmp_path):
    from tgw.api import _check_photo_readable

    photo_path = tmp_path / 'photo.jpg'
    _write_real_jpeg(photo_path)
    cache: dict = {}
    assert _check_photo_readable(photo_path, cache) is None

    # overwrite with corrupt bytes but keep checking the SAME path -- size
    # and/or mtime must change for the cache to notice and re-decode
    import time
    time.sleep(0.01)
    photo_path.write_bytes(b'corrupt now')
    error = _check_photo_readable(photo_path, cache)
    assert error is not None


def test_cmd_catalog_verify_check_photos_flag(tmp_path):
    itemdata_root = tmp_path / 'ItemData'
    itemdata_root.mkdir()
    catalog_root = tmp_path / 'catalog'
    item_dir = itemdata_root / 'tgw5'
    item_dir.mkdir()
    (item_dir / 'tgw5.json').write_text(json.dumps({'title': 'Widget', 'location': 'A1'}), encoding='utf-8')
    (item_dir / 'photo0.jpg').write_bytes(b'not a real jpeg')

    cfg = {'itemdata_root': itemdata_root, 'catalog_root': catalog_root, 'raw': {}}

    result_off = cmd_catalog_verify(cfg, min_severity='critical', check_photos=False)
    assert 'photo_files_readable' not in result_off['by_rule']

    result_on = cmd_catalog_verify(cfg, min_severity='critical', check_photos=True)
    assert result_on['by_rule'].get('photo_files_readable') == 1
    assert (catalog_root / 'photo-decode-cache.json').exists()


def test_save_photo_decode_cache_merges_not_overwrites(tmp_path):
    """code-review follow-up: _save_photo_decode_cache used to be a plain
    write_text -- a concurrent writer's entries (or entries from an earlier
    run not present in this process's in-memory dict) would be silently
    dropped. Must merge onto whatever's on disk, like the other eBay disk
    caches (locked_merge_cache_json)."""
    from tgw.api import _load_photo_decode_cache, _save_photo_decode_cache

    catalog_root = tmp_path / 'catalog'
    cfg = {'catalog_root': catalog_root}

    _save_photo_decode_cache(cfg, {'/a/1.jpg': {'fingerprint': [1, 1.0], 'error': None}})
    # a second, independent save with a DIFFERENT key -- simulates either a
    # concurrent writer or a fresh in-memory cache that didn't inherit the
    # first entry (e.g. two separate cmd_catalog_verify runs)
    _save_photo_decode_cache(cfg, {'/b/2.jpg': {'fingerprint': [2, 2.0], 'error': 'bad'}})

    on_disk = _load_photo_decode_cache(cfg)
    assert '/a/1.jpg' in on_disk
    assert '/b/2.jpg' in on_disk
