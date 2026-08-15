"""
tgw.reports — Sales and inventory report generation (PP-DOCFLOW-001 Phase-3 seed).

tgw report sales [--stale] [--output DIR]
  monthly units/revenue by category-group, sell-through, days-to-sale,
  price-stage-at-sale from ebay_sale + velocity data; dead-stock ranking.
  Writes markdown + CSV artifacts to vault (dev-workflow/research/).

Read-only: no item JSON writes.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers (re-use velocity.py date/days logic without importing — keep thin)
# ---------------------------------------------------------------------------


def _parse_date(raw: str) -> Optional[datetime]:
    """Parse ISO-8601 or 'Mon-DD-YY' date string."""
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%b-%d-%y",
    ):
        try:
            dt = datetime.strptime(raw.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _days_between(earlier: Optional[datetime], later: Optional[datetime]) -> Optional[float]:
    if earlier is None or later is None:
        return None
    return max(0.0, (later - earlier).total_seconds() / 86400.0)


def _pub_date(item: Dict[str, Any]) -> Optional[datetime]:
    """First publication date — launch stage done_at, then ebay_listing.published_at."""
    for s in item.get("reprice_schedule", []):
        if s.get("label") == "launch" and s.get("done_at"):
            dt = _parse_date(str(s["done_at"]))
            if dt:
                return dt
    for path in (
        (item.get("ebay_listing") or {}).get("published_at"),
        (item.get("ebay_offer") or {}).get("published_at"),
    ):
        if path:
            dt = _parse_date(str(path))
            if dt:
                return dt
    return None


def _sold_stage(item: Dict[str, Any], sale_dt: datetime) -> str:
    """Reprice stage label at time of sale ('launch', 'retail', 'move', 'unknown')."""
    schedule = item.get("reprice_schedule", [])
    if not schedule:
        return "unknown"
    active = [
        s for s in schedule
        if s.get("done_at") and _parse_date(str(s["done_at"])) and _parse_date(str(s["done_at"])) <= sale_dt
    ]
    if not active:
        return "unknown"
    return str(max(active, key=lambda s: s.get("stage", 0)).get("label", "unknown"))


def _coerce_price(raw: Any) -> Optional[float]:
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v if v > 0 else None
    if isinstance(raw, str):
        try:
            return float(raw.lstrip("$").replace(",", "")) or None
        except (ValueError, AttributeError):
            return None
    return None


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return round(s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0, 2)


def _pct(n: int, total: int) -> str:
    if not total:
        return "—"
    return f"{round(100.0 * n / total, 1):.1f}%"


# ---------------------------------------------------------------------------
# Category-group resolution
# ---------------------------------------------------------------------------


def _build_group_index(cfg: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Returns (cat_id_to_group_key, group_key_to_name).
    Uses category-groups.json; returns empty dicts if unavailable.
    """
    cat_id_to_key: Dict[str, str] = {}
    key_to_name: Dict[str, str] = {}
    try:
        from tgw.ebay.pricing import _load_groups

        data = _load_groups(cfg)
        for key, grp in data.get("groups", {}).items():
            key_to_name[key] = grp.get("name") or key
            for cat_id in grp.get("ebay_categories", []):
                cat_id_to_key[str(cat_id)] = key
    except Exception as exc:
        log.debug("report: could not load category groups: %s", exc)
    return cat_id_to_key, key_to_name


