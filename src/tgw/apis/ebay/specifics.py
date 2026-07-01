"""
tgw.apis.ebay.specifics — eBay item aspects (specifics) for a category.

Fetches the aspect definitions for a given categoryId and returns them in
a structured form ready to pass to an AI for value suggestion.

Aspects for a given category are stable for weeks at a time, but this used to
be called live on every item-detail page view with zero caching — a major
contributor to Taxonomy API quota exhaustion (that API is billed per-App-ID,
not per user token, and the default quota is only 5,000 calls/day). Results
are now cached to disk per category_id, refreshed after 14 days.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.apis.ebay.client import ebay_get
from tgw.apis.ebay.taxonomy import get_category_tree_id

log = logging.getLogger(__name__)

# Aspects we skip — not useful for AI to fill (operator/product-lookup handles these)
# California Prop 65 Warning was previously skipped as "legal boilerplate" but Dave
# flagged (session 39, item tgw202605060201087) that it's a real, near-universal
# aspect that must be shown/filled like any other — removed from the skip list.
_SKIP_ASPECTS = {'MPN', 'Model', 'Unit Quantity', 'Unit Type'}

_ASPECTS_CACHE_MAX_AGE = 14 * 86400  # 14 days
_aspects_mem_cache: Dict[str, List[Dict[str, Any]]] = {}


def _aspects_cache_path(cfg: Dict[str, Any]) -> Optional[Path]:
    root = cfg.get('catalog_root')
    return Path(root) / 'ebay-aspects-cache.json' if root else None


def _load_aspects_disk_cache(cache_path: Optional[Path]) -> Dict[str, Dict[str, Any]]:
    if not cache_path or not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        log.warning('aspects cache unreadable: %s', exc)
        return {}


def _fetch_aspects_live(cfg: Dict[str, Any], category_id: str) -> List[Dict[str, Any]]:
    tree_id = get_category_tree_id(cfg)
    data = ebay_get(
        cfg,
        f'/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category',
        params={'category_id': category_id},
    )
    results = []
    for aspect in data.get('aspects', []):
        name = aspect.get('localizedAspectName', '')
        if name in _SKIP_ASPECTS:
            continue
        constraint = aspect.get('aspectConstraint', {})
        allowed = [v.get('localizedValue', '')
                   for v in aspect.get('aspectValues', [])
                   if v.get('localizedValue')]
        results.append({
            'name':           name,
            'required':       constraint.get('aspectRequired', False),
            'mode':           constraint.get('aspectMode', 'FREE_TEXT'),
            'allowed_values': allowed,
        })
    return results


def get_aspects(cfg: Dict[str, Any], category_id: str) -> List[Dict[str, Any]]:
    """
    Return aspect definitions for a category, filtered and structured for AI use.

    Each entry: {name, required, mode, allowed_values (list, empty = free text)}
    Cached per category_id (disk + memory) — does not hit the live API for a
    category already fetched within the last 14 days.
    """
    category_id = str(category_id)
    if category_id in _aspects_mem_cache:
        return _aspects_mem_cache[category_id]

    cache_path = _aspects_cache_path(cfg)
    disk_cache = _load_aspects_disk_cache(cache_path)
    entry = disk_cache.get(category_id)
    if entry and time.time() - entry.get('_cached_at', 0) < _ASPECTS_CACHE_MAX_AGE:
        _aspects_mem_cache[category_id] = entry['aspects']
        return entry['aspects']

    results = _fetch_aspects_live(cfg, category_id)
    _aspects_mem_cache[category_id] = results
    if cache_path:
        disk_cache[category_id] = {'_cached_at': time.time(), 'aspects': results}
        try:
            cache_path.write_text(json.dumps(disk_cache), encoding='utf-8')
        except OSError as exc:
            log.warning('could not write aspects cache: %s', exc)
    return results


def warm_missing_aspects(cfg: Dict[str, Any], category_ids: List[str],
                         max_new: int = 25) -> int:
    """Opportunistically fill the aspects cache for categories not yet cached
    (or stale) among *category_ids* — meant to be called from an existing
    scheduled worker (e.g. ebay_sync's periodic run) with the categories it
    just touched, so real-use coverage grows toward complete over time using
    whatever Taxonomy API quota is left for the day.

    Self-throttling by design: stops at the first failure (e.g. quota
    exhausted) rather than retrying, and caps how many NEW live calls it will
    make in one pass. Categories already cache-fresh cost nothing extra.

    Returns the number of categories successfully warmed this call.
    """
    cache_path = _aspects_cache_path(cfg)
    disk_cache = _load_aspects_disk_cache(cache_path)
    seen: set = set()
    attempted = 0
    warmed = 0
    for raw_cid in category_ids:
        if attempted >= max_new:
            break
        cid = str(raw_cid or '').strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        if cid in _aspects_mem_cache:
            continue
        entry = disk_cache.get(cid)
        if entry and time.time() - entry.get('_cached_at', 0) < _ASPECTS_CACHE_MAX_AGE:
            continue
        attempted += 1
        try:
            get_aspects(cfg, cid)
            warmed += 1
        except Exception as exc:
            log.info('aspects cache warm-up stopped at category %s (%d warmed this pass): %s',
                     cid, warmed, exc)
            break
    return warmed
