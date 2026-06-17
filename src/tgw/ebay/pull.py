"""
tgw.ebay.pull — eBay data pull: Trading API active/sold sync + Inventory API full mirror.

Used by both the ebay_legacy_sync worker (scheduled daily) and the `tgw ebay-pull`
CLI command (on-demand).

Public API:
    build_listing_index(itemdata_root)                          → {listing_id: json_path}
    mark_item_sold(json_path, ...)                              → bool (True if newly marked)
    sync_active_listings(cfg, itemdata_root, ...)               → stats dict
    sync_sold_orders(cfg, listing_index, ...)                   → stats dict
    sync_inventory_api(cfg, itemdata_root, ...)                 → stats dict
    backfill_draft_from_live(item, cfg)                         → bool (True if draft written)
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Set, Tuple

import tgw.config as config
import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_get
from tgw.apis.ebay.trading import get_my_ebay_selling, get_orders
from tgw.items import atomic_write_json

log = logging.getLogger(__name__)

SOLD_INITIAL_LOOKBACK_DAYS = 365
SOLD_ORDERS_WINDOW_DAYS    = 90    # GetOrders API max per call

_TITLE_STOPWORDS = frozenset({
    'a', 'an', 'the', 'and', 'or', 'of', 'in', 'for',
    'by', 'to', 'is', 'it', 'at', 'as', 'on', 'be', 'with',
})


def _tokenize(title: str) -> List[str]:
    return [w for w in re.sub(r'[^\w\s]', ' ', (title or '').lower()).split()
            if len(w) > 2 and w not in _TITLE_STOPWORDS]


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def build_listing_index(itemdata_root: Path) -> Dict[str, Path]:
    """
    Scan ItemData and return {listing_id: json_path}.

    Indexes both ebay_listing.listing_id (Inventory API pipeline) and
    the legacy 'Item number' field (Trading API / pre-pipeline items).
    """
    index: Dict[str, Path] = {}
    for json_path in itemdata_root.glob('*/*.json'):
        try:
            text = json_path.read_text(encoding='utf-8')
            if '"listing_id"' not in text and '"Item number"' not in text:
                continue
            item = json.loads(text)
            lid = item.get('ebay_listing', {}).get('listing_id', '')
            if lid:
                index[str(lid)] = json_path
            item_num = str(item.get('Item number') or '').strip()
            if item_num and item_num not in index:
                index[item_num] = json_path
        except Exception:
            pass
    log.info('build_listing_index: %d eBay IDs indexed', len(index))
    return index


def build_title_lookup(catalog_db: Path, itemdata_root: Path,
                       ) -> Tuple[Dict[str, Tuple[str, Path]], Dict[str, List[str]]]:
    """
    Build an inverted-word title index from the SQLite catalog.

    Returns:
      title_index  — {canonical_key: (sku, json_path)}
      word_index   — {word: [canonical_key, ...]}

    canonical_key is the sorted token list joined by spaces, which makes
    Jaccard scoring straightforward without a second tokenize pass.
    """
    import sqlite3

    title_index: Dict[str, Tuple[str, Path]] = {}
    word_index:  Dict[str, List[str]]         = {}

    try:
        conn = sqlite3.connect(str(catalog_db))
        rows = conn.execute(
            'SELECT sku, title FROM catalog WHERE title IS NOT NULL'
        ).fetchall()
        conn.close()
    except Exception as exc:
        log.warning('build_title_lookup: SQLite unavailable (%s)', exc)
        return {}, {}

    for sku, title in rows:
        if not title or title == sku:
            continue
        tokens = _tokenize(title)
        if len(tokens) < 2:
            continue
        key = ' '.join(sorted(tokens))
        if key not in title_index:
            json_path = itemdata_root / sku / f'{sku}.json'
            title_index[key] = (sku, json_path)
            for word in tokens:
                word_index.setdefault(word, []).append(key)

    log.info('build_title_lookup: %d titles indexed', len(title_index))
    return title_index, word_index


def build_archive_index(archive_dir: Path, itemdata_root: Path,
                        cache_path: Optional[Path] = None,
                        ) -> Dict[str, Tuple[str, Path]]:
    """
    Build (or load from cache) {ebay_id: (sku, live_path)} from ItemArchive zips.

    Scanning 6K+ photo-bearing zips is slow (~minutes on HDD). The result is
    cached to cache_path (default: archive_dir/../var/archive-ebay-index.json).
    Re-scan only when cache is absent or older than any zip in the archive.

    Only includes entries where the live ItemData path still exists.
    """
    import zipfile

    if cache_path is None:
        cache_path = archive_dir.parent.parent / 'var' / 'archive-ebay-index.json'

    # Load from cache if it's newer than the newest zip
    if cache_path.exists():
        cache_mtime = cache_path.stat().st_mtime
        newest_zip  = max(
            (z.stat().st_mtime for z in archive_dir.glob('*.zip')),
            default=0,
        )
        if cache_mtime >= newest_zip:
            raw = json.loads(cache_path.read_text(encoding='utf-8'))
            index = {eid: (sku, itemdata_root / sku / f'{sku}.json')
                     for eid, sku in raw.items()}
            log.info('build_archive_index: loaded %d entries from cache', len(index))
            return index

    # Full scan — slow on HDD (one seek per zip); result is cached for all future runs.
    zip_paths = sorted(archive_dir.glob('*.zip'))
    total     = len(zip_paths)
    log.info('build_archive_index: scanning %d zips in %s…', total, archive_dir.name)
    print(f'  Scanning {total} archive zips (slow first run — subsequent runs use cache)…',
          flush=True)

    eid_to_sku: Dict[str, str] = {}
    scanned = 0

    for zip_path in zip_paths:
        sku       = zip_path.stem
        json_name = f'{sku}.json'
        try:
            with zipfile.ZipFile(zip_path) as zf:
                if json_name not in zf.namelist():
                    continue
                data = json.loads(zf.read(json_name).decode('utf-8'))
        except Exception:
            continue

        scanned += 1
        if scanned % 500 == 0:
            print(f'  … {scanned}/{total}', flush=True)
        if not isinstance(data, dict):
            continue
        ebay_id = str(data.get('Item number') or data.get('ebay_id') or '').strip()
        if ebay_id and ebay_id not in ('0', '') and ebay_id not in eid_to_sku:
            eid_to_sku[ebay_id] = sku

    # Write cache (sku strings only — Paths are reconstructed on load)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(eid_to_sku, indent=2), encoding='utf-8')
    log.info('build_archive_index: scanned %d zips → %d eBay IDs; cache written to %s',
             scanned, len(eid_to_sku), cache_path)

    index = {eid: (sku, itemdata_root / sku / f'{sku}.json')
             for eid, sku in eid_to_sku.items()}
    return index


def restore_archive_tombstone(
    archive_dir: Path,
    sku: str,
    itemdata_root: Path,
    cfg: Dict[str, Any],
) -> Optional[Path]:
    """
    Extract {sku}.json from an archive ZIP into ItemData so it can be marked sold.

    Creates itemdata_root/{sku}/{sku}.json with:
      - all original fields from the archive ZIP
      - ebay_listing.listing_id populated from Item number (if present)
      - _archive_tombstone: True marker

    Returns the json_path on success, None on failure.
    Idempotent — if the file already exists it is returned immediately.
    """
    import zipfile

    json_path = itemdata_root / sku / f'{sku}.json'
    if json_path.exists():
        return json_path

    zip_path = archive_dir / f'{sku}.zip'
    if not zip_path.exists():
        log.warning('restore_archive_tombstone: no archive ZIP for %s', sku)
        return None

    json_name = f'{sku}.json'
    try:
        with zipfile.ZipFile(zip_path) as zf:
            if json_name not in zf.namelist():
                log.warning('restore_archive_tombstone: %s not in %s', json_name, zip_path.name)
                return None
            item = json.loads(zf.read(json_name).decode('utf-8'))
    except Exception as exc:
        log.warning('restore_archive_tombstone: failed reading %s: %s', zip_path.name, exc)
        return None

    item_number = str(item.get('Item number') or '').strip()
    if item_number and item_number not in ('0', ''):
        item.setdefault('ebay_listing', {})['listing_id'] = item_number

    item['_archive_tombstone'] = True

    json_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
    log.info('restore_archive_tombstone: restored %s from %s', sku, zip_path.name)
    tgw_logging.log_event('archive_tombstone_restored', sku=sku)
    return json_path


def find_title_match(
    query_title: str,
    title_index: Dict[str, Tuple[str, Path]],
    word_index:  Dict[str, List[str]],
    threshold:   float = 0.80,
) -> Optional[Tuple[str, Path, float]]:
    """
    Find the best title match for a query string using Jaccard similarity.

    Returns (sku, json_path, score) if a unique match >= threshold exists.
    Returns None if below threshold, no candidates, or ambiguous (tie).
    """
    query_tokens = _tokenize(query_title)
    if not query_tokens:
        return None

    query_set = set(query_tokens)

    # Gather candidates via inverted index (titles sharing ≥1 word)
    candidate_hits: Dict[str, int] = {}
    for word in query_tokens:
        for key in word_index.get(word, []):
            candidate_hits[key] = candidate_hits.get(key, 0) + 1

    if not candidate_hits:
        return None

    # Score top 30 by word-overlap count, then by Jaccard
    best_score  = 0.0
    best_key: Optional[str] = None
    tie_count   = 0

    for key in sorted(candidate_hits, key=lambda k: -candidate_hits[k])[:30]:
        key_set = set(key.split())
        score   = len(query_set & key_set) / len(query_set | key_set)
        if score > best_score:
            best_score = score
            best_key   = key
            tie_count  = 1
        elif score == best_score:
            tie_count += 1

    if best_score < threshold or best_key is None or tie_count > 1:
        return None

    sku, json_path = title_index[best_key]
    return sku, json_path, best_score


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
                         synced_at: str, dry_run: bool = False,
                         sku_filter: Optional[Set[str]] = None) -> Dict[str, Any]:
    """
    Pull all active eBay listings via GetMyeBaySelling; write back to item JSONs.

    Skips items already managed by the Inventory API (api=inventory) — those are
    handled by the ebay_sync worker.  Returns a stats dict.

    sku_filter: if provided, only process listings whose custom_label is in the set.
    """
    stats: Dict[str, Any] = {
        'fetched': 0, 'matched': 0, 'updated': 0,
        'skipped_inventory': 0, 'orphaned': 0, 'errors': 0,
        'orphans': [],
    }

    listings = list(get_my_ebay_selling(cfg))
    stats['fetched'] = len(listings)
    if sku_filter is not None:
        log.info('ebay_pull: %d active listings fetched (filter: %d SKUs)',
                 len(listings), len(sku_filter))
    else:
        log.info('ebay_pull: %d active listings fetched', len(listings))

    for listing in listings:
        try:
            if sku_filter is not None:
                sku = listing.get('custom_label', '').strip()
                if sku not in sku_filter:
                    continue
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


# ---------------------------------------------------------------------------
# Inventory API full mirror
# ---------------------------------------------------------------------------

_HTML_TAG_RE  = re.compile(r'<[^>]+>')
_WHITESPACE   = re.compile(r'\s+')

# Inventory API condition enum → conditionId
_ENUM_TO_CONDITION_ID: Dict[str, str] = {
    'NEW':                        '1000',
    'NEW_OTHER':                  '1500',
    'NEW_WITH_DEFECTS':           '1750',
    'CERTIFIED_REFURBISHED':      '2000',
    'EXCELLENT_REFURBISHED':      '2010',
    'VERY_GOOD_REFURBISHED':      '2020',
    'GOOD_REFURBISHED':           '2030',
    'SELLER_REFURBISHED':         '2500',
    'LIKE_NEW':                   '2750',
    'USED_EXCELLENT':             '3000',
    'USED_VERY_GOOD':             '4000',
    'USED_GOOD':                  '5000',
    'USED_ACCEPTABLE':            '6000',
    'FOR_PARTS_OR_NOT_WORKING':   '7000',
}


def _strip_html(raw: str) -> str:
    """Strip HTML tags and decode entities; collapse whitespace."""
    text = _HTML_TAG_RE.sub(' ', raw or '')
    text = html.unescape(text)
    return _WHITESPACE.sub(' ', text).strip()


def iter_inventory_api_items(
    cfg: Dict[str, Any],
    limit: int = 100,
) -> Generator[Dict[str, Any], None, None]:
    """
    Paginate through all eBay Inventory API items.
    Yields raw inventory-item dicts from the eBay response.
    """
    offset = 0
    total: Optional[int] = None
    while True:
        resp = ebay_get(cfg, f'/sell/inventory/v1/inventory_item?limit={limit}&offset={offset}')
        items = resp.get('inventoryItems', [])
        if total is None:
            total = resp.get('total', 0)
            log.info('ebay_pull: Inventory API reports %d items total', total)
        if not items:
            break
        yield from items
        offset += limit
        if offset >= (total or 0):
            break


def fetch_offer_for_sku(cfg: Dict[str, Any], sku: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the current eBay offer for a single SKU.
    Returns the first offer dict or None.
    """
    try:
        resp = ebay_get(cfg, f'/sell/inventory/v1/offer?sku={sku}')
        offers = resp.get('offers', [])
        return offers[0] if offers else None
    except Exception as exc:
        log.debug('ebay_pull: offer fetch failed for %s: %s', sku, exc)
        return None


