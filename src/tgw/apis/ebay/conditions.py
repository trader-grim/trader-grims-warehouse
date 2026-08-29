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

# In-process memoization (same pattern as taxonomy._tree_id_cache /
# specifics._aspects_mem_cache): _get_policies() was re-reading and
# re-parsing the full ~2.7MB disk cache on every call, unlike its sibling
# caches which hold the parsed result in memory for the life of the process
# (audit#1143).
_policies_mem_cache: Optional[Dict[str, List[Tuple[str, str]]]] = None
_required_mem_cache: Dict[str, bool | None] = {}


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
        policies = {k: [tuple(p) for p in v] for k, v in raw['policies'].items()}
        global _required_mem_cache
        flags = raw.get('item_condition_required', {})
        _required_mem_cache = {str(k): v if isinstance(v, bool) else None for k, v in flags.items()} if isinstance(flags, dict) else {}
        return policies
    except Exception as exc:
        log.warning('condition policy cache unreadable (%s) — will refresh', exc)
        return None


def refresh_condition_policies(cfg: Dict[str, Any]) -> Dict[str, List[Tuple[str, str]]]:
    """Fetch full condition policy table from eBay and write to cache."""
    global _policies_mem_cache, _required_mem_cache
    log.info('fetching eBay condition policies from Metadata API')
    data = ebay_get(cfg, _METADATA_PATH)
    policies: Dict[str, List[Tuple[str, str]]] = {}
    required: Dict[str, bool | None] = {}
    for entry in data.get('itemConditionPolicies', []):
        cat_id = str(entry['categoryId'])
        flag = entry.get('itemConditionRequired')
        required[cat_id] = flag if isinstance(flag, bool) else None
        # Metadata occasionally omits the human description for a valid
        # conditionId.  The identifier is the authority needed for policy
        # enforcement; a missing optional label must not make the entire
        # all-category cache refresh fail (and thereby turn every listing
        # action into HTTP 500).
        conds = [
            (str(c['conditionId']), str(c.get('conditionDescription') or c['conditionId']))
            for c in entry.get('itemConditions', [])
            if isinstance(c, dict) and c.get('conditionId') not in (None, '')
        ]
        # A recognized category can explicitly require no listing condition.
        # Preserve that category with an empty allowed set rather than making
        # it indistinguishable from a cache miss.
        policies[cat_id] = conds

    path = _cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'policies':   {k: list(v) for k, v in policies.items()},
        'item_condition_required': required,
    }, indent=2), encoding='utf-8')

    log.info('condition policies cached: %d categories, %d unique sets',
             len(policies),
             len({frozenset(c[0] for c in v) for v in policies.values()}))
    _policies_mem_cache = policies
    _required_mem_cache = required
    return policies


def condition_policy_for_category(cfg: Dict[str, Any], category_id: str) -> Dict[str, Any]:
    """Return exact local policy evidence; never invent a fallback policy."""
    policies = _get_policies(cfg)
    key = str(category_id)
    allowed = policies.get(key)
    required = _required_mem_cache.get(key)
    return {'recognized': allowed is not None, 'item_condition_required': required,
            'required_flag_valid': isinstance(required, bool),
            'conditions': [_make_result(cid, label) for cid, label in (allowed or [])]}


def condition_policy_census(cfg: Dict[str, Any], expected_sets: int = 26) -> Dict[str, Any]:
    """Read-only coverage/drift census of the cached eBay policy observation."""
    policies = _get_policies(cfg)
    sets = sorted({tuple(sorted({cid for cid, _label in rows})) for rows in policies.values()})
    covered = sum(isinstance(_required_mem_cache.get(key), bool) for key in policies)
    return {'schema': 'tgw-ebay-condition-policy-census/v1', 'category_count': len(policies),
            'required_flag_coverage': covered, 'required_flag_missing_or_invalid': len(policies) - covered,
            'expected_distinct_condition_id_sets': expected_sets, 'actual_distinct_condition_id_sets': len(sets),
            'condition_id_sets': [list(row) for row in sets], 'drift': len(sets) != expected_sets}


def _get_policies(cfg: Dict[str, Any]) -> Dict[str, List[Tuple[str, str]]]:
    """Return policies from cache, refreshing if needed.

    Memoized in-process for the life of the process — an on-demand
    refresh_condition_policies() call updates the in-memory cache too, so it
    is never shadowed by a stale copy held here.
    """
    global _policies_mem_cache
    if _policies_mem_cache is not None:
        return _policies_mem_cache
    cached = _load_cache(cfg)
    if cached is not None:
        _policies_mem_cache = cached
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
    # MAX (worst-case), not MIN: several _ITEM_CONDITION_PREFERRED lists are
    # not rank-ascending (e.g. 'refurbished': ['2500' rank6, '3500' rank5,
    # '2000' rank4]) — MIN would set the same-or-worse floor below the
    # primary entry's own rank, letting step 2 hand out a genuinely better
    # condition than the item's true grade. Same upgrade-risk class as
    # best_condition_for_enum() below (audit#1143 #1178/#1252).
    item_rank = max((CONDITION_RANK.get(cid, 7) for cid in preferred_ids), default=7)

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


