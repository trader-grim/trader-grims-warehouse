"""
tgw.inventory_record — Set A ("Inventory Record") accessor module.

=============================================================================
TWO-SET RULE (todo #1418, PP-LISTEDITOR-001, foundation for #1416/#1417)
=============================================================================
Item JSON carries TWO distinct, deliberately-separate field-sets that look
superficially similar (both are "a dict of aspect-like facts") but must
NEVER be treated as one:

  Set A — INVENTORY RECORD  (this module)
    `item_attributes` — universal, marketplace-agnostic facts (Type, Brand,
    Metal, Department, ...). Meant to translate across eBay and any future
    marketplace. Edited carefully; NOT what gets pushed to eBay directly.

  Set B — EBAY DRAFT  (`tgw.ebay.draft_specifics`)
    `draft_listing.item_specifics` — the eBay-specific, category-mapped
    aspect values actually pushed to eBay's Inventory API
    (`sync.py:_build_offer_bodies` reads ONLY this set for the live push).

Dave, 2026-07-15: "The problem is you are considering keys individually...
They are sets of data. If you don't look at it that way you will keep
mixing them up." Confirmed live: two prior sessions (#1291, #1313/#1316)
each fixed a real bug in this territory without ever noticing the
set-boundary problem underneath, because the old bare-dict shape had no
self-identifying marker. See `reference/invariants.md` C12 and this
packet's doc (`docs/TGW-Plan-Vault/plan/packets/1418-field-set-schema-foundation.md`)
for the full "why."

THIS MODULE (`tgw.inventory_record`) IS THE ONLY SANCTIONED DIRECT-DICT-
ACCESS POINT for `item_attributes`. Any code that reads or writes a Set A
key directly (`item["item_attributes"][...]`, `item.get("item_attributes")`
outside this file) is the exact bug class C12 exists to catch. Cross-set
moves (Set A -> Set B or back) belong in #1416's translation function /
#1417's diff-apply function, built ON TOP of the accessors below — never a
per-key merge or `{**a, **b}` spread performed locally.
=============================================================================

Envelope shape (self-describing, per grep-discoverable in raw JSON):

    "item_attributes": {
        "_set": "inventory_record",
        "version": 1,
        "updated_at": "2026-07-15T12:00:00+00:00",
        "updated_at_backfilled": false,   # true only for migrated items
                                           # whose real edit time is unknown
                                           # (Prime Directive 1: never claim
                                           # false precision)
        "fields": {"Type": "Brooch", "Brand": "Unbranded", ...},
        "locked_keys": ["Type"]            # optional; keys NOT auto-synced
                                            # from eBay draft edits. Absent
                                            # or [] means "everything follows
                                            # eBay" (Dave, 2026-07-18): most
                                            # item_attributes content starts
                                            # as an unverified ai_identify
                                            # guess, not an operator-confirmed
                                            # fact, so the safe default is
                                            # "keep following the source that
                                            # actually gets corrected day to
                                            # day" — an operator locks a key
                                            # only when they deliberately want
                                            # the inventory record to diverge
                                            # from what's on the live listing.
    }

Provenance history — append-only, never edited or truncated (matches
`price_history`'s existing discipline, `http_server.py` session-42):

    "item_attributes_history": [
        {"ts": ..., "key": "Type", "value": "Brooch",
         "previous_value": "Lapel Pin", "source": "ai_identify",
         "applied_by": "system"},
        ...
    ]

Precedent: this is the third application of the "cheap current value +
append-only history array" shape in this codebase, not a new invention —
`price_history` (session 42) and `vision_results`/`alt_text_results` (raw
AI-call preservation) are the first two.

Back-compat note: pre-migration items (and any test fixture) still carry
`item_attributes` as a bare `{key: value}` dict with no `_set` tag. All
getters below transparently accept both shapes. The full 55k-item catalog
migration is a SEPARATE, explicit go/no-go decision (see the packet doc) —
it is not bundled into this module landing, so both shapes must coexist
correctly for a long transition period.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SET_TAG = "inventory_record"
ENVELOPE_VERSION = 1

__all__ = [
    "SET_TAG",
    "ENVELOPE_VERSION",
    "is_envelope",
    "get_inventory_fields",
    "get_inventory_field",
    "wrap_inventory_attributes",
    "set_inventory_fields",
    "ensure_required_category_fields",
    "get_locked_keys",
    "is_locked",
    "set_locked",
    "sync_from_draft",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_envelope(raw: Any) -> bool:
    """True if `raw` is already a Set A envelope (post-migration shape)."""
    return isinstance(raw, dict) and raw.get("_set") == SET_TAG


def get_inventory_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return the Set A fields dict — the ONLY sanctioned read of
    `item["item_attributes"]`'s contents. Handles both the enveloped
    (post-migration) and bare-dict (pre-migration / fixture) shapes
    transparently.
    """
    raw = item.get("item_attributes") or {}
    if not isinstance(raw, dict):
        return {}
    if is_envelope(raw):
        fields = raw.get("fields")
        return dict(fields) if isinstance(fields, dict) else {}
    # Legacy bare-dict shape — pre-migration item.
    return dict(raw)


def get_inventory_field(item: Dict[str, Any], key: str, default: Any = None) -> Any:
    """Return one Set A field value, or `default` if absent."""
    return get_inventory_fields(item).get(key, default)


