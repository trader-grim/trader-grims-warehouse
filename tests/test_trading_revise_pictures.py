"""PP-PHOTOSYNC-001 P10 — revise_item_pictures (Trading API in-place photo repair
for legacy Item# listings ebay_stage's relist guard refuses to touch via the
Inventory API)."""

from tgw.apis.ebay.trading import revise_item_pictures


def test_revise_item_pictures_sends_item_id_and_all_urls(monkeypatch):
    calls = []
    monkeypatch.setattr('tgw.apis.ebay.trading.trading_call',
                        lambda cfg, call_name, xml_body, **kw: calls.append((call_name, xml_body)))

    revise_item_pictures({}, '226700000001', ['https://eps/1.jpg', 'https://eps/2.jpg'])

    assert len(calls) == 1
    call_name, xml_body = calls[0]
    assert call_name == 'ReviseFixedPriceItem'
    assert '<ItemID>226700000001</ItemID>' in xml_body
    assert '<PictureURL>https://eps/1.jpg</PictureURL>' in xml_body
    assert '<PictureURL>https://eps/2.jpg</PictureURL>' in xml_body
    # Only ItemID + PictureDetails — must not touch price/title/description.
    assert '<Title>' not in xml_body
    assert '<StartPrice>' not in xml_body


def test_revise_item_pictures_propagates_call_failure(monkeypatch):
    def _raise(cfg, call_name, xml_body, **kw):
        raise RuntimeError('ReviseFixedPriceItem failed: item suspended')

    monkeypatch.setattr('tgw.apis.ebay.trading.trading_call', _raise)

    import pytest
    with pytest.raises(RuntimeError, match='suspended'):
        revise_item_pictures({}, '226700000001', ['https://eps/1.jpg'])
