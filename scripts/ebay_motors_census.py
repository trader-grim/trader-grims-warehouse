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
    """Yield (sku, marketplace_id, offer_id) for every offer captured across
    all incoming/ebay/*.jsonl.gz files."""
    for gz_path in sorted(capture_root.glob('*.jsonl.gz')):
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
                    yield sku, mkt, offer.get('offerId')


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
    record_count = 0

    for sku, mkt, offer_id in _iter_offer_records(capture_root):
        record_count += 1
        sku_marketplaces[sku].add(mkt)
        if offer_id:
            sku_offer_ids[sku].add(offer_id)
        marketplace_counts[mkt] += 1

    print(f'{record_count} offer records scanned; {len(sku_marketplaces)} unique SKUs.',
         flush=True)

    motors_skus = sorted(sku for sku, mkts in sku_marketplaces.items() if 'EBAY_MOTORS' in mkts)
    multi_marketplace = sorted(
        (sku, sorted(mkts)) for sku, mkts in sku_marketplaces.items() if len(mkts) > 1
    )

    lines: List[str] = []
    lines.append('# eBay marketplace census — 2026-07-04 (todo #1131, PP-EBAY-MOTORS-001 input)')
    lines.append('')
    lines.append('Source: every offer record captured in `incoming/ebay/*.jsonl.gz` '
                 '(R1.8 snapshot #1122 + any other captured activity). Zero eBay API '
                 'calls made by this script — parsed entirely from the existing raw capture.')
    lines.append('')
    lines.append(f'- Offer records scanned: **{record_count}**')
    lines.append(f'- Unique SKUs with a marketplaceId: **{len(sku_marketplaces)}**')
    lines.append(f'- EBAY_MOTORS SKUs found: **{len(motors_skus)}**')
    lines.append(f'- Cross-marketplace SKUs (duplicate-listing risk): **{len(multi_marketplace)}**')
    lines.append('')
    lines.append('## Counts per marketplace')
    lines.append('')
    lines.append('| marketplaceId | offer records |')
    lines.append('|---|---:|')
    for mkt, n in marketplace_counts.most_common():
        lines.append(f'| {mkt} | {n} |')
    lines.append('')
    lines.append('## Full EBAY_MOTORS SKU list')
    lines.append('')
    if motors_skus:
        for sku in motors_skus:
            offer_ids = ', '.join(sorted(sku_offer_ids.get(sku, [])))
            lines.append(f'- `{sku}` (offer_id: {offer_ids or "none captured"})')
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
        print(f'\n[DRY-RUN] {len(motors_skus)} Motors SKU(s) would be patched with '
             f'marketplace_id="EBAY_MOTORS". Pass --apply to write.')
        return 0

    cfg = load_config(Path(DEFAULT_CONFIG))
    patched = 0
    skipped = 0
    for sku in motors_skus:
        path = cfg['itemdata_root'] / sku / f'{sku}.json'
        if not path.exists():
            skipped += 1
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
         f'{skipped} not found in ItemData (legacy/non-inventory items).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
