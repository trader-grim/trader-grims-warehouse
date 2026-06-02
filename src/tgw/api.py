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

    return parser


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
