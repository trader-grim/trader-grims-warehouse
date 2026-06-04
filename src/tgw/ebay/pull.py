"""
tgw.ebay.pull — shared helpers for Trading API active-listing and sold-order sync.

Used by both the ebay_legacy_sync worker (scheduled daily) and the `tgw ebay-pull`
CLI command (on-demand).

Public API:
    build_listing_index(itemdata_root)              → {listing_id: json_path}
    mark_item_sold(json_path, ...)                  → bool (True if newly marked)
    sync_active_listings(cfg, itemdata_root, ...)   → stats dict
    sync_sold_orders(cfg, listing_index, ...)       → stats dict
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import tgw.logging as tgw_logging
from tgw.apis.ebay.trading import get_my_ebay_selling, get_orders
from tgw.items import atomic_write_json

log = logging.getLogger(__name__)

SOLD_INITIAL_LOOKBACK_DAYS = 365
SOLD_ORDERS_WINDOW_DAYS    = 90    # GetOrders API max per call


# ---------------------------------------------------------------------------
# Index builder
# ---------------------------------------------------------------------------

def build_listing_index(itemdata_root: Path) -> Dict[str, Path]:
    """Scan ItemData and return {listing_id: json_path} for all items that have one."""
    index: Dict[str, Path] = {}
    for json_path in itemdata_root.glob('*/*.json'):
        try:
            text = json_path.read_text(encoding='utf-8')
            if '"listing_id"' not in text:
                continue
            item = json.loads(text)
            lid = item.get('ebay_listing', {}).get('listing_id', '')
            if lid:
                index[str(lid)] = json_path
        except Exception:
            pass
    return index


# ---------------------------------------------------------------------------
# Sold marking
# ---------------------------------------------------------------------------

def mark_item_sold(json_path: Path, order_id: str, buyer: str,
                   sale_price: Any, quantity: int, sale_date: str,
                   synced_at: str, cfg: Dict[str, Any],
                   dry_run: bool = False) -> bool:
    """
    Mark an item sold in-place.  Idempotent — returns False if already sold.
    Writes status=sold + ebay_sale block and logs the event.
    """
    item = json.loads(json_path.read_text(encoding='utf-8'))
    if item.get('status') == 'sold':
        return False
    sku = json_path.parent.name
    if dry_run:
        log.info('[dry-run] would mark %s sold order=%s price=$%s', sku, order_id, sale_price)
        return True
    item['status'] = 'sold'
    item.setdefault('ebay_listing', {})['status'] = 'Sold'
    item['ebay_sale'] = {
        'order_id':   order_id,
        'buyer':      buyer,
        'sale_price': sale_price,
        'quantity':   quantity,
        'sale_date':  sale_date,
        'synced_at':  synced_at,
    }
    atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
    log.info('ebay_pull: sold %s order=%s price=$%s', sku, order_id, sale_price)
    tgw_logging.log_event('ebay_item_sold', sku=sku,
                          order_id=order_id, sale_price=sale_price)
    return True


# ---------------------------------------------------------------------------
# Active listings sync
# ---------------------------------------------------------------------------

def sync_active_listings(cfg: Dict[str, Any], itemdata_root: Path,
                         synced_at: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Pull all active eBay listings via GetMyeBaySelling; write back to item JSONs.

    Skips items already managed by the Inventory API (api=inventory) — those are
    handled by the ebay_sync worker.  Returns a stats dict.
    """
    stats: Dict[str, Any] = {
        'fetched': 0, 'matched': 0, 'updated': 0,
        'skipped_inventory': 0, 'orphaned': 0, 'errors': 0,
        'orphans': [],
    }

    listings = list(get_my_ebay_selling(cfg))
    stats['fetched'] = len(listings)
    log.info('ebay_pull: %d active listings fetched', len(listings))

    for listing in listings:
        try:
            _apply_active_listing(listing, itemdata_root, synced_at, stats, dry_run, cfg)
        except Exception:
            log.exception('ebay_pull: error on listing %s', listing.get('listing_id'))
            stats['errors'] += 1

    return stats


