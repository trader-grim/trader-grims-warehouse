"""Tests for cmd_create_item (PP-INTAKE-001 Phase 2.5 — tgw create-item).

All file I/O uses tmp_path; KDE Connect and context compat symlinks are mocked.
Tests pass completely offline.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

import tgw.api as api
import tgw.context as ctx_mod
import tgw.ebay.pricing as pricing

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

GROUPS = {
    "groups": {
        "books": {
            "name": "Books",
            "size_class": "small",
            "ai_hint": "printed book",
            "ebay_categories": ["261186"],
            "pricing": {"typical_used": 8.0},
        },
        "electronics": {
            "name": "Electronics",
            "size_class": "medium",
            "ai_hint": "electronic device",
            "ebay_categories": ["58058"],
            "pricing": {},
        },
    }
}

_TS1 = datetime(2026, 6, 12, 14, 30, 0, 123000)   # → tgw202606121430001
_TS2 = datetime(2026, 6, 12, 14, 30, 0, 456000)   # → tgw202606121430004
_TS3 = datetime(2026, 6, 12, 14, 30, 0, 789000)   # → tgw202606121430789


@pytest.fixture(autouse=True)
def _patch_compat_links(tmp_path, monkeypatch):
    """Redirect CurrentItem compat symlinks to tmp_path so tests don't touch /opt/TGW."""
    monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", tmp_path / "CurrentItem")
    monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM_JSON", tmp_path / "CurrentItem.json")


@pytest.fixture(autouse=True)
def _patch_groups(monkeypatch):
    """Always serve the test GROUPS fixture instead of reading from disk."""
    monkeypatch.setattr(pricing, "_load_groups", lambda cfg: GROUPS)
    # Also clear the module-level cache so each test starts clean
    monkeypatch.setattr(pricing, "_groups_cache", None)
    monkeypatch.setattr(pricing, "_groups_reverse", {})


def _make_cfg(tmp_path: Path) -> dict:
    itemdata = tmp_path / "ItemData"
    itemdata.mkdir()
    runtime = tmp_path / "runtime"
    (runtime / "state").mkdir(parents=True)
    return {
        "itemdata_root": itemdata,
        "raw": {"runtime_root": str(runtime)},
        "pretty": False,
    }


def _read_item(cfg: dict, sku: str) -> dict:
    p = Path(cfg["itemdata_root"]) / sku / f"{sku}.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _ts_seq(*timestamps):
    """Return a callable that yields timestamps in sequence."""
    it = iter(timestamps)
    def _fn():
        return next(it)
    return _fn


# ---------------------------------------------------------------------------
# _generate_sku
# ---------------------------------------------------------------------------

class TestGenerateSku:
    def test_format_is_18_chars(self):
        ts = datetime(2026, 6, 12, 10, 5, 0, 42000)
        sku = api._generate_sku(ts)
        assert len(sku) == 18

    def test_starts_with_tgw(self):
        sku = api._generate_sku(_TS1)
        assert sku.startswith("tgw")

    def test_15_digits_after_tgw(self):
        sku = api._generate_sku(_TS1)
        assert sku[3:].isdigit()
        assert len(sku[3:]) == 15

    def test_encodes_date_and_time(self):
        sku = api._generate_sku(_TS1)
        assert sku == "tgw202606121430001"

    def test_seconds_distinguish_skus(self):
        # Two datetimes differing only in seconds produce different SKUs
        ts_a = datetime(2026, 6, 12, 14, 30, 10, 0)
        ts_b = datetime(2026, 6, 12, 14, 30, 55, 0)
        assert api._generate_sku(ts_a) != api._generate_sku(ts_b)

    def test_milliseconds_distinguish_skus(self):
        assert api._generate_sku(_TS1) != api._generate_sku(_TS2)


# ---------------------------------------------------------------------------
# cmd_create_item — basic creation
# ---------------------------------------------------------------------------

