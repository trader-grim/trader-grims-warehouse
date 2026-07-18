#!/usr/bin/env python3
"""
ebay_photo_push.py — Restore full photo sets on live eBay listings.

For each listing where local photo count exceeds eBay EPS URL count:
  1. Upload any not-yet-uploaded local photos to eBay EPS (idempotent).
  2. PUT the full imageUrls list back to the live inventory_item.

Run AFTER ebay_sku_migrate completes — items must be on canonical SKUs.

PP-EBAY-MIRROR-001 Phase 1.5 — todo #1073

Usage:
  sudo -u tgw python3 scripts/ebay_photo_push.py
  sudo -u tgw python3 scripts/ebay_photo_push.py --dry-run
  sudo -u tgw python3 scripts/ebay_photo_push.py --sku tgw20240101120000000
  sudo -u tgw python3 scripts/ebay_photo_push.py --limit 20
  sudo -u tgw python3 scripts/ebay_photo_push.py --include-no-eps
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.config import load_config, sku_json
from tgw.logging import announce_script_run
from tgw.resolver import iter_all_skus
from tgw.assets import ordered_photos
from tgw.ebay.upload import upload_photo
from tgw.apis.ebay.client import ebay_get, ebay_put
from tgw.apis.fence import ebay_write as fence_ebay_write, patch_item as fence_patch_item

LOG_PATH = Path('/opt/TGW/var/log/ebay-photo-push.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH),
    ],
)
log = logging.getLogger('ebay_photo_push')


def _push_one(
    cfg: Dict[str, Any],
    sku: str,
    *,
    dry_run: bool = False,
    include_no_eps: bool = False,
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

    offer_id = (item.get('ebay_offer') or {}).get('offer_id')
    if not offer_id:
        return {'sku': sku, 'ok': True, 'skipped': True, 'reason': 'no offer_id (not Inventory API)'}

    eps_urls: List[str] = (
        live.get('inventory_item', {}).get('product', {}).get('imageUrls') or []
    )
    eps_count = len(eps_urls)

    sku_dir = cfg['itemdata_root'] / sku
    photos: List[Path] = ordered_photos(item, sku_dir)
    local_count = len(photos)

    if local_count == 0:
        return {'sku': sku, 'ok': True, 'skipped': True, 'reason': 'no local photos'}

    if eps_count >= local_count and not (include_no_eps and eps_count == 0):
        return {'sku': sku, 'ok': True, 'skipped': True,
                'reason': f'eBay has {eps_count} >= local {local_count}'}

    if dry_run:
        return {'sku': sku, 'ok': True, 'dry_run': True,
                'local_count': local_count, 'eps_count': eps_count}

    # Step 1: upload any photos not yet in ebay_photos
    existing_locals = {e['local'] for e in (item.get('ebay_photos') or [])}
    uploaded: List[Dict[str, str]] = list(item.get('ebay_photos') or [])
    new_uploads = 0
    upload_errors: List[str] = []

    for photo in photos:
        if str(photo) in existing_locals:
            continue
        try:
            url = upload_photo(cfg, photo)
            uploaded.append({'local': str(photo), 'url': url})
            new_uploads += 1
            log.debug('%s: uploaded %s → %s', sku, photo.name, url[:60])
        except Exception as exc:
            upload_errors.append(f'{photo.name}: {exc}')
            log.warning('%s: upload failed for %s: %s', sku, photo.name, exc)

    if upload_errors and not uploaded:
        return {'sku': sku, 'ok': False,
                'reason': f'all uploads failed: {upload_errors[0]}'}

    # Reorder to match photo_order (same logic as ebay_upload worker)
    seen = set()
    reordered: List[Dict[str, str]] = []
    local_to_url = {e['local']: e['url'] for e in uploaded}
    for p in ordered_photos(item, sku_dir):
        key = str(p)
        if key in local_to_url and key not in seen:
            reordered.append({'local': key, 'url': local_to_url[key]})
            seen.add(key)
    for e in uploaded:
        if e['local'] not in seen:
            reordered.append(e)

    all_urls = [e['url'] for e in reordered]

    # Step 2: GET current inventory_item body from eBay
    try:
        inv_body = ebay_get(cfg, f'/sell/inventory/v1/inventory_item/{sku}')
    except Exception as exc:
        return {'sku': sku, 'ok': False,
                'reason': f'GET inventory_item failed: {exc}',
                'new_uploads': new_uploads}

    # Step 3: update imageUrls and PUT back
    inv_body.setdefault('product', {})['imageUrls'] = all_urls
    # Strip read-only fields eBay rejects on PUT
    inv_body.pop('sku', None)
    inv_body.pop('locale', None)
    pkg = inv_body.get('packageWeightAndSize', {})
    if pkg.get('weight', {}).get('value', 1) == 0:
        inv_body.pop('packageWeightAndSize', None)
    (inv_body.get('availability', {})
             .get('shipToLocationAvailability', {})
             .pop('allocationByFormat', None))

    try:
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{sku}', inv_body)
    except Exception as exc:
        return {'sku': sku, 'ok': False,
                'reason': f'PUT inventory_item failed: {exc}',
                'new_uploads': new_uploads}

    # Step 4: update local data via fence
    updated_live = dict(live)
    updated_live.setdefault('inventory_item', {}).setdefault('product', {})['imageUrls'] = all_urls

    try:
        fence_patch_item(cfg, sku, {'ebay_photos': reordered})
        fence_ebay_write(cfg, sku,
                         ebay_live=updated_live,
                         ebay_offer={'photo_urls': all_urls})
    except Exception as exc:
        log.warning('%s: fence write failed (eBay already updated): %s', sku, exc)

    log.info('%s: pushed %d photos (%d new uploads, was %d on eBay)',
             sku, len(all_urls), new_uploads, eps_count)
    return {
        'sku': sku, 'ok': True,
        'photos_pushed': len(all_urls),
        'new_uploads': new_uploads,
        'was_on_ebay': eps_count,
        'upload_errors': upload_errors or None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--sku', help='process a single SKU')
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--delay', type=float, default=0.5,
                        help='seconds between items (default 0.5)')
    parser.add_argument('--include-no-eps', action='store_true',
                        help='also process items with zero EPS photos on eBay')
    args = parser.parse_args()

    announce_script_run(
        'ebay_photo_push.py',
        'restore full photo sets on live eBay listings where local photo count exceeds eBay EPS URL count',
        dry_run=args.dry_run, sku=args.sku, limit=args.limit,
        include_no_eps=args.include_no_eps,
    )

    cfg = load_config(Path('/opt/TGW/config/tgw-api-config.json'))
    key_path = cfg['secrets_root'] / 'tgw-api-key.json'
    cfg['api_key'] = json.loads(key_path.read_text(encoding='utf-8'))['api_key']

    if args.sku:
        result = _push_one(cfg, args.sku, dry_run=args.dry_run,
                           include_no_eps=args.include_no_eps)
        print(json.dumps(result, indent=2))
        return

    log.info('--- ebay_photo_push starting (dry_run=%s include_no_eps=%s) ---',
             args.dry_run, args.include_no_eps)

    skus: List[str] = list(iter_all_skus(cfg))
    if args.limit:
        skus = skus[:args.limit]
    total = len(skus)
    log.info('scanning %d SKUs', total)

    pushed = 0
    skipped = 0
    errors: List[Dict[str, Any]] = []

    for i, sku in enumerate(skus, 1):
        if i % 100 == 0:
            log.info('progress %d/%d  pushed=%d  skipped=%d  errors=%d',
                     i, total, pushed, skipped, len(errors))

        result = _push_one(cfg, sku, dry_run=args.dry_run,
                           include_no_eps=args.include_no_eps)

        if result.get('skipped'):
            skipped += 1
        elif result.get('ok'):
            pushed += 1
        else:
            errors.append({'sku': sku, 'reason': result.get('reason')})
            log.warning('error %s: %s', sku, result.get('reason'))

        if not result.get('skipped') and not args.dry_run and args.delay > 0:
            time.sleep(args.delay)

    log.info('--- done ---')
    log.info('total=%d  pushed=%d  skipped=%d  errors=%d',
             total, pushed, skipped, len(errors))
    if errors:
        log.warning('first 10 errors: %s', json.dumps(errors[:10], indent=2))
    if args.dry_run:
        log.info('(dry-run — no writes made)')


if __name__ == '__main__':
    main()
