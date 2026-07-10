#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""ebay_motors_census.py — Motors census from the R1.8 capture (todo #1131).

Zero API calls: parses every offer record already captured in
`incoming/ebay/*.jsonl.gz` (E7 raw capture — the R1.8 snapshot, #1122, plus
whatever else landed there) for `marketplaceId` per SKU, and:

  1. Writes reference/ebay-marketplace-census-2026-07-04.md — counts per
     marketplace, the full EBAY_MOTORS SKU list, and cross-marketplace
     multi-offer SKUs (same SKU seen under more than one marketplaceId across
     captured records — a duplicate-listing risk, not necessarily a bug: could
     be a genuine relist under a different marketplace, or a stale/duplicate
     offer that never got cleaned up).
  2. Patches each Motors SKU's item JSON with `marketplace_id` via the fence
     (items.update_item) — dataset growth per Prime Directive 1: this field
     didn't exist anywhere in ItemData before.

This is the 2pm 2026-07-04 planning input for PP-EBAY-MOTORS-001. Default is
dry-run (report only); pass --apply to patch item JSONs.

audit#1143 #1214: --apply used to (a) decide "is this a Motors SKU" from
whether EBAY_MOTORS EVER appeared for that SKU across ALL captured files,
baking in arbitrarily stale data from old captures with no recency check,
and (b) unconditionally patch every such SKU, including ones the census's
own "Cross-marketplace multi-offer SKUs" section flags as ambiguous and
needing human review — silently auto-resolving the exact ambiguity it warns
about. Fixed: a SKU's Motors status is now decided by its MOST RECENTLY
captured marketplaceId only (files are named YYYY-MM-DD.jsonl.gz — recency
is derivable with zero extra API calls), and any SKU that also appears
under a different marketplaceId in ANY capture is excluded from --apply
entirely (reported separately, never silently patched).
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw import items  # noqa: E402
from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402


