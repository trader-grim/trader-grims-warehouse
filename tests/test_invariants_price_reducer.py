"""Invariants C4/C5/C6 (docs/invariants.md) — ebay_price_reducer safety rules.

C6: reprice_skip honored; only offers with an Active/PUBLISHED listing are
touched; the highest due stage is applied with a single PUT and all due stages
stamped done_at; an eBay rejection leaves the item JSON untouched so the stage
stays due.

C5 (fixed 2026-06-10): a due stage priced at or above the current offer price
is stamped done WITHOUT an eBay call — the reducer never raises a price.

C4 (fixed 2026-06-10): offer PUT is full-replace per eBay semantics, so the
reducer rebuilds the complete offer body via sync._build_offer_bodies with the
new price injected, and appends a price_history event on every applied
reduction.

_reduce_item(jf, now, stats) is called directly; worker built via
object.__new__ (pattern from tests/test_strikethrough.py). The one account
API call _build_offer_bodies makes (_get_merchant_location) is stubbed.
"""

import json
from datetime import datetime, timezone

import pytest
import requests

import tgw.ebay.sync as sync
import tgw.workers.ebay_price_reducer as reducer_mod

NOW    = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)
PAST   = '2026-06-01T00:00:00+00:00'
FUTURE = '2026-12-01T00:00:00+00:00'


