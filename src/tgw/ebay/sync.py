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
_store_categories_cache: Optional[List[Dict[str, Any]]] = None


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
# Listing policy helpers (config-first, API fallback)
# ---------------------------------------------------------------------------

def _resolve_fulfillment_id(cfg: Dict[str, Any], ebay_category_id: str,
                            shipping_profile: Optional[str] = None,
                            size_class: Optional[str] = None,
                            thickness_in: Optional[float] = None) -> Optional[str]:
    """
    Resolve the fulfillment (shipping) policy id by precedence:

      1. per-item shipping_profile  (PP-HINT-001) — a name in
         ``fulfillment_policy_by_profile``, or a raw policy id if not mapped
      2. per-category override       — ``fulfillment_policy_by_category``
      3. Standard Envelope gate      — ``fulfillment_policy_envelope`` if
         size_class == 'flat' AND thickness_in is known and <= 0.25 in.
         Skipped when thickness_in is None (unknown) or > 0.25 in so
         over-thick flat items fall through to the regular size_class policy.
      4. per-size_class override     (PP-STORAGE-001) — ``fulfillment_policy_by_size_class``
      5. global default              — ``fulfillment_policy_id``

    Returns None if nothing resolves (caller then falls back to the account API).
    """
    if shipping_profile:
        by_profile = cfg.get('fulfillment_policy_by_profile', {})
        return by_profile.get(str(shipping_profile), str(shipping_profile))

    by_cat = cfg.get('fulfillment_policy_by_category', {})
    if str(ebay_category_id) in by_cat:
        return by_cat[str(ebay_category_id)]

    # Standard Envelope gate: only flat items with confirmed thickness <= 0.25 in qualify.
    # Items with unknown thickness (None) are intentionally excluded — assign envelope
    # explicitly via shipping_profile if you've verified the item fits.
    envelope_policy = cfg.get('fulfillment_policy_envelope')
    if (envelope_policy
            and size_class == 'flat'
            and thickness_in is not None
            and thickness_in <= 0.25):
        return str(envelope_policy)

    if size_class:
        by_size = cfg.get('fulfillment_policy_by_size_class', {})
        if str(size_class) in by_size:
            return by_size[str(size_class)]

    return cfg.get('fulfillment_policy_id')


def _get_listing_policies(cfg: Dict[str, Any], ebay_category_id: str, *,
                          shipping_profile: Optional[str] = None,
                          size_class: Optional[str] = None,
                          thickness_in: Optional[float] = None) -> Dict[str, str]:
    """
    Return {fulfillmentPolicyId, paymentPolicyId, returnPolicyId} for an offer.

    Prefers explicit config values so per-item / per-category / per-size_class
    fulfillment overrides work (see _resolve_fulfillment_id for precedence).
    Falls back to eBay account first-policy lookup when config is incomplete.
    """
    fulf_id = _resolve_fulfillment_id(cfg, ebay_category_id,
                                      shipping_profile=shipping_profile,
                                      size_class=size_class,
                                      thickness_in=thickness_in)
    pay_id = cfg.get('payment_policy_id')
    ret_id = cfg.get('return_policy_id')

    if fulf_id and pay_id and ret_id:
        return {
            'fulfillmentPolicyId': str(fulf_id),
            'paymentPolicyId':     str(pay_id),
            'returnPolicyId':      str(ret_id),
        }

    # Fallback: fetch first policy of each type from eBay account
    return _get_policies(cfg)


# ---------------------------------------------------------------------------
# Store category helpers (PP-STORE-001)
# ---------------------------------------------------------------------------

