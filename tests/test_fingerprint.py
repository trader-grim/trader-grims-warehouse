"""Round 4 #31 — tests for the Pillow-only fingerprint module (PP-VISION-001 P1).

Pure: generates tiny patterned images, builds the index in tmp_path, and checks
that a near-duplicate query ranks its own SKU first. No numpy, no live catalog.
"""

import json
import sqlite3

import pytest

# Pillow is the optional `thumbnails` extra; skip cleanly where it is absent
# (e.g. a base CI install) — mirrors the importorskip("httpx") guard in
# tests/test_http_server.py.
pytest.importorskip("PIL", reason="Pillow (thumbnails extra) required for fingerprint tests")

from PIL import Image  # noqa: E402

import tgw.fingerprint as fp  # noqa: E402


def _img(pattern: str, size=(64, 64)) -> Image.Image:
    """Make a distinct test image by pattern name.

    Patterns are chosen so BOTH signals discriminate: 'vstripes' has a strong
    alternating dHash, 'gradient'/'rgradient' are dHash opposites, 'red' is a
    flat colour the histogram separates.
    """
    im = Image.new("RGB", size, (0, 0, 0))
    px = im.load()
    w, h = size
    for y in range(h):
        for x in range(w):
            if pattern == "vstripes":        # vertical black/white stripes
                px[x, y] = (255, 255, 255) if (x // 8) % 2 else (0, 0, 0)
            elif pattern == "gradient":      # L→R increasing brightness
                v = int(255 * x / w)
                px[x, y] = (v, v, v)
            elif pattern == "rgradient":     # R→L (decreasing) — dHash opposite
                v = int(255 * (w - 1 - x) / w)
                px[x, y] = (v, v, v)
            elif pattern == "red":           # solid red
                px[x, y] = (220, 20, 20)
    return im


def _make_thumbs(tmp_path):
    thumbs = tmp_path / "thumbnails"
    thumbs.mkdir()
    skus = {"tgwAAA": "vstripes", "tgwBBB": "gradient",
            "tgwCCC": "rgradient", "tgwDDD": "red"}
    for sku, pat in skus.items():
        _img(pat).save(thumbs / f"{sku}.jpg")
    return thumbs, skus


def _make_catalog(tmp_path, size_classes):
    """Minimal sqlite catalog with sku + data(JSON with size_class)."""
    cat = tmp_path / "cat.db"
    con = sqlite3.connect(cat)
    con.execute("CREATE TABLE catalog (sku TEXT PRIMARY KEY, data TEXT)")
    for sku, sc in size_classes.items():
        con.execute("INSERT INTO catalog (sku, data) VALUES (?, ?)",
                    (sku, json.dumps({"size_class": sc})))
    con.commit()
    con.close()
    return cat


def _cfg(tmp_path, thumbs, cat=None):
    return {"thumbnail_root": str(thumbs),
            "fingerprint_index_path": str(tmp_path / "fp.db"),
            "sqlite_catalog_path": str(cat) if cat else str(tmp_path / "missing.db")}


def test_build_index_counts(tmp_path):
    thumbs, skus = _make_thumbs(tmp_path)
    cfg = _cfg(tmp_path, thumbs)
    out = fp.build_fingerprint_index(cfg)
    assert out["ok"] is True
    assert out["count"] == 4
    assert out["source_count"] == 4
    assert (tmp_path / "fp.db").exists()


def test_check_only_writes_nothing(tmp_path):
    thumbs, _ = _make_thumbs(tmp_path)
    cfg = _cfg(tmp_path, thumbs)
    out = fp.build_fingerprint_index(cfg, check_only=True)
    assert out["ok"] is True and out["check_only"] is True
    assert out["count"] == 4
    assert not (tmp_path / "fp.db").exists()


def test_near_duplicate_ranks_first(tmp_path):
    thumbs, skus = _make_thumbs(tmp_path)
    cfg = _cfg(tmp_path, thumbs)
    fp.build_fingerprint_index(cfg)

    # A re-encoded copy of the vstripes image should match tgwAAA best —
    # unique in both dHash (alternating) and histogram (50/50 b/w).
    query = tmp_path / "query.jpg"
    _img("vstripes").save(query)
    out = fp.locate_image(cfg, query, top=4)
    assert out["ok"] is True
    assert out["matches"][0]["sku"] == "tgwAAA"
    assert out["matches"][0]["distance"] < 0.05  # essentially identical


def test_size_class_filter(tmp_path):
    thumbs, skus = _make_thumbs(tmp_path)
    cat = _make_catalog(tmp_path, {"tgwAAA": "flat", "tgwBBB": "flat",
                                   "tgwCCC": "small_box", "tgwDDD": "small_box"})
    cfg = _cfg(tmp_path, thumbs, cat)
    fp.build_fingerprint_index(cfg)

    query = tmp_path / "q.jpg"
    _img("gradient").save(query)
    out = fp.locate_image(cfg, query, size_class="small_box", top=10)
    assert out["ok"] is True
    returned = {m["sku"] for m in out["matches"]}
    assert returned == {"tgwCCC", "tgwDDD"}  # only small_box candidates


def test_dhash_identical_zero_distance():
    a = fp.dhash(_img("gradient"))
    b = fp.dhash(_img("gradient"))
    assert fp.hamming(a, b) == 0
    # Opposite horizontal gradients → maximally different dHash.
    assert fp.hamming(fp.dhash(_img("gradient")), fp.dhash(_img("rgradient"))) > 0


def test_histogram_distance_bounds():
    h1 = fp.color_histogram(_img("red"))
    assert fp.histogram_distance(h1, h1) == 0.0
    d = fp.histogram_distance(h1, fp.color_histogram(_img("gradient")))
    assert 0.0 < d <= 1.0


def test_missing_index_returns_not_ok(tmp_path):
    thumbs, _ = _make_thumbs(tmp_path)
    cfg = _cfg(tmp_path, thumbs)  # never built
    query = tmp_path / "q.jpg"
    _img("red").save(query)
    out = fp.locate_image(cfg, query)
    assert out["ok"] is False
    assert "not built" in out["error"]


def test_size_class_enriched_from_catalog(tmp_path):
    thumbs, _ = _make_thumbs(tmp_path)
    cat = _make_catalog(tmp_path, {"tgwAAA": "flat"})
    cfg = _cfg(tmp_path, thumbs, cat)
    fp.build_fingerprint_index(cfg)
    con = sqlite3.connect(tmp_path / "fp.db")
    sc = dict(con.execute("SELECT sku, size_class FROM fingerprints"))
    con.close()
    assert sc["tgwAAA"] == "flat"
    assert sc["tgwBBB"] == ""  # not in catalog → empty
