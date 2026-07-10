#!/usr/bin/env python3
"""
ebay_normalize.py — Normalize ebay_live data into ebay_offer / ebay_listing.

No eBay API calls. Reads local ebay_live blocks and writes missing fields to
ebay_offer / ebay_listing / draft_listing via the fence.

Fixes applied to every item that has ebay_live:
  1. ebay_offer.photo_urls  ← ebay_live.inventory_item.product.imageUrls
     (unblocks ebay_sku_migrate for ~19k items)
  2. ebay_listing.listing_url  ← "https://www.ebay.com/itm/{listing_id}"
     (49 items missing this)
  3. draft_listing: rename image_urls → imageUrls
     (19k items with wrong snake_case key)

Usage:
  sudo -u tgw python3 scripts/ebay_normalize.py
  sudo -u tgw python3 scripts/ebay_normalize.py --dry-run
  sudo -u tgw python3 scripts/ebay_normalize.py --sku tgw20240101120000000
  sudo -u tgw python3 scripts/ebay_normalize.py --limit 100
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw import quota
from tgw.config import load_config, sku_json
from tgw.resolver import iter_all_skus
from tgw.apis.fence import ebay_write as fence_ebay_write, patch_item as fence_patch_item

LOG_PATH = Path('/opt/TGW/var/log/ebay-normalize.log')

# CI/portability fix: logging.FileHandler(LOG_PATH) used to run at import
# time, so merely importing this module (e.g. tests/test_ebay_normalize.py's
# offline unit tests, or any CI runner without /opt/TGW) crashed with
# FileNotFoundError before a single test could run. Deferred into main() —
# this module has no other import-time side effects, and every CI run on
# main/PRs since 2026-06-15 failed collection on this exact line.
log = logging.getLogger('ebay_normalize')

EBAY_ITEM_URL = 'https://www.ebay.com/itm/{listing_id}'


def _normalize_one(
    cfg: Dict[str, Any],
    sku: str,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {'sku': sku, 'ok': False, 'reason': 'json not found'}

    try:
        item = json.loads(json_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'sku': sku, 'ok': False, 'reason': f'parse error: {exc}'}

    live = item.get('ebay_live')
    if not live:
        return {'sku': sku, 'ok': True, 'skipped': True, 'reason': 'no ebay_live'}

    changes: List[str] = []

    # --- 1. ebay_offer.photo_urls from ebay_live ---
    image_urls: List[str] = (
        live.get('inventory_item', {}).get('product', {}).get('imageUrls') or []
    )
    offer_photo_urls = (item.get('ebay_offer') or {}).get('photo_urls')
    new_ebay_offer: Optional[Dict[str, Any]] = None
    if image_urls and not offer_photo_urls:
        new_ebay_offer = {'photo_urls': image_urls}
        changes.append(f'ebay_offer.photo_urls ({len(image_urls)} urls)')

    # --- 2. ebay_listing.listing_url ---
    listing_id = (
        (item.get('ebay_listing') or {}).get('listing_id')
        or (item.get('ebay_offer') or {}).get('listing_id')
    )
    current_url = (item.get('ebay_listing') or {}).get('listing_url')
    new_ebay_listing: Optional[Dict[str, Any]] = None
    if listing_id and not current_url:
        new_ebay_listing = {'listing_url': EBAY_ITEM_URL.format(listing_id=listing_id)}
        changes.append('ebay_listing.listing_url')

    # --- 3. draft_listing: rename image_urls → imageUrls ---
    draft = item.get('draft_listing') or {}
    new_draft: Optional[Dict[str, Any]] = None
    if 'image_urls' in draft and 'imageUrls' not in draft:
        # patch_item deep-merges draft_listing; None value deletes the key
        new_draft = {'imageUrls': draft['image_urls'], 'image_urls': None}
        changes.append('draft_listing image_urls→imageUrls')

    if not changes:
        return {'sku': sku, 'ok': True, 'skipped': True, 'reason': 'nothing to do'}

    if dry_run:
        return {'sku': sku, 'ok': True, 'dry_run': True, 'changes': changes}

    try:
        if new_ebay_offer is not None or new_ebay_listing is not None:
            fence_ebay_write(
                cfg, sku,
                ebay_offer=new_ebay_offer,
                ebay_listing=new_ebay_listing,
            )
        if new_draft is not None:
            fence_patch_item(cfg, sku, {'draft_listing': new_draft})
    except Exception as exc:
        return {'sku': sku, 'ok': False, 'reason': f'write error: {exc}', 'changes': changes}

    return {'sku': sku, 'ok': True, 'changes': changes}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_PATH),
        ],
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--sku', help='normalize a single SKU')
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()

    cfg = load_config(Path('/opt/TGW/config/tgw-api-config.json'))
    key_path = cfg['secrets_root'] / 'tgw-api-key.json'
    cfg['api_key'] = json.loads(key_path.read_text(encoding='utf-8'))['api_key']

    # This script's writes go through the fence as a background/machine
    # caller — without this, http_server treats every patch_item call as
    # operator-originated and auto-enqueues a live force=True ebay_stage
    # push to eBay for every touched item (real EPS-quota consumption
    # despite this script's own "No eBay API calls" docstring promise).
    quota.set_context('background', 'ebay_normalize')

    if args.sku:
        result = _normalize_one(cfg, args.sku, dry_run=args.dry_run)
        print(json.dumps(result, indent=2))
        return

    log.info('--- ebay_normalize starting (dry_run=%s) ---', args.dry_run)

    skus: List[str] = list(iter_all_skus(cfg))
    if args.limit:
        skus = skus[:args.limit]
    total = len(skus)
    log.info('scanning %d SKUs', total)

    updated = 0
    skipped = 0
    errors: List[Dict[str, Any]] = []

    for i, sku in enumerate(skus, 1):
        if i % 500 == 0:
            log.info('progress %d/%d  updated=%d  skipped=%d  errors=%d',
                     i, total, updated, skipped, len(errors))

        result = _normalize_one(cfg, sku, dry_run=args.dry_run)
        if result.get('skipped'):
            skipped += 1
        elif result.get('ok'):
            updated += 1
            if args.dry_run and i <= 5:
                log.info('dry-run %s: %s', sku, result.get('changes'))
        else:
            errors.append(result)
            log.warning('error %s: %s', sku, result.get('reason'))

    log.info('--- done ---')
    log.info('total=%d  updated=%d  skipped=%d  errors=%d',
             total, updated, skipped, len(errors))
    if errors:
        log.warning('first 10 errors: %s', json.dumps(errors[:10], indent=2))
    if args.dry_run:
        log.info('(dry-run — no writes made)')


if __name__ == '__main__':
    main()
