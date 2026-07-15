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

import json
import logging
from typing import Any, Dict, List, Optional

import requests

from tgw.apis.ebay.client import ebay_get, ebay_post, ebay_put

log = logging.getLogger(__name__)


def format_ebay_error(body: str, status: int) -> str:
    """Extract human-readable messages from eBay error JSON.

    Shared by ebay_publish.py and ebay_stage.py (audit#1143 #1171, finding
    17 — was byte-for-byte duplicated in both, a future fix to one would
    have silently missed the other).
    """
    try:
        errs = json.loads(body).get('errors', [])
        msgs = [e.get('longMessage') or e.get('message', '') for e in errs if e.get('longMessage') or e.get('message')]
        if msgs:
            return '; '.join(msgs)
    except Exception:
        pass
    return f'HTTP {status}: {body[:300]}'


class AmbiguousOfferError(RuntimeError):
    """Raised when a SKU has more than one existing eBay offer across
    marketplaces — the exact cross-marketplace duplicate-listing risk
    ebay_motors_census.py's report flags as 'needs human review, not
    auto-resolution' (todo #1214/#1254/#1255 code-review follow-up). Never
    silently pick one — callers should let this propagate so the job
    fails loudly (dead-letters) instead of guessing."""


MARKETPLACE_ID = 'EBAY_US'

# eBay Motors US is a genuinely SEPARATE category tree (ID 100) from
# EBAY_US's tree (ID 0) — confirmed live 2026-07-09 (todo #1254/
# PP-EBAY-MOTORS-001): a categoryId used by real Motors listings 404s
# against tree 0, and only resolves under tree 100. It is NOT a branch of
# the EBAY_US tree, an assumption an earlier planning pass got wrong.
# eBay rejects createOffer when marketplaceId/categoryId don't agree, so a
# genuinely new item in a Motors-tree category would fail outright under
# the old hardcoded EBAY_US — this is what makes the check below necessary,
# not optional.
_MOTORS_MARKETPLACE_ID = 'EBAY_MOTORS'

# eBay's Account API (Business Policies: fulfillment/payment/return) uses a
# DIFFERENT marketplace enum spelling for Motors than the Sell/Inventory
# API's offer.marketplaceId field — 'EBAY_MOTORS_US', not 'EBAY_MOTORS'
# (confirmed live 2026-07-10, code-review follow-up: the same per-API-family
# enum inconsistency already found for the Taxonomy API's
# get_default_category_tree_id in #1254). _get_policies() translates
# through this map; callers everywhere else keep using the offer-level
# 'EBAY_MOTORS' value.
_ACCOUNT_API_MARKETPLACE_ID = {
    _MOTORS_MARKETPLACE_ID: 'EBAY_MOTORS_US',
}


def _is_motors_category(cfg: Dict[str, Any], category_id: str) -> bool:
    """True if *category_id* belongs to eBay Motors' distinct category tree.

    Delegates to tgw.apis.ebay.taxonomy.is_motors_category() (todo #1255)
    — backed by a local disk+memory cache of the full Motors tree, not a
    live call per category. Only matters when creating a genuinely NEW
    offer (an existing offer's real marketplaceId is used directly
    instead, see stage_draft).
    """
    from tgw.apis.ebay.taxonomy import is_motors_category
    return is_motors_category(cfg, category_id)

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
    '3000':                     'USED_EXCELLENT',  # Trading API conditionId for generic "Used"
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

_policies_cache: Dict[str, Dict[str, str]] = {}
_location_cache: Optional[str] = None
_store_categories_cache: Optional[List[Dict[str, Any]]] = None