def _get_store_categories_cached(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    global _store_categories_cache
    if _store_categories_cache is not None:
        return _store_categories_cache
    try:
        from tgw.apis.ebay.trading import get_store_categories
        _store_categories_cache = get_store_categories(cfg)
        log.info('store categories loaded: %d', len(_store_categories_cache))
    except Exception as exc:
        log.warning('GetStore failed (%s) — store category injection disabled', exc)
        _store_categories_cache = []
    return _store_categories_cache


def _resolve_store_category_names(cfg: Dict[str, Any],
                                  ebay_category_id: str) -> Optional[List[str]]:
    """
    Return eBay store category name(s) for this eBay taxonomy category, or None.
    Config key: store_category_by_ebay_category — maps eBay cat ID or 'default' → name.
    Accepts a string (one category) or list of up to 2 names.
    """
    mapping: Dict[str, Any] = cfg.get('raw', {}).get('store_category_by_ebay_category', {})
    if not mapping:
        return None
    name = mapping.get(str(ebay_category_id)) or mapping.get('default')
    if not name:
        return None
    if isinstance(name, list):
        return [str(n) for n in name[:2]]
    return [str(name)]


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

    category_id_str = str(draft.get('category_id', ''))
    # PP-HINT-001 / PP-STORAGE-001 / PP-FULFILLMENT-001: a per-item shipping_profile,
    # size_class, or confirmed thickness_in drives fulfillment policy selection.
    _thickness = item.get('thickness_in')
    try:
        _thickness = float(_thickness) if _thickness not in (None, '') else None
    except (TypeError, ValueError):
        _thickness = None
    policies     = _get_listing_policies(
        cfg, category_id_str,
        shipping_profile=item.get('shipping_profile'),
        size_class=item.get('size_class'),
        thickness_in=_thickness,
    )
    location_key = _get_merchant_location(cfg)
    qty          = draft.get('quantity', 1)

    # availabilityDistributions links the inventory item to the merchant location,
    # which carries the country address. Some categories (e.g. 34032, 14027, 13916)
    # require this explicit binding so eBay can resolve Item.Country at publish time.
    # Omitting it causes errorId 25002 "No Item.Country exists" for those categories.
    inv_body: Dict[str, Any] = {
        'product': product_block,
        'condition': condition_enum,
        'availability': {
            'shipToLocationAvailability': {
                'availabilityDistributions': [
                    {'merchantLocationKey': location_key, 'quantity': qty},
                ],
                'quantity': qty,
            },
        },
    }

    # PP-GLOBALS-001: pass the operator-captured shipping weight through to the
    # Inventory API so calculated-shipping buyers get accurate rates. The intake
    # web form captures weight_oz but it was previously dropped on the floor.
    # eBay rejects weight.value == 0, so guard against zero/None/non-numeric
    # (mirrors ebay_sku_migrate.py, which pops a zero-weight block before re-PUT).
    weight_oz = item.get('weight_oz')
    if weight_oz not in (None, ''):
        try:
            weight_val = float(weight_oz)
        except (TypeError, ValueError):
            weight_val = 0.0
        if weight_val > 0:
            inv_body['packageWeightAndSize'] = {
                'weight': {'value': weight_val, 'unit': 'OUNCE'},
            }

    pricing_summary: Dict[str, Any] = {
        'price': {'currency': 'USD', 'value': str(price)},
    }

    # PP-STRIKE-001: add originalRetailPrice when MSRP is available and the
    # feature is enabled in config (requires eBay account-level approval).
    original_retail = draft.get('original_retail_price')
    if original_retail and cfg.get('raw', {}).get('ebay', {}).get('strikethrough_enabled', False):
        try:
            pricing_summary['originalRetailPrice'] = {
                'currency': 'USD',
                'value': f'{float(original_retail):.2f}',
            }
        except (TypeError, ValueError):
            pass

    offer_body: Dict[str, Any] = {
        'sku':                 sku,
        'marketplaceId':       MARKETPLACE_ID,
        'format':              'FIXED_PRICE',
        'availableQuantity':   qty,
        'categoryId':          category_id_str,
        'listingDescription':  listing_description,
        'listingPolicies':     policies,
        'merchantLocationKey': location_key,
        'pricingSummary':      pricing_summary,
        'shipToLocations': {
            'regionIncluded': [{'regionType': 'COUNTRY', 'regionName': 'US'}],
        },
    }

    # PP-STORE-001: file item into the matching eBay store category.
    # Prefer store_category_id from draft (set by ebay_draft via category-groups.json);
    # fall back to the config-based store_category_by_ebay_category name mapping.
    store_cat_id = draft.get('store_category_id')
    if store_cat_id is not None:
        store_cats = _get_store_categories_cached(cfg)
        matched = next((c for c in store_cats if c['id'] == str(store_cat_id)), None)
        if matched:
            offer_body['storeCategoryNames'] = [matched['name']]
        else:
            store_names = _resolve_store_category_names(cfg, category_id_str)
            if store_names:
                offer_body['storeCategoryNames'] = store_names
    else:
        store_names = _resolve_store_category_names(cfg, category_id_str)
        if store_names:
            offer_body['storeCategoryNames'] = store_names

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
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{sku}', inv_body)
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
                ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{sku}', inv_body)
            else:
                raise
        else:
            raise
    log.info('inventory item upserted for %s', sku)

    existing = _find_offer(cfg, sku)
    if existing:
        offer_id = existing['offerId']
        ebay_put(cfg, f'/sell/inventory/v1/offer/{offer_id}', offer_body)
        log.info('offer updated for %s (offerId=%s)', sku, offer_id)
    else:
        resp = ebay_post(cfg, '/sell/inventory/v1/offer', offer_body)
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
    resp = ebay_post(cfg, f'/sell/inventory/v1/offer/{offer_id}/publish', {})
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
