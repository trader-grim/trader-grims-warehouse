"""
tgw.workers.ebay_sku_migrate — Gradual eBay listing rename worker (PP-ADD-005 step 4).

For each live eBay listing whose local SKU is non-canonical this worker renames
the custom label on eBay to match the canonical SKU, then renames the local
ItemData folder.

Two paths depending on how the listing was created:

  Legacy (Trading API, no offer_id):
    ReviseFixedPriceItem — changes the custom label in-place.
    Listing age, watchers, listing_id, and price are ALL preserved.
    No delist/relist needed.

  Inventory API (has offer_id):
    Full replace: PUT new inventory item → POST new offer → DELETE old offer
    → PUBLISH new offer → DELETE old inventory item.
    Brief unlisted window is unavoidable for this path.

All 8,370 remaining items (as of 2026-06-04) are legacy Trading API listings,
so the revise path is the default case.

Processes batch_size items per run, then self-schedules for interval_hours later.
Worker is disabled by default — set ebay_sku_migrate.enabled=true to start.

Config keys in tgw-api-config.json (all optional):
  ebay_sku_migrate.enabled        — false by default; set true to activate
  ebay_sku_migrate.batch_size     — items per run (default: 5)
  ebay_sku_migrate.interval_hours — hours between runs (default: 1)

Queue name: ebay_sku_migrate
"""

from __future__ import annotations

import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_delete, ebay_get, ebay_post, ebay_put
from tgw.apis.ebay.trading import revise_item_sku

# PP-FENCE-001: atomic_write_json kept — all three writes happen after os.rename().
# Migrate to fence.create_item in Session C when the dir rename is also fenced.
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.sync import _build_offer_bodies, _find_offer, _get_policies, publish_offer
from tgw.items import atomic_write_json, load_item_doc
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
from tgw.sku_migration import build_migration_map, classify, rename_sku

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_sku_migrate'
_BLOCKED_REGISTRY = Path('/opt/TGW/var/migrate-blocked.json')
_MAX_ASPECT_LEN = 65  # Inventory API rejects aspect values longer than this


def _get_listing_policies(cfg: Dict[str, Any],
                          category_id: Optional[str] = None) -> Dict[str, str]:
    """
    Return {fulfillmentPolicyId, paymentPolicyId, returnPolicyId}.
    Uses explicit IDs from config; picks fulfillment policy by category when
    a category-specific override exists in fulfillment_policy_by_category.
    Falls back to account-default lookup only when config has no IDs at all.
    """
    fulfillment_id = (
        (cfg.get('fulfillment_policy_by_category', {}).get(str(category_id))
         if category_id else None)
        or cfg.get('fulfillment_policy_id')
    )
    explicit = {
        k: v for k, v in {
            'fulfillmentPolicyId': fulfillment_id,
            'paymentPolicyId':     cfg.get('payment_policy_id'),
            'returnPolicyId':      cfg.get('return_policy_id'),
        }.items() if v
    }
    if len(explicit) == 3:
        return explicit
    # Fill any missing IDs from the account lookup
    account = _get_policies(cfg)
    return {**account, **explicit}


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _is_live(item: Dict[str, Any]) -> bool:
    """True when item has an active eBay listing (legacy or Inventory API)."""
    listing = item.get('ebay_listing', {})
    return bool(listing.get('listing_id')
                and listing.get('status') in ('Active', 'PUBLISHED'))


_REASON_CODE_MAP: tuple = (
    (('25019',),                           'POLICY_VIOLATION'),
    (('25002', '25004'),                   'DATA_CONSTRAINT'),
    (('Best Offer',),                      'FEATURE_UNSUPPORTED'),
    (('not currently supported',),         'FEATURE_UNSUPPORTED'),
    (('no price',),                        'NO_PRICE'),
)


def _reason_code(error_text: str) -> str:
    lower = error_text.lower()
    for signals, code in _REASON_CODE_MAP:
        if any(s.lower() in lower for s in signals):
            return code
    return 'UNKNOWN_ERROR'