def _get_policies(cfg: Dict[str, Any], marketplace_id: str = MARKETPLACE_ID) -> Dict[str, str]:
    """Return {fulfillmentPolicyId, paymentPolicyId, returnPolicyId} for the
    account, scoped to *marketplace_id* (eBay Business Policies are
    per-marketplace — code-review follow-up to #1254/#1255: this used to
    hardcode EBAY_US unconditionally, so a Motors offer could get an
    EBAY_US policy id attached, which eBay would reject as invalid for
    that marketplace). Cached per marketplace_id, not a single global."""
    if marketplace_id in _policies_cache:
        return _policies_cache[marketplace_id]

    account_marketplace_id = _ACCOUNT_API_MARKETPLACE_ID.get(marketplace_id, marketplace_id)

    def _first(path: str, list_key: str, id_field: str) -> str:
        data = ebay_get(cfg, path, params={'marketplace_id': account_marketplace_id})
        items = data.get(list_key, [])
        if not items:
            raise RuntimeError(
                f'No {list_key} found in eBay account for marketplace '
                f'{account_marketplace_id!r} — create at least one in Seller Hub'
            )
        return items[0][id_field]

    policies = {
        'fulfillmentPolicyId': _first('/sell/account/v1/fulfillment_policy',
                                      'fulfillmentPolicies', 'fulfillmentPolicyId'),
        'paymentPolicyId':     _first('/sell/account/v1/payment_policy',
                                      'paymentPolicies', 'paymentPolicyId'),
        'returnPolicyId':      _first('/sell/account/v1/return_policy',
                                      'returnPolicies', 'returnPolicyId'),
    }
    _policies_cache[marketplace_id] = policies
    log.info('eBay account policies for %s: %s', marketplace_id, policies)
    return policies


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
                            thickness_in: Optional[float] = None,
                            free_shipping: bool = False) -> Optional[str]:
    """
    Resolve the fulfillment (shipping) policy id by precedence:

      0. per-item shipping_profile  (PP-HINT-001) — a name mapped in
         ``fulfillment_policy_by_profile``; unmapped names fall through rather than
         being forwarded to eBay as a raw policy ID.  Takes precedence over
         free_shipping so bulky/oversized profiles are honoured.
      1. free_shipping flag          — ``fulfillment_policy_free_shipping`` (PP-FREESHIP-001)
         Used when the item's price already includes shipping cost.
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
        sp = str(shipping_profile)
        # Session 42 (Dave's one-at-a-time test): the item editor's shipping
        # selector saves the chosen POLICY ID into shipping_profile, but this
        # resolver only accepted mapped NAMES — the operator's explicit FC4
        # selection was silently discarded (fell through to the account-first
        # fallback, which shipped FC8). An all-digit value IS a policy id:
        # honor the operator's choice directly.
        if sp.isdigit() and len(sp) >= 8:
            return sp
        by_profile = cfg.get('fulfillment_policy_by_profile', {})
        resolved = by_profile.get(sp)
        if resolved:
            return str(resolved)
        # Unmapped profile NAME — fall through rather than forward it verbatim.
        # Log so misconfigured/typo'd profile names are visible before eBay rejects the listing.
        log.warning('sync: shipping_profile %r not in fulfillment_policy_by_profile — falling through',
                    shipping_profile)

    if free_shipping:
        policy = cfg.get('fulfillment_policy_free_shipping')
        if policy:
            return str(policy)

    by_cat = cfg.get('fulfillment_policy_by_category', {})
    if str(ebay_category_id) in by_cat:
        return str(by_cat[str(ebay_category_id)])

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
                          thickness_in: Optional[float] = None,
                          free_shipping: bool = False,
                          marketplace_id: str = MARKETPLACE_ID) -> Dict[str, str]:
    """
    Return {fulfillmentPolicyId, paymentPolicyId, returnPolicyId} for an offer.

    Prefers explicit config values so per-item / per-category / per-size_class
    fulfillment overrides work (see _resolve_fulfillment_id for precedence).
    Falls back to eBay account first-policy lookup when config is incomplete.

    *marketplace_id*: for anything other than EBAY_US (e.g. EBAY_MOTORS),
    the config-based overrides below are skipped entirely and this always
    resolves via the account API scoped to that marketplace (code-review
    follow-up to #1254/#1255) — tgw-api-config.json's fulfillment_policy_id
    etc. are EBAY_US business policies; eBay scopes business policies per
    marketplace, so reusing them for a different marketplace's offer would
    get rejected. If no policies exist yet for that marketplace, this
    raises a clear RuntimeError (create one in Seller Hub) rather than
    silently attaching an EBAY_US policy id that eBay would reject anyway.
    """
    if marketplace_id != MARKETPLACE_ID:
        return _get_policies(cfg, marketplace_id=marketplace_id)

    fulf_id = _resolve_fulfillment_id(cfg, ebay_category_id,
                                      shipping_profile=shipping_profile,
                                      size_class=size_class,
                                      thickness_in=thickness_in,
                                      free_shipping=free_shipping)
    pay_id = cfg.get('payment_policy_id')
    ret_id = cfg.get('return_policy_id')

    # Per-field: use each configured/resolved value where present; consult the
    # account first-policy lookup ONLY for fields that are actually missing.
    # Session 45: the previous all-or-nothing gate (all three or none) silently
    # discarded a valid configured FC4 whenever payment/return ids were absent
    # from config — every new listing shipped with eBay's first-listed policy
    # ('PS') instead. A fallback on any field is a finding, not a quiet default.
    resolved = {
        'fulfillmentPolicyId': str(fulf_id) if fulf_id else None,
        'paymentPolicyId':     str(pay_id) if pay_id else None,
        'returnPolicyId':      str(ret_id) if ret_id else None,
    }
    missing = [k for k, v in resolved.items() if not v]
    if missing:
        account = _get_policies(cfg)
        for k in missing:
            resolved[k] = account[k]
        log.error(
            'sync: policy field(s) %s missing from config — fell back to eBay '
            'account first-listed policy (%s). Configure them in '
            'tgw-api-config.json; first-listed is arbitrary and has shipped '
            'wrong policies before (s45).',
            ', '.join(missing), {k: resolved[k] for k in missing},
        )
    return resolved  # all values are non-None strings here


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
    """Return the existing eBay offer for *sku*, or None.

    No marketplace_id filter (todo #1254/PP-EBAY-MOTORS-001, live-verified
    2026-07-09): filtering by EBAY_US made this 404 for a SKU whose real
    offer lives under EBAY_MOTORS, so stage_draft would conclude "no
    existing offer" and could attempt to create a DUPLICATE under EBAY_US
    for an item already live on Motors. A bare sku= query returns the one
    offer regardless of which marketplace it's actually on.

    Raises AmbiguousOfferError if the SKU genuinely has MORE THAN ONE offer
    (real cross-marketplace duplicate risk, code-review follow-up to
    #1254) — never silently pick offers[0], which would be an
    undocumented, order-dependent guess about which marketplace's offer is
    "the" one to update.
    """
    try:
        data = ebay_get(cfg, '/sell/inventory/v1/offer', params={'sku': sku})
        offers = data.get('offers', [])
    except Exception as exc:
        log.debug('offer lookup for %s returned: %s', sku, exc)
        return None
    if len(offers) > 1:
        marketplaces = sorted({str(o.get('marketplaceId') or '?') for o in offers})
        raise AmbiguousOfferError(
            f'{sku} has {len(offers)} existing offers across marketplaces '
            f'{marketplaces} — needs human review, not auto-resolution '
            f'(see ebay-marketplace-census report, todo #1214)'
        )
    return offers[0] if offers else None


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------

def _build_offer_bodies(cfg: Dict[str, Any], sku: str,
                        item: Dict[str, Any], *,
                        known_marketplace_id: Optional[str] = None) -> tuple:
    """
    Build (inv_body, offer_body) for the eBay Inventory + Offer APIs.
    Raises ValueError for missing required fields.

    *known_marketplace_id*: pass the SKU's EXISTING offer's real
    marketplaceId when the caller already looked one up (code-review
    follow-up to #1254/#1255) — ground truth always wins over guessing
    from the category, and skips the Motors category-tree check entirely
    (the common case: most calls are updates to an already-staged item,
    not brand-new creates).
    """
    draft = item.get('draft_listing', {})
    ebay_offer = item.get('ebay_offer', {})

    price = draft.get('price') or ebay_offer.get('price')
    if price is None:
        raise ValueError(f'{sku}: no price set — run ebay_price or set draft_listing.price')

    image_urls: List[str] = (
        draft.get('imageUrls')
        or [e['url'] for e in item.get('ebay_photos', [])]
    )[:24]  # eBay max is 24 images per listing
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

    # Never assume EBAY_US — a genuinely new item in a Motors-tree category
    # (todo #1254/PP-EBAY-MOTORS-001) needs marketplaceId=EBAY_MOTORS or
    # eBay rejects the createOffer call outright. Ground truth (an existing
    # offer's own live marketplaceId, passed by the caller) always wins
    # over this guess, and skips the Motors category-tree check entirely.
    # Computed BEFORE _get_listing_policies below so listing policies can
    # also be resolved for the correct marketplace (code-review follow-up:
    # EBAY_US business policies attached to a Motors offer get rejected).
    if known_marketplace_id:
        offer_marketplace_id = known_marketplace_id
    else:
        offer_marketplace_id = (
            _MOTORS_MARKETPLACE_ID if _is_motors_category(cfg, category_id_str) else MARKETPLACE_ID
        )

    # PP-HINT-001 / PP-STORAGE-001 / PP-FULFILLMENT-001: a per-item shipping_profile,
    # size_class, or confirmed thickness_in drives fulfillment policy selection.
    _thickness = item.get('thickness_in')
    try:
        _thickness = float(_thickness) if _thickness not in (None, '') else None
    except (TypeError, ValueError):
        _thickness = None
    policies     = _get_listing_policies(
        cfg, category_id_str,
        shipping_profile=draft.get('shipping_profile') or item.get('shipping_profile'),
        size_class=item.get('size_class'),
        thickness_in=_thickness,
        free_shipping=bool(item.get('free_shipping', False)),
        marketplace_id=offer_marketplace_id,
    )
    if draft.get('return_policy_id'):
        policies = dict(policies)
        policies['returnPolicyId'] = str(draft['return_policy_id'])

    # PP-OFFER-001 follow-up (todo #1256): offer.listingPolicies.bestOfferTerms
    # is a per-item Inventory API field, not an account default — only send it
    # when the operator has made an explicit choice (draft_listing.best_offer_enabled
    # is not None); leaving it unset means "don't touch, let eBay use whatever
    # the category default is" rather than silently forcing it off.
    if draft.get('best_offer_enabled') is not None:
        policies = dict(policies)
        best_offer_terms: Dict[str, Any] = {
            'bestOfferEnabled': bool(draft['best_offer_enabled']),
        }
        auto_accept = draft.get('best_offer_auto_accept_price')
        if auto_accept not in (None, ''):
            best_offer_terms['autoAcceptPrice'] = {'currency': 'USD', 'value': f'{float(auto_accept):.2f}'}
        auto_decline = draft.get('best_offer_auto_decline_price')
        if auto_decline not in (None, ''):
            best_offer_terms['autoDeclinePrice'] = {'currency': 'USD', 'value': f'{float(auto_decline):.2f}'}
        policies['bestOfferTerms'] = best_offer_terms

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
    cond_desc = draft.get('condition_description', '').strip()
    if cond_desc:
        inv_body['conditionDescription'] = cond_desc

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
        'marketplaceId':       offer_marketplace_id,
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

    # Secondary eBay marketplace category (optional — costs extra on eBay)
    secondary_cat_id = str(draft.get('secondary_category_id') or '').strip()
    if secondary_cat_id:
        offer_body['secondaryCategoryId'] = secondary_cat_id

    # PP-STORE-001: file item into matching eBay store categories (primary + optional secondary).
    # Prefer store_category_id from draft (set by ebay_draft via category-groups.json);
    # fall back to the config-based store_category_by_ebay_category name mapping.
    store_cats = _get_store_categories_cached(cfg)
    store_cat_id  = str(draft.get('store_category_id') or '').strip()
    store_cat2_id = str(draft.get('secondary_store_category_id') or '').strip()

    store_names: List[str] = []
    if store_cat_id:
        m = next((c for c in store_cats if c['id'] == store_cat_id), None)
        if m:
            store_names.append(m['name'])
    if store_cat2_id:
        m2 = next((c for c in store_cats if c['id'] == store_cat2_id), None)
        if m2:
            store_names.append(m2['name'])

    if not store_names:
        store_names = _resolve_store_category_names(cfg, category_id_str)

    if store_names:
        offer_body['storeCategoryNames'] = store_names[:2]  # eBay max 2

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
    Raises AmbiguousOfferError if the SKU has more than one existing offer
    across marketplaces (never auto-resolved — see _find_offer).
    """
    # Look up any existing offer FIRST (code-review follow-up to #1254):
    # its own live marketplaceId is ground truth and, when present, means
    # _build_offer_bodies never needs to guess via the Motors category-tree
    # check at all — skipping that work on the common path (most stage_draft
    # calls are updates to an already-staged item, not brand-new creates).
    existing = _find_offer(cfg, sku)
    known_marketplace_id = existing.get('marketplaceId') if existing else None
    inv_body, offer_body = _build_offer_bodies(
        cfg, sku, item, known_marketplace_id=known_marketplace_id)

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

    return {'offer_id': offer_id, 'status': 'UNPUBLISHED', 'inventory_item': inv_body}


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

    No marketplace_id filter (code-review follow-up to #1254): the old
    hardcoded EBAY_US would silently exclude any Motors offers from this
    account-wide reconciliation, the same bug class fixed in _find_offer.
    eBay's own getOffers docs describe marketplace_id as a purely optional
    narrowing filter with no correctness effect when omitted — this call
    was already receiving offers from whichever marketplaces exist,
    dropping the filter just stops silently excluding non-EBAY_US ones.
    NOTE: this endpoint is currently blocked account-wide by an unrelated,
    pre-existing issue (eBay error 25707 — see the 400-handling below and
    ebay_sync.py's circuit breaker), which falls back to
    _fetch_offers_by_local_skus() (already marketplace-agnostic, per-SKU).
    Whether getOffers even supports true bulk pagination without a sku
    filter (its docs describe it as SKU-scoped) is a separate, deeper
    question not resolved here — flagged as a follow-up, not fixed.
    """
    import requests as _requests
    results: List[Dict[str, Any]] = []
    offset, limit = 0, 100
    while True:
        try:
            data = ebay_get(cfg, '/sell/inventory/v1/offer',
                            params={'limit': limit, 'offset': offset})
        except _requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 404:
                log.debug('fetch_all_offers: 404 — no Inventory API offers registered')
                break
            if status == 400:
                _NO_OFFERS_IDS = {25702, 25710, 25009}
                try:
                    errors = exc.response.json().get('errors', [])
                    eids = {int(e.get('errorId', 0)) for e in errors}
                except Exception:
                    log.warning('fetch_all_offers: 400 with unparseable body — %s',
                                exc.response.text[:200] if exc.response else '')
                    raise exc
                if eids and eids.issubset(_NO_OFFERS_IDS):
                    log.debug('fetch_all_offers: 400/%s — no offers (graceful empty)', eids)
                    break
                if errors:
                    for e in errors:
                        log.warning('fetch_all_offers: eBay error %s: %s',
                                    e.get('errorId'), e.get('message', ''))
                else:
                    # Parsed OK but the 'errors' list itself was empty — still worth
                    # a log line before re-raising so this doesn't silently look like
                    # a no-op 400 in triage (todo #1397/PP-DEADLETTER-001).
                    log.warning('fetch_all_offers: 400 with empty errors list — %s',
                                exc.response.text[:200] if exc.response else '')
                raise
            raise
        batch = data.get('offers', [])
        results.extend(batch)
        total = int(data.get('total', 0))
        offset += limit
        if offset >= total or not batch:
            break
    return results
