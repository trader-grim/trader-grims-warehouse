"""Tests for tgw.ebay.sync._build_offer_bodies.

Focus (PP-GLOBALS-001): the operator-captured weight_oz field must flow into
the Inventory API body as packageWeightAndSize, with a zero/None/non-numeric
guard (eBay rejects weight.value == 0).

_build_offer_bodies is a pure function; the only eBay call on its path is
_get_merchant_location, which we stub so no network/token is required.
"""

import pytest

import tgw.ebay.sync as sync

# Captured before the autouse fixture below stubs sync._is_motors_category,
# so tests targeting the real function directly aren't testing the stub.
_real_is_motors_category = sync._is_motors_category


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
    """Stub the account-API calls _build_offer_bodies makes, including the
    Motors category-tree check (todo #1254) — defaults to EBAY_US (not
    Motors) unless a test explicitly overrides it."""
    monkeypatch.setattr(sync, '_get_merchant_location', lambda _cfg: 'LOC-1')
    monkeypatch.setattr(sync, '_is_motors_category', lambda cfg, category_id: False)


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


# ---------------------------------------------------------------------------
# Todo #1462: eBay's Inventory API rejects an empty-string aspect value with
# a garbled generic errorId 25002 whose message dumps the entire aspects
# dict rather than naming the offending field (confirmed live 2026-07-16 on
# tgw202605040949058, right after #1461 started correctly persisting an
# operator's cleared aspect field as an explicit ""). The internal record
# must keep "" as the real cleared value (see draft_specifics.py), but the
# push to eBay must omit that aspect key entirely — this PUT is a full
# replace of product.aspects, so omitting achieves the intended "clear this
# aspect on eBay" outcome.
# ---------------------------------------------------------------------------

def test_empty_string_aspect_omitted_from_push(cfg):
    item = _item(**{
        'draft_listing': {
            **_item()['draft_listing'],
            'item_specifics': {'Material': '', 'Type': 'Brooch'},
        },
    })
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', item)
    aspects = inv_body['product']['aspects']
    assert 'Material' not in aspects
    assert aspects['Type'] == ['Brooch']


def test_none_aspect_also_omitted_from_push(cfg):
    item = _item(**{
        'draft_listing': {
            **_item()['draft_listing'],
            'item_specifics': {'Material': None, 'Type': 'Brooch'},
        },
    })
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', item)
    aspects = inv_body['product']['aspects']
    assert 'Material' not in aspects
    assert aspects['Type'] == ['Brooch']


def test_non_empty_aspect_still_pushed(cfg):
    item = _item(**{
        'draft_listing': {
            **_item()['draft_listing'],
            'item_specifics': {'Material': 'Sterling Silver', 'Type': 'Brooch'},
        },
    })
    inv_body, _ = sync._build_offer_bodies(cfg, 'tgw0001', item)
    aspects = inv_body['product']['aspects']
    assert aspects['Material'] == ['Sterling Silver']


# ---------------------------------------------------------------------------
# audit#1143 #1254 — marketplaceId is never hardcoded to EBAY_US
# ---------------------------------------------------------------------------

