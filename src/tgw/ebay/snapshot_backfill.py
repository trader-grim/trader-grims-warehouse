"""
tgw.ebay.snapshot_backfill — Back-fill ebay_submitted for legacy listed items.

For each item that has ebay_listing.listing_id but no ebay_submitted block,
GET /sell/inventory/v1/inventory_item/{sku} from the eBay Inventory API and
write the full response as ebay_submitted.  Items only in the Trading API
(not mirrored to Inventory API) will return 404 and are skipped gracefully.

PP-EBAY-SNAPSHOT-001 Phase 4 — todo #894
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

from tgw.apis.ebay.client import ebay_get
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import sku_json

log = logging.getLogger(__name__)


def _backfill_one(
    cfg: Dict[str, Any],
    sku: str,
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Fetch and save ebay_submitted for one SKU. Returns a result dict."""
    json_path: Path = sku_json(cfg, sku)
    if not json_path.exists():
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': 'item JSON not found'}

    try:
        item = json.loads(json_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': f'JSON parse error: {exc}'}

    listing_id = (item.get('ebay_listing') or {}).get('listing_id')
    if not listing_id:
        return {'sku': sku, 'ok': False, 'skipped': True, 'reason': 'no ebay_listing.listing_id'}

    if item.get('ebay_submitted'):
        return {'sku': sku, 'ok': True, 'skipped': True, 'reason': 'already has ebay_submitted'}

    if dry_run:
        return {'sku': sku, 'ok': True, 'skipped': False, 'dry_run': True,
                'reason': 'dry-run: would fetch from eBay Inventory API'}

    try:
        response = ebay_get(cfg, f'/sell/inventory/v1/inventory_item/{sku}')
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status == 404:
            log.info('ebay_backfill: %s not in Inventory API (404) — Trading-API-only item', sku)
            return {'sku': sku, 'ok': True, 'skipped': True, 'reason': 'not in Inventory API (404)'}
        log.warning('ebay_backfill: GET failed for %s: HTTP %s', sku, status)
        return {'sku': sku, 'ok': False, 'skipped': False,
                'reason': f'HTTP {status}: {exc}'}
    except Exception as exc:
        log.warning('ebay_backfill: GET failed for %s: %s', sku, exc)
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': f'request error: {exc}'}

    ebay_submitted = {
        'inventory_item': response,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }
    item['ebay_submitted'] = ebay_submitted
    try:
        fence_patch_item(cfg, sku, {'ebay_submitted': ebay_submitted})
    except Exception as exc:
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': f'write error: {exc}'}

    log.info('ebay_backfill: saved ebay_submitted for %s', sku)
    return {'sku': sku, 'ok': True, 'skipped': False}


def cmd_ebay_backfill_snapshot(
    cfg: Dict[str, Any],
    *,
    dry_run: bool = False,
    limit: int = 0,
    delay: float = 0.5,
) -> Dict[str, Any]:
    """
    Back-fill ebay_submitted for all items with ebay_listing.listing_id but no
    ebay_submitted block.

    --dry-run: scan and report without making any eBay API calls.
    --limit N: stop after N items processed (0 = all).
    --delay S: seconds to sleep between API calls (default 0.5).
    """
    from tgw.resolver import iter_all_skus

    candidates: List[str] = []

    log.info('ebay_backfill: scanning ItemData for candidates')
    for sku in iter_all_skus(cfg):
        jf = sku_json(cfg, sku)
        try:
            doc = json.loads(jf.read_text(encoding='utf-8'))
        except Exception:
            continue
        listing_id = (doc.get('ebay_listing') or {}).get('listing_id')
        if listing_id and not doc.get('ebay_submitted'):
            candidates.append(sku)

    log.info('ebay_backfill: %d candidate(s) need ebay_submitted', len(candidates))

    if limit > 0:
        candidates = candidates[:limit]

    saved: List[str] = []
    skipped: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []
    not_in_api: List[str] = []

    for i, sku in enumerate(candidates):
        result = _backfill_one(cfg, sku, dry_run=dry_run)

        if result.get('reason') == 'not in Inventory API (404)':
            not_in_api.append(sku)
        elif result.get('skipped'):
            skipped.append({'sku': sku, 'reason': result.get('reason', '')})
        elif result.get('ok'):
            saved.append(sku)
        else:
            errors.append({'sku': sku, 'reason': result.get('reason', '')})

        if not dry_run and delay > 0 and i < len(candidates) - 1:
            time.sleep(delay)

    if saved and not dry_run:
        try:
            from tgw.queue import state_machine
            state_machine.enqueue_catalog_rebuild('ebay_backfill_snapshot')
            log.info('ebay_backfill: enqueued catalog_rebuild')
        except Exception as exc:
            log.warning('ebay_backfill: catalog_rebuild enqueue failed: %s', exc)

    return {
        'ok': len(errors) == 0,
        'total_candidates': len(candidates),
        'saved': saved,
        'saved_count': len(saved),
        'skipped': skipped,
        'not_in_api': not_in_api,
        'errors': errors,
        'dry_run': dry_run,
    }
