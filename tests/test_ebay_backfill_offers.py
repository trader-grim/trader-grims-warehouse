"""audit#1143 #1204+#1205 (todo #1236): ebay_backfill_offers.py used to
bypass the tgw-api fence entirely (atomic_write_json instead of
apis.fence.ebay_write), causing a lost-update race against concurrent
ebay_sync/ebay_publish fence writes and never enqueuing catalog_rebuild
(invariant A7). Fixed to route every write through apis.fence.ebay_write.

All eBay API calls and the fence HTTP client are mocked — tests pass
completely offline, no network, no real ItemData/log/checkpoint paths touched.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'ebay_backfill_offers.py'
_spec = importlib.util.spec_from_file_location('ebay_backfill_offers', _SCRIPT_PATH)
backfill_mod = importlib.util.module_from_spec(_spec)
sys.modules['ebay_backfill_offers'] = backfill_mod
_spec.loader.exec_module(backfill_mod)


def _write_item(itemdata_root: Path, sku: str, item: dict) -> None:
    d = itemdata_root / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def _offer(sku='tgwSKU1', offer_id='O1', listing_id='L1', price='9.99',
          status='ACTIVE', sold_qty=0, category='12345') -> dict:
    return {
        'offerId': offer_id,
        'listing': {'listingId': listing_id, 'listingStatus': status, 'soldQuantity': sold_qty},
        'pricingSummary': {'price': {'value': price}},
        'categoryId': category,
    }


def _run_backfill(tmp_path, monkeypatch, skus, offers_by_sku, existing_items=None, limit=0):
    """Run main() with all I/O boundaries mocked; return list of
    fence_ebay_write call kwargs."""
    itemdata_root = tmp_path / 'ItemData'
    itemdata_root.mkdir()
    for sku in skus:
        _write_item(itemdata_root, sku, (existing_items or {}).get(sku, {'sku': sku}))

    monkeypatch.setattr(backfill_mod, 'ITEMDATA', itemdata_root)
    monkeypatch.setattr(backfill_mod, 'CKPT_PATH', tmp_path / 'ckpt.json')
    monkeypatch.setattr(backfill_mod, 'load_config', lambda path: {'api_key': 'test-key', 'itemdata_root': itemdata_root})
    monkeypatch.setattr(backfill_mod, 'iter_inventory_api_items',
                        lambda cfg, limit=100: iter({'sku': s} for s in skus))
    monkeypatch.setattr(backfill_mod, 'fetch_offer_for_sku',
                        lambda cfg, sku: offers_by_sku.get(sku))

    calls = []
    monkeypatch.setattr(
        backfill_mod, 'fence_ebay_write',
        lambda cfg, sku, **kwargs: calls.append({'sku': sku, **kwargs}) or {'ok': True},
    )

    argv = ['ebay_backfill_offers.py', '--rate', '0']
    if limit:
        argv += ['--limit', str(limit)]
    monkeypatch.setattr(sys, 'argv', argv)

    backfill_mod.main()
    return calls


def test_writes_go_through_the_fence_not_atomic_write_json(tmp_path, monkeypatch):
    calls = _run_backfill(tmp_path, monkeypatch, ['tgwSKU1'],
                          {'tgwSKU1': _offer('tgwSKU1')})

    assert len(calls) == 1
    assert calls[0]['sku'] == 'tgwSKU1'
    assert calls[0]['ebay_offer'] == {
        'offer_id': 'O1', 'listing_id': 'L1', 'price': '9.99', 'category_id': '12345',
    }
    assert calls[0]['ebay_listing'] == {
        'listing_id': 'L1', 'listing_status': 'ACTIVE', 'sold_quantity': 0,
    }


def test_passes_only_new_fields_not_a_locally_premerged_block(tmp_path, monkeypatch):
    # The item already has an ebay_offer block with fields this script
    # doesn't know about (e.g. price_comps, a protected sub-field owned by
    # ebay_price) — the fence call must NOT include them, proving we don't
    # read-merge-writeback locally (which would reintroduce the lost-update
    # race one layer up).
    existing = {'tgwSKU1': {'sku': 'tgwSKU1', 'ebay_offer': {'price_comps': {'p25': 12.0}}}}
    calls = _run_backfill(tmp_path, monkeypatch, ['tgwSKU1'],
                          {'tgwSKU1': _offer('tgwSKU1')}, existing_items=existing)

    assert len(calls) == 1
    assert 'price_comps' not in calls[0]['ebay_offer']
    assert set(calls[0]['ebay_offer'].keys()) == {'offer_id', 'listing_id', 'price', 'category_id'}


def test_skips_sku_already_having_offer_data_locally(tmp_path, monkeypatch):
    existing = {'tgwSKU1': {'sku': 'tgwSKU1', 'ebay_offer': {'offer_id': 'EXISTING'}}}
    calls = _run_backfill(tmp_path, monkeypatch, ['tgwSKU1'],
                          {'tgwSKU1': _offer('tgwSKU1')}, existing_items=existing)

    assert calls == []


def test_skips_sku_with_no_local_itemdata(tmp_path, monkeypatch):
    # Build the SKU list without writing a local item file for it.
    itemdata_root = tmp_path / 'ItemData'
    itemdata_root.mkdir()
    monkeypatch.setattr(backfill_mod, 'ITEMDATA', itemdata_root)
    monkeypatch.setattr(backfill_mod, 'CKPT_PATH', tmp_path / 'ckpt.json')
    monkeypatch.setattr(backfill_mod, 'load_config', lambda path: {'api_key': 'test-key', 'itemdata_root': itemdata_root})
    monkeypatch.setattr(backfill_mod, 'iter_inventory_api_items',
                        lambda cfg, limit=100: iter([{'sku': 'tgwGhost'}]))
    monkeypatch.setattr(backfill_mod, 'fetch_offer_for_sku', lambda cfg, sku: _offer(sku))
    calls = []
    monkeypatch.setattr(backfill_mod, 'fence_ebay_write',
                        lambda cfg, sku, **kwargs: calls.append(kwargs) or {'ok': True})
    monkeypatch.setattr(sys, 'argv', ['ebay_backfill_offers.py', '--rate', '0'])

    backfill_mod.main()

    assert calls == []


def test_no_offer_found_is_not_written(tmp_path, monkeypatch):
    calls = _run_backfill(tmp_path, monkeypatch, ['tgwSKU1'], {'tgwSKU1': None})
    assert calls == []


def test_fence_write_failure_does_not_crash_the_run(tmp_path, monkeypatch):
    itemdata_root = tmp_path / 'ItemData'
    itemdata_root.mkdir()
    for sku in ('tgwSKU1', 'tgwSKU2'):
        _write_item(itemdata_root, sku, {'sku': sku})

    monkeypatch.setattr(backfill_mod, 'ITEMDATA', itemdata_root)
    monkeypatch.setattr(backfill_mod, 'CKPT_PATH', tmp_path / 'ckpt.json')
    monkeypatch.setattr(backfill_mod, 'load_config',
                        lambda path: {'api_key': 'test-key', 'itemdata_root': itemdata_root})
    monkeypatch.setattr(backfill_mod, 'iter_inventory_api_items',
                        lambda cfg, limit=100: iter([{'sku': 'tgwSKU1'}, {'sku': 'tgwSKU2'}]))
    monkeypatch.setattr(backfill_mod, 'fetch_offer_for_sku',
                        lambda cfg, sku: _offer(sku))

    attempted = []

    def _fence_write(cfg, sku, **kwargs):
        attempted.append(sku)
        if sku == 'tgwSKU1':
            raise RuntimeError('fence unreachable')
        return {'ok': True}

    monkeypatch.setattr(backfill_mod, 'fence_ebay_write', _fence_write)
    monkeypatch.setattr(sys, 'argv', ['ebay_backfill_offers.py', '--rate', '0'])

    backfill_mod.main()  # must not raise

    assert attempted == ['tgwSKU1', 'tgwSKU2']  # second SKU still attempted after first's failure