def _item_group(item: Dict[str, Any], cat_id_to_key: Dict[str, str]) -> str:
    """Resolve category group key for an item."""
    if item.get("category_group"):
        return str(item["category_group"])
    # Try to resolve from eBay category IDs
    for cat_id_src in (
        (item.get("draft_listing") or {}).get("category_id"),
        item.get("ebay_category_id"),
        item.get("eBay category 1 number"),
    ):
        if cat_id_src:
            key = cat_id_to_key.get(str(cat_id_src).strip())
            if key:
                return key
    return "uncategorized"


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def _scan_items(
    itemdata_root: Path,
    cat_id_to_key: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    """
    Walk ItemData once and return:
      sold_rows:   [{month, group, price, days_to_sale, stage}, ...]
      dead_stock:  [{sku, title, location, group, days_since_last_reprice, last_stage, price}, ...]
      total_items: int
    """
    sold_rows: List[Dict[str, Any]] = []
    dead_stock: List[Dict[str, Any]] = []
    total = 0

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
        total += 1

        status = str(item.get("status", "")).lower().strip()
        group = _item_group(item, cat_id_to_key)

        if status == "sold":
            # ebay_sale is a list of sold-order records (todo #1604 /
            # PP-SOLD-001) — a SKU can carry more than one distinct order
            # (multi-qty, or an "oversold" extra order). Legacy items
            # written before that fix may still carry a single dict; treat
            # that as a one-element list rather than dropping the data.
            raw_sales = item.get("ebay_sale") or []
            if isinstance(raw_sales, dict):
                raw_sales = [raw_sales] if raw_sales else []
            elif not isinstance(raw_sales, list):
                raw_sales = []
            pub_dt = _pub_date(item)
            for sale in raw_sales:
                if not isinstance(sale, dict):
                    continue
                sale_date_raw = str(sale.get("sale_date") or "")
                sale_dt = _parse_date(sale_date_raw)
                if sale_dt is None:
                    continue
                month = sale_dt.strftime("%Y-%m")
                price = _coerce_price(sale.get("sale_price"))
                days = _days_between(pub_dt, sale_dt)
                stage = _sold_stage(item, sale_dt)
                sold_rows.append({
                    "month": month,
                    "group": group,
                    "price": price,
                    "days_to_sale": days,
                    "stage": stage,
                })

        elif status not in ("archived", "disposed", "recalled", "merged",
                            "discard", "disposeddisposed", "vero", "draft"):
            # Active / in-stock: check for stale (all reprice stages completed)
            schedule = item.get("reprice_schedule", [])
            if not schedule:
                continue
            all_done = all(s.get("done_at") is not None for s in schedule)
            if not all_done:
                continue

            done_dts = [_parse_date(str(s["done_at"])) for s in schedule if s.get("done_at")]
            done_dts = [d for d in done_dts if d is not None]
            last_reprice_dt = max(done_dts) if done_dts else None
            days_stale = _days_between(last_reprice_dt, now)

            last_stage = str(max(schedule, key=lambda s: s.get("stage", 0)).get("label", ""))
            price_raw = (item.get("ebay_offer") or {}).get("price") or \
                        (item.get("ebay_listing") or {}).get("live_price")

            dead_stock.append({
                "sku": sku_dir.name,
                "title": str(item.get("title") or "")[:60],
                "location": str(item.get("location") or "").strip(),
                "group": group,
                "days_stale": round(days_stale, 0) if days_stale is not None else None,
                "last_stage": last_stage,
                "price": _coerce_price(price_raw),
            })

    # Dead stock: oldest first (most urgent)
    dead_stock.sort(key=lambda r: (r["days_stale"] is None, -(r["days_stale"] or 0)))
    return sold_rows, dead_stock, total


# ---------------------------------------------------------------------------
# Pivot: monthly × category-group
# ---------------------------------------------------------------------------


def _build_monthly_pivot(
    sold_rows: List[Dict[str, Any]],
    key_to_name: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Aggregate sold_rows into monthly × group summary rows."""
    # bucket key: (month, group)
    buckets: Dict[Tuple[str, str], Dict[str, Any]] = defaultdict(lambda: {
        "prices": [], "days": [],
        "launch": 0, "retail": 0, "move": 0, "unknown": 0,
    })
    for r in sold_rows:
        k = (r["month"], r["group"])
        b = buckets[k]
        if r["price"] is not None:
            b["prices"].append(r["price"])
        if r["days_to_sale"] is not None:
            b["days"].append(r["days_to_sale"])
        stage = r["stage"]
        if stage in b:
            b[stage] += 1
        else:
            b["unknown"] += 1

    rows = []
    for (month, group), b in sorted(buckets.items()):
        total_stage = b["launch"] + b["retail"] + b["move"] + b["unknown"]
        rows.append({
            "month": month,
            "group_key": group,
            "group_name": key_to_name.get(group, group),
            "units": total_stage,
            "revenue": round(sum(b["prices"]), 2),
            "avg_price": round(sum(b["prices"]) / len(b["prices"]), 2) if b["prices"] else None,
            "median_days_to_sale": _median(b["days"]),
            "pct_launch": _pct(b["launch"], total_stage),
            "pct_retail": _pct(b["retail"], total_stage),
            "pct_move": _pct(b["move"], total_stage),
            "pct_unknown": _pct(b["unknown"], total_stage),
        })
    return rows


# ---------------------------------------------------------------------------
# Markdown + CSV rendering
# ---------------------------------------------------------------------------


def _fmt_price(v: Optional[float]) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _fmt_days(v: Optional[float]) -> str:
    return f"{v:.0f}" if v is not None else "—"


def render_markdown(
    monthly: List[Dict[str, Any]],
    dead_stock: List[Dict[str, Any]],
    generated_at: str,
    total_items: int,
    *,
    stale_only: bool = False,
) -> str:
    """Render a full sales report as Markdown text."""
    lines: List[str] = []
    lines.append("# TGW Sales Report")
    lines.append("")
    lines.append(f"Generated: {generated_at}  |  Total items scanned: {total_items:,}")
    lines.append("")

    if not stale_only:
        if monthly:
            lines.append("## Monthly Sales by Category Group")
            lines.append("")
            lines.append("| Month | Category Group | Units | Revenue | Avg Price | Median Days | Launch% | Retail% | Move% |")
            lines.append("|-------|---------------|------:|--------:|----------:|------------:|--------:|--------:|------:|")
            for r in monthly:
                lines.append(
                    f"| {r['month']} | {r['group_name']} "
                    f"| {r['units']} "
                    f"| {_fmt_price(r['revenue'])} "
                    f"| {_fmt_price(r['avg_price'])} "
                    f"| {_fmt_days(r['median_days_to_sale'])} "
                    f"| {r['pct_launch']} "
                    f"| {r['pct_retail']} "
                    f"| {r['pct_move']} |"
                )
        else:
            lines.append("## Monthly Sales by Category Group")
            lines.append("")
            lines.append("_No sold items found with complete sale records._")

        lines.append("")

    # Dead stock section
    lines.append("## Dead-Stock Ranking")
    lines.append("")
    lines.append(f"Items past all reprice stages without selling ({len(dead_stock)} items):")
    lines.append("")
    if dead_stock:
        lines.append("| SKU | Title | Loc | Group | Days Stale | Last Stage | Price |")
        lines.append("|-----|-------|-----|-------|----------:|-----------|------:|")
        for r in dead_stock:
            lines.append(
                f"| `{r['sku']}` "
                f"| {r['title']} "
                f"| {r['location'] or '—'} "
                f"| {r['group']} "
                f"| {_fmt_days(r['days_stale'])} "
                f"| {r['last_stage'] or '—'} "
                f"| {_fmt_price(r['price'])} |"
            )
    else:
        lines.append("_No dead-stock items found._")

    lines.append("")
    lines.append("---")
    lines.append("_Report generated by `tgw report sales`._")
    return "\n".join(lines)


def render_csv(monthly: List[Dict[str, Any]]) -> str:
    """Render monthly pivot as CSV text."""
    if not monthly:
        return "month,group_key,group_name,units,revenue,avg_price,median_days_to_sale,pct_launch,pct_retail,pct_move,pct_unknown\n"
    out = io.StringIO()
    writer = csv.DictWriter(
        out,
        fieldnames=[
            "month", "group_key", "group_name", "units", "revenue",
            "avg_price", "median_days_to_sale",
            "pct_launch", "pct_retail", "pct_move", "pct_unknown",
        ],
    )
    writer.writeheader()
    writer.writerows(monthly)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Public command
# ---------------------------------------------------------------------------


def cmd_report_sales(
    cfg: Dict[str, Any],
    *,
    stale_only: bool = False,
    output_dir: Optional[str] = None,
    no_vault: bool = False,
) -> Dict[str, Any]:
    """
    Generate monthly sales report by category-group + dead-stock ranking.

    Scans ItemData for sold/active items; groups by category_group.
    Writes markdown + CSV to vault (dev-workflow/research/) unless no_vault=True.
    Returns {ok, monthly, dead_stock, total_items, report_path, csv_path}.
    """
    itemdata_root: Path = cfg["itemdata_root"]

    # Resolve category groups
    cat_id_to_key, key_to_name = _build_group_index(cfg)

    # Scan items
    sold_rows, dead_stock, total_items = _scan_items(itemdata_root, cat_id_to_key)

    # Build pivot
    monthly = _build_monthly_pivot(sold_rows, key_to_name)

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today = datetime.now().astimezone().strftime("%Y-%m-%d")

    # Render artifacts
    md_text = render_markdown(monthly, dead_stock, generated_at, total_items, stale_only=stale_only)
    csv_text = render_csv(monthly)

    result: Dict[str, Any] = {
        "ok": True,
        "total_items": total_items,
        "months": len({r["month"] for r in monthly}),
        "monthly_rows": len(monthly),
        "dead_stock_count": len(dead_stock),
        "monthly": monthly,
        "dead_stock": dead_stock,
        "generated_at": generated_at,
        "report_path": None,
        "csv_path": None,
    }

    if not no_vault:
        if output_dir:
            dest = Path(output_dir)
        else:
            plan_vault: Path = cfg.get("plan_vault_path") or Path("docs/TGW-Plan-Vault")
            dest = plan_vault / "dev-workflow" / "research"

        dest.mkdir(parents=True, exist_ok=True)

        md_path = dest / f"sales-report-{today}.md"
        csv_path = dest / f"sales-report-{today}.csv"

        md_path.write_text(md_text, encoding="utf-8")
        csv_path.write_text(csv_text, encoding="utf-8")

        result["report_path"] = str(md_path)
        result["csv_path"] = str(csv_path)

    return result
