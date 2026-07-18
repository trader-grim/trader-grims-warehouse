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
  0. the item's own legacy 'eBay category 1 number' raw field, if present —
     promoted to the canonical field, not skipped as "already has a
     category" (audit#1143 #1209: velocity._category() correctly falls
     back to this field for read paths, but using that same fallback here
     as an "already handled" gate left the value uncopied; a later
     data_scrub_legacy_ebay_fields.py run deletes the raw field once it
     matches history, silently zeroing the item's only category signal)
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
from tgw.logging import announce_script_run, setup_logging  # noqa: E402


def _canonical_category(doc: Dict[str, Any]) -> Tuple[str, str]:
    """Return (category_id, category_name) from canonical sources only —
    draft_listing.category_id or ebay_category_id. Deliberately does NOT
    fall back to the legacy raw 'eBay category 1 number' field the way
    velocity._category() does: that field is a promotion candidate here,
    not a reason to skip the item (audit#1143 #1209 — treating it as
    "already had a category" left it uncopied to the canonical field, and
    a later data_scrub_legacy_ebay_fields.py run deletes the raw field
    once it matches history, silently zeroing the item's only category
    signal)."""
    dl = doc.get('draft_listing') or {}
    if dl.get('category_id'):
        return str(dl['category_id']), str(dl.get('category_name') or '')
    if doc.get('ebay_category_id'):
        return str(doc['ebay_category_id']), str(doc.get('ebay_category_name') or '')
    return '', ''


def _legacy_category(doc: Dict[str, Any]) -> Tuple[str, str]:
    """Return (category_id, category_name) from the legacy raw Trading-API
    field only, or ('', '') if absent."""
    cat_id = str(doc.get('eBay category 1 number') or '').strip()
    cat_name = str(doc.get('eBay category 1 name') or '').strip()
    return cat_id, cat_name


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

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.recompile_category_backfill')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'recompile_category_backfill.py',
        'repeatable, additive-only own-dataset category recovery/backfill (todo #1135)',
        apply=args.apply, limit=args.limit, report=str(args.report),
    )

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
    promoted_from_legacy = 0
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

        cat_id, _ = _canonical_category(doc)
        if cat_id:
            already_had_category += 1
            continue

        # Legacy raw field present but never copied to the canonical field —
        # promote it now, before a future data-scrub pass can delete the
        # only copy (#1209). Uses the normal recovered/apply path below;
        # items.set_fields() is only_if_absent, so this is idempotent.
        legacy_id, legacy_name = _legacy_category(doc)
        if legacy_id:
            recovered[sku] = {'ebay_category_id': legacy_id,
                              'ebay_category_name': legacy_name,
                              'source': 'legacy-raw-field-promotion'}
            promoted_from_legacy += 1
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
    print(f'  promoted from legacy raw field: {promoted_from_legacy}')
    print(f'  recoverable this run (incl. promotions): {len(recovered)}')
    print(f'  still unrecoverable: {still_missing}')

    if not args.apply:
        print('\n[DRY-RUN] pass --apply to write. Sample:')
        for sku, info in list(recovered.items())[:5]:
            print(f'  {sku}: category_id={info["ebay_category_id"]!r} '
                 f'({info["source"]})')
        args.report.write_text(json.dumps(
            {'mode': 'DRY-RUN', 'scanned': scanned,
             'already_had_category': already_had_category,
             'promoted_from_legacy': promoted_from_legacy,
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
         'promoted_from_legacy': promoted_from_legacy,
         'recoverable': len(recovered), 'applied': applied,
         'still_missing': still_missing, 'errors': errors,
         'items': recovered}, indent=2))
    print(f'Report written to {args.report}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
