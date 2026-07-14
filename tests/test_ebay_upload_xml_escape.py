"""todo #1399 / PP-DEADLETTER-001 — UploadSiteHostedPictures XML payload
must escape PictureName.

Root cause: 3 ebay_upload dead-letters ("XML Parse error" from eBay's
Trading API) traced to raw f-string interpolation of `photo_path.stem`
into the request XML. Confirmed live: all 3 affected SKUs' photo
filenames contain a literal `&` (e.g. "Heartfelt Friends - Gramma &
Grampa and Tabby Cat-0.jpg", "Better Homes & Gardens_ Wood Magazine
-April 1999 Issue No. 114-0.jpg", "Car Muffler & Brake Embroidered Patch
2x3.25-Inches-0.jpg"), which produced malformed XML that eBay's parser
correctly rejected.
"""

import xml.etree.ElementTree as ET

from tgw.ebay.upload import _NS, _build_upload_payload


def test_unsafe_characters_produce_well_formed_xml_roundtrip():
    name = 'Heartfelt Friends - Gramma & Grampa and Tabby Cat-0'
    payload = _build_upload_payload(name)

    # Must parse without error (this is exactly what eBay's parser does).
    root = ET.fromstring(payload)
    picture_name = root.findtext(f'{{{_NS}}}PictureName')

    assert picture_name == name, 'escaped-then-parsed text must round-trip to the original'


def test_all_three_confirmed_unsafe_filenames_round_trip():
    names = [
        'Heartfelt Friends - Gramma & Grampa and Tabby Cat-0',
        'Better Homes & Gardens_ Wood Magazine -April 1999 Issue No. 114-0',
        'Car Muffler & Brake Embroidered Patch 2x3.25-Inches-0',
    ]
    for name in names:
        payload = _build_upload_payload(name)
        root = ET.fromstring(payload)
        assert root.findtext(f'{{{_NS}}}PictureName') == name


def test_less_than_and_greater_than_also_escaped():
    name = '<Weird> "Item" Name'
    payload = _build_upload_payload(name)
    root = ET.fromstring(payload)
    assert root.findtext(f'{{{_NS}}}PictureName') == name


def test_normal_ascii_filename_no_regression():
    name = 'Nice-Clean-Filename-0'
    payload = _build_upload_payload(name)

    root = ET.fromstring(payload)
    assert root.findtext(f'{{{_NS}}}PictureName') == name
    assert root.findtext(f'{{{_NS}}}PictureSet') == 'Supersize'
    assert root.tag == f'{{{_NS}}}UploadSiteHostedPicturesRequest'
    assert payload.startswith('<?xml version="1.0" encoding="utf-8"?>')
