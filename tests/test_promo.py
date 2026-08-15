"""Tests for tgw.promo — PP-PROMO-001 Phase 2 (read-only draft + scope check).

All tests are offline; no live eBay API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

from tgw.promo import (
    _discounted_price,
    _floor_for_group,
    _render_promo_draft,
    _scan_promo_candidates,
    cmd_promo_draft,
    cmd_promo_list,
    cmd_promo_sync,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_item_dir(root: Path, sku: str, data: dict) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku, **data}), encoding="utf-8")
    return d


_REPRICE_DONE = [
    {"label": "launch", "stage": 0, "price": 25.0, "done_at": "2026-01-01T00:00:00Z"},
    {"label": "retail", "stage": 1, "price": 20.0, "done_at": "2026-01-04T00:00:00Z"},
    {"label": "move", "stage": 2, "price": 15.0, "done_at": "2026-01-18T00:00:00Z"},
]

_REPRICE_PARTIAL = [
    {"label": "launch", "stage": 0, "price": 25.0, "done_at": "2026-01-01T00:00:00Z"},
    {"label": "retail", "stage": 1, "price": 20.0, "done_at": None},
]


def _dead_item(listing_id: str = "123456789012", price: float = 24.99, **extra) -> dict:
    """Build a dead-stock item (all reprice stages done, has listing_id)."""
    base = {
        "status": "active",
        "title": "Test Item",
        "location": "A1",
        "ebay_listing": {"listing_id": listing_id, "price": str(price)},
        "reprice_schedule": _REPRICE_DONE,
    }
    base.update(extra)
    return base


def _make_cfg(tmp_path: Path, *, promo_enabled: bool = True) -> Dict[str, Any]:
    itemdata = tmp_path / "ItemData"
    itemdata.mkdir()
    plan_vault = tmp_path / "vault"
    (plan_vault / "inbox").mkdir(parents=True)
    return {
        "itemdata_root": itemdata,
        "plan_vault_path": plan_vault,
        "promo": {
            "enabled": promo_enabled,
            "min_days_stale": 30,
            "min_price": 2.00,
            "max_items": 50,
            "discount_pct": 20,
            "duration_days": 30,
            "start_offset_days": 2,
            "marketplace_id": "EBAY_US",
        },
    }


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestDiscountedPrice:
    def test_twenty_pct_off(self):
        assert _discounted_price(24.99, 20) == pytest.approx(19.992, abs=0.01)

    def test_example_from_design_doc(self):
        # 20% off $34.99 ≈ $27.99
        assert _discounted_price(34.99, 20) == pytest.approx(27.992, abs=0.01)

    def test_zero_discount(self):
        assert _discounted_price(10.00, 0) == 10.00

    def test_rounding(self):
        assert _discounted_price(10.00, 20) == 8.00


class TestFloorForGroup:
    def test_returns_floor(self):
        groups = {"electronics": {"pricing": {"floor": 5.00}}}
        assert _floor_for_group("electronics", groups) == 5.00

    def test_missing_group_returns_none(self):
        assert _floor_for_group("unknown", {}) is None

    def test_group_without_pricing_returns_none(self):
        groups = {"electronics": {}}
        assert _floor_for_group("electronics", groups) is None

    def test_group_without_floor_key_returns_none(self):
        groups = {"electronics": {"pricing": {"typical_used": 15.00}}}
        assert _floor_for_group("electronics", groups) is None


# ---------------------------------------------------------------------------
# Candidate scan
# ---------------------------------------------------------------------------


class TestScanPromoCandidates:
    def test_no_listing_id_excluded_and_counted(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        _make_item_dir(
            itemdata,
            "tgw001",
            {
                "status": "active",
                "reprice_schedule": _REPRICE_DONE,
                # no ebay_listing → no listing_id
            },
        )
        rows, counts = _scan_promo_candidates(itemdata, {})
        assert rows == []
        assert counts["skipped_no_listing"] == 1
        assert counts["skipped_active_promo"] == 0

    def test_active_promo_excluded_and_counted(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        _make_item_dir(
            itemdata,
            "tgw001",
            {
                "status": "active",
                "reprice_schedule": _REPRICE_DONE,
                "ebay_listing": {"listing_id": "111111111111"},
                "ebay_promo": {"promo_id": "5xxxxxxxxxxx"},
            },
        )
        rows, counts = _scan_promo_candidates(itemdata, {})
        assert rows == []
        assert counts["skipped_active_promo"] == 1

    def test_promo_skip_flag_excluded(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        _make_item_dir(itemdata, "tgw001", {**_dead_item(), "promo_skip": True})
        rows, counts = _scan_promo_candidates(itemdata, {})
        assert rows == []
        assert counts["skipped_no_listing"] == 0

    def test_sold_status_excluded(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        _make_item_dir(
            itemdata,
            "tgw001",
            {
                "status": "sold",
                "reprice_schedule": _REPRICE_DONE,
                "ebay_listing": {"listing_id": "111111111111"},
            },
        )
        rows, counts = _scan_promo_candidates(itemdata, {})
        assert rows == []

    def test_partial_reprice_excluded(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        _make_item_dir(
            itemdata,
            "tgw001",
            {
                "status": "active",
                "reprice_schedule": _REPRICE_PARTIAL,
                "ebay_listing": {"listing_id": "111111111111"},
            },
        )
        rows, counts = _scan_promo_candidates(itemdata, {})
        assert rows == []

    def test_eligible_item_included(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        _make_item_dir(itemdata, "tgw001", _dead_item("111111111111", price=24.99))
        rows, counts = _scan_promo_candidates(itemdata, {})
        assert len(rows) == 1
        assert rows[0]["listing_id"] == "111111111111"
        assert rows[0]["price"] == 24.99

    def test_sorted_oldest_first(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        # Item 1: last stage 2026-01-01 (older)
        _make_item_dir(
            itemdata,
            "tgw001",
            {
                "status": "active",
                "ebay_listing": {"listing_id": "111111111111", "price": "10.00"},
                "reprice_schedule": [
                    {"label": "move", "stage": 2, "done_at": "2026-01-01T00:00:00Z"},
                ],
            },
        )
        # Item 2: last stage 2026-03-01 (newer)
        _make_item_dir(
            itemdata,
            "tgw002",
            {
                "status": "active",
                "ebay_listing": {"listing_id": "222222222222", "price": "10.00"},
                "reprice_schedule": [
                    {"label": "move", "stage": 2, "done_at": "2026-03-01T00:00:00Z"},
                ],
            },
        )
        rows, _ = _scan_promo_candidates(itemdata, {})
        assert len(rows) == 2
        # Oldest stale (highest days_stale) should be first
        assert rows[0]["listing_id"] == "111111111111"

    def test_other_excluded_statuses(self, tmp_path):
        itemdata = tmp_path / "ItemData"
        itemdata.mkdir()
        for i, status in enumerate(["archived", "disposed", "draft", "vero", "merged"]):
            _make_item_dir(
                itemdata,
                f"tgw00{i}",
                {
                    "status": status,
                    "reprice_schedule": _REPRICE_DONE,
                    "ebay_listing": {"listing_id": f"11111111111{i}"},
                },
            )
        rows, _ = _scan_promo_candidates(itemdata, {})
        assert rows == []


# ---------------------------------------------------------------------------
# Draft filters (cmd_promo_draft)
# ---------------------------------------------------------------------------


class TestPromoDraftFilters:
    def test_disabled_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path, promo_enabled=False)
        result = cmd_promo_draft(cfg, no_vault=True)
        assert result["ok"] is False
        assert "enabled" in result["error"]

    def test_min_days_stale_filter(self, tmp_path):
        import datetime as _dt

        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        # Stale item: last done_at 2026-01-18 → ~146 days from 2026-06-13
        _make_item_dir(itemdata, "tgw001", _dead_item("111111111111", price=10.00))
        # Fresh item: last done_at 5 days ago → below min_days
        recent = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _make_item_dir(
            itemdata,
            "tgw002",
            {
                "status": "active",
                "title": "Recent Item",
                "ebay_listing": {"listing_id": "222222222222", "price": "10.00"},
                "reprice_schedule": [
                    {"label": "launch", "stage": 0, "done_at": "2026-01-01T00:00:00Z"},
                    {"label": "move", "stage": 2, "done_at": recent},
                ],
            },
        )
        result = cmd_promo_draft(cfg, min_days=30, no_vault=True)
        assert result["ok"] is True
        assert result["filtered_count"] == 1
        assert result["total_candidates"] == 2

    def test_min_price_filter(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        _make_item_dir(itemdata, "tgw001", _dead_item("111111111111", price=1.00))
        _make_item_dir(itemdata, "tgw002", _dead_item("222222222222", price=10.00))
        result = cmd_promo_draft(cfg, min_price=2.00, no_vault=True)
        assert result["ok"] is True
        assert result["filtered_count"] == 1

    def test_max_items_cap(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        for i in range(5):
            _make_item_dir(itemdata, f"tgw00{i}", _dead_item(f"11111111111{i}", price=10.00))
        result = cmd_promo_draft(cfg, max_items=3, no_vault=True)
        assert result["ok"] is True
        assert result["filtered_count"] == 3

    def test_discount_below_range_rejected(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_promo_draft(cfg, discount=4, no_vault=True)
        assert result["ok"] is False
        assert "5" in result["error"] and "80" in result["error"]

    def test_discount_above_range_rejected(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_promo_draft(cfg, discount=81, no_vault=True)
        assert result["ok"] is False

    def test_empty_itemdata_returns_ok(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_promo_draft(cfg, no_vault=True)
        assert result["ok"] is True
        assert result["filtered_count"] == 0


# ---------------------------------------------------------------------------
# Draft output structure
# ---------------------------------------------------------------------------


class TestPromoDraftOutput:
    def test_writes_draft_file_to_inbox(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        _make_item_dir(itemdata, "tgw001", _dead_item("123456789012", price=24.99))
        result = cmd_promo_draft(cfg)
        assert result["ok"] is True
        assert result["draft_path"] is not None
        draft = Path(result["draft_path"])
        assert draft.exists()
        assert "promo-" in draft.name

    def test_yaml_frontmatter_present(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        _make_item_dir(itemdata, "tgw001", _dead_item("123456789012"))
        result = cmd_promo_draft(cfg)
        text = Path(result["draft_path"]).read_text()
        assert "pp: PP-PROMO-001" in text
        assert "discount_pct: 20" in text
        assert "marketplace: EBAY_US" in text
        assert "status: DRAFT" in text
        assert "start_date:" in text
        assert "end_date:" in text

    def test_sku_and_listing_id_in_table(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        _make_item_dir(itemdata, "tgw20260115120000123", _dead_item("123456789012", price=24.99))
        result = cmd_promo_draft(cfg)
        text = Path(result["draft_path"]).read_text()
        assert "tgw20260115120000123" in text
        assert "123456789012" in text
        assert "$24.99" in text

    def test_no_vault_returns_none_path(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_promo_draft(cfg, no_vault=True)
        assert result["ok"] is True
        assert result["draft_path"] is None

    def test_operator_instructions_present(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        _make_item_dir(itemdata, "tgw001", _dead_item("123456789012"))
        result = cmd_promo_draft(cfg)
        text = Path(result["draft_path"]).read_text()
        assert "Operator Instructions" in text

    def test_custom_output_dir(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        out_dir = tmp_path / "custom-out"
        result = cmd_promo_draft(cfg, output_dir=str(out_dir))
        assert result["ok"] is True
        assert result["draft_path"] is not None
        assert str(out_dir) in result["draft_path"]
        assert Path(result["draft_path"]).exists()

    def test_skipped_counts_in_result(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        itemdata = cfg["itemdata_root"]
        # Item with no listing_id → skipped_no_listing
        _make_item_dir(
            itemdata,
            "tgw001",
            {
                "status": "active",
                "reprice_schedule": _REPRICE_DONE,
            },
        )
        # Item with active promo → skipped_active_promo
        _make_item_dir(
            itemdata,
            "tgw002",
            {
                "status": "active",
                "reprice_schedule": _REPRICE_DONE,
                "ebay_listing": {"listing_id": "111111111111"},
                "ebay_promo": {"promo_id": "5abc"},
            },
        )
        result = cmd_promo_draft(cfg, no_vault=True)
        assert result["ok"] is True
        assert result["skipped_no_listing"] == 1
        assert result["skipped_active_promo"] == 1


# ---------------------------------------------------------------------------
# Floor annotation (tested via _render_promo_draft directly)
# ---------------------------------------------------------------------------


_RENDER_PARAMS = dict(
    event_name="TGW Dead Stock Clearance — 2026-06",
    discount_pct=20,
    start_date="2026-06-15",
    end_date="2026-07-15",
    marketplace_id="EBAY_US",
    generated_at="2026-06-13T00:00:00Z",
    scan_counts={"skipped_no_listing": 0, "skipped_active_promo": 0},
)


def _make_row(price: float, group: str = "electronics") -> dict:
    return {
        "sku": "tgw001",
        "title": "Test Item",
        "group": group,
        "days_stale": 60,
        "last_stage": "move",
        "price": price,
        "listing_id": "123456789012",
    }


class TestPromoDraftFloorAnnotation:
    def test_below_floor_flagged(self):
        # 20% off $30.00 = $24.00 < floor $25.00 → ⚠FLOOR
        groups_by_key = {"electronics": {"pricing": {"floor": 25.00}}}
        text = _render_promo_draft(
            [_make_row(30.00)],
            groups_by_key=groups_by_key,
            **_RENDER_PARAMS,
        )
        assert "⚠FLOOR" in text

    def test_at_floor_not_flagged(self):
        # 20% off $31.25 = $25.00 = floor $25.00 → no flag
        groups_by_key = {"electronics": {"pricing": {"floor": 25.00}}}
        text = _render_promo_draft(
            [_make_row(31.25)],
            groups_by_key=groups_by_key,
            **_RENDER_PARAMS,
        )
        assert "⚠FLOOR" not in text

    def test_above_floor_not_flagged(self):
        # 20% off $30.00 = $24.00 > floor $5.00 → no flag
        groups_by_key = {"electronics": {"pricing": {"floor": 5.00}}}
        text = _render_promo_draft(
            [_make_row(30.00)],
            groups_by_key=groups_by_key,
            **_RENDER_PARAMS,
        )
        assert "⚠FLOOR" not in text

    def test_no_groups_no_crash_or_flag(self):
        text = _render_promo_draft(
            [_make_row(24.99, group="uncategorized")],
            groups_by_key={},
            **_RENDER_PARAMS,
        )
        assert "⚠FLOOR" not in text

    def test_multiple_items_only_failing_flagged(self):
        groups_by_key = {"electronics": {"pricing": {"floor": 25.00}}}
        rows = [
            _make_row(30.00),  # 24.00 < 25.00 → FLOOR
            {**_make_row(50.00), "sku": "tgw002", "listing_id": "234567890123"},  # 40.00 > 25.00
        ]
        text = _render_promo_draft(rows, groups_by_key=groups_by_key, **_RENDER_PARAMS)
        assert text.count("⚠FLOOR") == 1

    def test_empty_items_no_table(self):
        text = _render_promo_draft([], groups_by_key={}, **_RENDER_PARAMS)
        assert "No eligible items" in text
        assert "⚠FLOOR" not in text


# ---------------------------------------------------------------------------
# promo list — scope check (mocked)
# ---------------------------------------------------------------------------


class TestPromoListMocked:
    def test_success_returns_scope_verified(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        mock_data = {
            "promotions": [
                {"promotionId": "5abc", "name": "Test Promo", "promotionStatus": "DRAFT"},
            ]
        }
        with patch("tgw.apis.ebay.client.ebay_get", return_value=mock_data):
            result = cmd_promo_list(cfg)
        assert result["ok"] is True
        assert result["scope_verified"] is True
        assert result["promotion_count"] == 1
        assert result["promotions"][0]["promotionId"] == "5abc"

    def test_empty_list_still_scope_verified(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        with patch("tgw.apis.ebay.client.ebay_get", return_value={}):
            result = cmd_promo_list(cfg)
        assert result["ok"] is True
        assert result["scope_verified"] is True
        assert result["promotion_count"] == 0
        assert result["promotions"] == []

    def test_403_reports_scope_not_verified(self, tmp_path):
        import requests as _requests

        cfg = _make_cfg(tmp_path)
        mock_resp = _requests.Response()
        mock_resp.status_code = 403
        http_err = _requests.HTTPError(response=mock_resp)
        with patch("tgw.apis.ebay.client.ebay_get", side_effect=http_err):
            result = cmd_promo_list(cfg)
        assert result["ok"] is False
        assert result["scope_verified"] is False
        assert result["status_code"] == 403
        assert "sell.marketing" in result["error"]

    def test_other_http_error_propagated(self, tmp_path):
        import requests as _requests

        cfg = _make_cfg(tmp_path)
        mock_resp = _requests.Response()
        mock_resp.status_code = 500
        http_err = _requests.HTTPError(response=mock_resp)
        with patch("tgw.apis.ebay.client.ebay_get", side_effect=http_err):
            result = cmd_promo_list(cfg)
        assert result["ok"] is False
        assert result.get("scope_verified") is None  # not set for non-403
        assert result["status_code"] == 500

    def test_marketplace_id_in_result(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        with patch("tgw.apis.ebay.client.ebay_get", return_value={}):
            result = cmd_promo_list(cfg)
        assert result["marketplace_id"] == "EBAY_US"


# ---------------------------------------------------------------------------
# promo sync — promotionId absent / promotionHref None (todo #1296)
# ---------------------------------------------------------------------------


class TestPromoSyncNullHref:
    """Regression tests for the AttributeError fixed in todo #1296:
    `promo_summary.get("promotionHref", "")` returned None (not the
    default) when the key was present with an explicit `null` value,
    and `.split("/")` on None crashed the whole sync loop.
    """

    def test_both_id_and_href_absent_or_none_skips_without_crash(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        promos = [
            {
                "promotionId": None,
                "promotionHref": None,
                "promotionStatus": "RUNNING",
                "name": "Broken Promo",
            }
        ]
        with (
            patch("tgw.apis.ebay.promotions.list_item_price_markdowns", return_value=promos),
            patch("tgw.apis.ebay.promotions.get_item_price_markdown") as mock_detail,
        ):
            result = cmd_promo_sync(cfg)
        # Must not raise AttributeError; the entry is skipped via `if not promo_id: continue`.
        assert result["ok"] is True
        assert result["blocks_written"] == 0
        mock_detail.assert_not_called()

    def test_promotion_id_present_used_directly(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        promos = [
            {
                "promotionId": "abc123",
                "promotionHref": None,
                "promotionStatus": "RUNNING",
                "name": "Normal Promo",
            }
        ]
        with (
            patch("tgw.apis.ebay.promotions.list_item_price_markdowns", return_value=promos),
            patch(
                "tgw.apis.ebay.promotions.get_item_price_markdown",
                return_value={"selectedInventoryDiscounts": []},
            ) as mock_detail,
        ):
            result = cmd_promo_sync(cfg)
        assert result["ok"] is True
        mock_detail.assert_called_once_with(cfg, "abc123")

    def test_href_fallback_used_when_id_absent(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        promos = [
            {
                "promotionId": None,
                "promotionHref": "https://api.ebay.com/sell/marketing/v1/item_price_markdown/PROMO-456",
                "promotionStatus": "SCHEDULED",
                "name": "Href-only Promo",
            }
        ]
        with (
            patch("tgw.apis.ebay.promotions.list_item_price_markdowns", return_value=promos),
            patch(
                "tgw.apis.ebay.promotions.get_item_price_markdown",
                return_value={"selectedInventoryDiscounts": []},
            ) as mock_detail,
        ):
            result = cmd_promo_sync(cfg)
        assert result["ok"] is True
        mock_detail.assert_called_once_with(cfg, "PROMO-456")
