"""
tgw.catalog_export — Portable catalog export (PP-PORTABLE-CATALOG-001 Phase 1).

Copies the SQLite catalog and a subset of per-SKU thumbnails into a destination
directory so Syncthing can carry it to a tablet or spare client machine.

Phase 1 is export-only. Source SQLite db lives at ``cfg['sqlite_catalog_path']``
and thumbnails at ``cfg['thumbnail_root']``/<SKU>.jpg.

Stdlib only.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def export_catalog(cfg: Dict[str, Any],
                   dest: Any,
                   *,
                   with_thumbnails: bool = True,
                   limit: Optional[int] = None,
                   check_only: bool = False) -> Dict[str, Any]:
    """Export the SQLite catalog (+ optional thumbnails) to ``dest``.

    Args:
        cfg: config dict; reads ``sqlite_catalog_path`` and ``thumbnail_root``.
        dest: destination directory (str or Path). Created if missing.
        with_thumbnails: also copy ``thumbnail_root``/*.jpg into ``dest/thumbnails/``.
        limit: if set and > 0, cap the number of thumbnails copied (sorted by name).
        check_only: compute what WOULD be copied without writing anything.

    Returns a dict with the output contract ``ok`` key plus:
        artifact, dest, db_copied, thumbnails_copied, bytes_total, elapsed_seconds.
        check_only runs additionally set ``check_only: True``.
    """
    started = time.time()
    db_src = Path(cfg['sqlite_catalog_path'])
    dest_dir = Path(dest)
    thumb_dest_dir = dest_dir / 'thumbnails'

    if not db_src.exists():
        return {'ok': False, 'artifact': 'catalog_export',
                'error': 'sqlite catalog not built — run tgw build-sqlite',
                'dest': str(dest_dir),
                'elapsed_seconds': round(time.time() - started, 3)}

    # Gather the thumbnail list (sorted by name) that would be copied.
    thumb_srcs: List[Path] = []
    if with_thumbnails:
        thumb_root = Path(cfg['thumbnail_root'])
        if thumb_root.is_dir():
            thumb_srcs = sorted(thumb_root.glob('*.jpg'))
            if limit and limit > 0:
                thumb_srcs = thumb_srcs[:limit]

    if check_only:
        bytes_total = db_src.stat().st_size + sum(p.stat().st_size for p in thumb_srcs)
        return {'ok': True, 'artifact': 'catalog_export',
                'dest': str(dest_dir), 'db_copied': True,
                'thumbnails_copied': len(thumb_srcs),
                'bytes_total': bytes_total,
                'elapsed_seconds': round(time.time() - started, 3),
                'check_only': True}

    dest_dir.mkdir(parents=True, exist_ok=True)
    db_dest = dest_dir / 'tgwcatalog.db'
    shutil.copy2(db_src, db_dest)
    bytes_total = db_dest.stat().st_size

    thumbnails_copied = 0
    if with_thumbnails and thumb_srcs:
        thumb_dest_dir.mkdir(parents=True, exist_ok=True)
        for src in thumb_srcs:
            out = thumb_dest_dir / src.name
            shutil.copy2(src, out)
            bytes_total += out.stat().st_size
            thumbnails_copied += 1

    return {'ok': True, 'artifact': 'catalog_export',
            'dest': str(dest_dir), 'db_copied': True,
            'thumbnails_copied': thumbnails_copied,
            'bytes_total': bytes_total,
            'elapsed_seconds': round(time.time() - started, 3)}
