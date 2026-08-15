"""
tgw.sqlite_catalog — SQLite catalog build.

Maintains a queryable SQLite mirror of the full catalog.
Indexed scalar columns for common queries; full JSON in `data` for everything else.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List

from .resolver import find_item_jsons, load_item_doc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    sku           TEXT PRIMARY KEY,
    title         TEXT,
    location      TEXT,
    status        TEXT,
    price         TEXT,
    qty           TEXT,
    image         TEXT,
    attribute_set TEXT,
    data          TEXT NOT NULL,
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_location ON catalog(location);
CREATE INDEX IF NOT EXISTS idx_status   ON catalog(status);
CREATE INDEX IF NOT EXISTS idx_title    ON catalog(title);
"""

# db_path strings this process has already confirmed the schema exists for
# (upsert_catalog_row) — avoids re-running the idempotent-but-not-free
# executescript on every single per-item upsert call. Process-lifetime only,
# matches other in-process caches in this codebase (e.g. sync.py's
# _policies_cache) — never persisted, never needs invalidating beyond a
# process restart since the schema itself never changes at runtime.
_SCHEMA_ENSURED: set = set()


_TERMINAL_STATUSES = frozenset({
    'sold', 'archived', 'disposed', 'recalled', 'merged', 'discard', 'vero',
    'disposeddisposed',  # known typo variant in data
})


def _scalar(doc: Dict[str, Any], key: str) -> str:
    val = doc.get(key, '')
    if isinstance(val, (list, dict)) or val is None:
        return ''
    return str(val)


def _resolve_status(doc: Dict[str, Any]) -> str:
    """Resolve status: terminal state wins over non-terminal; otherwise status > #STATUS."""
    s = str(doc.get('status') or '').strip()
    hs = str(doc.get('#STATUS') or '').strip()
    if s.lower() in _TERMINAL_STATUSES:
        return s
    if hs.lower() in _TERMINAL_STATUSES:
        return hs
    return s or hs


def _price_col(doc: Dict[str, Any]) -> str:
    """Resolve display price: ebay_offer → top-level → draft_listing."""
    eo = doc.get('ebay_offer') or {}
    ep = eo.get('price')
    if ep is not None:
        return str(ep)
    p = doc.get('price')
    if p is not None:
        return str(p)
    dl = doc.get('draft_listing') or {}
    dp = dl.get('price')
    if dp is not None:
        return str(dp)
    return ''


def build_sqlite_catalog(cfg: Dict[str, Any],
                         check_only: bool = False) -> Dict[str, Any]:
    """Build or refresh the SQLite catalog from ItemData."""
    started = time.time()
    db_path: Path = cfg['sqlite_catalog_path']
    item_jsons = find_item_jsons(cfg)
    rows: List[Dict[str, Any]] = []
    problems: List[str] = []

    for json_path in item_jsons:
        try:
            rows.append(load_item_doc(json_path))
        except Exception as e:
            problems.append(str(e))

    elapsed = round(time.time() - started, 3)
    if problems:
        return {'ok': False, 'artifact': 'sqlite_catalog',
                'problems': problems, 'source_count': len(item_jsons),
                'rows_built': len(rows), 'elapsed_seconds': elapsed}

    if check_only:
        return {'ok': True, 'artifact': 'sqlite_catalog',
                'path': str(db_path), 'source_count': len(item_jsons),
                'rows_built': len(rows), 'elapsed_seconds': elapsed,
                'check_only': True}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    new_rows = [
        (
            _scalar(r, 'sku'),
            _scalar(r, 'title'),
            _scalar(r, 'location'),
            _resolve_status(r),
            _price_col(r),
            _scalar(r, 'qty'),
            _scalar(r, 'image'),
            _scalar(r, 'attribute_set'),
            json.dumps(r, ensure_ascii=False),
        )
        for r in rows
    ]
    con = sqlite3.connect(db_path)
    try:
        con.executescript(_SCHEMA)
        with con:
            # Full replace: delete all then insert — removes stale rows for
            # renamed/deleted SKUs that INSERT OR REPLACE would leave behind.
            con.execute('DELETE FROM catalog')
            con.executemany(
                """INSERT INTO catalog
                   (sku, title, location, status, price, qty, image,
                    attribute_set, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                new_rows,
            )
    finally:
        con.close()

    elapsed = round(time.time() - started, 3)
    return {'ok': True, 'artifact': 'sqlite_catalog',
            'path': str(db_path), 'source_count': len(item_jsons),
            'rows_built': len(rows), 'elapsed_seconds': elapsed}


def upsert_catalog_row(cfg: Dict[str, Any], doc: Dict[str, Any]) -> Dict[str, Any]:
    """Atomic per-item SQLite catalog upsert (PP-CATALOG-INCR-001 CI-2).

    Called synchronously from the fence write path (_apply_patch /
    _apply_ebay_write in http_server.py) right after a successful
    atomic_write_json, so the SQLite catalog — what the inventory webui
    reads — reflects a write immediately, without waiting for the periodic
    full rebuild (CI-4). *doc* is the already-in-memory post-write document;
    no re-read from disk needed. Row shape/column derivation must stay
    identical to build_sqlite_catalog's, so a full rebuild is always a
    no-op reconciliation for any SKU this path already covered.
    """
    sku = _scalar(doc, 'sku')
    if not sku:
        return {'ok': False, 'error': 'doc has no sku'}
    db_path: Path = cfg['sqlite_catalog_path']
    row = (
        sku,
        _scalar(doc, 'title'),
        _scalar(doc, 'location'),
        _resolve_status(doc),
        _price_col(doc),
        _scalar(doc, 'qty'),
        _scalar(doc, 'image'),
        _scalar(doc, 'attribute_set'),
        json.dumps(doc, ensure_ascii=False),
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        # Schema is idempotent (CREATE TABLE/INDEX IF NOT EXISTS) but still
        # costs a parse+execute per call with no effect after the first —
        # real, avoidable overhead in a per-SKU bulk-edit loop (code-review
        # finding, 2026-07-18). Skip once this process has confirmed the
        # schema exists for this db_path.
        if str(db_path) not in _SCHEMA_ENSURED:
            con.executescript(_SCHEMA)
            _SCHEMA_ENSURED.add(str(db_path))
        with con:
            con.execute(
                """INSERT INTO catalog
                   (sku, title, location, status, price, qty, image,
                    attribute_set, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(sku) DO UPDATE SET
                       title=excluded.title, location=excluded.location,
                       status=excluded.status, price=excluded.price,
                       qty=excluded.qty, image=excluded.image,
                       attribute_set=excluded.attribute_set, data=excluded.data,
                       updated_at=excluded.updated_at""",
                row,
            )
    finally:
        con.close()
    return {'ok': True, 'sku': sku}
