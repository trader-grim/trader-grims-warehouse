"""
tgw.catalog — Catalog build operations.

Builds the various catalog projections from ItemData:
  full catalog      — all item records as a JSON array
  search catalog    — projected subset of fields for fast search
  location tree     — symlink tree indexed by location code
  CSV variants      — same data in CSV format

All functions return {'ok': True/False, ...} dicts.
None of them write to ItemData — read only from this module's perspective.
Writes go through tgw.items.
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .config import load_json_strict
from .resolver import find_item_jsons, load_item_doc

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def atomic_write_json(path: Path, data: Any, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', delete=False, dir=path.parent
    ) as tmp:
        json.dump(data, tmp, ensure_ascii=False,
                  indent=2 if pretty else None, sort_keys=False)
        tmp.write('\n')
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def atomic_write_csv(path: Path, rows: List[Dict[str, Any]],
                     fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', newline='', delete=False, dir=path.parent
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames,
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, path)


def get_nested(data: Dict[str, Any], dotted: str, default: Any = '') -> Any:
    cur: Any = data
    for part in dotted.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def normalize_value(value: Any) -> Any:
    if value is None:
        return ''
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def catalog_fieldnames_from_rows(rows: List[Dict[str, Any]],
                                  preferred: Optional[List[str]] = None
                                  ) -> List[str]:
    seen: List[str] = []
    if preferred:
        for key in preferred:
            if key not in seen:
                seen.append(key)
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.append(key)
    return seen


# ---------------------------------------------------------------------------
# Catalog loaders (read existing catalog files)
# ---------------------------------------------------------------------------

def load_full_catalog(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = cfg['full_catalog_path']
    if not path.exists():
        raise FileNotFoundError(f'missing full catalog: {path}')
    try:
        data = load_json_strict(path)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except Exception:
        pass
    # fallback: JSON lines
    rows = []
    with path.open('r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    return rows


def load_search_catalog(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = cfg['search_catalog_path']
    if not path.exists():
        raise FileNotFoundError(f'missing search catalog: {path}')
    data = load_json_strict(path)
    if isinstance(data, dict):
        rows = []
        for sku, row in data.items():
            if isinstance(row, dict):
                row = dict(row)
                row.setdefault('sku', sku)
                rows.append(row)
        return rows
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    raise ValueError(f'{path}: unsupported top-level JSON type')


# ---------------------------------------------------------------------------
# Projection and validation
# ---------------------------------------------------------------------------

def project_search_row(doc: Dict[str, Any],
                       fields: List[str]) -> Dict[str, Any]:
    row: Dict[str, Any] = {}
    for field in fields:
        value = (doc.get('sku', '') if field == 'sku'
                 else get_nested(doc, field, ''))
        row[field] = normalize_value(value)
    return row


def validate_rows(rows: List[Dict[str, Any]],
                  required: List[str]) -> List[str]:
    problems: List[str] = []
    seen: Set[str] = set()
    for idx, row in enumerate(rows, start=1):
        for key in required:
            if str(row.get(key, '')).strip() == '':
                problems.append(f'row {idx}: missing required field {key!r}')
        sku = str(row.get('sku', '')).strip()
        if sku:
            if sku in seen:
                problems.append(f'duplicate sku: {sku}')
            seen.add(sku)
    return problems


# ---------------------------------------------------------------------------
# Build operations
# ---------------------------------------------------------------------------

def build_full_catalog(cfg: Dict[str, Any],
                       check_only: bool = False) -> Dict[str, Any]:
    """Build full catalog JSON from ItemData."""
    started = time.time()
    item_jsons = find_item_jsons(cfg)
    rows, problems = [], []
    for json_path in item_jsons:
        try:
            rows.append(load_item_doc(json_path))
        except Exception as e:
            problems.append(str(e))
    rows.sort(key=lambda x: str(x.get('sku', '')))
    elapsed = round(time.time() - started, 3)
    if problems:
        return {'ok': False, 'artifact': 'full_catalog',
                'problems': problems, 'source_count': len(item_jsons),
                'rows_built': len(rows), 'elapsed_seconds': elapsed}
    if not check_only:
        atomic_write_json(cfg['full_catalog_path'], rows, cfg['pretty'])
    return {'ok': True, 'artifact': 'full_catalog',
            'path': str(cfg['full_catalog_path']),
            'source_mode': 'itemdata', 'source_count': len(item_jsons),
            'rows_built': len(rows), 'elapsed_seconds': elapsed,
            'check_only': check_only}


def build_search_catalog(cfg: Dict[str, Any], source: str = 'auto',
                         check_only: bool = False) -> Dict[str, Any]:
    """Build search catalog JSON (projected fields only)."""
    started = time.time()
    source_mode = source
    if source == 'auto':
        source_mode = ('full_catalog' if cfg['full_catalog_path'].exists()
                       else 'itemdata')
    if source_mode == 'full_catalog':
        docs = load_full_catalog(cfg)
    elif source_mode == 'itemdata':
        docs = [load_item_doc(p) for p in find_item_jsons(cfg)]
    else:
        raise ValueError(f'unsupported source: {source!r}')
    rows = [project_search_row(doc, cfg['search_fields']) for doc in docs]
    problems = validate_rows(rows, cfg['required'])
    elapsed = round(time.time() - started, 3)
    if problems:
        return {'ok': False, 'artifact': 'search_catalog',
                'source_mode': source_mode, 'problems': problems,
                'source_count': len(docs), 'rows_built': len(rows),
                'elapsed_seconds': elapsed}
    if not check_only:
        atomic_write_json(cfg['search_catalog_path'], rows, cfg['pretty'])
    return {'ok': True, 'artifact': 'search_catalog',
            'path': str(cfg['search_catalog_path']),
            'source_mode': source_mode, 'source_count': len(docs),
            'rows_built': len(rows), 'elapsed_seconds': elapsed,
            'check_only': check_only}


def build_location_tree(cfg: Dict[str, Any], source: str = 'auto',
                        check_only: bool = False) -> Dict[str, Any]:
    """Build symlink tree indexed by location code."""
    started = time.time()
    if source == 'auto':
        source = ('search_catalog' if cfg['search_catalog_path'].exists()
                  else ('full_catalog' if cfg['full_catalog_path'].exists()
                        else 'itemdata'))
    if source == 'search_catalog':
        rows = load_search_catalog(cfg)
    elif source == 'full_catalog':
        rows = load_full_catalog(cfg)
    elif source == 'itemdata':
        rows = [load_item_doc(p) for p in find_item_jsons(cfg)]
    else:
        raise ValueError(f'unsupported source: {source!r}')

    dest_root = cfg['location_tree_root']
    built, skipped, problems = 0, 0, []

    if not check_only and dest_root.exists():
        shutil.rmtree(dest_root)
    if not check_only:
        dest_root.mkdir(parents=True, exist_ok=True)

    for row in rows:
        sku      = str(row.get('sku', '')).strip()
        location = str(row.get('location', '')).strip()
        if not sku or not location:
            skipped += 1
            continue
        target = cfg['itemdata_root'] / sku
        if not target.exists():
            if cfg['skip_missing']:
                skipped += 1
                continue
            problems.append(f'missing item dir for sku {sku}: {target}')
            continue
        if not check_only:
            link_dir  = dest_root / location
            link_path = link_dir / sku
            link_dir.mkdir(parents=True, exist_ok=True)
            if link_path.exists() or link_path.is_symlink():
                link_path.unlink()
            os.symlink(target, link_path)
        built += 1

    elapsed = round(time.time() - started, 3)
    if problems:
        return {'ok': False, 'artifact': 'location_tree',
                'source_mode': source, 'problems': problems,
                'rows_seen': len(rows), 'links_built': built,
                'links_skipped': skipped, 'elapsed_seconds': elapsed}
    return {'ok': True, 'artifact': 'location_tree',
            'path': str(dest_root), 'source_mode': source,
            'rows_seen': len(rows), 'links_built': built,
            'links_skipped': skipped, 'elapsed_seconds': elapsed,
            'check_only': check_only}


def build_full_catalog_csv(cfg: Dict[str, Any],
                           check_only: bool = False) -> Dict[str, Any]:
    """Build full catalog CSV from ItemData."""
    started = time.time()
    item_jsons = find_item_jsons(cfg)
    rows, problems = [], []
    for json_path in item_jsons:
        try:
            rows.append(load_item_doc(json_path))
        except Exception as e:
            problems.append(str(e))
    rows.sort(key=lambda x: str(x.get('sku', '')))
    elapsed = round(time.time() - started, 3)
    if problems:
        return {'ok': False, 'artifact': 'full_catalog_csv',
                'problems': problems, 'source_count': len(item_jsons),
                'rows_built': len(rows), 'elapsed_seconds': elapsed}
    if not check_only:
        fieldnames = catalog_fieldnames_from_rows(rows, preferred=['sku'])
        atomic_write_csv(cfg['full_catalog_csv_path'], rows, fieldnames)
    return {'ok': True, 'artifact': 'full_catalog_csv',
            'path': str(cfg['full_catalog_csv_path']),
            'source_count': len(item_jsons), 'rows_built': len(rows),
            'elapsed_seconds': elapsed, 'check_only': check_only}


def build_search_catalog_csv(cfg: Dict[str, Any], source: str = 'auto',
                              check_only: bool = False) -> Dict[str, Any]:
    """Build search catalog CSV."""
    started = time.time()
    source_mode = source
    if source == 'auto':
        source_mode = ('full_catalog' if cfg['full_catalog_path'].exists()
                       else 'itemdata')
    if source_mode == 'full_catalog':
        docs = load_full_catalog(cfg)
    elif source_mode == 'itemdata':
        docs = [load_item_doc(p) for p in find_item_jsons(cfg)]
    else:
        raise ValueError(f'unsupported source: {source!r}')
    rows = [project_search_row(doc, cfg['search_fields']) for doc in docs]
    problems = validate_rows(rows, cfg['required'])
    elapsed = round(time.time() - started, 3)
    if problems:
        return {'ok': False, 'artifact': 'search_catalog_csv',
                'source_mode': source_mode, 'problems': problems,
                'source_count': len(docs), 'rows_built': len(rows),
                'elapsed_seconds': elapsed}
    if not check_only:
        atomic_write_csv(cfg['search_catalog_csv_path'], rows,
                         cfg['search_fields'])
    return {'ok': True, 'artifact': 'search_catalog_csv',
            'path': str(cfg['search_catalog_csv_path']),
            'source_mode': source_mode, 'source_count': len(docs),
            'rows_built': len(rows), 'elapsed_seconds': elapsed,
            'check_only': check_only}


def build_all_catalogs(cfg: Dict[str, Any],
                       check_only: bool = False) -> Dict[str, Any]:
    """Build full catalog, search catalog, and location tree in sequence."""
    started = time.time()
    steps = []
    for result in [
        build_full_catalog(cfg, check_only=check_only),
        build_search_catalog(cfg, source='full_catalog', check_only=check_only),
        build_location_tree(cfg, source='search_catalog', check_only=check_only),
    ]:
        steps.append(result)
        if not result.get('ok'):
            return {'ok': False, 'artifact': 'build_all', 'steps': steps,
                    'elapsed_seconds': round(time.time() - started, 3)}
    return {'ok': True, 'artifact': 'build_all', 'steps': steps,
            'elapsed_seconds': round(time.time() - started, 3),
            'check_only': check_only}
