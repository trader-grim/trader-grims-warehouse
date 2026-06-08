"""
tgw.fingerprint — Offline visual fingerprint index over catalog thumbnails (PP-VISION-001 Phase 1).

Pillow-only (no numpy): difference-hash (dHash) for structure + a joint RGB
color histogram for colour. Builds a queryable SQLite index over the existing
thumbnail cache; `locate_image()` ranks catalog SKUs by visual similarity to a
query image. Baseline precision — a workflow proof, not a final CLIP matcher.

Index db: ``cfg['fingerprint_index_path']`` (default catalog_root/fingerprints.db).
Source:   ``cfg['thumbnail_root']``/<SKU>.jpg  (one fingerprint per thumbnail).
"""

from __future__ import annotations

import json
import sqlite3
import time
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from PIL import Image

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fingerprints (
    sku        TEXT PRIMARY KEY,
    dhash      TEXT NOT NULL,          -- 64-bit hash as a decimal string (SQLite INTEGER is signed)
    histogram  TEXT NOT NULL,
    size_class TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_fp_size_class ON fingerprints(size_class);
"""

ImageOrPath = Union["Image.Image", str, Path]

# Weighting of the two signals in the combined distance (must sum to 1.0).
_W_DHASH = 0.6
_W_HIST = 0.4


def _as_image(img: ImageOrPath) -> "Image.Image":
    if isinstance(img, Image.Image):
        return img
    return Image.open(img)


def dhash(img: ImageOrPath, hash_size: int = 8) -> int:
    """64-bit difference hash (default hash_size=8 → 8x8 = 64 bits).

    Resize to (hash_size+1, hash_size) grayscale; each bit is 1 where a pixel
    is brighter than its right neighbour. Robust to scale/compression; blind to
    flat colour (that is what the histogram is for).
    """
    im = _as_image(img).convert("L").resize((hash_size + 1, hash_size), Image.BILINEAR)
    px = im.load()
    bits = 0
    bit = 0
    for row in range(hash_size):
        for col in range(hash_size):
            bits |= (1 << bit) if px[col, row] > px[col + 1, row] else 0
            bit += 1
    return bits


def color_histogram(img: ImageOrPath, bins: int = 8) -> List[int]:
    """Joint RGB colour histogram as a flat list of length bins**3.

    Downsamples to 32x32 then buckets each pixel into (bins per channel). Sums
    to the pixel count, so it is comparable across images of equal sample size.
    """
    im = _as_image(img).convert("RGB").resize((32, 32), Image.BILINEAR)
    hist = [0] * (bins * bins * bins)
    scale = bins / 256.0
    data = im.tobytes()  # flat RGBRGB… bytes; avoids the deprecated getdata()
    cap = bins - 1
    for i in range(0, len(data), 3):
        rb = min(int(data[i] * scale), cap)
        gb = min(int(data[i + 1] * scale), cap)
        bb = min(int(data[i + 2] * scale), cap)
        hist[(rb * bins + gb) * bins + bb] += 1
    return hist


def hamming(a: int, b: int) -> int:
    """Population count of the XOR — number of differing bits."""
    return bin(a ^ b).count("1")


def histogram_distance(h1: List[int], h2: List[int]) -> float:
    """Normalized L1 distance in [0, 1] (0 = identical).

    Tolerates unequal lengths by zero-padding the shorter histogram.
    """
    num = 0
    denom = 0
    for a, b in zip_longest(h1, h2, fillvalue=0):
        num += abs(a - b)
        denom += a + b
    return (num / denom) if denom else 0.0


def _size_class_map(sqlite_catalog_path: Optional[str]) -> Dict[str, str]:
    """Map SKU → size_class from the SQLite catalog, if present. Best-effort."""
    if not sqlite_catalog_path:
        return {}
    path = Path(sqlite_catalog_path)
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    try:
        con = sqlite3.connect(path)
        try:
            for sku, data in con.execute("SELECT sku, data FROM catalog"):
                try:
                    out[sku] = json.loads(data).get("size_class", "") or ""
                except (ValueError, TypeError):
                    continue
        finally:
            con.close()
    except sqlite3.Error:
        return {}
    return out


def build_fingerprint_index(cfg: Dict[str, Any], *,
                            limit: Optional[int] = None,
                            check_only: bool = False) -> Dict[str, Any]:
    """Build/refresh the fingerprint index from the thumbnail cache.

    This is an on-demand batch build (same class as `tgw build-thumbnails` /
    `tgw build-sqlite`) — it never runs inline from a worker.
    """
    started = time.time()
    thumb_root = Path(cfg["thumbnail_root"])
    db_path = Path(cfg["fingerprint_index_path"])

    jpgs = sorted(thumb_root.glob("*.jpg")) + sorted(thumb_root.glob("*.JPG"))
    if limit and limit > 0:
        jpgs = jpgs[:limit]

    size_classes = _size_class_map(cfg.get("sqlite_catalog_path"))

    rows: List[tuple] = []
    problems = 0
    for jpg in jpgs:
        sku = jpg.stem
        try:
            with Image.open(jpg) as im:
                im.load()
                dh = dhash(im)
                hist = color_histogram(im)
        except Exception:
            problems += 1
            continue
        rows.append((sku, str(dh), json.dumps(hist), size_classes.get(sku, "")))

    elapsed = round(time.time() - started, 3)
    if check_only:
        return {"ok": True, "artifact": "fingerprint_index", "path": str(db_path),
                "count": len(rows), "source_count": len(jpgs), "problems": problems,
                "elapsed_seconds": elapsed, "check_only": True}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA)
        with con:
            con.executemany(
                """INSERT OR REPLACE INTO fingerprints
                   (sku, dhash, histogram, size_class, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'))""",
                rows,
            )
    finally:
        con.close()

    elapsed = round(time.time() - started, 3)
    return {"ok": True, "artifact": "fingerprint_index", "path": str(db_path),
            "count": len(rows), "source_count": len(jpgs), "problems": problems,
            "elapsed_seconds": elapsed}


def locate_image(cfg: Dict[str, Any], image_path: ImageOrPath, *,
                 size_class: Optional[str] = None, top: int = 10) -> Dict[str, Any]:
    """Rank catalog SKUs by visual similarity to ``image_path``.

    Combined distance = 0.6 * (dHash hamming / 64) + 0.4 * histogram L1.
    Lower is closer. ``size_class`` restricts the candidate set when given.
    """
    db_path = Path(cfg["fingerprint_index_path"])
    if not db_path.exists():
        return {"ok": False,
                "error": "fingerprint index not built — run tgw build-fingerprints"}

    try:
        q_hash = dhash(image_path)
        q_hist = color_histogram(image_path)
    except Exception as exc:
        return {"ok": False, "error": f"could not read query image: {exc}"}

    sql = "SELECT sku, dhash, histogram, size_class FROM fingerprints"
    params: tuple = ()
    if size_class:
        sql += " WHERE size_class = ?"
        params = (size_class,)

    matches: List[Dict[str, Any]] = []
    con = sqlite3.connect(db_path)
    try:
        for sku, dh, hist_json, sc in con.execute(sql, params):
            dh_dist = hamming(q_hash, int(dh)) / 64.0
            try:
                hist = json.loads(hist_json)
            except (ValueError, TypeError):
                hist = []
            h_dist = histogram_distance(q_hist, hist)
            dist = _W_DHASH * dh_dist + _W_HIST * h_dist
            matches.append({"sku": sku, "distance": round(dist, 6),
                            "dhash_distance": round(dh_dist, 6),
                            "hist_distance": round(h_dist, 6),
                            "size_class": sc or ""})
    finally:
        con.close()

    matches.sort(key=lambda m: m["distance"])
    if top and top > 0:
        matches = matches[:top]
    return {"ok": True, "query": str(image_path), "count": len(matches), "matches": matches}
