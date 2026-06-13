"""
tgw.ebay.pricing — Market price suggestion using eBay Browse API active listings.

Note: eBay Finding API (sold prices) is unavailable at this app tier.
We use active listing prices via Browse API; p25 of asking prices gives a
competitive price point that moves inventory.

suggest_price(cfg, title, category_name, category_id, item_condition,
              product_lookup) → {price, source, comps, price_confidence, queried_at}

Fallback chain when comps are thin:
  0. product_lookup brand+MPN query (if PP-LOOKUP-001 data present)
  1. Full title search
  2. Category name + first 3 title words
  3. Category name only
  4. Category group typical × condition_factor  (from category-groups.json)
  5. Category default from config
Returns None price if all stages exhausted.

Floor enforcement: global_floor and per-group floor from category-groups.json are
applied to ALL prices regardless of source (Browse API, group assumption, or config).

Condition filtering: Browse API summaries are filtered to same-or-worse condition
before computing percentiles. Avoids inflated p25 from New listings when pricing
a Used item. Falls back to unfiltered if filter leaves < MIN_COMPS results.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tgw.apis.ebay.client import ebay_get

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Category-groups loader
# ---------------------------------------------------------------------------

_groups_cache: Optional[Dict[str, Any]] = None
_groups_reverse: Optional[Dict[str, str]] = None  # category_id → group_key


def _load_groups(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Load category-groups.json once per process, cache in module globals."""
    global _groups_cache, _groups_reverse
    if _groups_cache is not None:
        return _groups_cache

    path = cfg.get('category_groups_path')
    if path and Path(path).exists():
        try:
            _groups_cache = json.loads(Path(path).read_text(encoding='utf-8'))
        except Exception as exc:
            log.warning('category-groups.json load failed: %s', exc)
            _groups_cache = {}
    else:
        _groups_cache = {}

    # Build reverse index: category_id → group_key
    _groups_reverse = {}
    for key, grp in _groups_cache.get('groups', {}).items():
        for cat_id in grp.get('ebay_categories', []):
            _groups_reverse[str(cat_id)] = key

    return _groups_cache


def _group_for_category(cfg: Dict[str, Any], category_id: str) -> Optional[Dict[str, Any]]:
    """Return the group dict for a category_id, or None."""
    groups_data = _load_groups(cfg)
    if not groups_data:
        return None
    grp_key = (_groups_reverse or {}).get(str(category_id))
    if not grp_key:
        return None
    return groups_data['groups'].get(grp_key)


def _apply_floor(price: float, cfg: Dict[str, Any], category_id: str) -> Tuple[float, bool]:
    """Return (floored_price, was_floored). Applies per-group floor, then global floor."""
    groups_data = _load_groups(cfg)
    global_floor = float(groups_data.get('global_floor', 0.99))

    grp = _group_for_category(cfg, category_id)
    floor = float(grp['pricing']['floor']) if grp and grp.get('pricing', {}).get('floor') else global_floor
    floor = max(floor, global_floor)

    if price < floor:
        return floor, True
    return price, False

MIN_COMPS = 3   # minimum comps needed to set a price; below this price left null

# Condition rank: lower = better condition, higher = worse
# Our internal condition strings → rank
_ITEM_CONDITION_RANK: Dict[str, int] = {
    'new':        0,
    'like new':   1,
    'very good':  2,
    'good':       3,
    'acceptable': 4,
    'for parts':  5,
}

# Browse API conditionDisplayName (normalised to lowercase) → rank
_BROWSE_CONDITION_RANK: Dict[str, int] = {
    'new':                            0,
    'brand new':                      0,
    'open box':                       1,
    'certified refurbished':          1,
    'manufacturer refurbished':       1,
    'like new':                       1,
    'excellent - refurbished':        1,
    'very good - refurbished':        2,
    'seller refurbished':             2,
    'very good':                      2,
    'good':                           3,
    'acceptable':                     4,
    'for parts or not working':       5,
    'parts only':                     5,
}


def to_99(price: float) -> float:
    """Round price up to the nearest .99 price point (e.g. 15.23 → 15.99, 16.00 → 16.99)."""
    p = round(price, 2)
    base = math.floor(p)
    candidate = round(base + 0.99, 2)
    return candidate if candidate >= p else round(base + 1.99, 2)


def freeship_price(item_price: float, shipping_cost: float) -> float:
    """
    Compute a free-shipping listing price: item_price + shipping_cost rounded
    to the nearest .99 price point (PP-FREESHIP-001).

    e.g. 12.99 + 5.00 = 17.99, 12.99 + 5.51 = 18.99 (tipping at base+0.49)
    Never returns below 0.99.
    """
    combined = max(0.0, round(item_price + shipping_cost, 2))
    base = math.floor(combined)
    upper = round(base + 0.99, 2)
    lower = round(base - 0.01, 2)   # = (base - 1) + 0.99
    midpoint = round(base + 0.49, 2)
    if combined > midpoint:
        return upper
    return max(lower, 0.99)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_raw(cfg: Dict[str, Any], keywords: str,
               limit: int = 20) -> List[Dict[str, Any]]:
    """Search Browse API and return raw itemSummaries (price + condition info)."""
    try:
        data = ebay_get(cfg, '/buy/browse/v1/item_summary/search',
                        params={'q': keywords, 'limit': limit, 'sort': 'PRICE'})
        return data.get('itemSummaries', [])
    except Exception as exc:
        log.warning('Browse API comp search failed for %r: %s', keywords, exc)
        return []


