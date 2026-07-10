"""
tgw.revision — Live listing revision (PP-REVISION-001).

Slice 1 — dry-run delta computer: writes revision_draft to item JSON and shows
a diff vs the live-mirror (ebay_listing block).  No eBay API calls.

Slice 2 — apply path: reads the stored revision_draft, checks drift against the
pinned baseline, composes the full revised state, and (when enabled) submits to
eBay.  Default is dry-run; live writes are gated behind _APPLY_ENABLED.

revision_draft schema:
    {
        "delta":    {"field": new_value, ...},   # sparse: only assigned fields
        "baseline": {
            "hash":     "sha256_prefix_16hex",   # for fast drift fingerprint
            "snapshot": { <ebay_listing block> } # live-mirror at draft creation
        },
        "created_at": "ISO8601",
        "by":       "agent-or-operator"
    }
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from tgw.config import sku_json
from tgw.items import atomic_write_json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_assignments(assignments: List[str]) -> Dict[str, Any]:
    """Parse ['field=value', 'x.y=42'] → {'field': 'value', 'x.y': 42}.

    Values are parsed as JSON scalars first (numbers, true/false/null) and
    fall back to a plain string.  Splits only on the first '=' so values
    containing '=' are preserved intact.
    """
    result: Dict[str, Any] = {}
    for a in assignments:
        try:
            eq = a.index("=")
        except ValueError:
            raise ValueError(f"--set requires FIELD=VALUE form, got: {a!r}")
        key = a[:eq].strip()
        if not key:
            raise ValueError(f"empty field name in --set: {a!r}")
        raw_val = a[eq + 1:]
        try:
            val: Any = json.loads(raw_val)
        except (json.JSONDecodeError, ValueError):
            val = raw_val
        result[key] = val
    return result


def _get_nested(obj: Any, dotted_path: str) -> Any:
    """Traverse a dotted path into a nested dict; return None on miss."""
    val = obj
    for part in dotted_path.split("."):
        if not isinstance(val, dict):
            return None
        val = val.get(part)
    return val


def live_mirror(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return the live-mirror block (ebay_listing) from the item, or {}."""
    return dict(item.get("ebay_listing") or {})


