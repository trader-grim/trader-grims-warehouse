"""
tgw.history_index — archive history indexer (PP-HISTORY-001 / GEMINI-007).

Builds supplementary indexes for items in /opt/TGW/data/history/ that are
not yet covered by the main archive-ebay-index.json (which only maps
eBay-ID → SKU for items that reached eBay).

The 54K ItemArchive zips contain:
  ~22K with eBay IDs — already in archive-ebay-index.json
  ~32K with SKU/title/location but no eBay ID — legacy Magento inventory

This module indexes the 32K "no-eBay-ID" zips into a JSONL file so their
location + title info is recoverable without scanning 163GB of zips.

Output schema (one JSON-Lines record per zip):
  {sku, title, location, status, price, condition, zip_stem}

Usage:
    from tgw.history_index import index_archive_unindexed, index_loose_csvs

    stats = index_archive_unindexed(cfg, dry_run=False)
    print(stats)
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

_ARCHIVE_FIELDS = ('sku', 'title', '#STATUS', '#LOCATION', 'price', 'condition')
_CSV_HISTORY_INDEX_NAME = 'history-loose-csv-index.jsonl'
_ARCHIVE_INDEX_NAME = 'history-itemdata-index.jsonl'
_EBAY_ORDERS_COLS = ('Item Number', 'Item Title', 'Custom Label', 'Sold For', 'Sale Date')
_ACTIVE_LISTING_COLS = ('Item number', 'Title', 'Custom label', 'Start price')


def _archive_dir(cfg: Dict[str, Any]) -> Path:
    return cfg['itemdata_root'].parent / 'history' / 'ItemArchive'


def _var_dir(cfg: Dict[str, Any]) -> Path:
    return cfg['itemdata_root'].parent.parent / 'var'


def _load_existing_skus(out_path: Path) -> set:
    """Return set of SKUs already written to the JSONL output file."""
    if not out_path.exists():
        return set()
    skus = set()
    with out_path.open('r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    skus.add(json.loads(line).get('sku', ''))
                except json.JSONDecodeError:
                    pass
    return skus


def _load_ebay_indexed_skus(archive_ebay_index_path: Path) -> set:
    """Return set of SKUs that already have eBay IDs in the main archive index."""
    if not archive_ebay_index_path.exists():
        return set()
    raw = json.loads(archive_ebay_index_path.read_text(encoding='utf-8'))
    return set(raw.values())


def _extract_zip_data(zip_path: Path) -> Optional[Dict[str, Any]]:
    """Extract relevant fields from an ItemArchive zip. Returns None on failure."""
    sku = zip_path.stem
    json_name = f'{sku}.json'
    try:
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            target = json_name if json_name in names else None
            if target is None:
                lower = {n.lower(): n for n in names}
                target = lower.get(json_name.lower())
            if target is None:
                return None
            data = json.loads(zf.read(target).decode('utf-8', errors='replace'))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    record: Dict[str, Any] = {'sku': sku}
    record['title'] = str(data.get('title') or data.get('name') or '').strip()
    record['location'] = str(data.get('#LOCATION') or data.get('location') or '').strip()
    record['status'] = str(data.get('#STATUS') or '').strip()
    record['price'] = data.get('price')
    record['condition'] = str(data.get('condition') or '').strip()
    record['zip_stem'] = sku
    return record


def index_archive_unindexed(
    cfg: Dict[str, Any],
    out_path: Optional[Path] = None,
    archive_ebay_index_path: Optional[Path] = None,
    limit: int = 0,
    dry_run: bool = False,
    progress_every: int = 500,
) -> Dict[str, Any]:
    """Scan ItemArchive zips not in archive-ebay-index.json → append to JSONL.

    Skips:
    - SKUs already in archive-ebay-index.json (have eBay IDs; already indexed)
    - SKUs already in the output JSONL (incremental — safe to re-run)

    Returns summary: {total_zips, already_ebay, already_indexed, new, skipped, dry_run}
    """
    archive_dir = _archive_dir(cfg)
    var_dir = _var_dir(cfg)

    if archive_ebay_index_path is None:
        archive_ebay_index_path = var_dir / 'archive-ebay-index.json'
    if out_path is None:
        out_path = var_dir / _ARCHIVE_INDEX_NAME

    ebay_skus = _load_ebay_indexed_skus(archive_ebay_index_path)
    existing_skus = _load_existing_skus(out_path)

    zips = sorted(archive_dir.glob('*.zip'))
    total = len(zips)

    counts = {'total_zips': total, 'already_ebay': 0, 'already_indexed': 0,
              'new': 0, 'skipped_no_json': 0, 'dry_run': dry_run}
    new_records: List[Dict[str, Any]] = []

    for i, zip_path in enumerate(zips):
        sku = zip_path.stem
        if sku in ebay_skus:
            counts['already_ebay'] += 1
            continue
        if sku in existing_skus:
            counts['already_indexed'] += 1
            continue

        record = _extract_zip_data(zip_path)
        if record is None:
            counts['skipped_no_json'] += 1
            continue

        new_records.append(record)
        counts['new'] += 1

        if progress_every and counts['new'] % progress_every == 0:
            print(f'  … {counts["new"]} new records ({i + 1}/{total} zips)', flush=True)

        if limit and counts['new'] >= limit:
            break

    if not dry_run and new_records:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open('a', encoding='utf-8') as f:
            for rec in new_records:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    counts['out_path'] = str(out_path) if not dry_run else None
    return counts


def index_loose_csvs(
    cfg: Dict[str, Any],
    out_path: Optional[Path] = None,
    history_root: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Parse eBay OrdersReport and active-listing CSVs in history root → JSONL.

    Extracts: {source_file, ebay_id, title, sku, sold_for, sale_date}
    Useful for recovering pre-2-year sold prices for archive tombstones.
    """
    if history_root is None:
        history_root = cfg['itemdata_root'].parent / 'history'
    var_dir = _var_dir(cfg)
    if out_path is None:
        out_path = var_dir / _CSV_HISTORY_INDEX_NAME

    records: List[Dict[str, Any]] = []
    files_scanned = 0
    files_skipped = 0

    candidate_csvs = list(history_root.glob('eBay-OrdersReport-*.csv')) + \
                     list(history_root.glob('Transaction*.csv'))

    for csv_path in sorted(candidate_csvs):
        try:
            rows = _parse_ebay_orders_csv(csv_path)
            if rows:
                files_scanned += 1
                records.extend(rows)
            else:
                files_skipped += 1
        except Exception:
            files_skipped += 1

    counts = {
        'files_scanned': files_scanned,
        'files_skipped': files_skipped,
        'records': len(records),
        'dry_run': dry_run,
    }

    if not dry_run and records:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open('w', encoding='utf-8') as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')
        counts['out_path'] = str(out_path)

    return counts


def _parse_ebay_orders_csv(csv_path: Path) -> List[Dict[str, Any]]:
    """Parse an eBay OrdersReport CSV → list of normalized records."""
    with csv_path.open('r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            ebay_id = str(row.get('Item Number') or '').strip()
            if not ebay_id or not ebay_id.isdigit():
                continue
            rows.append({
                'source_file': csv_path.name,
                'ebay_id': ebay_id,
                'title': str(row.get('Item Title') or '').strip(),
                'sku': str(row.get('Custom Label') or '').strip(),
                'sold_for': str(row.get('Sold For') or '').strip(),
                'sale_date': str(row.get('Sale Date') or '').strip(),
            })
    return rows
