#!/usr/bin/env python3
"""
ebay_audit.py — Cross-reference Inventory API vs Trading API vs local ItemData.

Produces a JSON report at /opt/TGW/var/log/ebay-audit-<timestamp>.json and a
human-readable summary on stdout.

Buckets:
  inventory_only  — in Inventory API, not in Trading API (expected majority)
  trading_only    — in Trading API, not in Inventory API → needs migration
  duplicates      — same SKU active in BOTH APIs → end Trading API listing only
  no_offer        — in Inventory API but no active offer (not live on eBay)
  orphan_trading  — Trading API listing has no custom label / no local item JSON
  orphan_inventory— Inventory API SKU has no local item JSON

Acceptance target: inventory_only + trading_only + duplicates = 19,653 (Seller Hub
count as of 2026-06-28). Any gap is a data integrity issue requiring investigation.

Usage:
  sudo -u tgw python3 scripts/ebay_audit.py
  sudo -u tgw python3 scripts/ebay_audit.py --config /opt/TGW/config/tgw-api-config.json
  sudo -u tgw python3 scripts/ebay_audit.py --no-offers   # skip per-SKU offer fetch (faster)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Bootstrap path so tgw imports work when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.apis.ebay.trading import get_my_ebay_selling
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pull import fetch_offer_for_sku, iter_inventory_api_items
from tgw.logging import announce_script_run

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

SELLER_HUB_LIVE_COUNT = 19653  # as of 2026-06-28


def run_audit(cfg: Dict[str, Any], fetch_offers: bool = True) -> Dict[str, Any]:
    itemdata_root = cfg['itemdata_root']
    audited_at = datetime.now(timezone.utc).isoformat()

    # --- Step 1: collect all Inventory API SKUs ---
    log.info('Fetching Inventory API items...')
    inventory_skus: Dict[str, Dict] = {}  # sku -> raw inventory_item
    for item in iter_inventory_api_items(cfg):
        sku = item.get('sku', '').strip()
        if sku:
            inventory_skus[sku] = item
    log.info('Inventory API: %d SKUs', len(inventory_skus))

    # --- Step 2: collect all Trading API active listings ---
    log.info('Fetching Trading API listings (GetMyeBaySelling, all pages)...')
    trading_listings: List[Dict] = list(get_my_ebay_selling(cfg))
    log.info('Trading API: %d active listings', len(trading_listings))

    # Index trading listings by custom_label (SKU)
    trading_by_sku: Dict[str, List[Dict]] = {}
    orphan_trading: List[Dict] = []
    for listing in trading_listings:
        sku = listing.get('custom_label', '').strip()
        if not sku:
            orphan_trading.append(listing)
            continue
        trading_by_sku.setdefault(sku, []).append(listing)

    # --- Step 3: cross-reference ---
    inventory_sku_set = set(inventory_skus)
    trading_sku_set = set(trading_by_sku)

    inventory_only_skus = inventory_sku_set - trading_sku_set
    trading_only_skus = trading_sku_set - inventory_sku_set
    duplicate_skus = inventory_sku_set & trading_sku_set

    log.info(
        'Cross-reference: inventory_only=%d trading_only=%d duplicates=%d orphan_trading=%d',
        len(inventory_only_skus), len(trading_only_skus),
        len(duplicate_skus), len(orphan_trading),
    )

    # duplicates count as 2 live listings (one per API)
    live_count = len(inventory_only_skus) + len(trading_only_skus) + 2 * len(duplicate_skus)
    gap = SELLER_HUB_LIVE_COUNT - live_count
    log.info(
        'Live listing count: %d (Seller Hub target: %d, gap: %+d)',
        live_count, SELLER_HUB_LIVE_COUNT, -gap,
    )

    # --- Step 4: check local item JSON existence ---
    def has_local_item(sku: str) -> bool:
        return (Path(itemdata_root) / sku / f'{sku}.json').exists()

    orphan_inventory: List[str] = [
        sku for sku in inventory_sku_set if not has_local_item(sku)
    ]
    orphan_trading_no_local: List[str] = [
        sku for sku in trading_only_skus if not has_local_item(sku)
    ]

    # --- Step 5: fetch offers for inventory items (optional, slow) ---
    no_offer: List[str] = []
    offer_data: Dict[str, Optional[Dict]] = {}

    if fetch_offers:
        log.info(
            'Fetching offers for %d inventory SKUs (this will take a while)...',
            len(inventory_sku_set),
        )
        total = len(inventory_sku_set)
        for i, sku in enumerate(sorted(inventory_sku_set), 1):
            if i % 500 == 0:
                log.info('  offers: %d/%d', i, total)
            offer = fetch_offer_for_sku(cfg, sku)
            offer_data[sku] = offer
            if offer is None or offer.get('status') not in ('PUBLISHED',):
                no_offer.append(sku)
            time.sleep(0.05)  # ~20 req/s, well under eBay rate limit
    else:
        log.info('Skipping offer fetch (--no-offers)')

    # --- Step 6: build report ---
    def trading_summary(sku: str) -> List[Dict]:
        return [
            {
                'listing_id': listing.get('listing_id'),
                'title': listing.get('title', '')[:80],
                'price': listing.get('live_price'),
                'url': listing.get('listing_url'),
            }
            for listing in trading_by_sku.get(sku, [])
        ]

    def offer_summary(sku: str) -> Optional[Dict]:
        o = offer_data.get(sku)
        if not o:
            return None
        return {
            'offer_id': o.get('offerId'),
            'status': o.get('status'),
            'listing_id': (o.get('listing') or {}).get('listingId'),
            'price': ((o.get('pricingSummary') or {}).get('price') or {}).get('value'),
        }

    report = {
        'audited_at': audited_at,
        'seller_hub_target': SELLER_HUB_LIVE_COUNT,
        'counts': {
            'inventory_api_total': len(inventory_skus),
            'trading_api_total': len(trading_listings),
            'inventory_only': len(inventory_only_skus),
            'trading_only': len(trading_only_skus),
            'duplicates': len(duplicate_skus),
            'no_offer': len(no_offer),
            'orphan_trading_no_label': len(orphan_trading),
            'orphan_inventory_no_local': len(orphan_inventory),
            'orphan_trading_no_local': len(orphan_trading_no_local),
            'live_listing_count': live_count,
            'gap_vs_seller_hub': gap,
        },
        'duplicates': {
            sku: {
                'inventory_item_sku': sku,
                'trading_listings': trading_summary(sku),
                'offer': offer_summary(sku) if fetch_offers else None,
                'has_local_item': has_local_item(sku),
                'action': 'END_TRADING_LISTING',
            }
            for sku in sorted(duplicate_skus)
        },
        'trading_only': {
            sku: {
                'trading_listings': trading_summary(sku),
                'has_local_item': has_local_item(sku),
                'action': 'MIGRATE_TO_INVENTORY' if has_local_item(sku) else 'INVESTIGATE_NO_LOCAL',
            }
            for sku in sorted(trading_only_skus)
        },
        'no_offer': sorted(no_offer),
        'orphan_trading_no_label': [
            {
                'listing_id': listing.get('listing_id'),
                'title': listing.get('title', '')[:80],
                'price': listing.get('live_price'),
            }
            for listing in orphan_trading
        ],
        'orphan_inventory_no_local': sorted(orphan_inventory),
        'orphan_trading_no_local': sorted(orphan_trading_no_local),
    }

    return report


def print_summary(report: Dict[str, Any]) -> None:
    c = report['counts']
    gap = c['gap_vs_seller_hub']
    gap_str = f'+{gap}' if gap > 0 else str(gap)

    print()
    print('=' * 60)
    print('eBay AUDIT REPORT')
    print(f"  audited_at:          {report['audited_at']}")
    print(f"  Seller Hub target:   {report['seller_hub_target']:,}")
    print()
    print(f"  Inventory API items: {c['inventory_api_total']:,}")
    print(f"  Trading API listings:{c['trading_api_total']:,}")
    print()
    print('  Cross-reference buckets:')
    print(f"    inventory_only:    {c['inventory_only']:,}  (in Inventory, not Trading)")
    print(f"    trading_only:      {c['trading_only']:,}  (in Trading, not Inventory → migrate)")
    print(f"    duplicates:        {c['duplicates']:,}  (in BOTH → end Trading listing)")
    print()
    print(f"  Computed live count: {c['live_listing_count']:,}")
    print(f"  Gap vs Seller Hub:   {gap_str}  {'✅ RECONCILED' if gap == 0 else '⚠️  INVESTIGATE'}")
    print()
    if c['no_offer']:
        print(f"  ⚠️  no_offer:         {c['no_offer']:,}  (Inventory item, not published)")
    if c['orphan_trading_no_label']:
        print(f"  ⚠️  orphan (no label):{c['orphan_trading_no_label']:,}  (Trading listing, no SKU)")
    if c['orphan_inventory_no_local']:
        print(f"  ⚠️  orphan (no JSON): {c['orphan_inventory_no_local']:,}  (Inventory SKU, no local item)")
    if c['orphan_trading_no_local']:
        print(f"  ⚠️  trading no local: {c['orphan_trading_no_local']:,}  (Trading-only, no local item)")
    print()

    if report['duplicates']:
        print('  DUPLICATES (end Trading listing):')
        for sku, info in list(report['duplicates'].items())[:10]:
            tl = info['trading_listings']
            lid = tl[0]['listing_id'] if tl else '?'
            price = tl[0]['price'] if tl else '?'
            print(f"    {sku}  trading_listing={lid}  price=${price}")
        if len(report['duplicates']) > 10:
            print(f"    ... and {len(report['duplicates']) - 10} more")
        print()

    if report['trading_only']:
        print('  TRADING-ONLY (migrate or investigate):')
        for sku, info in list(report['trading_only'].items())[:10]:
            tl = info['trading_listings']
            lid = tl[0]['listing_id'] if tl else '?'
            price = tl[0]['price'] if tl else '?'
            action = info['action']
            print(f"    {sku}  listing={lid}  price=${price}  → {action}")
        if len(report['trading_only']) > 10:
            print(f"    ... and {len(report['trading_only']) - 10} more")
        print()

    print('=' * 60)
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit eBay listing state vs local ItemData')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--no-offers', action='store_true',
                        help='Skip per-SKU offer fetch (faster, but no_offer bucket empty)')
    parser.add_argument('--out', default=None,
                        help='Output JSON path (default: /opt/TGW/var/log/ebay-audit-<ts>.json)')
    args = parser.parse_args()

    announce_script_run(
        'ebay_audit.py',
        'cross-reference Inventory API vs Trading API vs local ItemData',
        config=args.config, no_offers=args.no_offers, out=args.out,
    )

    cfg = load_config(Path(args.config))

    report = run_audit(cfg, fetch_offers=not args.no_offers)
    print_summary(report)

    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    out_path = Path(args.out) if args.out else Path('/opt/TGW/var/log') / f'ebay-audit-{ts}.json'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Report saved: {out_path}')

    c = report['counts']
    if c['gap_vs_seller_hub'] != 0:
        print(f"⚠️  Gap of {c['gap_vs_seller_hub']} vs Seller Hub — investigate before proceeding")
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
