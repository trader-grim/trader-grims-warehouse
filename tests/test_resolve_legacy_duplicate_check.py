"""PP-PHOTOSYNC-001 P10 (session 43) — cmd_resolve_legacy must verify a SKU is
not a genuine duplicate listing before resolving, and check_legacy_duplicate_listing
must compare the live Inventory API offer against the locally-recorded listing_id.

Dave: "check for both specifically, then resolve" — the escape hatch operators
use to unblock a legacy-flagged item must not itself become a way to silently
create/leave a duplicate live listing.
"""

import json

import pytest

import tgw.api as api
import tgw.ebay.pull as pull_mod


def _write_item(tmp_path, sku, **fields):
    d = tmp_path / sku
    d.mkdir(parents=True)
    doc = {'sku': sku, 'ebay_listing': {'listing_id': '226700000001', 'status': 'Active'}}
    doc.update(fields)
    (d / f'{sku}.json').write_text(json.dumps(doc), encoding='utf-8')
    return d / f'{sku}.json'


@pytest.fixture
def cfg(tmp_path):
    return {'itemdata_root': tmp_path, 'pretty': False,
           'postgres_dsn': 'dbname=state_machine user=tgw'}


@pytest.fixture(autouse=True)
def _stub_state_machine(monkeypatch):
    import tgw.queue.state_machine as sm
    monkeypatch.setattr(sm, 'init', lambda *a, **k: None)


def test_resolve_skips_sku_on_duplicate_risk(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: {
                            'ok': True, 'match': False, 'duplicate': True,
                            'reason': 'no published Inventory API offer found for this SKU'})
    _write_item(tmp_path, 'tgw1', **{'Item number': '110000012345'})

    result = api.cmd_resolve_legacy(cfg, ['tgw1'], enqueue_stage=False)

    assert result['resolved'] == []
    assert len(result['duplicate_risk']) == 1
    assert result['duplicate_risk'][0]['sku'] == 'tgw1'
    doc = json.loads((tmp_path / 'tgw1' / 'tgw1.json').read_text(encoding='utf-8'))
    assert 'legacy_listing_resolved' not in doc


def test_resolve_proceeds_on_confirmed_match(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: {
                            'ok': True, 'match': True, 'duplicate': False,
                            'inventory_listing_id': listing_id, 'inventory_status': 'ACTIVE'})
    _write_item(tmp_path, 'tgw2', **{'Item number': '110000012345'})

    result = api.cmd_resolve_legacy(cfg, ['tgw2'], enqueue_stage=False)

    assert result['resolved'] == ['tgw2']
    assert result['duplicate_risk'] == []
    doc = json.loads((tmp_path / 'tgw2' / 'tgw2.json').read_text(encoding='utf-8'))
    assert doc['legacy_listing_resolved'] is True
    assert doc['legacy_listing_blocked']['duplicate_check']['match'] is True


def test_resolve_fetch_error_is_treated_as_duplicate_risk(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda cfg, sku, listing_id: {'ok': False, 'error': 'timeout'})
    _write_item(tmp_path, 'tgw3', **{'Item number': '110000012345'})

    result = api.cmd_resolve_legacy(cfg, ['tgw3'], enqueue_stage=False)

    assert result['resolved'] == []
    assert len(result['duplicate_risk']) == 1


def test_resolve_force_bypasses_the_check(cfg, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pull_mod, 'check_legacy_duplicate_listing',
                        lambda *a, **k: calls.append(1))
    _write_item(tmp_path, 'tgw4', **{'Item number': '110000012345'})

    result = api.cmd_resolve_legacy(cfg, ['tgw4'], enqueue_stage=False, force=True)

    assert calls == []   # never called — force skips the live check entirely
    assert result['resolved'] == ['tgw4']


# ---------------------------------------------------------------------------
# check_legacy_duplicate_listing itself
# ---------------------------------------------------------------------------

def test_check_duplicate_listing_match(monkeypatch):
    monkeypatch.setattr(pull_mod, 'ebay_get', lambda cfg, path, params=None: {
        'offers': [{'status': 'PUBLISHED', 'listing': {'listingId': '226700000001', 'listingStatus': 'ACTIVE'}}]})
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['ok'] is True
    assert result['match'] is True
    assert result['duplicate'] is False


def test_check_duplicate_listing_mismatch_is_duplicate_risk(monkeypatch):
    monkeypatch.setattr(pull_mod, 'ebay_get', lambda cfg, path, params=None: {
        'offers': [{'status': 'PUBLISHED', 'listing': {'listingId': '999999999999', 'listingStatus': 'ACTIVE'}}]})
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['ok'] is True
    assert result['match'] is False
    assert result['duplicate'] is True


def test_check_duplicate_listing_no_published_offer_is_duplicate_risk(monkeypatch):
    monkeypatch.setattr(pull_mod, 'ebay_get', lambda cfg, path, params=None: {'offers': []})
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['ok'] is True
    assert result['duplicate'] is True
    assert result['match'] is False


def test_check_duplicate_listing_fetch_error(monkeypatch):
    def _raise(cfg, path, params=None):
        raise RuntimeError('network error')
    monkeypatch.setattr(pull_mod, 'ebay_get', _raise)
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['ok'] is False


# ---------------------------------------------------------------------------
# PP-EBAY-MOTORS-001 — marketplace-aware duplicate detection (session 43,
# found live: "Best Offer is not permitted with a SKU selling on multiple
# eBay marketplaces" for an EBAY_MOTORS offer).
# ---------------------------------------------------------------------------

def test_check_duplicate_listing_surfaces_marketplace_id(monkeypatch):
    monkeypatch.setattr(pull_mod, 'ebay_get', lambda cfg, path, params=None: {
        'offers': [{'status': 'PUBLISHED', 'marketplaceId': 'EBAY_MOTORS',
                   'listing': {'listingId': '226700000001', 'listingStatus': 'ACTIVE'}}]})
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['match'] is True
    assert result['marketplace_id'] == 'EBAY_MOTORS'
    assert result['is_ebay_motors'] is True


def test_check_duplicate_listing_non_motors_marketplace(monkeypatch):
    monkeypatch.setattr(pull_mod, 'ebay_get', lambda cfg, path, params=None: {
        'offers': [{'status': 'PUBLISHED', 'marketplaceId': 'EBAY_US',
                   'listing': {'listingId': '226700000001', 'listingStatus': 'ACTIVE'}}]})
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['is_ebay_motors'] is False


def test_check_duplicate_listing_multiple_marketplaces_is_duplicate(monkeypatch):
    """The literal scenario eBay's rejection described: the same SKU has a
    published offer on more than one marketplace at once. This must be
    flagged as a duplicate even if one of the listingIds matches our local
    record — never treated as safe."""
    monkeypatch.setattr(pull_mod, 'ebay_get', lambda cfg, path, params=None: {
        'offers': [
            {'status': 'PUBLISHED', 'marketplaceId': 'EBAY_MOTORS',
             'listing': {'listingId': '226700000001', 'listingStatus': 'ACTIVE'}},
            {'status': 'PUBLISHED', 'marketplaceId': 'EBAY_US',
             'listing': {'listingId': '226700000099', 'listingStatus': 'ACTIVE'}},
        ]})
    result = pull_mod.check_legacy_duplicate_listing({}, 'tgw1', '226700000001')
    assert result['ok'] is True
    assert result['duplicate'] is True
    assert result['match'] is False
    assert result['is_ebay_motors'] is True
    assert set(result['other_marketplaces']) == {'EBAY_MOTORS', 'EBAY_US'}
