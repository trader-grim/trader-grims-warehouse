"""
tgw.apis.lookup.dispatcher — route item JSON to the right product lookup source.

Public API:
    lookup_product(item, cfg) → Optional[LookupResult]

Routing:
  isbn                       → Open Library
  upc/ean + music hint       → Discogs (if key present) → upcitemdb → Go-UPC
  upc/ean + food hint        → Open Food Facts → upcitemdb → Go-UPC
  upc/ean (default)          → upcitemdb → Go-UPC
  game category + title hint → IGDB (if key present)
  tcg category + title hint  → JustTCG

Cache: result stored in item['product_lookup']; re-fetched only if absent or > 30 days old.
Call site is responsible for writing the result back to item JSON.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import discogs, go_upc, igdb, justtcg, open_food_facts, open_library, upcitemdb
from .base import LookupResult, barcode_from_item

log = logging.getLogger(__name__)

_CACHE_TTL_DAYS = 30

_MUSIC_KEYWORDS = {'music', 'vinyl', 'record', 'cd', 'cassette', 'tape',
                   'album', 'lp', '45 rpm', '33 rpm', 'discogs'}

_FOOD_KEYWORDS  = {'food', 'beverage', 'grocery', 'snack', 'drink', 'cereal',
                   'sauce', 'condiment', 'household', 'health & beauty',
                   'personal care', 'nutrition', 'supplement', 'vitamin'}

_GAME_KEYWORDS  = {'video game', 'videogame', 'game cartridge', 'game disc',
                   'game software', 'nintendo', 'playstation', 'xbox', 'sega',
                   'atari', 'gameboy', 'game boy', 'ps1', 'ps2', 'ps3', 'ps4',
                   'ps5', 'nes', 'snes', 'n64', 'genesis', 'gaming'}

_TCG_KEYWORDS   = {'trading card', 'collectible card', 'ccg', 'tcg',
                   'magic the gathering', 'mtg', 'pokemon card', 'yu-gi-oh',
                   'yugioh', 'card game', 'booster pack'}

_SKU_PREFIX = 'tgw'


def _category_hint(item: Dict[str, Any]) -> str:
    return (str(item.get('category', ''))
            + str(item.get('ai_hint', ''))
            + str(item.get('title', ''))).lower()


def _search_title(item: Dict[str, Any]) -> str:
    """Return a useful title/hint for name-based lookup, or '' if only a SKU."""
    for key in ('ai_hint', 'title'):
        val = str(item.get(key, '')).strip()
        if val and not val.startswith(_SKU_PREFIX):
            return val
    return ''


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
    hint           = _category_hint(item)
    title          = _search_title(item)

    is_music = any(kw in hint for kw in _MUSIC_KEYWORDS)
    is_food  = any(kw in hint for kw in _FOOD_KEYWORDS)
    is_game  = any(kw in hint for kw in _GAME_KEYWORDS)
    is_tcg   = any(kw in hint for kw in _TCG_KEYWORDS)

    result: Optional[LookupResult] = None

    if btype == 'isbn':
        result = open_library.lookup(barcode, cfg)

    elif btype in ('upc', 'ean'):
        if is_music:
            result = discogs.lookup(barcode, cfg)
        if result is None and is_food:
            result = open_food_facts.lookup(barcode, cfg)
        if result is None:
            result = upcitemdb.lookup(barcode, cfg)
        if result is None:
            result = go_upc.lookup(barcode, cfg)

    # Name-based fallbacks when barcode lookup found nothing (or no barcode)
    if result is None and is_game and title:
        result = igdb.lookup(title, cfg)
    if result is None and is_tcg and title:
        result = justtcg.lookup(title, cfg)

    if result:
        log.info('lookup_product: %s resolved via %s — %r',
                 barcode or title, result.source, result.title[:60])
    else:
        log.debug('lookup_product: no result (barcode=%r type=%s title=%r)',
                  barcode, btype, title)

    return result
