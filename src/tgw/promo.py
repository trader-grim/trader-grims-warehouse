"""
tgw.promo — Sale event automation (PP-PROMO-001).

P2 (read-only):
  cmd_promo_draft() — scan dead_stock, apply filters, write markdown draft
  cmd_promo_list()  — GET Promotions API to verify sell.marketing scope

P3 (eBay writes):
  cmd_promo_apply() — parse draft, POST to Promotions API, write ebay_promo blocks

P4 (lifecycle):
  cmd_promo_end()   — pause/delete promotion, clear ebay_promo blocks
  cmd_promo_sync()  — import Seller Hub promotions → write ebay_promo blocks to item JSONs
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .reports import _coerce_price, _item_group, _parse_date

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

_PROMO_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "min_days_stale": 30,
    "min_price": 2.00,
    "max_items": 50,
    "discount_pct": 20,
    "duration_days": 30,
    "start_offset_days": 2,
    "marketplace_id": "EBAY_US",
}

_EXCLUDED_STATUSES = frozenset(
    {
        "sold",
        "archived",
        "disposed",
        "discard",
        "vero",
        "draft",
        "merged",
        "disposeddisposed",
        "recalled",
    }
)


def _get_promo_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return {**_PROMO_DEFAULTS, **(cfg.get("promo") or {})}


# ---------------------------------------------------------------------------
# Category group index with floor data
# ---------------------------------------------------------------------------


def _build_promo_group_index(cfg: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """
    Returns (cat_id_to_key, groups_by_key) where groups_by_key maps
    group_key → full group dict (including pricing.floor).
    """
    cat_id_to_key: Dict[str, str] = {}
    groups_by_key: Dict[str, Any] = {}
    try:
        from tgw.ebay.pricing import _load_groups

        data = _load_groups(cfg)
        for key, grp in data.get("groups", {}).items():
            groups_by_key[key] = grp
            for cat_id in grp.get("ebay_categories", []):
                cat_id_to_key[str(cat_id)] = key
    except Exception as exc:
        log.debug("promo: could not load category groups: %s", exc)
    return cat_id_to_key, groups_by_key


def _floor_for_group(group_key: str, groups_by_key: Dict[str, Any]) -> Optional[float]:
    grp = groups_by_key.get(group_key)
    if grp:
        floor = (grp.get("pricing") or {}).get("floor")
        if floor is not None:
            return float(floor)
    return None


# ---------------------------------------------------------------------------
# Dead-stock candidate scan with listing_id augmentation
# ---------------------------------------------------------------------------


def _scan_promo_candidates(
    itemdata_root: Path,
    cat_id_to_key: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Walk ItemData; return (candidate_rows, scan_counts).

    candidate_rows: dead-stock items with listing_id (oldest-stale first).
    scan_counts: {"skipped_no_listing": N, "skipped_active_promo": N}

    Hard exclusions (not configurable):
      - status in _EXCLUDED_STATUSES
      - not all reprice stages done
      - promo_skip: true
      - ebay_promo.promo_id already set
      - no ebay_listing.listing_id
    """
    rows: List[Dict[str, Any]] = []
    skipped_no_listing = 0
    skipped_active_promo = 0
    now = datetime.now(timezone.utc)

    for sku_dir in sorted(itemdata_root.iterdir()):
        jf = sku_dir / f"{sku_dir.name}.json"
        if not jf.exists():
            continue
        try:
            item = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(item, dict):
            continue

        status = str(item.get("status", "")).lower().strip()
        if status in _EXCLUDED_STATUSES:
            continue

        schedule = item.get("reprice_schedule") or []
        if not schedule or not all(s.get("done_at") for s in schedule):
            continue

        if item.get("promo_skip"):
            continue
        if (item.get("ebay_promo") or {}).get("promo_id"):
            skipped_active_promo += 1
            continue

        ebay_listing = item.get("ebay_listing") or {}
        listing_id = ebay_listing.get("listing_id")
        if not listing_id:
            skipped_no_listing += 1
            continue

        done_dts = [_parse_date(str(s["done_at"])) for s in schedule if s.get("done_at")]
        done_dts = [d for d in done_dts if d is not None]
        last_reprice_dt = max(done_dts) if done_dts else None
        days_stale = max(0.0, (now - last_reprice_dt).total_seconds() / 86400.0) if last_reprice_dt else None

        last_stage = str(max(schedule, key=lambda s: s.get("stage", 0)).get("label", ""))

        price_raw = (item.get("ebay_offer") or {}).get("price") or ebay_listing.get("live_price") or ebay_listing.get("price")

        rows.append(
            {
                "sku": sku_dir.name,
                "title": str(item.get("title") or "")[:60],
                "location": str(item.get("location") or "").strip(),
                "group": _item_group(item, cat_id_to_key),
                "days_stale": round(days_stale, 0) if days_stale is not None else None,
                "last_stage": last_stage,
                "price": _coerce_price(price_raw),
                "listing_id": str(listing_id),
            }
        )

    rows.sort(key=lambda r: (r["days_stale"] is None, -(r["days_stale"] or 0)))
    return rows, {
        "skipped_no_listing": skipped_no_listing,
        "skipped_active_promo": skipped_active_promo,
    }