_PERMANENT_ERROR_SIGNALS = (
    'Best Offer',
    'Inventory-based listing management is not currently supported',
    '"errorId":25709',
    "'errorId': 25709",
    # Policy violation — listing flagged by eBay; needs human review
    '"errorId":25019',
    "'errorId': 25019",
    # Condition invalid for category even after USED_EXCELLENT retry — needs category fix
    '"errorId":25021',
    "'errorId': 25021",
    # Missing/invalid item specific (e.g. Type, Book Title too long) — needs data fix
    '"errorId":25002',
    "'errorId': 25002",
    # Quantity must be > 0 — item sold/zeroed on eBay; mark sold via ebay_legacy_sync
    '"errorId":25004',
    "'errorId': 25004",
    # Invalid category ID — needs category correction in item JSON
    '"errorId":25005',
    "'errorId': 25005",
    # Availability not found — offer in bad state; needs manual inspection
    '"errorId":25604',
    "'errorId': 25604",
    # Duplicate listing detected by eBay
    'already have on eBay',
    # Ended listing can not be revised via Trading API
    'not allowed to revise ended listings',
)


def _is_permanent_failure(error_text: str) -> bool:
    """True when the error is not expected to resolve on retry."""
    return any(sig in error_text for sig in _PERMANENT_ERROR_SIGNALS)


def _sanitize_inv_aspects(inv_body: Dict[str, Any]) -> None:
    """
    Sanitize inventory item body in-place before PUT.
    Trading API accepted multi-value aspects and long values that the Inventory API
    rejects at publish time. Collapses to single value and truncates so migration
    can complete; the operator can refine data later via the editor.
    """
    aspects = inv_body.get('product', {}).get('aspects', {})
    for key, values in aspects.items():
        if not isinstance(values, list):
            continue
        clean = [str(v).strip() for v in values if v and str(v).strip()]
        if not clean:
            continue
        if len(clean) > 1:
            log.info('ebay_sku_migrate: aspect %r: %d values → 1 (%r dropped)',
                     key, len(clean), clean[1:])
        aspects[key] = [clean[0][:_MAX_ASPECT_LEN]]


def _update_blocked_registry(sku: str, entry: Optional[Dict[str, Any]]) -> None:
    """Add or remove a SKU from the blocked-items registry file."""
    try:
        registry: Dict[str, Any] = {}
        if _BLOCKED_REGISTRY.exists():
            registry = json.loads(_BLOCKED_REGISTRY.read_text(encoding='utf-8'))
        if entry is None:
            registry.pop(sku, None)
        else:
            registry[sku] = entry
        _BLOCKED_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        tmp = _BLOCKED_REGISTRY.with_suffix('.tmp')
        tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False),
                       encoding='utf-8')
        tmp.replace(_BLOCKED_REGISTRY)
    except Exception as exc:
        log.warning('ebay_sku_migrate: could not update blocked registry: %s', exc)


def find_batch(cfg: Dict[str, Any],
               batch_size: int) -> List[Tuple[str, str]]:
    """
    Return up to batch_size (old_sku, new_sku) pairs ready for eBay migration.
    Scans the current migration map for non-canonical items with live listings.
    Skips items marked sku_migrate_skip=true (permanent failures).
    """
    migration_map, _ = build_migration_map(cfg)
    batch: List[Tuple[str, str]] = []
    for old_sku, new_sku in migration_map.items():
        if classify(old_sku) == 'C':
            continue
        json_path = cfg['itemdata_root'] / old_sku / f'{old_sku}.json'
        if not json_path.exists():
            continue
        try:
            item = load_item_doc(json_path)
        except Exception:
            continue
        if item.get('sku_migrate_skip'):
            continue
        if item.get('status') == 'sold':
            # Auto-skip sold items — listing is ended, no migration needed
            try:
                from tgw.items import _write_field
                _write_field(cfg, old_sku, 'sku_migrate_skip', True)
            except Exception:
                pass
            continue
        if _is_live(item):
            batch.append((old_sku, new_sku))
        if len(batch) >= batch_size:
            break
    return batch


# ---------------------------------------------------------------------------
# Migration paths
# ---------------------------------------------------------------------------

