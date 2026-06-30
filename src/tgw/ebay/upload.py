"""
tgw.ebay.upload — Upload item photos to eBay via UploadSiteHostedPictures.

Uses the eBay Trading API multipart POST to upload a local photo and return
the eBay-hosted EPS URL.  Callers should use the ebay_upload queue worker
rather than calling this directly.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict

import requests

from tgw.apis.ebay.client import load_token

log = logging.getLogger(__name__)

_TRADING_ENDPOINT = 'https://api.ebay.com/ws/api.dll'
_API_VERSION = '1155'
_SITE_ID = '0'   # EBAY_US

_NS = 'urn:ebay:apis:eBLBaseComponents'

_MIME = {
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png':  'image/png',
    '.gif':  'image/gif',
    '.tif':  'image/tiff',
    '.tiff': 'image/tiff',
}


def upload_photo(cfg: Dict[str, Any], photo_path: Path) -> str:
    """
    Upload *photo_path* to eBay EPS and return the eBay-hosted FullURL.

    Raises FileNotFoundError if the photo does not exist.
    Raises RuntimeError if eBay rejects the upload.
    Raises requests.exceptions.* on network failures (caller may retry).
    """
    if not photo_path.exists():
        raise FileNotFoundError(f'photo not found: {photo_path}')

    token = load_token(cfg)
    mime = _MIME.get(photo_path.suffix.lower(), 'image/jpeg')

    xml_payload = (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<UploadSiteHostedPicturesRequest xmlns="{_NS}">'
        f'<PictureName>{photo_path.stem}</PictureName>'
        '<PictureSet>Supersize</PictureSet>'
        '</UploadSiteHostedPicturesRequest>'
    )

    headers = {
        'X-EBAY-API-IAF-TOKEN':          token,
        'X-EBAY-API-COMPATIBILITY-LEVEL': _API_VERSION,
        'X-EBAY-API-CALL-NAME':          'UploadSiteHostedPictures',
        'X-EBAY-API-SITEID':             _SITE_ID,
    }

    # requests builds multipart/form-data automatically from the files dict
    files = {
        'XML Payload': ('', xml_payload.encode('utf-8'), 'text/xml;charset=utf-8'),
        'image':       (photo_path.name, photo_path.read_bytes(), mime),
    }

    resp = requests.post(_TRADING_ENDPOINT, headers=headers, files=files, timeout=90)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    ack = root.findtext(f'{{{_NS}}}Ack') or ''

    if ack not in ('Success', 'Warning'):
        msgs = root.findall(f'.//{{{_NS}}}ShortMessage')
        error_text = '; '.join(m.text or '' for m in msgs) or 'unknown error'
        raise RuntimeError(f'UploadSiteHostedPictures failed ({ack}): {error_text}')

    url = root.findtext(f'{{{_NS}}}SiteHostedPictureDetails/{{{_NS}}}FullURL') or ''
    if not url:
        raise RuntimeError('UploadSiteHostedPictures: no FullURL in response')

    log.info('uploaded %s → %s', photo_path.name, url[:60])
    return url