# ---------------------------------------------------------------------------
# Draft rendering
# ---------------------------------------------------------------------------


def _discounted_price(price: float, discount_pct: int) -> float:
    return round(price * (1.0 - discount_pct / 100.0), 2)


def _fmt_price(v: Optional[float]) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _render_promo_draft(
    items: List[Dict[str, Any]],
    *,
    event_name: str,
    discount_pct: int,
    start_date: str,
    end_date: str,
    marketplace_id: str,
    generated_at: str,
    scan_counts: Dict[str, int],
    groups_by_key: Dict[str, Any],
) -> str:
    lines: List[str] = [
        "---",
        "pp: PP-PROMO-001",
        f"generated: {generated_at}",
        f"discount_pct: {discount_pct}",
        f"start_date: {start_date}",
        f"end_date: {end_date}",
        f"marketplace: {marketplace_id}",
        "status: DRAFT",
        "---",
        "",
        f"# PP-PROMO-001 Sale Event Draft — {generated_at[:10]}",
        "",
        f"**Event name**: {event_name}",
        f"**Discount**: {discount_pct}% off list price",
        f"**Start**: {start_date} 00:00 UTC",
        f"**End**:   {end_date} 00:00 UTC",
        f"**Marketplace**: {marketplace_id}",
        f"**Items**: {len(items)}",
        "",
        f"> skipped_no_listing: {scan_counts.get('skipped_no_listing', 0)}  skipped_active_promo: {scan_counts.get('skipped_active_promo', 0)}",
        "",
        "## SKU List",
        "",
    ]

    if items:
        lines += [
            "| SKU | Title | Group | Days Stale | Price | Discounted | listing_id |",
            "|-----|-------|-------|------------|-------|------------|------------|",
        ]
        for row in items:
            price = row["price"]
            disc = _discounted_price(price, discount_pct) if price is not None else None
            floor = _floor_for_group(row["group"], groups_by_key)
            floor_flag = " ⚠FLOOR" if disc is not None and floor is not None and disc < floor else ""
            days = int(row["days_stale"]) if row["days_stale"] is not None else "—"
            lines.append(f"| `{row['sku']}` | {row['title']} | {row['group']} | {days} | {_fmt_price(price)} | {_fmt_price(disc)}{floor_flag} | {row['listing_id']} |")
    else:
        lines.append("_No eligible items found after applying filters._")

    lines += [
        "",
        "## Operator Instructions",
        "",
        "1. Review the SKU list — delete any rows you don't want in this event",
        "2. Adjust `discount_pct` in the YAML header if needed (must be 5–80)",
        "3. Adjust `start_date` / `end_date` if needed (start must be ≥ today + 1h)",
        "4. Complete the operator checklist in the PP-PROMO-001 design doc",
        "5. Apply: `tgw promo apply <path-to-this-file>`",
        "   (pm_intake will not auto-apply; P3 is a manual step)",
    ]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------


