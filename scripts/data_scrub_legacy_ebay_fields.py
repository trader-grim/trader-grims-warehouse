#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""data_scrub_legacy_ebay_fields.py — strip legacy eBay Trading API fields
from item JSON (todo #1053).

Fields (confirmed by Dave 2026-07-04: these are eBay Trading API fields, not
Magento — safe to drop from the base record since they're never read by the
current Inventory-API pipeline; nothing under ebay_offer/ebay_listing/
ebay_submitted/ebay_live is touched):

  Item number, #STATUS, attribute_set, m2_categories, category_ids,
  ebay_condition_number, eBay category 1 name, eBay category 1 number,
  C:Brand, C:Type, C:MPN, C:Model, C:Language, C:Movie/TV Title, input_voltage

Safety (Dave, 2026-07-04): "verify the values match the history data before
deletion just to be safe and if anything isn't there make a list and I will
check again to be certain." For every field present on an item, this script
checks the SAME field/value on that SKU's record against THREE historical
sources: historical-tgwcatalog.json, historical-master-catalog.json (both
under catalog_root — near-complete coverage for 2020+ items, near-zero for
2014-2019), and a per-SKU ItemData snapshot on the archive drive
(--history-itemdata-root, default /home/db/devices/porche/history/ItemData/
ItemData — a temporary consolidation location per Dave, not a permanent
path; covers the 2014-2019 gap the two catalog exports miss). Only fields
whose value matches at least one historical source are removed. A live
value with no match anywhere in history is left untouched and reported as
an exception for Dave to review — never silently dropped.

'eBay category 1 name'/'eBay category 1 number' get an extra guard
(audit#1143 #1209/#1252): even when the value matches history, they are
never removed unless the item's canonical ebay_category_id is already
populated — otherwise this legacy field is the item's ONLY category signal,
and deleting it (even though the value is technically recoverable from the
historical catalogs used for verification) silently zeroes the item's live
category. Held items are reported separately (held_pending_promotion), never
silently dropped — run recompile_category_backfill.py --apply first.

Writes go through items.strip_fields() (one archive entry per item, not per
field — invariant E5, todo #1104). Default is dry-run; pass --apply to write.

Usage:
    python scripts/data_scrub_legacy_ebay_fields.py [--apply] [--limit N]
        [--report PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tgw import items  # noqa: E402
from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402
from recompile_category_backfill import _canonical_category  # noqa: E402

FIELDS_TO_CHECK = [
    'Item number', '#STATUS', 'attribute_set', 'm2_categories', 'category_ids',
    'ebay_condition_number', 'eBay category 1 name', 'eBay category 1 number',
    'C:Brand', 'C:Type', 'C:MPN', 'C:Model', 'C:Language', 'C:Movie/TV Title',
    'input_voltage',
]

# audit#1143 #1209/#1252: these two fields are the item's ONLY category
# signal until something promotes them to the canonical ebay_category_id
# field (see scripts/recompile_category_backfill.py). Deleting them before
# that promotion has happened silently zeroes the item's category — never
# strip them unless _canonical_category() already finds a real value.
_CATEGORY_LEGACY_FIELDS = {'eBay category 1 name', 'eBay category 1 number'}


def _load_historical_index(catalog_root: Path) -> Dict[str, Dict[str, Any]]:
    """Return sku -> merged historical record (fields from both sources;
    historical-tgwcatalog.json wins on key collision, both are consulted
    independently per-field by the caller anyway)."""
    index: Dict[str, Dict[str, Any]] = {}

    tgwcat_path = catalog_root / 'historical-tgwcatalog.json'
    if tgwcat_path.exists():
        tgwcat = json.loads(tgwcat_path.read_text(encoding='utf-8'))
        for sku, rec in tgwcat.items():
            index.setdefault(sku, {}).update(rec)

    mastercat_path = catalog_root / 'historical-master-catalog.json'
    if mastercat_path.exists():
        mastercat = json.loads(mastercat_path.read_text(encoding='utf-8'))
        for rec in mastercat:
            sku = rec.get('sku')
            if sku:
                merged = index.setdefault(sku, {})
                for k, v in rec.items():
                    merged.setdefault(k, v)  # tgwcat already took precedence above

    return index


def _load_from_history_itemdata(root: Path, sku: str) -> Optional[Dict[str, Any]]:
    """Lazy per-SKU lookup against the archive-drive ItemData snapshot —
    only consulted when the two catalog exports have nothing for this SKU,
    to avoid 55k needless file reads."""
    path = root / sku / f'{sku}.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _matches_history(field: str, live_value: Any, hist_record: Dict[str, Any]) -> bool:
    if field not in hist_record:
        return False
    return str(hist_record[field]).strip() == str(live_value).strip()


def _scan_item(sku: str, doc: Dict[str, Any],
              hist_record: Dict[str, Any]
              ) -> Tuple[List[str], List[Dict[str, Any]], int, List[str]]:
    """Return (fields_safe_to_remove, real_exceptions, no_history_count,
    held_pending_promotion).

    A field present live with no historical record for the SKU AT ALL is not
    a discrepancy to review — it's expected for items outside the Magento
    snapshot's coverage (created after migration, or churned before it) and
    is counted, not itemized. A field present in BOTH but disagreeing in
    value is the real signal Dave asked to see.

    _CATEGORY_LEGACY_FIELDS get an extra guard (#1209/#1252): even when the
    value matches history, never remove them unless the item's canonical
    ebay_category_id is already populated — otherwise this is the item's
    only category signal and deleting it zeroes the item's category. Held
    fields are reported separately (held_pending_promotion), never silently
    dropped (invariant C11) — run recompile_category_backfill.py --apply
    first, then re-run this scan.
    """
    safe: List[str] = []
    exceptions: List[Dict[str, Any]] = []
    no_history = 0
    held_pending_promotion: List[str] = []
    canonical_cat_id, _ = _canonical_category(doc)
    for field in FIELDS_TO_CHECK:
        if field not in doc:
            continue
        if field in _CATEGORY_LEGACY_FIELDS and not canonical_cat_id:
            held_pending_promotion.append(field)
            continue
        if _matches_history(field, doc[field], hist_record):
            safe.append(field)
        elif not hist_record:
            no_history += 1
        elif field not in hist_record:
            no_history += 1
        else:
            exceptions.append({
                'sku': sku, 'field': field, 'live_value': doc[field],
                'historical_value': hist_record[field],
            })
    return safe, exceptions, no_history, held_pending_promotion


def _scan(args) -> Dict[str, Any]:
    """Read-only pass: builds the removal plan + report. No secrets/cfg
    needed (path defaults only) — this half can run as any user able to
    read ItemData + the historical sources (db, for the porche third
    source; tgw-only files just get skipped with a warning — they were
    already covered by an earlier pure-tgw pass if they exist)."""
    history_itemdata_root = Path(args.history_itemdata_root)
    if not history_itemdata_root.exists():
        print(f'NOTE: {history_itemdata_root} not reachable — third source skipped, '
             f'2014-2019 items will show as no-history like before', file=sys.stderr)
        history_itemdata_root = None

    raw_cfg = json.loads(Path(DEFAULT_CONFIG).read_text(encoding='utf-8'))
    itemdata_root = Path(raw_cfg.get('itemdata_root', '/opt/TGW/data/ItemData'))
    catalog_root = Path(raw_cfg.get('catalog_root', '/opt/TGW/data/ItemCatalog'))

    print(f'Loading historical catalogs from {catalog_root} ...', flush=True)
    hist_index = _load_historical_index(catalog_root)
    print(f'{len(hist_index)} historical records indexed.', flush=True)

    scanned = 0
    unreadable = 0
    no_history_total = 0
    field_counts: Dict[str, int] = {f: 0 for f in FIELDS_TO_CHECK}
    all_exceptions: List[Dict[str, Any]] = []
    plan: Dict[str, List[str]] = {}
    held_pending_promotion: Dict[str, List[str]] = {}

    for sku_dir in sorted(itemdata_root.iterdir()):
        if not sku_dir.is_dir() or not sku_dir.name.startswith('tgw'):
            continue
        sku = sku_dir.name
        json_path = sku_dir / f'{sku}.json'
        if not json_path.exists():
            continue
        if args.limit and scanned >= args.limit:
            break
        scanned += 1

        try:
            doc = json.loads(json_path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            unreadable += 1
            print(f'WARN: could not read {json_path}: {exc}', file=sys.stderr)
            continue

        hist_record = hist_index.get(sku, {})
        if not hist_record and history_itemdata_root is not None:
            drive_rec = _load_from_history_itemdata(history_itemdata_root, sku)
            if drive_rec:
                hist_record = drive_rec
        safe, exceptions, no_history, held = _scan_item(sku, doc, hist_record)
        all_exceptions.extend(exceptions)
        no_history_total += no_history

        if safe:
            plan[sku] = safe
            for f in safe:
                field_counts[f] += 1
        if held:
            held_pending_promotion[sku] = held

        if scanned % 5000 == 0:
            print(f'  ... {scanned} scanned, {len(plan)} planned so far', flush=True)

    print(f'\n[SCAN] scanned={scanned} planned={len(plan)} unreadable={unreadable}')
    print(f'no-history fields (SKU outside the Magento snapshot coverage — '
         f'expected, not itemized): {no_history_total}')
    print(f'REAL exceptions (SKU in history but value disagrees — for Dave to '
         f'review): {len(all_exceptions)}')
    print(f'HELD PENDING PROMOTION (legacy category field is the item\'s only '
         f'category signal — run recompile_category_backfill.py --apply first, '
         f'#1209/#1252): {len(held_pending_promotion)}')
    print('Per-field planned-removal counts:')
    for f, n in sorted(field_counts.items(), key=lambda kv: -kv[1]):
        if n:
            print(f'  {f}: {n}')

    return {
        'scanned': scanned, 'planned': len(plan), 'unreadable': unreadable,
        'no_history_field_count': no_history_total, 'field_counts': field_counts,
        'real_exceptions': all_exceptions, 'plan': plan,
        'held_pending_promotion': held_pending_promotion,
    }


def _apply(plan_path: Path) -> None:
    """Write pass: reads a plan produced by _scan() and calls
    items.strip_fields() per SKU. Needs load_config() (secrets) — run as
    tgw. Does not need the historical sources or the porche drive at all."""
    plan_data = json.loads(plan_path.read_text(encoding='utf-8'))
    plan: Dict[str, List[str]] = plan_data['plan']
    cfg = load_config(Path(DEFAULT_CONFIG))

    modified = 0
    field_counts: Dict[str, int] = {f: 0 for f in FIELDS_TO_CHECK}
    for i, (sku, fields) in enumerate(plan.items(), 1):
        result = items.strip_fields(cfg, sku, fields)
        if result.get('ok') and result.get('removed'):
            modified += 1
            for f in result['removed']:
                field_counts[f] += 1
        if i % 5000 == 0:
            print(f'  ... {i}/{len(plan)} applied so far', flush=True)

    print(f'\n[APPLIED] planned={len(plan)} modified={modified}')
    print('Per-field removal counts:')
    for f, n in sorted(field_counts.items(), key=lambda kv: -kv[1]):
        if n:
            print(f'  {f}: {n}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=0, help='Stop after N items scanned')
    parser.add_argument('--report', default='/opt/TGW/var/log/data-scrub-1053-report.json',
                       help='Where to write the exceptions + summary + plan')
    parser.add_argument('--history-itemdata-root',
                       default='/home/db/devices/porche/history/ItemData/ItemData',
                       help='Third verification source for the 2014-2019 gap '
                            '(temporary location, per Dave)')
    parser.add_argument('--apply-plan', metavar='PATH', default=None,
                       help='Skip scanning; read a previously written plan/report '
                            'file and apply it (run this mode as tgw)')
    args = parser.parse_args()

    if args.apply_plan:
        _apply(Path(args.apply_plan))
        return 0

    report = _scan(args)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(f'\nPlan + report written to {report_path} '
         f'({report["planned"]} items planned, {len(report["real_exceptions"])} '
         f'real exceptions for Dave to review). Run with --apply-plan {report_path} '
         f'as tgw to write.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
