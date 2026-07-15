"""Tests for the vision-based aspect-fill photo selection in ebay_draft.py
(session 41 — "wire in bulk_classify"): the aspect-fill step now looks at the
item's actual photos instead of asking a text-only model to guess from the
title, so details only visible in one photo (barcode, tag, engraving) aren't
silently missed. See _aspect_fill_photos / _build_prompt.
"""

from __future__ import annotations

from pathlib import Path

from tgw.workers.ebay_draft import (
    _MAX_PHOTOS_ASPECTS,
    _aspect_fill_photos,
    _build_prompt,
)


def _touch(sku_dir: Path, name: str) -> None:
    # A genuinely decodable 1x1 JPEG, not just a header stub -- todo #1403
    # made _aspect_fill_photos screen out undecodable files (the truncated/
    # corrupt-photo class), so a bare header-only stub would now be
    # (correctly) filtered out and break these filename-selection tests.
    from PIL import Image
    Image.new("RGB", (1, 1), color=(128, 128, 128)).save(sku_dir / name, format="JPEG")


class TestAspectFillPhotos:
    def test_non_cloud_provider_gets_no_photos(self, tmp_path):
        sku_dir = tmp_path / "tgw001"
        sku_dir.mkdir()
        _touch(sku_dir, "tgw001.jpg")

        photos = _aspect_fill_photos({}, sku_dir, "ollama")
        assert photos == []

    def test_openrouter_gets_photos(self, tmp_path):
        sku_dir = tmp_path / "tgw001"
        sku_dir.mkdir()
        _touch(sku_dir, "tgw001.jpg")
        _touch(sku_dir, "tgw001_2.jpg")

        photos = _aspect_fill_photos({}, sku_dir, "openrouter")
        assert len(photos) == 2

    def test_google_direct_gets_photos(self, tmp_path):
        sku_dir = tmp_path / "tgw001"
        sku_dir.mkdir()
        _touch(sku_dir, "tgw001.jpg")

        photos = _aspect_fill_photos({}, sku_dir, "google_direct")
        assert len(photos) == 1

    def test_excludes_alt_and_cropped_derivatives(self, tmp_path):
        sku_dir = tmp_path / "tgw001"
        sku_dir.mkdir()
        _touch(sku_dir, "tgw001.jpg")
        _touch(sku_dir, "tgw001-alt.jpg")
        _touch(sku_dir, "cropped-tgw001.jpg")

        photos = _aspect_fill_photos({}, sku_dir, "openrouter")
        names = [p.name for p in photos]
        assert names == ["tgw001.jpg"]

    def test_caps_at_max_photos_aspects(self, tmp_path):
        sku_dir = tmp_path / "tgw001"
        sku_dir.mkdir()
        for i in range(_MAX_PHOTOS_ASPECTS + 5):
            _touch(sku_dir, f"tgw001_{i}.jpg")

        photos = _aspect_fill_photos({}, sku_dir, "openrouter")
        assert len(photos) == _MAX_PHOTOS_ASPECTS


class TestBuildPromptMentionsPhotos:
    def test_prompt_tells_model_photos_are_attached(self):
        prompt = _build_prompt(
            {"title": "Test Item", "ebay_category_name": "Widgets"},
            aspects=[{"name": "Color", "required": False, "allowed_values": [], "mode": "FREE_TEXT"}],
        )
        assert "Photos of the item are attached" in prompt