def wrap_inventory_attributes(
    fields: Dict[str, Any],
    *,
    updated_at: Optional[str] = None,
    backfilled: bool = False,
    locked_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build a full Set A envelope from a plain fields dict.

    `backfilled=True` marks `updated_at` as a migration-time guess rather
    than a real edit timestamp — Prime Directive 1: never claim false
    precision about when the data actually changed.

    `locked_keys` (Dave, 2026-07-18): keys excluded from auto-sync — see
    module docstring's envelope shape note. ALWAYS included (even as `[]`)
    despite this module's usual convention of omitting empty/default
    scaffolding — _apply_patch shallow-merges an already-enveloped
    item_attributes onto the existing one rather than replacing it, so an
    omitted key here would leave a stale prior value on disk instead of
    clearing it (caught live by test_inventory_lock_toggle_endpoint: unlock
    silently no-opped before this was unconditional).
    """
    return {
        "_set": SET_TAG,
        "version": ENVELOPE_VERSION,
        "updated_at": updated_at or _now_iso(),
        "updated_at_backfilled": bool(backfilled),
        "fields": dict(fields),
        "locked_keys": list(locked_keys) if locked_keys else [],
    }


def get_locked_keys(item: Dict[str, Any]) -> List[str]:
    """Return the list of Set A keys excluded from eBay-draft auto-sync."""
    raw = item.get("item_attributes") or {}
    if not is_envelope(raw):
        return []
    keys = raw.get("locked_keys")
    return list(keys) if isinstance(keys, list) else []


def is_locked(item: Dict[str, Any], key: str) -> bool:
    """True if `key` is excluded from eBay-draft auto-sync."""
    return key in get_locked_keys(item)


def set_locked(item: Dict[str, Any], key: str, locked: bool) -> Dict[str, Any]:
    """Compute the patch for locking/unlocking one Set A key.

    Pure — like `set_inventory_fields`, returns {"item_attributes": <new
    envelope>} for the caller to fence-PATCH. Does not touch field values
    or history — locking is metadata about future sync behavior, not a
    fact about the item, so it never appends to item_attributes_history.
    """
    current = set(get_locked_keys(item))
    if locked:
        current.add(key)
    else:
        current.discard(key)
    fields = get_inventory_fields(item)
    raw = item.get("item_attributes") or {}
    updated_at = raw.get("updated_at") if is_envelope(raw) else None
    backfilled = bool(raw.get("updated_at_backfilled")) if is_envelope(raw) else False
    return {
        "item_attributes": wrap_inventory_attributes(
            fields, updated_at=updated_at, backfilled=backfilled,
            locked_keys=sorted(current),
        )
    }


def sync_from_draft(
    item: Dict[str, Any],
    draft_fields: Dict[str, Any],
    *,
    source: str,
    applied_by: str = "operator",
) -> Dict[str, Any]:
    """Auto-sync eBay-draft-sourced values into Set A for every key NOT
    currently locked (Dave, 2026-07-18 — the padlock design: default is
    'inventory record follows the eBay draft', an explicit lock is the
    only way to make a key stop following).

    `draft_fields` is whatever the caller considers the current eBay-side
    truth for keys that also belong in Set A (e.g. {"Title": <draft
    title>, **item_specifics}) — this function does not know or care
    where they came from, same separation of concerns as
    `set_inventory_fields`.

    Pure. Returns the same shape as `set_inventory_fields` (empty patch —
    i.e. identical to the item's current fields/history — if nothing
    changed or everything relevant is locked).
    """
    locked = set(get_locked_keys(item))
    updates = {k: v for k, v in draft_fields.items() if k not in locked}
    return set_inventory_fields(item, updates, source=source, applied_by=applied_by)


def set_inventory_fields(
    item: Dict[str, Any],
    updates: Dict[str, Any],
    *,
    source: str,
    applied_by: str = "system",
) -> Dict[str, Any]:
    """Compute the patch fields for writing `updates` into Set A.

    Pure — does not mutate `item` or perform I/O. Returns a dict with the
    two keys a caller should fence-PATCH / merge together:
      {"item_attributes": <full new envelope>,
       "item_attributes_history": <full new history list, append-only>}

    Only keys whose value actually changes get a history entry (matches
    `price_history`'s "only append on a real change" discipline). A value
    of None in `updates` is treated as "no-op" (never used to delete a Set
    A key through this path) — Set A deletions, if ever needed, are a
    separate, explicit decision, not a side effect of a generic update.
    """
    existing_fields = get_inventory_fields(item)
    history: List[Dict[str, Any]] = list(item.get("item_attributes_history") or [])
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
        "item_attributes": wrap_inventory_attributes(
            new_fields, updated_at=ts, locked_keys=get_locked_keys(item)),
        "item_attributes_history": history,
    }


def ensure_required_category_fields(
    item: Dict[str, Any], aspects: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Scaffold missing required marketplace aspects as visible Set A fields.

    A missing required aspect is meaningful operator work, not an absent UI
    control.  This function adds only absent keys with an empty value; it never
    overwrites an existing value, including an intentional existing empty value.
    The normal Set A envelope and append-only history are used so the scaffold
    has the same generation/CAS behavior as every other inventory change.
    """
    existing = get_inventory_fields(item)
    missing = {
        str(aspect["name"]): ""
        for aspect in aspects
        if aspect.get("required") and str(aspect.get("name") or "").strip()
        and str(aspect["name"]) not in existing
    }
    if not missing:
        return {}
    return set_inventory_fields(
        item,
        missing,
        source="ebay_category_schema",
        applied_by="system",
    )
