"""
tgw.apis.ebay.conditions — eBay condition policy lookup and best-condition selection.

eBay groups all 15K+ categories into 26 unique condition policy sets.
This module caches the full policy table and resolves an item's condition
to the best allowed conditionId for its category.

Core rule: NEVER upgrade condition. Always fall back to the same or worse
condition from the buyer's perspective. If no valid fallback exists (all
allowed conditions are better than the item), returns None — the item
must be reviewed manually before listing.

Cache: catalog_root/ebay-condition-policies.json — refreshed every 7 days,
or on demand via refresh_condition_policies(cfg).

Public API:
    best_condition(cfg, category_id, item_condition)
        → {'condition_id': '3000', 'condition_label': 'Used'} | None

    refresh_condition_policies(cfg)
        → fetches from eBay Metadata API, writes cache

    condition_enum(condition_id)
        → Inventory API enum string (e.g. '3000' → 'USED_EXCELLENT')
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tgw.apis.ebay.client import ebay_get

log = logging.getLogger(__name__)

CACHE_MAX_AGE_DAYS = 7
_METADATA_PATH = '/sell/metadata/v1/marketplace/EBAY_US/get_item_condition_policies'


# ---------------------------------------------------------------------------
# Condition quality ranking — lower rank = better for buyer
# ---------------------------------------------------------------------------

CONDITION_RANK: Dict[str, int] = {
    '1000': 0,   # New
    '1500': 1,   # New Other / Open Box
    '1750': 2,   # New With Defects
    '2750': 3,   # Like New
    '2000': 4,   # Certified Refurbished
    '2010': 4,   # Excellent Refurbished
    '3500': 5,   # Manufacturer Refurbished
    '2020': 5,   # Very Good Refurbished
    '4000': 5,   # Very Good
    '2030': 6,   # Good Refurbished
    '2500': 6,   # Seller Refurbished
    '2990': 6,   # Pre-loved Refurbished
    '3000': 7,   # Used / Pre-owned / Pre-owned - Good
    '3010': 7,   # Generic Refurbished
    '5000': 7,   # Good
    '6000': 8,   # Acceptable
    '7000': 9,   # For Parts or Not Working
}

# conditionId → Inventory API enum string
CONDITION_ID_TO_ENUM: Dict[str, str] = {
    '1000': 'NEW',
    '1500': 'NEW_OTHER',
    '1750': 'NEW_WITH_DEFECTS',
    '2000': 'CERTIFIED_REFURBISHED',
    '2010': 'EXCELLENT_REFURBISHED',
    '2020': 'VERY_GOOD_REFURBISHED',
    '2030': 'GOOD_REFURBISHED',
    '2500': 'SELLER_REFURBISHED',
    '2750': 'LIKE_NEW',
    '2990': 'LIKE_NEW',
    '3000': 'USED_EXCELLENT',
    '3010': 'SELLER_REFURBISHED',
    '3500': 'MANUFACTURER_REFURBISHED',
    '4000': 'USED_VERY_GOOD',
    '5000': 'USED_GOOD',
    '6000': 'USED_ACCEPTABLE',
    '7000': 'FOR_PARTS_OR_NOT_WORKING',
}

# Item condition string → preferred conditionIds in priority order
_ITEM_CONDITION_PREFERRED: Dict[str, List[str]] = {
    'new':                       ['1000'],
    'new in box':                ['1000'],
    'brand new':                 ['1000'],
    'new old stock':             ['1500', '1000'],
    'nos':                       ['1500', '1000'],
    'open box':                  ['1500', '2750'],
    'new other':                 ['1500'],
    'new with defects':          ['1750', '1500'],
    'like new':                  ['2750', '4000'],
    'manufacturer refurbished':  ['3500', '2000', '2010'],
    'certified refurbished':     ['2000', '2010', '3500'],
    'seller refurbished':        ['2500'],
    'refurbished':               ['2500', '3500', '2000'],
    'used: excellent':           ['4000', '3000'],
    'excellent':                 ['4000', '3000'],
    'used: very good':           ['4000'],
    'very good':                 ['4000', '3000'],
    'used: good':                ['5000', '3000'],
    'good':                      ['5000', '3000'],
    'used':                      ['3000', '5000'],
    'pre-owned':                 ['3000', '5000'],
    'pre owned':                 ['3000', '5000'],
    'used: acceptable':          ['6000', '5000'],
    'acceptable':                ['6000', '5000'],
    'fair':                      ['6000', '5000'],
    'for parts':                 ['7000'],
    'for parts or not working':  ['7000'],
    'not working':               ['7000'],
    'parts only':                ['7000'],
}

_DEFAULT_PREFERRED = ['3000', '5000', '6000']   # fallback when condition unknown


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def _cache_path(cfg: Dict[str, Any]) -> Path:
    return cfg['catalog_root'] / 'ebay-condition-policies.json'


def _load_cache(cfg: Dict[str, Any]) -> Optional[Dict[str, List[Tuple[str, str]]]]:
    """Return cached policies dict or None if missing/stale."""
    path = _cache_path(cfg)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        fetched_at = datetime.fromisoformat(raw['fetched_at'])
        if datetime.now(timezone.utc) - fetched_at > timedelta(days=CACHE_MAX_AGE_DAYS):
            log.info('condition policy cache is stale — will refresh')
            return None
        # Stored as {cat_id: [[cond_id, desc], ...]}
        return {k: [tuple(p) for p in v] for k, v in raw['policies'].items()}
    except Exception as exc:
        log.warning('condition policy cache unreadable (%s) — will refresh', exc)
        return None


def refresh_condition_policies(cfg: Dict[str, Any]) -> Dict[str, List[Tuple[str, str]]]:
    """Fetch full condition policy table from eBay and write to cache."""
    log.info('fetching eBay condition policies from Metadata API')
    data = ebay_get(cfg, _METADATA_PATH)
    policies: Dict[str, List[Tuple[str, str]]] = {}
    for entry in data.get('itemConditionPolicies', []):
        cat_id = entry['categoryId']
        conds = [
            (c['conditionId'], c['conditionDescription'])
            for c in entry.get('itemConditions', [])
        ]
        if conds:
            policies[cat_id] = conds

    path = _cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'policies':   {k: list(v) for k, v in policies.items()},
    }, indent=2), encoding='utf-8')

    log.info('condition policies cached: %d categories, %d unique sets',
             len(policies),
             len({frozenset(c[0] for c in v) for v in policies.values()}))
    return policies


def _get_policies(cfg: Dict[str, Any]) -> Dict[str, List[Tuple[str, str]]]:
    """Return policies from cache, refreshing if needed."""
    cached = _load_cache(cfg)
    if cached is not None:
        return cached
    return refresh_condition_policies(cfg)


# ---------------------------------------------------------------------------
# Best-condition lookup
# ---------------------------------------------------------------------------

def best_condition(cfg: Dict[str, Any], category_id: str,
                   item_condition: str) -> Optional[Dict[str, str]]:
    """
    Return the best allowed condition for *item_condition* in *category_id*.

    Never upgrades condition — falls back to the same or worse quality only.
    Returns {'condition_id': str, 'condition_label': str, 'condition_enum': str}
    or None if no valid condition exists (all allowed are better than item).

    None means the item needs manual review before it can be listed.
    """
    policies = _get_policies(cfg)
    allowed: List[Tuple[str, str]] = policies.get(str(category_id), [])

    if not allowed:
        log.warning('no condition policy for category %s — using enum fallback',
                    category_id)
        return None

    normalized = item_condition.lower().strip()
    preferred_ids = _ITEM_CONDITION_PREFERRED.get(normalized, _DEFAULT_PREFERRED)
    item_rank = min((CONDITION_RANK.get(cid, 7) for cid in preferred_ids), default=7)

    allowed_map: Dict[str, str] = {cid: desc for cid, desc in allowed}

    # 1. Try preferred conditionIds in order (first available wins)
    for pref_id in preferred_ids:
        if pref_id in allowed_map:
            return _make_result(pref_id, allowed_map[pref_id])

    # 2. Fall back: best (lowest rank) allowed condition that is same-or-worse
    candidates = [
        (CONDITION_RANK.get(cid, 7), cid, desc)
        for cid, desc in allowed
        if CONDITION_RANK.get(cid, 7) >= item_rank
    ]
    if candidates:
        candidates.sort()
        _, best_id, best_desc = candidates[0]
        log.info('condition fallback for %r in category %s: %s (%s)',
                 item_condition, category_id, best_id, best_desc)
        return _make_result(best_id, best_desc)

    # 3. All allowed conditions are better than item — cannot list honestly
    allowed_ids = [cid for cid, _ in allowed]
    log.warning('no same-or-worse condition for %r in category %s (allowed: %s)',
                item_condition, category_id, allowed_ids)
    return None


def condition_enum(condition_id: str) -> str:
    """Return the Inventory API enum string for a conditionId."""
    return CONDITION_ID_TO_ENUM.get(condition_id, 'USED_EXCELLENT')


def _make_result(condition_id: str, condition_label: str) -> Dict[str, str]:
    return {
        'condition_id':    condition_id,
        'condition_label': condition_label,
        'condition_enum':  condition_enum(condition_id),
    }
