"""
tgw.ebay.repush — Re-PUT inventory item(s) to eBay from local ebay_submitted snapshot.

cmd_ebay_repush(cfg, skus, *, all_listed, dry_run) -> Dict[str, Any]

Nuclear-option recovery: when eBay loses listing data, re-push every active
item from the local ebay_submitted.inventory_item snapshot without re-staging.
Requires the item to have both ebay_submitted.inventory_item and
ebay_offer.offer_id set (written by ebay_stage).

Also backs the operator-facing "Resync Photos" button (Dave, 2026-07-17):
photo re-verification only ran on first publish (ebay_publish.py), so an
already-Active item's photo_verify could go stale after any later photo
push and stay wrong with no way for an operator to force a recheck — the
same failure class as the s43 incident referenced in _refresh_photo_verify's
docstring. Rather than wiring repush into every "Update Listing" click (Dave:
"the photos really don't need to be pushed every time"), this module now
owns refreshing photo_verify itself, on demand, per fence.py's own comment
that ebay_repush is the field's intended owner.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import tgw.config as config
from tgw.apis.ebay.client import ebay_get, ebay_put
from tgw.apis.fence import ebay_write as fence_ebay_write

log = logging.getLogger(__name__)


def _repush_one(cfg: Dict[str, Any], sku: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """
    Re-PUT the inventory item for one SKU using ebay_submitted.inventory_item,
    then re-verify photos actually confirmed live and persist photo_verify.

    Returns a result dict with keys: sku, ok, skipped (bool), reason (str on
    skip/error), and — on a real (non-dry-run) push — submitted_count/
    confirmed_count so a caller (e.g. the Resync Photos button) can show the
    operator an immediate, accurate number instead of a bare "done".
    """
    json_path: Path = config.sku_json(cfg, sku)
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

    # Bug found live (Dave, 2026-07-17): re-PUTting the stale ebay_submitted
    # snapshot as-is just re-confirms whatever subset was already on eBay —
    # it never picks up photos added/changed locally since the last stage.
    # Rebuild imageUrls from the CURRENT local photo set, same source +
    # 24-cap ebay_stage.py uses, so a resync can actually fix a limited set.
    current_urls = (item.get('draft_listing') or {}).get('imageUrls') or [
        e['url'] for e in (item.get('ebay_photos') or []) if e.get('url')
    ]
    if current_urls:
        inv_body = dict(inv_body)
        inv_body['product'] = dict(inv_body.get('product') or {})
        inv_body['product']['imageUrls'] = current_urls[:24]

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

    submitted_urls = (inv_body.get('product') or {}).get('imageUrls') or []
    photo_verify: Dict[str, Any] = {
        'submitted_count': len(submitted_urls),
        'confirmed_count': None,
        'verified_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        live = ebay_get(cfg, f'/sell/inventory/v1/inventory_item/{sku}')
        confirmed_urls = (live.get('product') or {}).get('imageUrls') or []
        photo_verify['confirmed_count'] = len(confirmed_urls)
        if len(confirmed_urls) < len(submitted_urls):
            log.warning('ebay_repush: %s photo count mismatch — submitted=%d confirmed=%d',
                        sku, len(submitted_urls), len(confirmed_urls))
        # Also refresh ebay_live (Dave, 2026-07-17: resync showed 24/24
        # confirmed but the item page's own "Photos on eBay" strip still
        # showed 1) — that display reads item['ebay_live'], a separate
        # cache only otherwise updated by the ebay_sync worker. repush
        # already has the freshest live GET right here; without writing
        # it back, ebay_submitted/photo_verify become correct while the
        # operator-facing display keeps showing stale data indefinitely.
        fence_ebay_write(cfg, sku, ebay_listing={'photo_verify': photo_verify},
                         allow_protected=['photo_verify'],
                         ebay_submitted={'inventory_item': inv_body},
                         ebay_live={'inventory_item': live})
    except Exception as exc:
        log.warning('ebay_repush: %s photo_verify refresh failed (non-fatal): %s', sku, exc)

    return {
        'sku': sku, 'ok': True, 'skipped': False, 'offer_id': offer_id,
        'submitted_count': photo_verify['submitted_count'],
        'confirmed_count': photo_verify['confirmed_count'],
    }


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
        for sku in iter_all_skus(cfg):
            jf = config.sku_json(cfg, sku)
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
