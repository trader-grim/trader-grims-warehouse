"""tgw.ebay.inventory_diff — the reverse-flow (Set B -> Set A) diff engine
and gated apply function (todo #1417, PP-LISTEDITOR-001, built on #1418's
accessor modules and #1416's Set A/Set B boundary fix).

=============================================================================
WHY THIS MODULE EXISTS (see reference/invariants.md C12/C13)
=============================================================================
`tgw.ebay.aspect_translation` (todo #1416) is the one named FORWARD
(Set A -> Set B) translation function. This module is its REVERSE
counterpart: eBay-draft-discovered values (Set B, `draft_listing.
item_specifics` — e.g. an AI-vision-resolved "Brooch") flowing back into
the universal inventory record (Set A, `item_attributes`) as a proposed,
operator-reviewed correction — never a silent overwrite (Dave, 2026-07-15:
"gated automatic update", checked-diff pattern, no confidence-threshold
auto-promotion).

Both directions are DELIBERATELY separate functions/code paths (spec point
6): this module never touches `accept_proposals`/`revision_draft`, and
`aspect_translation.py` never touches `item_attributes`. Built ON TOP of
the sanctioned accessors (`tgw.inventory_record`, `tgw.ebay.
draft_specifics`) — never a per-key merge or `{**a, **b}` spread performed
locally.
=============================================================================
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from tgw import inventory_record
from tgw.ebay.draft_specifics import (
    get_ebay_aspects,
    get_ebay_aspects_history,
    get_ebay_aspects_updated_at,
)

__all__ = ["diff_ebay_draft_to_inventory", "apply_inventory_diff"]


def _latest_source_and_ts(history: List[Dict[str, Any]], key: str) -> "tuple[Optional[str], Optional[str]]":
    """Scan the (append-only, chronological) Set B history for the most
    recent entry touching `key`. Returns (source, ts) or (None, None) if
    the key never appears (legacy item, or the current value was set
    before history-tracking existed)."""
    source = None
    ts = None
    for entry in history:
        if entry.get("key") == key:
            source = entry.get("source")
            ts = entry.get("ts")
    return source, ts


def diff_ebay_draft_to_inventory(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Compare Set B (`draft_listing.item_specifics`, post-#1416 =
    trustworthy) against Set A (`item_attributes`) key-by-key.

    Returns one FieldDiff dict per differing key:
        {"key": str, "inventory_value": Any | None, "ebay_value": Any,
         "source": str, "detected_at": str | None}

    - Keys present in Set B but absent from Set A ARE a diff
      (inventory_value=None) — a new fact, not just a correction.
    - Keys present only in Set A are NOT part of this diff — Set A can
      legitimately hold universal facts no marketplace needs; that's not
      a discrepancy to resolve (spec point 1).
    - `source` defaults to 'ebay_draft' (the only current writer of Set B
      envelopes via the sanctioned accessor) when no history entry names
      one — honest best-guess, not a fabricated provenance chain.
    - `detected_at` is None when neither a matching history entry nor an
      envelope `updated_at` exists (legacy bare-dict item, no timestamp
      information available) — Prime Directive 1: never claim false
      precision about when a value was actually discovered.

    Pure — no I/O, no mutation.
    """
    inv_fields = inventory_record.get_inventory_fields(item)
    ebay_fields = get_ebay_aspects(item)
    history = get_ebay_aspects_history(item)
    envelope_updated_at = get_ebay_aspects_updated_at(item)

    diffs: List[Dict[str, Any]] = []
    for key in sorted(ebay_fields):
        ebay_value = ebay_fields[key]
        has_inv = key in inv_fields
        inv_value = inv_fields.get(key)
        if has_inv and str(inv_value) == str(ebay_value):
            continue
        source, ts = _latest_source_and_ts(history, key)
        diffs.append({
            "key": key,
            "inventory_value": inv_value if has_inv else None,
            "ebay_value": ebay_value,
            "source": source or "ebay_draft",
            "detected_at": ts or envelope_updated_at,
        })
    return diffs


def apply_inventory_diff(
    item: Dict[str, Any],
    keys: Iterable[str],
    *,
    applied_by: str = "operator",
) -> Dict[str, Any]:
    """Compute the patch fields for writing the CHECKED subset of the
    eBay->Inventory diff into Set A (`item_attributes`).

    Pure — does not mutate `item` or perform I/O; returns the same
    `{"item_attributes": ..., "item_attributes_history": ...}` shape
    `tgw.inventory_record.set_inventory_fields` returns, ready for a
    caller to fence-PATCH/merge (spec point 4: this is its own explicit,
    named write path into Set A — never a generic PATCH passthrough).

    Re-diffs LIVE against `item` rather than trusting caller-supplied
    values (spec point 5's idempotency requirement, and defense against a
    stale UI submitting a value that's no longer the current eBay-draft
    value): a requested key that is no longer an active diff at call time
    (Set A/Set B already agree, or the key no longer exists in Set B) is
    silently skipped — same "diff is always recomputed, nothing sticky"
    reasoning as the read endpoint. See this packet's result manifest for
    the explicit sticky-vs-resurface design confirmation (spec point 5).

    Multiple diffs may carry different `source` values (e.g. one key last
    touched by 'ebay_draft', another by 'accept_proposals') — grouped and
    applied via separate `set_inventory_fields` calls so each history
    entry keeps its own accurate source, then each newly-appended history
    entry is annotated with the diff's `detected_at` (the extra provenance
    field spec point 4 asks for beyond what `set_inventory_fields` already
    records: `ts`/`applied_by`/`source`/`previous_value`).
    """
    diffs = diff_ebay_draft_to_inventory(item)
    keys_wanted = set(keys)
    applicable = [d for d in diffs if d["key"] in keys_wanted]
    if not applicable:
        return {}

    by_source: Dict[str, List[Dict[str, Any]]] = {}
    detected_at_by_key: Dict[str, Optional[str]] = {}
    for d in applicable:
        by_source.setdefault(d["source"], []).append(d)
        detected_at_by_key[d["key"]] = d["detected_at"]

    working_item: Dict[str, Any] = dict(item)
    for source, group in by_source.items():
        updates = {d["key"]: d["ebay_value"] for d in group}
        before_len = len(working_item.get("item_attributes_history") or [])
        patch = inventory_record.set_inventory_fields(
            working_item, updates, source=source, applied_by=applied_by)
        new_history = patch["item_attributes_history"]
        for i in range(before_len, len(new_history)):
            entry = new_history[i]
            if entry.get("key") in detected_at_by_key:
                entry["detected_at"] = detected_at_by_key[entry["key"]]
        # (a) accessor patch output moving onward into the next iteration's
        # input / the final return value — same category as
        # http_server.py's existing allowlisted accessor-output-forwarding
        # hits (see tests/test_invariant_c12_field_set_accessors.py).
        working_item["item_attributes"] = patch["item_attributes"]  # noqa: allowlisted, C12 category (a)
        working_item["item_attributes_history"] = new_history

    return {
        "item_attributes": working_item["item_attributes"],  # noqa: allowlisted, C12 category (a)
        "item_attributes_history": working_item["item_attributes_history"],
        "applied_keys": sorted(detected_at_by_key),
    }
