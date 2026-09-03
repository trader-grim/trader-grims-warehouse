"""
tgw.apis.ebay.taxonomy — eBay Taxonomy API wrapper.

Resolves a free-text category string (from AI identification) to an eBay
categoryId using the Commerce Taxonomy API category suggestions endpoint.

Caches the category tree ID per process lifetime (it rarely changes).

Also maintains a disk+memory cache of the FULL category tree so the web UI's
search/browse/ID-lookup can run with zero live eBay API calls per keystroke —
the live get_category_suggestions endpoint is quota-limited and was getting
exhausted by the item-detail type-ahead (one call per keystroke).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tgw import quota
from tgw.apis.ebay.client import ebay_get
from tgw.catalog import atomic_write_json as _atomic_write_cache_json
from tgw.config import configured_ebay_environment, ebay_cache_filename

log = logging.getLogger(__name__)

MARKETPLACE_ID = 'EBAY_US'
# Session 41 (2026-07-02): the category tree does NOT expire on a timer. Dave's
# design (stated twice — session 39 and again here): eBay announces category-tree
# changes, it doesn't change often, so there is no reason to burn a live re-fetch on
# a schedule. The cache is permanent until explicitly invalidated — see
# `refresh_category_tree_cache()` / `tgw refresh-ebay-taxonomy`, run manually when an
# eBay taxonomy change is announced. A 30-day auto-expiry was implemented here
# instead of that spec and went unnoticed for 3+ days of live production, during
# which quota was perpetually re-exhausted refetching a tree that never changed.
_TREE_ID_CACHE_MAX_AGE = 365 * 86400  # tree ID for a marketplace is effectively permanent

# eBay's documented default category tree ID for EBAY_US — this is a stable platform
# constant (not business data), used only as a last-resort fallback when both the
# disk cache and a live call are unavailable (e.g. Taxonomy API quota exhausted),
# so a tree-ID lookup failure doesn't also block aspects/condition/search calls that
# only need the ID, not a fresh live confirmation of it.
_EBAY_US_DEFAULT_TREE_ID = '0'

_tree_id_cache: Optional[str] = None
_tree_id_cache_environment: Optional[str] = None
_tree_index_cache: Optional[Dict[str, Dict[str, Any]]] = None
_tree_roots_cache: Optional[List[str]] = None
_tree_index_cache_environment: Optional[str] = None

# eBay Motors US — a genuinely SEPARATE category tree from EBAY_US's tree 0,
# confirmed live 2026-07-09 (todo #1254): a real Motors category 404s
# against tree 0 and only resolves under tree 100. NOT a branch of the
# EBAY_US tree, an assumption an earlier planning pass got wrong. Hardcoded
# here the same way _EBAY_US_DEFAULT_TREE_ID is — a stable platform
# constant, not business data. The Taxonomy API's marketplace_id enum for
# this marketplace is 'EBAY_MOTORS_US' (distinct from the Sell/Inventory
# API's offer.marketplaceId value 'EBAY_MOTORS' — same marketplace,
# different spelling per eBay API family).
_MOTORS_TREE_ID = '100'
_motors_tree_index_cache: Optional[Dict[str, Dict[str, Any]]] = None
_motors_tree_roots_cache: Optional[List[str]] = None
_motors_tree_index_cache_environment: Optional[str] = None


class CategoryTaxonomySnapshotUnavailable(RuntimeError):
    """The operator taxonomy snapshot is not available locally.

    Operator reads must never turn a missing cache into an implicit provider
    call.  The explicit taxonomy refresh command remains the only path that is
    allowed to fetch a replacement tree.
    """


def _tree_id_cache_path(cfg: Dict[str, Any]) -> Optional[Path]:
    root = cfg.get('catalog_root')
    return (Path(root) / ebay_cache_filename(cfg, 'ebay-category-tree-id.json')
            if root else None)


def get_category_tree_id(cfg: Dict[str, Any]) -> str:
    """Return the eBay category tree ID for EBAY_US.

    Resolution order: in-memory cache (this process) → disk cache (any process,
    effectively permanent — this ID does not change) → live API call → the
    documented EBAY_US default ('0') as a last resort, so a quota-exhausted
    Taxonomy API doesn't also block every aspects/condition/search call that only
    needs this ID, not a fresh confirmation of it.
    """
    global _tree_id_cache, _tree_id_cache_environment
    environment = configured_ebay_environment(cfg)
    if (_tree_id_cache is not None
            and _tree_id_cache_environment == environment):
        return _tree_id_cache

    cache_path = _tree_id_cache_path(cfg)
    if cache_path and cache_path.exists():
        try:
            wrapper = json.loads(cache_path.read_text(encoding='utf-8'))
            if time.time() - wrapper.get('_cached_at', 0) < _TREE_ID_CACHE_MAX_AGE:
                _tree_id_cache = wrapper['tree_id']
                _tree_id_cache_environment = environment
                return _tree_id_cache
        except (OSError, ValueError, KeyError) as exc:
            log.warning('tree-id cache unreadable, refetching: %s', exc)

    try:
        data = ebay_get(cfg,
                        '/commerce/taxonomy/v1/get_default_category_tree_id',
                        params={'marketplace_id': MARKETPLACE_ID})
        _tree_id_cache = data['categoryTreeId']
        _tree_id_cache_environment = environment
        log.debug('eBay category tree ID: %s', _tree_id_cache)
        if cache_path:
            try:
                # audit#1143 #1239: plain write_text — a crash mid-write
                # could corrupt this file, forcing every subsequent process
                # to silently re-hit the live Taxonomy API (the exact
                # quota-exhaustion mode this cache exists to prevent).
                # tgw.catalog.atomic_write_json (tmp+rename, mode-preserving)
                # fixes that.
                _atomic_write_cache_json(
                    cache_path, {'_cached_at': time.time(), 'tree_id': _tree_id_cache},
                    pretty=False)
            except OSError as exc:
                log.warning('could not write tree-id cache: %s', exc)
    except Exception as exc:
        log.warning('tree-id live lookup failed (%s) — using documented EBAY_US default %r',
                    exc, _EBAY_US_DEFAULT_TREE_ID)
        _tree_id_cache = _EBAY_US_DEFAULT_TREE_ID
        _tree_id_cache_environment = environment

    return _tree_id_cache


def get_category_suggestions(cfg: Dict[str, Any],
                              query: str) -> List[Dict[str, Any]]:
    """Return raw category suggestion list for a free-text query."""
    tree_id = get_category_tree_id(cfg)
    data = ebay_get(cfg,
                    f'/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions',
                    params={'q': query})
    return data.get('categorySuggestions', [])


def best_category(cfg: Dict[str, Any],
                  *queries: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Return (categoryId, categoryName) for the best match across one or more queries.
    Tries each query in order and returns the first result found.
    Pass title first, then the broader category string as fallback.
    """
    for query in queries:
        if not query:
            continue
        try:
            suggestions = get_category_suggestions(cfg, query)
        except quota.QuotaBudgetExceeded:
            # A second live query would be gated the same way — propagate
            # so the caller's worker requeues transiently instead of
            # silently degrading to "no category found" (same convention
            # as audit#1143 #1173's lookup_epid fix).
            raise
        except RuntimeError:
            # code-review follow-up: the #1173 precedent cited above also
            # re-raises the plain RuntimeError client.py's load_token()
            # raises for an expired token ('eBay access token is
            # expired...') — every query would fail identically until
            # token_refresh runs, so this must reach worker_base's
            # dedicated 900s 'token is expired' transient-requeue pattern
            # instead of being logged N times and degrading to no category.
            raise
        except Exception as exc:
            # audit#1143 #1181: previously uncaught — a failure on the
            # first (title) query aborted the whole documented fallback
            # chain instead of trying the next (broader category) query.
            log.warning('category suggestion query %r failed (%s) — trying next fallback',
                        query, exc)
            continue
        if suggestions:
            cat = suggestions[0].get('category', {})
            category_id   = cat.get('categoryId')
            category_name = cat.get('categoryName')
            log.info('eBay category for %r → %s (%s)', query, category_name, category_id)
            return category_id, category_name
    log.warning('no eBay category suggestions for queries: %r', queries)
    return None, None


