"""
tgw.apis.ebay.promotions — eBay Promotions Management API (Marketing API v1).

Scope required: sell.marketing (already held).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .client import _counted, _headers, ebay_delete, ebay_get, ebay_post

log = logging.getLogger(__name__)

_MKT = '/sell/marketing/v1'


def list_item_price_markdowns(
    cfg: Dict[str, Any],
    marketplace_id: str = 'EBAY_US',
) -> List[Dict[str, Any]]:
    """List all ITEM_PRICE_MARKDOWN promotions. Returns list of promotion summary dicts."""
    data = ebay_get(
        cfg,
        f'{_MKT}/promotions',
        params={
            'marketplace_id': marketplace_id,
            'promotion_type': 'ITEM_PRICE_MARKDOWN',
            'limit': 200,
        },
    )
    return data.get('promotions') or []


def get_item_price_markdown(cfg: Dict[str, Any], promo_id: str) -> Dict[str, Any]:
    """Get full details of a specific ITEM_PRICE_MARKDOWN promotion including listing IDs."""
    return ebay_get(cfg, f'{_MKT}/item_price_markdown/{promo_id}')


def create_item_price_markdown(
    cfg: Dict[str, Any],
    *,
    name: str,
    marketplace_id: str,
    start_date: str,
    end_date: str,
    discount_pct: int,
    listing_ids: List[str],
) -> str:
    """
    Create a DRAFT ITEM_PRICE_MARKDOWN promotion. Returns the promo_id extracted from
    the Location response header.

    Status is DRAFT — operator activates it via `tgw promo start <promo_id>` or
    directly in Seller Hub Marketing → Promotions.
    """
    body = {
        'name': name,
        'marketplaceId': marketplace_id,
        'promotionStatus': 'DRAFT',
        'startDate': f'{start_date}T00:00:00.000Z',
        'endDate': f'{end_date}T00:00:00.000Z',
        'selectedInventoryDiscounts': [
            {
                'discountBenefit': {
                    'percentageOffList': str(discount_pct),
                },
                'inventoryCriterion': {
                    'inventoryCriterionType': 'INVENTORY_BY_VALUE',
                    'inventoryItems': [
                        {'listingId': lid} for lid in listing_ids
                    ],
                },
            }
        ],
    }
    # goes through _counted (not ebay_post) because the Location header is needed
    resp = _counted(
        cfg, 'post', f'{_MKT}/item_price_markdown',
        headers=_headers(cfg),
        json=body,
        timeout=30,
    )
    location = resp.headers.get('Location', '')
    if not location:
        raise RuntimeError('eBay did not return Location header after creating promotion')
    promo_id = location.rstrip('/').split('/')[-1]
    if not promo_id:
        raise RuntimeError(f'Could not extract promo_id from Location header: {location!r}')
    log.info('promotion created: %s (Location: %s)', promo_id, location)
    return promo_id


def pause_promotion(cfg: Dict[str, Any], promo_id: str) -> None:
    """Pause a RUNNING or SCHEDULED promotion (reversible)."""
    ebay_post(cfg, f'{_MKT}/promotion/{promo_id}/pause')
    log.info('promotion paused: %s', promo_id)


def resume_promotion(cfg: Dict[str, Any], promo_id: str) -> None:
    """Resume a PAUSED promotion."""
    ebay_post(cfg, f'{_MKT}/promotion/{promo_id}/resume')
    log.info('promotion resumed: %s', promo_id)


def delete_promotion(cfg: Dict[str, Any], promo_id: str) -> None:
    """Permanently delete a promotion (cannot be undone — use pause for temporary stop)."""
    ebay_delete(cfg, f'{_MKT}/promotion/{promo_id}')
    log.info('promotion deleted: %s', promo_id)
