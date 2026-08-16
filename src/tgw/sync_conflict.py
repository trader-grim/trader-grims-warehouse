"""
tgw.sync_conflict — Syncthing conflict-file scanner (PP-PORTABLE-CATALOG-001 P3).

Scans configured sync roots for ``*.sync-conflict-*`` files. Each conflict is
compared to its canonical counterpart:

  - identical          → auto-discard (byte-for-byte match; no data lost)
  - divergent_pipeline → move to inbox/review/, HIGH-priority todo
                         (conflict has unique/different TGW pipeline data: status
                         mismatch beyond stale defaults, unique ebay_listing, etc.)
  - divergent          → move to inbox/review/, NORMAL-priority todo
                         (non-SKU-JSON or unclassified ItemData divergence)
  - divergent_legacy   → move to inbox/review/, LOW-priority todo
                         (conflict differs only in obsolete M1/M2/CSV schema fields;
                         TGW pipeline fields agree or conflict holds stale defaults)
  - no_canonical       → move to inbox/review/, NORMAL-priority todo
                         (canonical file does not exist; unknown provenance)

Zero-data-loss invariant: unique content is NEVER deleted under any code path.

Scan roots default to ``[itemdata_root]`` and are extended via
``sync_conflict_roots`` in ``tgw-api-config.json``.  The legacy Plan Vault
is intentionally excluded from this operational scanner.

Decision tree for ItemData SKU JSON files (tgwXXXXXX.json):

  1. Identical bytes                  → 'identical'  (auto-discard)
  2. Conflict has unique pipeline field
     with non-stale value             → 'divergent_pipeline'
  3. Conflict status differs and is
     not a stale default              → 'divergent_pipeline'
  4. All conflict-unique keys are known
     legacy M1/M2/CSV fields and
     only stale status difference     → 'divergent_legacy'
  5. Otherwise                        → 'divergent'

For non-SKU-JSON files, classification is binary: 'identical' or 'divergent'.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

# Syncthing conflict filename: <stem>.sync-conflict-YYYYMMDD-HHMMSS-HASH[.<ext>]
_CONFLICT_RE = re.compile(
    r"^(.+?)\.sync-conflict-\d{8}-\d{6}-[A-Za-z0-9]+(\.[^.]+)?$",
)

# ItemData SKU JSON: tgw + exactly 15 digits + .json  (18 chars total, normalized format)
_SKU_JSON_RE = re.compile(r"^tgw\d{15}\.json$")

# Status values that are stale defaults from M1/M2/old CSV import; NOT authoritative.
_STALE_STATUSES = frozenset({"unknown", "Unknown", "Enabled", "Disabled", "new", ""})

# TGW pipeline fields written by workers — divergence here may mean real data loss.
_PIPELINE_FIELDS = frozenset(
    {
        "ebay_listing",
        "ebay_offer",
        "reprice_schedule",
        "ebay_promo",
        "promo_skip",
        "reprice_skip",
    }
)

# Known-obsolete M1/M2/Magento/CSV import field names (exact match).
_LEGACY_FIELDS_EXACT = frozenset(
    {
        "Action",
        "Action(CC=CP1252)",
        "Custom Label (SKU)",
        "Custom label (SKU)",
        "Custom Label",
        "SKU",
        "Title",  # uppercase — old CSV column; pipeline uses lowercase 'title'
        "Category ID",
        "SiteID",
        "Start price",
        "Current price",
        "Start date",
        "End date",
        "Currency",
        "Available quantity",
        "Sold quantity",
        "Listing site",
        "Format",
        "ItemID",
        "Ebayid-last",
        "Watchers",
        "Condition ID",
        "Condition description",
        "Item location",
        "Shipping service 1 option",
        "Shipping service 1 cost",
        "Shipping type",
        "Best Offer Enabled",
        "Best Offer Type",
        "Returns accepted option",
        "Payment method",
        "Reserved1-Do not edit or delete",
        "P:UPC",
        # Magento product/catalog fields
        "name",
        "short_description",
        "small_image",
        "thumbnail",
        "image",
        "additional_images",
        "url_key",
        "url_path",
        "attribute_set",
        "category_ids",
        "store",
        "store_id",
        "websites",
        "visibility",
        "has_options",
        "product_type_id",
        "type",
        "is_qty_decimal",
        "min_qty",
        "max_sale_qty",
        "min_sale_qty",
        "qty_increments",
        "backorders",
        "is_decimal_divided",
        "stock_status_changed_automatically",
        "stock_status_changed_auto",
        "manage_stock",
        "use_config_manage_stock",
        "use_config_min_qty",
        "use_config_max_sale_qty",
        "use_config_min_sale_qty",
        "use_config_qty_increments",
        "use_config_enable_qty_increments",
        "use_config_enable_qty_inc",
        "use_config_backorders",
        "use_config_notify_stock_qty",
        "enable_qty_increments",
        "tax_class_id",
        "research_terms",
        "product_name",
        "tgw_location",
        # TGW location field variants from old imports
        "#VERIFIED",
        "#LOCATION",
        # Old eBay bulk listing column variants
        "Listing type",
        "PayPal email address",
        "Immediate pay required",
        "Location",
        "Default country (ship-from)",
    }
)

# Prefixes that identify legacy field names.
_LEGACY_PREFIXES = ("m1_", "m2_", "202", "use_config_")

# Todo priorities by verdict (lower number = higher urgency in TGW task system).
_TODO_PRIORITIES: Dict[str, int] = {
    "divergent_pipeline": 15,
    "divergent": 30,
    "no_canonical": 45,
    "divergent_legacy": 65,
}


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------


def canonical_name(filename: str) -> Optional[str]:
    """Return the canonical filename for a sync-conflict filename.

    Returns None if ``filename`` does not match the Syncthing conflict pattern.

    Examples::

        canonical_name('community-plugins.sync-conflict-20260601-120000-ABCDEF.json')
        # → 'community-plugins.json'

        canonical_name('directorysizes.sync-conflict-20260517-134153-Y3YVMPP')
        # → 'directorysizes'

        canonical_name('normal.json')  # → None
    """
    m = _CONFLICT_RE.match(filename)
    if not m:
        return None
    stem = m.group(1)
    ext = m.group(2) or ""
    return stem + ext


# ---------------------------------------------------------------------------
# Legacy-field helpers
# ---------------------------------------------------------------------------


def _is_legacy_field(key: str) -> bool:
    """True if *key* is a known obsolete M1/M2/Magento/CSV import field."""
    if key in _LEGACY_FIELDS_EXACT:
        return True
    return any(key.startswith(p) for p in _LEGACY_PREFIXES)


# ---------------------------------------------------------------------------
# Deep classification for ItemData SKU JSON files
# ---------------------------------------------------------------------------


def _classify_itemdata_json(conflict_path: Path, canonical_path: Path) -> str:
    """Semantic classification of a divergent ItemData JSON conflict.

    Returns 'divergent_pipeline', 'divergent_legacy', or 'divergent'.
    Called only when byte comparison already showed the files differ.
    """
    try:
        c_data = json.loads(conflict_path.read_bytes())
        k_data = json.loads(canonical_path.read_bytes())
    except Exception:
        return "divergent"

    if not isinstance(c_data, dict) or not isinstance(k_data, dict):
        return "divergent"

    unique_to_conflict = set(c_data) - set(k_data)

    # Pipeline fields unique to conflict → could mean data in conflict not in canonical.
    if unique_to_conflict & _PIPELINE_FIELDS:
        return "divergent_pipeline"

    # Status divergence: if conflict holds a real (non-stale) status different from
    # canonical, something meaningful changed — operator must review.
    c_status = str(c_data.get("status", ""))
    k_status = str(k_data.get("status", ""))
    if c_status != k_status and c_status not in _STALE_STATUSES:
        return "divergent_pipeline"

    # All unique-to-conflict keys are known-legacy → low-risk.
    # (Stale status difference alone is acceptable here.)
    if all(_is_legacy_field(k) for k in unique_to_conflict):
        return "divergent_legacy"

    return "divergent"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def classify_conflict(conflict_path: Path) -> str:
    """Classify one conflict file against its canonical counterpart.

    Returns one of:
      ``'identical'``           — byte-for-byte match; safe to discard
      ``'divergent_pipeline'``  — differs; conflict has unique/different TGW
                                  pipeline data (high-priority review needed)
      ``'divergent_legacy'``    — differs only in obsolete M1/M2/CSV fields
                                  (low-priority review)
      ``'divergent'``           — general divergence (normal-priority review)
      ``'no_canonical'``        — canonical does not exist (normal-priority review)
    """
    canon = canonical_name(conflict_path.name)
    if canon is None:
        return "no_canonical"

    canonical_path = conflict_path.parent / canon
    if not canonical_path.exists():
        return "no_canonical"

    if conflict_path.read_bytes() == canonical_path.read_bytes():
        return "identical"

    if _SKU_JSON_RE.match(canon):
        return _classify_itemdata_json(conflict_path, canonical_path)

    return "divergent"


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve_conflict(
    conflict_path: Path,
    review_dir: Path,
    *,
    dry_run: bool = False,
    add_todo_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Resolve one conflict file.

    Returns a dict with keys:
      ``action``       — 'discarded', 'flagged', or 'skipped' (dry_run)
      ``conflict``     — Path of the conflict file
      ``canonical``    — Path of the canonical file (or None)
      ``reason``       — classification verdict string
      ``todo_priority``— recommended todo priority (lower = more urgent)
      ``dest``         — Path the file was moved to (flagged action only)

    ``add_todo_fn`` is called as ``add_todo_fn(body, priority=N)`` when a file
    is flagged. It should accept an optional ``priority`` keyword argument.
    Callers that only need the body can use ``lambda body, priority=30: ...``.
    """
    verdict = classify_conflict(conflict_path)
    canon_name = canonical_name(conflict_path.name)
    _canon_path = (conflict_path.parent / canon_name) if canon_name else None
    canonical = _canon_path if (_canon_path and _canon_path.exists()) else None
    todo_priority = _TODO_PRIORITIES.get(verdict, 30)

    result: Dict[str, Any] = {
        "conflict": conflict_path,
        "canonical": canonical,
        "reason": verdict,
        "todo_priority": todo_priority,
        "dest": None,
    }

    if verdict == "identical":
        if not dry_run:
            conflict_path.unlink()
            log.info("sync_conflict: discarded identical %s", conflict_path.name)
        result["action"] = "skipped" if dry_run else "discarded"
        return result

    # All non-identical verdicts: move to review and create todo
    if dry_run:
        result["action"] = "skipped"
        return result

    review_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(review_dir, conflict_path.name)
    conflict_path.rename(dest)
    log.warning("sync_conflict: flagged %s (%s) → %s", conflict_path.name, verdict, dest)

    if add_todo_fn:
        try:
            add_todo_fn(
                f"Review sync-conflict: {conflict_path.name}\nMoved to: {dest}\nCanonical: {canonical}\nReason: {verdict}",
                priority=todo_priority,
            )
        except Exception as exc:
            log.warning("sync_conflict: todo_add failed for %s: %s", conflict_path.name, exc)

    result["action"] = "flagged"
    result["dest"] = dest
    return result


