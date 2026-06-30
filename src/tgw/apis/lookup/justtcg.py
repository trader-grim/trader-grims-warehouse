"""
tgw.apis.lookup.justtcg — Trading card lookup via JustTCG (no auth required).

Triggered when AI category matches Trading Cards / CCG.
Free tier, no key needed.  API: https://api.justtcg.com/v1/
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests

from .base import LookupResult, now_iso

log = logging.getLogger(__name__)

_ENDPOINT = 'https://api.justtcg.com/v1/cards'
_TIMEOUT  = 10


def lookup(name: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Search JustTCG by card name. Returns None on miss or error."""
    name = (name or '').strip()
    if not name:
        return None
    try:
        resp = requests.get(
            _ENDPOINT,
            params={'name': name, 'limit': 1},
            headers={'Accept': 'application/json'},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (404, 400):
            log.debug('justtcg: no result for %r', name)
            return None
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('justtcg: request failed for %r: %s', name, exc)
        return None

    # API may return {"data": [...]} or a bare list
    cards = (data.get('data')
             or data.get('cards')
             or (data if isinstance(data, list) else []))
    if not cards:
        log.debug('justtcg: no result for %r', name)
        return None

    hit      = cards[0]
    set_info = hit.get('set', {})
    set_name = set_info.get('name', '') if isinstance(set_info, dict) else str(set_info)
    rarity   = hit.get('rarity', '')
    game     = hit.get('game', '')

    price: Optional[float] = None
    for key in ('market_price', 'price', 'low_price'):
        try:
            val = hit.get(key)
            if val is not None:
                price = float(val)
                break
        except (ValueError, TypeError):
            pass

    desc_parts = [p for p in (set_name, rarity) if p]
    card_name  = hit.get('name', '')

    log.info('justtcg: hit for %r — %r', name, card_name[:60])
    return LookupResult(
        source      = 'justtcg',
        fetched_at  = now_iso(),
        title       = card_name,
        description = ', '.join(desc_parts),
        category    = f'{game} Trading Cards' if game else 'Trading Cards',
        msrp        = price,
        extra       = {'raw': hit, 'set': set_name, 'rarity': rarity},
    )
