"""
tgw.apis.lookup.dispatcher — route item JSON to the right product lookup source.

Public API:
    lookup_product(item, cfg) → Optional[LookupResult]

Routing:
  isbn  → Open Library
  upc/ean → upcitemdb → Go-UPC (if key present)
  any barcode + music category → Discogs (if key present)

Cache: result stored in item['product_lookup']; re-fetched only if absent or > 30 days old.
Call site is responsible for writing the result back to item JSON.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import discogs, go_upc, open_library, upcitemdb
from .base import LookupResult, barcode_from_item

log = logging.getLogger(__name__)

_CACHE_TTL_DAYS = 30

_MUSIC_KEYWORDS = {'music', 'vinyl', 'record', 'cd', 'cassette', 'tape',
                   'album', 'lp', '45 rpm', '33 rpm', 'discogs'}


def lookup_product(item: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """
    Look up product data for an item.  Returns a LookupResult or None.

    Checks the cached result first; fetches fresh data if absent or stale.
    Does NOT write back to item JSON — caller handles persistence.
    """
    # Return cached result if still fresh
    cached = item.get('product_lookup')
    if cached and isinstance(cached, dict):
        try:
            age = (datetime.now(timezone.utc)
                   - datetime.fromisoformat(cached['fetched_at'])).days
            if age < _CACHE_TTL_DAYS:
                fields = LookupResult.__dataclass_fields__
                return LookupResult(**{k: cached[k] for k in fields if k in cached})
        except Exception:
            pass

    barcode, btype = barcode_from_item(item)
    if not barcode:
        return None

    result: Optional[LookupResult] = None

    if btype == 'isbn':
        result = open_library.lookup(barcode, cfg)

    elif btype in ('upc', 'ean'):
        # Check if this looks like a music item — try Discogs in parallel path
        category_hint = (str(item.get('category', ''))
                         + str(item.get('ai_hint', ''))
                         + str(item.get('title', ''))).lower()
        is_music = any(kw in category_hint for kw in _MUSIC_KEYWORDS)

        if is_music:
            result = discogs.lookup(barcode, cfg)

        if result is None:
            result = upcitemdb.lookup(barcode, cfg)

        if result is None:
            result = go_upc.lookup(barcode, cfg)

    if result:
        log.info('lookup_product: %s resolved via %s — %r',
                 barcode, result.source, result.title[:60])
    else:
        log.debug('lookup_product: no result for barcode %s (type=%s)', barcode, btype)

    return result
