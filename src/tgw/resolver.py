"""
tgw.resolver — Resolve any identifier to a canonical set of SKUs.

This is the core of tgw-api's value proposition: give it whatever you have
(a location code from a barcode reader, a UPC, an eBay item number, a date
range, free text) and get back the canonical set of matching SKUs.

Callers — queue workers, CLI commands, HUD clients — never construct paths
or query the catalog directly.  They call resolve() and work with the result.

Fast paths (no JSON loading):
    sku=, skus=            direct set construction
    location=              symlink tree directory listing
    date_from=, date_to=   pure string prefix match on SKU format

Slower paths (require loading item JSON):
    status=, ebay_item_id=, upc=, search=

Selectors are combined with AND when multiple are given.
"""

from __future__ import annotations

import logging
import re  # remove: import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from .config import location_dir, sku_json

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SKU iteration — filesystem only, no JSON
# ---------------------------------------------------------------------------

def iter_all_skus(cfg: Dict[str, Any]) -> Iterator[str]:
    """Yield every SKU that has a valid JSON file.  No JSON loading."""
    root: Path = cfg['itemdata_root']
    if not root.exists():
        return
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / f'{child.name}.json').exists():
            yield child.name


def find_item_jsons(cfg: Dict[str, Any]) -> List[Path]:
    """Return all canonical item JSON paths, sorted by SKU."""
    root: Path = cfg['itemdata_root']
    paths: List[Path] = []
    if not root.exists():
        return paths
    for child in sorted(root.iterdir()):
        if child.is_dir():
            candidate = child / f'{child.name}.json'
            if candidate.exists():
                paths.append(candidate)
    return paths


# ---------------------------------------------------------------------------
# Item loading
# ---------------------------------------------------------------------------

def load_item_doc(json_path: Path) -> Dict[str, Any]:
    """Load one item JSON.  Injects sku from directory name if absent."""
    doc = _load_json_strict(json_path)
    if not isinstance(doc, dict):
        raise ValueError(f'{json_path}: top-level JSON is not an object')
    sku = str(doc.get('sku', '')).strip() or json_path.parent.name
    if 'sku' not in doc or not str(doc.get('sku', '')).strip():
        doc = dict(doc)
        doc['sku'] = sku
    return doc


# sku_old → current sku index, built lazily on first miss, cached per process.
_sku_old_index: Optional[Dict[str, str]] = None


