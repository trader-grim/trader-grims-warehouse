"""
tgw.config — Config loading and canonical path resolution.

All code that needs TGW paths imports from here.  Nothing constructs
/opt/TGW paths by hand.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Set


DEFAULT_CONFIG = Path('/opt/TGW/config/tgw-api-config.json')


# ---------------------------------------------------------------------------
# JSON helpers used by config loading
# ---------------------------------------------------------------------------

def load_json_strict(path: Path) -> Any:
    """Load JSON, raising ValueError on duplicate keys."""
    def hook(pairs):
        out: Dict[str, Any] = {}
        seen: Set[str] = set()
        for k, v in pairs:
            if k in seen:
                raise ValueError(f'duplicate key {k!r} in {path}')
            seen.add(k)
            out[k] = v
        return out
    with path.open('r', encoding='utf-8') as f:
        return json.load(f, object_pairs_hook=hook)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> Dict[str, Any]:
    """
    Load and normalise the TGW config file.

    All paths are resolved to absolute Path objects.  Missing keys fall back
    to well-known defaults so callers never need to guard for absence.
    """
    raw: Dict[str, Any] = load_json_strict(path) if path.exists() else {}

    def p(key: str, default: str) -> Path:
        return Path(os.path.expanduser(raw.get(key, default)))

    secrets_root   = p('secrets_root',   '/opt/TGW/secrets')
    itemdata_root  = p('itemdata_root',  '/opt/TGW/data/ItemData')
    catalog_root   = p('catalog_root',   '/opt/TGW/data/ItemCatalog')

    full_catalog_path        = p('full_catalog_path',        str(catalog_root / 'tgwcatalog.json'))
    search_catalog_path      = p('search_catalog_path',      str(catalog_root / 'search-catalog.json'))
    location_tree_root       = p('location_tree_root',       str(catalog_root / 'by-location'))
    full_catalog_csv_path    = p('full_catalog_csv_path',    str(catalog_root / 'tgwcatalog.csv'))
    search_catalog_csv_path  = p('search_catalog_csv_path',  str(catalog_root / 'searchcatalog.csv'))
    sqlite_catalog_path      = p('sqlite_catalog_path',      str(catalog_root / 'tgwcatalog.db'))
    thumbnail_root           = p('thumbnail_root',           str(catalog_root / 'thumbnails'))

    ebay_token_path       = secrets_root / 'ebay-token.json'
    ebay_credentials_path = secrets_root / 'ebay-credentials.json'

    postgres_dsn = raw.get('postgres_dsn', 'dbname=state_machine user=tgw')

    search_fields    = raw.get('search_catalog_fields',
                               ['title', 'location', '#STATUS', 'status'])
    required         = raw.get('search_catalog_required', ['sku'])
    pretty           = bool(raw.get('pretty_json', True))
    skip_missing     = bool(raw.get('skip_missing_files', True))
    thumbnail_size   = raw.get('thumbnail_size', [256, 256])

    return {
        'config_path':             path,
        'secrets_root':            secrets_root,
        'ebay_token_path':         ebay_token_path,
        'ebay_credentials_path':   ebay_credentials_path,
        'postgres_dsn':            postgres_dsn,
        'itemdata_root':           itemdata_root,
        'catalog_root':            catalog_root,
        'full_catalog_path':       full_catalog_path,
        'search_catalog_path':     search_catalog_path,
        'full_catalog_csv_path':   full_catalog_csv_path,
        'search_catalog_csv_path': search_catalog_csv_path,
        'location_tree_root':      location_tree_root,
        'sqlite_catalog_path':     sqlite_catalog_path,
        'thumbnail_root':          thumbnail_root,
        'thumbnail_size':          thumbnail_size,
        'search_fields':           ['sku', *[f for f in search_fields if f != 'sku']],
        'required':                required,
        'pretty':                  pretty,
        'skip_missing':            skip_missing,
        'raw':                     raw,
    }


# ---------------------------------------------------------------------------
# Canonical path helpers — the only place paths are constructed
# ---------------------------------------------------------------------------

def sku_dir(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical directory for a SKU."""
    return cfg['itemdata_root'] / sku


def sku_json(cfg: Dict[str, Any], sku: str) -> Path:
    """Canonical JSON file path for a SKU."""
    return sku_dir(cfg, sku) / f'{sku}.json'


def sku_exists(cfg: Dict[str, Any], sku: str) -> bool:
    """True if the canonical JSON file for this SKU exists."""
    return sku_json(cfg, sku).exists()


def location_dir(cfg: Dict[str, Any], location: str) -> Path:
    """Canonical location directory in the symlink tree."""
    return cfg['location_tree_root'] / location


def queue_dir(cfg: Dict[str, Any], queue_name: str) -> Path:
    """Canonical path for a named queue directory."""
    runtime_root = Path(cfg['raw'].get('runtime_root', '/opt/TGW/runtime'))
    return runtime_root / 'state' / 'queues' / queue_name