def _apply_active_listing(listing: Dict[str, Any], itemdata_root: Path,
                           synced_at: str, stats: Dict[str, Any],
                           dry_run: bool, cfg: Dict[str, Any]) -> None:
    sku = listing.get('custom_label', '').strip()
    if not sku:
        stats['orphans'].append(listing)
        stats['orphaned'] += 1
        return

    json_path = itemdata_root / sku / f'{sku}.json'
    if not json_path.exists():
        log.warning('ebay_pull: listing %s has custom_label %r but no local item',
                    listing['listing_id'], sku)
        stats['orphans'].append(listing)
        stats['orphaned'] += 1
        return

    stats['matched'] += 1
    item = json.loads(json_path.read_text(encoding='utf-8'))
    existing = item.get('ebay_listing', {})

    if existing.get('api') == 'inventory' and existing.get('listing_id'):
        stats['skipped_inventory'] += 1
        return

    new_listing: Dict[str, Any] = {
        'listing_id':   listing['listing_id'],
        'listing_url':  listing['listing_url'],
        'status':       listing['status'],
        'live_price':   listing['live_price'],
        'api':          'trading',
        'synced_at':    synced_at,
    }
    for k in ('offer_id', 'published_at'):
        if existing.get(k):
            new_listing[k] = existing[k]

    if all(new_listing.get(k) == existing.get(k) for k in new_listing):
        return

    if dry_run:
        log.info('[dry-run] would update %s listing_id=%s price=$%s',
                 sku, listing['listing_id'], listing['live_price'])
        stats['updated'] += 1
        return

    item['ebay_listing'] = new_listing
    atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
    stats['updated'] += 1
    log.debug('ebay_pull: synced %s listing_id=%s price=$%s',
              sku, listing['listing_id'], listing['live_price'])


# ---------------------------------------------------------------------------
# Sold orders sync
# ---------------------------------------------------------------------------

def sync_sold_orders(cfg: Dict[str, Any], listing_index: Dict[str, Path],
                     synced_at: str, state_path: Path,
                     dry_run: bool = False) -> Dict[str, Any]:
    """
    Pull completed orders via GetOrders; mark matched items sold.
    Reads/writes state_path for incremental window tracking.
    Returns a stats dict.
    """
    stats: Dict[str, Any] = {'orders_fetched': 0, 'sold_marked': 0, 'errors': 0}
    now = datetime.now(timezone.utc)

    if state_path.exists():
        state = json.loads(state_path.read_text())
        scan_from = datetime.fromisoformat(state['last_synced_at']) - timedelta(hours=2)
    else:
        scan_from = now - timedelta(days=SOLD_INITIAL_LOOKBACK_DAYS)
        log.info('ebay_pull: first sold sync — looking back %d days',
                 SOLD_INITIAL_LOOKBACK_DAYS)

    orders: List[Dict[str, Any]] = []
    window_start = scan_from
    while window_start < now:
        window_end = min(window_start + timedelta(days=SOLD_ORDERS_WINDOW_DAYS), now)
        chunk = list(get_orders(cfg, window_start, window_end))
        log.info('ebay_pull: orders %s–%s → %d',
                 window_start.strftime('%Y-%m-%d'), window_end.strftime('%Y-%m-%d'),
                 len(chunk))
        orders.extend(chunk)
        window_start = window_end

    stats['orders_fetched'] = len(orders)

    for order in orders:
        for tx in order['transactions']:
            listing_id = tx.get('listing_id', '')
            json_path = listing_index.get(listing_id)
            if not json_path or not json_path.exists():
                continue
            try:
                did_mark = mark_item_sold(
                    json_path,
                    order_id=order['order_id'],
                    buyer=order['buyer'],
                    sale_price=tx['sale_price'],
                    quantity=tx['quantity'],
                    sale_date=tx['sale_date'],
                    synced_at=synced_at,
                    cfg=cfg,
                    dry_run=dry_run,
                )
                if did_mark:
                    stats['sold_marked'] += 1
            except Exception as exc:
                log.error('ebay_pull: sold mark failed listing %s: %s', listing_id, exc)
                stats['errors'] += 1

    log.info('ebay_pull: %d items marked sold', stats['sold_marked'])
    tgw_logging.log_event('ebay_sold_sync_complete',
                          marked=stats['sold_marked'],
                          orders_fetched=stats['orders_fetched'])

    if not dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({'last_synced_at': now.isoformat()}, indent=2))

    return stats
