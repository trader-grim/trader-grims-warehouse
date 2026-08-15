"""Tests for todo #1403: ebay_draft must not let a truncated/corrupt photo
propagate an uncaught OSError into a bare dead_letter. Confirmed live
2026-07-14: 7-8 ebay_draft dead-letters, `OSError('image file is truncated
(N bytes not processed)')` / `OSError('broken data stream when reading
image file')`, all from `Image.open()` in `_encode_resized()`, called from
`_aspect_fill_photos()`'s vision-photo-selection path.

This is the same corruption class PP-DATAINTEGRITY-001 leg 1's
`photo_files_readable` catalog-verify rule already detects project-wide
(todo #1154). This packet does NOT repair photos or build legs 2/3 -- it
only makes the pipeline log+notify+skip instead of dead-lettering the whole
job over one bad photo (invariant C11: a guard's skip is a finding, not a
log line -- reuses the generic `pipeline_error` mechanism, not a new
tracking scheme).
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from tgw.workers.ebay_draft import _aspect_fill_photos, _encode_resized


def _write_good_jpeg(path: Path, color=(200, 50, 50)) -> None:
    img = Image.new("RGB", (50, 50), color=color)
    img.save(path, format="JPEG", quality=85)


def _write_truncated_jpeg(path: Path) -> None:
    img = Image.new("RGB", (50, 50), color=(10, 200, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    # Cut the file off mid-stream -- this is exactly the corruption class
    # confirmed live (OSError('image file is truncated ...')).
    path.write_bytes(data[: len(data) // 2])


class TestEncodeResizedCorruptPhoto:
    def test_truncated_image_returns_none_not_raises(self, tmp_path):
        bad = tmp_path / "corrupt.jpg"
        _write_truncated_jpeg(bad)

        result = _encode_resized(bad)
        assert result is None

    def test_good_image_still_encodes_normally(self, tmp_path):
        good = tmp_path / "good.jpg"
        _write_good_jpeg(good)

        result = _encode_resized(good)
        assert isinstance(result, str)
        assert len(result) > 0


class TestAspectFillPhotosSkipsCorrupt:
    def test_mixed_good_and_corrupt_photos_keeps_good_ones(self, tmp_path):
        sku_dir = tmp_path / "tgw1"
        sku_dir.mkdir()
        _write_good_jpeg(sku_dir / "tgw1.jpg")
        _write_truncated_jpeg(sku_dir / "tgw1_2.jpg")
        _write_good_jpeg(sku_dir / "tgw1_3.jpg")

        patched = {}

        def fake_patch_item(cfg, sku, fields):
            patched.update(fields)
            return {"ok": True}

        import tgw.workers.ebay_draft as ebay_draft_mod
        orig = ebay_draft_mod.fence_patch_item
        ebay_draft_mod.fence_patch_item = fake_patch_item
        try:
            photos = _aspect_fill_photos(
                {}, sku_dir, "openrouter", sku="tgw1", config={})
        finally:
            ebay_draft_mod.fence_patch_item = orig

        names = sorted(p.name for p in photos)
        assert names == ["tgw1.jpg", "tgw1_3.jpg"]
        assert "tgw1_2.jpg" not in names

        # A durable finding was recorded (invariant C11) via the same
        # generic pipeline_error mechanism api.py's catalog-verify surfaces
        # (`_verify_item`: any pipeline_error dict becomes a
        # `pipeline_error:<code>` violation) -- not a new tracking scheme.
        pe = patched.get("pipeline_error")
        assert pe is not None
        assert pe["code"] == "photo_files_readable"
        assert "tgw1_2.jpg" in pe["detail"]
        assert pe["source"] == "ebay_draft"

    def test_all_good_photos_unaffected_no_spurious_finding(self, tmp_path):
        sku_dir = tmp_path / "tgw2"
        sku_dir.mkdir()
        _write_good_jpeg(sku_dir / "tgw2.jpg")
        _write_good_jpeg(sku_dir / "tgw2_2.jpg")

        called = []

        def fake_patch_item(cfg, sku, fields):
            called.append(fields)
            return {"ok": True}

        import tgw.workers.ebay_draft as ebay_draft_mod
        orig = ebay_draft_mod.fence_patch_item
        ebay_draft_mod.fence_patch_item = fake_patch_item
        try:
            photos = _aspect_fill_photos(
                {}, sku_dir, "openrouter", sku="tgw2", config={})
        finally:
            ebay_draft_mod.fence_patch_item = orig

        names = sorted(p.name for p in photos)
        assert names == ["tgw2.jpg", "tgw2_2.jpg"]
        assert called == []

    def test_no_sku_or_config_still_filters_but_skips_persistence(self, tmp_path):
        # Pre-#1403 call sites (and the existing aspect-photos test suite)
        # don't pass sku/config -- filtering must still work, just without
        # recording a finding (no cfg/sku to attach it to).
        sku_dir = tmp_path / "tgw3"
        sku_dir.mkdir()
        _write_good_jpeg(sku_dir / "tgw3.jpg")
        _write_truncated_jpeg(sku_dir / "tgw3_2.jpg")

        photos = _aspect_fill_photos({}, sku_dir, "openrouter")
        names = sorted(p.name for p in photos)
        assert names == ["tgw3.jpg"]
