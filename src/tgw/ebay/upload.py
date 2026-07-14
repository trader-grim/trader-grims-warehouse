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

from tgw import quota
from tgw.apis.ebay.client import capture_response, load_token

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


def _build_upload_payload(picture_name: str) -> str:
    """
    Build the UploadSiteHostedPicturesRequest XML body.

    *picture_name* (typically a photo's filename stem) is placed as element
    text content via ElementTree, which XML-escapes it automatically --
    unlike raw f-string interpolation, this is safe for names containing
    `&`, `<`, `>`, etc.
    """
    root = ET.Element('UploadSiteHostedPicturesRequest', xmlns=_NS)
    ET.SubElement(root, 'PictureName').text = picture_name
    ET.SubElement(root, 'PictureSet').text = 'Supersize'
    body = ET.tostring(root, encoding='unicode')
    return f'<?xml version="1.0" encoding="utf-8"?>{body}'


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

    xml_payload = _build_upload_payload(photo_path.stem)

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

    quota.precheck(cfg, 'ebay_eps')
    resp = requests.post(_TRADING_ENDPOINT, headers=headers, files=files, timeout=90)
    quota.record(cfg, 'ebay_eps')
    if resp.status_code == 429:
        quota.record_429(cfg, 'ebay_eps', photo_path.name)
    capture_response(cfg, 'eps', f'UploadSiteHostedPictures {photo_path.name}',
                     None, resp.status_code, resp.content)
    resp.raise_for_status()

    root = ET.fromstring(resp.text)
    ack = root.findtext(f'{{{_NS}}}Ack') or ''

    if ack not in ('Success', 'Warning'):
        msgs = root.findall(f'.//{{{_NS}}}ShortMessage')
        error_text = '; '.join(m.text or '' for m in msgs) or 'unknown error'
        # EPS reports quota exhaustion as Ack=Failure, not HTTP 429
        if 'usage limit' in error_text.lower():
            quota.record_429(cfg, 'ebay_eps', error_text)
        raise RuntimeError(f'UploadSiteHostedPictures failed ({ack}): {error_text}')

    url = root.findtext(f'{{{_NS}}}SiteHostedPictureDetails/{{{_NS}}}FullURL') or ''
    if not url:
        raise RuntimeError('UploadSiteHostedPictures: no FullURL in response')

    log.info('uploaded %s → %s', photo_path.name, url[:60])
    return url
