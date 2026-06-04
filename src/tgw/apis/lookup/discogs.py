"""
tgw.apis.lookup.discogs — Music release lookup via Discogs barcode search.

Silently skipped if secrets_root/discogs-credentials.json is absent.
Key: {"personal_access_token": "..."} or {"user_token": "..."}
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import requests

from .base import LookupResult, now_iso
from .base import secrets_root as _secrets_root

log = logging.getLogger(__name__)

_ENDPOINT = 'https://api.discogs.com/database/search'
_TIMEOUT  = 10
_UA       = 'TGW-inventory/1.0 (trader-grims-warehouse)'


def lookup(barcode: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Search Discogs by barcode. Returns None if no key or on miss."""
    key_file = _secrets_root(cfg) / 'discogs-credentials.json'
    if not key_file.exists():
        return None
    try:
        creds = json.loads(key_file.read_text())
        token = creds.get('personal_access_token') or creds.get('user_token', '')
    except Exception:
        return None
    if not token:
        return None

    try:
        resp = requests.get(
            _ENDPOINT,
            params={'barcode': barcode, 'per_page': 1},
            headers={'Authorization': f'Discogs token={token}',
                     'User-Agent': _UA},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('discogs: request failed for %s: %s', barcode, exc)
        return None

    results = data.get('results', [])
    if not results:
        log.debug('discogs: no result for %s', barcode)
        return None

    hit    = results[0]
    title  = hit.get('title', '')
    year   = str(hit.get('year', ''))
    label  = ', '.join(hit.get('label', [])[:2])
    genre  = ', '.join(hit.get('genre', [])[:3])
    format_str = ', '.join(hit.get('format', [])[:2])

    description_parts = [p for p in (label, year, format_str) if p]

    log.info('discogs: hit for %s — %r', barcode, title[:60])
    return LookupResult(
        source      = 'discogs',
        fetched_at  = now_iso(),
        title       = title,
        brand       = label,
        description = '. '.join(description_parts),
        upc         = barcode,
        category    = genre,
        image_url   = hit.get('cover_image', ''),
        extra       = {'raw': hit},
    )
