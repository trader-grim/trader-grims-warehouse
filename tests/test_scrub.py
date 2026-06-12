"""Tests for tgw.scrub — data maintenance passes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tgw.ebay.pricing as pricing
from tgw.scrub import data_scrub_pass1, data_scrub_size_class_backfill

GROUPS = {
    "groups": {
        "books": {
            "name": "Books",
            "size_class": "flat",
            "ebay_categories": ["261186", "29223"],
            "pricing": {},
        },
        "gadgets": {
            "name": "Gadgets",
            "size_class": "small_box",
            "ebay_categories": ["9355"],
            "pricing": {},
        },
        "misc": {
            "name": "Miscellaneous",
            "size_class": "",          # group with no size_class
            "ebay_categories": ["99999"],
            "pricing": {},
        },
    }
}


@pytest.fixture
def item_root(tmp_path):
    return tmp_path


@pytest.fixture(autouse=True)
def _stub_groups(monkeypatch):
    monkeypatch.setattr(pricing, "_groups_cache", None)
    monkeypatch.setattr(pricing, "_groups_reverse", {})
    monkeypatch.setattr(pricing, "_load_groups", lambda cfg: GROUPS)


def _write_item(root: Path, sku: str, doc: dict) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _read_item(root: Path, sku: str) -> dict:
    return json.loads((root / sku / f"{sku}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# data_scrub_pass1
# ---------------------------------------------------------------------------

class TestPass1:
    def test_dry_run_does_not_write(self, item_root):
        p = _write_item(item_root, "tgw001", {"sku": "tgw001", "#VERIFIED": "yes"})
        result = data_scrub_pass1({"itemdata_root": item_root}, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["renamed"] == 1
        doc = json.loads(p.read_text())
        assert "#VERIFIED" in doc          # untouched

    def test_write_renames_field(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "#VERIFIED": "yes"})
        result = data_scrub_pass1({"itemdata_root": item_root}, dry_run=False)
        assert result["renamed"] == 1
        doc = _read_item(item_root, "tgw001")
        assert "verified" in doc
        assert "#VERIFIED" not in doc

    def test_skips_item_without_field(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "title": "X"})
        result = data_scrub_pass1({"itemdata_root": item_root}, dry_run=False)
        assert result["renamed"] == 0
        assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# data_scrub_size_class_backfill (pass 2)
# ---------------------------------------------------------------------------

class TestPass2:
    def _cfg(self, item_root):
        return {"itemdata_root": item_root, "category_groups_path": "dummy"}

    def test_dry_run_does_not_write(self, item_root):
        p = _write_item(item_root, "tgw001", {"sku": "tgw001", "ebay_category_id": "261186"})
        cfg = self._cfg(item_root)
        result = data_scrub_size_class_backfill(cfg, dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["updated"] == 1
        doc = json.loads(p.read_text())
        assert "size_class" not in doc     # untouched

    def test_infers_from_category_group(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "category_group": "books"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 1
        doc = _read_item(item_root, "tgw001")
        assert doc["size_class"] == "flat"
        assert "category_group" in doc     # preserved

    def test_infers_from_ebay_category_id(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "ebay_category_id": "9355"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 1
        doc = _read_item(item_root, "tgw001")
        assert doc["size_class"] == "small_box"
        assert doc["category_group"] == "gadgets"

    def test_skips_item_with_existing_size_class(self, item_root):
        _write_item(item_root, "tgw001",
                    {"sku": "tgw001", "ebay_category_id": "261186", "size_class": "packet"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 0
        assert result["skipped"] == 1
        doc = _read_item(item_root, "tgw001")
        assert doc["size_class"] == "packet"   # unchanged

    def test_skips_item_with_no_mapping_basis(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "title": "mystery item"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 0
        assert result["skipped"] == 1

    def test_skips_unknown_ebay_category(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "ebay_category_id": "777777"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 0
        assert result["skipped"] == 1

    def test_skips_group_without_size_class(self, item_root):
        # 'misc' group has empty size_class
        _write_item(item_root, "tgw001", {"sku": "tgw001", "category_group": "misc"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 0
        assert result["skipped"] == 1

    def test_ebay_category_as_string_or_int(self, item_root):
        # ebay_category_id may be stored as int in some legacy items
        _write_item(item_root, "tgw001", {"sku": "tgw001", "ebay_category_id": 261186})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 1
        doc = _read_item(item_root, "tgw001")
        assert doc["size_class"] == "flat"

    def test_sample_in_dry_run_output(self, item_root):
        _write_item(item_root, "tgw001", {"sku": "tgw001", "ebay_category_id": "261186"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=True)
        assert "sample_would_update" in result
        assert result["sample_would_update"][0]["sku"] == "tgw001"

    def test_batch_of_mixed_items(self, item_root):
        # 2 updatable, 1 already-set, 1 unmappable
        _write_item(item_root, "tgw001", {"sku": "tgw001", "ebay_category_id": "261186"})
        _write_item(item_root, "tgw002", {"sku": "tgw002", "category_group": "gadgets"})
        _write_item(item_root, "tgw003", {"sku": "tgw003", "size_class": "large_box"})
        _write_item(item_root, "tgw004", {"sku": "tgw004", "title": "no info"})
        result = data_scrub_size_class_backfill(self._cfg(item_root), dry_run=False)
        assert result["updated"] == 2
        assert result["skipped"] == 2
        assert _read_item(item_root, "tgw001")["size_class"] == "flat"
        assert _read_item(item_root, "tgw002")["size_class"] == "small_box"
        assert _read_item(item_root, "tgw003")["size_class"] == "large_box"  # untouched
        assert "size_class" not in _read_item(item_root, "tgw004")
