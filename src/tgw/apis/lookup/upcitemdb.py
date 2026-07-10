"""
tgw.apis.lookup.upcitemdb — UPC/EAN barcode lookup via upcitemdb.com.

Free tier: 100 req/day (no key required; key raises burst limit).
Key (optional): UPCITEMDB_API_KEY env var (tgw.apis.secrets.get_api_key,
sourced from secrets_root/tgw.env).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from tgw.apis.secrets import get_api_key

from .base import LookupResult, now_iso

log = logging.getLogger(__name__)

_ENDPOINT = 'https://api.upcitemdb.com/prod/trial/lookup'
_TIMEOUT  = 10


def lookup(barcode: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Look up a UPC or EAN barcode. Returns None on miss or error."""
    headers: Dict[str, str] = {'Accept': 'application/json'}
    try:
        headers['user_key'] = get_api_key('upcitemdb')
    except RuntimeError:
        pass

    try:
        resp = requests.get(_ENDPOINT, params={'upc': barcode},
                            headers=headers, timeout=_TIMEOUT)
        if resp.status_code == 429:
            log.warning('upcitemdb: daily rate limit reached')
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('upcitemdb: request failed for %s: %s', barcode, exc)
        return None

    items = data.get('items', [])
    if not items:
        log.debug('upcitemdb: no result for %s', barcode)
        return None

    hit = items[0]
    msrp: Optional[float] = None
    for offer in hit.get('offers', []):
        try:
            msrp = float(offer.get('price', 0)) or None
            break
        except (ValueError, TypeError):
            pass

    log.info('upcitemdb: hit for %s — %r', barcode, hit.get('title', '')[:60])
    return LookupResult(
        source      = 'upcitemdb',
        fetched_at  = now_iso(),
        title       = hit.get('title', ''),
        brand       = hit.get('brand', ''),
        description = hit.get('description', ''),
        ean         = hit.get('ean', ''),
        upc         = barcode,
        msrp        = msrp,
        category    = hit.get('category', ''),
        image_url   = (hit.get('images') or [''])[0],
        extra       = {'raw': hit},
    )