def cmd_promo_draft(
    cfg: Dict[str, Any],
    *,
    discount: Optional[int] = None,
    min_days: Optional[int] = None,
    min_price: Optional[float] = None,
    max_items: Optional[int] = None,
    duration: Optional[int] = None,
    start_offset: Optional[int] = None,
    output_dir: Optional[str] = None,
    no_vault: bool = False,
) -> Dict[str, Any]:
    """
    Read-only: generate markdown draft from dead-stock scan.
    Requires promo.enabled: true in config.
    Returns {ok, filtered_count, draft_path, ...}.
    """
    pcfg = _get_promo_cfg(cfg)
    if not pcfg["enabled"]:
        return {
            "ok": False,
            "error": "promo not enabled — set promo.enabled: true in tgw-api-config.json first",
        }

    discount_pct = int(discount if discount is not None else pcfg["discount_pct"])
    min_days_stale = int(min_days if min_days is not None else pcfg["min_days_stale"])
    min_price_val = float(min_price if min_price is not None else pcfg["min_price"])
    max_items_val = int(max_items if max_items is not None else pcfg["max_items"])
    duration_days = int(duration if duration is not None else pcfg["duration_days"])
    start_offset_days = int(start_offset if start_offset is not None else pcfg["start_offset_days"])
    marketplace_id = str(pcfg["marketplace_id"])

    if not (5 <= discount_pct <= 80):
        return {"ok": False, "error": f"discount_pct {discount_pct} is outside eBay's allowed range 5–80"}

    itemdata_root: Path = cfg["itemdata_root"]
    cat_id_to_key, groups_by_key = _build_promo_group_index(cfg)

    all_candidates, scan_counts = _scan_promo_candidates(itemdata_root, cat_id_to_key)

    filtered = [r for r in all_candidates if r["days_stale"] is not None and r["days_stale"] >= min_days_stale and r["price"] is not None and r["price"] >= min_price_val]
    filtered = filtered[:max_items_val]

    now = datetime.now(timezone.utc)
    start_dt = now + timedelta(days=start_offset_days)
    end_dt = start_dt + timedelta(days=duration_days)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    month_label = now.strftime("%Y-%m")

    event_name = f"TGW Dead Stock Clearance — {month_label}"

    md_text = _render_promo_draft(
        filtered,
        event_name=event_name,
        discount_pct=discount_pct,
        start_date=start_date,
        end_date=end_date,
        marketplace_id=marketplace_id,
        generated_at=generated_at,
        scan_counts=scan_counts,
        groups_by_key=groups_by_key,
    )

    result: Dict[str, Any] = {
        "ok": True,
        "total_candidates": len(all_candidates),
        "filtered_count": len(filtered),
        "discount_pct": discount_pct,
        "start_date": start_date,
        "end_date": end_date,
        "marketplace_id": marketplace_id,
        "skipped_no_listing": scan_counts["skipped_no_listing"],
        "skipped_active_promo": scan_counts["skipped_active_promo"],
        "draft_path": None,
    }

    if not no_vault:
        if output_dir:
            dest = Path(output_dir)
        else:
            plan_vault: Path = cfg.get("plan_vault_path") or Path("/opt/TGW/library/plans")
            dest = Path(plan_vault) / "inbox"

        dest.mkdir(parents=True, exist_ok=True)
        today = now.strftime("%Y%m%d")
        draft_path = dest / f"promo-{today}.md"
        draft_path.write_text(md_text, encoding="utf-8")
        result["draft_path"] = str(draft_path)

    return result


