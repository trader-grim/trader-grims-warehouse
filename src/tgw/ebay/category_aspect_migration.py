"""tgw.ebay.category_aspect_migration — category-change aspect migration
(todo #1471, PP-LISTEDITOR-001, invariant C14 lineage).

=============================================================================
WHY THIS MODULE EXISTS
=============================================================================
eBay's own Seller Hub discards item specifics that don't belong to a
listing's category when the category changes. TGW's push
(`tgw.ebay.sync._build_offer_bodies`) doesn't discard anything — it sends
every stored `draft_listing.item_specifics` field regardless of whether
the item's CURRENT category still recognizes it as a valid aspect. That
gap is a confirmed live incident (2026-07-16): a category change stranded
18 of 20 real, live-pushed aspects, invisible in the item-detail editor
because the aspects form only ever rendered inputs for the category's
official list (fixed separately, todo #1470, which also added visibility
+ an explicit "add custom aspect" affordance for the ongoing case).

This module is the OTHER half — matching eBay's own discard-on-category-
change behavior as TGW's *default*, without ever deleting the underlying
data (Prime Directive 1). Dave, 2026-07-16: "I always wanted the
attributes to move. They are good seo... operator chooses discards and
makes their own mess to repair if they screw up." Confirmed destination:
`item_attributes` (Set A, the universal inventory record) — reusing
`tgw.ebay.inventory_diff`'s exact pre-checked-by-default, operator-can-
uncheck-to-keep-on-eBay-instead review pattern (C13), not inventing a new
convention. Built ON TOP of the sanctioned accessors
(`tgw.inventory_record`, `tgw.ebay.draft_specifics`) — never a per-key
merge or `{**a, **b}` spread performed locally.
=============================================================================
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from tgw import inventory_record
from tgw.apis.ebay.specifics import get_aspects
from tgw.ebay.draft_specifics import get_ebay_aspects, remove_ebay_aspects

__all__ = ["detect_category_orphaned_aspects", "apply_category_aspect_migration"]


def detect_category_orphaned_aspects(cfg: Dict[str, Any], item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stored Set B aspect keys that are NOT part of the item's CURRENT
    category's official eBay aspect list.

    Live-recomputed every call — no stored/sticky state, same idempotency
    reasoning as `tgw.ebay.inventory_diff.diff_ebay_draft_to_inventory`:
    if the category changes again, or an aspect is edited out, the next
    call reflects that automatically.

    Returns one dict per orphaned key: {"key": str, "value": Any},
    sorted by key. Empty list (never an error) when: no category set yet,
    the category's aspect lookup fails (rate-limited/offline — Prime
    Directive 1: never propose discarding real data on a lookup error,
    fail toward "nothing orphaned" instead), or nothing is actually
    orphaned.
    """
    category_id = str((item.get("draft_listing") or {}).get("category_id") or "")
    if not category_id:
        return []
    try:
        official = {a["name"] for a in get_aspects(cfg, category_id)}
    except Exception:
        return []
    fields = get_ebay_aspects(item)
    return [{"key": k, "value": fields[k]} for k in sorted(fields) if k not in official]


def apply_category_aspect_migration(
    item: Dict[str, Any],
    keys: Iterable[str],
    *,
    cfg: Dict[str, Any],
    applied_by: str = "operator",
) -> Dict[str, Any]:
    """Move the CHECKED subset of category-orphaned Set B aspects into Set
    A, removing them from Set B — matching eBay's own discard-on-category-
    change behavior for the live push, while preserving the data (Prime
    Directive 1).

    Re-detects LIVE against `item` rather than trusting caller-supplied
    keys (same re-diffing discipline as `apply_inventory_diff`) — a
    requested key that is no longer orphaned at call time (category
    changed again, the field was already removed) is silently skipped.

    Pure — does not mutate `item` or perform I/O. Returns `{}` if nothing
    in `keys` is currently an applicable orphan. Otherwise:
      {"item_attributes": <full new Set A envelope>,
       "item_attributes_history": <...>,
       "draft_listing": {"item_specifics": <full new Set B envelope>,
                          "item_specifics_history": <...>},
       "migrated_keys": [sorted list of keys actually moved]}
    Ready for a caller to fence-PATCH/merge — this is its own explicit,
    named write path for both sets, never a generic PATCH passthrough
    (invariant C12).
    """
    orphaned = detect_category_orphaned_aspects(cfg, item)
    keys_wanted = set(keys)
    applicable = [o for o in orphaned if o["key"] in keys_wanted]
    if not applicable:
        return {}

    updates = {o["key"]: o["value"] for o in applicable}

    inv_patch = inventory_record.set_inventory_fields(
        item, updates, source="category_aspect_migration", applied_by=applied_by)

    # (a) accessor patch output moving onward — same category as
    # inventory_diff.py's own allowlisted equivalent; remove_ebay_aspects
    # below needs the item's Set A write already reflected so a caller
    # inspecting `working_item` mid-migration sees a consistent state.
    working_item = dict(item)
    working_item["item_attributes"] = inv_patch["item_attributes"]  # noqa: allowlisted, C12 category (a)

    ebay_patch = remove_ebay_aspects(
        working_item, sorted(updates), source="category_aspect_migration",
        applied_by=applied_by)

    return {
        "item_attributes": inv_patch["item_attributes"],  # noqa: allowlisted, C12 category (a)
        "item_attributes_history": inv_patch["item_attributes_history"],
        "draft_listing": {
            "item_specifics": ebay_patch["item_specifics"],
            "item_specifics_history": ebay_patch["item_specifics_history"],
        },
        "migrated_keys": sorted(updates),
    }
