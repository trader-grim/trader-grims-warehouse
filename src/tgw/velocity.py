"""
tgw.velocity — Sold velocity analytics and category sell-through stats (PP-PRICE-004).

Scans ItemData for sold items; groups by eBay category; computes:
  sold_count, active_count, stale_count,
  median_days_to_sale, median_sale_price, p25_sale_price,
  sell_at_launch_pct, sell_at_retail_pct, sell_at_move_pct,
  never_sold_pct (active items past all reprice stages / all items)

Results stored in catalog_root/velocity-stats.json; refresh on demand
or via the velocity_stats nightly worker.

Stage determination requires reprice_schedule with done_at timestamps.
Legacy items (no reprice_schedule) contribute to price stats but stage
is recorded as 'unknown'.

sale_date is parsed from "Mon-DD-YY" (eBay CSV) or ISO-8601.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import tgw.logging as tgw_logging
from tgw.items import atomic_write_json

log = logging.getLogger(__name__)

VELOCITY_STATS_FILE = 'velocity-stats.json'


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

def _parse_date(raw: str) -> Optional[datetime]:
    """Parse sale/publish dates: ISO-8601 variants or 'Mon-DD-YY' (eBay CSV)."""
    if not raw:
        return None
    for fmt in (
        '%Y-%m-%dT%H:%M:%S%z',
        '%Y-%m-%dT%H:%M:%S.%f%z',
        '%Y-%m-%dT%H:%M:%S',
        '%Y-%m-%d',
        '%b-%d-%y',
    ):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _category(item: Dict[str, Any]) -> Tuple[str, str]:
    """Return (category_id, category_name), preferring new-pipeline fields."""
    dl = item.get('draft_listing') or {}
    if dl.get('category_id'):
        return str(dl['category_id']), str(dl.get('category_name') or '')

    if item.get('ebay_category_id'):
        return str(item['ebay_category_id']), str(item.get('ebay_category_name') or '')

    cat_id   = str(item.get('eBay category 1 number') or '').strip()
    cat_name = str(item.get('eBay category 1 name') or '').strip()
    return cat_id, cat_name


def _sold_stage_label(item: Dict[str, Any], sale_dt: datetime) -> str:
    """
    Return reprice stage label at time of sale ('launch', 'retail', 'move') or
    'unknown' if reprice_schedule is absent.  The active stage is the one with
    the highest stage number whose done_at <= sale_dt.
    """
    schedule: List[Dict[str, Any]] = item.get('reprice_schedule', [])
    if not schedule:
        return 'unknown'

    active = []
    for s in schedule:
        raw = s.get('done_at')
        if not raw:
            continue
        done_dt = _parse_date(str(raw))
        if done_dt and done_dt <= sale_dt:
            active.append(s)

    if not active:
        return 'unknown'
    return str(max(active, key=lambda s: s.get('stage', 0)).get('label', 'unknown'))


def _days_to_sale(item: Dict[str, Any], sale_dt: datetime) -> Optional[float]:
    """
    Days between listing (launch) and sale.  Uses launch stage done_at first,
    then ebay_listing.published_at, then ebay_offer.published_at.
    """
    raw_pub: Optional[str] = None

    for s in item.get('reprice_schedule', []):
        if s.get('label') == 'launch' and s.get('done_at'):
            raw_pub = s['done_at']
            break

    if not raw_pub:
        raw_pub = (item.get('ebay_listing') or {}).get('published_at')
    if not raw_pub:
        raw_pub = (item.get('ebay_offer') or {}).get('published_at')

    if not raw_pub:
        return None

    pub_dt = _parse_date(str(raw_pub))
    if pub_dt is None:
        return None
    return max(0.0, (sale_dt - pub_dt).total_seconds() / 86400.0)


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return round(s[n // 2], 2)
    return round((s[n // 2 - 1] + s[n // 2]) / 2.0, 2)


def _percentile(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, int(len(s) * pct / 100) - 1)
    return round(s[idx], 2)


def _pct(n: int, total: int) -> float:
    return round(n / total, 3) if total else 0.0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_velocity(itemdata_root: Path) -> Dict[str, Any]:
    """
    Scan all ItemData and return velocity stats keyed by category_id.

    Returns:
        {
          'generated_at': ISO-8601 string,
          'item_count':   int,
          'categories': {
            'CAT_ID': {
              'category_name':       str,
              'sold_count':          int,
              'active_count':        int,
              'stale_count':         int,
              'never_sold_pct':      float,
              'median_days_to_sale': float | None,
              'sell_at_launch_pct':  float,
              'sell_at_retail_pct':  float,
              'sell_at_move_pct':    float,
              'sell_at_unknown_pct': float,
              'median_sale_price':   float | None,
              'p25_sale_price':      float | None,
            }
          }
        }
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    item_count = 0

    def _bucket(cat_id: str, cat_name: str) -> Dict[str, Any]:
        if cat_id not in buckets:
            buckets[cat_id] = {
                'name': cat_name, 'sold_prices': [], 'days_list': [],
                'stages': [], 'active': 0, 'stale': 0,
            }
        elif cat_name and not buckets[cat_id]['name']:
            buckets[cat_id]['name'] = cat_name
        return buckets[cat_id]

    for child in sorted(itemdata_root.iterdir()):
        jf = child / f'{child.name}.json'
        if not jf.exists():
            continue
        try:
            item = json.loads(jf.read_text(encoding='utf-8'))
        except Exception:
            continue
        item_count += 1

        cat_id, cat_name = _category(item)
        if not cat_id:
            continue

        status = str(item.get('status', '')).lower().strip()

        if status == 'sold':
            sale = item.get('ebay_sale') or {}
            sale_raw = str(sale.get('sale_date') or '')
            sale_price = sale.get('sale_price')
            sale_dt = _parse_date(sale_raw) if sale_raw else None
            if sale_dt is None:
                continue

            b = _bucket(cat_id, cat_name)
            if isinstance(sale_price, (int, float)) and sale_price > 0:
                b['sold_prices'].append(float(sale_price))

            b['stages'].append(_sold_stage_label(item, sale_dt))

            days = _days_to_sale(item, sale_dt)
            if days is not None:
                b['days_list'].append(days)

        elif status in ('in stock', 'available', 'active', ''):
            b = _bucket(cat_id, cat_name)
            b['active'] += 1
            schedule = item.get('reprice_schedule', [])
            if schedule and all(s.get('done_at') is not None for s in schedule):
                b['stale'] += 1

    categories: Dict[str, Any] = {}
    for cat_id, b in sorted(buckets.items()):
        sold_count = len(b['stages'])
        active     = b['active']
        stale      = b['stale']
        total      = sold_count + active

        stages = b['stages']
        categories[cat_id] = {
            'category_name':       b['name'],
            'sold_count':          sold_count,
            'active_count':        active,
            'stale_count':         stale,
            'never_sold_pct':      _pct(stale, total),
            'median_days_to_sale': _median(b['days_list']),
            'sell_at_launch_pct':  _pct(stages.count('launch'), sold_count),
            'sell_at_retail_pct':  _pct(stages.count('retail'), sold_count),
            'sell_at_move_pct':    _pct(stages.count('move'), sold_count),
            'sell_at_unknown_pct': _pct(stages.count('unknown'), sold_count),
            'median_sale_price':   _median(b['sold_prices']),
            'p25_sale_price':      _percentile(b['sold_prices'], 25),
        }

    return {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'item_count':   item_count,
        'categories':   categories,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_velocity_stats(catalog_root: Path) -> Optional[Dict[str, Any]]:
    p = catalog_root / VELOCITY_STATS_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception as exc:
        log.warning('velocity: failed to load %s: %s', p, exc)
        return None


def save_velocity_stats(catalog_root: Path, stats: Dict[str, Any],
                        pretty: bool = True) -> None:
    p = catalog_root / VELOCITY_STATS_FILE
    atomic_write_json(p, stats, pretty=pretty)
    log.info('velocity: saved %d categories to %s',
             len(stats.get('categories', {})), p)
    tgw_logging.log_event('velocity_stats_saved',
                          categories=len(stats.get('categories', {})),
                          item_count=stats.get('item_count', 0))