# Full set of Inventory API condition enum strings this system knows about,
# derived from CONDITION_ID_TO_ENUM's values (not eBay's per-category subset
# — that additionally narrows by category via allowed_conditions_for_category,
# but this global set is the cheap, always-available first check: a value
# like "Very Good" (a human label, not an enum) fails this regardless of
# category context. See PP-CONDITION-ENUM-001 / todo #1562: draft_listing.
# condition_enum was silently corrupted to a raw label with zero validation,
# bypassing ebay_stage's safe legacy-string fallback and dead-lettering the
# eBay push with an opaque error.
ALL_CONDITION_ENUMS: frozenset = frozenset(CONDITION_ID_TO_ENUM.values())


def is_known_condition_enum(value: Any) -> bool:
    """True if *value* is a real Inventory API condition enum string.

    Global vocabulary check only — does not require a category_id. Used to
    reject obviously-corrupt values (e.g. a human-readable label) before
    they're persisted to draft_listing.condition_enum.
    """
    return isinstance(value, str) and value in ALL_CONDITION_ENUMS



def allowed_conditions_for_category(
    cfg: Dict[str, Any], category_id: str
) -> List[Dict[str, str]]:
    """
    Return all allowed conditions for *category_id* as a list of dicts:
      [{'condition_id': '1000', 'condition_label': 'Brand New',
        'condition_enum': 'NEW'}, ...]
    Returns [] if the category has no policy entry.
    """
    policies = _get_policies(cfg)
    allowed = policies.get(str(category_id), [])
    return [_make_result(cid, desc) for cid, desc in allowed]


def best_condition_for_enum(
    cfg: Dict[str, Any], category_id: str, current_enum: str
) -> Optional[Dict[str, str]]:
    """
    Given a condition_enum already set on a draft (e.g. "USED_EXCELLENT") and a
    (possibly new) category_id, return the best valid condition dict for that
    category, or None if the enum cannot map to any allowed condition.

    Used when category changes: remaps the existing condition to the new
    category's constraint set without requiring the original human-readable
    condition string.  Never upgrades condition quality.
    """
    # Build enum → [conditionIds] reverse map
    enum_to_ids: Dict[str, List[str]] = {}
    for cid, en in CONDITION_ID_TO_ENUM.items():
        enum_to_ids.setdefault(en, []).append(cid)

    source_ids = enum_to_ids.get(current_enum, [])
    if not source_ids:
        return None

    # An enum can be ambiguous (e.g. LIKE_NEW covers both '2750' rank-3 and
    # '2990' rank-6 "Pre-loved Refurbished") and we don't know which real
    # conditionId the item actually had. Assume the worst (MAX rank) so the
    # remap can never upgrade a worse-graded item that happens to share an
    # enum with a better one.
    item_rank = max(CONDITION_RANK.get(cid, 7) for cid in source_ids)

    policies = _get_policies(cfg)
    allowed_list = policies.get(str(category_id), [])
    allowed_map: Dict[str, str] = {cid: desc for cid, desc in allowed_list}

    # 1. Direct hit: only the worst-ranked source conditionId(s) qualify —
    # a better-ranked alias of the same enum is never a safe direct match,
    # since it would be an upgrade relative to the worst-case assumption.
    worst_source_ids = [cid for cid in source_ids if CONDITION_RANK.get(cid, 7) == item_rank]
    for src_id in worst_source_ids:
        if src_id in allowed_map:
            return _make_result(src_id, allowed_map[src_id])

    # 2. Fallback: best allowed condition at same or worse rank
    candidates = [
        (CONDITION_RANK.get(cid, 7), cid, desc)
        for cid, desc in allowed_list
        if CONDITION_RANK.get(cid, 7) >= item_rank
    ]
    if candidates:
        candidates.sort()
        _, best_id, best_desc = candidates[0]
        log.info('condition remap for enum %r in category %s: %s (%s)',
                 current_enum, category_id, best_id, best_desc)
        return _make_result(best_id, best_desc)

    log.warning('no valid condition remap for enum %r in category %s (all allowed are better)',
                current_enum, category_id)
    return None

def _make_result(condition_id: str, condition_label: str) -> Dict[str, str]:
    return {
        'condition_id':    condition_id,
        'condition_label': condition_label,
        'condition_enum':  condition_enum(condition_id),
    }
