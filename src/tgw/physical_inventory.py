"""
tgw.physical_inventory — PP-INVENTORY-001 manual leg (todo #1482).

Manifest-vs-physical checklist workflow: an operator physically walks a
storage location, compares what is actually there against the record
system's expected-contents manifest (every item whose `location` field
equals that location — no new schema needed, see pp/PP-INVENTORY-001.md),
and records the reconciliation result per SKU.

This is the manual leg — steps 3/4 of the PP doc (vision-worker auto
check-off) are NOT implemented here; that is the vision-assisted leg,
gated on PP-VISION-001 Phase 2 (see PP-INVENTORY-001.md "Two legs,
sequenced"). Every SKU in the manual leg is adjudicated by eye, by an
operator, one at a time.

Absorbs todo #11 (`tgw ebay-sweep`) conceptually: that existing command
generates a checklist for *ambiguous-status* items (open-set, not scoped
to a location); this module generates a checklist for one location's
*expected contents* (closed-set, the manifest), and additionally provides
the write-back/adjudication step ebay-sweep never had (it only tells the
operator which `tgw update` command to run by hand). `ebay-sweep` is left
as-is — it still serves its own "ambiguous status wherever it is" use
case, which is a different question than "is location X's manifest
accurate."

Reconciliation results are persisted durably on the item under the
`inventory_sweep` field (invariant C11 — a finding is persisted, not just
logged) and go through the existing `_write_field` audit-trail plumbing
(queryable later via `tgw audit-trail <SKU>`), never a silent overwrite
(C14 discipline) — a misfiled correction is a real `location` write via
the same `locationupdate()` path already used everywhere else, so the
location symlink tree and audit trail stay in sync automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import sku_json
from .items import _write_field, locationupdate
from .resolver import load_item_doc, resolve

_RESULTS = ("present", "missing", "misfiled")


def build_manifest(cfg: Dict[str, Any], location: str) -> List[Dict[str, Any]]:
    """Return the expected-contents manifest for one storage location.

    Every item whose current `location` field equals ``location`` — the
    "manifest" the PP doc describes (no new schema; a direct query over
    the existing location field). Sorted by SKU for stable checklist
    ordering.
    """
    skus = resolve(cfg, location=location)
    manifest: List[Dict[str, Any]] = []
    for sku in sorted(skus):
        path = sku_json(cfg, sku)
        try:
            doc = load_item_doc(path)
        except Exception:
            continue
        sweep = doc.get("inventory_sweep") or {}
        manifest.append(
            {
                "sku": sku,
                "title": str(doc.get("title", "")).strip(),
                "status": str(doc.get("status") or doc.get("#STATUS") or "").strip(),
                "last_result": sweep.get("result"),
                "last_checked_at": sweep.get("checked_at"),
            }
        )
    return manifest


def inventory_sweep_checklist(
    cfg: Dict[str, Any],
    location: str,
    *,
    output: Optional[Path] = None,
) -> Dict[str, Any]:
    """Generate a markdown manifest-vs-physical checklist for one location.

    Mirrors `cmd_ebay_sweep`'s checklist shape (markdown table, Obsidian-
    friendly) but scoped to one location's full expected-contents manifest
    rather than ambiguous-status items system-wide.
    """
    manifest = build_manifest(cfg, location)
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: List[str] = [
        f"# Physical Inventory Checklist — location {location} — {ts}",
        f"Manifest size: {len(manifest)}",
        "",
        "Walk the location, check each SKU against what is physically",
        "present, then record the result with:",
        f"  `tgw inventory-record <SKU> present|missing|misfiled --location {location}`",
        "(misfiled additionally needs `--to-location <NEW_LOC>`).",
        "",
        "| Done | SKU | Status | Last check | Title |",
        "|------|-----|--------|------------|-------|",
    ]
    for it in manifest:
        last = (
            f"{it['last_result']} @ {it['last_checked_at']}"
            if it["last_result"]
            else "—"
        )
        title_col = it["title"][:50].replace("|", "/") if it["title"] else "—"
        lines.append(
            f"| [ ] | {it['sku']} | {it['status'] or '(empty)'} | {last} | {title_col} |"
        )
    lines.append("")

    content = "\n".join(lines)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
        print(f"Inventory checklist written to {output}  ({len(manifest)} items)")
    else:
        print(content)

    return {
        "ok": True,
        "location": location,
        "count": len(manifest),
        "manifest": manifest,
        "output": str(output) if output else None,
    }


def inventory_record(
    cfg: Dict[str, Any],
    sku: str,
    result: str,
    *,
    location: Optional[str] = None,
    to_location: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Record one operator adjudication for a SKU during a physical sweep.

    result:
      present  — SKU physically confirmed at its recorded location.
      missing  — SKU not found; recorded as a durable finding on the item
                 (never silently discarded — Prime Directive 1), left for
                 the operator to resolve separately (e.g. `tgw update
                 <SKU> status sold`); this call does not itself change
                 `status`, since "missing" is not the same fact as "sold"
                 and conflating them would be a silent substitution.
      misfiled — SKU found in a different location than recorded;
                 requires `to_location`, which is written through the
                 existing `locationupdate()` path (keeps the location
                 symlink tree + audit trail correct, same as any other
                 location correction in the system).
    """
    if result not in _RESULTS:
        return {"ok": False, "error": f"result must be one of {_RESULTS}, got {result!r}"}

    path = sku_json(cfg, sku)
    if not path.exists():
        return {"ok": False, "error": f"sku not found: {sku!r}"}

    if result == "misfiled" and not to_location:
        return {"ok": False, "error": "misfiled requires --to-location"}

    ts = datetime.now(tz=timezone.utc).isoformat()
    record: Dict[str, Any] = {
        "result": result,
        "checked_at": ts,
        "location_at_check": location,
    }
    if note:
        record["note"] = note
    if result == "misfiled":
        record["corrected_to"] = to_location

    loc_result: Optional[Dict[str, Any]] = None
    if result == "misfiled":
        loc_result = locationupdate(cfg, sku, to_location)
        if not loc_result.get("ok"):
            return {"ok": False, "error": loc_result.get("error", "location update failed")}

    write_result = _write_field(cfg, sku, "inventory_sweep", record)

    return {
        "ok": True,
        "sku": sku,
        "result": result,
        "record": record,
        "location_update": loc_result,
        "field_write": write_result,
    }
