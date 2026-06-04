"""
tgw.ebay.pricing — Market price suggestion using eBay Browse API active listings.

Note: eBay Finding API (sold prices) is unavailable at this app tier.
We use active listing prices via Browse API; p25 of asking prices gives a
competitive price point that moves inventory.

suggest_price(cfg, title, category_name) → {price, source, comps, queried_at}

Three-stage fallback if comps are thin:
  1. Full title search
  2. Category name + first 3 title words
  3. Category name only
Returns None price if fewer than MIN_COMPS results across all stages.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tgw.apis.ebay.client import ebay_get

log = logging.getLogger(__name__)

MIN_COMPS = 3   # minimum sold comps to set a price; below this we leave price null


def to_99(price: float) -> float:
    """Round price up to the nearest .99 price point (e.g. 15.23 → 15.99, 16.00 → 16.99)."""
    p = round(price, 2)
    base = math.floor(p)
    candidate = round(base + 0.99, 2)
    return candidate if candidate >= p else round(base + 1.99, 2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_comps(cfg: Dict[str, Any], keywords: str,
                 limit: int = 20) -> List[float]:
    """Search Browse API and return active listing prices for *keywords*."""
    try:
        data = ebay_get(cfg, '/buy/browse/v1/item_summary/search',
                        params={'q': keywords, 'limit': limit, 'sort': 'PRICE'})
        items = data.get('itemSummaries', [])
        prices = []
        for item in items:
            p = item.get('price', {})
            try:
                prices.append(float(p['value']))
            except (KeyError, ValueError, TypeError):
                continue
        return prices
    except Exception as exc:
        log.warning('Browse API comp search failed for %r: %s', keywords, exc)
        return []


def _compute_stats(prices: List[float]) -> Dict[str, Any]:
    if not prices:
        return {}
    s = sorted(prices)
    n = len(s)
    return {
        'count':  n,
        'min':    round(s[0], 2),
        'p25':    round(s[max(0, int(n * 0.25))], 2),
        'median': round(s[n // 2], 2),
        'p75':    round(s[min(n - 1, int(n * 0.75))], 2),
        'max':    round(s[-1], 2),
    }


def _short_keywords(title: str, words: int = 3) -> str:
    """Return the first *words* words of a title, stripped of common filler."""
    _STOP = {'a', 'an', 'the', 'of', 'for', 'with', 'and', 'in', 'on', 'by',
             'used', 'vintage', 'lot', 'set', 'pack'}
    tokens = [w for w in title.split() if w.lower() not in _STOP]
    return ' '.join(tokens[:words]) if tokens else title


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_price(cfg: Dict[str, Any], title: str,
                  category_name: str = '',
                  category_id: str = '') -> Dict[str, Any]:
    """
    Suggest a price for *title* based on Browse API active listing comps.

    Falls back to cfg['category_price_defaults'][category_id] when Browse API
    returns insufficient comps.

    Returns a dict with keys:
        price         float or None (None = insufficient data)
        source        description of how the price was derived
        comps         {count, min, p25, median, p75, max}
        queried_at    ISO-8601 timestamp
    """
    queried_at = datetime.now(timezone.utc).isoformat()
    all_prices: List[float] = []
    source = ''

    # Stage 1 — full title
    prices = _fetch_comps(cfg, title)
    if len(prices) >= MIN_COMPS:
        all_prices = prices
        source = 'browse:full_title'
    else:
        # Stage 2 — category + first 3 title words
        short = _short_keywords(title, 3)
        query2 = f'{category_name} {short}'.strip() if category_name else short
        prices = _fetch_comps(cfg, query2)
        if len(prices) >= MIN_COMPS:
            all_prices = prices
            source = 'browse:category+short'
        elif category_name:
            # Stage 3 — category name only
            prices = _fetch_comps(cfg, category_name)
            if len(prices) >= MIN_COMPS:
                all_prices = prices
                source = 'browse:category_only'

    stats = _compute_stats(all_prices)

    if not stats:
        # Stage 4 — category price default from config
        defaults: Dict[str, float] = cfg.get('category_price_defaults', {})
        default_price = defaults.get(str(category_id))
        if default_price is not None:
            log.info('pricing: %r — using category default $%.2f for category %s',
                     title[:60], default_price, category_id)
            return {
                'price':      float(default_price),
                'source':     f'category_default:{category_id}',
                'comps':      {},
                'queried_at': queried_at,
            }
        log.info('pricing: insufficient comps for %r', title[:60])
        return {'price': None, 'source': 'insufficient_data',
                'comps': {}, 'queried_at': queried_at}

    price = stats['p25']
    log.info('pricing: %r → $%.2f (p25 of %d comps, %s)',
             title[:60], price, stats['count'], source)

    return {
        'price':      price,
        'source':     source,
        'comps':      stats,
        'queried_at': queried_at,
    }
