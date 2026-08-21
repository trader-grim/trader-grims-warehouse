"""SECURITY audit#COHESION-2026-07, todo #1277 — Trading API XML builders must
escape caller-supplied string values (revise_item_sku and
respond_to_best_offer) so a malicious value can't
inject extra XML structure into the outbound request body."""

import xml.etree.ElementTree as ET

from tgw.apis.ebay.trading import respond_to_best_offer, revise_item_sku


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(
        'tgw.apis.ebay.trading.trading_call',
        lambda cfg, call_name, xml_body, **kw: calls.append((call_name, xml_body)),
    )
    return calls


def test_revise_item_sku_escapes_malicious_new_sku(monkeypatch):
    calls = _capture(monkeypatch)
    malicious = 'tgw123"/><Evil>x</Evil><SKU>'

    revise_item_sku({}, '226700000001', malicious)

    _, xml_body = calls[0]
    # xml.sax.saxutils.escape() only escapes &, <, > by default — quotes
    # don't need escaping in element text content (only in attribute
    # values, which none of these builders use for caller-supplied data).
    assert '&gt;' in xml_body
    assert '&lt;' in xml_body
    assert '<Evil>' not in xml_body
    # Whole document must still parse as well-formed XML with no injected
    # sibling elements.
    root = ET.fromstring(xml_body)
    assert root.find('.//Evil') is None


def test_respond_to_best_offer_escapes_malicious_offer_id(monkeypatch):
    calls = _capture(monkeypatch)
    malicious = '123</BestOfferID><Evil>y</Evil><BestOfferID>'

    respond_to_best_offer({}, offer_id=malicious, listing_id='226700000001', action='Accept')

    _, xml_body = calls[0]
    assert '&lt;' in xml_body
    assert '&gt;' in xml_body
    assert '<Evil>' not in xml_body
    root = ET.fromstring(xml_body)
    assert root.find('.//Evil') is None


def test_normal_values_pass_through_unchanged(monkeypatch):
    """Ordinary characters (real SKU, numeric listing_id, HTTPS EPS URL) must
    be byte-identical to the pre-escaping output — no regression to the
    common case."""
    calls = _capture(monkeypatch)

    revise_item_sku({}, '226700000001', 'tgw20260713120000123')
    _, xml_body = calls[0]
    assert '<ItemID>226700000001</ItemID>' in xml_body
    assert '<SKU>tgw20260713120000123</SKU>' in xml_body

    calls.clear()
    respond_to_best_offer({}, offer_id='5551234567890', listing_id='226700000001', action='Accept')
    _, xml_body = calls[0]
    assert '<BestOfferID>5551234567890</BestOfferID>' in xml_body
    assert '<Action>Accept</Action>' in xml_body