def _migrate_legacy(cfg: Dict[str, Any],
                    old_sku: str, new_sku: str,
                    listing_id: str) -> Dict[str, Any]:
    """
    Legacy Trading API path: change the custom label in-place via
    ReviseFixedPriceItem.  Listing age and watchers are preserved.
    """
    try:
        revise_item_sku(cfg, listing_id, new_sku)
    except Exception as exc:
        return {'ok': False, 'old_sku': old_sku,
                'error': f'ReviseFixedPriceItem listing {listing_id}: {exc}'}

    result = rename_sku(cfg, old_sku, new_sku, classify(old_sku), dry_run=False)
    if not result.get('ok'):
        # eBay custom label already changed — log clearly for manual fix
        log.error('eBay label revised but local rename failed %s → %s: %s',
                  old_sku, new_sku, result.get('error'))
        return {'ok': False, 'old_sku': old_sku,
                'error': f'local rename failed (eBay already done): {result.get("error")}',
                'ebay_done': True}

    return {
        'ok':         True,
        'path':       'legacy',
        'old_sku':    old_sku,
        'new_sku':    new_sku,
        'listing_id': listing_id,
    }


def _migrate_inventory(cfg: Dict[str, Any],
                       old_sku: str, new_sku: str,
                       item: Dict[str, Any],
                       old_offer_id: str,
                       old_listing_id: str) -> Dict[str, Any]:
    """
    Inventory API path: create new item+offer, delete old offer, publish new offer.
    Brief unlisted window between delete and publish.
    """
    # Use the current live price, not the (stale) draft launch price
    ebay_offer_data = item.get('ebay_offer', {})
    current_price: Optional[float] = (ebay_offer_data.get('price')
                                      or item.get('draft_listing', {}).get('price'))
    if not current_price:
        return {'ok': False, 'old_sku': old_sku, 'error': 'no price found'}

    item_copy = copy.deepcopy(item)
    if item_copy.get('draft_listing'):
        item_copy['draft_listing']['price'] = current_price

    try:
        inv_body, offer_body = _build_offer_bodies(cfg, new_sku, item_copy)
    except ValueError as exc:
        return {'ok': False, 'old_sku': old_sku,
                'error': f'build offer body: {exc}'}

    # PUT new inventory item (idempotent)
    try:
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}',
                 inv_body)
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'PUT inventory_item/{new_sku}: {body}'}

    # Find or create unpublished offer for new_sku
    try:
        existing = _find_offer(cfg, new_sku)
        if existing:
            new_offer_id = existing['offerId']
            ebay_put(cfg, f'/sell/inventory/v1/offer/{new_offer_id}',
                     offer_body)
        else:
            resp = ebay_post(cfg, '/sell/inventory/v1/offer',
                             offer_body)
            new_offer_id = resp.get('offerId', '')
            if not new_offer_id:
                return {'ok': False, 'old_sku': old_sku,
                        'error': f'POST offer returned no offerId: {resp}'}
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'offer create/update: {body}'}

    # DELETE old offer — ends old listing; point of no return
    try:
        ebay_delete(cfg, f'/sell/inventory/v1/offer/{old_offer_id}')
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status != 404:
            body = exc.response.text[:200] if exc.response is not None else str(exc)
            return {'ok': False, 'old_sku': old_sku,
                    'error': f'DELETE old offer {old_offer_id}: {body}'}

    # Publish new offer — retry with USED_EXCELLENT if category rejects condition
    try:
        pub = publish_offer(cfg, new_offer_id)
        new_listing_id  = pub['listing_id']
        new_listing_url = pub['listing_url']
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            errors = exc.response.json().get('errors', [])
            if any(e.get('errorId') == 25021 for e in errors):
                log.warning('condition rejected for %s — retrying with USED_EXCELLENT', new_sku)
                try:
                    inv_body['condition'] = 'USED_EXCELLENT'
                    ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}', inv_body)
                    pub = publish_offer(cfg, new_offer_id)
                    new_listing_id  = pub['listing_id']
                    new_listing_url = pub['listing_url']
                except requests.exceptions.HTTPError as exc2:
                    body = exc2.response.text[:300] if exc2.response is not None else str(exc2)
                    return {'ok': False, 'old_sku': old_sku,
                            'error': f'publish offer {new_offer_id}: {body}',
                            'ebay_done': True}
            else:
                body = exc.response.text[:300]
                return {'ok': False, 'old_sku': old_sku,
                        'error': f'publish offer {new_offer_id}: {body}',
                        'ebay_done': True}
        else:
            body = exc.response.text[:300] if exc.response is not None else str(exc)
            return {'ok': False, 'old_sku': old_sku,
                    'error': f'publish offer {new_offer_id}: {body}',
                    'ebay_done': True}

    # Delete old inventory item (cleanup; non-fatal)
    try:
        ebay_delete(cfg, f'/sell/inventory/v1/inventory_item/{old_sku}')
    except Exception as exc:
        log.warning('could not delete old inventory_item/%s (non-fatal): %s', old_sku, exc)

    # Local rename
    rename_result = rename_sku(cfg, old_sku, new_sku, classify(old_sku), dry_run=False)
    if not rename_result.get('ok'):
        log.error('eBay migrated but local rename failed %s → %s: %s',
                  old_sku, new_sku, rename_result.get('error'))
        return {'ok': False, 'old_sku': old_sku,
                'error': f'local rename failed (eBay already done): {rename_result.get("error")}',
                'ebay_done': True, 'new_offer_id': new_offer_id,
                'new_listing_id': new_listing_id}

    # Update item JSON at new path
    new_path = cfg['itemdata_root'] / new_sku / f'{new_sku}.json'
    item = load_item_doc(new_path)
    now = datetime.now(timezone.utc).isoformat()
    item['ebay_listing'] = {
        'offer_id':     new_offer_id,
        'listing_id':   new_listing_id,
        'listing_url':  new_listing_url,
        'status':       'Active',
        'api':          'inventory',
        'published_at': now,
    }
    item['ebay_offer']['offer_id']     = new_offer_id
    item['ebay_offer']['status']       = 'PUBLISHED'
    item['ebay_offer']['published_at'] = now
    atomic_write_json(new_path, item, pretty=cfg.get('pretty', True))

    return {
        'ok':             True,
        'path':           'inventory',
        'old_sku':        old_sku,
        'new_sku':        new_sku,
        'old_listing_id': old_listing_id,
        'new_listing_id': new_listing_id,
    }