def _prices_from(summaries: List[Dict[str, Any]]) -> List[float]:
    """Extract all prices from Browse API summaries."""
    prices = []
    for item in summaries:
        try:
            prices.append(float(item['price']['value']))
        except (KeyError, ValueError, TypeError):
            continue
    return prices


def _prices_condition_filtered(
    summaries: List[Dict[str, Any]],
    item_rank: int,
) -> List[float]:
    """
    Extract prices from summaries where condition is same-or-worse than item_rank.
    Avoids inflating our p25 with New listings when we're selling a Used item.
    """
    prices = []
    for item in summaries:
        cond_raw = (item.get('condition') or {})
        cond_name = str(cond_raw if isinstance(cond_raw, str)
                        else cond_raw.get('conditionDisplayName', '')).lower().strip()
        browse_rank = _BROWSE_CONDITION_RANK.get(cond_name)
        if browse_rank is None:
            # Unknown condition — include it (conservative: don't drop it)
            include = True
        else:
            include = browse_rank >= item_rank
        if include:
            try:
                prices.append(float(item['price']['value']))
            except (KeyError, ValueError, TypeError):
                continue
    return prices


def _best_prices(
    summaries: List[Dict[str, Any]],
    item_rank: Optional[int],
) -> Tuple[List[float], bool]:
    """
    Return (prices, condition_filtered).
    Tries condition-filtered first; falls back to unfiltered if < MIN_COMPS.
    condition_filtered=True means the result set was filtered by condition.
    """
    if item_rank is not None:
        filtered = _prices_condition_filtered(summaries, item_rank)
        if len(filtered) >= MIN_COMPS:
            return filtered, True

    return _prices_from(summaries), False


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


def _lookup_query(title: str, pl: Dict[str, Any]) -> Optional[str]:
    """
    Build a tighter Browse API search query from product_lookup data.
    Returns None if no useful structured data is available.
    Priority: brand+MPN > brand+short_product_title > brand+short_title
    """
    brand = str(pl.get('brand') or '').strip()
    mpn   = str(pl.get('mpn') or '').strip()
    pl_title = str(pl.get('title') or '').strip()

    _GENERIC = {'unbranded', 'does not apply', 'n/a', 'na', 'generic', ''}
    if brand.lower() in _GENERIC:
        brand = ''

    if not brand:
        return None

    if mpn and mpn.lower() not in _GENERIC and len(mpn) > 2:
        return f'{brand} {mpn}'

    if pl_title:
        short = _short_keywords(pl_title, 4)
        # Strip leading brand word from product title to avoid "Aiwa Aiwa RC-TN450EX"
        if short and short.lower().startswith(brand.lower()):
            short = short[len(brand):].strip()
        if short:
            return f'{brand} {short}'

    return f'{brand} {_short_keywords(title, 3)}'


