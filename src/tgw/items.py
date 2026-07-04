"""
tgw.items — Item read and write operations.

This is the write side of the fence.  All mutations to ItemData go through
here.  Reads that need full item detail also go through here.

Atomic writes are guaranteed — partial writes never happen.
All functions return {'ok': True/False, ...} dicts.
"""

from __future__ import annotations

import contextvars
import datetime
import json
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config import location_dir, sku_dir, sku_json
from .resolver import load_item_doc, resolve

# ---------------------------------------------------------------------------
# Mutation source tracking (PP-AIOPS-001)
# ---------------------------------------------------------------------------
# Set at worker startup or session entry. Inherited by threads and async tasks.
_mutation_source: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_mutation_source", default="api:operator"
)
_session_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_session_id", default=None
)


def set_mutation_context(source: str, session_id: Optional[str] = None) -> None:
    """Set the attribution context for all subsequent mutations in this thread/task."""
    _mutation_source.set(source)
    if session_id is not None:
        _session_id.set(session_id)

# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def _archive_before_overwrite(archive_root: Path, path: Path) -> None:
    """Invariant E5: zip the item's current on-disk JSON into ItemArchive
    before a destructive overwrite. This is the last line of defense for data
    recovery (Prime Directive 1) — 49 item JSONs were unrecoverable from every
    other source on 2026-06-28 and were rescued solely from these zips.
    Archiving failure MUST abort the write (no try/except swallow here);
    a best-effort archive is not what the invariant means by "before"."""
    archive_root = Path(archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    zpath = archive_root / f'{path.stem}.zip'
    ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S%f')
    with zipfile.ZipFile(zpath, 'a', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, arcname=f'{path.name}.{ts}')


def atomic_write_json(path: Path, data: Any, pretty: bool = True,
                      *, archive_root: Optional[Path] = None) -> None:
    """Write JSON atomically via a temp file + rename.

    NamedTemporaryFile creates the temp file at mode 0600 regardless of the
    parent directory's permissions or any default ACL in place — an ACL can
    only constrain a requested mode downward, not grant access the creator
    explicitly excluded. Left alone, every atomic write here would silently
    revert the file to owner-only, undoing shared group-write permissions
    (confirmed live in session 41 on docs/TGW-Plan-Vault). Explicitly chmod
    the temp file before the rename so the final file keeps the target's
    existing mode (or 0o660, the group-writable default, for a new file).

    ``archive_root``: pass ``cfg['archive_root']`` to enforce invariant E5 —
    if the target already exists (a real overwrite, not first creation), its
    current content is archived to ``archive_root/<sku>.zip`` before the
    overwrite proceeds. Omit only for non-item writes (catalogs, digests,
    caches) that aren't covered by E5.
    """
    if archive_root is not None and path.exists():
        _archive_before_overwrite(archive_root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        want_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        want_mode = 0o660
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', delete=False, dir=path.parent
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False,
                  indent=2 if pretty else None, sort_keys=False)
        tmp.write('\n')
        tmp_path = Path(tmp.name)
    os.chmod(tmp_path, want_mode)
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
        from .resolver import find_current_sku
        current = find_current_sku(cfg, sku)
        if current:
            path = sku_json(cfg, current)
        else:
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
    if field == 'qty':
        try:
            qty_f = float(value)
        except (TypeError, ValueError):
            raise ValueError(f'qty must be numeric: {value!r}')
        if qty_f < 0:
            raise ValueError(f'qty cannot be negative: {value!r}')
    doc = load_item_doc(path)
    before = doc.get(field)
    doc[field] = value
    if field != 'catalog_verified':
        doc.pop('catalog_verified', None)
    atomic_write_json(path, doc, pretty=True, archive_root=cfg.get('archive_root'))
    # Publish to audit stream (PP-AIOPS-001 Phase 1) — fire-and-forget
    try:
        from .apis.nats_client import publish_mutation
        publish_mutation(
            sku=sku, field=field,
            old_value=before, new_value=value,
            source=_mutation_source.get(),
            session_id=_session_id.get(),
        )
    except Exception:
        pass
    return {'sku': sku, 'field': field, 'before': before, 'after': value}


def strip_fields(cfg: Dict[str, Any], sku: str, fields: List[str],
                 check_only: bool = False) -> Dict[str, Any]:
    """Remove a set of top-level fields from one item's JSON in a single
    write — one archive entry per item (E5), not one per field. Fields
    absent from the doc are silently skipped (idempotent). Used by the
    legacy-field data-scrub pass (todo #1053)."""
    path = sku_json(cfg, sku)
    if not path.exists():
        return {'ok': False, 'error': f'sku not found: {sku!r}'}
    doc = load_item_doc(path)
    present = [f for f in fields if f in doc]
    if check_only:
        return {'ok': True, 'sku': sku, 'removed': present, 'check_only': True}
    if not present:
        return {'ok': True, 'sku': sku, 'removed': []}
    for f in present:
        doc.pop(f, None)
    doc.pop('catalog_verified', None)
    atomic_write_json(path, doc, pretty=cfg.get('pretty', True),
                      archive_root=cfg.get('archive_root'))
    return {'ok': True, 'sku': sku, 'removed': present}


def set_fields(cfg: Dict[str, Any], sku: str, fields: Dict[str, Any],
               only_if_absent: bool = True,
               check_only: bool = False) -> Dict[str, Any]:
    """Set a set of top-level fields on one item's JSON in a single write —
    one archive entry per item (E5), not one per field. When
    only_if_absent=True (default), a field already present with a truthy
    value is left untouched — this makes the caller safe for repeat
    backfill runs (never clobbers a stronger/newer signal with an older
    recovered one). Used by the category-recompile pass (todo #1135)."""
    path = sku_json(cfg, sku)
    if not path.exists():
        return {'ok': False, 'error': f'sku not found: {sku!r}'}
    doc = load_item_doc(path)
    to_set = {}
    for f, v in fields.items():
        if only_if_absent and doc.get(f):
            continue
        to_set[f] = v
    if check_only:
        return {'ok': True, 'sku': sku, 'would_set': to_set, 'check_only': True}
    if not to_set:
        return {'ok': True, 'sku': sku, 'set': {}}
    doc.update(to_set)
    doc.pop('catalog_verified', None)
    atomic_write_json(path, doc, pretty=cfg.get('pretty', True),
                      archive_root=cfg.get('archive_root'))
    return {'ok': True, 'sku': sku, 'set': to_set}


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
    doc.pop('catalog_verified', None)
    atomic_write_json(path, doc, pretty=True, archive_root=cfg.get('archive_root'))
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


# ---------------------------------------------------------------------------
# PP-BULKEDIT-001 — filter → preview → apply bulk editor (CLI + web share this)
# ---------------------------------------------------------------------------

# JSON key that each editable field maps to (status uses the legacy #STATUS key).
BULK_FIELD_KEYS: Dict[str, str] = {
    'title':            'title',
    'location':         'location',
    'status':           '#STATUS',
    'ai_hint':          'ai_hint',
    'shipping_profile': 'shipping_profile',
}


def _bulk_write(cfg: Dict[str, Any], field: str, sku: str, value: str) -> Dict[str, Any]:
    """Route one field write through the correct fence helper."""
    if field == 'location':
        # location needs the symlink tree kept in sync
        return locationupdate(cfg, sku, value)
    return update_item(cfg, sku, BULK_FIELD_KEYS[field], value)


def bulk_edit(cfg: Dict[str, Any], selectors: Dict[str, Any], field: str,
              value: str, apply: bool = False, limit: int = 0) -> Dict[str, Any]:
    """
    Bulk-edit one field across the items matching *selectors*.

    Dry-run by default (``apply=False``) — returns the matched items with their
    current vs. proposed value and writes nothing.  With ``apply=True`` the
    change is written through the per-field fence helper (location keeps its
    symlink tree in sync; ai_hint is set without re-queuing identification so a
    large batch can't flood the ai_identify queue).

    Editable fields: title, location, status, ai_hint, shipping_profile.
    """
    if field not in BULK_FIELD_KEYS:
        return {'ok': False,
                'error': f'field not editable: {field!r}; '
                         f'allowed: {sorted(BULK_FIELD_KEYS)}'}

    skus = sorted(resolve(cfg, **selectors)) if selectors else []
    if limit > 0:  # negative would slice from the end — treat as "no cap"
        skus = skus[:limit]

    key = BULK_FIELD_KEYS[field]
    preview: List[Dict[str, Any]] = []
    for sku in skus:
        path = sku_json(cfg, sku)
        if not path.exists():
            continue
        doc = load_item_doc(path)
        preview.append({
            'sku':      sku,
            'title':    str(doc.get('title', '')),
            'current':  doc.get(key, ''),
            'proposed': value,
        })

    if not apply:
        return {'ok': True, 'field': field, 'value': value,
                'count': len(preview), 'preview': preview, 'applied': False}

    updated: List[str] = []
    failed: List[Dict[str, Any]] = []
    for row in preview:
        res = _bulk_write(cfg, field, row['sku'], value)
        if res.get('ok'):
            updated.append(row['sku'])
        else:
            failed.append({'sku': row['sku'], 'error': res.get('error')})

    return {'ok': len(failed) == 0, 'field': field, 'value': value,
            'count': len(updated), 'updated': updated, 'failed': failed,
            'applied': True}
