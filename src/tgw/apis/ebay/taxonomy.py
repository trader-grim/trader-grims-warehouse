"""
tgw.apis.ebay.taxonomy — eBay Taxonomy API wrapper.

Resolves a free-text category string (from AI identification) to an eBay
categoryId using the Commerce Taxonomy API category suggestions endpoint.

Caches the category tree ID per process lifetime (it rarely changes).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from tgw.apis.ebay.client import ebay_get

log = logging.getLogger(__name__)

MARKETPLACE_ID = 'EBAY_US'

_tree_id_cache: Optional[str] = None


def get_category_tree_id(cfg: Dict[str, Any]) -> str:
    global _tree_id_cache
    if _tree_id_cache is None:
        data = ebay_get(cfg,
                        '/commerce/taxonomy/v1/get_default_category_tree_id',
                        params={'marketplace_id': MARKETPLACE_ID})
        _tree_id_cache = data['categoryTreeId']
        log.debug('eBay category tree ID: %s', _tree_id_cache)
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
