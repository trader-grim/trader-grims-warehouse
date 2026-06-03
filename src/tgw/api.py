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
from .sqlite_catalog import build_sqlite_catalog
from .thumbnail import build_thumbnail_cache
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
    from tgw.config import sku_json
    from tgw.items import atomic_write_json
    from tgw.queue import state_machine
    import psycopg2.errors

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