def _migrate_inventory_live(cfg: Dict[str, Any],
                            old_sku: str, new_sku: str,
                            old_offer: Dict[str, Any],
                            old_listing_id: str) -> Dict[str, Any]:
    """
    Inventory API path using live eBay data (no local draft_listing needed).
    GET inventory item + offer from eBay → PUT new item → POST new offer →
    DELETE old offer → PUBLISH new offer → DELETE old item → local rename.
    """
    old_offer_id = old_offer['offerId']

    # GET live inventory item body
    try:
        inv_body = ebay_get(cfg, f'/sell/inventory/v1/inventory_item/{old_sku}')
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'GET inventory_item/{old_sku}: {body}'}

    # Strip read-only / invalid fields from inventory item before re-PUT
    inv_body.pop('sku', None)
    inv_body.pop('locale', None)
    # weight.value=0 is rejected by eBay
    pkg = inv_body.get('packageWeightAndSize', {})
    if pkg.get('weight', {}).get('value', 1) == 0:
        inv_body.pop('packageWeightAndSize', None)
    # allocationByFormat is read-only
    (inv_body.get('availability', {})
             .get('shipToLocationAvailability', {})
             .pop('allocationByFormat', None))
    # Sanitize aspects: collapse multi-value and truncate (Trading API was more permissive)
    _sanitize_inv_aspects(inv_body)

    # Build new offer body from live offer, replacing the SKU and injecting policies
    offer_body = {k: v for k, v in old_offer.items()
                  if k not in ('offerId', 'status', 'listing')}
    offer_body['sku'] = new_sku
    # Live offers often lack explicit policy IDs — inject from account policies
    category_id = old_offer.get('categoryId')
    policies = _get_listing_policies(cfg, category_id)
    offer_body.setdefault('listingPolicies', {}).update(policies)

    # PUT new inventory item (idempotent)
    try:
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}',
                 inv_body)
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'PUT inventory_item/{new_sku}: {body}'}

    # Find or create offer for new_sku
    try:
        existing = _find_offer(cfg, new_sku)
        if existing:
            new_offer_id = existing['offerId']
            ebay_put(cfg, f'/sell/inventory/v1/offer/{new_offer_id}',
                     offer_body)
        else:
            resp = ebay_post(cfg, '/sell/inventory/v1/offer',
                             offer_body)
            new_offer_id = resp.get('offerId', '')
            if not new_offer_id:
                return {'ok': False, 'old_sku': old_sku,
                        'error': f'POST offer returned no offerId: {resp}'}
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'offer create/update: {body}'}

    # DELETE old offer — ends old listing; point of no return
    try:
        ebay_delete(cfg, f'/sell/inventory/v1/offer/{old_offer_id}')
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status != 404:
            body = exc.response.text[:200] if exc.response is not None else str(exc)
            return {'ok': False, 'old_sku': old_sku,
                    'error': f'DELETE old offer {old_offer_id}: {body}'}

    # Publish new offer
    try:
        pub = publish_offer(cfg, new_offer_id)
        new_listing_id  = pub['listing_id']
        new_listing_url = pub['listing_url']
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:300] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'publish offer {new_offer_id}: {body}',
                'ebay_done': True}

    # Delete old inventory item (cleanup; non-fatal)
    try:
        ebay_delete(cfg, f'/sell/inventory/v1/inventory_item/{old_sku}')
    except Exception as exc:
        log.warning('could not delete old inventory_item/%s (non-fatal): %s', old_sku, exc)

    # Local rename
    rename_result = rename_sku(cfg, old_sku, new_sku, classify(old_sku), dry_run=False)
    if not rename_result.get('ok'):
        log.error('eBay migrated but local rename failed %s → %s: %s',
                  old_sku, new_sku, rename_result.get('error'))
        return {'ok': False, 'old_sku': old_sku,
                'error': f'local rename failed (eBay already done): {rename_result.get("error")}',
                'ebay_done': True, 'new_offer_id': new_offer_id,
                'new_listing_id': new_listing_id}

    # Update item JSON at new path
    new_path = cfg['itemdata_root'] / new_sku / f'{new_sku}.json'
    item = load_item_doc(new_path)
    now = datetime.now(timezone.utc).isoformat()
    item['ebay_listing'] = {
        'offer_id':     new_offer_id,
        'listing_id':   new_listing_id,
        'listing_url':  new_listing_url,
        'status':       'Active',
        'api':          'inventory',
        'published_at': now,
    }
    if 'ebay_offer' not in item:
        item['ebay_offer'] = {}
    item['ebay_offer']['offer_id']     = new_offer_id
    item['ebay_offer']['status']       = 'PUBLISHED'
    item['ebay_offer']['published_at'] = now
    atomic_write_json(new_path, item, pretty=cfg.get('pretty', True))

    return {
        'ok':             True,
        'path':           'inventory_live',
        'old_sku':        old_sku,
        'new_sku':        new_sku,
        'old_listing_id': old_listing_id,
        'new_listing_id': new_listing_id,
    }


