"""
tgw.apis.lookup.pricecharting — current market value for games / cards / collectibles.

PriceCharting Tier 2 enrichment (PP-LOOKUP-001): IGDB/JustTCG return metadata
but no price; PriceCharting fills LookupResult.msrp with a category-specific
current value the price worker consumes.

Silently skipped if secrets_root/pricecharting-credentials.json is absent
(mirrors the IGDB graceful-skip pattern), so this module is inert until the
operator supplies a token. Public API; prices are returned in pennies.

Key file: {"token": "..."}   (or {"api_key": "..."})
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import requests

from .base import LookupResult, now_iso
from .base import secrets_root as _secrets_root

log = logging.getLogger(__name__)

_API_URL = 'https://www.pricecharting.com/api/product'
_TIMEOUT = 10


def _to_dollars(pennies: Any) -> Optional[float]:
    """PriceCharting prices are integer pennies; convert to dollars, drop non-positive."""
    try:
        cents = int(pennies)
    except (TypeError, ValueError):
        return None
    return round(cents / 100.0, 2) if cents > 0 else None


def _load_token(cfg: Dict[str, Any]) -> str:
    key_file = _secrets_root(cfg) / 'pricecharting-credentials.json'
    if not key_file.exists():
        return ''
    try:
        creds = json.loads(key_file.read_text())
    except Exception:
        return ''
    return str(creds.get('token') or creds.get('api_key') or '').strip()


def lookup(title: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Look up current market value by title. None if no token, on miss, or error."""
    if not title or not title.strip():
        return None

    token = _load_token(cfg)
    if not token:
        return None

    try:
        resp = requests.get(
            _API_URL,
            params={'t': token, 'q': title.strip()[:120]},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('pricecharting: request failed for %r: %s', title, exc)
        return None
    except ValueError:
        return None

    if not isinstance(data, dict) or data.get('status') == 'error' or not data.get('id'):
        log.debug('pricecharting: no result for %r', title)
        return None

    loose = _to_dollars(data.get('loose-price'))
    cib   = _to_dollars(data.get('cib-price'))
    new   = _to_dollars(data.get('new-price'))
    # Prefer a retail-ish value for msrp (so the strikethrough gate behaves);
    # fall back to complete-in-box, then loose. Full breakdown lives in extra.
    msrp = new or cib or loose

    name    = str(data.get('product-name', '') or '')
    console = str(data.get('console-name', '') or '')

    log.info('pricecharting: hit for %r — %r (new=%s loose=%s)',
             title, name[:60], new, loose)
    return LookupResult(
        source     = 'pricecharting',
        fetched_at = now_iso(),
        title      = name,
        category   = console,
        msrp       = msrp,
        extra      = {
            'raw':         data,
            'loose_price': loose,
            'cib_price':   cib,
            'new_price':   new,
        },
    )