def _build_sku_old_index(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Scan all item JSONs once and build {sku_old: current_sku} mapping."""
    import json as _json
    index: Dict[str, str] = {}
    for path in find_item_jsons(cfg):
        try:
            raw = path.read_text(encoding='utf-8', errors='replace')
            # Fast scan: skip full parse if sku_old not in file at all
            if 'sku_old' not in raw:
                continue
            doc = _json.loads(raw)
            if isinstance(doc, dict):
                old_sku = str(doc.get('sku_old', '')).strip()
                if old_sku:
                    index[old_sku] = path.parent.name
        except Exception:
            pass
    return index


def find_current_sku(cfg: Dict[str, Any], old_sku: str) -> Optional[str]:
    """Return the current SKU whose sku_old field matches old_sku, or None.

    Builds a process-level cache on first call (scans only items containing
    'sku_old' in their JSON text, so the scan is fast on modern items).
    """
    global _sku_old_index
    if _sku_old_index is None:
        _sku_old_index = _build_sku_old_index(cfg)
    return _sku_old_index.get(old_sku)


def load_item_doc_by_sku(cfg: Dict[str, Any], sku: str) -> Dict[str, Any]:
    """Load the canonical JSON for a SKU.

    Falls back to sku_old lookup if the SKU directory is not found, so
    callers transparently handle old-format / pre-migration SKUs.
    """
    path = sku_json(cfg, sku)
    if not path.exists():
        current = find_current_sku(cfg, sku)
        if current:
            path = sku_json(cfg, current)
        else:
            raise FileNotFoundError(f'no item JSON for sku {sku!r}: {path}')
    return load_item_doc(path)


def _load_json_strict(path: Path) -> Any:
    import json
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
# SKU date parsing — free because it's just a string match
# ---------------------------------------------------------------------------

_SKU_DATE_RE = re.compile(r'^tgw(\d{4})(\d{2})(\d{2})')


def sku_date_str(sku: str) -> Optional[str]:
    """Extract YYYYMMDD from a TGW SKU timestamp, or None."""
    m = _SKU_DATE_RE.match(sku)
    return f'{m.group(1)}{m.group(2)}{m.group(3)}' if m else None


# ---------------------------------------------------------------------------
# Location fast path
# ---------------------------------------------------------------------------

def _location_skus_from_tree(cfg: Dict[str, Any], location: str) -> Set[str]:
    """Fast: read symlink tree directory listing, no JSON."""
    loc = location_dir(cfg, location)
    if not loc.exists():
        return set()
    return {p.name for p in loc.iterdir() if p.is_symlink() or p.is_dir()}


def _location_skus_from_itemdata(cfg: Dict[str, Any], location: str) -> Set[str]:
    """Fallback: scan itemdata JSON when tree is missing."""
    result: Set[str] = set()
    for path in find_item_jsons(cfg):
        try:
            doc = load_item_doc(path)
            if str(doc.get('location', '')).strip() == location:
                result.add(str(doc.get('sku', path.parent.name)))
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# resolve() — the selector engine
# ---------------------------------------------------------------------------

def resolve(cfg: Dict[str, Any], **selectors: Any) -> Set[str]:
    """
    Return the set of SKUs matching all given selectors (AND logic).

    Keyword arguments (all optional):
        sku          str        exact SKU
        skus         list[str]  explicit set
        location     str        storage location code
        status       str        #STATUS or status field value
        date_from    str        YYYYMMDD lower bound (inclusive)
        date_to      str        YYYYMMDD upper bound (inclusive)
        ebay_item_id str        eBay item number
        upc          str        UPC / barcode value
        search       str        free-text substring (all fields)
        empty_field  str        field name — match items where it is missing/null/empty-string

    Returns an empty set if no items match, never raises on missing data.
    """
    candidates: Optional[Set[str]] = None

    def narrow(s: Set[str]) -> None:
        nonlocal candidates
        candidates = s if candidates is None else candidates & s

    # --- fast paths (no JSON) ---

    if 'sku' in selectors:
        q = str(selectors['sku']).strip()
        # Fast exact match first; if the SKU looks like a TGW prefix that is
        # shorter than the stored format, fall back to first-18-char prefix
        # match so 18-char old-format queries find 20-char new-format items.
        root: Path = cfg['itemdata_root']
        if (root / q).is_dir():
            narrow({q})
        else:
            if len(q) <= 18 and q.lower().startswith('tgw') and len(q) >= 14:
                narrow({s for s in iter_all_skus(cfg) if s[:len(q)] == q})
            else:
                narrow({q})

    if 'skus' in selectors:
        narrow({str(s) for s in selectors['skus']})

    if 'location' in selectors:
        loc = str(selectors['location']).strip()
        if cfg['location_tree_root'].exists():
            narrow(_location_skus_from_tree(cfg, loc))
        else:
            narrow(_location_skus_from_itemdata(cfg, loc))

    date_from = selectors.get('date_from')
    date_to   = selectors.get('date_to')
    if date_from or date_to:
        pool = set(iter_all_skus(cfg)) if candidates is None else set(candidates)
        matched: Set[str] = set()
        for sku in pool:
            d = sku_date_str(sku)
            if d is None:
                continue
            if date_from and d < str(date_from):
                continue
            if date_to and d > str(date_to):
                continue
            matched.add(sku)
        narrow(matched)

    # --- slower paths (JSON loading) ---

    needs_json = {'status', 'ebay_item_id', 'upc', 'search', 'empty_field'}
    if needs_json & set(selectors):
        status       = str(selectors['status']).strip()       if 'status'       in selectors else ''
        ebay_item_id = str(selectors['ebay_item_id']).strip() if 'ebay_item_id' in selectors else ''
        upc          = str(selectors['upc']).strip()          if 'upc'          in selectors else ''
        search       = str(selectors['search']).lower()       if 'search'       in selectors else ''
        empty_field  = str(selectors['empty_field']).strip()  if 'empty_field'  in selectors else ''

        pool = set(iter_all_skus(cfg)) if candidates is None else set(candidates)
        matched = set()
        for sku in pool:
            try:
                doc = load_item_doc_by_sku(cfg, sku)
            except Exception as exc:
                log.warning(
                    'resolve(): skipping sku %s — failed to load item JSON: %s',
                    sku, exc,
                )
                continue
            if status:
                item_status = str(doc.get('#STATUS', doc.get('status', ''))).strip()
                if item_status != status:
                    continue
            if ebay_item_id:
                if str(doc.get('Item number', doc.get('ebay_item_id', ''))).strip() != ebay_item_id:
                    continue
            if upc:
                if str(doc.get('upc', doc.get('UPC', ''))).strip() != upc:
                    continue
            if search:
                haystack = '\n'.join(
                    f'{k}={v}' for k, v in doc.items()
                    if isinstance(v, (str, int, float, bool)) or v is None
                ).lower()
                if search not in haystack:
                    continue
            if empty_field:
                val = doc.get(empty_field)
                if not (val is None or (isinstance(val, str) and not val.strip())):
                    continue
            matched.add(sku)
        narrow(matched)

    # No selectors at all → everything
    if candidates is None:
        candidates = set(iter_all_skus(cfg))

    return candidates
