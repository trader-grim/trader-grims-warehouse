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
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item

log = logging.getLogger(__name__)

SOLD_ORDERS_WINDOW_DAYS    = 90    # GetOrders API max per call
# GetOrders' real constraint is a rolling one: CreateTimeFrom can never be
# more than 90 days before *now*, no matter the window width (audit#1143
# #1153 -- a prior SOLD_INITIAL_LOOKBACK_DAYS=365 constant fed a scan_from
# far outside that boundary straight into the first 90-day chunk, so the
# very first call failed with "Invalid dates in CreateTimeFrom -- orders
# older than 90 days cannot be retrieved"; chunking the window narrower
# doesn't help since the START date was already too old — a real backfill
# beyond this ceiling is not achievable via GetOrders at all, so a separate
# "look back 365 days" constant was never anything but misleading. 89, not
# 90, for a one-day safety margin. code-review follow-up (2026-07-10):
# collapsed the two contradictory constants into this one source of truth.
_MAX_ORDER_LOOKBACK_DAYS = 89

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
    # PP-FENCE-001 gap: needs upsert/overwrite semantics not yet in fence;
    # migrate to fence.create_or_overwrite once that endpoint exists.
    from tgw.items import atomic_write_json as _atomic_write_json
    _atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
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
    Decrement inventory quantity by the sold quantity.
    Marks status=sold + ebay_listing.status=Sold only when remaining qty reaches 0.
    Idempotent — returns False if already sold or order already recorded.
    """
    item = json.loads(json_path.read_text(encoding='utf-8'))
    if item.get('status') == 'sold':
        return False
    # Idempotency for multi-qty: skip if this order was already recorded
    if item.get('ebay_sale', {}).get('order_id') == order_id:
        return False
    sku = json_path.parent.name

    current_qty = int(item.get('draft_listing', {}).get('quantity') or 1)
    remaining = max(0, current_qty - quantity)

    if dry_run:
        action = 'sold out' if remaining == 0 else f'qty {current_qty} → {remaining}'
        log.info('[dry-run] %s sold order=%s price=$%s qty_sold=%d (%s)',
                 sku, order_id, sale_price, quantity, action)
        return True

    ebay_sale = {
        'order_id':   order_id,
        'buyer':      buyer,
        'sale_price': sale_price,
        'quantity':   quantity,
        'sale_date':  sale_date,
        'synced_at':  synced_at,
    }

    if remaining == 0:
        fence_ebay_write(cfg, sku, ebay_listing={'status': 'Sold'})
        fence_patch_item(cfg, sku, {
            'status': 'sold',
            'ebay_sale': ebay_sale,
            'draft_listing': {'quantity': 0},
        })
        log.info('ebay_pull: sold out %s order=%s price=$%s', sku, order_id, sale_price)
        tgw_logging.log_event('ebay_item_sold', sku=sku,
                              order_id=order_id, sale_price=sale_price, sold_out=True)
    else:
        fence_patch_item(cfg, sku, {
            'ebay_sale': ebay_sale,
            'draft_listing': {'quantity': remaining},
        })
        log.info('ebay_pull: partial sale %s order=%s price=$%s qty %d→%d',
                 sku, order_id, sale_price, current_qty, remaining)
        tgw_logging.log_event('ebay_item_sold', sku=sku,
                              order_id=order_id, sale_price=sale_price, sold_out=False,
                              remaining_qty=remaining)
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


_MOTORS_MARKETPLACE = 'EBAY_MOTORS'


def check_legacy_duplicate_listing(cfg: Dict[str, Any], sku: str,
                                   local_listing_id: str) -> Dict[str, Any]:
    """
    PP-PHOTOSYNC-001 P10 (session 43) — verify a legacy-flagged SKU is NOT
    actually two live listings on eBay (the old classic Item# plus a separate
    Inventory-API listing) before ever marking it legacy_listing_resolved.

    Dave, s43: a month of gaps in our own inventory tooling meant occasionally
    using Seller Hub directly — "a known consequence of my actions... it
    could happen again and needs an auto repair path." This is that path:
    GET the live Inventory API offer(s) for the SKU and compare listing IDs
    against the locally-recorded (legacy) listing_id. Same ID, one offer =
    one listing, dual-manageable via both APIs (safe to resolve). A mismatch,
    no published offer, or MULTIPLE published offers across marketplaces
    means a genuine duplicate-listing risk and must NEVER be auto-resolved.

    eBay Motors extension (same session, live-found): a real rejection —
    "Best Offer is not permitted with a SKU selling on multiple eBay
    marketplaces" — surfaced that this SKU's offer carries
    marketplaceId=EBAY_MOTORS. PP-EBAY-MOTORS-001 (urgent, unscoped) tracks
    the larger gap (no marketplaceId stored anywhere locally, Trading API
    hardcoded to SiteID=0/EBAY_US); this function is the immediate,
    marketplace-AWARE piece of it — it surfaces marketplace_id and flags
    cross-marketplace duplication explicitly, rather than only checking a
    single listingId match.

    Returns {'ok': True, 'duplicate': bool, 'match': bool,
    'inventory_listing_id': str|None, 'inventory_status': str|None,
    'marketplace_id': str|None, 'is_ebay_motors': bool,
    'other_marketplaces': list[str]} — 'ok': False on a fetch error (treat
    as unresolved, do not proceed).
    """
    try:
        resp = ebay_get(cfg, '/sell/inventory/v1/offer', params={'sku': sku})
    except Exception as exc:
        return {'ok': False, 'error': str(exc)[:300]}

    offers = resp.get('offers') or []
    published = [o for o in offers if o.get('status') == 'PUBLISHED']
    if not published:
        return {'ok': True, 'duplicate': True, 'match': False,
                'inventory_listing_id': None, 'inventory_status': None,
                'marketplace_id': None, 'is_ebay_motors': False,
                'other_marketplaces': [],
                'reason': 'no published Inventory API offer found for this SKU'}

    # Multiple published offers for the same SKU = live on more than one
    # marketplace simultaneously. That IS the duplicate-listing risk, full
    # stop — never treat as safe even if one of them matches local_listing_id.
    marketplaces = [str(o.get('marketplaceId') or '') for o in published]
    if len(published) > 1:
        return {'ok': True, 'duplicate': True, 'match': False,
                'inventory_listing_id': None, 'inventory_status': None,
                'marketplace_id': marketplaces[0] if marketplaces else None,
                'is_ebay_motors': _MOTORS_MARKETPLACE in marketplaces,
                'other_marketplaces': marketplaces,
                'reason': f'SKU has {len(published)} published offers across '
                          f'marketplaces {marketplaces} — cross-marketplace duplicate'}

    offer = published[0]
    inv_listing = offer.get('listing') or {}
    inv_listing_id = str(inv_listing.get('listingId') or '')
    inv_status = inv_listing.get('listingStatus')
    marketplace_id = str(offer.get('marketplaceId') or '') or None
    match = bool(inv_listing_id) and inv_listing_id == str(local_listing_id)

    return {
        'ok': True,
        'duplicate': not match,
        'match': match,
        'inventory_listing_id': inv_listing_id,
        'inventory_status': inv_status,
        'marketplace_id': marketplace_id,
        'is_ebay_motors': marketplace_id == _MOTORS_MARKETPLACE,
        'other_marketplaces': [],
    }


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
    fence_ebay_write(cfg, sku, ebay_listing=new_listing)
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

    # GetOrders can never see further back than _MAX_ORDER_LOOKBACK_DAYS from
    # now, no matter what triggered this sync (first-ever run or a
    # long-stale incremental resume) — this is the ceiling on both paths,
    # not a fallback clamp on top of a separate "ideal" lookback.
    earliest_allowed = now - timedelta(days=_MAX_ORDER_LOOKBACK_DAYS)

    if state_path.exists():
        state = json.loads(state_path.read_text())
        scan_from = datetime.fromisoformat(state['last_synced_at']) - timedelta(hours=2)
        if scan_from < earliest_allowed:
            log.info('ebay_pull: scan_from %s predates GetOrders\' %d-day limit — '
                     'clamped to %s', scan_from.date(), _MAX_ORDER_LOOKBACK_DAYS,
                     earliest_allowed.date())
            scan_from = earliest_allowed
    else:
        scan_from = earliest_allowed
        log.info('ebay_pull: first sold sync — looking back %d days '
                 '(GetOrders cannot see further back than this)',
                 _MAX_ORDER_LOOKBACK_DAYS)

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
        'imageUrls':        image_urls,
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


def backfill_canonical_from_live(item: Dict[str, Any]) -> Dict[str, str]:
    """
    Promote eBay live data into top-level canonical inventory fields.

    Only fills fields that are currently empty/missing. Never overwrites
    existing operator-set values. Returns a dict of field → new_value
    for every field that was changed.

    Sources in priority order:
      1. ebay_live.inventory_item (freshest eBay data)
      2. draft_listing (already normalised from ebay_live)
      3. Legacy top-level fields with different names (e.g. weight → weight_oz)
    """
    changed: Dict[str, str] = {}

    live   = item.get('ebay_live') or {}
    inv    = live.get('inventory_item') or {}
    product = inv.get('product') or {}
    aspects = product.get('aspects') or {}
    dl     = item.get('draft_listing') or {}

    def _set(field: str, value: Any) -> None:
        """Set field only if currently empty."""
        if value and not item.get(field):
            item[field] = value
            changed[field] = value

    def _overwrite(field: str, value: Any) -> None:
        """Set field unconditionally (for cases where current value is known-bad)."""
        if value and item.get(field) != value:
            item[field] = value
            changed[field] = value

    # Title — use eBay title if our title is missing or just repeats the SKU
    _title = product.get('title') or dl.get('title') or ''
    if _title and (not item.get('title') or item.get('title') == item.get('sku')):
        _overwrite('title', _title)

    # Description — promote the real eBay description.
    # Overwrite if empty OR if it's just a copy of the title (placeholder).
    _desc = dl.get('description') or ''
    _current_desc = str(item.get('description') or '').strip()
    _current_title = str(item.get('title') or _title).strip()
    if _desc and (not _current_desc or _current_desc == _current_title):
        _overwrite('description', _desc)

    # Condition — prefer human enum string over legacy raw conditionId integer.
    _cond_live = inv.get('condition', '')  # e.g. USED_EXCELLENT
    if _cond_live:
        _current_cond = str(item.get('condition') or '').strip()
        if not _current_cond or _current_cond.isdigit():
            _overwrite('condition', _cond_live)

    # Brand from eBay aspects
    _brand = (aspects.get('Brand') or aspects.get('brand') or [''])[0]
    _set('brand', _brand)

    # Model from eBay aspects
    _model = (aspects.get('Model') or aspects.get('model') or [''])[0]
    _set('model', _model)

    # MPN / model number
    _mpn = (aspects.get('MPN') or aspects.get('mpn') or [''])[0]
    _set('model_number', _mpn)

    # Country of manufacture
    _coo = (aspects.get('Country of Manufacture') or
            aspects.get('Country of Origin') or [''])[0]
    _set('country_of_manufacture', _coo)

    # UPC from product or draft
    _upc = product.get('upc') or dl.get('upc')
    if isinstance(_upc, list):
        _upc = _upc[0] if _upc else ''
    _set('upc', _upc)

    # weight_oz — promote from legacy 'weight' field (Magento export used oz)
    if not item.get('weight_oz') and item.get('weight'):
        try:
            _set('weight_oz', float(item['weight']))
        except (TypeError, ValueError):
            pass

    return changed


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

        live_changed = apply_ebay_live(item, inventory_item=inv_item, offer=offer, synced_at=synced_at)

        draft_changed = False
        if not skip_draft:
            draft_changed = backfill_draft_from_live(item, cfg)
            if draft_changed:
                stats['drafts_written'] += 1

        # Keep ebay_listing in sync with what we now know from the offer
        ebay_listing_update = None
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
                ebay_listing_update = ebay_listing

        # Write changed blocks through fence
        if live_changed or ebay_listing_update is not None:
            fence_ebay_write(cfg, sku,
                             ebay_live=item.get('ebay_live') if live_changed else None,
                             ebay_listing=ebay_listing_update)
        if draft_changed:
            fence_patch_item(cfg, sku, {'draft_listing': item.get('draft_listing')})

        if stats['inventory_fetched'] % 500 == 0:
            log.info('ebay_pull: progress %d inventory items processed, %d matched, %d drafts',
                     stats['inventory_fetched'], stats['matched'], stats['drafts_written'])

    log.info('ebay_pull: inventory sync complete — %s', stats)
    tgw_logging.log_event('ebay_inventory_sync_complete', **{
        k: v for k, v in stats.items() if isinstance(v, (int, float))
    })
    return stats
