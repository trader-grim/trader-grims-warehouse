"""
tgw.revision — Live listing revision draft (PP-REVISION-001, first slice).

Dry-run delta computer: writes a revision_draft to the item JSON and
shows a diff vs the live-mirror (ebay_listing block).  No eBay API calls
are made; no fields outside of revision_draft are modified.

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
