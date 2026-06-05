"""
tgw.ebay.sync — eBay listing staging, publishing, and status sync.

stage_draft(cfg, sku, item)
    Upsert inventory item + create/update UNPUBLISHED offer on eBay.
    Returns {offer_id, status: UNPUBLISHED}.
    Draft is immediately visible and editable in Seller Hub.

publish_offer(cfg, offer_id)
    Publish an existing UNPUBLISHED offer.  One API call.
    Returns {listing_id, listing_url, status: PUBLISHED}.

publish_draft(cfg, sku, item)  [convenience wrapper]
    stage_draft() + publish_offer() in one shot.

fetch_all_offers(cfg)
    Return every eBay offer (all statuses, paginated).

Account policies (fulfillment/payment/return) and the merchant location key
are fetched once per process lifetime and cached.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from tgw.apis.ebay.client import ebay_get, ebay_post, ebay_put

log = logging.getLogger(__name__)

MARKETPLACE_ID = 'EBAY_US'

# ---------------------------------------------------------------------------
# Condition mapping  (AI string → eBay Inventory API enum)
# ---------------------------------------------------------------------------

_CONDITION_MAP: Dict[str, str] = {
    'new':                      'NEW',
    'new in box':               'NEW',
    'brand new':                'NEW',
    'new old stock':            'NEW_OTHER',
    'nos':                      'NEW_OTHER',
    'open box':                 'NEW_OTHER',
    'like new':                 'LIKE_NEW',
    'manufacturer refurbished': 'MANUFACTURER_REFURBISHED',
    'seller refurbished':       'SELLER_REFURBISHED',
    'refurbished':              'SELLER_REFURBISHED',
    'used: excellent':          'USED_EXCELLENT',
    'excellent':                'USED_EXCELLENT',
    'used: very good':          'USED_VERY_GOOD',
    'very good':                'USED_VERY_GOOD',
    'used: good':               'USED_GOOD',
    'good':                     'USED_GOOD',
    'used':                     'USED_GOOD',
    'pre-owned':                'USED_GOOD',
    'pre owned':                'USED_GOOD',
    'used: acceptable':         'USED_ACCEPTABLE',
    'acceptable':               'USED_ACCEPTABLE',
    'fair':                     'USED_ACCEPTABLE',
    'for parts':                'FOR_PARTS_OR_NOT_WORKING',
    'for parts or not working': 'FOR_PARTS_OR_NOT_WORKING',
    'not working':              'FOR_PARTS_OR_NOT_WORKING',
    'parts only':               'FOR_PARTS_OR_NOT_WORKING',
}


def _map_condition(condition: str) -> str:
    return _CONDITION_MAP.get(condition.lower().strip(), 'USED_GOOD')


# ---------------------------------------------------------------------------
# Per-process caches for account-level data that rarely changes
# ---------------------------------------------------------------------------

_policies_cache: Optional[Dict[str, str]] = None
_location_cache: Optional[str] = None


def _get_policies(cfg: Dict[str, Any]) -> Dict[str, str]:
    """Return {fulfillmentPolicyId, paymentPolicyId, returnPolicyId} for the account."""
    global _policies_cache
    if _policies_cache is not None:
        return _policies_cache

    def _first(path: str, list_key: str, id_field: str) -> str:
        data = ebay_get(cfg, path, params={'marketplace_id': MARKETPLACE_ID})
        items = data.get(list_key, [])
        if not items:
            raise RuntimeError(
                f'No {list_key} found in eBay account — create at least one in Seller Hub'
            )
        return items[0][id_field]

    _policies_cache = {
        'fulfillmentPolicyId': _first('/sell/account/v1/fulfillment_policy',
                                      'fulfillmentPolicies', 'fulfillmentPolicyId'),
        'paymentPolicyId':     _first('/sell/account/v1/payment_policy',
                                      'paymentPolicies', 'paymentPolicyId'),
        'returnPolicyId':      _first('/sell/account/v1/return_policy',
                                      'returnPolicies', 'returnPolicyId'),
    }
    log.info('eBay account policies: %s', _policies_cache)
    return _policies_cache


def _get_merchant_location(cfg: Dict[str, Any]) -> str:
    """Return the first enabled merchant location key for the account."""
    global _location_cache
    if _location_cache is not None:
        return _location_cache

    data = ebay_get(cfg, '/sell/inventory/v1/location')
    locations: List[Dict[str, Any]] = data.get('locations', [])
    enabled = [loc for loc in locations
               if loc.get('merchantLocationStatus') == 'ENABLED']
    chosen = (enabled or locations)
    if not chosen:
        raise RuntimeError(
            'No merchant locations found — create one in eBay Seller Hub > Account > Business Policies'
        )
    _location_cache = chosen[0]['merchantLocationKey']
    log.info('eBay merchant location: %s', _location_cache)
    return _location_cache


# ---------------------------------------------------------------------------
# Offer lookup
# ---------------------------------------------------------------------------

def _find_offer(cfg: Dict[str, Any], sku: str) -> Optional[Dict[str, Any]]:
    """Return the existing eBay offer for *sku*, or None."""
    try:
        data = ebay_get(cfg, '/sell/inventory/v1/offer',
                        params={'sku': sku, 'marketplace_id': MARKETPLACE_ID})
        offers = data.get('offers', [])
        return offers[0] if offers else None
    except Exception as exc:
        log.debug('offer lookup for %s returned: %s', sku, exc)
        return None


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def _build_offer_bodies(cfg: Dict[str, Any], sku: str,
                        item: Dict[str, Any]) -> tuple:
    """
    Build (inv_body, offer_body) for the eBay Inventory + Offer APIs.
    Raises ValueError for missing required fields.
    """
    draft = item.get('draft_listing', {})
    ebay_offer = item.get('ebay_offer', {})

    price = draft.get('price') or ebay_offer.get('price')
    if price is None:
        raise ValueError(f'{sku}: no price set — run ebay_price or set draft_listing.price')

    image_urls: List[str] = (
        draft.get('imageUrls')
        or [e['url'] for e in item.get('ebay_photos', [])]
    )
    if not image_urls:
        raise ValueError(f'{sku}: no eBay photo URLs — run ebay_upload first')

    aspects: Dict[str, List[str]] = {
        k: [str(v)] for k, v in draft.get('item_specifics', {}).items()
    }

    # Prefer the pre-resolved condition_enum written by ebay_draft (already
    # validated against the category's allowed conditions). Fall back to the
    # legacy enum map for items drafted before condition resolution was added.
    condition_enum = (draft.get('condition_enum')
                      or _map_condition(item.get('condition', 'used')))
    title       = draft.get('title') or item.get('title', '')
    description = draft.get('description') or item.get('description', '')
    # Use the full listing description (AI text + boilerplate + picklist line) if available
    listing_description = draft.get('listing_description') or description

    product_block: Dict[str, Any] = {
        'title':       title,
        'description': description,
        'imageUrls':   image_urls,
        'aspects':     aspects,
    }
    epid = str(item.get('epid') or '').strip()
    if epid:
        product_block['epid'] = epid

    inv_body: Dict[str, Any] = {
        'product': product_block,
        'condition':    condition_enum,
        'availability': {
            'shipToLocationAvailability': {
                'quantity': draft.get('quantity', 1),
            },
        },
    }

    policies     = _get_policies(cfg)
    location_key = _get_merchant_location(cfg)

    offer_body: Dict[str, Any] = {
        'sku':                 sku,
        'marketplaceId':       MARKETPLACE_ID,
        'format':              'FIXED_PRICE',
        'availableQuantity':   draft.get('quantity', 1),
        'categoryId':          str(draft.get('category_id', '')),
        'listingDescription':  listing_description,
        'listingPolicies':     policies,
        'merchantLocationKey': location_key,
        'pricingSummary':      {
            'price': {'currency': 'USD', 'value': str(price)},
        },
        # Some categories require explicit shipToLocations for Item.Country resolution.
        # The fulfillment policy's implicit coverage is not sufficient for all categories.
        'shipToLocations': {
            'regionIncluded': [{'regionType': 'COUNTRY', 'regionName': 'US'}],
        },
    }

    return inv_body, offer_body


def stage_draft(cfg: Dict[str, Any], sku: str,
                item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upsert the eBay inventory item and create/update an UNPUBLISHED offer.

    The offer is immediately visible and editable in Seller Hub as a draft.
    Does NOT publish — call publish_offer() when ready to go live.

    Returns {offer_id, status: UNPUBLISHED}.
    Raises ValueError for missing price or photos.
    Raises requests.exceptions.* on network/auth failures (caller retries).
    """
    inv_body, offer_body = _build_offer_bodies(cfg, sku, item)

    try:
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{sku}', inv_body,
                 extra_headers={'Content-Language': 'en-US'})
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            body = exc.response.json()
            errors = body.get('errors', [])
            if any(e.get('errorId') == 25021 for e in errors):
                # Category doesn't support this condition granularity — fall back
                # to USED_EXCELLENT (conditionId 3000 "Used") which is universally
                # accepted in categories that allow used items.
                log.warning('%s: condition %r rejected by category — retrying with USED_EXCELLENT',
                            sku, inv_body['condition'])
                inv_body['condition'] = 'USED_EXCELLENT'
                ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{sku}', inv_body,
                         extra_headers={'Content-Language': 'en-US'})
            else:
                raise
        else:
            raise
    log.info('inventory item upserted for %s', sku)

    _cl = {'Content-Language': 'en-US'}
    existing = _find_offer(cfg, sku)
    if existing:
        offer_id = existing['offerId']
        ebay_put(cfg, f'/sell/inventory/v1/offer/{offer_id}', offer_body,
                 extra_headers=_cl)
        log.info('offer updated for %s (offerId=%s)', sku, offer_id)
    else:
        resp = ebay_post(cfg, '/sell/inventory/v1/offer', offer_body,
                         extra_headers=_cl)
        offer_id = resp.get('offerId', '')
        if not offer_id:
            raise RuntimeError(f'create offer returned no offerId for {sku}: {resp}')
        log.info('offer created for %s (offerId=%s)', sku, offer_id)

    return {'offer_id': offer_id, 'status': 'UNPUBLISHED'}


