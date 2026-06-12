"""
tgw.catalog_export — Portable catalog export (PP-PORTABLE-CATALOG-001).

Phase 1: copies the SQLite catalog and thumbnail subset into a destination
directory for Syncthing transport.

Phase 2 additions:
- Atomic backup via ``sqlite3.Connection.backup()`` (safe while catalog is live).
- Optional Syncthing push: pass ``push_folder_id`` to trigger a folder rescan
  immediately after export so satellites pick up the new file without waiting
  for Syncthing's scheduled scan interval.
- ``/api/catalog/snapshot`` HTTP endpoint (in http_server.py) for Flutter pulls.

Source SQLite db: ``cfg['sqlite_catalog_path']``.
Thumbnails: ``cfg['thumbnail_root']``/<SKU>.jpg.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def _atomic_backup(src: Path, dst: Path) -> int:
    """
    Copy a live SQLite database atomically using sqlite3.Connection.backup().
    Returns the size of the destination file in bytes.
    Safe to run while the source database has open connections or active writes.
    """
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(dst))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()
    return dst.stat().st_size


def export_catalog(cfg: Dict[str, Any],
                   dest: Any,
                   *,
                   with_thumbnails: bool = True,
                   limit: Optional[int] = None,
                   check_only: bool = False,
                   push_folder_id: Optional[str] = None) -> Dict[str, Any]:
    """Export the SQLite catalog (+ optional thumbnails) to ``dest``.

    Args:
        cfg: config dict; reads ``sqlite_catalog_path`` and ``thumbnail_root``.
        dest: destination directory (str or Path). Created if missing.
        with_thumbnails: also copy ``thumbnail_root``/*.jpg into ``dest/thumbnails/``.
        limit: if set and > 0, cap the number of thumbnails copied (sorted by name).
        check_only: compute what WOULD be copied without writing anything.
        push_folder_id: Syncthing folder ID to rescan after export. When set,
            calls ``tgw.apis.syncthing.scan_folder()`` so satellites pick up the
            new file immediately. Errors are captured in ``syncthing_error`` and
            do not raise (export result is still ``ok: True``).

    Returns a dict with the output contract ``ok`` key plus:
        artifact, dest, db_copied, thumbnails_copied, bytes_total, elapsed_seconds,
        syncthing_pushed (bool).
        check_only additionally sets ``check_only: True``.
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
                'check_only': True,
                'syncthing_pushed': False}

    dest_dir.mkdir(parents=True, exist_ok=True)
    db_dest = dest_dir / 'tgwcatalog.db'
    bytes_total = _atomic_backup(db_src, db_dest)

    thumbnails_copied = 0
    if with_thumbnails and thumb_srcs:
        thumb_dest_dir.mkdir(parents=True, exist_ok=True)
        for src in thumb_srcs:
            out = thumb_dest_dir / src.name
            shutil.copy2(src, out)
            bytes_total += out.stat().st_size
            thumbnails_copied += 1

    result: Dict[str, Any] = {
        'ok': True, 'artifact': 'catalog_export',
        'dest': str(dest_dir), 'db_copied': True,
        'thumbnails_copied': thumbnails_copied,
        'bytes_total': bytes_total,
        'elapsed_seconds': round(time.time() - started, 3),
        'syncthing_pushed': False,
    }

    if push_folder_id:
        try:
            from tgw.apis.syncthing import scan_folder
            scan_folder(cfg, push_folder_id)
            result['syncthing_pushed'] = True
        except Exception as exc:
            result['syncthing_error'] = str(exc)

    return result
