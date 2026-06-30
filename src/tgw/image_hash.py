"""
tgw.image_hash — perceptual hash (dHash) for image deduplication.

Computes a 64-bit difference hash using Pillow only (numpy-free). Used by
alt_text and ai_identify workers to skip redundant vision API calls when the
same physical image appears under multiple SKUs or is re-processed after a rename.

Algorithm (dHash):
  1. Convert to grayscale
  2. Resize to (hash_size+1) × hash_size  — 9×8 for the default 64-bit hash
  3. Each bit: 1 if left pixel > right neighbour, else 0
  4. Encode as 16-character hex string

DB table: state_machine.image_hashes — see queue/image_hashes.sql
  PRIMARY KEY (phash, task): same image may cache different results per task.

Fail-open contract: both lookup_hash and store_hash swallow DB errors so that a
missing table, down database, or schema mismatch never blocks the worker.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_HASH_SIZE = 8  # (hash_size+1) × hash_size pixels → hash_size² bits = 64


def compute_dhash(img_path: Path, hash_size: int = _HASH_SIZE) -> str:
    """Return a 64-bit dHash of *img_path* as a 16-character lowercase hex string.

    Returns ``''`` if Pillow is unavailable or the image cannot be opened.
    Callers treat ``''`` as "no hash" and skip the DB lookup.
    """
    try:
        from PIL import Image
    except ImportError:
        return ""

    try:
        with Image.open(img_path) as img:
            small = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
            pixels = small.tobytes()  # "L" mode: one byte per pixel, row-major
    except Exception:
        return ""

    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            left = pixels[row * (hash_size + 1) + col]
            right = pixels[row * (hash_size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)

    return f"{bits:016x}"


def lookup_hash(phash: str, task: str) -> Optional[Dict[str, Any]]:
    """Return cached ``result_json`` for *(phash, task)*, or ``None`` on miss/error.

    Fail-open: any DB error logs at DEBUG and returns ``None`` so the caller
    falls through to the live API call.
    """
    if not phash:
        return None
    try:
        from tgw.queue import state_machine

        with state_machine._conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT result_json FROM image_hashes WHERE phash = %s AND task = %s",
                    (phash, task),
                )
                row = cur.fetchone()
        if row is None:
            return None
        raw = row[0]
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        log.debug("image_hash lookup failed (phash=%s task=%s): %s", phash, task, exc)
        return None


def store_hash(phash: str, sku: str, task: str, result: Dict[str, Any]) -> None:
    """Upsert *(phash, task)* → *(sku, result_json)* in ``image_hashes``.

    ON CONFLICT DO NOTHING: first writer wins; subsequent SKUs for the same
    image are still served correctly by lookup.
    Silently ignores errors — cache writes are non-critical.
    """
    if not phash:
        return
    try:
        import psycopg2.extras

        from tgw.queue import state_machine

        with state_machine._conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO image_hashes (phash, task, sku, result_json)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (phash, task) DO NOTHING
                    """,
                    (phash, task, sku, psycopg2.extras.Json(result)),
                )
    except Exception as exc:
        log.debug("image_hash store failed (phash=%s task=%s sku=%s): %s", phash, task, sku, exc)
