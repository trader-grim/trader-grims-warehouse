"""
tgw.apis.ebay.specifics — eBay item aspects (specifics) for a category.

Fetches the aspect definitions for a given categoryId and returns them in
a structured form ready to pass to an AI for value suggestion.

Aspects for a given category are stable for weeks at a time, but this used to
be called live on every item-detail page view with zero caching — a major
contributor to Taxonomy API quota exhaustion (that API is billed per-App-ID,
not per user token, and the default quota is only 5,000 calls/day).

Caching (session 42 / R0.4): like the category tree, cached aspects are
PERMANENT until explicitly refreshed — no auto-expiry (Dave's stated policy:
eBay announces taxonomy changes; don't burn quota re-fetching stable data).
Two cache layers, checked in order:

  1. `ebay-aspects-cache.json` — per-category entries written by live
     get_item_aspects_for_category calls (commerce.taxonomy pool, 5,000/day).
  2. `ebay-aspects-bulk/<category_id>.json` — one shard per leaf category,
     written by `bulk_refresh_aspects()` from the fetch_item_aspects bulk
     download (commerce.taxonomy.BULK pool — a separate 100/day quota; ONE
     call covers all ~15,000 leaf categories). The raw gzip is kept beside
     the shards as the permanent source asset. Run via
     `tgw warm-ebay-aspects`.

With the bulk shards populated, interactive/UI use needs zero live Taxonomy
calls; the live path remains only as a last resort for categories missing
from both layers (e.g. between a taxonomy change and the next bulk refresh).
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.apis.ebay._cache_io import locked_merge_cache_json
from tgw.apis.ebay.client import ebay_get, ebay_get_bytes
from tgw.apis.ebay.taxonomy import get_category_tree_id
from tgw.catalog import atomic_write_json as _atomic_write_cache_json

log = logging.getLogger(__name__)

# Aspects we skip — not useful for AI to fill (operator/product-lookup handles these)
# California Prop 65 Warning was previously skipped as "legal boilerplate" but Dave
# flagged (session 39, item tgw202605060201087) that it's a real, near-universal
# aspect that must be shown/filled like any other — removed from the skip list.
_SKIP_ASPECTS = {'MPN', 'Model', 'Unit Quantity', 'Unit Type'}

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


def _structure_aspects(raw_aspects: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter + flatten eBay's aspect metadata into the TGW structured form.
    Shared by the live per-category fetch and the bulk download (same shape)."""
    results = []
    for aspect in raw_aspects:
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


def _fetch_aspects_live(cfg: Dict[str, Any], category_id: str) -> List[Dict[str, Any]]:
    tree_id = get_category_tree_id(cfg)
    data = ebay_get(
        cfg,
        f'/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category',
        params={'category_id': category_id},
    )
    return _structure_aspects(data.get('aspects', []))


def _bulk_dir(cfg: Dict[str, Any]) -> Optional[Path]:
    root = cfg.get('catalog_root')
    return Path(root) / 'ebay-aspects-bulk' if root else None


def _load_bulk_shard(cfg: Dict[str, Any], category_id: str) -> Optional[List[Dict[str, Any]]]:
    bulk = _bulk_dir(cfg)
    if not bulk:
        return None
    shard = bulk / f'{category_id}.json'
    if not shard.exists():
        return None
    try:
        return json.loads(shard.read_text(encoding='utf-8'))['aspects']
    except (OSError, ValueError, KeyError) as exc:
        log.warning('bulk aspects shard %s unreadable: %s', shard, exc)
        return None


