"""Tests for tgw.reports — tgw report sales (PP-DOCFLOW-001 Phase-3 seed).

All I/O uses tmp_path; no eBay API calls; no velocity-stats.json dependency.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tgw.reports import (
    _build_monthly_pivot,
    _coerce_price,
    _item_group,
    _median,
    _parse_date,
    _scan_items,
    cmd_report_sales,
    render_csv,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item_dir(root: Path, sku: str, data: dict) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku, **data}), encoding="utf-8")
    return d


def _make_cfg(tmp_path: Path) -> dict:
    itemdata = tmp_path / "ItemData"
    itemdata.mkdir()
    plan_vault = tmp_path / "vault"
    (plan_vault / "dev-workflow" / "research").mkdir(parents=True)
    return {
        "itemdata_root": itemdata,
        "plan_vault_path": plan_vault,
        "pretty": False,
    }


_REPRICE = [
    {"label": "launch", "stage": 0, "price": 25.0, "due_at": "2026-01-01T00:00:00Z", "done_at": "2026-01-01T00:00:00Z"},
    {"label": "retail", "stage": 1, "price": 20.0, "due_at": "2026-01-04T00:00:00Z", "done_at": "2026-01-04T00:00:00Z"},
    {"label": "move",   "stage": 2, "price": 15.0, "due_at": "2026-01-18T00:00:00Z", "done_at": "2026-01-18T00:00:00Z"},
]

_REPRICE_PARTIAL = [
    {"label": "launch", "stage": 0, "price": 25.0, "due_at": "2026-01-01T00:00:00Z", "done_at": "2026-01-01T00:00:00Z"},
    {"label": "retail", "stage": 1, "price": 20.0, "due_at": "2026-01-04T00:00:00Z", "done_at": None},
]


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_with_tz(self):
        dt = _parse_date("2026-01-15T12:00:00Z")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 1 and dt.day == 15

    def test_iso_date_only(self):
        dt = _parse_date("2026-03-20")
        assert dt is not None
        assert dt.year == 2026 and dt.month == 3

    def test_ebay_csv_format(self):
        dt = _parse_date("Jan-15-26")
        assert dt is not None
        assert dt.month == 1 and dt.day == 15

    def test_empty_returns_none(self):
        assert _parse_date("") is None

    def test_garbage_returns_none(self):
        assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _coerce_price
# ---------------------------------------------------------------------------

class TestCoercePrice:
    def test_float(self):
        assert _coerce_price(19.99) == 19.99

    def test_int(self):
        assert _coerce_price(20) == 20.0

    def test_string_dollar(self):
        assert _coerce_price("$25.00") == 25.0

    def test_string_plain(self):
        assert _coerce_price("15.50") == 15.5

    def test_zero_returns_none(self):
        assert _coerce_price(0) is None

    def test_none_returns_none(self):
        assert _coerce_price(None) is None

    def test_garbage_returns_none(self):
        assert _coerce_price("abc") is None


# ---------------------------------------------------------------------------
# _median
# ---------------------------------------------------------------------------

class TestMedian:
    def test_odd(self):
        assert _median([1.0, 2.0, 3.0]) == 2.0

    def test_even(self):
        assert _median([1.0, 3.0]) == 2.0

    def test_empty(self):
        assert _median([]) is None

    def test_single(self):
        assert _median([5.0]) == 5.0


# ---------------------------------------------------------------------------
# _item_group
# ---------------------------------------------------------------------------

class TestItemGroup:
    def test_uses_category_group_field_first(self):
        item = {"category_group": "books", "ebay_category_id": "12345"}
        assert _item_group(item, {"12345": "electronics"}) == "books"

    def test_falls_back_to_cat_id(self):
        item = {"ebay_category_id": "12345"}
        assert _item_group(item, {"12345": "electronics"}) == "electronics"

    def test_falls_back_to_draft_listing_cat_id(self):
        item = {"draft_listing": {"category_id": "99999"}}
        assert _item_group(item, {"99999": "cameras"}) == "cameras"

    def test_uncategorized_when_no_match(self):
        assert _item_group({}, {}) == "uncategorized"


# ---------------------------------------------------------------------------
# _scan_items
# ---------------------------------------------------------------------------

class TestScanItems:
    def test_sold_item_produces_sold_row(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw001", {
            "status": "sold",
            "category_group": "books",
            "ebay_sale": {"sale_price": 15.0, "sale_date": "2026-01-15"},
            "reprice_schedule": [{"label": "launch", "stage": 0, "done_at": "2026-01-01T00:00:00Z"}],
        })
        sold, dead, total = _scan_items(root, {})
        assert len(sold) == 1
        assert sold[0]["month"] == "2026-01"
        assert sold[0]["price"] == 15.0
        assert sold[0]["group"] == "books"
        assert total == 1

    def test_sold_without_sale_date_skipped(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw001", {
            "status": "sold",
            "ebay_sale": {"sale_price": 15.0},  # no sale_date
        })
        sold, dead, total = _scan_items(root, {})
        assert len(sold) == 0

    def test_stale_item_produces_dead_stock_row(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw002", {
            "status": "live",
            "title": "Old camera",
            "location": "B2",
            "category_group": "cameras",
            "reprice_schedule": _REPRICE,
        })
        sold, dead, total = _scan_items(root, {})
        assert len(dead) == 1
        assert dead[0]["sku"] == "tgw002"
        assert dead[0]["group"] == "cameras"

    def test_partial_reprice_not_stale(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw003", {
            "status": "live",
            "reprice_schedule": _REPRICE_PARTIAL,
        })
        sold, dead, total = _scan_items(root, {})
        assert len(dead) == 0

    def test_active_no_reprice_not_stale(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw004", {"status": "live"})
        sold, dead, total = _scan_items(root, {})
        assert len(dead) == 0

    def test_sold_status_case_insensitive(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw005", {
            "status": "Sold",
            "category_group": "books",
            "ebay_sale": {"sale_price": 10.0, "sale_date": "2026-02-10"},
        })
        sold, _, _ = _scan_items(root, {})
        assert len(sold) == 1

    def test_empty_itemdata(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        sold, dead, total = _scan_items(root, {})
        assert sold == [] and dead == [] and total == 0

    def test_dead_stock_sorted_by_days_stale_descending(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        # Two stale items; one has all stages done in 2025, one in 2024
        for sku, done_year in [("tgw010", "2025"), ("tgw011", "2024")]:
            _make_item_dir(root, sku, {
                "status": "live",
                "reprice_schedule": [
                    {"label": "launch", "stage": 0, "done_at": f"{done_year}-06-01T00:00:00Z"},
                ],
            })
        _, dead, _ = _scan_items(root, {})
        assert dead[0]["sku"] == "tgw011"  # 2024 → more days stale

    def test_stage_launch_detected(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw006", {
            "status": "sold",
            "category_group": "books",
            "ebay_sale": {"sale_price": 20.0, "sale_date": "2026-01-02"},
            "reprice_schedule": [
                {"label": "launch", "stage": 0, "done_at": "2026-01-01T00:00:00Z"},
                {"label": "retail", "stage": 1, "done_at": None},
            ],
        })
        sold, _, _ = _scan_items(root, {})
        assert sold[0]["stage"] == "launch"

    def test_stage_move_detected(self, tmp_path):
        root = tmp_path / "ItemData"
        root.mkdir()
        _make_item_dir(root, "tgw007", {
            "status": "sold",
            "category_group": "electronics",
            "ebay_sale": {"sale_price": 15.0, "sale_date": "2026-01-20"},
            "reprice_schedule": _REPRICE,
        })
        sold, _, _ = _scan_items(root, {})
        assert sold[0]["stage"] == "move"


# ---------------------------------------------------------------------------
# _build_monthly_pivot
# ---------------------------------------------------------------------------

class TestBuildMonthlyPivot:
    def test_aggregates_same_month_and_group(self):
        sold = [
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": 5.0, "stage": "launch"},
            {"month": "2026-01", "group": "books", "price": 20.0, "days_to_sale": 10.0, "stage": "retail"},
        ]
        rows = _build_monthly_pivot(sold, {"books": "Books"})
        assert len(rows) == 1
        r = rows[0]
        assert r["units"] == 2
        assert r["revenue"] == 30.0
        assert r["avg_price"] == 15.0

    def test_separates_different_groups(self):
        sold = [
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": None, "stage": "launch"},
            {"month": "2026-01", "group": "cameras", "price": 50.0, "days_to_sale": 3.0, "stage": "retail"},
        ]
        rows = _build_monthly_pivot(sold, {})
        assert len(rows) == 2

    def test_separates_different_months(self):
        sold = [
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": None, "stage": "launch"},
            {"month": "2026-02", "group": "books", "price": 10.0, "days_to_sale": None, "stage": "launch"},
        ]
        rows = _build_monthly_pivot(sold, {})
        assert len(rows) == 2

    def test_empty_sold_returns_empty(self):
        assert _build_monthly_pivot([], {}) == []

    def test_median_days_computed(self):
        sold = [
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": 4.0, "stage": "launch"},
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": 8.0, "stage": "launch"},
        ]
        rows = _build_monthly_pivot(sold, {})
        assert rows[0]["median_days_to_sale"] == 6.0

    def test_stage_pcts_computed(self):
        sold = [
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": None, "stage": "launch"},
            {"month": "2026-01", "group": "books", "price": 10.0, "days_to_sale": None, "stage": "retail"},
        ]
        rows = _build_monthly_pivot(sold, {})
        r = rows[0]
        assert r["pct_launch"] == "50.0%"
        assert r["pct_retail"] == "50.0%"
        assert r["pct_move"] == "—"

    def test_group_name_resolved_from_key(self):
        sold = [{"month": "2026-01", "group": "books", "price": 5.0, "days_to_sale": None, "stage": "unknown"}]
        rows = _build_monthly_pivot(sold, {"books": "Books & Literature"})
        assert rows[0]["group_name"] == "Books & Literature"

    def test_items_without_price_counted_in_units(self):
        sold = [
            {"month": "2026-01", "group": "books", "price": None, "days_to_sale": None, "stage": "launch"},
            {"month": "2026-01", "group": "books", "price": None, "days_to_sale": None, "stage": "launch"},
        ]
        rows = _build_monthly_pivot(sold, {})
        assert rows[0]["units"] == 2
        assert rows[0]["revenue"] == 0.0


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def test_has_title(self):
        md = render_markdown([], [], "2026-01-01T00:00:00Z", 100)
        assert "# TGW Sales Report" in md

    def test_has_generated_at(self):
        md = render_markdown([], [], "2026-01-01T00:00:00Z", 100)
        assert "2026-01-01T00:00:00Z" in md

    def test_monthly_table_present_when_rows(self):
        monthly = [{"month": "2026-01", "group_key": "books", "group_name": "Books",
                    "units": 2, "revenue": 30.0, "avg_price": 15.0,
                    "median_days_to_sale": 5.0,
                    "pct_launch": "100.0%", "pct_retail": "—", "pct_move": "—", "pct_unknown": "—"}]
        md = render_markdown(monthly, [], "2026-01-01T00:00:00Z", 10)
        assert "Monthly Sales by Category Group" in md
        assert "2026-01" in md
        assert "Books" in md

    def test_dead_stock_section_present(self):
        dead = [{"sku": "tgw001", "title": "Old item", "location": "A1",
                  "group": "books", "days_stale": 90.0, "last_stage": "move", "price": 9.99}]
        md = render_markdown([], dead, "2026-01-01T00:00:00Z", 10)
        assert "Dead-Stock" in md
        assert "tgw001" in md
        assert "Old item" in md

    def test_stale_only_skips_monthly_table(self):
        monthly = [{"month": "2026-01", "group_key": "books", "group_name": "Books",
                    "units": 1, "revenue": 10.0, "avg_price": 10.0,
                    "median_days_to_sale": None,
                    "pct_launch": "—", "pct_retail": "—", "pct_move": "—", "pct_unknown": "—"}]
        md = render_markdown(monthly, [], "2026-01-01T00:00:00Z", 10, stale_only=True)
        assert "Monthly Sales" not in md
        assert "Dead-Stock" in md

    def test_empty_sold_no_monthly_table_rows(self):
        md = render_markdown([], [], "2026-01-01T00:00:00Z", 0)
        assert "No sold items" in md

    def test_empty_dead_stock_message(self):
        md = render_markdown([], [], "2026-01-01T00:00:00Z", 0)
        assert "No dead-stock" in md


# ---------------------------------------------------------------------------
# render_csv
# ---------------------------------------------------------------------------

class TestRenderCsv:
    def test_csv_has_header(self):
        csv_text = render_csv([])
        rows = list(csv.reader(csv_text.splitlines()))
        assert "month" in rows[0]
        assert "units" in rows[0]
        assert "revenue" in rows[0]

    def test_csv_has_data_rows(self):
        monthly = [{"month": "2026-01", "group_key": "books", "group_name": "Books",
                    "units": 2, "revenue": 30.0, "avg_price": 15.0,
                    "median_days_to_sale": 5.0,
                    "pct_launch": "100.0%", "pct_retail": "—", "pct_move": "—", "pct_unknown": "—"}]
        csv_text = render_csv(monthly)
        rows = list(csv.reader(csv_text.splitlines()))
        assert len(rows) == 2  # header + 1 data row
        assert "2026-01" in rows[1]

    def test_empty_monthly_produces_header_only(self):
        csv_text = render_csv([])
        rows = list(csv.reader(csv_text.splitlines()))
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# cmd_report_sales
# ---------------------------------------------------------------------------

class TestCmdReportSales:
    def _populate(self, cfg, sold_items=None, stale_items=None):
        root: Path = cfg["itemdata_root"]
        if sold_items:
            for sku, data in sold_items:
                _make_item_dir(root, sku, data)
        if stale_items:
            for sku, data in stale_items:
                _make_item_dir(root, sku, data)

    def test_returns_ok(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg, no_vault=True)
        assert result["ok"] is True

    def test_total_items_counted(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        root = cfg["itemdata_root"]
        _make_item_dir(root, "tgw001", {"status": "live"})
        _make_item_dir(root, "tgw002", {"status": "live"})
        result = cmd_report_sales(cfg, no_vault=True)
        assert result["total_items"] == 2

    def test_returns_monthly_list(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg, no_vault=True)
        assert isinstance(result["monthly"], list)

    def test_returns_dead_stock_list(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg, no_vault=True)
        assert isinstance(result["dead_stock"], list)

    def test_writes_markdown_to_vault(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg)
        assert result["report_path"] is not None
        assert Path(result["report_path"]).exists()
        content = Path(result["report_path"]).read_text(encoding="utf-8")
        assert content.startswith("# TGW Sales Report")

    def test_writes_csv_to_vault(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg)
        assert result["csv_path"] is not None
        assert Path(result["csv_path"]).exists()

    def test_no_vault_does_not_write_files(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg, no_vault=True)
        assert result["report_path"] is None
        assert result["csv_path"] is None

    def test_custom_output_dir(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        custom_dir = tmp_path / "custom-output"
        result = cmd_report_sales(cfg, output_dir=str(custom_dir))
        assert Path(result["report_path"]).parent == custom_dir

    def test_stale_only_flag_passed_through(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg)
        md_text = Path(result["report_path"]).read_text()
        assert "Monthly Sales" in md_text

        result2 = cmd_report_sales(cfg, stale_only=True)
        md_text2 = Path(result2["report_path"]).read_text()
        assert "Monthly Sales" not in md_text2
        assert "Dead-Stock" in md_text2

    def test_sold_item_appears_in_monthly(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item_dir(cfg["itemdata_root"], "tgw001", {
            "status": "sold",
            "category_group": "books",
            "ebay_sale": {"sale_price": 15.0, "sale_date": "2026-01-15"},
        })
        result = cmd_report_sales(cfg, no_vault=True)
        assert result["monthly_rows"] >= 1
        groups = [r["group_key"] for r in result["monthly"]]
        assert "books" in groups

    def test_stale_item_appears_in_dead_stock(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item_dir(cfg["itemdata_root"], "tgw002", {
            "status": "live",
            "title": "Stale item",
            "category_group": "cameras",
            "reprice_schedule": _REPRICE,
        })
        result = cmd_report_sales(cfg, no_vault=True)
        assert result["dead_stock_count"] >= 1
        skus = [r["sku"] for r in result["dead_stock"]]
        assert "tgw002" in skus

    def test_report_path_uses_today_date(self, tmp_path):
        from datetime import datetime
        cfg = _make_cfg(tmp_path)
        result = cmd_report_sales(cfg)
        today = datetime.now().strftime("%Y-%m-%d")
        assert today in result["report_path"]
        assert today in result["csv_path"]