def _price_confidence(stats: Dict[str, Any], source: str) -> str:
    """
    Classify comp quality as high / medium / low.
    high:   ≥5 comps, tight price range (max/min ratio < 3)
    medium: 3–4 comps, or ≥5 comps with wide range
    low:    <3 comps, category default, or no data
    """
    if not stats or source.startswith('category_default') or source == 'insufficient_data':
        return 'low'
    count   = stats.get('count', 0)
    min_p   = stats.get('min', 0)
    max_p   = stats.get('max', 0)
    tight   = (max_p / min_p < 3.0) if min_p > 0 else False
    if count >= 5 and tight:
        return 'high'
    if count >= MIN_COMPS:
        return 'medium'
    return 'low'


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_price(
    cfg: Dict[str, Any],
    title: str,
    category_name: str = '',
    category_id: str = '',
    item_condition: str = '',
    product_lookup: Optional[Dict[str, Any]] = None,
    velocity: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Suggest a price for *title* based on Browse API active listing comps.

    When product_lookup is provided, tries a structured brand+MPN query before
    falling back to title-based search. Condition filtering removes better-condition
    listings from the comp set when item_condition is known.

    Falls back to cfg['category_price_defaults'][category_id] when Browse API
    returns insufficient comps.

    velocity: optional per-category velocity stats dict (from velocity-stats.json
    categories[category_id]).  If the category has >50% sell-through at launch,
    a 'velocity_hint' of 'hold_launch' is added to the result to signal that the
    reprice schedule should hold the launch price longer before stepping down.

    Returns a dict with keys:
        price             float or None (None = insufficient data)
        source            description of how the price was derived
        comps             {count, min, p25, median, p75, max}
        price_confidence  'high' | 'medium' | 'low'
        velocity_hint     'hold_launch' | None
        queried_at        ISO-8601 timestamp
    """
    queried_at = datetime.now(timezone.utc).isoformat()
    pl         = product_lookup or {}

    # Velocity hint: if this category historically sells >50% at launch,
    # signal that the reprice schedule should hold the launch price longer.
    vel_hint: Optional[str] = None
    if velocity and velocity.get('sell_at_launch_pct', 0) > 0.50:
        vel_hint = 'hold_launch'

    # Resolve item condition rank for filtering
    item_rank: Optional[int] = _ITEM_CONDITION_RANK.get(
        item_condition.lower().strip()) if item_condition else None

    all_prices:  List[float] = []
    source = ''
    was_cond_filtered = False

    # Stage 0 — product_lookup structured query (brand+MPN or brand+product_title)
    lq = _lookup_query(title, pl)
    if lq:
        summaries = _fetch_raw(cfg, lq)
        prices, cfiltered = _best_prices(summaries, item_rank)
        if len(prices) >= MIN_COMPS:
            all_prices = prices
            was_cond_filtered = cfiltered
            source = 'browse:lookup_query'
            log.debug('pricing stage 0 (lookup): %r → %d comps', lq, len(prices))

    # Stage 1 — full title
    if not all_prices:
        summaries = _fetch_raw(cfg, title)
        prices, cfiltered = _best_prices(summaries, item_rank)
        if len(prices) >= MIN_COMPS:
            all_prices = prices
            was_cond_filtered = cfiltered
            source = 'browse:full_title'

    # Stage 2 — category + first 3 title words
    if not all_prices:
        short  = _short_keywords(title, 3)
        query2 = f'{category_name} {short}'.strip() if category_name else short
        summaries = _fetch_raw(cfg, query2)
        prices, cfiltered = _best_prices(summaries, item_rank)
        if len(prices) >= MIN_COMPS:
            all_prices = prices
            was_cond_filtered = cfiltered
            source = 'browse:category+short'

    # Stage 3 — category name only
    if not all_prices and category_name:
        summaries = _fetch_raw(cfg, category_name)
        prices, cfiltered = _best_prices(summaries, item_rank)
        if len(prices) >= MIN_COMPS:
            all_prices = prices
            was_cond_filtered = cfiltered
            source = 'browse:category_only'

    if was_cond_filtered:
        source += '+cond'

    stats = _compute_stats(all_prices)

    if not stats:
        # Stage 4 — category group typical × condition factor (category-groups.json)
        grp = _group_for_category(cfg, str(category_id))
        if grp:
            pricing = grp.get('pricing', {})
            # Pick typical_used vs typical_new based on condition
            is_new = item_condition.lower().strip() in ('new',)
            typical_raw = pricing.get('typical_new') if is_new else pricing.get('typical_used')
            if typical_raw is None:
                typical_raw = pricing.get('typical_used') or pricing.get('typical_new')
            if typical_raw is not None:
                typical = float(typical_raw)
                # Scale by condition factor
                cond_factors = _load_groups(cfg).get('condition_factors', {})
                factor = cond_factors.get(item_condition.lower().strip(), 1.0)
                assumed = round(typical * factor, 2)
                assumed, _ = _apply_floor(assumed, cfg, str(category_id))
                assumed = to_99(assumed)
                log.info('pricing: %r — group assumption $%.2f (typical=%.2f factor=%.2f group=%s)',
                         title[:60], assumed, typical, factor, grp['name'])
                return {
                    'price':            assumed,
                    'source':           f'group_assumption:{grp["name"]}',
                    'comps':            {},
                    'price_confidence': 'low',
                    'velocity_hint':    vel_hint,
                    'queried_at':       queried_at,
                }

        # Stage 5 — legacy per-category config default
        defaults: Dict[str, float] = cfg.get('category_price_defaults', {})
        default_price = defaults.get(str(category_id))
        if default_price is not None:
            assumed, _ = _apply_floor(float(default_price), cfg, str(category_id))
            log.info('pricing: %r — using category config default $%.2f for category %s',
                     title[:60], assumed, category_id)
            return {
                'price':            assumed,
                'source':           f'category_default:{category_id}',
                'comps':            {},
                'price_confidence': 'low',
                'velocity_hint':    vel_hint,
                'queried_at':       queried_at,
            }
        log.info('pricing: insufficient comps for %r', title[:60])
        return {
            'price':            None,
            'source':           'insufficient_data',
            'comps':            {},
            'price_confidence': 'low',
            'velocity_hint':    vel_hint,
            'queried_at':       queried_at,
        }

    price = stats['p25']
    confidence = _price_confidence(stats, source)

    # Hard floor — apply to Browse API results too
    price, floored = _apply_floor(price, cfg, str(category_id))
    if floored:
        log.info('pricing: %r → floor applied, final $%.2f', title[:60], price)

    log.info('pricing: %r → $%.2f (p25 of %d comps, %s, confidence=%s)',
             title[:60], price, stats['count'], source, confidence)

    return {
        'price':            price,
        'source':           source,
        'comps':            stats,
        'price_confidence': confidence,
        'velocity_hint':    vel_hint,
        'queried_at':       queried_at,
    }
