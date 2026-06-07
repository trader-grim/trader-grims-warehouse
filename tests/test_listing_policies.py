"""PP-HINT-001 + PP-STORAGE-001 — fulfillment-policy resolution precedence.

The shipping (fulfillment) policy is resolved by precedence:
    per-item shipping_profile > per-category > per-size_class > global default
and the item's shipping_profile / size_class are threaded from the item JSON
into the offer body by _build_offer_bodies. All offline — no eBay call.
"""

import pytest

import tgw.ebay.sync as sync


def _cfg(**extra):
    base = {
        "payment_policy_id":     "PAY1",
        "return_policy_id":      "RET1",
        "fulfillment_policy_id": "GLOBAL",
        "raw": {},
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# _resolve_fulfillment_id precedence
# ---------------------------------------------------------------------------

def test_global_default():
    assert sync._resolve_fulfillment_id(_cfg(), "12345") == "GLOBAL"


def test_category_override_and_fallthrough():
    cfg = _cfg(fulfillment_policy_by_category={"12345": "CATPOL"})
    assert sync._resolve_fulfillment_id(cfg, "12345") == "CATPOL"
    assert sync._resolve_fulfillment_id(cfg, "99999") == "GLOBAL"  # no match -> global


def test_size_class_override_and_fallthrough():
    cfg = _cfg(fulfillment_policy_by_size_class={"flat": "FLATPOL"})
    assert sync._resolve_fulfillment_id(cfg, "12345", size_class="flat") == "FLATPOL"
    assert sync._resolve_fulfillment_id(cfg, "12345", size_class="huge") == "GLOBAL"


def test_category_beats_size_class():
    cfg = _cfg(
        fulfillment_policy_by_category={"12345": "CATPOL"},
        fulfillment_policy_by_size_class={"flat": "FLATPOL"},
    )
    assert sync._resolve_fulfillment_id(cfg, "12345", size_class="flat") == "CATPOL"


def test_shipping_profile_mapped_name():
    cfg = _cfg(fulfillment_policy_by_profile={"oversize": "OVERPOL"})
    assert sync._resolve_fulfillment_id(cfg, "12345", shipping_profile="oversize") == "OVERPOL"


def test_shipping_profile_raw_id_passthrough():
    # An unmapped profile string is treated as a raw policy id.
    assert sync._resolve_fulfillment_id(_cfg(), "12345", shipping_profile="RAW-POL-9") == "RAW-POL-9"


def test_shipping_profile_beats_everything():
    cfg = _cfg(
        fulfillment_policy_by_category={"12345": "CATPOL"},
        fulfillment_policy_by_size_class={"flat": "FLATPOL"},
        fulfillment_policy_by_profile={"oversize": "OVERPOL"},
    )
    got = sync._resolve_fulfillment_id(
        cfg, "12345", shipping_profile="oversize", size_class="flat")
    assert got == "OVERPOL"


def test_get_listing_policies_assembles_dict():
    cfg = _cfg(fulfillment_policy_by_size_class={"flat": "FLATPOL"})
    pol = sync._get_listing_policies(cfg, "12345", size_class="flat")
    assert pol == {
        "fulfillmentPolicyId": "FLATPOL",
        "paymentPolicyId":     "PAY1",
        "returnPolicyId":      "RET1",
    }


# ---------------------------------------------------------------------------
# _build_offer_bodies threads item fields into policy resolution
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_ebay(monkeypatch):
    monkeypatch.setattr(sync, "_get_merchant_location", lambda _cfg: "LOC-1")


def _item(**extra):
    base = {
        "draft_listing": {
            "price":          "9.99",
            "title":          "Thing",
            "description":    "d",
            "imageUrls":      ["https://example.com/1.jpg"],
            "condition_enum": "USED_GOOD",
            "category_id":    "12345",
            "quantity":       1,
        },
    }
    base.update(extra)
    return base


def test_offer_body_uses_item_shipping_profile():
    cfg = _cfg(fulfillment_policy_by_profile={"oversize": "OVERPOL"})
    _, offer = sync._build_offer_bodies(cfg, "tgw1", _item(shipping_profile="oversize"))
    assert offer["listingPolicies"]["fulfillmentPolicyId"] == "OVERPOL"


def test_offer_body_uses_item_size_class():
    cfg = _cfg(fulfillment_policy_by_size_class={"flat": "FLATPOL"})
    _, offer = sync._build_offer_bodies(cfg, "tgw1", _item(size_class="flat"))
    assert offer["listingPolicies"]["fulfillmentPolicyId"] == "FLATPOL"


def test_offer_body_defaults_to_global_without_overrides():
    _, offer = sync._build_offer_bodies(_cfg(), "tgw1", _item())
    assert offer["listingPolicies"]["fulfillmentPolicyId"] == "GLOBAL"
