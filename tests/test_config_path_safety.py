"""Tests for todo #1274 (PP-COHESION-001, SECURITY): sku_dir()/location_dir()
must reject path-traversal and absolute-path-override input instead of doing
a raw, unvalidated pathlib join.

Escape vectors covered:
  1. Absolute-path override — Path(root) / "/etc/passwd" == Path("/etc/passwd")
     (pathlib's `/` discards the left side when the right side is absolute).
  2. Traversal — a sku/location value containing "../.." walks out of root
     even when not absolute.

Legitimate values (real production SKU format `tgwYYYYMMDDHHMMSSmmm` and
real location codes like `SAT013`) must continue to resolve exactly as
before this fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tgw.config import load_config, location_dir, sku_dir, sku_json


def _write_cfg(tmp_path: Path, data: dict) -> Path:
    merged = {"secrets_root": str(tmp_path / "secrets"), **data}
    p = tmp_path / "tgw-api-config.json"
    p.write_text(json.dumps(merged))
    return p


def _cfg(tmp_path: Path) -> dict:
    itemdata_root = tmp_path / "ItemData"
    location_tree_root = tmp_path / "ItemCatalog" / "by-location"
    itemdata_root.mkdir(parents=True)
    location_tree_root.mkdir(parents=True)
    cfg_path = _write_cfg(
        tmp_path,
        {
            "itemdata_root": str(itemdata_root),
            "catalog_root": str(tmp_path / "ItemCatalog"),
            "location_tree_root": str(location_tree_root),
        },
    )
    return load_config(cfg_path)


# ---------------------------------------------------------------------------
# Legitimate values — behavior unchanged
# ---------------------------------------------------------------------------


def test_sku_dir_valid_sku_unchanged(tmp_path):
    cfg = _cfg(tmp_path)
    sku = "tgw20260713120000000"
    assert sku_dir(cfg, sku) == (cfg["itemdata_root"] / sku).resolve()


def test_sku_json_valid_sku_unchanged(tmp_path):
    cfg = _cfg(tmp_path)
    sku = "tgw20260713120000000"
    assert sku_json(cfg, sku) == (cfg["itemdata_root"] / sku).resolve() / f"{sku}.json"


def test_location_dir_valid_location_unchanged(tmp_path):
    cfg = _cfg(tmp_path)
    assert location_dir(cfg, "SAT013") == (cfg["location_tree_root"] / "SAT013").resolve()


# ---------------------------------------------------------------------------
# Escape vector 1 — absolute-path override
# ---------------------------------------------------------------------------


def test_sku_dir_rejects_absolute_override(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        sku_dir(cfg, "/etc/passwd")


def test_location_dir_rejects_absolute_override(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        location_dir(cfg, "/tmp/x")


# ---------------------------------------------------------------------------
# Escape vector 2 — traversal
# ---------------------------------------------------------------------------


def test_sku_dir_rejects_traversal(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        sku_dir(cfg, "../../../etc/passwd")


def test_location_dir_rejects_traversal(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError):
        location_dir(cfg, "../outside")


# ---------------------------------------------------------------------------
# Additional guard-rail cases
# ---------------------------------------------------------------------------


def test_sku_dir_rejects_empty_and_dot_segments(tmp_path):
    cfg = _cfg(tmp_path)
    for bad in ("", ".", ".."):
        with pytest.raises(ValueError):
            sku_dir(cfg, bad)


def test_location_dir_rejects_spaces_and_slashes(tmp_path):
    cfg = _cfg(tmp_path)
    for bad in ("SAT 013", "SAT/013", "SAT\\013"):
        with pytest.raises(ValueError):
            location_dir(cfg, bad)
