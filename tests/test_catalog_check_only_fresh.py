"""todo #1286 / PP-COHESION-001 — build_all_catalogs(check_only=True) must be
a clean dry-run preview on a fresh system (no catalog ever built before), not
raise FileNotFoundError.

Root cause: build_all_catalogs() forced source='full_catalog' /
source='search_catalog' on the downstream build_search_catalog() /
build_location_tree() calls, regardless of check_only. In check_only mode
the upstream step never writes its output file, so on a fresh system with
no full_catalog/search_catalog file on disk yet, the forced source made
load_full_catalog()/load_search_catalog() raise FileNotFoundError instead
of falling back to reading ItemData directly (as their own 'auto' mode
already does).
"""

import json

import tgw.catalog as catalog


def _cfg(tmp_path):
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir(parents=True, exist_ok=True)
    return {
        "itemdata_root": itemdata_root,
        "location_tree_root": tmp_path / "LocationTree",
        "full_catalog_path": tmp_path / "full_catalog.json",
        "search_catalog_path": tmp_path / "search_catalog.json",
        "sqlite_catalog_path": tmp_path / "catalog.db",
        "search_fields": ["sku", "title", "location"],
        "required": ["sku"],
        "pretty": True,
        "skip_missing": True,
    }


def _make_item(cfg, sku, title="Widget", location="SAT013"):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(
        json.dumps({"sku": sku, "title": title, "location": location}))


def test_check_only_fresh_system_no_prior_catalog_does_not_raise(tmp_path):
    """Fresh system: no full_catalog/search_catalog file has ever been
    written. A check_only=True dry-run must return cleanly, not raise."""
    cfg = _cfg(tmp_path)
    assert not cfg["full_catalog_path"].exists()
    assert not cfg["search_catalog_path"].exists()
    _make_item(cfg, "tgw000000000000001")

    out = catalog.build_all_catalogs(cfg, check_only=True)

    assert out["ok"] is True
    assert out["check_only"] is True
    assert len(out["steps"]) == 4
    for step in out["steps"]:
        assert step["ok"] is True
        assert step["check_only"] is True

    # Still a true dry run: nothing was actually written.
    assert not cfg["full_catalog_path"].exists()
    assert not cfg["search_catalog_path"].exists()
    assert not cfg["location_tree_root"].exists()
    assert not cfg["sqlite_catalog_path"].exists()

    # Preview correctly fell back to reading ItemData directly, since there
    # was no on-disk full/search catalog to source from yet.
    search_step = out["steps"][1]
    assert search_step["source_mode"] == "itemdata"
    location_step = out["steps"][2]
    assert location_step["source_mode"] == "itemdata"


def test_check_only_empty_itemdata_returns_clean_empty_preview(tmp_path):
    """Absolute fresh baseline: no items at all yet either."""
    cfg = _cfg(tmp_path)

    out = catalog.build_all_catalogs(cfg, check_only=True)

    assert out["ok"] is True
    full_step = out["steps"][0]
    assert full_step["rows_built"] == 0
    search_step = out["steps"][1]
    assert search_step["rows_built"] == 0
    assert search_step["source_mode"] == "itemdata"
