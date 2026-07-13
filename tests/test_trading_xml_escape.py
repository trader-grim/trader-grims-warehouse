"""SECURITY audit#COHESION-2026-07, todo #1277 — Trading API XML builders must
escape caller-supplied string values (end_item, revise_item_sku,
revise_item_pictures, respond_to_best_offer) so a malicious value can't
inject extra XML structure into the outbound request body."""

import xml.etree.ElementTree as ET

from tgw.apis.ebay.trading import (
    end_item,
    respond_to_best_offer,
    revise_item_pictures,
    revise_item_sku,
)


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


def test_end_item_escapes_malicious_reason(monkeypatch):
    calls = _capture(monkeypatch)
    malicious = 'x</EndingReason><Evil>y</Evil><EndingReason>'

    end_item({}, '226700000001', reason=malicious)

    _, xml_body = calls[0]
    assert '&lt;' in xml_body
    assert '&gt;' in xml_body
    assert '<Evil>' not in xml_body
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


def test_revise_item_pictures_escapes_malicious_url(monkeypatch):
    calls = _capture(monkeypatch)
    malicious = 'https://eps/1.jpg</PictureURL><Evil>y</Evil><PictureURL>x'

    revise_item_pictures({}, '226700000001', [malicious])

    _, xml_body = calls[0]
    assert '&lt;' in xml_body
    assert '&gt;' in xml_body
    assert '<Evil>' not in xml_body


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
    end_item({}, '226700000001', reason='NotAvailable')
    _, xml_body = calls[0]
    assert '<ItemID>226700000001</ItemID>' in xml_body
    assert '<EndingReason>NotAvailable</EndingReason>' in xml_body

    calls.clear()
    revise_item_pictures({}, '226700000001', ['https://i.ebayimg.com/images/g/abc/s-l1600.jpg'])
    _, xml_body = calls[0]
    assert '<PictureURL>https://i.ebayimg.com/images/g/abc/s-l1600.jpg</PictureURL>' in xml_body

    calls.clear()
    respond_to_best_offer({}, offer_id='5551234567890', listing_id='226700000001', action='Accept')
    _, xml_body = calls[0]
    assert '<BestOfferID>5551234567890</BestOfferID>' in xml_body
    assert '<Action>Accept</Action>' in xml_body