class TestCmdCreateItemBasic:
    def test_creates_item_json(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["ok"] is True
        sku = result["created"][0]
        assert (cfg["itemdata_root"] / sku / f"{sku}.json").exists()

    def test_item_has_status_new(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        sku = api._generate_sku(_TS1)
        item = _read_item(cfg, sku)
        assert item["#STATUS"] == "New"

    def test_item_has_sku_field(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        sku = api._generate_sku(_TS1)
        item = _read_item(cfg, sku)
        assert item["sku"] == sku

    def test_returns_created_list(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert isinstance(result["created"], list)
        assert len(result["created"]) == 1

    def test_returns_count(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["count"] == 1

    def test_returns_template_none_when_not_given(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["template"] is None

    def test_no_template_fields_when_omitted(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        sku = api._generate_sku(_TS1)
        item = _read_item(cfg, sku)
        assert "category_group" not in item
        assert "ai_hint" not in item


# ---------------------------------------------------------------------------
# Template application
# ---------------------------------------------------------------------------

class TestCreateItemTemplate:
    def test_template_fields_applied(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        api.cmd_create_item(cfg, template="books", _now_fn=_ts_seq(_TS1))
        sku = api._generate_sku(_TS1)
        item = _read_item(cfg, sku)
        assert item["category_group"] == "books"
        assert item["size_class"] == "small"
        assert item["ai_hint"] == "printed book"
        assert item["ebay_category_id"] == "261186"

    def test_template_status_still_new(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        api.cmd_create_item(cfg, template="books", _now_fn=_ts_seq(_TS1))
        sku = api._generate_sku(_TS1)
        item = _read_item(cfg, sku)
        assert item["#STATUS"] == "New"

    def test_unknown_template_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, template="nonexistent", _now_fn=_ts_seq(_TS1))
        assert result["ok"] is False
        assert "unknown template" in result["error"]
        assert "available" in result

    def test_returns_template_key_in_result(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, template="electronics", _now_fn=_ts_seq(_TS1))
        assert result["template"] == "electronics"


# ---------------------------------------------------------------------------
# count > 1
# ---------------------------------------------------------------------------

class TestCreateItemCount:
    def test_creates_n_items(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=3, _now_fn=_ts_seq(_TS1, _TS2, _TS3))
        assert result["ok"] is True
        assert len(result["created"]) == 3
        assert result["count"] == 3

    def test_all_skus_unique(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=3, _now_fn=_ts_seq(_TS1, _TS2, _TS3))
        skus = result["created"]
        assert len(set(skus)) == 3

    def test_all_files_exist(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=3, _now_fn=_ts_seq(_TS1, _TS2, _TS3))
        for sku in result["created"]:
            assert (cfg["itemdata_root"] / sku / f"{sku}.json").exists()

    def test_count_zero_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=0, _now_fn=_ts_seq(_TS1))
        assert result["ok"] is False
        assert "count" in result["error"]

    def test_count_over_limit_returns_error(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=21, _now_fn=_ts_seq(_TS1))
        assert result["ok"] is False
        assert "count" in result["error"]

    def test_count_at_max_accepted(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        timestamps = [datetime(2026, 6, 12, 14, 30, i, 0) for i in range(20)]
        result = api.cmd_create_item(cfg, count=20, _now_fn=_ts_seq(*timestamps))
        assert result["ok"] is True
        assert result["count"] == 20


# ---------------------------------------------------------------------------
# dry_run
# ---------------------------------------------------------------------------

class TestCreateItemDryRun:
    def test_dry_run_creates_no_files(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        api.cmd_create_item(cfg, dry_run=True, _now_fn=_ts_seq(_TS1))
        assert list(cfg["itemdata_root"].iterdir()) == []

    def test_dry_run_returns_would_create(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, dry_run=True, _now_fn=_ts_seq(_TS1))
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert isinstance(result["would_create"], list)
        assert len(result["would_create"]) == 1

    def test_dry_run_count_matches(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=3, dry_run=True, _now_fn=_ts_seq(_TS1, _TS2, _TS3))
        assert len(result["would_create"]) == 3


# ---------------------------------------------------------------------------
# context setting
# ---------------------------------------------------------------------------

class TestCreateItemContext:
    def test_context_set_to_first_sku(self, tmp_path):
        from tgw.context import get_context
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        first_sku = result["created"][0]
        ctx = get_context(cfg)
        assert ctx["sku"] == first_sku

    def test_context_set_true_in_result(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["context_set"] is True

    def test_context_set_to_first_when_multiple(self, tmp_path):
        from tgw.context import get_context
        cfg = _make_cfg(tmp_path)
        result = api.cmd_create_item(cfg, count=3, _now_fn=_ts_seq(_TS1, _TS2, _TS3))
        ctx = get_context(cfg)
        assert ctx["sku"] == result["created"][0]


# ---------------------------------------------------------------------------
# KDE Connect push
# ---------------------------------------------------------------------------

_KDC_PATCH = "tgw.api.cmd_create_item"   # not used directly; we patch kdeconnect module

class TestCreateItemKdeConnect:
    def test_no_kdc_device_pushed_false(self, tmp_path):
        cfg = _make_cfg(tmp_path)  # no kdeconnect_device_id key
        result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["kdeconnect"]["pushed"] is False

    def test_kdc_push_succeeds(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        cfg["kdeconnect_device_id"] = "teststesttest1234567890abcdef01"
        with patch("tgw.apis.kdeconnect.get_device_id", return_value="teststesttest1234567890abcdef01"), \
             patch("tgw.apis.kdeconnect.send_text", return_value=True) as mock_send:
            result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["kdeconnect"]["pushed"] is True
        first_sku = result["created"][0]
        mock_send.assert_called_once_with(
            "teststesttest1234567890abcdef01",
            f"COMMAND:SKU:{first_sku}",
        )

    def test_kdc_push_text_format(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        cfg["kdeconnect_device_id"] = "teststesttest1234567890abcdef01"
        with patch("tgw.apis.kdeconnect.get_device_id", return_value="teststesttest1234567890abcdef01"), \
             patch("tgw.apis.kdeconnect.send_text", return_value=True):
            result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["kdeconnect"]["text"].startswith("COMMAND:SKU:tgw")

    def test_kdc_exception_is_swallowed(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        cfg["kdeconnect_device_id"] = "teststesttest1234567890abcdef01"
        with patch("tgw.apis.kdeconnect.get_device_id", side_effect=RuntimeError("no kdeconnect")):
            result = api.cmd_create_item(cfg, _now_fn=_ts_seq(_TS1))
        assert result["ok"] is True
        assert result["kdeconnect"]["pushed"] is False
        assert "error" in result["kdeconnect"]