def _tree_cache_path(cfg: Dict[str, Any]) -> Optional[Path]:
    root = cfg.get('catalog_root')
    return (Path(root) / ebay_cache_filename(cfg, 'ebay-category-tree.json')
            if root else None)


def _flatten_tree(node: Dict[str, Any], parent_id: Optional[str],
                   index: Dict[str, Dict[str, Any]]) -> None:
    cat = node.get('category', {})
    cid = cat.get('categoryId')
    if not cid:
        return
    children_nodes = node.get('childCategoryTreeNodes') or []
    child_ids = [c.get('category', {}).get('categoryId') for c in children_nodes]
    child_ids = [c for c in child_ids if c]
    index[cid] = {
        'id': cid,
        'name': cat.get('categoryName', ''),
        'parent_id': parent_id,
        'children': child_ids,
        'leaf': bool(node.get('leafCategoryTreeNode')) or not child_ids,
    }
    for child in children_nodes:
        _flatten_tree(child, cid, index)


def _build_index(cfg: Dict[str, Any], tree_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Flatten the tree, skipping eBay's synthetic rootCategoryNode itself —
    only its children are real, assignable top-level categories."""
    index: Dict[str, Dict[str, Any]] = {}
    root = tree_data.get('rootCategoryNode')
    if root:
        for child in root.get('childCategoryTreeNodes') or []:
            _flatten_tree(child, None, index)
    return index


def _load_or_fetch_tree(cfg: Dict[str, Any], tree_id: str,
                        cache_path: Optional[Path], cache_label: str) -> Dict[str, Any]:
    """Shared core for _ensure_tree_index/_ensure_motors_tree_index (todo
    #1255 code-review follow-up — was two ~30-line copies): load the raw
    tree JSON from disk cache if present, else fetch live and write it.
    The disk cache never auto-expires — see the module-level note by
    ``_TREE_ID_CACHE_MAX_AGE`` for why. Returns the raw tree_data dict
    (not yet flattened into an index)."""
    tree_data: Optional[Dict[str, Any]] = None
    if cache_path and cache_path.exists():
        try:
            wrapper = json.loads(cache_path.read_text(encoding='utf-8'))
            tree_data = wrapper.get('tree')
        except (OSError, ValueError) as exc:
            log.warning('%s cache unreadable, refetching: %s', cache_label, exc)

    if tree_data is None:
        tree_data = ebay_get(cfg, f'/commerce/taxonomy/v1/category_tree/{tree_id}')
        if cache_path:
            try:
                # audit#1143 #1239: plain write_text — see get_category_tree_id's
                # comment above; same non-atomic-write corruption risk, just
                # for the full tree instead of the tree ID.
                _atomic_write_cache_json(
                    cache_path, {'_cached_at': time.time(), 'tree': tree_data},
                    pretty=False)
            except OSError as exc:
                log.warning('could not write %s cache: %s', cache_label, exc)
    return tree_data


def _fetch_tree_live(cfg: Dict[str, Any], tree_id: str,
                     cache_path: Optional[Path]) -> Dict[str, Any]:
    """Shared core for refresh_category_tree_cache/
    refresh_motors_category_tree_cache: force a live re-fetch, overwriting
    the disk cache unconditionally. Returns the raw tree_data dict."""
    tree_data = ebay_get(cfg, f'/commerce/taxonomy/v1/category_tree/{tree_id}')
    if cache_path:
        _atomic_write_cache_json(
            cache_path, {'_cached_at': time.time(), 'tree': tree_data},
            pretty=False)
    return tree_data


def _ensure_tree_index(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return (and lazily build/cache) the full category id → node index.

    The disk cache never auto-expires — see the module-level note by
    ``_TREE_ID_CACHE_MAX_AGE`` for why. Use ``refresh_category_tree_cache()`` to
    force a re-fetch when eBay announces an actual taxonomy change.
    """
    global _tree_index_cache, _tree_roots_cache, _tree_index_cache_environment
    environment = configured_ebay_environment(cfg)
    if (_tree_index_cache is not None
            and _tree_index_cache_environment == environment):
        return _tree_index_cache

    tree_id = get_category_tree_id(cfg)
    tree_data = _load_or_fetch_tree(cfg, tree_id, _tree_cache_path(cfg), 'category tree')

    index = _build_index(cfg, tree_data)
    _tree_index_cache = index
    _tree_roots_cache = [nid for nid, n in index.items() if n['parent_id'] is None]
    _tree_index_cache_environment = environment
    log.info('eBay category tree loaded: %d categories', len(index))
    return index


def refresh_category_tree_cache(cfg: Dict[str, Any]) -> int:
    """Force a live re-fetch of the full category tree, overwriting the disk cache
    and clearing the in-memory cache. Run manually (`tgw refresh-ebay-taxonomy`)
    when eBay announces an actual taxonomy change — the cache otherwise never
    expires on its own.

    Returns the number of categories in the newly cached tree.
    """
    global _tree_index_cache, _tree_roots_cache, _tree_index_cache_environment
    environment = configured_ebay_environment(cfg)
    tree_id = get_category_tree_id(cfg)
    tree_data = _fetch_tree_live(cfg, tree_id, _tree_cache_path(cfg))
    index = _build_index(cfg, tree_data)
    _tree_index_cache = index
    _tree_roots_cache = [nid for nid, n in index.items() if n['parent_id'] is None]
    _tree_index_cache_environment = environment
    log.info('eBay category tree cache refreshed: %d categories', len(index))
    return len(index)


# ---------------------------------------------------------------------------
# eBay Motors tree (tree 100) — todo #1255, PP-EBAY-MOTORS-001
#
# Mirrors the EBAY_US tree caching above, but for the genuinely separate
# Motors tree. Deliberately a parallel set of GLOBAL CACHES (not threading
# tree_id through every EBAY_US function above and its callers) — the
# EBAY_US tree is the hot path for the whole drafting pipeline (category
# suggestions, search, browse) and stays untouched; only the fetch/cache
# mechanics are shared, via _load_or_fetch_tree/_fetch_tree_live above.
# ---------------------------------------------------------------------------

def _motors_tree_cache_path(cfg: Dict[str, Any]) -> Optional[Path]:
    root = cfg.get('catalog_root')
    return (Path(root) / ebay_cache_filename(cfg, 'ebay-motors-category-tree.json')
            if root else None)


def _ensure_motors_tree_index(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return (and lazily build/cache) the Motors category id → node index.

    Same never-auto-expires contract as _ensure_tree_index — use
    refresh_motors_category_tree_cache() to force a re-fetch.
    """
    global _motors_tree_index_cache, _motors_tree_roots_cache
    global _motors_tree_index_cache_environment
    environment = configured_ebay_environment(cfg)
    if (_motors_tree_index_cache is not None
            and _motors_tree_index_cache_environment == environment):
        return _motors_tree_index_cache

    tree_data = _load_or_fetch_tree(cfg, _MOTORS_TREE_ID, _motors_tree_cache_path(cfg),
                                    'Motors category tree')
    index = _build_index(cfg, tree_data)
    _motors_tree_index_cache = index
    _motors_tree_roots_cache = [
        nid for nid, node in index.items() if node['parent_id'] is None
    ]
    _motors_tree_index_cache_environment = environment
    log.info('eBay Motors category tree loaded: %d categories', len(index))
    return index


def refresh_motors_category_tree_cache(cfg: Dict[str, Any]) -> int:
    """Force a live re-fetch of the Motors category tree (mirrors
    refresh_category_tree_cache for the EBAY_US tree). Returns the number
    of categories in the newly cached tree."""
    global _motors_tree_index_cache, _motors_tree_roots_cache
    global _motors_tree_index_cache_environment
    environment = configured_ebay_environment(cfg)
    tree_data = _fetch_tree_live(cfg, _MOTORS_TREE_ID, _motors_tree_cache_path(cfg))
    index = _build_index(cfg, tree_data)
    _motors_tree_index_cache = index
    _motors_tree_roots_cache = [
        nid for nid, node in index.items() if node['parent_id'] is None
    ]
    _motors_tree_index_cache_environment = environment
    log.info('eBay Motors category tree cache refreshed: %d categories', len(index))
    return len(index)


def is_motors_category(cfg: Dict[str, Any], category_id: str) -> bool:
    """True if *category_id* belongs to eBay Motors' distinct category tree.

    Backed by the local disk+memory Motors tree cache above — a real
    membership check, not a live API call per category (todo #1255,
    replacing the per-category live-call stopgap sync.py used from todo
    #1254). Fails closed to False on any fetch error — never blocks a
    draft push over a Taxonomy API hiccup.

    Cross-tree collision check (code-review follow-up): verified live
    against the real cached trees — 17,105 assignable EBAY_US categories
    (tree 0) vs 3,288 Motors categories (tree 100), **zero overlap**. The
    only id present in both trees' raw data is '0' (each tree's own
    synthetic rootCategoryNode), and _build_index() already excludes the
    root from the indexed set (only its children are indexed), so it can
    never appear as a real category_id here either. A false positive
    (an ordinary EBAY_US category_id misidentified as Motors) is not
    possible with the current tree data.
    """
    if not category_id:
        return False
    try:
        index = _ensure_motors_tree_index(cfg)
    except Exception as exc:
        log.warning('Motors category tree unavailable (defaulting to EBAY_US): %s', exc)
        return False
    return str(category_id).strip() in index


def _breadcrumb(index: Dict[str, Dict[str, Any]], category_id: str) -> str:
    parts = []
    cur = index.get(category_id)
    seen = set()
    while cur and cur['id'] not in seen:
        seen.add(cur['id'])
        parts.append(cur['name'])
        cur = index.get(cur['parent_id']) if cur['parent_id'] else None
    return ' > '.join(reversed(parts))


def _snapshot_tree_index(
        cfg: Dict[str, Any], *, motors: bool = False,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Load one taxonomy index from memory/disk only.

    Unlike ``_ensure_tree_index`` this helper deliberately has no live fallback
    and does not resolve a tree ID.  The snapshot filenames are stable platform
    data, so operator page reads can remain quota-free and deterministic.
    """
    global _tree_index_cache, _tree_roots_cache
    global _motors_tree_index_cache, _motors_tree_roots_cache
    global _tree_index_cache_environment, _motors_tree_index_cache_environment

    environment = configured_ebay_environment(cfg)
    cached = _motors_tree_index_cache if motors else _tree_index_cache
    cached_environment = (
        _motors_tree_index_cache_environment
        if motors else _tree_index_cache_environment
    )
    if cached is not None and cached_environment == environment:
        return cached

    cache_path = _motors_tree_cache_path(cfg) if motors else _tree_cache_path(cfg)
    if cache_path is None or not cache_path.exists():
        return None
    try:
        wrapper = json.loads(cache_path.read_text(encoding='utf-8'))
        if not isinstance(wrapper, dict):
            raise ValueError('tree snapshot wrapper is not an object')
        tree_data = wrapper.get('tree')
        if not isinstance(tree_data, dict):
            raise ValueError('tree snapshot has no tree object')
        index = _build_index(cfg, tree_data)
    except (OSError, ValueError) as exc:
        log.warning('%s snapshot unavailable: %s',
                    'Motors category tree' if motors else 'category tree', exc)
        return None

    roots = [nid for nid, node in index.items() if node['parent_id'] is None]
    if motors:
        _motors_tree_index_cache = index
        _motors_tree_roots_cache = roots
        _motors_tree_index_cache_environment = environment
    else:
        _tree_index_cache = index
        _tree_roots_cache = roots
        _tree_index_cache_environment = environment
    return index


def _category_projection(
        index: Dict[str, Dict[str, Any]], category_id: str, marketplace_id: str,
) -> Optional[Dict[str, Any]]:
    node = index.get(str(category_id).strip())
    if not node:
        return None
    return {
        'id': node['id'],
        'name': node['name'],
        'path': _breadcrumb(index, node['id']),
        'leaf': node['leaf'],
        'marketplace_id': marketplace_id,
        'source': 'taxonomy-snapshot',
    }


def _available_snapshot_indexes(
        cfg: Dict[str, Any],
) -> List[Tuple[str, Dict[str, Dict[str, Any]]]]:
    available: List[Tuple[str, Dict[str, Dict[str, Any]]]] = []
    us_index = _snapshot_tree_index(cfg)
    if us_index is not None:
        available.append((MARKETPLACE_ID, us_index))
    motors_index = _snapshot_tree_index(cfg, motors=True)
    if motors_index is not None:
        available.append(('EBAY_MOTORS_US', motors_index))
    return available


def get_cached_category_node(
        cfg: Dict[str, Any], category_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve an ID through local US/Motors snapshots, never the provider."""
    value = str(category_id).strip()
    if not value:
        return None
    for marketplace_id, index in _available_snapshot_indexes(cfg):
        projected = _category_projection(index, value, marketplace_id)
        if projected is not None:
            return projected
    return None


def search_categories_cached(cfg: Dict[str, Any], query: str,
                             limit: int = 20) -> List[Dict[str, Any]]:
    """Search local US/Motors taxonomy snapshots by human name or numeric ID."""
    indexes = _available_snapshot_indexes(cfg)
    if not indexes:
        raise CategoryTaxonomySnapshotUnavailable('category taxonomy snapshot is unavailable')
    q = query.strip().lower()
    if not q:
        return []

    exact: List[Tuple[str, Dict[str, Any], Dict[str, Dict[str, Any]]]] = []
    starts: List[Tuple[str, Dict[str, Any], Dict[str, Dict[str, Any]]]] = []
    contains: List[Tuple[str, Dict[str, Any], Dict[str, Dict[str, Any]]]] = []
    numeric = q.isdigit()
    for marketplace_id, index in indexes:
        for node in index.values():
            if not node['leaf']:
                continue
            candidate = node['id'] if numeric else node['name'].lower()
            row = (marketplace_id, node, index)
            if candidate == q:
                exact.append(row)
            elif candidate.startswith(q):
                starts.append(row)
            elif not numeric and q in candidate:
                contains.append(row)

    ranked = exact + starts + contains
    ranked.sort(key=lambda row: (
        0 if (row[1]['id'] if numeric else row[1]['name'].lower()) == q else
        1 if (row[1]['id'] if numeric else row[1]['name'].lower()).startswith(q) else 2,
        _breadcrumb(row[2], row[1]['id']).casefold(),
        row[1]['id'],
    ))
    return [
        _category_projection(index, node['id'], marketplace_id)
        for marketplace_id, node, index in ranked[:limit]
    ]


def get_cached_category_children(
        cfg: Dict[str, Any], parent_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Return snapshot-only browse children with full human taxonomy paths."""
    indexes = _available_snapshot_indexes(cfg)
    if not indexes:
        raise CategoryTaxonomySnapshotUnavailable('category taxonomy snapshot is unavailable')

    value = str(parent_id or '').strip()
    out: List[Dict[str, Any]] = []
    for marketplace_id, index in indexes:
        if value:
            parent = index.get(value)
            if parent is None:
                continue
            child_ids = parent['children']
        elif marketplace_id == MARKETPLACE_ID:
            child_ids = _tree_roots_cache or []
        else:
            child_ids = _motors_tree_roots_cache or []
        for category_id in child_ids:
            projected = _category_projection(index, category_id, marketplace_id)
            if projected is not None:
                out.append(projected)
        if value:
            break
    out.sort(key=lambda node: (node['path'].casefold(), node['id']))
    return out


def search_categories_local(cfg: Dict[str, Any], query: str,
                             limit: int = 20) -> List[Dict[str, Any]]:
    """Search the cached local tree by name — no live API call, no quota risk."""
    index = _ensure_tree_index(cfg)
    q = query.strip().lower()
    if not q:
        return []
    exact, starts, contains = [], [], []
    for node in index.values():
        name_l = node['name'].lower()
        candidate = node['id'] if q.isdigit() else name_l
        if not node['leaf']:
            continue  # only leaf categories are assignable
        if candidate == q:
            exact.append(node)
        elif candidate.startswith(q):
            starts.append(node)
        elif not q.isdigit() and q in name_l:
            contains.append(node)
    ranked = exact + starts + contains
    results = []
    for node in ranked[:limit]:
        results.append({
            'id': node['id'],
            'name': node['name'],
            'path': _breadcrumb(index, node['id']),
        })
    return results


def get_category_node(cfg: Dict[str, Any], category_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a raw category ID (operator-typed) to name + breadcrumb + leaf flag."""
    index = _ensure_tree_index(cfg)
    node = index.get(str(category_id).strip())
    if not node:
        return None
    return {
        'id': node['id'],
        'name': node['name'],
        'path': _breadcrumb(index, node['id']),
        'leaf': node['leaf'],
    }


def get_category_children(cfg: Dict[str, Any],
                           parent_id: Optional[str]) -> List[Dict[str, Any]]:
    """Return direct children of parent_id for tree-browse navigation (roots if None/empty)."""
    index = _ensure_tree_index(cfg)
    if not parent_id:
        child_ids = _tree_roots_cache or []
    else:
        parent = index.get(str(parent_id).strip())
        child_ids = parent['children'] if parent else []
    out = []
    for cid in child_ids:
        node = index.get(cid)
        if not node:
            continue
        out.append({
            'id': node['id'],
            'name': node['name'],
            'path': _breadcrumb(index, node['id']),
            'leaf': node['leaf'],
        })
    out.sort(key=lambda n: n['name'])
    return out
