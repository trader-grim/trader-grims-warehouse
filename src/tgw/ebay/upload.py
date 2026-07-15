"""
tgw.ebay.upload — Upload item photos to eBay via UploadSiteHostedPictures.

Uses the eBay Trading API multipart POST to upload a local photo and return
the eBay-hosted EPS URL.  Callers should use the ebay_upload queue worker
rather than calling this directly.
"""

from __future__ import annotations

import io
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

# eBay's UploadSiteHostedPictures rejects any image with either dimension
# over this many pixels ("File dimension limit exceeds 15000 pixels." —
# live dead-letter text, todo #1398/PP-DEADLETTER-001, 10 SKUs). This is
# eBay's own enforced limit taken verbatim from the live error, not a
# separately published constant found elsewhere in eBay's docs.
_MAX_DIMENSION_PX = 15000

_PIL_FORMAT_BY_SUFFIX = {
    '.jpg':  'JPEG',
    '.jpeg': 'JPEG',
    '.png':  'PNG',
    '.gif':  'GIF',
    '.tif':  'TIFF',
    '.tiff': 'TIFF',
}


class PhotoResizeError(RuntimeError):
    """Raised when a photo needed a pre-upload resize but couldn't be
    processed (e.g. corrupt/unreadable image) — distinct from a plain
    upload failure so it doesn't get masked as an eBay-side rejection
    (packet #1398 spec item 3)."""


def _prepare_upload_bytes(photo_path: Path) -> bytes:
    """
    Return the bytes to POST for *photo_path*.

    Normal case (within eBay's dimension limit): reads the file's raw
    bytes unchanged — no re-encoding, no quality loss, byte-identical to
    the stored file.

    Oversized case (either dimension > _MAX_DIMENSION_PX): downscales a
    temporary in-memory copy to fit within the limit, preserving aspect
    ratio. The original file on disk is never opened for writing and is
    never touched (Prime Directive 1 — raw is permanent, derived is
    recomputable; this resized copy is upload-time-only, never persisted
    back to ItemData).
    """
    raw = photo_path.read_bytes()

    try:
        from PIL import Image
    except ImportError:
        # No Pillow available — fall back to raw bytes; eBay will reject an
        # oversized image with the same error as before this fix, no worse
        # than pre-#1398 behavior.
        log.warning('Pillow not installed — skipping dimension pre-flight for %s',
                    photo_path.name)
        return raw

    width = height = None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
            if width <= _MAX_DIMENSION_PX and height <= _MAX_DIMENSION_PX:
                return raw

            scale = _MAX_DIMENSION_PX / float(max(width, height))
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img_format = img.format or _PIL_FORMAT_BY_SUFFIX.get(
                photo_path.suffix.lower(), 'JPEG')
            resized = img.resize(new_size, Image.LANCZOS)
            buf = io.BytesIO()
            save_kwargs: Dict[str, Any] = {}
            if img_format == 'JPEG':
                if resized.mode not in ('RGB', 'L'):
                    resized = resized.convert('RGB')
                save_kwargs['quality'] = 90
            resized.save(buf, format=img_format, **save_kwargs)
            resized_bytes = buf.getvalue()
    except Exception as exc:
        # Distinct exception type so the caller (and dead-letter triage) can
        # tell "resize itself failed / corrupt image" apart from a plain
        # upload failure (packet #1398 spec item 3 — may overlap with
        # PP-DATAINTEGRITY-001's corrupt-photo detection; noted separately,
        # not silently merged into this fix).
        dims = f'{width}x{height}' if width is not None else 'unknown'
        raise PhotoResizeError(
            f'failed to resize oversized photo {photo_path.name} ({dims}): {exc}') from exc

    log.info('resized oversized photo %s: %dx%d -> %dx%d (exceeds %dpx limit)',
             photo_path.name, width, height, new_size[0], new_size[1],
             _MAX_DIMENSION_PX)
    from tgw import logging as tgw_logging
    tgw_logging.log_event('ebay_upload_photo_resized', photo=photo_path.name,
                          original_dimensions=f'{width}x{height}',
                          resized_dimensions=f'{new_size[0]}x{new_size[1]}',
                          max_dimension_px=_MAX_DIMENSION_PX)
    return resized_bytes


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

    # Pre-flight dimension check/downscale (todo #1398/PP-DEADLETTER-001):
    # eBay's UploadSiteHostedPictures rejects images over 15000px on a side.
    # Operates on an in-memory copy only — never touches the stored original.
    image_bytes = _prepare_upload_bytes(photo_path)

    # requests builds multipart/form-data automatically from the files dict
    files = {
        'XML Payload': ('', xml_payload.encode('utf-8'), 'text/xml;charset=utf-8'),
        'image':       (photo_path.name, image_bytes, mime),
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