class TestMarketplaceIdNeverHardcoded:
    """PP-EBAY-MOTORS-001 follow-up: eBay Motors is a genuinely SEPARATE
    category tree (ID 100) from EBAY_US (ID 0), confirmed live 2026-07-09 —
    NOT a branch of the EBAY_US tree as an earlier planning pass assumed.
    eBay rejects createOffer when marketplaceId/categoryId disagree, so a
    hardcoded EBAY_US would fail outright for a genuinely new item in a
    Motors-tree category."""

    def test_offer_body_defaults_to_ebay_us_for_non_motors_category(self, cfg):
        _inv, offer_body = sync._build_offer_bodies(cfg, 'tgw0001', _item())
        assert offer_body['marketplaceId'] == 'EBAY_US'

    def test_offer_body_uses_motors_marketplace_when_category_is_motors_tree(self, cfg, monkeypatch):
        monkeypatch.setattr(sync, '_is_motors_category', lambda cfg, category_id: True)
        # For a Motors offer, listing policies bypass config entirely and
        # resolve via the account API scoped to EBAY_MOTORS (code-review
        # follow-up to #1254/#1255) — stub that live call.
        monkeypatch.setattr(sync, '_get_policies', lambda cfg, marketplace_id=None: {
            'fulfillmentPolicyId': 'MOTORS_FC', 'paymentPolicyId': 'MOTORS_PAY',
            'returnPolicyId': 'MOTORS_RET',
        })
        _inv, offer_body = sync._build_offer_bodies(cfg, 'tgw0001', _item())
        assert offer_body['marketplaceId'] == 'EBAY_MOTORS'
        assert offer_body['listingPolicies']['fulfillmentPolicyId'] == 'MOTORS_FC'

    def test_is_motors_category_delegates_to_taxonomy_module(self, cfg, monkeypatch):
        # todo #1255: the real membership check (disk+memory Motors tree
        # cache) now lives in tgw.apis.ebay.taxonomy — see
        # tests/test_motors_category_tree.py. sync._is_motors_category is
        # just a thin pass-through.
        import tgw.apis.ebay.taxonomy as taxonomy

        calls = []
        monkeypatch.setattr(taxonomy, 'is_motors_category',
                            lambda cfg, category_id: calls.append(category_id) or True)

        assert _real_is_motors_category(cfg, '100449') is True
        assert calls == ['100449']


class TestFindOfferNoMarketplaceFilter:
    def test_find_offer_query_has_no_marketplace_filter(self, monkeypatch):
        calls = []

        def _fake_get(cfg, path, params=None):
            calls.append(params)
            return {'offers': [{'offerId': 'O1', 'marketplaceId': 'EBAY_MOTORS'}]}

        monkeypatch.setattr(sync, 'ebay_get', _fake_get)
        result = sync._find_offer({}, 'tgwSKU')

        assert calls == [{'sku': 'tgwSKU'}]  # no marketplace_id key at all
        assert result == {'offerId': 'O1', 'marketplaceId': 'EBAY_MOTORS'}

    def test_find_offer_does_not_turn_a_failed_lookup_into_create_permission(
        self, monkeypatch,
    ):
        """An unavailable lookup is unknown, never evidence of no offer."""
        def _fail(cfg, path, params=None):
            raise RuntimeError("quota lookup unavailable")

        monkeypatch.setattr(sync, 'ebay_get', _fail)
        with pytest.raises(RuntimeError, match="quota lookup unavailable"):
            sync._find_offer({}, 'tgwSKU')


class TestStageDraftUsesExistingOfferMarketplace:
    def test_update_path_overrides_marketplace_id_with_existing_offer_truth(self, cfg, monkeypatch):
        # Category-based guess would say EBAY_US, but the EXISTING live
        # offer is actually on EBAY_MOTORS — the update must use the
        # ground truth, never the guess.
        def _boom(cfg, category_id):
            raise AssertionError('_is_motors_category must not be called '
                                 'when an existing offer is found (code-review '
                                 'follow-up to #1254 — ground truth skips the guess)')
        monkeypatch.setattr(sync, '_is_motors_category', _boom)
        monkeypatch.setattr(sync, '_find_offer',
                            lambda cfg, sku: {'offerId': 'O1', 'marketplaceId': 'EBAY_MOTORS'})
        monkeypatch.setattr(sync, '_get_policies', lambda cfg, marketplace_id=None: {
            'fulfillmentPolicyId': 'MOTORS_FC', 'paymentPolicyId': 'MOTORS_PAY',
            'returnPolicyId': 'MOTORS_RET',
        })

        put_calls = []
        monkeypatch.setattr(sync, 'ebay_put', lambda cfg, path, body: put_calls.append((path, body)))

        sync.stage_draft(cfg, 'tgw0001', _item())

        # ebay_put is called twice: inventory_item upsert, then the offer
        # update — only the offer PUT carries marketplaceId.
        offer_puts = [(p, b) for p, b in put_calls if '/offer/' in p]
        assert len(offer_puts) == 1
        _path, body = offer_puts[0]
        assert body['marketplaceId'] == 'EBAY_MOTORS'

    def test_create_path_uses_category_based_guess_when_no_existing_offer(self, cfg, monkeypatch):
        monkeypatch.setattr(sync, '_is_motors_category', lambda cfg, category_id: True)
        monkeypatch.setattr(sync, '_find_offer', lambda cfg, sku: None)
        monkeypatch.setattr(sync, 'ebay_put', lambda cfg, path, body: None)
        monkeypatch.setattr(sync, '_get_policies', lambda cfg, marketplace_id=None: {
            'fulfillmentPolicyId': 'MOTORS_FC', 'paymentPolicyId': 'MOTORS_PAY',
            'returnPolicyId': 'MOTORS_RET',
        })

        post_calls = []

        def _fake_post(cfg, path, body):
            post_calls.append((path, body))
            return {'offerId': 'NEW1'}

        monkeypatch.setattr(sync, 'ebay_post', _fake_post)

        sync.stage_draft(cfg, 'tgw0001', _item())

        assert len(post_calls) == 1
        _path, body = post_calls[0]
        assert body['marketplaceId'] == 'EBAY_MOTORS'

    def test_find_offer_called_before_build_offer_bodies(self, cfg, monkeypatch):
        # Ordering matters: _find_offer must run first so its result can be
        # passed into _build_offer_bodies as known_marketplace_id.
        call_order = []

        def _tracking_find_offer(cfg, sku):
            call_order.append('_find_offer')
            return None

        def _tracking_build(cfg, sku, item, **kw):
            call_order.append('_build_offer_bodies')
            return ({'condition': 'USED_GOOD'}, {'sku': sku, 'marketplaceId': 'EBAY_US'})

        monkeypatch.setattr(sync, '_find_offer', _tracking_find_offer)
        monkeypatch.setattr(sync, '_build_offer_bodies', _tracking_build)
        monkeypatch.setattr(sync, 'ebay_put', lambda cfg, path, body: None)
        monkeypatch.setattr(sync, 'ebay_post', lambda cfg, path, body: {'offerId': 'NEW1'})

        sync.stage_draft(cfg, 'tgw0001', _item())

        assert call_order == ['_find_offer', '_build_offer_bodies']