def _recover_partial(cfg: Dict[str, Any],
                     old_sku: str, new_sku: str,
                     new_offer: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recovery path for partial-state items: new_sku offer exists but is unpublished
    (old offer was already deleted in a previous run).  Inject policies, publish,
    clean up old inventory item, then do the local rename.
    """
    new_offer_id = new_offer['offerId']
    log.info('ebay_sku_migrate: recovering partial state %s → %s (offer %s)',
             old_sku, new_sku, new_offer_id)

    # Re-sanitize the inventory item — previous run may have PUT it before
    # sanitization was added, or eBay's validation has changed.
    try:
        inv_body = ebay_get(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}')
        inv_body.pop('sku', None)
        inv_body.pop('locale', None)
        pkg = inv_body.get('packageWeightAndSize', {})
        if pkg.get('weight', {}).get('value', 1) == 0:
            inv_body.pop('packageWeightAndSize', None)
        (inv_body.get('availability', {})
                 .get('shipToLocationAvailability', {})
                 .pop('allocationByFormat', None))
        _sanitize_inv_aspects(inv_body)
        ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}', inv_body)
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'recovery sanitize PUT inventory_item/{new_sku}: {body}'}
    except Exception as exc:
        log.warning('recovery: could not re-sanitize inventory item %s (continuing): %s',
                    new_sku, exc)

    # Inject account policies — the offer was likely created without them
    try:
        category_id = new_offer.get('categoryId')
        policies = _get_listing_policies(cfg, category_id)
        offer_body = {k: v for k, v in new_offer.items()
                      if k not in ('offerId', 'status', 'listing')}
        offer_body.setdefault('listingPolicies', {}).update(policies)
        ebay_put(cfg, f'/sell/inventory/v1/offer/{new_offer_id}',
                 offer_body)
    except requests.exceptions.HTTPError as exc:
        body = exc.response.text[:200] if exc.response is not None else str(exc)
        return {'ok': False, 'old_sku': old_sku,
                'error': f'PUT offer {new_offer_id} (recovery): {body}'}

    # Publish — retry with USED_EXCELLENT if category rejects the stored condition
    try:
        pub = publish_offer(cfg, new_offer_id)
        new_listing_id  = pub['listing_id']
        new_listing_url = pub['listing_url']
    except requests.exceptions.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 400:
            errors = exc.response.json().get('errors', [])
            if any(e.get('errorId') == 25021 for e in errors):
                log.warning('recovery: condition rejected for %s — retrying with USED_EXCELLENT',
                            new_sku)
                try:
                    inv_body2 = ebay_get(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}')
                    inv_body2.pop('sku', None)
                    inv_body2.pop('locale', None)
                    inv_body2['condition'] = 'USED_EXCELLENT'
                    ebay_put(cfg, f'/sell/inventory/v1/inventory_item/{new_sku}', inv_body2)
                    pub = publish_offer(cfg, new_offer_id)
                    new_listing_id  = pub['listing_id']
                    new_listing_url = pub['listing_url']
                except requests.exceptions.HTTPError as exc2:
                    body = exc2.response.text[:300] if exc2.response is not None else str(exc2)
                    return {'ok': False, 'old_sku': old_sku,
                            'error': f'publish offer {new_offer_id} (recovery+condition): {body}',
                            'ebay_done': True}
            else:
                body = exc.response.text[:300]
                return {'ok': False, 'old_sku': old_sku,
                        'error': f'publish offer {new_offer_id} (recovery): {body}',
                        'ebay_done': True}
        else:
            body = exc.response.text[:300] if exc.response is not None else str(exc)
            return {'ok': False, 'old_sku': old_sku,
                    'error': f'publish offer {new_offer_id} (recovery): {body}',
                    'ebay_done': True}

    # Delete old inventory item (non-fatal)
    try:
        ebay_delete(cfg, f'/sell/inventory/v1/inventory_item/{old_sku}')
    except Exception as exc:
        log.warning('recovery: could not delete old inventory_item/%s: %s', old_sku, exc)

    # Local rename
    rename_result = rename_sku(cfg, old_sku, new_sku, classify(old_sku), dry_run=False)
    if not rename_result.get('ok'):
        log.error('recovery: publish OK but local rename failed %s → %s: %s',
                  old_sku, new_sku, rename_result.get('error'))
        return {'ok': False, 'old_sku': old_sku,
                'error': f'local rename failed (eBay already done): {rename_result.get("error")}',
                'ebay_done': True, 'new_offer_id': new_offer_id,
                'new_listing_id': new_listing_id}

    # Update item JSON at new path
    new_path = cfg['itemdata_root'] / new_sku / f'{new_sku}.json'
    item = load_item_doc(new_path)
    now = datetime.now(timezone.utc).isoformat()
    item['ebay_listing'] = {
        'offer_id':     new_offer_id,
        'listing_id':   new_listing_id,
        'listing_url':  new_listing_url,
        'status':       'Active',
        'api':          'inventory',
        'published_at': now,
    }
    item.setdefault('ebay_offer', {})
    item['ebay_offer']['offer_id']     = new_offer_id
    item['ebay_offer']['status']       = 'PUBLISHED'
    item['ebay_offer']['published_at'] = now
    atomic_write_json(new_path, item, pretty=cfg.get('pretty', True))

    return {
        'ok':           True,
        'path':         'recovery',
        'old_sku':      old_sku,
        'new_sku':      new_sku,
        'new_listing_id': new_listing_id,
    }


def migrate_one(cfg: Dict[str, Any],
                old_sku: str, new_sku: str) -> Dict[str, Any]:
    """Migrate one live eBay listing to the canonical SKU."""
    old_path = cfg['itemdata_root'] / old_sku / f'{old_sku}.json'
    if not old_path.exists():
        return {'ok': False, 'old_sku': old_sku, 'error': 'source JSON not found'}

    item = load_item_doc(old_path)
    listing = item.get('ebay_listing', {})
    listing_id = listing.get('listing_id', '')
    if not listing_id:
        return {'ok': False, 'old_sku': old_sku, 'error': 'no listing_id'}
    if listing.get('status') not in ('Active', 'PUBLISHED'):
        return {'ok': False, 'old_sku': old_sku,
                'error': f'listing status {listing.get("status")!r} — not active'}

    # Local data may not have offer_id even for Inventory API listings —
    # look it up live from eBay to determine which path to use.
    offer_id = item.get('ebay_offer', {}).get('offer_id')
    live_offer: Optional[Dict[str, Any]] = None
    if not offer_id:
        live_offer = _find_offer(cfg, old_sku)
        if live_offer:
            offer_id = live_offer['offerId']
            # Guard: if the offer is sold/ended on eBay but locally still Active,
            # update local status and skip — do NOT re-list a sold item.
            live_status = live_offer.get('status', '')
            if live_status not in ('PUBLISHED', 'UNPUBLISHED', 'ACTIVE', ''):
                log.warning(
                    'migrate_one: %s — eBay offer %s is %r (not active); '
                    'updating local status and skipping',
                    old_sku, offer_id, live_status,
                )
                fence_ebay_write(
                    cfg, old_sku,
                    ebay_listing={'status': live_status},
                )
                return {'ok': False, 'old_sku': old_sku, 'skip': True,
                        'error': f'eBay offer status is {live_status!r} — item not active'}

    if offer_id:
        if live_offer is not None:
            # Have live offer data already — use it directly
            return _migrate_inventory_live(cfg, old_sku, new_sku, live_offer, listing_id)
        else:
            # Belt-and-suspenders: if photo_urls missing locally, fall back to live path.
            if not item.get('ebay_offer', {}).get('photo_urls'):
                log.warning(
                    'migrate_one: %s — offer_id known locally but photo_urls missing; '
                    'falling back to live eBay fetch',
                    old_sku,
                )
                live_offer = _find_offer(cfg, old_sku)
                if live_offer:
                    return _migrate_inventory_live(
                        cfg, old_sku, new_sku, live_offer, listing_id)
            return _migrate_inventory(cfg, old_sku, new_sku, item, offer_id, listing_id)
    else:
        # Check for partial state: a previous run may have created new_sku offer
        # but failed before publishing (old offer already deleted)
        new_offer = _find_offer(cfg, new_sku)
        if new_offer:
            return _recover_partial(cfg, old_sku, new_sku, new_offer)
        return _migrate_legacy(cfg, old_sku, new_sku, listing_id)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class EbaySkuMigrateWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('ebay_sku_migrate worker started: owner=%s', self.owner)

        migrate_cfg = self.config.get('ebay_sku_migrate', {})
        if not migrate_cfg.get('enabled', True):
            log.info('ebay_sku_migrate: disabled in config — exiting')
            return

        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                )
                log.info('ebay_sku_migrate: enqueued startup job')
        except Exception as exc:
            log.warning('ebay_sku_migrate: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        migrate_cfg = self.config.get('ebay_sku_migrate', {})
        batch_size  = int(migrate_cfg.get('batch_size', 5))
        interval_h  = float(migrate_cfg.get('interval_hours', 1))

        log.info('ebay_sku_migrate: scanning for batch (size=%d)', batch_size)
        tgw_logging.log_event('ebay_sku_migrate_start', batch_size=batch_size)

        batch = find_batch(self.config, batch_size)
        if not batch:
            log.info('ebay_sku_migrate: no live non-canonical items remain — done')
            tgw_logging.log_event('ebay_sku_migrate_complete', remaining=0)
            return  # don't reschedule — migration is finished

        stats = {'attempted': len(batch), 'succeeded': 0, 'failed': 0}
        for old_sku, new_sku in batch:
            log.info('ebay_sku_migrate: %s → %s', old_sku, new_sku)
            try:
                result = migrate_one(self.config, old_sku, new_sku)
            except Exception as exc:
                result = {'ok': False, 'old_sku': old_sku,
                          'error': f'unhandled: {type(exc).__name__}: {exc}'}

            if result['ok']:
                stats['succeeded'] += 1
                log.info('ebay_sku_migrate: OK %s → %s (path=%s listing=%s)',
                         old_sku, new_sku,
                         result.get('path', '?'),
                         result.get('listing_id') or result.get('new_listing_id', ''))
                tgw_logging.log_event('ebay_sku_migrated',
                                      old_sku=old_sku, new_sku=new_sku,
                                      path=result.get('path'),
                                      listing_id=result.get('listing_id')
                                                 or result.get('new_listing_id'))
            else:
                stats['failed'] += 1
                error_text = result.get('error', '')
                log.error('ebay_sku_migrate: FAILED %s → %s: %s',
                          old_sku, new_sku, error_text)
                if result.get('ebay_done'):
                    log.error('ebay_sku_migrate: eBay already revised/migrated for %s '
                              '— manual local fix required', old_sku)
                if _is_permanent_failure(error_text):
                    try:
                        from tgw.items import _write_field
                        now = datetime.now(timezone.utc).isoformat()
                        blocked_entry = {
                            'error': error_text[:300],
                            'blocked_at': now,
                            'ebay_done': result.get('ebay_done', False),
                        }
                        review_block = {
                            'stage':       'ebay_sku_migrate',
                            'reason_code': _reason_code(error_text),
                            'error':       error_text[:300],
                            'suggestion':  None,
                            'flagged_at':  now,
                            'retested_at': None,
                            'ready':       False,
                        }
                        _write_field(self.config, old_sku, 'sku_migrate_skip', True)
                        _write_field(self.config, old_sku, 'sku_migrate_blocked',
                                     blocked_entry)
                        _write_field(self.config, old_sku, 'review_block', review_block)
                        _update_blocked_registry(old_sku, blocked_entry)
                        stats.setdefault('skipped_permanent', 0)
                        stats['skipped_permanent'] += 1
                        log.warning('ebay_sku_migrate: permanent failure — blocked %s '
                                    '(use "tgw migrate-unblock %s" to re-queue)',
                                    old_sku, old_sku)
                        tgw_logging.log_event('ebay_sku_migrate_skip',
                                              old_sku=old_sku, reason=error_text[:120])
                    except Exception as write_exc:
                        log.error('ebay_sku_migrate: could not write skip flag for %s: %s',
                                  old_sku, write_exc)

        log.info('ebay_sku_migrate batch complete: %s', stats)
        tgw_logging.log_event('ebay_sku_migrate_batch', **stats)
        self._reschedule(interval_h)

    def _on_terminal_failure(self, job: Dict[str, Any], error_text: str) -> None:
        # audit#1143 #1234 follow-up (todo #1242): a dead-lettered batch must
        # not end the chain — migration would silently stall forever. handle()
        # computes interval_h from config before the batch loop; recompute the
        # same way here since a terminal failure means handle() never reached
        # its own self._reschedule(interval_h) call.
        migrate_cfg = self.config.get('ebay_sku_migrate', {})
        interval_h = float(migrate_cfg.get('interval_hours', 1))
        log.warning('ebay_sku_migrate dead-lettered; rescheduling next batch anyway')
        self._reschedule(interval_h)

    def _reschedule(self, interval_hours: float) -> None:
        next_run = time.time() + interval_hours * 3600
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={'reason': 'scheduled'},
            not_before=next_run,
            max_attempts=3,
        )
        log.info('ebay_sku_migrate: next run in %.1fh (job %s)', interval_hours, jid)
        tgw_logging.log_event('ebay_sku_migrate_rescheduled',
                              next_run_in_hours=interval_hours)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-sku-migrate-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbaySkuMigrateWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