def cmd_promo_list(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read-only scope check: list ITEM_PRICE_MARKDOWN promotions from eBay.
    200 → scope_verified: true.
    403 → scope_verified: false (sell.marketing not active on this token).
    """
    import requests as _requests

    from .apis.ebay.client import ebay_get

    pcfg = _get_promo_cfg(cfg)
    marketplace_id = str(pcfg["marketplace_id"])

    try:
        data = ebay_get(
            cfg,
            "/sell/marketing/v1/promotions",
            params={
                "marketplace_id": marketplace_id,
                "promotion_type": "ITEM_PRICE_MARKDOWN",
            },
        )
    except _requests.HTTPError as exc:
        resp = exc.response
        status = resp.status_code if resp is not None else None
        if status == 403:
            return {
                "ok": False,
                "scope_verified": False,
                "error": ("sell.marketing scope not active on this token (HTTP 403) — re-run get-ebay-token to re-consent with sell.marketing included"),
                "status_code": 403,
            }
        return {
            "ok": False,
            "error": f"eBay API error HTTP {status}: {exc}",
            "status_code": status,
        }

    promotions = data.get("promotions") or []
    return {
        "ok": True,
        "scope_verified": True,
        "marketplace_id": marketplace_id,
        "promotion_count": len(promotions),
        "promotions": promotions,
    }


# ---------------------------------------------------------------------------
# Active-promo guard (used by price reducer and apply validation)
# ---------------------------------------------------------------------------


def has_active_promo(item: Dict[str, Any]) -> bool:
    """Return True if the item has an ebay_promo block with a future end_date."""
    promo = item.get("ebay_promo") or {}
    if not promo.get("promo_id"):
        return False
    end_date_str = promo.get("end_date")
    if not end_date_str:
        return True  # no end date → treat as active (safe default)
    try:
        end_dt = datetime.fromisoformat(str(end_date_str))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < end_dt
    except (ValueError, TypeError):
        return True  # unparseable → treat as active


# ---------------------------------------------------------------------------
# P3 — tgw promo apply
# ---------------------------------------------------------------------------

_TABLE_ROW_RE = re.compile(r"^\|\s*`(tgw\w+)`\s*\|.*\|\s*(\d{10,13})\s*\|")


def _parse_promo_draft(draft_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """
    Parse a promo draft markdown file.
    Returns (frontmatter_dict, rows) where rows = [{"sku": ..., "listing_id": ...}, ...].
    """
    text = draft_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # --- YAML frontmatter between first two '---' delimiters ---
    fm: Dict[str, Any] = {}
    fm_end = 0
    if lines and lines[0].strip() == "---":
        for i, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                fm_end = i
                break
            if ":" in line:
                k, _, v = line.partition(":")
                fm[k.strip()] = v.strip()

    # --- table rows ---
    rows: List[Dict[str, str]] = []
    for line in lines[fm_end:]:
        m = _TABLE_ROW_RE.match(line.strip())
        if m:
            rows.append({"sku": m.group(1), "listing_id": m.group(2)})

    return fm, rows


def cmd_promo_apply(cfg: Dict[str, Any], draft_path: str) -> Dict[str, Any]:
    """
    P3: Parse an approved promo draft, POST to eBay Promotions API, write ebay_promo
    blocks to item JSONs.

    The promotion is created as DRAFT — activate via `tgw promo start <promo_id>` or
    in Seller Hub Marketing → Promotions.
    """
    import requests as _requests

    from .apis.ebay.promotions import create_item_price_markdown
    from .apis.fence import patch_item as fence_patch_item

    pcfg = _get_promo_cfg(cfg)
    if not pcfg["enabled"]:
        return {
            "ok": False,
            "error": "promo not enabled — set promo.enabled: true in tgw-api-config.json first",
        }

    path = Path(draft_path)
    if not path.exists():
        return {"ok": False, "error": f"draft file not found: {draft_path}"}

    fm, rows = _parse_promo_draft(path)
    if not rows:
        return {"ok": False, "error": "no SKU rows found in draft file — nothing to apply"}

    discount_pct = int(fm.get("discount_pct") or pcfg["discount_pct"])
    start_date = str(fm.get("start_date") or "")
    end_date = str(fm.get("end_date") or "")
    marketplace_id = str(fm.get("marketplace") or pcfg["marketplace_id"])
    event_name = f"TGW Dead Stock Clearance — {start_date[:7]}"

    if not (5 <= discount_pct <= 80):
        return {"ok": False, "error": f"discount_pct {discount_pct} outside eBay range 5–80"}
    if not start_date or not end_date:
        return {"ok": False, "error": "draft missing start_date or end_date in frontmatter"}

    listing_ids = [r["listing_id"] for r in rows]
    sku_by_listing: Dict[str, str] = {r["listing_id"]: r["sku"] for r in rows}

    log.info("promo apply: %d items, %d%% off, %s → %s",
             len(rows), discount_pct, start_date, end_date)

    try:
        promo_id = create_item_price_markdown(
            cfg,
            name=event_name,
            marketplace_id=marketplace_id,
            start_date=start_date,
            end_date=end_date,
            discount_pct=discount_pct,
            listing_ids=listing_ids,
        )
    except _requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = ""
        try:
            body = exc.response.text[:400]
        except Exception:
            pass
        return {
            "ok": False,
            "error": f"eBay API error HTTP {status}: {body}",
            "status_code": status,
        }

    # Write ebay_promo block to each item JSON
    applied_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    promo_block = {
        "promo_id": promo_id,
        "event_name": event_name,
        "discount_pct": discount_pct,
        "start_date": start_date,
        "end_date": end_date,
        "applied_at": applied_at,
    }
    written: List[str] = []
    write_errors: List[str] = []
    for listing_id, sku in sku_by_listing.items():
        try:
            fence_patch_item(cfg, sku, {"ebay_promo": promo_block})
            written.append(sku)
        except Exception as exc:
            log.error("promo apply: failed to write ebay_promo to %s: %s", sku, exc)
            write_errors.append(f"{sku}: {exc}")

    log.info("promo apply complete: promo_id=%s, %d items written, %d errors",
             promo_id, len(written), len(write_errors))

    return {
        "ok": True,
        "promo_id": promo_id,
        "event_name": event_name,
        "discount_pct": discount_pct,
        "start_date": start_date,
        "end_date": end_date,
        "marketplace_id": marketplace_id,
        "items_applied": len(written),
        "write_errors": write_errors,
        "next_step": (
            f"Promotion {promo_id!r} created as DRAFT. "
            "Run `tgw promo start <promo_id>` to schedule it, "
            "or activate in Seller Hub → Marketing → Promotions."
        ),
    }


# ---------------------------------------------------------------------------
# P4 — tgw promo end / start
# ---------------------------------------------------------------------------


def _clear_promo_blocks(cfg: Dict[str, Any], promo_id: str) -> Tuple[int, List[str]]:
    """
    Scan ItemData for items with ebay_promo.promo_id == promo_id and clear the block.
    Returns (cleared_count, error_list).
    """
    from .apis.fence import patch_item as fence_patch_item

    itemdata_root: Path = cfg["itemdata_root"]
    cleared = 0
    errors: List[str] = []

    for sku_dir in sorted(itemdata_root.iterdir()):
        jf = sku_dir / f"{sku_dir.name}.json"
        if not jf.exists():
            continue
        try:
            item = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (item.get("ebay_promo") or {}).get("promo_id") == promo_id:
            try:
                fence_patch_item(cfg, sku_dir.name, {"ebay_promo": None})
                cleared += 1
            except Exception as exc:
                errors.append(f"{sku_dir.name}: {exc}")

    return cleared, errors


def cmd_promo_end(
    cfg: Dict[str, Any],
    promo_id: str,
    pause: bool = False,
) -> Dict[str, Any]:
    """
    P4: Pause or permanently delete a promotion, then clear ebay_promo blocks in item JSONs.
    pause=True → POST .../pause (reversible).
    pause=False → DELETE (permanent).
    """
    import requests as _requests

    from .apis.ebay.promotions import delete_promotion, pause_promotion

    try:
        if pause:
            pause_promotion(cfg, promo_id)
            action = "paused"
        else:
            delete_promotion(cfg, promo_id)
            action = "deleted"
    except _requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = ""
        try:
            body = exc.response.text[:400]
        except Exception:
            pass
        return {
            "ok": False,
            "error": f"eBay API error HTTP {status}: {body}",
            "status_code": status,
        }

    cleared, errors = _clear_promo_blocks(cfg, promo_id)
    log.info("promo end: %s %s, cleared %d item blocks, %d errors",
             promo_id, action, cleared, len(errors))

    return {
        "ok": True,
        "promo_id": promo_id,
        "action": action,
        "blocks_cleared": cleared,
        "errors": errors,
    }


def cmd_promo_start(cfg: Dict[str, Any], promo_id: str) -> Dict[str, Any]:
    """Activate a DRAFT promotion to SCHEDULED (eBay validates start date ≥ now + 1h)."""
    import requests as _requests

    from .apis.ebay.promotions import resume_promotion

    try:
        resume_promotion(cfg, promo_id)
    except _requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        body = ""
        try:
            body = exc.response.text[:400]
        except Exception:
            pass
        return {
            "ok": False,
            "error": f"eBay API error HTTP {status}: {body}",
            "status_code": status,
        }

    return {"ok": True, "promo_id": promo_id, "action": "activated"}


# ---------------------------------------------------------------------------
# P4 — tgw promo sync (import Seller Hub promotions)
# ---------------------------------------------------------------------------


def _build_listing_index(cfg: Dict[str, Any]) -> Dict[str, str]:
    """
    Build listing_id → sku index from the SQLite catalog.
    Falls back to ItemData scan if catalog is unavailable.
    """
    index: Dict[str, str] = {}
    db_path = Path(cfg.get("sqlite_catalog_path", ""))
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as conn:
                rows = conn.execute(
                    "SELECT sku, json_extract(data, '$.ebay_listing.listing_id') as lid"
                    " FROM catalog WHERE lid IS NOT NULL"
                ).fetchall()
            for sku, lid in rows:
                index[str(lid)] = sku
            log.debug("promo sync: catalog index built, %d listing IDs", len(index))
            return index
        except Exception as exc:
            log.warning("promo sync: catalog index failed, falling back to ItemData scan: %s", exc)

    # Fallback: scan ItemData directly
    itemdata_root: Path = cfg["itemdata_root"]
    for sku_dir in itemdata_root.iterdir():
        jf = sku_dir / f"{sku_dir.name}.json"
        if not jf.exists():
            continue
        try:
            item = json.loads(jf.read_text(encoding="utf-8"))
            lid = (item.get("ebay_listing") or {}).get("listing_id")
            if lid:
                index[str(lid)] = sku_dir.name
        except Exception:
            continue
    log.debug("promo sync: ItemData scan index built, %d listing IDs", len(index))
    return index


def cmd_promo_sync(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Import all active/scheduled Seller Hub promotions into item JSON ebay_promo blocks.
    Safe to run repeatedly — only writes blocks where promo_id differs or is missing.
    Use this to bring TGW in sync after creating promos directly in Seller Hub.
    """
    import requests as _requests

    from .apis.ebay.promotions import get_item_price_markdown, list_item_price_markdowns
    from .apis.fence import patch_item as fence_patch_item

    pcfg = _get_promo_cfg(cfg)
    marketplace_id = str(pcfg["marketplace_id"])

    try:
        all_promos = list_item_price_markdowns(cfg, marketplace_id)
    except _requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 403:
            return {
                "ok": False,
                "scope_verified": False,
                "error": "sell.marketing scope not active (HTTP 403)",
            }
        return {"ok": False, "error": f"eBay API error HTTP {status}: {exc}"}

    active_statuses = {"RUNNING", "SCHEDULED", "DRAFT"}
    active_promos = [p for p in all_promos if p.get("promotionStatus") in active_statuses]

    if not active_promos:
        return {
            "ok": True,
            "scope_verified": True,
            "active_promos": 0,
            "blocks_written": 0,
            "message": "No active/scheduled/draft promotions found on eBay.",
        }

    listing_index = _build_listing_index(cfg)
    blocks_written = 0
    not_found: List[str] = []
    errors: List[str] = []

    for promo_summary in active_promos:
        promo_id = promo_summary.get("promotionId") or (promo_summary.get("promotionHref") or "").split("/")[-1]
        if not promo_id:
            continue
        promo_status = promo_summary.get("promotionStatus", "")
        promo_name = promo_summary.get("name", "")
        start_date = (promo_summary.get("startDate") or "")[:10]
        end_date = (promo_summary.get("endDate") or "")[:10]

        try:
            detail = get_item_price_markdown(cfg, promo_id)
        except Exception as exc:
            log.warning("promo sync: could not fetch detail for %s: %s", promo_id, exc)
            errors.append(f"{promo_id}: {exc}")
            continue

        discounts = detail.get("selectedInventoryDiscounts") or []
        discount_pct_raw = 0
        if discounts:
            benefit = (discounts[0].get("discountBenefit") or {})
            try:
                discount_pct_raw = int(benefit.get("percentageOffList") or 0)
            except (ValueError, TypeError):
                pass

        for discount in discounts:
            criterion = discount.get("inventoryCriterion") or {}
            inventory_items = criterion.get("inventoryItems") or []
            for inv in inventory_items:
                listing_id = str(inv.get("listingId") or "")
                if not listing_id:
                    continue
                sku = listing_index.get(listing_id)
                if not sku:
                    not_found.append(listing_id)
                    continue

                promo_block = {
                    "promo_id": promo_id,
                    "event_name": promo_name,
                    "discount_pct": discount_pct_raw,
                    "start_date": start_date,
                    "end_date": end_date,
                    "promo_status": promo_status,
                    "synced_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
                try:
                    fence_patch_item(cfg, sku, {"ebay_promo": promo_block})
                    blocks_written += 1
                    log.debug("promo sync: wrote block for %s (listing %s, promo %s)",
                              sku, listing_id, promo_id)
                except Exception as exc:
                    log.error("promo sync: failed to write block for %s: %s", sku, exc)
                    errors.append(f"{sku}: {exc}")

    return {
        "ok": True,
        "scope_verified": True,
        "active_promos": len(active_promos),
        "blocks_written": blocks_written,
        "not_found_listing_ids": len(not_found),
        "errors": errors,
        "not_found_sample": not_found[:10],
    }