def bulk_refresh_aspects(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Download aspects for EVERY leaf category in one fetch_item_aspects call
    (commerce.taxonomy.bulk pool — 100/day, separate from the 5,000/day
    per-category pool) and write one shard file per category under
    `catalog_root/ebay-aspects-bulk/`. The raw gzip is kept beside the shards
    as the permanent source asset (~130 MB); shards are the derived, cheap-to-
    read form. Memory note: parses an ~870 MB JSON — run from the CLI
    (`tgw warm-ebay-aspects`), not inside a worker or the web server.
    """
    bulk = _bulk_dir(cfg)
    if not bulk:
        raise ValueError('catalog_root not configured — nowhere to store bulk aspects')
    tree_id = get_category_tree_id(cfg)
    log.info('bulk aspects download starting (tree %s) — one commerce.taxonomy.bulk call', tree_id)
    raw = ebay_get_bytes(
        cfg, f'/commerce/taxonomy/v1/category_tree/{tree_id}/fetch_item_aspects')
    try:
        text = gzip.decompress(raw).decode('utf-8')
    except (OSError, gzip.BadGzipFile):
        text = raw.decode('utf-8')  # transport layer already decoded it
    data = json.loads(text)
    categories = data.get('categoryAspects', [])

    bulk.mkdir(parents=True, exist_ok=True)
    raw_path = bulk / 'fetch_item_aspects.json.gz'
    raw_path.write_bytes(raw if raw[:2] == b'\x1f\x8b' else gzip.compress(text.encode('utf-8')))

    written = 0
    fetched_at = time.time()
    for entry in categories:
        cid = str(entry.get('category', {}).get('categoryId', '')).strip()
        if not cid:
            continue
        shard = bulk / f'{cid}.json'
        # code-review follow-up (#1239): plain write_text here left this
        # loop's ~15,000 shard writes as the one unconverted site — a
        # crash mid-loop could corrupt a shard, and _load_bulk_shard()'s
        # silent catch-and-fall-through would then burn a live per-category
        # Taxonomy call (5,000/day pool) instead of using the bulk shard
        # (100/day pool), defeating the whole point of this bulk download.
        _atomic_write_cache_json(shard, {
            '_cached_at': fetched_at,
            'name': entry.get('category', {}).get('categoryName', ''),
            'aspects': _structure_aspects(entry.get('aspects', [])),
        }, pretty=False)
        written += 1
    log.info('bulk aspects refresh: %d category shards written to %s', written, bulk)
    return {'categories': written, 'raw_bytes': len(raw), 'shard_dir': str(bulk)}


def get_aspects(cfg: Dict[str, Any], category_id: str) -> List[Dict[str, Any]]:
    """
    Return aspect definitions for a category, filtered and structured for AI use.

    Each entry: {name, required, mode, allowed_values (list, empty = free text)}
    Lookup order: memory → per-category disk cache → bulk shard → live API.
    Cached entries are permanent until an explicit refresh (`tgw
    warm-ebay-aspects`) — no auto-expiry, matching the category-tree policy.
    """
    category_id = str(category_id)
    if category_id in _aspects_mem_cache:
        return _aspects_mem_cache[category_id]

    cache_path = _aspects_cache_path(cfg)
    disk_cache = _load_aspects_disk_cache(cache_path)
    entry = disk_cache.get(category_id)
    if entry and 'aspects' in entry:
        _aspects_mem_cache[category_id] = entry['aspects']
        return entry['aspects']

    shard = _load_bulk_shard(cfg, category_id)
    if shard is not None:
        _aspects_mem_cache[category_id] = shard
        return shard

    results = _fetch_aspects_live(cfg, category_id)
    _aspects_mem_cache[category_id] = results
    if cache_path:
        # audit#1143 #1239: previously read-modify-wrote the whole disk_cache
        # dict with a plain write_text — unlocked, so two concurrent
        # cache-miss writers could race and silently drop each other's new
        # entries, and a crash mid-write could corrupt the entire cache
        # (every category, not just this one). locked_merge_cache_json holds
        # a flock across a FRESH read+merge+atomic-write — the live fetch
        # above already happened outside the lock, so this doesn't
        # serialize concurrent live API calls, only the disk merge itself.
        entry = {'_cached_at': time.time(), 'aspects': results}
        try:
            locked_merge_cache_json(
                cache_path,
                lambda current, _cid=category_id, _entry=entry: {**current, _cid: _entry},
            )
        except OSError as exc:
            log.warning('could not write aspects cache: %s', exc)
    return results


def warm_missing_aspects(cfg: Dict[str, Any], category_ids: List[str],
                         max_new: int = 25) -> int:
    """Opportunistically fill the aspects cache for categories not yet cached
    among *category_ids* — meant to be called from an existing scheduled
    worker (e.g. ebay_sync's periodic run) with the categories it just
    touched, so real-use coverage grows toward complete over time using
    whatever Taxonomy API quota is left for the day. With the bulk shards
    populated (`tgw warm-ebay-aspects`) this is normally a no-op.

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
        if entry and 'aspects' in entry:
            continue
        if _load_bulk_shard(cfg, cid) is not None:
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
