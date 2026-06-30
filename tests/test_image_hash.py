"""Tests for tgw.image_hash — perceptual hash deduplication.

All tests are offline: no DB, no eBay API calls.
DB functions are tested by injecting a mock _conn via monkeypatch.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import tgw.image_hash as image_hash_mod
from tgw.image_hash import compute_dhash, lookup_hash, store_hash

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png(path: Path, width: int = 16, height: int = 16, color: tuple = (128, 64, 192)) -> Path:
    """Write a solid-colour PNG using Pillow. Skip test if Pillow unavailable."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    img = Image.new("RGB", (width, height), color=color)
    img.save(path, format="PNG")
    return path


def _make_gradient_png(path: Path, ascending: bool = True) -> Path:
    """Write a left-to-right gradient PNG."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")
    size = 32
    img = Image.new("L", (size, size))
    pixels = []
    for y in range(size):
        for x in range(size):
            v = (x * 255 // (size - 1)) if ascending else (255 - x * 255 // (size - 1))
            pixels.append(v)
    img.putdata(pixels)
    img.save(path, format="PNG")
    return path


class _MockCursor:
    """Minimal cursor mock that supports the context-manager protocol."""

    def __init__(self, fetchone_result=None):
        self._fetchone = fetchone_result
        self.executed = []
        self.inserted = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        if "SELECT" in sql:
            self.executed.append(params)
        else:
            self.inserted.append(params)

    def fetchone(self):
        return self._fetchone

    def close(self):
        pass


class _MockConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cursor(self):
        return self._cursor


def _mock_conn_factory(cursor):
    """Return a `_conn`-compatible contextmanager factory using *cursor*."""

    @contextmanager
    def _conn():
        yield _MockConn(cursor)

    return _conn


# ---------------------------------------------------------------------------
# compute_dhash
# ---------------------------------------------------------------------------


class TestComputeDhash:
    def test_returns_16_char_hex(self, tmp_path):
        p = _make_png(tmp_path / "img.png")
        h = compute_dhash(p)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic_same_image(self, tmp_path):
        p1 = _make_png(tmp_path / "a.png", color=(10, 20, 30))
        p2 = _make_png(tmp_path / "b.png", color=(10, 20, 30))
        assert compute_dhash(p1) == compute_dhash(p2)

    def test_different_images_differ(self, tmp_path):
        p1 = _make_gradient_png(tmp_path / "asc.png", ascending=True)
        p2 = _make_gradient_png(tmp_path / "desc.png", ascending=False)
        h1 = compute_dhash(p1)
        h2 = compute_dhash(p2)
        assert h1 != h2

    def test_bad_file_returns_empty(self, tmp_path):
        p = tmp_path / "bad.jpg"
        p.write_bytes(b"definitely not an image")
        assert compute_dhash(p) == ""

    def test_missing_file_returns_empty(self, tmp_path):
        assert compute_dhash(tmp_path / "ghost.jpg") == ""

    def test_empty_phash_guard_in_lookup(self):
        """Empty string from compute_dhash → lookup_hash returns None without hitting DB."""
        assert lookup_hash("", "alt_text") is None


# ---------------------------------------------------------------------------
# lookup_hash
# ---------------------------------------------------------------------------


class TestLookupHash:
    def test_miss_returns_none(self, monkeypatch):
        cur = _MockCursor(fetchone_result=None)
        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", _mock_conn_factory(cur))
        assert lookup_hash("abcd1234abcd1234", "alt_text") is None

    def test_hit_returns_dict_from_string(self, monkeypatch):
        payload = {"alt_text": "A watch", "seo_caption": "Vintage Elgin."}
        cur = _MockCursor(fetchone_result=(json.dumps(payload),))
        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", _mock_conn_factory(cur))
        result = lookup_hash("abcd1234abcd1234", "alt_text")
        assert result == payload

    def test_hit_returns_dict_already_parsed(self, monkeypatch):
        payload = {"title": "Thing", "category": "Stuff", "description": "Desc", "condition": "Good"}
        cur = _MockCursor(fetchone_result=(payload,))
        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", _mock_conn_factory(cur))
        result = lookup_hash("ffff000000001234", "ai_identify")
        assert result == payload

    def test_db_error_returns_none(self, monkeypatch):
        """DB exception must not propagate — fail-open."""

        @contextmanager
        def boom():
            raise RuntimeError("DB is down")
            yield  # pragma: no cover

        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", boom)
        assert lookup_hash("abcd1234abcd1234", "alt_text") is None

    def test_empty_phash_skips_db(self, monkeypatch):
        called = []

        @contextmanager
        def sentinel():
            called.append(True)
            yield MagicMock()

        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", sentinel)
        result = lookup_hash("", "alt_text")
        assert result is None
        assert called == []  # DB never touched


# ---------------------------------------------------------------------------
# store_hash
# ---------------------------------------------------------------------------


class TestStoreHash:
    def test_inserts_row(self, monkeypatch):
        cur = _MockCursor()
        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", _mock_conn_factory(cur))
        store_hash("abcd1234abcd1234", "tgw202601011200123", "alt_text", {"alt_text": "A", "seo_caption": "B"})
        assert len(cur.inserted) == 1
        params = cur.inserted[0]
        assert params[0] == "abcd1234abcd1234"
        assert params[1] == "alt_text"
        assert params[2] == "tgw202601011200123"

    def test_empty_phash_is_noop(self, monkeypatch):
        called = []

        @contextmanager
        def sentinel():
            called.append(True)
            yield MagicMock()

        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", sentinel)
        store_hash("", "tgw202601011200123", "alt_text", {"alt_text": "A"})
        assert called == []  # DB never touched

    def test_db_error_is_silent(self, monkeypatch):
        """Store failure must not propagate — non-critical cache write."""

        @contextmanager
        def boom():
            raise RuntimeError("DB is down")
            yield  # pragma: no cover

        import tgw.queue.state_machine as sm

        monkeypatch.setattr(sm, "_conn", boom)
        store_hash("abcd1234abcd1234", "tgw202601011200123", "alt_text", {"alt_text": "A"})
        # No exception raised = pass


# ---------------------------------------------------------------------------
# Integration: alt_text wiring (cache hit skips call_model)
# ---------------------------------------------------------------------------


class TestAltTextCacheIntegration:
    def _make_cfg(self, tmp_path: Path) -> dict:
        return {"itemdata_root": tmp_path / "ItemData", "pretty": False}

    def _make_item(self, cfg: dict, sku: str) -> None:
        sku_dir = Path(cfg["itemdata_root"]) / sku
        sku_dir.mkdir(parents=True, exist_ok=True)
        (sku_dir / f"{sku}.json").write_text(json.dumps({"sku": sku}), encoding="utf-8")

    def _add_photo(self, cfg: dict, sku: str) -> Path:
        sku_dir = Path(cfg["itemdata_root"]) / sku
        p = sku_dir / f"{sku}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 16)
        return p

    def test_cache_hit_skips_api(self, tmp_path, monkeypatch):
        import tgw.alt_text as alt_mod

        cfg = self._make_cfg(tmp_path)
        sku = "tgw202601011200001"
        self._make_item(cfg, sku)
        self._add_photo(cfg, sku)

        cached_payload = {"alt_text": "Cached result", "seo_caption": "Cached caption."}

        # compute_dhash returns a valid hash; lookup_hash hits the cache
        monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda p, hash_size=8: "abcd1234abcd1234")
        monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda h, t: cached_payload if h else None)
        monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **kw: None)

        api_called = []
        monkeypatch.setattr(alt_mod, "call_model", lambda *a, **kw: api_called.append(True) or "{}")
        monkeypatch.setattr(alt_mod, "_encode_resized", lambda p, max_px=512: "FAKE==")

        result = alt_mod.cmd_alt_text(cfg, sku=sku, provider="openrouter", model="test-model")

        assert result["ok"] is True
        assert result["cache_hit"] is True
        assert result["alt_text"] == "Cached result"
        assert api_called == []  # API never called

    def test_cache_miss_calls_api_and_stores(self, tmp_path, monkeypatch):
        import tgw.alt_text as alt_mod

        cfg = self._make_cfg(tmp_path)
        sku = "tgw202601011200002"
        self._make_item(cfg, sku)
        self._add_photo(cfg, sku)

        stored = []
        monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda p, hash_size=8: "beef1234beef1234")
        monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda h, t: None)
        monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **kw: stored.append(a))

        monkeypatch.setattr(alt_mod, "_encode_resized", lambda p, max_px=512: "FAKE==")
        monkeypatch.setattr(
            alt_mod,
            "call_model",
            lambda *a, **kw: json.dumps({"alt_text": "Fresh result", "seo_caption": "Fresh caption."}),
        )

        result = alt_mod.cmd_alt_text(cfg, sku=sku, provider="openrouter", model="test-model")

        assert result["ok"] is True
        assert result["cache_hit"] is False
        assert result["alt_text"] == "Fresh result"
        assert len(stored) == 1
        assert stored[0][0] == "beef1234beef1234"
        assert stored[0][2] == "alt_text"
