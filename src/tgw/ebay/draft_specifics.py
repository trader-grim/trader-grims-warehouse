"""
tgw.ebay.draft_specifics — Set B ("eBay Draft") accessor module.

=============================================================================
TWO-SET RULE (todo #1418, PP-LISTEDITOR-001, foundation for #1416/#1417)
=============================================================================
See `tgw.inventory_record`'s banner for the full two-set explanation. In
short:

  Set A — INVENTORY RECORD (`tgw.inventory_record`)
    `item_attributes` — universal, marketplace-agnostic facts.

  Set B — EBAY DRAFT  (this module)
    `draft_listing.item_specifics` — the eBay-specific, category-mapped
    aspect values. `ebay.sync._build_offer_bodies` reads ONLY this set for
    the actual eBay Inventory API push (`product.aspects`) — this is the
    ONE set that ever reaches eBay's servers.

THIS MODULE (`tgw.ebay.draft_specifics`) IS THE ONLY SANCTIONED DIRECT-
DICT-ACCESS POINT for `draft_listing.item_specifics`. Any code that reads
or writes `draft_listing["item_specifics"][...]` directly outside this
file is the exact bug class C12 exists to catch. Cross-set moves (Set A ->
Set B translation, or the reverse-flow diff) belong in #1416's translation
function / #1417's diff-apply function, built ON TOP of the accessors
below — never a per-key merge or `{**a, **b}` spread performed locally.
=============================================================================

Envelope shape, nested to match where `item_specifics` itself lives
(inside `draft_listing`):

    "draft_listing": {
        ...,
        "item_specifics": {
            "_set": "ebay_draft",
            "version": 1,
            "updated_at": "2026-07-15T12:00:00+00:00",
            "updated_at_backfilled": false,
            "fields": {"Type": "Brooch", ...}
        },
        ...
    }

Provenance history — nested alongside `item_specifics`, same append-only
discipline as `price_history` / `tgw.inventory_record`'s
`item_attributes_history`:

    "draft_listing": {
        ...,
        "item_specifics_history": [
            {"ts": ..., "key": "Type", "value": "Brooch",
             "previous_value": "Lapel Pin", "source": "ebay_draft",
             "applied_by": "system"},
            ...
        ]
    }

Back-compat: pre-migration items still carry `item_specifics` as a bare
`{name: value}` dict with no `_set` tag — all getters below accept both
shapes transparently. The full-catalog migration is a separate, explicit
decision (see the packet doc); this module must work correctly against
both shapes for as long as that transition takes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SET_TAG = "ebay_draft"
ENVELOPE_VERSION = 1

__all__ = [
    "SET_TAG",
    "ENVELOPE_VERSION",
    "is_envelope",
    "get_ebay_aspects",
    "get_ebay_aspect",
    "wrap_ebay_specifics",
    "set_ebay_aspects",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_envelope(raw: Any) -> bool:
    """True if `raw` is already a Set B envelope (post-migration shape)."""
    return isinstance(raw, dict) and raw.get("_set") == SET_TAG


def get_ebay_aspects(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return the Set B fields dict — the ONLY sanctioned read of
    `item["draft_listing"]["item_specifics"]`'s contents. Handles both the
    enveloped (post-migration) and bare-dict (pre-migration / fixture)
    shapes transparently.
    """
    dl = item.get("draft_listing") or {}
    if not isinstance(dl, dict):
        return {}
    raw = dl.get("item_specifics") or {}
    if not isinstance(raw, dict):
        return {}
    if is_envelope(raw):
        fields = raw.get("fields")
        return dict(fields) if isinstance(fields, dict) else {}
    # Legacy bare-dict shape — pre-migration item.
    return dict(raw)


def get_ebay_aspect(item: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Return one Set B aspect value, or `default` if absent."""
    return get_ebay_aspects(item).get(key, default)


def wrap_ebay_specifics(
    fields: Dict[str, Any],
    *,
    updated_at: Optional[str] = None,
    backfilled: bool = False,
) -> Dict[str, Any]:
    """Build a full Set B envelope from a plain fields dict.

    `backfilled=True` marks `updated_at` as a migration-time guess rather
    than a real edit timestamp (Prime Directive 1: never claim false
    precision).
    """
    return {
        "_set": SET_TAG,
        "version": ENVELOPE_VERSION,
        "updated_at": updated_at or _now_iso(),
        "updated_at_backfilled": bool(backfilled),
        "fields": dict(fields),
    }


def set_ebay_aspects(
    item: Dict[str, Any],
    updates: Dict[str, Any],
    *,
    source: str,
    applied_by: str = "system",
) -> Dict[str, Any]:
    """Compute the patch fields for writing `updates` into Set B.

    Pure — does not mutate `item` or perform I/O. Returns:
      {"item_specifics": <full new envelope>,
       "item_specifics_history": <full new history list, append-only>}
    A caller merges these two keys into its own `draft_listing` patch
    dict (they live nested inside `draft_listing`, unlike Set A's
    top-level keys).

    Same "only append on a real change" / "None never deletes" discipline
    as `tgw.inventory_record.set_inventory_fields`.
    """
    existing_fields = get_ebay_aspects(item)
    dl = item.get("draft_listing") or {}
    history: List[Dict[str, Any]] = list((dl.get("item_specifics_history") or [])
                                          if isinstance(dl, dict) else [])
    ts = _now_iso()
    new_fields = dict(existing_fields)
    for key, value in updates.items():
        if value is None:
            continue
        previous = existing_fields.get(key)
        if str(previous) == str(value):
            continue
        new_fields[key] = value
        history.append({
            "ts": ts,
            "key": key,
            "value": value,
            "previous_value": previous,
            "source": source,
            "applied_by": applied_by,
        })
    return {
        "item_specifics": wrap_ebay_specifics(new_fields, updated_at=ts),
        "item_specifics_history": history,
    }
