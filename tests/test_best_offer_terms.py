"""todo #1256 — per-item Best Offer control.

offer.listingPolicies.bestOfferTerms is a per-item eBay Inventory API field,
not an account default. Previously not exposed anywhere in TGW: no
draft_listing field, no operator form control, no wiring into
_build_offer_bodies()'s listingPolicies construction. Whatever a listing
showed was either an eBay category default or an untracked manual Seller
Hub change (invariant C11 drift class).

All eBay API calls are mocked -- tests pass completely offline.
"""

from __future__ import annotations

import pytest

import tgw.ebay.sync as sync


@pytest.fixture(autouse=True)
def _no_ebay_calls(monkeypatch):
    monkeypatch.setattr(sync, '_get_merchant_location', lambda _cfg: 'LOC-1')
    monkeypatch.setattr(sync, '_get_store_categories_cached', lambda _cfg: [])
    monkeypatch.setattr(sync, '_resolve_store_category_names', lambda _cfg, _cat: None)


def _item(**extra_draft):
    draft = {
        'price':          '9.99',
        'title':          'Remote Control',
        'description':    'A remote.',
        'imageUrls':      ['https://example.com/1.jpg'],
        'condition_enum': 'USED_GOOD',
        'category_id':    '14999',
        'quantity':       1,
    }
    draft.update(extra_draft)
    return {'draft_listing': draft}


@pytest.fixture
def cfg_sync():
    return {
        'fulfillment_policy_id': 'FC4',
        'payment_policy_id':     'PAY1',
        'return_policy_id':      'RET1',
        'raw': {},
    }


def test_best_offer_omitted_when_unset(cfg_sync):
    """Operator hasn't touched the field — don't send bestOfferTerms at all,
    so eBay's existing category default (or prior state) is left untouched."""
    item = _item()
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    assert 'bestOfferTerms' not in offer['listingPolicies']


def test_best_offer_enabled_true(cfg_sync):
    item = _item(best_offer_enabled=True)
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    assert offer['listingPolicies']['bestOfferTerms'] == {'bestOfferEnabled': True}


def test_best_offer_explicitly_disabled(cfg_sync):
    """False is a real, meaningful choice (operator turning OFF a category
    default) -- must still send bestOfferEnabled: false, not omit the field."""
    item = _item(best_offer_enabled=False)
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    assert offer['listingPolicies']['bestOfferTerms'] == {'bestOfferEnabled': False}


def test_best_offer_with_auto_accept_and_decline_prices(cfg_sync):
    item = _item(
        best_offer_enabled=True,
        best_offer_auto_accept_price=45.00,
        best_offer_auto_decline_price=20,
    )
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    terms = offer['listingPolicies']['bestOfferTerms']
    assert terms['bestOfferEnabled'] is True
    assert terms['autoAcceptPrice'] == {'currency': 'USD', 'value': '45.00'}
    assert terms['autoDeclinePrice'] == {'currency': 'USD', 'value': '20.00'}


def test_best_offer_enabled_without_prices_omits_price_fields(cfg_sync):
    item = _item(best_offer_enabled=True)
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    terms = offer['listingPolicies']['bestOfferTerms']
    assert 'autoAcceptPrice' not in terms
    assert 'autoDeclinePrice' not in terms
