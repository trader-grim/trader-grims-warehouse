"""Tests for tgw.context — set/get/clear current-item context (PP-CONTEXT-001)."""

from __future__ import annotations

import json
from pathlib import Path

import tgw.context as ctx_mod
from tgw.context import clear_context, get_context, set_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cfg(tmp_path: Path) -> dict:
    itemdata = tmp_path / "ItemData"
    itemdata.mkdir()
    runtime = tmp_path / "runtime"
    (runtime / "state").mkdir(parents=True)
    return {
        "itemdata_root": itemdata,
        "raw": {"runtime_root": str(runtime)},
    }


def _make_sku(cfg: dict, sku: str) -> Path:
    d = Path(cfg["itemdata_root"]) / sku
    d.mkdir(parents=True, exist_ok=True)
    j = d / f"{sku}.json"
    j.write_text(json.dumps({"sku": sku}), encoding="utf-8")
    return d


_SKU = "tgw20240601120000000"
_SKU2 = "tgw20240602130000000"


# ---------------------------------------------------------------------------
# set_context
# ---------------------------------------------------------------------------


class TestSetContext:
    def test_set_valid_sku(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        r = set_context(cfg, _SKU)
        assert r["ok"] is True
        assert r["sku"] == _SKU
        assert r["changed"] is True
        assert r["set_at"] is not None

    def test_set_writes_state_file(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        set_context(cfg, _SKU)
        state_path = tmp_path / "runtime" / "state" / "current-item.json"
        assert state_path.exists()
        data = json.loads(state_path.read_text())
        assert data["sku"] == _SKU
        assert data["set_by"] == "cli"

    def test_set_idempotent_same_sku(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        set_context(cfg, _SKU)
        r2 = set_context(cfg, _SKU)
        assert r2["ok"] is True
        assert r2["changed"] is False

    def test_set_different_sku_changes(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        _make_sku(cfg, _SKU2)
        set_context(cfg, _SKU)
        r = set_context(cfg, _SKU2)
        assert r["ok"] is True
        assert r["sku"] == _SKU2
        assert r["changed"] is True

    def test_set_invalid_format_rejected(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        r = set_context(cfg, "not-a-sku")
        assert r["ok"] is False
        assert "invalid" in r["error"]

    def test_set_missing_sku_dir_rejected(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        r = set_context(cfg, _SKU)  # directory not created
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_set_custom_set_by(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        r = set_context(cfg, _SKU, set_by="camera")
        assert r["set_by"] == "camera"
        state_path = tmp_path / "runtime" / "state" / "current-item.json"
        data = json.loads(state_path.read_text())
        assert data["set_by"] == "camera"


# ---------------------------------------------------------------------------
# get_context
# ---------------------------------------------------------------------------


class TestGetContext:
    def test_get_when_not_set(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", tmp_path / "CurrentItem")
        r = get_context(cfg)
        assert r["ok"] is True
        assert r["sku"] is None

    def test_get_after_set(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        set_context(cfg, _SKU)
        r = get_context(cfg)
        assert r["ok"] is True
        assert r["sku"] == _SKU
        assert r["set_at"] is not None

    def test_get_returns_latest_sku(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        _make_sku(cfg, _SKU2)
        set_context(cfg, _SKU)
        set_context(cfg, _SKU2)
        r = get_context(cfg)
        assert r["sku"] == _SKU2

    def test_get_falls_back_to_symlink(self, tmp_path, monkeypatch):
        """If no state file exists, get_context falls back to legacy symlink."""
        cfg = _make_cfg(tmp_path)
        sku_dir = _make_sku(cfg, _SKU)
        # Simulate a symlink set by the old tgwset
        link = tmp_path / "CurrentItem"
        link.symlink_to(sku_dir)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", link)
        r = get_context(cfg)
        assert r["sku"] == _SKU
        assert r["set_by"] == "legacy"

    def test_get_ignores_corrupt_state_file(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", tmp_path / "CurrentItem")
        state_path = tmp_path / "runtime" / "state" / "current-item.json"
        state_path.write_text("not json", encoding="utf-8")
        r = get_context(cfg)
        assert r["ok"] is True
        assert r["sku"] is None

    def test_get_ignores_state_file_with_bad_sku(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", tmp_path / "CurrentItem")
        state_path = tmp_path / "runtime" / "state" / "current-item.json"
        state_path.write_text(json.dumps({"sku": "badformat", "set_at": "x", "set_by": "x"}))
        r = get_context(cfg)
        assert r["sku"] is None


# ---------------------------------------------------------------------------
# clear_context
# ---------------------------------------------------------------------------


class TestClearContext:
    def test_clear_after_set(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        set_context(cfg, _SKU)
        r = clear_context(cfg)
        assert r["ok"] is True
        assert r["changed"] is True
        state_path = tmp_path / "runtime" / "state" / "current-item.json"
        assert not state_path.exists()

    def test_clear_idempotent_when_empty(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        r = clear_context(cfg)
        assert r["ok"] is True
        assert r["changed"] is False

    def test_clear_then_get_returns_none(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        set_context(cfg, _SKU)
        clear_context(cfg)
        r = get_context(cfg)
        assert r["sku"] is None


# ---------------------------------------------------------------------------
# Compat symlinks
# ---------------------------------------------------------------------------


class TestCompatSymlinks:
    def test_set_creates_current_item_symlink(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        ci_link = tmp_path / "CurrentItem"
        cij_link = tmp_path / "CurrentItem.json"
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", ci_link)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM_JSON", cij_link)
        set_context(cfg, _SKU)
        assert ci_link.is_symlink()
        assert ci_link.resolve() == (Path(cfg["itemdata_root"]) / _SKU).resolve()
        assert cij_link.is_symlink()

    def test_set_updates_symlink_to_new_sku(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        _make_sku(cfg, _SKU2)
        ci_link = tmp_path / "CurrentItem"
        cij_link = tmp_path / "CurrentItem.json"
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", ci_link)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM_JSON", cij_link)
        set_context(cfg, _SKU)
        set_context(cfg, _SKU2)
        assert ci_link.resolve() == (Path(cfg["itemdata_root"]) / _SKU2).resolve()

    def test_clear_removes_symlinks(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        ci_link = tmp_path / "CurrentItem"
        cij_link = tmp_path / "CurrentItem.json"
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", ci_link)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM_JSON", cij_link)
        set_context(cfg, _SKU)
        clear_context(cfg)
        assert not ci_link.exists()
        assert not cij_link.exists()

    def test_set_atomic_no_gap(self, tmp_path, monkeypatch):
        """Symlink replacement via temp+rename — link always exists between set calls."""
        cfg = _make_cfg(tmp_path)
        _make_sku(cfg, _SKU)
        _make_sku(cfg, _SKU2)
        ci_link = tmp_path / "CurrentItem"
        cij_link = tmp_path / "CurrentItem.json"
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM", ci_link)
        monkeypatch.setattr(ctx_mod, "_COMPAT_CURRENT_ITEM_JSON", cij_link)
        set_context(cfg, _SKU)
        assert ci_link.is_symlink()
        set_context(cfg, _SKU2)
        # After second set the link should point to SKU2, no broken intermediate
        assert ci_link.is_symlink()
        assert ci_link.resolve() == (Path(cfg["itemdata_root"]) / _SKU2).resolve()
