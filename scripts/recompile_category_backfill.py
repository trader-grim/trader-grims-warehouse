#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""recompile_category_backfill.py — repeatable own-dataset category recovery
(todo #1135).

Dave, 2026-07-04: "My dataset was built using a dump it all into a flat
structure just to capture the data format. Worked ok, but we have better
tools and more data and we can recompile a better dataset. Build it like
we are going to go back in with a stronger dataset every so often."

This is designed to be RE-RUN, not a one-shot: it's additive-only (never
overwrites an item that already has a real category — see
items.set_fields()'s only_if_absent default), and sources are modular
functions so a future run with a new/better source just adds another
_source_* function to SOURCES below. Re-running after nothing has changed
is a safe no-op.

Writes ebay_category_id/ebay_category_name (NOT draft_listing.category_id
— that field represents an item's actual current eBay draft/listing and
should only ever be set by the real drafting pipeline; ebay_category_id
is the "best known category from any source" fallback _category() in
velocity.py already checks second, after draft_listing.category_id).

Sources checked, in order (first match wins per item, reported for
provenance/audit):
  1. historical-tgwcatalog.json  — direct sku lookup (55,347 entries,
     eBay-side fields mixed with Magento export)
  2. historical-master-catalog.json — sku_old lookup (55,347 entries,
     same mixed-source shape, different key)
  3. searchcatalog.csv — a genuinely distinct, eBay-only per-item export
     (ebaycat column); its literal 'uncategorized' placeholder (34,478 of
     55,347 rows) is treated as "no data", not a real category

Investigated 2026-07-04: of 26,709 items with no real category_id today,
5,367 (20%) recoverable across these three sources combined — the bulk
flat-file mining is now close to its ceiling; the rest needs either a
live eBay Taxonomy lookup, the PP-PRICING-001 Phase 0 comping interface,
or waiting until an item is touched again.

Default is dry-run (prints a report); pass --apply to write.

Usage:
    python scripts/recompile_category_backfill.py [--apply] [--limit N]
        [--report PATH]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw import items  # noqa: E402
from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402
from tgw.velocity import _category  # noqa: E402


# ---------------------------------------------------------------------------
# Sources — each returns {sku_or_sku_old_lower: (category_id, category_name)}
# ---------------------------------------------------------------------------

def _source_historical_tgwcatalog(catalog_root: Path) -> Dict[str, Tuple[str, str]]:
    path = catalog_root / 'historical-tgwcatalog.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    out: Dict[str, Tuple[str, str]] = {}
    for sku, r in data.items():
        cat_id = str(r.get('eBay category 1 number') or '').strip()
        if cat_id:
            out[sku.strip().lower()] = (cat_id, str(r.get('eBay category 1 name') or '').strip())
    return out


def _source_historical_master_by_sku_old(catalog_root: Path) -> Dict[str, Tuple[str, str]]:
    path = catalog_root / 'historical-master-catalog.json'
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    out: Dict[str, Tuple[str, str]] = {}
    for r in data:
        sku_old = (r.get('sku_old') or '').strip().lower()
        cat_id = str(r.get('eBay category 1 number') or '').strip()
        if sku_old and cat_id:
            out[sku_old] = (cat_id, str(r.get('eBay category 1 name') or '').strip())
    return out


def _source_searchcatalog_csv(catalog_root: Path) -> Dict[str, Tuple[str, str]]:
    path = catalog_root / 'searchcatalog.csv'
    if not path.exists():
        return {}
    out: Dict[str, Tuple[str, str]] = {}
    with path.open(newline='', encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            sku = (row.get('sku') or '').strip().lower()
            cat = (row.get('ebaycat') or '').strip()
            if sku and cat and cat.lower() != 'uncategorized':
                out[sku] = (cat, '')
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                       help='Actually write (default: dry-run/report only)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Cap the number of items processed this run (0 = no cap)')
    parser.add_argument('--report', type=Path,
                       default=Path('/opt/TGW/var/log/category-recompile-report.json'))
    args = parser.parse_args()

    cfg = load_config(DEFAULT_CONFIG)
    catalog_root = Path(cfg['catalog_root'])
    itemdata_root = Path(cfg['itemdata_root'])

    print('Loading sources...')
    by_sku = _source_historical_tgwcatalog(catalog_root)
    by_sku_old = _source_historical_master_by_sku_old(catalog_root)
    by_sc = _source_searchcatalog_csv(catalog_root)
    print(f'  historical-tgwcatalog.json: {len(by_sku)} usable rows')
    print(f'  historical-master-catalog.json (sku_old): {len(by_sku_old)} usable rows')
    print(f'  searchcatalog.csv: {len(by_sc)} usable rows')

    dirs = sorted(p.name for p in itemdata_root.iterdir() if p.is_dir())
    if args.limit:
        dirs = dirs[:args.limit]

    recovered: Dict[str, Dict[str, Any]] = {}
    still_missing = 0
    already_had_category = 0
    scanned = 0

    for sku in dirs:
        json_path = itemdata_root / sku / f'{sku}.json'
        if not json_path.exists():
            continue
        scanned += 1
        try:
            doc = json.loads(json_path.read_text(encoding='utf-8'))
        except Exception:
            continue

        cat_id, _ = _category(doc)
        if cat_id:
            already_had_category += 1
            continue

        sku_old = (doc.get('sku_old') or '').strip().lower()
        sku_l = sku.lower()

        hit = by_sku.get(sku_l) or by_sku_old.get(sku_old) or by_sc.get(sku_l)
        source = ('historical-tgwcatalog' if sku_l in by_sku else
                  'historical-master-sku_old' if sku_old in by_sku_old else
                  'searchcatalog-csv' if sku_l in by_sc else None)

        if not hit:
            still_missing += 1
            continue

        new_cat_id, new_cat_name = hit
        recovered[sku] = {'ebay_category_id': new_cat_id,
                          'ebay_category_name': new_cat_name,
                          'source': source}

    print(f'\nScanned {scanned} items.')
    print(f'  already had a real category: {already_had_category}')
    print(f'  recoverable this run: {len(recovered)}')
    print(f'  still unrecoverable: {still_missing}')

    if not args.apply:
        print('\n[DRY-RUN] pass --apply to write. Sample:')
        for sku, info in list(recovered.items())[:5]:
            print(f'  {sku}: category_id={info["ebay_category_id"]!r} '
                 f'({info["source"]})')
        args.report.write_text(json.dumps(
            {'mode': 'DRY-RUN', 'scanned': scanned,
             'already_had_category': already_had_category,
             'recoverable': len(recovered), 'still_missing': still_missing,
             'items': recovered}, indent=2))
        print(f'Report written to {args.report}')
        return 0

    applied = 0
    errors = []
    for sku, info in recovered.items():
        res = items.set_fields(cfg, sku, {
            'ebay_category_id': info['ebay_category_id'],
            'ebay_category_name': info['ebay_category_name'],
        })
        if res.get('ok') and res.get('set'):
            applied += 1
        elif not res.get('ok'):
            errors.append({'sku': sku, 'error': res.get('error')})

    print(f'\n[APPLIED] {applied}/{len(recovered)} items updated, {len(errors)} errors')
    args.report.write_text(json.dumps(
        {'mode': 'APPLIED', 'scanned': scanned,
         'already_had_category': already_had_category,
         'recoverable': len(recovered), 'applied': applied,
         'still_missing': still_missing, 'errors': errors,
         'items': recovered}, indent=2))
    print(f'Report written to {args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
