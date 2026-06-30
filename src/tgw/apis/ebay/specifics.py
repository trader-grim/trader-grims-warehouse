"""
tgw.apis.ebay.specifics — eBay item aspects (specifics) for a category.

Fetches the aspect definitions for a given categoryId and returns them in
a structured form ready to pass to an AI for value suggestion.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from tgw.apis.ebay.client import ebay_get
from tgw.apis.ebay.taxonomy import get_category_tree_id

log = logging.getLogger(__name__)

# Aspects we skip — legal boilerplate, not useful for AI to fill
_SKIP_ASPECTS = {'California Prop 65 Warning', 'MPN', 'Model',
                 'Unit Quantity', 'Unit Type'}


def get_aspects(cfg: Dict[str, Any], category_id: str) -> List[Dict[str, Any]]:
    """
    Return aspect definitions for a category, filtered and structured for AI use.

    Each entry: {name, required, mode, allowed_values (list, empty = free text)}
    """
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
