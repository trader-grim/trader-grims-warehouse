"""
tgw.apis.lookup.open_food_facts — food/household product lookup via Open Food Facts.

No auth required.  Triggered when category hints food, beverage, or household.
Endpoint: https://world.openfoodfacts.org/api/v2/product/<barcode>.json
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .base import LookupResult, now_iso

log = logging.getLogger(__name__)

_ENDPOINT = 'https://world.openfoodfacts.org/api/v2/product/{barcode}.json'
_TIMEOUT  = 10
_UA       = 'TGW-inventory/1.0 (trader-grims-warehouse)'


def lookup(barcode: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Look up a barcode on Open Food Facts. Returns None on miss or error."""
    try:
        resp = requests.get(
            _ENDPOINT.format(barcode=barcode),
            headers={'User-Agent': _UA},
            timeout=_TIMEOUT,
        )
        if resp.status_code == 404:
            log.debug('open-food-facts: no result for %s', barcode)
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('open-food-facts: request failed for %s: %s', barcode, exc)
        return None

    if data.get('status') != 1:
        log.debug('open-food-facts: no product for %s', barcode)
        return None

    product = data.get('product', {})
    if not product:
        return None

    name = (product.get('product_name_en')
            or product.get('product_name')
            or product.get('product_name_fr', ''))
    brand = (product.get('brands') or '').split(',')[0].strip()
    categories_raw = product.get('categories_en') or product.get('categories', '')
    category = categories_raw.split(',')[0].strip() if categories_raw else ''

    log.info('open-food-facts: hit for %s — %r', barcode, name[:60])
    return LookupResult(
        source      = 'open-food-facts',
        fetched_at  = now_iso(),
        title       = name,
        brand       = brand,
        description = product.get('generic_name_en') or product.get('generic_name', ''),
        upc         = barcode,
        category    = category,
        image_url   = product.get('image_url', ''),
        extra       = {'raw': product},
    )
