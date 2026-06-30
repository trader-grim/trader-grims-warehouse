"""
tgw.ready — Ready state + rate-limited listing dole-out (PP-EDITOR-001 / PP-REVISION-001).

Carries the draft → review → apply principle into code: after staging review,
"set Ready" is the default done-state. Ready items are published automatically
by the self-scheduling ``ebay_dole`` worker at a configurable rate (default
1/60 of the ready pool per cycle). ``tgw publish <sku>`` remains the List-Now
bypass that skips the dole-out queue entirely.

State lives in the item JSON as ``ebay_offer.ready_at`` (ISO-8601, UTC).
``ebay_offer.status`` stays eBay's value (UNPUBLISHED/PUBLISHED — ebay_sync
rewrites it), so the local review verdict needs its own field. An item is in
the ready pool when it has an ``offer_id``, ``status == 'UNPUBLISHED'`` and a
``ready_at`` timestamp; publishing flips status to PUBLISHED, which removes it
from the pool automatically.

CLI: ``tgw ready [list|set <sku…>|unset <sku…>]``
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from tgw.items import load_item_doc, sku_json, update_item


def ready_pool(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Scan ItemData for ready items, oldest ``ready_at`` first."""
    root = cfg['itemdata_root']
    pool: List[Dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        jf = child / f'{child.name}.json'
        if not jf.exists():
            continue
        try:
            doc = json.loads(jf.read_text(encoding='utf-8'))
        except Exception:
            continue
        offer = doc.get('ebay_offer') or {}
        if offer.get('offer_id') and offer.get('status') == 'UNPUBLISHED' and offer.get('ready_at'):
            pool.append({
                'sku': child.name,
                'title': doc.get('title', ''),
                'price': offer.get('price'),
                'location': doc.get('location', ''),
                'ready_at': offer['ready_at'],
            })
    pool.sort(key=lambda x: x['ready_at'])
    return pool


def dole_batch_size(pool_size: int, divisor: int) -> int:
    """Items to publish this cycle: 1/divisor of the pool, at least 1 when non-empty."""
    if pool_size <= 0:
        return 0
    return max(1, pool_size // max(1, divisor))


def set_ready(cfg: Dict[str, Any], skus: List[str]) -> Dict[str, Any]:
    """Mark staged items ready for dole-out. Validates each is a reviewable offer."""
    marked: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []
    now_iso = datetime.now(tz=timezone.utc).isoformat(timespec='seconds')

    for sku in skus:
        path = sku_json(cfg, sku)
        if not path.exists():
            errors.append(f'{sku}: item not found')
            continue
        try:
            doc = load_item_doc(path)
        except Exception as exc:
            errors.append(f'{sku}: bad JSON — {exc}')
            continue
        offer = doc.get('ebay_offer') or {}
        if not offer.get('offer_id'):
            errors.append(f'{sku}: no offer_id — run ebay_stage first')
            continue
        if offer.get('status') != 'UNPUBLISHED':
            skipped.append(f'{sku}: offer status is {offer.get("status")!r} — not UNPUBLISHED')
            continue
        if offer.get('ready_at'):
            skipped.append(f'{sku}: already ready since {offer["ready_at"]}')
            continue
        offer['ready_at'] = now_iso
        result = update_item(cfg, sku, 'ebay_offer', offer)
        if result['ok']:
            marked.append(sku)
        else:
            errors.append(f'{sku}: {result.get("error")}')

    return {'ok': not errors, 'marked': marked, 'skipped': skipped, 'errors': errors}


def unset_ready(cfg: Dict[str, Any], skus: List[str]) -> Dict[str, Any]:
    """Pull items back out of the dole-out queue (clears ``ready_at``)."""
    cleared: List[str] = []
    skipped: List[str] = []
    errors: List[str] = []

    for sku in skus:
        path = sku_json(cfg, sku)
        if not path.exists():
            errors.append(f'{sku}: item not found')
            continue
        try:
            doc = load_item_doc(path)
        except Exception as exc:
            errors.append(f'{sku}: bad JSON — {exc}')
            continue
        offer = doc.get('ebay_offer') or {}
        if not offer.get('ready_at'):
            skipped.append(f'{sku}: not in ready state')
            continue
        offer.pop('ready_at', None)
        result = update_item(cfg, sku, 'ebay_offer', offer)
        if result['ok']:
            cleared.append(sku)
        else:
            errors.append(f'{sku}: {result.get("error")}')

    return {'ok': not errors, 'cleared': cleared, 'skipped': skipped, 'errors': errors}


def cmd_ready(cfg: Dict[str, Any], op: str, skus: List[str]) -> Dict[str, Any]:
    """``tgw ready [list|set <sku…>|unset <sku…>]`` handler."""
    if op in ('set', 'unset') and not skus:
        print(f'Usage: tgw ready {op} <sku...>')
        return {'ok': False, 'error': 'no SKUs given'}

    if op == 'set':
        result = set_ready(cfg, skus)
        for sku in result['marked']:
            print(f'ready: {sku}')
        for line in result['skipped']:
            print(f'skipped: {line}')
        for line in result['errors']:
            print(f'error: {line}')
        return result

    if op == 'unset':
        result = unset_ready(cfg, skus)
        for sku in result['cleared']:
            print(f'unready: {sku}')
        for line in result['skipped']:
            print(f'skipped: {line}')
        for line in result['errors']:
            print(f'error: {line}')
        return result

    # default: list
    pool = ready_pool(cfg)
    divisor = int(cfg.get('dole_divisor', 60))
    interval_s = int(cfg.get('dole_interval_s', 3600))
    per_cycle = dole_batch_size(len(pool), divisor)
    if not pool:
        print('Ready pool is empty.')
    else:
        for item in pool:
            price = f'{item["price"]}' if item['price'] is not None else '?'
            print(f'{item["sku"]}  ${price:>8}  since {item["ready_at"]}  {item["title"][:60]}')
        print(f'\n{len(pool)} ready — dole rate {per_cycle}/cycle '
              f'(1/{divisor} of pool, cycle {interval_s // 60}min)')
    return {'ok': True, 'count': len(pool), 'per_cycle': per_cycle, 'items': pool}
