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
import logging
import os
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config import location_dir, sku_dir, sku_json
from .resolver import load_item_doc, resolve

log = logging.getLogger(__name__)

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


def _atomic_write(path: Path, write_body: Any, *,
                  archive_root: Optional[Path] = None) -> None:
    """Shared core: temp file + chmod + rename, with optional archive-before-
    overwrite (invariant E5). ``write_body`` is called with the open temp
    file handle and does the actual serialization (json.dump vs plain text).

    NamedTemporaryFile creates the temp file at mode 0600 regardless of the
    parent directory's permissions or any default ACL in place — an ACL can
    only constrain a requested mode downward, not grant access the creator
    explicitly excluded. Left alone, every atomic write here would silently
    revert the file to owner-only, undoing shared group-write permissions
    (confirmed live in session 41 on docs/TGW-Plan-Vault). Explicitly chmod
    the temp file before the rename so the final file keeps the target's
    existing mode (or 0o660, the group-writable default, for a new file).
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
        write_body(tmp)
        tmp_path = Path(tmp.name)
    os.chmod(tmp_path, want_mode)
    os.replace(tmp_path, path)


def atomic_write_json(path: Path, data: Any, pretty: bool = True,
                      *, archive_root: Optional[Path] = None,
                      sort_keys: bool = False) -> None:
    """Write JSON atomically via a temp file + rename.

    ``archive_root``: pass ``cfg['archive_root']`` to enforce invariant E5 —
    if the target already exists (a real overwrite, not first creation), its
    current content is archived to ``archive_root/<sku>.zip`` before the
    overwrite proceeds. Omit only for non-item writes (catalogs, digests,
    caches) that aren't covered by E5.

    ``sort_keys``: off by default (matches historical behavior of most
    callers). Pass True for callers that relied on deterministic, diffable
    key order (e.g. itemdata_scrub.py, audit#1143 #1235 follow-up).
    """
    def _write(tmp):
        json.dump(data, tmp, ensure_ascii=False,
                  indent=2 if pretty else None, sort_keys=sort_keys)
        tmp.write('\n')
    _atomic_write(path, _write, archive_root=archive_root)


def atomic_write_text(path: Path, text: str, *,
                      archive_root: Optional[Path] = None) -> None:
    """Write plain text atomically via temp file + rename.

    Same tmp+rename+chmod guarantee as ``atomic_write_json``, for the
    non-JSON durable docs (e.g. the Master Plan) that also need it —
    audit#1143 #1162+#1177 found several call sites writing straight to the
    target path with plain ``write_text``, risking a truncated file on crash
    mid-write and, for anything with prior content worth keeping, an
    unrecoverable overwrite (invariant E5).

    ``archive_root``: same contract as ``atomic_write_json`` — if the target
    already exists, its current content is zipped into
    ``archive_root/<stem>.zip`` before the overwrite proceeds.
    """
    _atomic_write(path, lambda tmp: tmp.write(text), archive_root=archive_root)


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
    import uuid

    from .item_mutation import ABSENT_GENERATION, mutate_item

    path = sku_json(cfg, sku)
    result = mutate_item(cfg, 'legacy-' + uuid.uuid4().hex, sku, 'create',
                         ABSENT_GENERATION, {'data': data})
    if result['status'] == 'CONFLICT':
        raise FileExistsError(f'item already exists: {path}')
    if result['status'] != 'COMMITTED':
        raise RuntimeError(f"item create {result['status']}: {result.get('reason') or result.get('failures')}")
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
    fields = {field: value}
    delete_fields = (['catalog_verified']
                     if field != 'catalog_verified' and 'catalog_verified' in doc else [])
    from .item_mutation import legacy_mutate
    result = legacy_mutate(cfg, sku, 'set',
                           {'fields': fields, 'delete_fields': delete_fields})
    if result['status'] != 'COMMITTED':
        raise RuntimeError(f"item mutation {result['status']}: {result.get('failures') or result.get('reason')}")
    # Preserve the old compatibility shape while exposing transaction truth.
    return {'sku': sku, 'field': field, 'before': before, 'after': value,
            'status': 'COMMITTED'}


def strip_fields(cfg: Dict[str, Any], sku: str, fields: List[str],
                 check_only: bool = False) -> Dict[str, Any]:
    """Remove a set of top-level fields from one item's JSON in a single
    write — one archive entry per item (E5), not one per field. Fields
    absent from the doc are silently skipped (idempotent). Used by the
    legacy-field data-scrub pass (todo #1053).

    Side effect: also clears 'catalog_verified' whenever any field is
    actually removed (the catalog-derived hall-pass no longer reflects the
    doc's new contents). Every caller inherits this — audit#1143 #1244
    follow-up found it undocumented and surprising when data_scrub_magento.py
    started routing through this function."""
    path = sku_json(cfg, sku)
    if not path.exists():
        return {'ok': False, 'error': f'sku not found: {sku!r}'}
    doc = load_item_doc(path)
    present = [f for f in fields if f in doc]
    if check_only:
        return {'ok': True, 'sku': sku, 'removed': present, 'check_only': True}
    if not present:
        return {'ok': True, 'sku': sku, 'removed': []}
    from .item_mutation import legacy_mutate
    remove = list(present)
    if 'catalog_verified' in doc:
        remove.append('catalog_verified')
    result = legacy_mutate(cfg, sku, 'delete', {'fields': remove})
    if result['status'] != 'COMMITTED':
        return {'ok': False, 'sku': sku, 'status': result['status'],
                'error': result.get('failures') or result.get('reason')}
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
    from .item_mutation import legacy_mutate
    delete_fields = ['catalog_verified'] if 'catalog_verified' in doc else []
    result = legacy_mutate(cfg, sku, 'set',
                           {'fields': to_set, 'delete_fields': delete_fields})
    if result['status'] != 'COMMITTED':
        return {'ok': False, 'sku': sku, 'status': result['status'],
                'error': result.get('failures') or result.get('reason')}
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
        from .item_mutation import legacy_mutate
        result = legacy_mutate(cfg, sku, 'set',
                               {'fields': {'location': new_location},
                                'delete_fields': (['catalog_verified']
                                                  if 'catalog_verified' in doc else [])})
        if result['status'] != 'COMMITTED':
            return {'ok': False, 'sku': sku, 'status': result['status'],
                    'error': result.get('failures') or result.get('reason')}
        return {'ok': True, 'sku': sku, 'old_location': old_location,
                'new_location': new_location, 'status': result['status']}
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
    from .item_mutation import legacy_mutate
    result = legacy_mutate(cfg, sku, 'set',
                           {'fields': {'verified': value, '#STATUS': 'In Stock'},
                            'delete_fields': (['catalog_verified']
                                              if 'catalog_verified' in doc else [])})
    if result['status'] != 'COMMITTED':
        return {'ok': False, 'sku': sku, 'field': 'verified', 'value': value,
                'status': result['status'],
                'error': result.get('failures') or result.get('reason')}
    return {'ok': True, 'sku': sku, 'field': 'verified', 'value': value,
            'status': result['status']}


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