def baseline_hash(snapshot: Dict[str, Any]) -> str:
    """16-char hex prefix of SHA-256 over the canonical JSON of the snapshot."""
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def detect_drift(
    current_mirror: Dict[str, Any],
    pinned_snapshot: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Return list of fields that differ between current mirror and pinned baseline.

    Each entry: {"field", "baseline", "current", "changed_to_missing"}
    Covers all keys present in either dict.
    """
    all_fields = sorted(set(pinned_snapshot) | set(current_mirror))
    drifted = []
    for field in all_fields:
        base_val = pinned_snapshot.get(field)
        curr_val = current_mirror.get(field)
        if base_val != curr_val:
            drifted.append({
                "field": field,
                "baseline": base_val,
                "current": curr_val,
            })
    return drifted


# ---------------------------------------------------------------------------
# Diff display
# ---------------------------------------------------------------------------

def format_diff(
    item: Dict[str, Any],
    delta: Dict[str, Any],
    mirror: Dict[str, Any],
    drift: List[Dict[str, Any]],
) -> List[str]:
    """Return a list of human-readable diff lines for display.

    Lines describe the proposed delta (current item value → new value) and
    any live-mirror drift detected vs the pinned baseline.
    """
    lines: List[str] = []
    lines.append("=== revision delta ===")
    for field, new_val in delta.items():
        current = _get_nested(item, field)
        lines.append(f"  {field}:")
        if current is None and not _field_exists(item, field):
            lines.append(f"    (new field)  →  {new_val!r}")
        else:
            lines.append(f"    was:  {current!r}")
            lines.append(f"    now:  {new_val!r}")
        # Also show the live-mirror value for this field if available
        mirror_val = mirror.get(field)
        if mirror_val is not None:
            lines.append(f"    live: {mirror_val!r}")

    if drift:
        lines.append("")
        lines.append("=== drift detected (live mirror changed since baseline was pinned) ===")
        for d in drift:
            lines.append(f"  {d['field']}: baseline={d['baseline']!r}  current={d['current']!r}")
    elif mirror:
        lines.append("")
        lines.append("=== live mirror: no drift (baseline matches current) ===")
    else:
        lines.append("")
        lines.append("=== live mirror: not available (item not yet published or synced) ===")

    return lines


def _field_exists(item: Dict[str, Any], dotted_path: str) -> bool:
    """Return True if the dotted path leads to an existing key (even if value is None)."""
    parts = dotted_path.split(".")
    val = item
    for i, part in enumerate(parts):
        if not isinstance(val, dict):
            return False
        if part not in val:
            return False
        if i == len(parts) - 1:
            return True
        val = val[part]
    return False


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------

def cmd_revise(
    cfg: Dict[str, Any],
    sku: str,
    assignments: List[str],
    *,
    show: bool = False,
    by: str = "claude",
) -> Dict[str, Any]:
    """Compute a revision delta and write revision_draft to the item JSON.

    NO eBay API calls are made; NO fields outside of revision_draft are modified.
    Always returns {ok, sku, delta, baseline_hash, drift, diff_lines, ...}.
    """
    if not assignments:
        return {"ok": False, "error": "no --set assignments provided"}

    try:
        delta = parse_assignments(assignments)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    json_path = sku_json(cfg, sku)

    if not json_path.exists():
        return {"ok": False, "error": f"item JSON not found: {json_path}"}

    try:
        item = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"failed to read item JSON: {exc}"}

    # Build baseline from current live mirror
    mirror = live_mirror(item)
    snapshot = dict(mirror)
    b_hash = baseline_hash(snapshot)

    # Check for existing draft and detect drift against its pinned baseline
    existing_draft = item.get("revision_draft")
    drift: List[Dict[str, Any]] = []
    if existing_draft and isinstance(existing_draft.get("baseline"), dict):
        pinned = existing_draft["baseline"].get("snapshot") or {}
        drift = detect_drift(mirror, pinned)

    # Build diff lines (always computed; printed only when --show)
    diff_lines = format_diff(item, delta, mirror, drift)

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    revision_draft = {
        "delta": delta,
        "baseline": {
            "hash": b_hash,
            "snapshot": snapshot,
        },
        "created_at": now_iso,
        "by": by,
    }

    # Only revision_draft is written — all other item fields are untouched
    item["revision_draft"] = revision_draft
    atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))

    return {
        "ok": True,
        "sku": sku,
        "delta": delta,
        "baseline_hash": b_hash,
        "drift": drift,
        "diff_lines": diff_lines,
        "created_at": now_iso,
        "by": by,
        "had_existing_draft": existing_draft is not None,
    }


# ---------------------------------------------------------------------------
# Apply path (PP-REVISION-001 second slice)
# ---------------------------------------------------------------------------

# Design confirmed by Dave 2026-07-01 (session 40). The CLI --live flag /
# dry_run=False is still required so an accidental call never fires.
_APPLY_ENABLED = True

# Delta fields the live apply knows how to place into the eBay API bodies.
# Anything else refuses the apply — silently dropping an operator edit is
# worse than making them re-draft.
_INV_ITEM_FIELDS = {
    "title", "description", "condition", "condition_enum",
    "item_specifics", "aspects", "imageUrls", "quantity",
}
_OFFER_FIELDS = {"price", "live_price", "listing_description", "quantity"}
_SUPPORTED_FIELDS = _INV_ITEM_FIELDS | _OFFER_FIELDS


def _place_delta_in_bodies(
    inv_body: Dict[str, Any],
    offer_body: Dict[str, Any],
    delta: Dict[str, Any],
) -> tuple:
    """Mutate fresh GET bodies with the delta. Returns (inv_changed, offer_changed)."""
    inv_changed = offer_changed = False
    product = inv_body.setdefault("product", {})
    for field, val in delta.items():
        if field == "title":
            product["title"] = str(val)
            inv_changed = True
        elif field == "description":
            product["description"] = str(val)
            inv_changed = True
        elif field in ("condition", "condition_enum"):
            inv_body["condition"] = str(val)
            inv_changed = True
        elif field in ("item_specifics", "aspects") and isinstance(val, dict):
            product["aspects"] = {
                k: (v if isinstance(v, list) else [str(v)]) for k, v in val.items()
            }
            inv_changed = True
        elif field == "imageUrls" and isinstance(val, list):
            product["imageUrls"] = [str(u) for u in val][:24]
            inv_changed = True
        elif field == "quantity":
            qty = int(val)
            avail = inv_body.setdefault("availability", {}).setdefault(
                "shipToLocationAvailability", {}
            )
            avail["quantity"] = qty
            for dist in avail.get("availabilityDistributions") or []:
                dist["quantity"] = qty
            offer_body["availableQuantity"] = qty
            inv_changed = offer_changed = True
        elif field in ("price", "live_price"):
            pricing = offer_body.setdefault("pricingSummary", {})
            price_block = pricing.setdefault("price", {"currency": "USD"})
            price_block["value"] = f"{float(val):.2f}"
            offer_changed = True
        elif field == "listing_description":
            offer_body["listingDescription"] = str(val)
            offer_changed = True
    return inv_changed, offer_changed


def _apply_live_revision(
    cfg: Dict[str, Any],
    sku: str,
    item: Dict[str, Any],
    delta: Dict[str, Any],
) -> Dict[str, Any]:
    """Push the composed revision to eBay: fresh GET → place delta → PUT.

    Inventory API only. Returns {ok, calls: [...]} or {ok: False, error}.
    """
    unsupported = sorted(set(delta) - _SUPPORTED_FIELDS)
    if unsupported:
        return {
            "ok": False,
            "error": (
                f"unsupported delta field(s) for live apply: {', '.join(unsupported)} — "
                f"supported: {', '.join(sorted(_SUPPORTED_FIELDS))}"
            ),
        }

    listing = item.get("ebay_listing") or {}
    offer_id = listing.get("offer_id") or (item.get("ebay_offer") or {}).get("offer_id")
    if listing.get("api") == "trading" or not offer_id:
        return {
            "ok": False,
            "error": (
                f"{sku}: live apply requires an Inventory API offer "
                "(Trading API revision is a follow-on — see PP-LISTEDITOR-001)"
            ),
        }

    from tgw.apis.ebay.client import ebay_get, ebay_put

    # Fresh live state is the composition baseline — never the cached mirror.
    inv_body = ebay_get(cfg, f"/sell/inventory/v1/inventory_item/{sku}")
    offer_body = ebay_get(cfg, f"/sell/inventory/v1/offer/{offer_id}")
    # GET returns read-only fields the PUT schema rejects; strip them.
    for k in ("sku", "locale"):
        inv_body.pop(k, None)
    for k in ("offerId", "sku", "status", "listing", "statusReason"):
        offer_body.pop(k, None)

    inv_changed, offer_changed = _place_delta_in_bodies(inv_body, offer_body, delta)

    calls: List[str] = []
    if inv_changed:
        ebay_put(cfg, f"/sell/inventory/v1/inventory_item/{sku}", inv_body)
        calls.append(f"PUT inventory_item/{sku}")
    if offer_changed:
        ebay_put(cfg, f"/sell/inventory/v1/offer/{offer_id}", offer_body)
        calls.append(f"PUT offer/{offer_id}")
    return {"ok": True, "calls": calls, "offer_id": offer_id}


def compose_revised_state(
    current_mirror: Dict[str, Any],
    delta: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge sparse delta onto current live mirror, returning the composed state.

    Delta keys take precedence; all other mirror keys are preserved.
    The result is the full state that would be PUT to eBay at apply time.
    Dotted-path delta keys are applied as flat top-level keys (Inventory API
    does not support nested partial updates; composition happens at apply time).
    """
    composed = dict(current_mirror)
    composed.update(delta)
    return composed


def _overlapping_drift(
    delta: Dict[str, Any],
    drift: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return drift entries whose field name also appears in delta (blocking)."""
    delta_fields = set(delta)
    return [d for d in drift if d["field"] in delta_fields]


def format_apply_diff(
    delta: Dict[str, Any],
    current_mirror: Dict[str, Any],
    composed: Dict[str, Any],
    blocking_drift: List[Dict[str, Any]],
    non_blocking_drift: List[Dict[str, Any]],
) -> List[str]:
    """Return human-readable lines showing the proposed apply diff."""
    lines: List[str] = ["=== apply diff (current mirror → composed) ==="]
    for field, new_val in delta.items():
        old_val = current_mirror.get(field)
        if old_val != new_val:
            lines.append(f"  {field}:")
            lines.append(f"    was:  {old_val!r}")
            lines.append(f"    now:  {new_val!r}")

    if blocking_drift:
        lines.append("")
        lines.append("=== BLOCKING DRIFT — apply refused ===")
        for d in blocking_drift:
            lines.append(
                f"  {d['field']}: baseline={d['baseline']!r}  current={d['current']!r}"
            )

    if non_blocking_drift:
        lines.append("")
        lines.append("=== non-blocking drift (unrelated fields changed on eBay) ===")
        for d in non_blocking_drift:
            lines.append(
                f"  {d['field']}: baseline={d['baseline']!r}  current={d['current']!r}"
            )

    return lines


def cmd_revise_apply(
    cfg: Dict[str, Any],
    sku: str,
    *,
    dry_run: bool = True,
    by: str = "claude",
) -> Dict[str, Any]:
    """Apply the stored revision_draft to the live eBay listing.

    Drift-gate: if any field in delta has drifted in the live mirror since the
    baseline was pinned, the apply is refused with details.  Only non-blocking
    drift (fields not in the delta) is tolerated; a warning is included.

    dry_run=True (default): compose and display; no eBay call.
    dry_run=False: requires _APPLY_ENABLED = True; otherwise returns an error.

    Always returns {ok, sku, dry_run, ...}.
    """
    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {"ok": False, "error": f"item JSON not found: {json_path}"}

    try:
        item = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"failed to read item JSON: {exc}"}

    draft = item.get("revision_draft")
    if not draft:
        return {
            "ok": False,
            "error": (
                f"no revision_draft found for {sku} — "
                f"run `tgw revise {sku} --set FIELD=VALUE` first"
            ),
        }

    delta: Dict[str, Any] = draft.get("delta") or {}
    if not delta:
        return {"ok": False, "error": "revision_draft has an empty delta"}

    baseline = draft.get("baseline") or {}
    pinned_snapshot: Dict[str, Any] = baseline.get("snapshot") or {}
    pinned_hash: str = baseline.get("hash") or ""

    current_mirror = live_mirror(item)
    current_hash = baseline_hash(current_mirror)

    # Drift detection
    all_drift = detect_drift(current_mirror, pinned_snapshot)
    blocking = _overlapping_drift(delta, all_drift)
    non_blocking = [d for d in all_drift if d not in blocking]

    if blocking:
        diff_lines = format_apply_diff(delta, current_mirror, {}, blocking, non_blocking)
        return {
            "ok": False,
            "sku": sku,
            "error": (
                f"{len(blocking)} delta field(s) have drifted since baseline was pinned — "
                "apply refused; re-run `tgw revise` to re-pin baseline after reviewing"
            ),
            "blocking_drift": blocking,
            "non_blocking_drift": non_blocking,
            "diff_lines": diff_lines,
            "dry_run": dry_run,
        }

    composed = compose_revised_state(current_mirror, delta)
    diff_lines = format_apply_diff(delta, current_mirror, composed, [], non_blocking)

    calls: List[str] = []
    if not dry_run:
        if not _APPLY_ENABLED:
            return {
                "ok": False,
                "sku": sku,
                "error": (
                    "Live eBay write is disabled (_APPLY_ENABLED = False). "
                    "Re-run without --live to preview the composed state."
                ),
                "dry_run": dry_run,
            }
        live = _apply_live_revision(cfg, sku, item, delta)
        if not live.get("ok"):
            return {"ok": False, "sku": sku, "dry_run": dry_run, **{
                k: v for k, v in live.items() if k != "ok"
            }}
        calls = live.get("calls") or []

        # Clear the draft and keep an audit trail of what was pushed.
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        history = item.get("revision_history") or []
        history.append({
            "applied_at": now_iso,
            "by": by,
            "delta": delta,
            "baseline_hash": pinned_hash,
            "calls": calls,
        })
        item["revision_history"] = history
        item.pop("revision_draft", None)
        atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))

    return {
        "ok": True,
        "sku": sku,
        "dry_run": dry_run,
        "applied": not dry_run,
        "calls": calls,
        "delta": delta,
        "composed": composed,
        "baseline_hash": pinned_hash,
        "current_hash": current_hash,
        "hash_match": pinned_hash == current_hash,
        "blocking_drift": blocking,
        "non_blocking_drift": non_blocking,
        "diff_lines": diff_lines,
    }