def _iter_offer_records(capture_root: Path):
    """Yield (sku, marketplace_id, offer_id, capture_date) for every offer
    captured across all incoming/ebay/*.jsonl.gz files, in chronological
    order. capture_date is the file's date stem (files are named
    YYYY-MM-DD.jsonl.gz) — the only recency signal available without a live
    eBay call, and enough to prefer a SKU's most-recently-captured
    marketplaceId over an arbitrarily old one (audit#1143 #1214)."""
    for gz_path in sorted(capture_root.glob('*.jsonl.gz')):
        capture_date = Path(gz_path.stem).stem  # '2026-07-04.jsonl.gz' -> '2026-07-04'
        try:
            with gzip.open(gz_path, 'rb') as fh:
                data = fh.read()
        except OSError as exc:
            print(f'WARN: could not read {gz_path}: {exc}', file=sys.stderr)
            continue
        for line in data.decode('utf-8', errors='replace').splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get('name') != 'GET /sell/inventory/v1/offer':
                continue
            try:
                body = json.loads(rec['body'])
            except (KeyError, ValueError):
                continue
            for offer in body.get('offers', []):
                sku = offer.get('sku')
                mkt = offer.get('marketplaceId')
                if sku and mkt:
                    yield sku, mkt, offer.get('offerId'), capture_date


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                       help='Patch marketplace_id onto Motors SKUs (default: dry-run/report only)')
    parser.add_argument('--capture-root', default='/opt/TGW/incoming/ebay',
                       help='Directory of daily *.jsonl.gz capture files')
    parser.add_argument('--out', default=None,
                       help='Census markdown output path (default: '
                            'reference/ebay-marketplace-census-2026-07-04.md)')
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    out_path = Path(args.out) if args.out else (
        repo_root / 'docs' / 'TGW-Plan-Vault' / 'reference'
        / 'ebay-marketplace-census-2026-07-04.md')

    capture_root = Path(args.capture_root)
    print(f'Scanning offer records in {capture_root} ...', flush=True)

    sku_marketplaces: Dict[str, Set[str]] = defaultdict(set)
    sku_offer_ids: Dict[str, Set[str]] = defaultdict(set)
    marketplace_counts: Counter = Counter()
    sku_latest: Dict[str, tuple] = {}  # sku -> (capture_date, marketplace_id) of most recent record
    record_count = 0

    for sku, mkt, offer_id, capture_date in _iter_offer_records(capture_root):
        record_count += 1
        sku_marketplaces[sku].add(mkt)
        if offer_id:
            sku_offer_ids[sku].add(offer_id)
        marketplace_counts[mkt] += 1
        prev = sku_latest.get(sku)
        if prev is None or capture_date >= prev[0]:
            sku_latest[sku] = (capture_date, mkt)

    print(f'{record_count} offer records scanned; {len(sku_marketplaces)} unique SKUs.',
         flush=True)

    # Motors SKUs are decided by the MOST RECENTLY captured marketplaceId per
    # SKU, not "ever seen as EBAY_MOTORS in any historical file" — a SKU
    # relisted to a different marketplace since an old capture must not be
    # patched back to EBAY_MOTORS from stale data (audit#1143 #1214).
    motors_skus = sorted(sku for sku, (_, mkt) in sku_latest.items() if mkt == 'EBAY_MOTORS')
    multi_marketplace = sorted(
        (sku, sorted(mkts)) for sku, mkts in sku_marketplaces.items() if len(mkts) > 1
    )
    # Cross-marketplace ambiguity is the exact case the census tells the
    # operator "needs human review, not auto-resolution" — --apply must
    # never silently resolve it, even when the LATEST record says EBAY_MOTORS.
    ambiguous_motors = sorted(set(motors_skus) & {sku for sku, _ in multi_marketplace})
    safe_motors = sorted(set(motors_skus) - set(ambiguous_motors))

    lines: List[str] = []
    lines.append('# eBay marketplace census — 2026-07-04 (todo #1131, PP-EBAY-MOTORS-001 input)')
    lines.append('')
    lines.append('Source: every offer record captured in `incoming/ebay/*.jsonl.gz` '
                 '(R1.8 snapshot #1122 + any other captured activity). Zero eBay API '
                 'calls made by this script — parsed entirely from the existing raw capture.')
    lines.append('')
    lines.append(f'- Offer records scanned: **{record_count}**')
    lines.append(f'- Unique SKUs with a marketplaceId: **{len(sku_marketplaces)}**')
    lines.append(f'- EBAY_MOTORS SKUs found (most recent capture): **{len(motors_skus)}**')
    lines.append(f'  - Safe to auto-patch: **{len(safe_motors)}**')
    lines.append(f'  - Ambiguous, excluded from --apply (see below): **{len(ambiguous_motors)}**')
    lines.append(f'- Cross-marketplace SKUs (duplicate-listing risk): **{len(multi_marketplace)}**')
    lines.append('')
    lines.append('## Counts per marketplace')
    lines.append('')
    lines.append('| marketplaceId | offer records |')
    lines.append('|---|---:|')
    for mkt, n in marketplace_counts.most_common():
        lines.append(f'| {mkt} | {n} |')
    lines.append('')
    lines.append('## Full EBAY_MOTORS SKU list (most recently captured marketplaceId)')
    lines.append('')
    ambiguous_set = set(ambiguous_motors)
    if motors_skus:
        for sku in motors_skus:
            offer_ids = ', '.join(sorted(sku_offer_ids.get(sku, [])))
            flag = (' — **AMBIGUOUS, NOT auto-patched** (also seen under another '
                   'marketplaceId — see Cross-marketplace section below)'
                   if sku in ambiguous_set else '')
            lines.append(f'- `{sku}` (offer_id: {offer_ids or "none captured"}){flag}')
    else:
        lines.append('None found.')
    lines.append('')
    lines.append('## Cross-marketplace multi-offer SKUs (duplicate-listing risk)')
    lines.append('')
    lines.append('Same SKU seen under more than one `marketplaceId` across captured '
                 'offer records — could be a genuine relist under a different '
                 'marketplace, or a stale/duplicate offer never cleaned up. Needs '
                 'human review, not auto-resolution.')
    lines.append('')
    if multi_marketplace:
        for sku, mkts in multi_marketplace:
            lines.append(f'- `{sku}`: {", ".join(mkts)}')
    else:
        lines.append('None found.')
    lines.append('')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text('\n'.join(lines), encoding='utf-8')
    print(f'Census written to {out_path}', flush=True)

    if not args.apply:
        print(f'\n[DRY-RUN] {len(safe_motors)} Motors SKU(s) would be patched with '
             f'marketplace_id="EBAY_MOTORS"; {len(ambiguous_motors)} ambiguous '
             f'cross-marketplace SKU(s) excluded (see report — needs manual review). '
             f'Pass --apply to write.')
        return 0

    cfg = load_config(Path(DEFAULT_CONFIG))
    patched = 0
    skipped_not_found = 0
    for sku in safe_motors:
        path = cfg['itemdata_root'] / sku / f'{sku}.json'
        if not path.exists():
            skipped_not_found += 1
            continue
        doc = json.loads(path.read_text(encoding='utf-8'))
        if doc.get('marketplace_id') == 'EBAY_MOTORS':
            continue  # idempotent
        result = items.update_item(cfg, sku, 'marketplace_id', 'EBAY_MOTORS')
        if result.get('ok'):
            patched += 1
        else:
            print(f'WARN: patch failed for {sku}: {result.get("error")}', file=sys.stderr)

    print(f'\n[APPLIED] {patched} Motors SKU(s) patched with marketplace_id; '
         f'{skipped_not_found} not found in ItemData (legacy/non-inventory items); '
         f'{len(ambiguous_motors)} ambiguous cross-marketplace SKU(s) excluded — '
         f'see "Cross-marketplace multi-offer SKUs" in the report and resolve manually.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