def apply_ebay_live(
    item: Dict[str, Any],
    *,
    inventory_item: Optional[Dict[str, Any]] = None,
    offer: Optional[Dict[str, Any]] = None,
    synced_at: str,
) -> bool:
    """
    Merge Inventory API data into item['ebay_live']. Returns True if anything changed.
    """
    ebay_live = item.get('ebay_live') or {}
    changed = False

    if inventory_item is not None:
        ebay_live['inventory_item'] = inventory_item
        changed = True

    if offer is not None:
        ebay_live['offer'] = offer
        changed = True

    if changed:
        ebay_live['pulled_at'] = synced_at
        item['ebay_live'] = ebay_live

    return changed


def backfill_draft_from_live(item: Dict[str, Any], cfg: Dict[str, Any]) -> bool:
    """
    Populate item['draft_listing'] from item['ebay_live'] data.

    Rules:
    - Only creates draft if ebay_live is present.
    - If draft_listing already exists and source != 'ebay_live', does NOT overwrite
      (operator may have manually edited it).
    - Returns True if draft_listing was written.
    """
    live = item.get('ebay_live') or {}
    if not live:
        return False

    existing_draft = item.get('draft_listing') or {}
    if existing_draft and existing_draft.get('source') != 'ebay_live':
        return False

    inv    = live.get('inventory_item') or {}
    offer  = live.get('offer') or {}
    product = inv.get('product') or {}

    title           = product.get('title', '')
    aspects_raw     = product.get('aspects', {})
    image_urls      = product.get('imageUrls', [])
    upc             = product.get('upc', [])
    condition_enum  = inv.get('condition', '')
    cond_desc       = inv.get('conditionDescription', '')
    condition_id    = _ENUM_TO_CONDITION_ID.get(condition_enum, '')

    listing_desc_html = offer.get('listingDescription', '')
    listing_desc_text = _strip_html(listing_desc_html)
    price_info      = offer.get('pricingSummary', {}).get('price', {})
    price           = price_info.get('value', '')
    category_id     = offer.get('categoryId', '')
    offer_id        = offer.get('offerId', '')
    policies        = offer.get('listingPolicies', {})
    fulfillment_id  = policies.get('fulfillmentPolicyId', '')
    payment_id      = policies.get('paymentPolicyId', '')
    return_id       = policies.get('returnPolicyId', '')
    store_cats      = offer.get('storeCategoryNames', [])
    qty_avail       = (inv.get('availability', {})
                          .get('shipToLocationAvailability', {})
                          .get('quantity', 1))
    listing_info    = offer.get('listing', {})
    listing_id      = listing_info.get('listingId', '')

    # item_specifics: flatten aspects {name: [val1, val2]} → {name: 'val1'}
    item_specifics: Dict[str, str] = {
        k: v[0] if v else '' for k, v in aspects_raw.items()
    }

    draft: Dict[str, Any] = {
        'source':           'ebay_live',
        'title':            title,
        'category_id':      category_id,
        'category_name':    item.get('ebay_category_name', ''),
        'condition':        condition_enum,
        'condition_id':     condition_id,
        'condition_description': cond_desc,
        'format':           'FixedPrice',
        'quantity':         qty_avail,
        'price':            price,
        'item_specifics':   item_specifics,
        'description':      listing_desc_text,
        'listing_description': listing_desc_html,
        'image_urls':       image_urls,
        'upc':              upc,
        'fulfillment_policy_id': fulfillment_id,
        'payment_policy_id':     payment_id,
        'return_policy_id':      return_id,
        'store_category_names':  store_cats,
    }
    if offer_id:
        draft['offer_id'] = offer_id
    if listing_id:
        draft['listing_id'] = listing_id

    # Preserve aspect counts if they exist in current draft
    for k in ('aspects_required_total', 'aspects_required_filled',
              'aspects_recommended_total', 'aspects_recommended_filled'):
        if existing_draft.get(k) is not None:
            draft[k] = existing_draft[k]

    item['draft_listing'] = draft
    return True


