"""
tgw.items — Item read and write operations.

This is the write side of the fence.  All mutations to ItemData go through
here.  Reads that need full item detail also go through here.

Atomic writes are guaranteed — partial writes never happen.
All functions return {'ok': True/False, ...} dicts.
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Set  # remove Optional

from .config import location_dir, sku_dir, sku_json
from .resolver import load_item_doc, resolve

# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: Any, pretty: bool = True) -> None:
    """Write JSON atomically via a temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', delete=False, dir=path.parent
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False,
                  indent=2 if pretty else None, sort_keys=False)
        tmp.write('\n')
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


# ---------------------------------------------------------------------------
# Identification history trail
# ---------------------------------------------------------------------------

def append_history_event(item: Dict[str, Any], event: Dict[str, Any]) -> None:
    """Append one event to item['identification_history'], adding ISO ts if absent."""
    if 'ts' not in event:
        event = {'ts': datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ'), **event}
    history: List[Dict[str, Any]] = item.setdefault('identification_history', [])
    history.append(event)


# ---------------------------------------------------------------------------
# Item creation
# ---------------------------------------------------------------------------

def create_item(cfg: Dict[str, Any], sku: str, data: Dict[str, Any]) -> Path:
    """
    Write a new item JSON. Raises if the item already exists.
    Returns the path written.
    """
    path = sku_json(cfg, sku)
    if path.exists():
        raise FileExistsError(f'item already exists: {path}')
    record = {'sku': sku, **data}
    atomic_write_json(path, record, pretty=cfg.get('pretty', True))
    return path


# ---------------------------------------------------------------------------
# Single item read (with media discovery)
# ---------------------------------------------------------------------------

def get_item(cfg: Dict[str, Any], sku: str) -> Dict[str, Any]:
    """
    Load full item record plus discovered media.

    Adds _images and _videos lists to the returned dict.
    Raises FileNotFoundError if the SKU does not exist.
    """
    path = sku_json(cfg, sku)
    if not path.exists():
        raise FileNotFoundError(f'no item JSON for sku {sku!r}: {path}')
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        data = json.load(f)
    images, videos = [], []
    for p in sorted(path.parent.iterdir()):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf in {'.jpg', '.jpeg', '.png', '.gif', '.webp'}:
            images.append(str(p))
        elif suf in {'.mp4', '.mov', '.mkv', '.webm'}:
            videos.append(str(p))
    data['_images'] = images
    data['_videos'] = videos
    return data


# ---------------------------------------------------------------------------
# Location tree maintenance
# ---------------------------------------------------------------------------

def _rebuild_location_link(cfg: Dict[str, Any], sku: str,
                            location: str) -> None:
    """Create/replace the symlink for one SKU in the location tree."""
    target    = sku_dir(cfg, sku)
    link_dir  = location_dir(cfg, location)
    link_path = link_dir / sku
    link_dir.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()
    os.symlink(target, link_path)


def _remove_location_link(cfg: Dict[str, Any], sku: str,
                           location: str) -> None:
    """Remove the symlink for one SKU from the location tree."""
    link_path = location_dir(cfg, location) / sku
    if link_path.exists() or link_path.is_symlink():
        link_path.unlink()


# ---------------------------------------------------------------------------
# Single item write
# ---------------------------------------------------------------------------

def _write_field(cfg: Dict[str, Any], sku: str, field: str,
                 value: Any) -> Dict[str, Any]:
    """Atomic single-field update.  Returns before/after record."""
    path = sku_json(cfg, sku)
    if not path.exists():
        raise FileNotFoundError(f'no item JSON for sku {sku!r}: {path}')
    doc = load_item_doc(path)
    before = doc.get(field)
    doc[field] = value
    atomic_write_json(path, doc, pretty=True)
    return {'sku': sku, 'field': field, 'before': before, 'after': value}


def update_item(cfg: Dict[str, Any], sku: str, field: str, value: Any,
                check_only: bool = False) -> Dict[str, Any]:
    """Update one field on one item."""
    if check_only:
        path = sku_json(cfg, sku)
        if not path.exists():
            return {'ok': False, 'error': f'sku not found: {sku!r}'}
        return {'ok': True, 'sku': sku, 'field': field, 'value': value,
                'check_only': True}
    try:
        change = _write_field(cfg, sku, field, value)
        return {'ok': True, **change}
    except Exception as e:
        return {'ok': False, 'sku': sku, 'field': field, 'error': str(e)}


# ---------------------------------------------------------------------------
# Bulk write operations
# ---------------------------------------------------------------------------

def update_items(cfg: Dict[str, Any], skus: Set[str], field: str,
                 value: Any, check_only: bool = False) -> Dict[str, Any]:
    """
    Bulk update — same field/value across a set of SKUs.

    Claims all work up front (sorted for determinism), processes the full
    set, returns a summary.  Does not stop on individual failures.
    """
    started = time.time()
    updated: List[str] = []
    failed:  List[Dict[str, Any]] = []
    skipped: List[str] = []

    for sku in sorted(skus):
        path = sku_json(cfg, sku)
        if not path.exists():
            skipped.append(sku)
            continue
        if check_only:
            updated.append(sku)
            continue
        try:
            _write_field(cfg, sku, field, value)
            updated.append(sku)
        except Exception as e:
            failed.append({'sku': sku, 'error': str(e)})

    return {
        'ok':              len(failed) == 0,
        'field':           field,
        'value':           value,
        'updated':         updated,
        'failed':          failed,
        'skipped':         skipped,
        'count':           len(updated),
        'elapsed_seconds': round(time.time() - started, 3),
        'check_only':      check_only,
    }


def update_where(cfg: Dict[str, Any], selectors: Dict[str, Any],
                 field: str, value: Any,
                 check_only: bool = False) -> Dict[str, Any]:
    """Resolve a selector set then bulk-update the matching SKUs."""
    skus   = resolve(cfg, **selectors)
    result = update_items(cfg, skus, field, value, check_only=check_only)
    result['selectors'] = selectors
    return result


# ---------------------------------------------------------------------------
# tgw.source replacements
# ---------------------------------------------------------------------------

def titleupdate(cfg: Dict[str, Any], sku: str, value: str,
                check_only: bool = False) -> Dict[str, Any]:
    """Update the title field on one item."""
    return update_item(cfg, sku, 'title', value, check_only=check_only)


def locationupdate(cfg: Dict[str, Any], sku: str, new_location: str,
                   check_only: bool = False) -> Dict[str, Any]:
    """Update location field and keep the location tree in sync."""
    path = sku_json(cfg, sku)
    if not path.exists():
        return {'ok': False, 'error': f'sku not found: {sku!r}'}
    doc = load_item_doc(path)
    old_location = str(doc.get('location', '')).strip()
    if check_only:
        return {'ok': True, 'sku': sku, 'old_location': old_location,
                'new_location': new_location, 'check_only': True}
    try:
        _write_field(cfg, sku, 'location', new_location)
        if old_location and old_location != new_location:
            _remove_location_link(cfg, sku, old_location)
        _rebuild_location_link(cfg, sku, new_location)
        return {'ok': True, 'sku': sku, 'old_location': old_location,
                'new_location': new_location}
    except Exception as e:
        return {'ok': False, 'sku': sku, 'error': str(e)}


def verifiedupdate(cfg: Dict[str, Any], sku: str, value: str,
                   check_only: bool = False) -> Dict[str, Any]:
    """Update verified field and set #STATUS=In Stock atomically."""
    path = sku_json(cfg, sku)
    if not path.exists():
        return {'ok': False, 'error': f'sku not found: {sku!r}'}
    if check_only:
        return {'ok': True, 'sku': sku, 'value': value, 'check_only': True}
    doc = load_item_doc(path)
    doc['verified'] = value
    doc['#STATUS'] = 'In Stock'
    atomic_write_json(path, doc, pretty=True)
    return {'ok': True, 'sku': sku, 'field': 'verified', 'value': value}


def statusupdate(cfg: Dict[str, Any], sku: str, value: str,
                 check_only: bool = False) -> Dict[str, Any]:
    """Update the #STATUS field on one item (legacy name; rename pending in data scrub pass 2)."""
    return update_item(cfg, sku, '#STATUS', value, check_only=check_only)


def catlocmvall(cfg: Dict[str, Any], from_location: str, to_location: str,
                check_only: bool = False) -> Dict[str, Any]:
    """
    Move every item at from_location to to_location.

    Resolves the full set at once, updates all records, rebuilds all
    location tree links.  Returns a summary.
    """
    started = time.time()
    skus = resolve(cfg, location=from_location)
    if not skus:
        return {'ok': True, 'from_location': from_location,
                'to_location': to_location, 'moved': [], 'count': 0,
                'elapsed_seconds': round(time.time() - started, 3)}

    moved: List[str] = []
    failed: List[Dict[str, Any]] = []

    for sku in sorted(skus):
        if check_only:
            moved.append(sku)
            continue
        try:
            _write_field(cfg, sku, 'location', to_location)
            _remove_location_link(cfg, sku, from_location)
            _rebuild_location_link(cfg, sku, to_location)
            moved.append(sku)
        except Exception as e:
            failed.append({'sku': sku, 'error': str(e)})

    return {
        'ok':              len(failed) == 0,
        'from_location':   from_location,
        'to_location':     to_location,
        'moved':           moved,
        'failed':          failed,
        'count':           len(moved),
        'elapsed_seconds': round(time.time() - started, 3),
        'check_only':      check_only,
    }
