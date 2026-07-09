"""
tgw.apis.lookup.go_upc — UPC/EAN fallback via go-upc.com.

Silently skipped if GO_UPC_API_KEY isn't set (tgw.apis.secrets.get_api_key,
sourced from secrets_root/tgw.env). Value is the full header string,
e.g. "Bearer <token>".
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from tgw.apis.secrets import get_api_key

from .base import LookupResult, now_iso

log = logging.getLogger(__name__)

_ENDPOINT = 'https://go-upc.com/api/v1/code/{barcode}'
_TIMEOUT  = 10


def lookup(barcode: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Look up a barcode via Go-UPC. Returns None if no key or on miss."""
    try:
        api_key = get_api_key('go_upc')
    except RuntimeError:
        return None

    try:
        resp = requests.get(
            _ENDPOINT.format(barcode=barcode),
            headers={'Authorization': api_key, 'Accept': 'application/json'},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (404, 400):
            log.debug('go-upc: no result for %s', barcode)
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('go-upc: request failed for %s: %s', barcode, exc)
        return None

    product = data.get('product', {})
    if not product:
        return None

    log.info('go-upc: hit for %s — %r', barcode, product.get('name', '')[:60])
    return LookupResult(
        source      = 'go-upc',
        fetched_at  = now_iso(),
        title       = product.get('name', ''),
        brand       = product.get('brand', ''),
        description = product.get('description', ''),
        upc         = barcode,
        category    = product.get('category', ''),
        image_url   = product.get('imageUrl', ''),
        extra       = {'raw': product},
    )
