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


def _scalar(doc: Dict[str, Any], key: str) -> str:
    val = doc.get(key, '')
    if isinstance(val, (list, dict)) or val is None:
        return ''
    return str(val)


def _price_col(doc: Dict[str, Any]) -> str:
    """Resolve display price: top-level → ebay_offer → draft_listing."""
    p = doc.get('price')
    if p is not None:
        return str(p)
    eo = doc.get('ebay_offer') or {}
    ep = eo.get('price')
    if ep is not None:
        return str(ep)
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
            _scalar(r, '#STATUS') or _scalar(r, 'status'),
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
