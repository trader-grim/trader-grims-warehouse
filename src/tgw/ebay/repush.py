"""
tgw.ebay.repush — Re-PUT inventory item(s) to eBay from local ebay_submitted snapshot.

cmd_ebay_repush(cfg, skus, *, all_listed, dry_run) -> Dict[str, Any]

Nuclear-option recovery: when eBay loses listing data, re-push every active
item from the local ebay_submitted.inventory_item snapshot without re-staging.
Requires the item to have both ebay_submitted.inventory_item and
ebay_offer.offer_id set (written by ebay_stage).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from tgw.apis.ebay.client import ebay_put

log = logging.getLogger(__name__)


def _repush_one(cfg: Dict[str, Any], sku: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """
    Re-PUT the inventory item for one SKU using ebay_submitted.inventory_item.

    Returns a result dict with keys: sku, ok, skipped (bool), reason (str on skip/error).
    """
    json_path: Path = cfg['itemdata_root'] / sku / f'{sku}.json'
    if not json_path.exists():
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': 'item JSON not found'}

    try:
        item = json.loads(json_path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': f'JSON parse error: {exc}'}

    submitted = item.get('ebay_submitted') or {}
    inv_body = submitted.get('inventory_item')
    if not inv_body:
        return {
            'sku': sku, 'ok': False, 'skipped': True,
            'reason': 'no ebay_submitted.inventory_item — run ebay_stage first',
        }

    offer_id = (item.get('ebay_offer') or {}).get('offer_id')
    if not offer_id:
        return {
            'sku': sku, 'ok': False, 'skipped': True,
            'reason': 'no ebay_offer.offer_id — item not staged',
        }

    if dry_run:
        return {'sku': sku, 'ok': True, 'skipped': False, 'dry_run': True,
                'offer_id': offer_id, 'reason': 'dry-run: no eBay API call made'}

    try:
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{sku}', inv_body)
    except Exception as exc:
        return {'sku': sku, 'ok': False, 'skipped': False, 'reason': f'eBay PUT failed: {exc}'}

    log.info('ebay_repush: re-PUT inventory item for %s (offerId=%s)', sku, offer_id)
    return {'sku': sku, 'ok': True, 'skipped': False, 'offer_id': offer_id}


def cmd_ebay_repush(
    cfg: Dict[str, Any],
    skus: List[str],
    *,
    all_listed: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Re-PUT inventory item(s) to eBay from local ebay_submitted.inventory_item snapshot.

    With --all-listed: scans all items with ebay_listing.status=Active and
    re-pushes each one. With explicit SKU(s): re-pushes only those items.
    --dry-run: validates eligibility without making any eBay API calls.
    """
    from tgw.resolver import iter_all_skus

    target_skus: List[str] = []

    if all_listed:
        root: Path = cfg['itemdata_root']
        for sku in iter_all_skus(cfg):
            jf = root / sku / f'{sku}.json'
            try:
                doc = json.loads(jf.read_text(encoding='utf-8'))
            except Exception:
                continue
            listing = doc.get('ebay_listing') or {}
            if listing.get('status') == 'Active':
                target_skus.append(sku)
    else:
        target_skus = list(skus)

    if not target_skus:
        return {
            'ok': True,
            'count': 0,
            'pushed': [],
            'skipped': [],
            'errors': [],
            'dry_run': dry_run,
            'note': 'no items matched',
        }

    pushed: List[str] = []
    skipped: List[Dict[str, str]] = []
    errors: List[Dict[str, str]] = []

    for sku in target_skus:
        result = _repush_one(cfg, sku, dry_run=dry_run)
        if result.get('skipped'):
            skipped.append({'sku': sku, 'reason': result.get('reason', '')})
        elif result.get('ok'):
            pushed.append(sku)
        else:
            errors.append({'sku': sku, 'reason': result.get('reason', '')})

    return {
        'ok': len(errors) == 0,
        'count': len(pushed),
        'pushed': pushed,
        'skipped': skipped,
        'errors': errors,
        'dry_run': dry_run,
        'total_scanned': len(target_skus),
    }
