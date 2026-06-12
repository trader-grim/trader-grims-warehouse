"""Tests for tgw.alt_text — vision alt-text generation (OpenRouter + Ollama)."""

from __future__ import annotations

import json
from pathlib import Path

import tgw.alt_text as alt_text_mod
from tgw.alt_text import cmd_alt_text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_MODEL = "qwen2.5vl:7b"

_GOOD_RESPONSE = json.dumps(
    {
        "alt_text": "Silver pocket watch with chain on white background",
        "seo_caption": "Vintage Elgin pocket watch, 17 jewels, runs well, includes chain.",
    }
)


def _make_cfg(tmp_path: Path) -> dict:
    return {
        "itemdata_root": tmp_path / "ItemData",
        "pretty": False,
    }


def _make_item(cfg: dict, sku: str, extra: dict | None = None) -> Path:
    sku_dir = Path(cfg["itemdata_root"]) / sku
    sku_dir.mkdir(parents=True, exist_ok=True)
    doc = {"sku": sku, **(extra or {})}
    p = sku_dir / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _add_photo(cfg: dict, sku: str, name: str | None = None) -> Path:
    sku_dir = Path(cfg["itemdata_root"]) / sku
    fname = name or f"{sku}.jpg"
    p = sku_dir / fname
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)  # minimal JPEG header
    return p


def _patch_vision(monkeypatch, response: str = _GOOD_RESPONSE):
    """Patch encoding + call_model; pass response to simulate model output."""
    monkeypatch.setattr(alt_text_mod, "_encode_resized", lambda p, max_px=512: "AABB==")
    monkeypatch.setattr(alt_text_mod, "call_model", lambda *a, **kw: response)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAltText:
    def test_missing_item_json(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        result = cmd_alt_text(cfg, sku="tgw999", dry_run=False)
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_no_primary_image(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        result = cmd_alt_text(cfg, sku="tgw001", dry_run=False)
        assert result["ok"] is False
        assert "no primary image" in result["error"]

    def test_dry_run_does_not_write(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        photo = _add_photo(cfg, "tgw001")
        result = cmd_alt_text(cfg, sku="tgw001", dry_run=True)
        assert result["ok"] is True
        assert result["dry_run"] is True
        assert result["sku"] == "tgw001"
        assert photo.exists()  # photo untouched

    def test_dry_run_shows_alt_path(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        result = cmd_alt_text(cfg, sku="tgw001", dry_run=True)
        assert "tgw001-alt.jpg" in result["alt_path_would_be"]

    def test_dry_run_reports_archive_needed(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        result = cmd_alt_text(cfg, sku="tgw001", dry_run=True)
        assert result["archive_needed"] is True

    def test_success_writes_draft_listing(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        _patch_vision(monkeypatch)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["ok"] is True
        doc = json.loads((Path(cfg["itemdata_root"]) / "tgw001" / "tgw001.json").read_text())
        assert doc["draft_listing"]["alt_text"] == "Silver pocket watch with chain on white background"
        assert "Elgin" in doc["draft_listing"]["seo_caption"]

    def test_success_renames_production_image(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        photo = _add_photo(cfg, "tgw001", name="tgw001.jpg")
        _patch_vision(monkeypatch)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["ok"] is True
        assert not photo.exists()  # original gone from production
        alt_path = Path(cfg["itemdata_root"]) / "tgw001" / "tgw001-alt.jpg"
        assert alt_path.exists()

    def test_success_archives_original(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001", name="tgw001.jpg")
        _patch_vision(monkeypatch)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["archived_to_history"] is True
        history_path = Path(tmp_path) / "history" / "ItemData" / "tgw001" / "tgw001.jpg"
        assert history_path.exists()

    def test_does_not_rearchive_if_already_in_history(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001", name="tgw001.jpg")
        # Pre-populate history
        hist_dir = Path(tmp_path) / "history" / "ItemData" / "tgw001"
        hist_dir.mkdir(parents=True)
        (hist_dir / "tgw001.jpg").write_bytes(b"old")
        _patch_vision(monkeypatch)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["archived_to_history"] is False
        # Original history file unchanged
        assert (hist_dir / "tgw001.jpg").read_bytes() == b"old"

    def test_idempotent_skip_when_already_processed(self, tmp_path):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001", {"draft_listing": {"alt_text": "already set"}})
        sku_dir = Path(cfg["itemdata_root"]) / "tgw001"
        (sku_dir / "tgw001-alt.jpg").write_bytes(b"img")
        result = cmd_alt_text(cfg, sku="tgw001", dry_run=False)
        assert result["ok"] is True
        assert result.get("skipped") is True

    def test_non_json_response_returns_error(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        _patch_vision(monkeypatch, response="Sorry, I cannot describe this.")

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["ok"] is False
        assert "non-JSON" in result["error"]

    def test_empty_alt_text_returns_error(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        empty = json.dumps({"alt_text": "", "seo_caption": "something"})
        _patch_vision(monkeypatch, response=empty)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["ok"] is False
        assert "empty alt_text" in result["error"]

    def test_alt_text_truncated_to_150(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        long_text = "A" * 200
        response = json.dumps({"alt_text": long_text, "seo_caption": "cap"})
        _patch_vision(monkeypatch, response=response)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert result["ok"] is True
        assert len(result["alt_text"]) == 150

    def test_ollama_unavailable_returns_error(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001")
        monkeypatch.setattr(alt_text_mod, "is_available", lambda m: False)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL, provider="ollama")
        assert result["ok"] is False
        assert "Ollama unavailable" in result["error"]

    def test_primary_image_skips_alt_files(self, tmp_path):
        """_primary_image should not return already-renamed -alt.jpg files."""
        cfg = _make_cfg(tmp_path)
        sku_dir = Path(cfg["itemdata_root"]) / "tgw001"
        sku_dir.mkdir(parents=True)
        (sku_dir / "tgw001-alt.jpg").write_bytes(b"img")
        from tgw.alt_text import _primary_image

        result = _primary_image(sku_dir)
        assert result is None

    def test_image_renamed_result_field(self, tmp_path, monkeypatch):
        cfg = _make_cfg(tmp_path)
        _make_item(cfg, "tgw001")
        _add_photo(cfg, "tgw001", name="product-photo-front.jpg")
        _patch_vision(monkeypatch)

        result = cmd_alt_text(cfg, sku="tgw001", model=_DUMMY_MODEL)
        assert "product-photo-front.jpg" in result["image_renamed"]
        assert "tgw001-alt.jpg" in result["image_renamed"]
