"""
tgw.api — CLI entry point.

This module is intentionally thin.  It parses arguments, calls the
appropriate function from tgw.items, tgw.catalog, or tgw.resolver,
and prints the result as JSON.

No business logic lives here.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .catalog import (
    build_all_catalogs,
    build_full_catalog,
    build_full_catalog_csv,
    build_location_tree,
    build_search_catalog,
    build_search_catalog_csv,
    load_full_catalog,
    load_search_catalog,
)
from .config import DEFAULT_CONFIG, load_config
from .health import check_all
from .items import (
    catlocmvall,
    get_item,
    locationupdate,
    titleupdate,
    update_item,
    update_where,
    verifiedupdate,
)
from .resolver import resolve, sku_date_str
from .sqlite_catalog import build_sqlite_catalog
from .thumbnail import build_thumbnail_cache

# ---------------------------------------------------------------------------
# list_items — lives here because it bridges catalog and resolver
# ---------------------------------------------------------------------------

def list_items(cfg: Dict[str, Any], search: str = '', location: str = '',
               status: str = '', limit: Optional[int] = None,
               date_from: str = '', date_to: str = '') -> Dict[str, Any]:
    """List items matching filters.  Always returns {'ok': True, 'items': [...]}."""
    # Load from best available source
    if cfg['search_catalog_path'].exists():
        rows = load_search_catalog(cfg)
    elif cfg['full_catalog_path'].exists():
        rows = load_full_catalog(cfg)
    else:
        from .resolver import find_item_jsons, load_item_doc
        rows = [load_item_doc(p) for p in find_item_jsons(cfg)]

    out: List[Dict[str, Any]] = []
    for item in rows:
        if search and search.lower() not in '\n'.join(
            f'{k}={v}' for k, v in item.items()
            if isinstance(v, (str, int, float, bool)) or v is None
        ).lower():
            continue
        if location and str(item.get('location', '')) != location:
            continue
        if status and str(item.get('#STATUS', item.get('status', ''))) != status:
            continue
        if date_from or date_to:
            sku = str(item.get('sku', ''))
            d = sku_date_str(sku)
            if d is None:
                continue
            if date_from and d < date_from:
                continue
            if date_to and d > date_to:
                continue
        out.append(item)
        if limit not in (None, 0) and len(out) >= int(limit):
            break
    return {'ok': True, 'count': len(out), 'items': out}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='tgw',
        description='TGW inventory management API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--config', default=str(DEFAULT_CONFIG),
                        help='Path to config JSON (default: %(default)s)')
    sub = parser.add_subparsers(dest='op', required=True)

    # --- read ---
    p = sub.add_parser('get', help='get full item record by SKU')
    p.add_argument('sku')

    p = sub.add_parser('list', help='list items with optional filters')
    p.add_argument('--search',    default='')
    p.add_argument('--location',  default='')
    p.add_argument('--status',    default='')
    p.add_argument('--date-from', default='', dest='date_from',
                   help='YYYYMMDD lower bound on SKU timestamp')
    p.add_argument('--date-to',   default='', dest='date_to',
                   help='YYYYMMDD upper bound on SKU timestamp')
    p.add_argument('--limit',     type=int, default=None)

    p = sub.add_parser('resolve', help='resolve identifiers to a set of SKUs')
    p.add_argument('--sku',          default=None)
    p.add_argument('--location',     default=None)
    p.add_argument('--status',       default=None)
    p.add_argument('--date-from',    default=None, dest='date_from')
    p.add_argument('--date-to',      default=None, dest='date_to')
    p.add_argument('--ebay-item-id', default=None, dest='ebay_item_id')
    p.add_argument('--upc',          default=None)
    p.add_argument('--search',       default=None)

    # --- write ---
    p = sub.add_parser('update', help='update one field on one item')
    p.add_argument('sku')
    p.add_argument('field')
    p.add_argument('value')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('update-where',
                       help='bulk-update items matching selectors')
    p.add_argument('field')
    p.add_argument('value')
    p.add_argument('--location',   default=None)
    p.add_argument('--status',     default=None)
    p.add_argument('--date-from',  default=None, dest='date_from')
    p.add_argument('--date-to',    default=None, dest='date_to')
    p.add_argument('--search',     default=None)
    p.add_argument('--check-only', action='store_true')

    # --- tgw.source replacements ---
    p = sub.add_parser('titleupdate', help='update title field on one item')
    p.add_argument('sku')
    p.add_argument('value')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('locationupdate',
                       help='update location and rebuild tree link')
    p.add_argument('sku')
    p.add_argument('location')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('verifiedupdate', help='update VERIFIED field')
    p.add_argument('sku')
    p.add_argument('value')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('catlocmvall',
                       help='move all items from one location to another')
    p.add_argument('from_location')
    p.add_argument('to_location')
    p.add_argument('--check-only', action='store_true')

    # --- catalog builds ---
    p = sub.add_parser('build-full', help='build full catalog JSON from ItemData')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-search', help='build search catalog JSON')
    p.add_argument('--source',
                   choices=['auto', 'full_catalog', 'itemdata'], default='auto')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-locations', help='build location symlink tree')
    p.add_argument('--source',
                   choices=['auto', 'search_catalog', 'full_catalog', 'itemdata'],
                   default='auto')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-full-csv', help='build full catalog CSV')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-search-csv', help='build search catalog CSV')
    p.add_argument('--source',
                   choices=['auto', 'full_catalog', 'itemdata'], default='auto')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-sqlite',
                       help='build SQLite catalog from ItemData')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-thumbnails',
                       help='generate per-SKU thumbnail cache (requires Pillow)')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('build-all',
                       help='build full catalog, search catalog, location tree, and SQLite catalog')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('ensure-catalog',
                       help='build search catalog only if missing')
    p.add_argument('--check-only', action='store_true')

    p = sub.add_parser('health', help='run platform health checks')
    p.add_argument('--no-ollama', action='store_true',
                   help='skip Ollama check')
    p.add_argument('--no-ebay', action='store_true',
                   help='skip eBay token check')

    p = sub.add_parser('lookup', help='run product enrichment lookup for one item (PP-LOOKUP-001)')
    p.add_argument('sku', help='SKU to look up')
    p.add_argument('--force', action='store_true',
                   help='ignore cache and re-fetch even if fresh result exists')
    p.add_argument('--save', action='store_true',
                   help='write result back to item JSON')

    p = sub.add_parser('quality',
                       help='show listing quality score for one or more items (PP-QUALITY-001)')
    p.add_argument('skus', nargs='+', help='SKU(s) to score')
    p.add_argument('--save', action='store_true',
                   help='write updated quality score back to draft_listing in item JSON')

    p = sub.add_parser('suggest', help='append a suggestion for the next planning session')
    p.add_argument('text', nargs='+', help='suggestion text')

    p = sub.add_parser('hint', help='set an ai_hint on an item and re-queue identification')
    p.add_argument('sku', help='SKU to hint')
    p.add_argument('text', nargs='+', help='hint text (e.g. "thimbles" or "mini liquor bottles")')
    p.add_argument('--force', action='store_true',
                   help='re-identify even if already ai_identified')

    p = sub.add_parser('requeue',
                       help='bulk-enqueue ai_identify for items matching a filter')
    p.add_argument('--no-title', action='store_true',
                   help='items with photos but title still equals SKU (truly unprocessed)')
    p.add_argument('--unidentified', action='store_true',
                   help='all items where ai_identified is not True')
    p.add_argument('--hint-set', action='store_true',
                   help='items with ai_hint set but not yet ai_identified')
    p.add_argument('--no-draft', action='store_true',
                   help='items that are ai_identified but have no draft_listing')
    p.add_argument('--no-price', action='store_true',
                   help='items with draft_listing but no price set')
    p.add_argument('--catalog-only', action='store_true',
                   help='identify for catalog only — skip ebay_draft cascade')
    p.add_argument('--limit', type=int, default=100,
                   help='max items to queue (default: 100; use 0 for unlimited)')
    p.add_argument('--run', action='store_true',
                   help='actually queue jobs (default is dry-run)')

    p = sub.add_parser('resolve-legacy',
                       help='mark item(s) as having legacy eBay listing cleared, '
                            'enabling ebay_stage to proceed')
    p.add_argument('skus', nargs='+', help='one or more SKUs to resolve')
    p.add_argument('--no-stage', action='store_true',
                   help='mark resolved but do not enqueue ebay_stage')

    p = sub.add_parser('staged',
                       help='list items staged as UNPUBLISHED eBay offers, awaiting review')
    p.add_argument('--json', action='store_true', dest='as_json',
                   help='output as JSON instead of a table')

    p = sub.add_parser('publish',
                       help='approve and publish one or more staged items')
    p.add_argument('skus', nargs='+', help='one or more SKUs to publish')
    p.add_argument('--dry-run', action='store_true',
                   help='show what would be enqueued without actually doing it')

    p = sub.add_parser('setup-ebay-hooks',
                       help='register eBay push notification delivery URL (run once)')
    p.add_argument('--url', required=True,
                   help='public HTTPS URL eBay will POST to, e.g. https://hooks.example.com/webhooks/ebay/notification')
    p.add_argument('--check', action='store_true',
                   help='print currently registered URL without making changes')

    p = sub.add_parser('serve', help='start tgw-http FastAPI service on port 7373')
    p.add_argument('--host', default='127.0.0.1', help='bind host (default: 127.0.0.1)')
    p.add_argument('--port', type=int, default=7373, help='bind port (default: 7373)')
    p.add_argument('--reload', action='store_true', help='enable auto-reload (dev only)')

    p = sub.add_parser('ebay-sweep',
                       help='generate physical inventory checklist for ambiguous-status items')
    p.add_argument('--groups', default='A',
                   help='comma-separated groups to include: A=active/unclear, '
                        'B=out-of-stock/no-listing, C=no-status/no-listing (default: A)')
    p.add_argument('--location', default=None,
                   help='filter to a specific shelf location')
    p.add_argument('--limit', type=int, default=0,
                   help='max items per group (0 = unlimited)')
    p.add_argument('--output', default=None,
                   help='write markdown checklist to this file instead of stdout')

    p = sub.add_parser('import-sold-csv',
                       help='import eBay Seller Hub sold-orders CSV → mark items sold')
    p.add_argument('file', help='path to eBay sold-orders CSV file')
    p.add_argument('--dry-run', action='store_true',
                   help='show what would be marked without writing')
    p.add_argument('--show-columns', action='store_true',
                   help='print CSV column names and exit (for format inspection)')

    p = sub.add_parser('ebay-pull',
                       help='on-demand eBay data pull: active listings + sold orders → ItemData')
    p.add_argument('--no-active', action='store_true',
                   help='skip active listing sync')
    p.add_argument('--no-sold', action='store_true',
                   help='skip sold orders sync')
    p.add_argument('--dry-run', action='store_true',
                   help='show what would change without writing')

    p = sub.add_parser('sku-migrate', help='SKU normalization (PP-ADD-005)')
    p.add_argument('--check-collisions', action='store_true',
                   help='run collision check only — no changes')
    p.add_argument('--class', dest='classes', default='A,B,C,D,E,F',
                   help='comma-separated class list to process (default: all)')
    p.add_argument('--dry-run', action='store_true', default=True,
                   help='show planned renames without making changes (default)')
    p.add_argument('--run', action='store_true',
                   help='actually execute renames (overrides --dry-run)')
    p.add_argument('--include-live-ebay', action='store_true',
                   help='include items with live eBay listings (default: skip)')
    p.add_argument('--limit', type=int, default=0,
                   help='max items to process (0 = unlimited)')
    p.add_argument('--manifest', default='',
                   help='path for rollback manifest JSON (default: var/log/sku-migrate-<ts>.json)')

    return parser


def cmd_hint(cfg: Dict[str, Any], sku: str, hint: str, force: bool = False) -> Dict[str, Any]:
    """Write ai_hint to an item and enqueue re-identification."""
    from tgw.config import sku_json
    from tgw.items import atomic_write_json
    from tgw.queue import state_machine

    json_path = sku_json(cfg, sku)
    if not json_path.exists():
        return {'ok': False, 'error': f'item not found: {sku}'}

    item = json.loads(json_path.read_text(encoding='utf-8'))
    already = bool(item.get('ai_identified'))

    item['ai_hint'] = hint
    if force or not already:
        item['ai_reidentify'] = True

    atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))

    # Enqueue ai_identify — dedupe key means a pending job won't double-enqueue
    import psycopg2.errors
    try:
        state_machine.init(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))
        jid = state_machine.enqueue_job(
            queue_name='ai_identify',
            payload={'sku': sku},
            dedupe_key=f'ai_identify:{sku}',
            max_attempts=3,
        )
        queued = True
    except psycopg2.errors.UniqueViolation:
        jid = None
        queued = False

    return {
        'ok':     True,
        'sku':    sku,
        'hint':   hint,
        'force':  force or not already,
        'queued': queued,
        'job_id': jid,
    }


def cmd_requeue(cfg: Dict[str, Any], *,
                no_title: bool = False,
                unidentified: bool = False,
                hint_set: bool = False,
                no_draft: bool = False,
                no_price: bool = False,
                catalog_only: bool = False,
                limit: int = 100,
                dry_run: bool = True) -> Dict[str, Any]:
    """
    Bulk-enqueue ai_identify (or ebay_draft/ebay_price) for items matching filters.
    Default is dry-run — pass dry_run=False to actually queue.
    At least one filter must be specified.
    """
    import psycopg2.errors

    from tgw.queue import state_machine

    _IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG'}

    if not any([no_title, unidentified, hint_set, no_draft, no_price]):
        return {'ok': False, 'error': 'specify at least one filter flag'}

    if not dry_run:
        state_machine.init(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))

    matched, queued, skipped_pending, skipped_no_photos = [], [], [], []
    root: Path = cfg['itemdata_root']

    for sku_dir in root.iterdir():
        if limit and len(queued) >= limit:
            break
        j = sku_dir / f'{sku_dir.name}.json'
        if not j.exists():
            continue
        d = json.loads(j.read_text(encoding='utf-8'))
        sku   = sku_dir.name
        title = str(d.get('title', '')).strip()
        ai_id = d.get('ai_identified')
        draft = d.get('draft_listing') or {}
        price = draft.get('price') or d.get('ebay_offer', {}).get('price')

        # Determine which queue this item needs
        target_queue = 'ai_identify'
        payload: Dict[str, Any] = {'sku': sku}
        if catalog_only:
            payload['catalog_only'] = True

        if no_title:
            if ai_id or (title and title != sku):
                continue
        if unidentified:
            if ai_id:
                continue
        if hint_set:
            if not d.get('ai_hint') or ai_id:
                continue
        if no_draft:
            if not ai_id or draft:
                continue
            target_queue = 'ebay_draft'
            payload = {'sku': sku}
        if no_price:
            if not draft or price is not None:
                continue
            target_queue = 'ebay_price'
            payload = {'sku': sku}

        # ai_identify requires at least one photo
        if target_queue == 'ai_identify':
            has_photos = any(
                p.suffix in _IMAGE_EXTS
                for p in sku_dir.iterdir() if p.is_file()
            )
            if not has_photos:
                skipped_no_photos.append(sku)
                continue

        matched.append(sku)

        if not dry_run:
            dedupe_key = f'{target_queue}:{sku}'
            try:
                state_machine.enqueue_job(
                    queue_name=target_queue,
                    payload=payload,
                    dedupe_key=dedupe_key,
                    max_attempts=3,
                )
                queued.append(sku)
            except psycopg2.errors.UniqueViolation:
                skipped_pending.append(sku)

    return {
        'ok':               True,
        'dry_run':          dry_run,
        'catalog_only':     catalog_only,
        'matched':          len(matched),
        'queued':           len(queued) if not dry_run else 0,
        'skipped_pending':  len(skipped_pending),
        'skipped_no_photos': len(skipped_no_photos),
        'limit':            limit,
        'sample':           matched[:5],
    }


def cmd_resolve_legacy(cfg: Dict[str, Any], skus: List[str],
                       enqueue_stage: bool = True) -> Dict[str, Any]:
    """
    Mark one or more items as having their legacy eBay Trading API listing
    cleared, setting legacy_listing_resolved=True so ebay_stage can proceed.
    Optionally enqueues ebay_stage for each resolved item.
    """
    import psycopg2.errors

    from tgw.config import sku_json
    from tgw.items import atomic_write_json
    from tgw.queue import state_machine

    state_machine.init(cfg.get('postgres_dsn', 'dbname=state_machine user=tgw'))

    resolved, not_found, already_done, staged = [], [], [], []

    for sku in skus:
        json_path = sku_json(cfg, sku)
        if not json_path.exists():
            not_found.append(sku)
            continue

        item = json.loads(json_path.read_text(encoding='utf-8'))

        if item.get('legacy_listing_resolved'):
            already_done.append(sku)
        else:
            item['legacy_listing_resolved'] = True
            atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
            resolved.append(sku)

        # Only queue ebay_stage if the item has already been priced —
        # otherwise the normal pipeline will handle it after ai_identify/ebay_draft/ebay_price
        draft = item.get('draft_listing', {})
        pipeline_ready = (
            draft.get('price') is not None
            or item.get('ebay_offer', {}).get('price') is not None
        )
        if enqueue_stage and pipeline_ready and not item.get('ebay_offer', {}).get('offer_id'):
            try:
                state_machine.enqueue_job(
                    queue_name='ebay_stage',
                    payload={'sku': sku},
                    dedupe_key=f'ebay_stage:{sku}',
                    max_attempts=5,
                )
                staged.append(sku)
            except psycopg2.errors.UniqueViolation:
                pass

    return {
        'ok':          True,
        'resolved':    resolved,
        'already_done': already_done,
        'not_found':   not_found,
        'stage_queued': staged,
    }


def cmd_staged(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """List all items with UNPUBLISHED eBay offers awaiting operator review."""
    root: Path = cfg['itemdata_root']
    items = []
    for child in sorted(root.iterdir()):
        jf = child / f'{child.name}.json'
        if not jf.exists():
            continue
        try:
            doc = json.loads(jf.read_text(encoding='utf-8'))
        except Exception:
            continue
        offer = doc.get('ebay_offer', {})
        if offer.get('offer_id') and offer.get('status') == 'UNPUBLISHED':
            draft   = doc.get('draft_listing') or {}
            quality = draft.get('quality') or {}
            items.append({
                'sku':              child.name,
                'title':            doc.get('title', ''),
                'price':            offer.get('price'),
                'location':         doc.get('location', ''),
                'category':         doc.get('ebay_category_name', ''),
                'offer_id':         offer.get('offer_id'),
                'staged_at':        offer.get('staged_at', ''),
                'quality':          quality.get('score'),
                'quality_flags':    quality.get('flags', []),
                'price_confidence': draft.get('price_confidence'),
                'comp_count':       (offer.get('price_comps') or {}).get('count'),
            })
    # Sort ascending by quality score so worst items surface first
    items.sort(key=lambda x: (x['quality'] is None, x['quality'] or 0))
    return {'ok': True, 'count': len(items), 'items': items}


def cmd_publish(cfg: Dict[str, Any], skus: List[str],
                dry_run: bool = False) -> Dict[str, Any]:
    """Enqueue ebay_publish for each SKU that has an UNPUBLISHED offer."""
    import psycopg2.errors

    from tgw.queue import state_machine

    enqueued: List[str] = []
    skipped:  List[str] = []
    errors:   List[str] = []

    for sku in skus:
        jf = cfg['itemdata_root'] / sku / f'{sku}.json'
        if not jf.exists():
            errors.append(f'{sku}: item not found')
            continue
        try:
            doc = json.loads(jf.read_text(encoding='utf-8'))
        except Exception as exc:
            errors.append(f'{sku}: bad JSON — {exc}')
            continue

        offer = doc.get('ebay_offer', {})
        if not offer.get('offer_id'):
            errors.append(f'{sku}: no offer_id — run ebay_stage first')
            continue
        if offer.get('status') != 'UNPUBLISHED':
            skipped.append(f'{sku}: offer status is {offer.get("status")!r} — not UNPUBLISHED')
            continue

        if dry_run:
            enqueued.append(sku)
            continue

        try:
            state_machine.enqueue_job(
                queue_name='ebay_publish',
                payload={'sku': sku},
                dedupe_key=f'ebay_publish:{sku}',
                max_attempts=3,
            )
            enqueued.append(sku)
        except psycopg2.errors.UniqueViolation:
            skipped.append(f'{sku}: already queued')
        except Exception as exc:
            errors.append(f'{sku}: {exc}')

    return {
        'ok':       not errors,
        'dry_run':  dry_run,
        'enqueued': enqueued,
        'skipped':  skipped,
        'errors':   errors,
    }


def cmd_import_sold_csv(cfg: Dict[str, Any], csv_path: Path,
                        dry_run: bool = False,
                        show_columns: bool = False) -> Dict[str, Any]:
    """
    Import an eBay Seller Hub sold-orders CSV and mark matched items sold.

    Matches rows to item JSONs via Item number → ebay_listing.listing_id.
    Idempotent: items already marked sold are skipped.
    """
    import csv as _csv

    from .ebay.pull import build_listing_index, mark_item_sold

    # eBay Seller Hub column names vary slightly across exports; try each in order.
    _COL_LISTING_ID = ('Item number', 'Item Number', 'ItemID', 'Item ID')
    _COL_SALE_DATE  = ('Sale date', 'Sale Date', 'Purchase date', 'Purchase Date', 'Order date')
    _COL_SALE_PRICE = ('Sale price', 'Sale Price', 'Item price', 'Sold for', 'Unit price')
    _COL_BUYER      = ('Buyer username', 'Buyer Username', 'Buyer user ID', 'Buyer')
    _COL_ORDER_ID   = ('Order ID', 'Order number', 'Sales record number', 'Transaction ID')
    _COL_QUANTITY   = ('Quantity', 'Qty', 'Item quantity')

    def _pick(row: Dict[str, str], candidates: tuple) -> str:
        for c in candidates:
            if c in row:
                return row[c].strip()
        return ''

    if not csv_path.exists():
        return {'ok': False, 'error': f'file not found: {csv_path}'}

    with csv_path.open(encoding='utf-8-sig', newline='') as fh:
        reader = _csv.DictReader(fh)
        rows = list(reader)
        columns = reader.fieldnames or []

    if show_columns:
        return {'ok': True, 'columns': list(columns), 'row_count': len(rows)}

    if not rows:
        return {'ok': True, 'matched': 0, 'marked': 0, 'skipped': 0,
                'unmatched': 0, 'errors': 0, 'dry_run': dry_run}

    # Verify we can find the listing_id column
    sample = rows[0]
    if not any(c in sample for c in _COL_LISTING_ID):
        return {
            'ok': False,
            'error': f'Cannot find Item number column. Columns found: {list(columns)}. '
                     f'Use --show-columns to inspect the file.',
        }

    synced_at     = datetime.now(tz=timezone.utc).isoformat()
    itemdata_root = cfg['itemdata_root']
    listing_index = build_listing_index(itemdata_root)

    stats: Dict[str, Any] = {
        'rows': len(rows), 'matched': 0, 'marked': 0,
        'already_sold': 0, 'unmatched': 0, 'errors': 0,
    }
    unmatched_ids: List[str] = []

    for row in rows:
        listing_id = _pick(row, _COL_LISTING_ID)
        if not listing_id:
            continue

        json_path = listing_index.get(listing_id)
        if not json_path:
            stats['unmatched'] += 1
            unmatched_ids.append(listing_id)
            continue

        stats['matched'] += 1
        sale_price_raw = _pick(row, _COL_SALE_PRICE)
        try:
            sale_price = float(sale_price_raw.lstrip('$').replace(',', ''))
        except (ValueError, AttributeError):
            sale_price = sale_price_raw

        try:
            did_mark = mark_item_sold(
                json_path,
                order_id=_pick(row, _COL_ORDER_ID) or f'csv-import-{listing_id}',
                buyer=_pick(row, _COL_BUYER),
                sale_price=sale_price,
                quantity=int(_pick(row, _COL_QUANTITY) or '1'),
                sale_date=_pick(row, _COL_SALE_DATE),
                synced_at=synced_at,
                cfg=cfg,
                dry_run=dry_run,
            )
            if did_mark:
                stats['marked'] += 1
            else:
                stats['already_sold'] += 1
        except Exception as exc:
            stats['errors'] += 1
            print(f'  ERROR listing {listing_id}: {exc}')

    if unmatched_ids:
        print(f'  {len(unmatched_ids)} unmatched listing IDs (not in local ItemData):')
        for lid in unmatched_ids[:20]:
            print(f'    {lid}')
        if len(unmatched_ids) > 20:
            print(f'    ... and {len(unmatched_ids) - 20} more')

    return {'ok': True, 'dry_run': dry_run, **stats}


def cmd_ebay_sweep(cfg: Dict[str, Any], *,
                   groups: str = 'A',
                   location: Optional[str] = None,
                   limit: int = 0,
                   output: Optional[Path] = None) -> Dict[str, Any]:
    """
    Scan ItemData for ambiguous-status items and generate a physical inventory checklist.

    Groups:
      A — Active eBay listing, local status not confirmed (most urgent)
      B — "out of stock" legacy items with no eBay listing (likely sold, untracked)
      C — No status and no eBay listing (completely uncategorized)

    Output is a markdown checklist (stdout or --output file) for Obsidian review.
    """

    selected = {g.strip().upper() for g in groups.split(',')}

    _CLEAR_STATUS = {'sold', 'disposed', 'recalled', 'merged', 'discard',
                     'disposeddisposed', 'vero'}

    itemdata_root = cfg['itemdata_root']
    results: Dict[str, List[Dict[str, Any]]] = {'A': [], 'B': [], 'C': []}

    for json_path in itemdata_root.glob('*/*.json'):
        try:
            item = json.loads(json_path.read_text(encoding='utf-8'))
            if not isinstance(item, dict):
                continue
        except Exception:
            continue

        sku        = json_path.parent.name
        raw_status = str(item.get('status', '')).lower().strip()
        loc        = str(item.get('location', '')).strip()
        title      = str(item.get('title', '')).strip()
        ebay_lst   = item.get('ebay_listing') or {}
        ebay_status = str(ebay_lst.get('status', '')).lower().strip()
        listing_id  = ebay_lst.get('listing_id', '')
        listing_url = ebay_lst.get('listing_url', '')
        live_price  = ebay_lst.get('live_price') or item.get('ebay_offer', {}).get('price')

        if location and loc.lower() != location.lower():
            continue
        if raw_status in _CLEAR_STATUS:
            continue

        entry: Dict[str, Any] = {
            'sku': sku, 'title': title, 'location': loc,
            'status': raw_status or '(empty)',
            'ebay_status': ebay_status or '(none)',
            'listing_id': listing_id,
            'listing_url': listing_url,
            'price': live_price,
        }

        if 'A' in selected and ebay_status == 'active' and raw_status not in ('in stock',):
            results['A'].append(entry)
        elif 'B' in selected and raw_status == 'out of stock' and not listing_id:
            results['B'].append(entry)
        elif 'C' in selected and not raw_status and not listing_id:
            results['C'].append(entry)

    # Apply per-group limit
    if limit:
        for g in results:
            results[g] = results[g][:limit]

    total = sum(len(v) for v in results.values())
    ts    = datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')

    lines: List[str] = [
        f'# eBay Physical Inventory Sweep — {ts}',
        f'Groups: {groups}' + (f'  |  Location filter: {location}' if location else ''),
        f'Total items: {total}',
        '',
    ]

    _GROUP_DESC = {
        'A': ('Active eBay listing — local status unclear',
              'Check shelf. Present → `tgw update <SKU> status "in stock"` '
              '| Missing → likely sold; check eBay order history'),
        'B': ('Legacy "out of stock" — no eBay listing',
              'Check shelf. Present → `tgw update <SKU> status available` '
              '| Missing → `tgw update <SKU> status sold` (or use import-sold-csv)'),
        'C': ('No status, no eBay listing — completely uncategorized',
              'Assess: still have it? list it? already gone?'),
    }

    for g in ('A', 'B', 'C'):
        items = results.get(g, [])
        if not items:
            continue
        title_str, action_str = _GROUP_DESC[g]
        lines += [
            f'## Group {g} — {title_str} ({len(items)})',
            f'*{action_str}*',
            '',
            '| Done | SKU | Status | eBay | Loc | Price | Title |',
            '|------|-----|--------|------|-----|-------|-------|',
        ]
        for it in items:
            price_str = f'${it["price"]}' if it['price'] else ''
            url_str   = f'[{it["listing_id"]}]({it["listing_url"]})' if it['listing_id'] else ''
            title_col = it['title'][:45].replace('|', '/') if it['title'] else '—'
            lines.append(
                f'| [ ] | {it["sku"]} | {it["status"]} | {url_str or it["ebay_status"]} '
                f'| {it["location"] or "—"} | {price_str} | {title_col} |'
            )
        lines.append('')

    content = '\n'.join(lines)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding='utf-8')
        print(f'Sweep report written to {output}  ({total} items)')
    else:
        print(content)

    counts = {g: len(v) for g, v in results.items()}
    return {'ok': True, 'total': total, 'groups': counts,
            'output': str(output) if output else None}


def cmd_suggest(cfg: Dict[str, Any], text: str) -> Dict[str, Any]:
    suggestions_file = cfg['plan_vault_path'] / 'suggestions' / 'SUGGESTIONS.md'
    suggestions_file.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M')
    line = f'- [ ] {ts} :: {text}\n'
    with suggestions_file.open('a', encoding='utf-8') as f:
        f.write(line)
    return {'ok': True, 'written': line.strip(), 'file': str(suggestions_file)}


def main() -> int:
    parser = _build_parser()
    args   = parser.parse_args()
    cfg    = load_config(Path(os.path.expanduser(args.config)))
    check  = getattr(args, 'check_only', False)

    try:
        if args.op == 'get':
            result = get_item(cfg, args.sku)

        elif args.op == 'list':
            result = list_items(cfg, search=args.search,
                                location=args.location, status=args.status,
                                limit=args.limit, date_from=args.date_from,
                                date_to=args.date_to)

        elif args.op == 'resolve':
            sel: Dict[str, Any] = {}
            if args.sku:
                sel['sku'] = args.sku
            if args.location:
                sel['location'] = args.location
            if args.status:
                sel['status'] = args.status
            if args.date_from:
                sel['date_from'] = args.date_from
            if args.date_to:
                sel['date_to'] = args.date_to
            if args.ebay_item_id:
                sel['ebay_item_id'] = args.ebay_item_id
            if args.upc:
                sel['upc'] = args.upc
            if args.search:
                sel['search'] = args.search
            skus = resolve(cfg, **sel)
            result = {'ok': True, 'selectors': sel,
                      'count': len(skus), 'skus': sorted(skus)}

        elif args.op == 'update':
            result = update_item(cfg, args.sku, args.field, args.value,
                                 check_only=check)

        elif args.op == 'update-where':
            sel = {}
            if args.location:
                sel['location'] = args.location
            if args.status:
                sel['status'] = args.status
            if args.date_from:
                sel['date_from'] = args.date_from
            if args.date_to:
                sel['date_to'] = args.date_to
            if args.search:
                sel['search'] = args.search
            result = update_where(cfg, sel, args.field, args.value,
                                  check_only=check)

        elif args.op == 'titleupdate':
            result = titleupdate(cfg, args.sku, args.value, check_only=check)

        elif args.op == 'locationupdate':
            result = locationupdate(cfg, args.sku, args.location,
                                    check_only=check)

        elif args.op == 'verifiedupdate':
            result = verifiedupdate(cfg, args.sku, args.value, check_only=check)

        elif args.op == 'catlocmvall':
            result = catlocmvall(cfg, args.from_location, args.to_location,
                                 check_only=check)

        elif args.op == 'build-full':
            result = build_full_catalog(cfg, check_only=check)

        elif args.op == 'build-search':
            result = build_search_catalog(cfg, source=args.source,
                                          check_only=check)

        elif args.op == 'build-locations':
            result = build_location_tree(cfg, source=args.source,
                                         check_only=check)

        elif args.op == 'build-full-csv':
            result = build_full_catalog_csv(cfg, check_only=check)

        elif args.op == 'build-search-csv':
            result = build_search_catalog_csv(cfg, source=args.source,
                                              check_only=check)

        elif args.op == 'build-sqlite':
            result = build_sqlite_catalog(cfg, check_only=check)

        elif args.op == 'build-thumbnails':
            result = build_thumbnail_cache(cfg, check_only=check)

        elif args.op == 'build-all':
            result = build_all_catalogs(cfg, check_only=check)

        elif args.op == 'ensure-catalog':
            if cfg['search_catalog_path'].exists():
                result = {'ok': True, 'exists': True,
                          'path': str(cfg['search_catalog_path'])}
            else:
                result = build_search_catalog(cfg, source='auto',
                                              check_only=check)
        elif args.op == 'health':
            result = check_all(cfg,
                               include_ollama=not args.no_ollama,
                               include_ebay=not args.no_ebay)

        elif args.op == 'quality':
            from .config import sku_json
            from .items import atomic_write_json
            from .listing_quality import score_draft
            rows = []
            for sku in args.skus:
                json_path = sku_json(cfg, sku)
                if not json_path.exists():
                    rows.append({'sku': sku, 'ok': False, 'error': 'item not found'})
                    continue
                try:
                    item = json.loads(json_path.read_text(encoding='utf-8'))
                except Exception as exc:
                    rows.append({'sku': sku, 'ok': False, 'error': str(exc)})
                    continue
                q = score_draft(item)
                row: Dict[str, Any] = {'sku': sku, 'ok': True, **q.to_dict()}
                if args.save and item.get('draft_listing') is not None:
                    item['draft_listing']['quality'] = q.to_dict()
                    atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
                    row['saved'] = True
                rows.append(row)
            result = {'ok': True, 'items': rows}
            if not getattr(args, 'as_json', False):
                print(f'{"SKU":<24} {"Score":>5}  {"Flags"}')
                print('-' * 70)
                for r in rows:
                    if not r['ok']:
                        print(f'{r["sku"]:<24}  ERR    {r.get("error","")}')
                        continue
                    flags = ','.join(r.get('flags') or []) or '—'
                    print(f'{r["sku"]:<24} {r["score"]:>5}  {flags}')
                    bk = r.get('breakdown') or {}
                    parts = [f'{k}={v}' for k, v in bk.items()]
                    print(f'  {"  ".join(parts)}')
                return 0

        elif args.op == 'lookup':
            from .apis.lookup import lookup_product
            from .config import sku_json
            from .items import atomic_write_json
            json_path = sku_json(cfg, args.sku)
            if not json_path.exists():
                result = {'ok': False, 'error': f'item not found: {args.sku}'}
            else:
                item = json.loads(json_path.read_text(encoding='utf-8'))
                if args.force:
                    item.pop('product_lookup', None)
                lookup = lookup_product(item, cfg)
                if lookup is None:
                    result = {'ok': True, 'sku': args.sku, 'found': False,
                              'note': 'no barcode field (upc/ean/isbn) in item JSON'}
                else:
                    result = {'ok': True, 'sku': args.sku, 'found': True,
                              'result': lookup.to_dict()}
                    if args.save:
                        item['product_lookup'] = lookup.to_dict()
                        atomic_write_json(json_path, item,
                                          pretty=cfg.get('pretty', True))
                        result['saved'] = True

        elif args.op == 'suggest':
            result = cmd_suggest(cfg, ' '.join(args.text))

        elif args.op == 'hint':
            result = cmd_hint(cfg, args.sku, ' '.join(args.text), force=args.force)

        elif args.op == 'requeue':
            result = cmd_requeue(
                cfg,
                no_title=args.no_title,
                unidentified=args.unidentified,
                hint_set=args.hint_set,
                no_draft=args.no_draft,
                no_price=args.no_price,
                catalog_only=args.catalog_only,
                limit=args.limit,
                dry_run=not args.run,
            )

        elif args.op == 'resolve-legacy':
            result = cmd_resolve_legacy(cfg, args.skus,
                                        enqueue_stage=not args.no_stage)

        elif args.op == 'staged':
            result = cmd_staged(cfg)
            if not getattr(args, 'as_json', False) and result['ok']:
                items = result['items']
                if not items:
                    print('No items staged and awaiting review.')
                else:
                    _PC = {'high': 'H', 'medium': 'M', 'low': 'L', None: '—'}
                    print(f'{"SKU":<24} {"Q":>3} {"PC"}  {"Price":>7}  {"Location":<10} {"Title"}')
                    print('-' * 88)
                    for it in items:
                        price    = f'${it["price"]}' if it['price'] else '  N/A'
                        q        = it.get('quality')
                        q_str    = f'{q:3d}' if q is not None else '  —'
                        pc       = _PC.get(it.get('price_confidence'), '?')
                        flags    = it.get('quality_flags') or []
                        flag_str = f' [{",".join(flags[:3])}]' if flags else ''
                        print(f'{it["sku"]:<24} {q_str} {pc:>2}  {price:>7}  '
                              f'{it["location"]:<10} {it["title"][:33]}{flag_str}')
                    print(f'\n{len(items)} item(s) awaiting review. '
                          f'Q=quality 0–100 (worst first)  PC=price confidence H/M/L'
                          f'\nUse: tgw publish <SKU>')
                return 0

        elif args.op == 'publish':
            result = cmd_publish(cfg, args.skus, dry_run=args.dry_run)

        elif args.op == 'setup-ebay-hooks':
            from .apis.ebay.notifications import get_notification_preferences, set_notification_preferences
            if args.check:
                current = get_notification_preferences(cfg)
                result = {'ok': True, 'current_url': current or '(not set)'}
            else:
                set_notification_preferences(cfg, args.url)
                result = {'ok': True, 'delivery_url': args.url,
                          'note': 'eBay will now POST FixedPriceTransaction events to this URL'}

        elif args.op == 'serve':
            import uvicorn

            from .http_server import app
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                reload=args.reload,
                log_level='info',
            )
            return 0

        elif args.op == 'sku-migrate':
            from .queue import state_machine as _sm
            from .sku_migration import check_collisions, run_migration
            _sm.init(cfg['postgres_dsn'])

            if args.check_collisions:
                result = check_collisions(cfg)
            else:
                classes = [c.strip().upper() for c in args.classes.split(',') if c.strip()]
                dry_run = not args.run
                manifest_path: Optional[Path] = None
                if not dry_run:
                    if args.manifest:
                        manifest_path = Path(args.manifest)
                    else:
                        ts = datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')
                        manifest_path = Path('/opt/TGW/var/log') / f'sku-migrate-{ts}.json'
                result = run_migration(
                    cfg,
                    classes=classes,
                    dry_run=dry_run,
                    include_live_ebay=args.include_live_ebay,
                    limit=args.limit,
                    manifest_path=manifest_path,
                )

        elif args.op == 'ebay-sweep':
            result = cmd_ebay_sweep(
                cfg,
                groups=args.groups,
                location=args.location,
                limit=args.limit,
                output=Path(args.output) if args.output else None,
            )
            if args.output:
                print(json.dumps(result, indent=2))
            return 0 if result['ok'] else 1

        elif args.op == 'import-sold-csv':
            from .queue import state_machine as _sm
            _sm.init(cfg['postgres_dsn'])
            result = cmd_import_sold_csv(cfg, Path(args.file),
                                         dry_run=args.dry_run,
                                         show_columns=args.show_columns)
            if result.get('ok') and not args.show_columns:
                marked = result.get('marked', 0)
                if marked and not args.dry_run:
                    try:
                        _sm.enqueue_job(
                            queue_name='catalog_rebuild',
                            payload={'reason': 'import_sold_csv'},
                            dedupe_key='catalog_rebuild:pending',
                            not_before=time.time() + 30,
                            max_attempts=3,
                        )
                        print('catalog_rebuild job enqueued.')
                    except Exception:
                        pass

        elif args.op == 'ebay-pull':
            from .ebay.pull import build_listing_index, sync_active_listings, sync_sold_orders
            from .queue import state_machine as _sm
            from .workers.ebay_legacy_sync import _sold_state_path
            _sm.init(cfg['postgres_dsn'])

            synced_at     = datetime.now(tz=timezone.utc).isoformat()
            itemdata_root = cfg['itemdata_root']
            dry_run       = args.dry_run
            total_changes = 0

            active_stats: Dict[str, Any] = {}
            if not args.no_active:
                print('Fetching active listings from eBay...')
                active_stats = sync_active_listings(cfg, itemdata_root, synced_at,
                                                    dry_run=dry_run)
                total_changes += active_stats.get('updated', 0)
                print(f"  fetched={active_stats['fetched']}  matched={active_stats['matched']}  "
                      f"updated={active_stats['updated']}  orphaned={active_stats['orphaned']}  "
                      f"skipped_inventory={active_stats['skipped_inventory']}  "
                      f"errors={active_stats['errors']}")
                for o in active_stats.get('orphans', []):
                    print(f"  ORPHAN: ItemID={o['listing_id']} label={o.get('custom_label','')!r} "
                          f"title={o.get('title','')[:60]}")

            sold_stats: Dict[str, Any] = {}
            if not args.no_sold:
                print('Fetching sold orders from eBay...')
                listing_index = build_listing_index(itemdata_root)
                print(f'  listing index: {len(listing_index)} entries')
                sold_stats = sync_sold_orders(cfg, listing_index, synced_at,
                                              _sold_state_path(cfg), dry_run=dry_run)
                total_changes += sold_stats.get('sold_marked', 0)
                print(f"  orders_fetched={sold_stats['orders_fetched']}  "
                      f"sold_marked={sold_stats['sold_marked']}  "
                      f"errors={sold_stats['errors']}")

            if total_changes and not dry_run:
                try:
                    _sm.enqueue_job(
                        queue_name='catalog_rebuild',
                        payload={'reason': 'ebay_pull'},
                        dedupe_key='catalog_rebuild:pending',
                        not_before=time.time() + 30,
                        max_attempts=3,
                    )
                    print('catalog_rebuild job enqueued.')
                except Exception:
                    pass

            result = {
                'ok': True, 'dry_run': dry_run,
                'active': active_stats, 'sold': sold_stats,
            }

        else:
            result = {'ok': False, 'error': f'unknown op: {args.op!r}'}

        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get('ok', True) else 1

    except Exception as e:
        print(json.dumps({'ok': False, 'error': str(e)},
                         ensure_ascii=False, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