class TestFindOfferAmbiguousOffers:
    def test_multiple_offers_raises_ambiguous_offer_error(self, monkeypatch):
        def _fake_get(cfg, path, params=None):
            return {'offers': [
                {'offerId': 'O1', 'marketplaceId': 'EBAY_US'},
                {'offerId': 'O2', 'marketplaceId': 'EBAY_MOTORS'},
            ]}

        monkeypatch.setattr(sync, 'ebay_get', _fake_get)

        with pytest.raises(sync.AmbiguousOfferError, match='EBAY_MOTORS'):
            sync._find_offer({}, 'tgwSKU')

    def test_single_offer_does_not_raise(self, monkeypatch):
        def _fake_get(cfg, path, params=None):
            return {'offers': [{'offerId': 'O1', 'marketplaceId': 'EBAY_US'}]}

        monkeypatch.setattr(sync, 'ebay_get', _fake_get)

        result = sync._find_offer({}, 'tgwSKU')
        assert result == {'offerId': 'O1', 'marketplaceId': 'EBAY_US'}


class TestListingPoliciesMarketplaceScoped:
    """Code-review follow-up to #1254/#1255: tgw-api-config.json's
    fulfillment_policy_id/payment_policy_id/return_policy_id are EBAY_US
    business policies. eBay scopes business policies per marketplace, so
    reusing them for a Motors offer would get rejected — _get_listing_policies
    must bypass config entirely for a non-EBAY_US marketplace and resolve
    via the account API scoped to that marketplace instead."""

    def test_ebay_us_still_uses_config_values(self, cfg, monkeypatch):
        def _boom(cfg, marketplace_id=None):
            raise AssertionError('must not hit the account API when config is complete')
        monkeypatch.setattr(sync, '_get_policies', _boom)

        policies = sync._get_listing_policies(cfg, '12345')
        assert policies['fulfillmentPolicyId'] == 'FC4'
        assert policies['paymentPolicyId'] == 'PAY1'
        assert policies['returnPolicyId'] == 'RET1'

    def test_motors_bypasses_config_and_uses_account_api_for_that_marketplace(self, cfg, monkeypatch):
        calls = []

        def _fake_get_policies(cfg, marketplace_id=None):
            calls.append(marketplace_id)
            return {'fulfillmentPolicyId': 'MOTORS_FC', 'paymentPolicyId': 'MOTORS_PAY',
                    'returnPolicyId': 'MOTORS_RET'}

        monkeypatch.setattr(sync, '_get_policies', _fake_get_policies)

        policies = sync._get_listing_policies(cfg, '100449', marketplace_id='EBAY_MOTORS')

        assert calls == ['EBAY_MOTORS']
        # config's FC4/PAY1/RET1 (EBAY_US policies) must NOT leak through
        assert policies == {'fulfillmentPolicyId': 'MOTORS_FC', 'paymentPolicyId': 'MOTORS_PAY',
                            'returnPolicyId': 'MOTORS_RET'}

    def test_get_policies_is_cached_per_marketplace_not_globally(self, monkeypatch):
        sync._policies_cache.clear()
        calls = []

        def _fake_get(cfg, path, params=None):
            calls.append((path, params['marketplace_id']))
            if 'fulfillment' in path:
                return {'fulfillmentPolicies': [{'fulfillmentPolicyId': f"FC-{params['marketplace_id']}"}]}
            if 'payment' in path:
                return {'paymentPolicies': [{'paymentPolicyId': f"PAY-{params['marketplace_id']}"}]}
            return {'returnPolicies': [{'returnPolicyId': f"RET-{params['marketplace_id']}"}]}

        monkeypatch.setattr(sync, 'ebay_get', _fake_get)

        us = sync._get_policies({}, marketplace_id='EBAY_US')
        motors = sync._get_policies({}, marketplace_id='EBAY_MOTORS')

        assert us['fulfillmentPolicyId'] == 'FC-EBAY_US'
        # Cached under the OFFER-level 'EBAY_MOTORS' key (what callers pass),
        # even though the underlying API call used the translated
        # 'EBAY_MOTORS_US' value — see test_account_api_translates_motors_
        # marketplace_id below.
        assert motors['fulfillmentPolicyId'] == 'FC-EBAY_MOTORS_US'
        assert us != motors
        # 3 calls per marketplace (fulfillment/payment/return) = 6 total, not deduped across marketplaces
        assert len(calls) == 6

        # Re-fetching the same marketplace uses the cache, not a new call.
        calls.clear()
        sync._get_policies({}, marketplace_id='EBAY_US')
        assert calls == []

    def test_account_api_translates_motors_marketplace_id(self, monkeypatch):
        # eBay's Account API (Business Policies) uses 'EBAY_MOTORS_US', not
        # the offer-level 'EBAY_MOTORS' — confirmed live 2026-07-10 (same
        # per-API-family enum inconsistency as the Taxonomy API found in
        # #1254). _get_policies must translate for the live call while
        # still being invoked with the offer-level value by callers.
        sync._policies_cache.clear()
        calls = []

        def _fake_get(cfg, path, params=None):
            calls.append(params['marketplace_id'])
            fields = {
                'fulfillment': ('fulfillmentPolicies', 'fulfillmentPolicyId'),
                'payment':     ('paymentPolicies', 'paymentPolicyId'),
                'return':      ('returnPolicies', 'returnPolicyId'),
            }
            for frag, (list_key, id_field) in fields.items():
                if frag in path:
                    return {list_key: [{id_field: 'X'}]}
            raise AssertionError(f'unexpected path {path}')

        monkeypatch.setattr(sync, 'ebay_get', _fake_get)

        sync._get_policies({}, marketplace_id='EBAY_MOTORS')

        assert calls == ['EBAY_MOTORS_US'] * 3

    def test_account_api_does_not_translate_ebay_us(self, monkeypatch):
        sync._policies_cache.clear()
        calls = []

        def _fake_get(cfg, path, params=None):
            calls.append(params['marketplace_id'])
            fields = {
                'fulfillment': ('fulfillmentPolicies', 'fulfillmentPolicyId'),
                'payment':     ('paymentPolicies', 'paymentPolicyId'),
                'return':      ('returnPolicies', 'returnPolicyId'),
            }
            for frag, (list_key, id_field) in fields.items():
                if frag in path:
                    return {list_key: [{id_field: 'X'}]}
            raise AssertionError(f'unexpected path {path}')

        monkeypatch.setattr(sync, 'ebay_get', _fake_get)

        sync._get_policies({}, marketplace_id='EBAY_US')

        assert calls == ['EBAY_US'] * 3