def _unique_dest(review_dir: Path, filename: str) -> Path:
    """Return a non-colliding path inside review_dir for filename."""
    dest = review_dir / filename
    if not dest.exists():
        return dest
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    i = 1
    while True:
        candidate = review_dir / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


# ---------------------------------------------------------------------------
# Scan helpers
# ---------------------------------------------------------------------------


def _iter_conflicts(root: Path):
    """Yield all sync-conflict files under root (recursive)."""
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and canonical_name(path.name) is not None:
            yield path


def count_conflicts(roots: List[Path]) -> int:
    """Return the total count of unresolved sync-conflict files across roots."""
    return sum(1 for root in roots for _ in _iter_conflicts(root))


# ---------------------------------------------------------------------------
# Full-scan entry point
# ---------------------------------------------------------------------------


def _default_add_todo(body: str, *, priority: int = 30) -> None:
    from tgw.todo import todo_add

    todo_add(agent="admin", body=body, priority=priority, source="sync_conflict", pp_ref="PP-PORTABLE-CATALOG-001")


def run_scan(
    cfg: Dict[str, Any],
    *,
    dry_run: bool = False,
    add_todo_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Scan all configured sync roots and resolve every conflict found.

    Returns a result dict with counts and per-file details.
    ``dry_run=True`` classifies without mutating anything.

    When ``add_todo_fn`` is None and dry_run is False, uses the live todo DB.
    ``add_todo_fn`` receives ``(body: str, *, priority: int)``; callers that
    only need the body can pass ``lambda body, priority=30: ...``.
    """
    t0 = time.time()

    roots: List[Path] = cfg.get("sync_conflict_roots") or []
    # W11: the mutable source-tree vault is archive/inbox material, not an
    # operational sync surface.  Filter it at the execution boundary as well
    # as config loading so an old configuration cannot re-enable the scanner.
    legacy_vault = cfg.get("plan_vault_path")
    if legacy_vault:
        legacy = Path(legacy_vault).resolve()
        roots = [
            root for root in roots
            if not (Path(root).resolve() == legacy or legacy in Path(root).resolve().parents)
        ]
    review_dir: Path = cfg["plan_inbox_path"] / "review"

    todo_fn = add_todo_fn
    if todo_fn is None and not dry_run:
        todo_fn = _default_add_todo

    total = discarded = flagged = 0
    details = []

    for root in roots:
        for conflict in list(_iter_conflicts(root)):
            total += 1
            res = resolve_conflict(
                conflict,
                review_dir,
                dry_run=dry_run,
                add_todo_fn=todo_fn,
            )
            if res["action"] == "discarded":
                discarded += 1
            elif res["action"] == "flagged":
                flagged += 1
            details.append(
                {
                    "file": str(res["conflict"]),
                    "action": res["action"],
                    "reason": res["reason"],
                    "todo_priority": res["todo_priority"],
                }
            )

    elapsed = round(time.time() - t0, 3)
    log.info(
        "sync_conflict scan: %d found, %d discarded, %d flagged, %.1fs",
        total,
        discarded,
        flagged,
        elapsed,
    )

    return {
        "ok": True,
        "artifact": "sync_conflict_scan",
        "dry_run": dry_run,
        "roots_scanned": [str(r) for r in roots],
        "total": total,
        "discarded": discarded,
        "flagged": flagged,
        "skipped": total - discarded - flagged,
        "details": details,
        "elapsed_seconds": elapsed,
    }
