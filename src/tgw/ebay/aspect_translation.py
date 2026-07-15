"""
tgw.ebay.aspect_translation — the ONE named Set A -> Set B translation
function (todo #1416, PP-LISTEDITOR-001, built on #1418's accessor
modules).

=============================================================================
WHY THIS MODULE EXISTS (see reference/invariants.md C12 for the full rule)
=============================================================================
`item_attributes` (Set A — Inventory Record, `tgw.inventory_record`) and
`draft_listing.item_specifics` (Set B — eBay Draft,
`tgw.ebay.draft_specifics`) are deliberately separate field-sets. Moving
data from Set A to Set B is a translation, not a merge — it must respect
the target eBay category's allowed-values/required-aspects, and it must
happen in exactly one place so every caller gets the same behavior.

Before this module, the translation logic lived inline inside
`workers/ebay_draft.py`'s "Phase 2b" prefill block (extracted here
verbatim, no behavior change — see #1416's result manifest for confirmed
before/after equivalence). `http_server.py`'s `saveEbayDraft()` and
`accept_proposals` never used this translation at all; they wrote
straight into the wrong set (the bug this packet fixes). Any FUTURE code
path that needs to move a value from Set A to Set B calls
`translate_inventory_to_ebay_draft()` below — never a new inline prefill
or `{**a, **b}` spread.
=============================================================================
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from tgw.apis.ebay.specifics import get_aspects

__all__ = ["translate_inventory_to_ebay_draft"]


def translate_inventory_to_ebay_draft(
    item_attributes: Dict[str, Any],
    category_id: str,
    cfg,
    *,
    aspects: Optional[List[Dict[str, Any]]] = None,
    already_filled: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Translate Set A fields into the Set B (`item_specifics`) values they
    map to for `category_id`.

    Args:
      item_attributes: the Set A fields dict (obtained via
        `tgw.inventory_record.get_inventory_fields(item)` — this function
        takes the plain fields dict, not the item document or envelope).
      category_id: target eBay leaf category ID. Category '99' (the
        non-leaf catch-all) has no aspect definitions to validate
        against — returns {} immediately, matching `ebay_draft.py`'s
        existing skip-aspects-for-99 behavior.
      cfg: TGW config, used only as a fallback to fetch aspect definitions
        via the existing category-context lookup
        (`tgw.apis.ebay.specifics.get_aspects`) when `aspects` isn't
        already supplied — reused, not reinvented, per the packet spec.
      aspects: optional pre-fetched aspect definitions for `category_id`
        (same shape `get_aspects()` returns). Callers that already fetched
        the category's aspects (e.g. `ebay_draft.py`, which needs them for
        several other phases too) should pass them here to avoid a
        redundant lookup — this is the normal path. Only fetched internally
        via `get_aspects(cfg, category_id)` if omitted.
      already_filled: optional dict of aspect names already resolved by a
        higher-priority source (e.g. `product_lookup`) — those aspect
        names are left untouched, matching `ebay_draft.py`'s existing
        priority order (product_lookup > item_attributes).

    Returns:
      A plain `{aspect_name: value}` dict — the Set B fields this
      Set A snapshot maps to. Callers wrap it into the Set B envelope via
      `tgw.ebay.draft_specifics.set_ebay_aspects` /
      `wrap_ebay_specifics` before writing it back to an item, or merge it
      into a larger `item_specifics` build (as `ebay_draft.py` does,
      layering AI-vision-filled values underneath).

    Only maps a Set A key onto a Set B aspect when:
      - the eBay category actually defines an aspect with that exact name
        (no fuzzy matching — same behavior as the pre-extraction inline
        version), and
      - the value is non-empty, and
      - for SELECTION_ONLY aspects with a closed allowed_values list, the
        value is one of the allowed values (values outside the list are
        silently skipped, not force-coerced — same as before extraction).
    """
    if category_id == '99':
        return {}
    already = set((already_filled or {}).keys())
    if aspects is None:
        aspects = get_aspects(cfg, category_id)
    aspect_by_name = {a['name']: a for a in aspects}

    result: Dict[str, str] = {}
    for attr_name, attr_val in item_attributes.items():
        if not attr_val or attr_name in already:
            continue
        aspect_def = aspect_by_name.get(attr_name)
        if aspect_def is None:
            continue
        val = str(attr_val).strip()
        if not val:
            continue
        if aspect_def['mode'] == 'SELECTION_ONLY' and aspect_def['allowed_values']:
            if val not in aspect_def['allowed_values']:
                continue
        result[attr_name] = val
    return result