def publish_offer(cfg: Dict[str, Any], offer_id: str) -> Dict[str, Any]:
    """
    Publish an existing UNPUBLISHED eBay offer.  One API call.

    Returns {listing_id, listing_url, status: PUBLISHED}.
    Raises RuntimeError if eBay returns no listingId.
    """
    resp = ebay_post(cfg, f'/sell/inventory/v1/offer/{offer_id}/publish', {},
                     extra_headers={'Content-Language': 'en-US'})
    listing_id = resp.get('listingId', '')
    if not listing_id:
        raise RuntimeError(f'publish offer {offer_id} returned no listingId: {resp}')
    log.info('offer %s published → listingId=%s', offer_id, listing_id)
    return {
        'listing_id':  listing_id,
        'listing_url': f'https://www.ebay.com/itm/{listing_id}',
        'status':      'PUBLISHED',
    }


def publish_draft(cfg: Dict[str, Any], sku: str,
                  item: Dict[str, Any]) -> Dict[str, Any]:
    """Convenience wrapper: stage_draft() then publish_offer() in one shot."""
    staged   = stage_draft(cfg, sku, item)
    published = publish_offer(cfg, staged['offer_id'])
    return {
        'offer_id':    staged['offer_id'],
        'listing_id':  published['listing_id'],
        'listing_url': published['listing_url'],
        'status':      'PUBLISHED',
    }


# ---------------------------------------------------------------------------
# Status sync
# ---------------------------------------------------------------------------

def fetch_all_offers(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Return every eBay offer (paginated, all statuses).
    Each entry contains at minimum: offerId, sku, status, listing (if published).

    Returns [] if the account has no Inventory API offers (eBay returns 400 in
    this case rather than an empty list, so we treat it as a graceful empty).
    """
    import requests as _requests
    results: List[Dict[str, Any]] = []
    offset, limit = 0, 100
    while True:
        try:
            data = ebay_get(cfg, '/sell/inventory/v1/offer',
                            params={'marketplace_id': MARKETPLACE_ID,
                                    'limit': limit, 'offset': offset})
        except _requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 404):
                # eBay returns 400/404 when the seller has no Inventory API offers
                log.debug('fetch_all_offers: no offers (HTTP %s) — returning []', status)
                break
            raise
        batch = data.get('offers', [])
        results.extend(batch)
        total = int(data.get('total', 0))
        offset += limit
        if offset >= total or not batch:
            break
    return results
