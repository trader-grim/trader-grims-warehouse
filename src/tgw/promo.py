"""
tgw.promo — Sale event automation (PP-PROMO-001 P2).

P2 (read-only):
  cmd_promo_draft() — scan dead_stock, apply filters, write markdown draft
  cmd_promo_list()  — GET Promotions API to verify sell.marketing scope
"""

from __future__ import annotations

import json
import logging
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
            plan_vault: Path = cfg.get("plan_vault_path") or Path("docs/TGW-Plan-Vault")
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
