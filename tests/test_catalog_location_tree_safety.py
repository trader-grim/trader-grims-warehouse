"""todo #1275 / PP-COHESION-001 — build_location_tree() must route link_dir
construction through the hardened config.location_dir() (todo #1274) instead
of a raw dest_root / location join, so a malformed/malicious location value
in ItemData cannot escape the location_tree_root symlink tree during a
catalog rebuild.
"""

import json

import tgw.catalog as catalog


def _cfg(tmp_path):
    itemdata_root = tmp_path / "ItemData"
    location_tree_root = tmp_path / "LocationTree"
    itemdata_root.mkdir(parents=True, exist_ok=True)
    return {
        "itemdata_root": itemdata_root,
        "location_tree_root": location_tree_root,
        "search_catalog_path": tmp_path / "search_catalog.does-not-exist",
        "full_catalog_path": tmp_path / "full_catalog.does-not-exist",
        "skip_missing": False,
    }


def _make_item(cfg, sku, location):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku, "location": location}))


def test_valid_location_builds_link_no_problems(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000001", "SAT013")

    out = catalog.build_location_tree(cfg, source="itemdata")

    assert out["ok"] is True
    assert out.get("problems", []) == []
    assert out["links_built"] == 1
    link = cfg["location_tree_root"] / "SAT013" / "tgw000000000000001"
    assert link.is_symlink()
    assert link.resolve() == (cfg["itemdata_root"] / "tgw000000000000001").resolve()


def test_malicious_location_rejected_not_escaped(tmp_path):
    cfg = _cfg(tmp_path)
    evil_target = tmp_path / "tmp" / "evil"
    _make_item(cfg, "tgw000000000000002", "../../../tmp/evil")

    out = catalog.build_location_tree(cfg, source="itemdata")

    # Rejected: recorded as a problem, not a crash.
    assert out["ok"] is False
    assert any("unsafe location" in p for p in out["problems"])
    assert out["links_built"] == 0

    # Never escaped location_tree_root.
    assert not evil_target.exists()
    for p in cfg["location_tree_root"].rglob("*") if cfg["location_tree_root"].exists() else []:
        assert cfg["location_tree_root"].resolve() in p.resolve().parents or p.resolve() == cfg["location_tree_root"].resolve()


def test_malicious_row_does_not_block_remaining_rows(tmp_path):
    cfg = _cfg(tmp_path)
    _make_item(cfg, "tgw000000000000003", "../../../tmp/evil")
    _make_item(cfg, "tgw000000000000004", "SAT014")

    out = catalog.build_location_tree(cfg, source="itemdata")

    assert out["ok"] is False
    assert out["links_built"] == 1
    link = cfg["location_tree_root"] / "SAT014" / "tgw000000000000004"
    assert link.is_symlink()
