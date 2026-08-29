"""Regression: the retired Trading XML upload contract is absent."""

from pathlib import Path

from tgw.ebay import upload


def test_upload_adapter_contains_no_trading_xml_payload_or_endpoint():
    source = Path(upload.__file__).read_text(encoding='utf-8')
    assert 'ws/api.dll' not in source
    assert ('Upload' + 'SiteHostedPictures') not in source
    assert 'xml.etree.ElementTree' not in source
