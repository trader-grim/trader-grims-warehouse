"""Tests for tgw.ebay.sync._build_offer_bodies.

Focus (PP-GLOBALS-001): the operator-captured weight_oz field must flow into
the Inventory API body as packageWeightAndSize, with a zero/None/non-numeric
guard (eBay rejects weight.value == 0).

_build_offer_bodies is a pure function; the only eBay call on its path is
_get_merchant_location, which we stub so no network/token is required.
"""

import pytest

import tgw.ebay.sync as sync


@pytest.fixture
def cfg():
    # All three policy IDs present so _get_listing_policies returns from config
    # and never falls back to the eBay account API.
    return {
        'fulfillment_policy_id': 'FC4',
        'payment_policy_id':     'PAY1',
        'return_policy_id':      'RET1',
        'raw': {},
    }


@pytest.fixture(autouse=True)
def _no_ebay_calls(monkeypatch):
    """Stub the one account-API call _build_offer_bodies makes."""
    monkeypatch.setattr(sync, '_get_merchant_location', lambda _cfg: 'LOC-1')


def _item(**extra):
    base = {
        'draft_listing': {
            'price':          '19.99',
            'title':          'Test Widget',
            'description':    'A test widget',
            'imageUrls':      ['https://example.com/1.jpg'],
            'condition_enum': 'USED_GOOD',
            'category_id':    '12345',
            'quantity':       1,
        },
    }
    base.update(extra)
    return base


def test_weight_oz_present_adds_package_weight(cfg):
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', _item(weight_oz=4.5))
    assert inv_body['packageWeightAndSize'] == {
        'weight': {'value': 4.5, 'unit': 'OUNCE'},
    }


def test_weight_oz_string_is_coerced(cfg):
    # The intake form / item JSON may store weight_oz as a string.
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', _item(weight_oz='12'))
    assert inv_body['packageWeightAndSize']['weight']['value'] == 12.0


def test_weight_oz_absent_omits_block(cfg):
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', _item())
    assert 'packageWeightAndSize' not in inv_body


def test_weight_oz_zero_omits_block(cfg):
    # eBay rejects weight.value == 0 — must not emit the block.
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', _item(weight_oz=0))
    assert 'packageWeightAndSize' not in inv_body


def test_weight_oz_empty_string_omits_block(cfg):
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', _item(weight_oz=''))
    assert 'packageWeightAndSize' not in inv_body


def test_weight_oz_nonnumeric_omits_block(cfg):
    # Bad data must degrade gracefully, not raise.
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', _item(weight_oz='heavy'))
    assert 'packageWeightAndSize' not in inv_body


def test_offer_body_still_builds_without_weight(cfg):
    # Sanity: the rest of the contract is unaffected by the new block.
    inv_body, offer_body = sync._build_offer_bodies(cfg, 'tgw0001', _item(weight_oz=4.5))
    assert offer_body['sku'] == 'tgw0001'
    assert offer_body['pricingSummary']['price'] == {'currency': 'USD', 'value': '19.99'}
    assert inv_body['condition'] == 'USED_GOOD'