@pytest.fixture
def reducer(tmp_path, monkeypatch):
    monkeypatch.setattr(reducer_mod.tgw_logging, 'log_event', lambda *a, **k: None)
    monkeypatch.setattr(sync, '_get_merchant_location', lambda _cfg: 'LOC-1')
    puts = []

    def fake_ebay_put(cfg, path, body, extra_headers=None):
        puts.append({'path': path, 'body': body})

    monkeypatch.setattr(reducer_mod, 'ebay_put', fake_ebay_put)

    worker = object.__new__(reducer_mod.EbayPriceReducerWorker)
    worker.config = {
        'itemdata_root':         tmp_path,
        'pretty':                False,
        # full policy set so _get_listing_policies stays config-first (no API)
        'fulfillment_policy_id': 'FC4',
        'payment_policy_id':     'PAY1',
        'return_policy_id':      'RET1',
        'raw':                   {},
        'api_key':               'test-api-key',
    }
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(reducer_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(reducer_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    worker._puts = puts
    return worker


def _item(price=24.99, listing_status='Active', offer_id='OFF1', **extra):
    item = {
        'sku': 'tgw1',
        'ebay_offer':   {'offer_id': offer_id, 'price': price},
        'ebay_listing': {'status': listing_status},
        'draft_listing': {
            'title':          'Acme Thing',
            'description':    'A thing.',
            'category_id':    '12345',
            'quantity':       1,
            'condition_enum': 'USED_GOOD',
            'imageUrls':      ['https://eps/1.jpg'],
            'price':          price,
        },
        'reprice_schedule': [
            {'stage': 0, 'label': 'launch', 'price': 24.99,
             'due_at': PAST, 'done_at': PAST},
            {'stage': 1, 'label': 'retail', 'price': 18.0,
             'due_at': PAST, 'done_at': None},
            {'stage': 2, 'label': 'move', 'price': 12.0,
             'due_at': FUTURE, 'done_at': None},
        ],
    }
    item.update(extra)
    return item


def _write(tmp_path, item):
    d = tmp_path / item['sku']
    d.mkdir(parents=True)
    path = d / f"{item['sku']}.json"
    path.write_text(json.dumps(item), encoding='utf-8')
    return path


def _run(reducer, path):
    stats = {'scanned': 0, 'reduced': 0, 'skipped': 0, 'errors': 0}
    reducer._reduce_item(path, NOW, stats)
    return stats, json.loads(path.read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# C6 — skip rules and write ordering
# ---------------------------------------------------------------------------

def test_applies_due_stage_and_stamps_done_at(reducer, tmp_path):
    path = _write(tmp_path, _item())
    stats, after = _run(reducer, path)
    assert stats['reduced'] == 1
    assert len(reducer._puts) == 1
    assert reducer._puts[0]['path'] == '/sell/inventory/v1/offer/OFF1'
    assert after['ebay_offer']['price'] == 18.0
    assert after['reprice_schedule'][1]['done_at'] is not None
    assert after['reprice_schedule'][2]['done_at'] is None   # future stage untouched


def test_catchup_applies_only_latest_due_stage_one_put(reducer, tmp_path):
    # Worker downtime: retail AND move both due — one PUT at the move price,
    # both stages stamped done (no intermediate price flapping on eBay).
    item = _item()
    item['reprice_schedule'][2]['due_at'] = PAST
    path = _write(tmp_path, item)
    _, after = _run(reducer, path)
    assert len(reducer._puts) == 1
    assert after['ebay_offer']['price'] == 12.0
    assert after['reprice_schedule'][1]['done_at'] is not None
    assert after['reprice_schedule'][2]['done_at'] is not None


def test_reprice_skip_is_honored(reducer, tmp_path):
    path = _write(tmp_path, _item(reprice_skip=True))
    before = path.read_text()
    _run(reducer, path)
    assert reducer._puts == []
    assert path.read_text() == before


def test_inactive_listing_not_touched(reducer, tmp_path):
    path = _write(tmp_path, _item(listing_status='Ended'))
    _run(reducer, path)
    assert reducer._puts == []


def test_missing_offer_id_not_touched(reducer, tmp_path):
    path = _write(tmp_path, _item(offer_id=None))
    _run(reducer, path)
    assert reducer._puts == []


def test_ebay_rejection_leaves_item_unchanged(reducer, tmp_path, monkeypatch):
    # eBay says no → no done_at stamp, no local price change; the stage stays
    # due so the next 6h pass retries it.
    def rejecting_put(cfg, path, body, extra_headers=None):
        raise requests.exceptions.HTTPError('400 Bad Request')

    monkeypatch.setattr(reducer_mod, 'ebay_put', rejecting_put)
    path = _write(tmp_path, _item())
    before = path.read_text()
    stats, _ = _run(reducer, path)
    assert stats['errors'] == 1
    assert stats['reduced'] == 0
    assert path.read_text() == before


def test_missing_draft_listing_is_error_not_partial_put(reducer, tmp_path):
    # Without a draft the full offer body cannot be rebuilt — the reducer must
    # refuse (count an error) rather than fall back to a partial PUT.
    item = _item()
    del item['draft_listing']
    path = _write(tmp_path, item)
    before = path.read_text()
    stats, _ = _run(reducer, path)
    assert reducer._puts == []
    assert stats['errors'] == 1
    assert path.read_text() == before


# ---------------------------------------------------------------------------
# C5 — never raise a price
# ---------------------------------------------------------------------------

def test_reducer_never_raises_price(reducer, tmp_path):
    # Operator manually dropped the price to $10; the due retail stage says
    # $18. The stage is satisfied: stamped done, no eBay call, price untouched.
    path = _write(tmp_path, _item(price=10.0))
    stats, after = _run(reducer, path)
    assert reducer._puts == []
    assert stats['skipped'] == 1
    assert stats['reduced'] == 0
    assert after['ebay_offer']['price'] == 10.0
    assert after['reprice_schedule'][1]['done_at'] is not None  # won't re-fire
    assert 'price_history' not in after                         # nothing applied


def test_equal_price_stage_is_satisfied_without_put(reducer, tmp_path):
    path = _write(tmp_path, _item(price=18.0))
    stats, after = _run(reducer, path)
    assert reducer._puts == []
    assert stats['skipped'] == 1
    assert after['ebay_offer']['price'] == 18.0


# ---------------------------------------------------------------------------
# C4 — full-replace offer body + price history
# ---------------------------------------------------------------------------

def test_offer_put_body_is_complete(reducer, tmp_path):
    # Offer PUT is full-replace (services.md §12–13, sync.py): the body must
    # carry the whole offer, not just pricingSummary (C4 fix, 2026-06-10).
    path = _write(tmp_path, _item())
    _run(reducer, path)
    body = reducer._puts[0]['body']
    required = {'sku', 'marketplaceId', 'format', 'availableQuantity',
                'categoryId', 'listingDescription', 'listingPolicies',
                'merchantLocationKey', 'pricingSummary'}
    assert required <= set(body)
    assert body['pricingSummary']['price']['value'] == '18.0'
    assert body['listingPolicies']['fulfillmentPolicyId'] == 'FC4'


def test_applied_reduction_appends_price_history(reducer, tmp_path):
    path = _write(tmp_path, _item())
    _, after = _run(reducer, path)
    assert after['price_history'] == [{
        'ts':             NOW.isoformat(),
        'price':          18.0,
        'previous_price': 24.99,
        'stage':          1,
        'label':          'retail',
        'source':         'ebay_price_reducer',
    }]


def test_price_history_accumulates_across_reductions(reducer, tmp_path):
    item = _item(price_history=[{'ts': PAST, 'price': 24.99,
                                 'previous_price': None, 'stage': 0,
                                 'label': 'launch', 'source': 'test-seed'}])
    path = _write(tmp_path, item)
    _, after = _run(reducer, path)
    assert len(after['price_history']) == 2
    assert after['price_history'][-1]['price'] == 18.0


# ---------------------------------------------------------------------------
# Session 41 regression — draft_listing.price must persist after a reduction
# ---------------------------------------------------------------------------
#
# Bug: the reducer mutated draft['price'] in memory but never included
# draft_listing in the fence_patch_item payload, so it was silently dropped
# every run. ebay_stage.py reads draft_listing.price FIRST, so the next
# ebay_stage run (for any reason — redraft, force restage) would push the
# stale pre-reduction price back live, silently reverting the markdown eBay
# had already accepted. Confirmed live on tgw202605051933258.

def test_draft_listing_price_persisted_after_reduction(reducer, tmp_path):
    path = _write(tmp_path, _item())
    _, after = _run(reducer, path)
    assert after['draft_listing']['price'] == 18.0


def test_draft_listing_price_matches_ebay_offer_price(reducer, tmp_path):
    """The two must never be allowed to drift apart again — ebay_stage.py
    reads draft_listing.price first, so if it lags ebay_offer.price a later
    stage re-run silently reverts the live markdown."""
    path = _write(tmp_path, _item())
    _, after = _run(reducer, path)
    assert after['draft_listing']['price'] == after['ebay_offer']['price']


def test_draft_listing_price_persisted_on_catchup(reducer, tmp_path):
    item = _item()
    item['reprice_schedule'][2]['due_at'] = PAST
    path = _write(tmp_path, item)
    _, after = _run(reducer, path)
    assert after['draft_listing']['price'] == 12.0


def test_ebay_write_failure_does_not_discard_draft_listing_price(reducer, tmp_path, monkeypatch):
    """Even if the ebay_offer.price deep-merge fails (e.g. the transient
    KeyError('api_key') crash confirmed in production logs), the more
    critical draft_listing/reprice_schedule/price_history bookkeeping — for a
    price change eBay has ALREADY accepted live — must not be lost."""
    def boom(*a, **k):
        raise KeyError('api_key')
    monkeypatch.setattr(reducer_mod, 'fence_ebay_write', boom)

    path = _write(tmp_path, _item())
    stats, after = _run(reducer, path)

    assert stats['reduced'] == 1
    assert after['draft_listing']['price'] == 18.0
    assert after['reprice_schedule'][1]['done_at'] is not None
    assert after['price_history'][-1]['price'] == 18.0
