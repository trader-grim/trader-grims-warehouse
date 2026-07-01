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

from tgw.apis.ebay.client import ebay_get

log = logging.getLogger(__name__)

MARKETPLACE_ID = 'EBAY_US'
_TREE_CACHE_MAX_AGE = 30 * 86400  # 30 days — eBay's tree changes rarely
_TREE_ID_CACHE_MAX_AGE = 365 * 86400  # tree ID for a marketplace is effectively permanent

# eBay's documented default category tree ID for EBAY_US — this is a stable platform
# constant (not business data), used only as a last-resort fallback when both the
# disk cache and a live call are unavailable (e.g. Taxonomy API quota exhausted),
# so a tree-ID lookup failure doesn't also block aspects/condition/search calls that
# only need the ID, not a fresh live confirmation of it.
_EBAY_US_DEFAULT_TREE_ID = '0'

_tree_id_cache: Optional[str] = None
_tree_index_cache: Optional[Dict[str, Dict[str, Any]]] = None
_tree_roots_cache: Optional[List[str]] = None


def _tree_id_cache_path(cfg: Dict[str, Any]) -> Optional[Path]:
    root = cfg.get('catalog_root')
    return Path(root) / 'ebay-category-tree-id.json' if root else None


def get_category_tree_id(cfg: Dict[str, Any]) -> str:
    """Return the eBay category tree ID for EBAY_US.

    Resolution order: in-memory cache (this process) → disk cache (any process,
    effectively permanent — this ID does not change) → live API call → the
    documented EBAY_US default ('0') as a last resort, so a quota-exhausted
    Taxonomy API doesn't also block every aspects/condition/search call that only
    needs this ID, not a fresh confirmation of it.
    """
    global _tree_id_cache
    if _tree_id_cache is not None:
        return _tree_id_cache

    cache_path = _tree_id_cache_path(cfg)
    if cache_path and cache_path.exists():
        try:
            wrapper = json.loads(cache_path.read_text(encoding='utf-8'))
            if time.time() - wrapper.get('_cached_at', 0) < _TREE_ID_CACHE_MAX_AGE:
                _tree_id_cache = wrapper['tree_id']
                return _tree_id_cache
        except (OSError, ValueError, KeyError) as exc:
            log.warning('tree-id cache unreadable, refetching: %s', exc)

    try:
        data = ebay_get(cfg,
                        '/commerce/taxonomy/v1/get_default_category_tree_id',
                        params={'marketplace_id': MARKETPLACE_ID})
        _tree_id_cache = data['categoryTreeId']
        log.debug('eBay category tree ID: %s', _tree_id_cache)
        if cache_path:
            try:
                cache_path.write_text(
                    json.dumps({'_cached_at': time.time(), 'tree_id': _tree_id_cache}),
                    encoding='utf-8',
                )
            except OSError as exc:
                log.warning('could not write tree-id cache: %s', exc)
    except Exception as exc:
        log.warning('tree-id live lookup failed (%s) — using documented EBAY_US default %r',
                    exc, _EBAY_US_DEFAULT_TREE_ID)
        _tree_id_cache = _EBAY_US_DEFAULT_TREE_ID

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
        suggestions = get_category_suggestions(cfg, query)
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
    return Path(root) / 'ebay-category-tree.json' if root else None


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


def _ensure_tree_index(cfg: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return (and lazily build/cache) the full category id → node index."""
    global _tree_index_cache, _tree_roots_cache
    if _tree_index_cache is not None:
        return _tree_index_cache

    cache_path = _tree_cache_path(cfg)
    tree_data: Optional[Dict[str, Any]] = None

    if cache_path and cache_path.exists():
        try:
            wrapper = json.loads(cache_path.read_text(encoding='utf-8'))
            if time.time() - wrapper.get('_cached_at', 0) < _TREE_CACHE_MAX_AGE:
                tree_data = wrapper.get('tree')
        except (OSError, ValueError) as exc:
            log.warning('category tree cache unreadable, refetching: %s', exc)

    if tree_data is None:
        tree_id = get_category_tree_id(cfg)
        tree_data = ebay_get(cfg, f'/commerce/taxonomy/v1/category_tree/{tree_id}')
        if cache_path:
            try:
                cache_path.write_text(
                    json.dumps({'_cached_at': time.time(), 'tree': tree_data}),
                    encoding='utf-8',
                )
            except OSError as exc:
                log.warning('could not write category tree cache: %s', exc)

    index = _build_index(cfg, tree_data)
    _tree_index_cache = index
    _tree_roots_cache = [nid for nid, n in index.items() if n['parent_id'] is None]
    log.info('eBay category tree loaded: %d categories', len(index))
    return index


def _breadcrumb(index: Dict[str, Dict[str, Any]], category_id: str) -> str:
    parts = []
    cur = index.get(category_id)
    seen = set()
    while cur and cur['id'] not in seen:
        seen.add(cur['id'])
        parts.append(cur['name'])
        cur = index.get(cur['parent_id']) if cur['parent_id'] else None
    return ' > '.join(reversed(parts))


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
        if not node['leaf']:
            continue  # only leaf categories are assignable
        if name_l == q:
            exact.append(node)
        elif name_l.startswith(q):
            starts.append(node)
        elif q in name_l:
            contains.append(node)
    ranked = exact + starts + contains
    results = []
    for node in ranked[:limit]:
        results.append({
            'id': node['id'],
            'name': node['name'],
            'path': _breadcrumb(index, node['parent_id']) if node['parent_id'] else '',
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
        out.append({'id': node['id'], 'name': node['name'], 'leaf': node['leaf']})
    out.sort(key=lambda n: n['name'])
    return out