def sync_inventory_api(
    cfg: Dict[str, Any],
    itemdata_root: Path,
    synced_at: str,
    *,
    dry_run: bool = False,
    sku_filter: Optional[Set[str]] = None,
    skip_offers: bool = False,
    skip_draft: bool = False,
    rate_limit_sleep: float = 0.05,
) -> Dict[str, Any]:
    """
    Full Inventory API mirror: pull every inventory item + its offer,
    write to ebay_live in item JSON, then backfill draft_listing.

    skip_offers: only pull inventory items (much faster; no price/description)
    skip_draft:  don't backfill draft_listing
    rate_limit_sleep: seconds to sleep between offer API calls (default 50ms)
    """
    stats: Dict[str, Any] = {
        'inventory_fetched': 0,
        'matched': 0,
        'unmatched': 0,
        'offers_fetched': 0,
        'offer_errors': 0,
        'drafts_written': 0,
        'errors': 0,
    }

    for inv_item in iter_inventory_api_items(cfg):
        sku = inv_item.get('sku', '')
        if not sku:
            continue

        stats['inventory_fetched'] += 1

        if sku_filter is not None and sku not in sku_filter:
            continue

        jf = config.sku_json(cfg, sku)
        if not jf.exists():
            log.debug('ebay_pull: no local item for sku=%s (Inventory API)', sku)
            stats['unmatched'] += 1
            continue

        stats['matched'] += 1

        if dry_run:
            log.info('[dry-run] would sync inventory_item for %s', sku)
            continue

        try:
            item = json.loads(jf.read_text(encoding='utf-8'))
        except Exception as exc:
            log.error('ebay_pull: JSON parse error for %s: %s', sku, exc)
            stats['errors'] += 1
            continue

        offer: Optional[Dict[str, Any]] = None
        if not skip_offers:
            offer = fetch_offer_for_sku(cfg, sku)
            if offer is not None:
                stats['offers_fetched'] += 1
            else:
                stats['offer_errors'] += 1
            if rate_limit_sleep > 0:
                time.sleep(rate_limit_sleep)

        apply_ebay_live(item, inventory_item=inv_item, offer=offer, synced_at=synced_at)

        if not skip_draft:
            if backfill_draft_from_live(item, cfg):
                stats['drafts_written'] += 1

        # Also keep ebay_listing in sync with what we now know
        if offer:
            listing_info = offer.get('listing', {})
            if listing_info.get('listingId'):
                ebay_listing = item.get('ebay_listing') or {}
                ebay_listing['listing_id']    = listing_info['listingId']
                ebay_listing['listing_url']   = (
                    f'https://www.ebay.com/itm/{listing_info["listingId"]}'
                )
                ebay_listing['listing_status'] = listing_info.get('listingStatus', '')
                ebay_listing['offer_id']       = offer.get('offerId', '')
                if offer.get('status') == 'PUBLISHED':
                    ebay_listing['status'] = 'Active'
                elif offer.get('status') == 'UNPUBLISHED':
                    ebay_listing.setdefault('status', 'Inactive')
                ebay_listing['api']       = 'inventory'
                ebay_listing['synced_at'] = synced_at
                item['ebay_listing'] = ebay_listing

        atomic_write_json(jf, item, pretty=cfg.get('pretty', True))

        if stats['inventory_fetched'] % 500 == 0:
            log.info('ebay_pull: progress %d inventory items processed, %d matched, %d drafts',
                     stats['inventory_fetched'], stats['matched'], stats['drafts_written'])

    log.info('ebay_pull: inventory sync complete — %s', stats)
    tgw_logging.log_event('ebay_inventory_sync_complete', **{
        k: v for k, v in stats.items() if isinstance(v, (int, float))
    })
    return stats
