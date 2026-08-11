#!/usr/bin/env python3
"""
ebay_backfill_offers.py — write offer_id + listing_id + price to every ItemData JSON.

Strategy:
  1. Page through all Inventory API items (100/page) to collect all live SKUs.
  2. For each SKU missing offer data locally, fetch offer from eBay.
  3. Write ebay_offer + ebay_listing blocks via the tgw-api fence
     (apis.fence.ebay_write) — never atomic_write_json directly.
  4. Save progress checkpoint every 500 items so the script can resume.

audit#1143 #1204+#1205 (todo #1236): this used to bypass the fence entirely
via atomic_write_json, which (a) silently discarded any concurrent
ebay_sync/ebay_publish fence write (lost-update race) and skipped the
fence's protected-subfield merge logic, and (b) never enqueued
catalog_rebuild (invariant A7), leaving catalog/thumbnails stale after a
fleet-wide backfill. Routing through apis.fence.ebay_write closes both —
the fence write path already enqueues catalog_rebuild per A7.

Usage:
  sudo -u tgw python3 scripts/ebay_backfill_offers.py
  sudo -u tgw python3 scripts/ebay_backfill_offers.py --resume
  sudo -u tgw python3 scripts/ebay_backfill_offers.py --limit N
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.config import load_config
from tgw.ebay.pull import fetch_offer_for_sku, iter_inventory_api_items
from tgw.logging import announce_script_run

LOG_PATH  = Path('/opt/TGW/var/log/ebay-backfill-offers.log')
CKPT_PATH = Path('/opt/TGW/var/run/ebay-backfill-offers-ckpt.json')
ITEMDATA  = Path('/opt/TGW/data/ItemData')

# CI/portability fix: logging.FileHandler(LOG_PATH) used to run at import
# time, so merely importing this module (e.g. tests/test_ebay_backfill_offers.py's
# offline unit tests, or any CI runner without /opt/TGW) crashed with
# FileNotFoundError before a single test could run. Deferred into main() —
# every CI run on main/PRs since 2026-06-15 failed collection on this exact line.
log = logging.getLogger('backfill')


def load_checkpoint() -> dict:
    if CKPT_PATH.exists():
        return json.loads(CKPT_PATH.read_text())
    return {'done': [], 'failed': []}


def save_checkpoint(ckpt: dict):
    CKPT_PATH.write_text(json.dumps(ckpt))


def has_offer_data(item: dict) -> bool:
    offer = item.get('ebay_offer') or {}
    return bool(offer.get('offer_id') or offer.get('listing_id'))


def main():
    # CI/portability: tests call main() directly with mocked I/O boundaries
    # but no /opt/TGW filesystem — fall back to stream-only logging rather
    # than crashing on a log directory that doesn't exist there.
    try:
        _handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH)]
    except OSError:
        _handlers = [logging.StreamHandler(sys.stdout)]
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=_handlers,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--rate', type=float, default=0.3,
                        help='seconds between offer calls (default 0.3 = ~3/sec)')
    args = parser.parse_args()

    announce_script_run(
        'ebay_backfill_offers.py',
        'write offer_id + listing_id + price to every ItemData JSON via the tgw-api fence',
        resume=args.resume, limit=args.limit, rate=args.rate,
    )

    cfg = load_config(Path('/opt/TGW/config/tgw-api-config.json'))
    ckpt = load_checkpoint() if args.resume else {'done': [], 'failed': []}
    done_set = set(ckpt['done'])

    log.info('--- eBay offer backfill starting ---')
    log.info('resume=%s  limit=%s  rate=%.2fs', args.resume, args.limit or 'all', args.rate)

    log.info('Step 1: paging through eBay Inventory API items...')
    all_skus = []
    for item in iter_inventory_api_items(cfg, limit=100):
        sku = item.get('sku', '')
        if sku:
            all_skus.append(sku)
    log.info('Inventory API: %d SKUs collected', len(all_skus))

    to_process = [s for s in all_skus if s not in done_set]
    if args.limit:
        to_process = to_process[:args.limit]
    total = len(to_process)
    log.info('Step 2: fetching offers for %d SKUs', total)

    written = 0
    skipped_no_local = 0
    skipped_already  = 0
    failed           = 0

    for i, sku in enumerate(to_process, 1):
        if i % 200 == 0:
            log.info('progress: %d/%d  written=%d  failed=%d', i, total, written, failed)
            save_checkpoint(ckpt)

        item_dir = ITEMDATA / sku
        json_path = item_dir / f'{sku}.json'
        if not json_path.exists():
            skipped_no_local += 1
            ckpt['done'].append(sku)
            continue

        try:
            item = json.loads(json_path.read_text())
        except Exception as e:
            log.warning('json read error %s: %s', sku, e)
            ckpt['failed'].append(sku)
            failed += 1
            continue

        if has_offer_data(item):
            skipped_already += 1
            ckpt['done'].append(sku)
            continue

        offer = fetch_offer_for_sku(cfg, sku)
        time.sleep(args.rate)

        if offer is None:
            ckpt['failed'].append(sku)
            failed += 1
            log.debug('no offer found: %s', sku)
            continue

        offer_id   = offer.get('offerId', '')
        listing_id = offer.get('listing', {}).get('listingId', '')
        price      = offer.get('pricingSummary', {}).get('price', {}).get('value', '')
        status     = offer.get('listing', {}).get('listingStatus', 'ACTIVE')
        sold_qty   = offer.get('listing', {}).get('soldQuantity', 0)
        category   = offer.get('categoryId', '')

        # Pass only the fields we're actually setting — NOT a locally
        # pre-merged copy of the existing ebay_offer/ebay_listing blocks.
        # The fence does its own current-state merge server-side (audit#1143
        # #1204+#1205, todo #1236): pre-merging locally from our own read
        # earlier in this loop and sending the WHOLE block back would
        # reintroduce the same lost-update race one layer up, overwriting
        # any concurrent ebay_sync/ebay_publish write with our stale copy.
        try:
            fence_ebay_write(
                cfg, sku,
                ebay_offer={
                    'offer_id':    offer_id,
                    'listing_id':  listing_id,
                    'price':       price,
                    'category_id': category,
                },
                ebay_listing={
                    'listing_id':     listing_id,
                    'listing_status': status,
                    'sold_quantity':  sold_qty,
                },
            )
            written += 1
            ckpt['done'].append(sku)
        except Exception as e:
            log.warning('write error %s: %s', sku, e)
            ckpt['failed'].append(sku)
            failed += 1

    save_checkpoint(ckpt)
    log.info('--- backfill complete ---')
    log.info('written=%d  skipped_already=%d  skipped_no_local=%d  failed=%d',
             written, skipped_already, skipped_no_local, failed)


if __name__ == '__main__':
    main()
