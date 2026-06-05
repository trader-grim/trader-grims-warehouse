"""
tgw.apis.lookup.igdb — Video game lookup via IGDB (Twitch developer API).

Silently skipped if secrets_root/igdb-credentials.json is absent.
Key: {"client_id": "...", "client_secret": "..."}
Requires free Twitch developer account: https://dev.twitch.tv/console
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import requests

from .base import LookupResult, now_iso
from .base import secrets_root as _secrets_root

log = logging.getLogger(__name__)

_TOKEN_URL = 'https://id.twitch.tv/oauth2/token'
_GAMES_URL = 'https://api.igdb.com/v4/games'
_TIMEOUT   = 10

# In-memory token cache: client_id → (access_token, expires_at_epoch)
_token_cache: Dict[str, Tuple[str, float]] = {}


def _get_token(client_id: str, client_secret: str) -> Optional[str]:
    """Return a valid Twitch app access token, refreshing when near expiry."""
    cached = _token_cache.get(client_id)
    if cached and time.time() < cached[1] - 60:
        return cached[0]
    try:
        resp = requests.post(
            _TOKEN_URL,
            params={
                'client_id':     client_id,
                'client_secret': client_secret,
                'grant_type':    'client_credentials',
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('igdb: token fetch failed: %s', exc)
        return None

    token      = data.get('access_token', '')
    expires_in = int(data.get('expires_in', 3600))
    if token:
        _token_cache[client_id] = (token, time.time() + expires_in)
    return token or None


def lookup(title: str, cfg: Dict[str, Any]) -> Optional[LookupResult]:
    """Search IGDB for a game by title. Returns None if no key, on miss, or error."""
    if not title or not title.strip():
        return None

    key_file = _secrets_root(cfg) / 'igdb-credentials.json'
    if not key_file.exists():
        return None
    try:
        creds         = json.loads(key_file.read_text())
        client_id     = creds.get('client_id', '')
        client_secret = creds.get('client_secret', '')
    except Exception:
        return None
    if not client_id or not client_secret:
        return None

    token = _get_token(client_id, client_secret)
    if not token:
        return None

    # Apicalypse query syntax
    safe_title = title.replace('"', '')[:80]
    query = (
        f'search "{safe_title}"; '
        'fields name,genres.name,platforms.abbreviation,'
        'cover.url,first_release_date,summary; '
        'limit 1;'
    )
    try:
        resp = requests.post(
            _GAMES_URL,
            headers={
                'Client-ID':     client_id,
                'Authorization': f'Bearer {token}',
                'Accept':        'application/json',
            },
            data=query,
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
    except requests.exceptions.RequestException as exc:
        log.warning('igdb: request failed for %r: %s', title, exc)
        return None

    if not results:
        log.debug('igdb: no result for %r', title)
        return None

    hit       = results[0]
    genres    = ', '.join(g.get('name', '') for g in hit.get('genres', []))
    platforms = ', '.join(p.get('abbreviation', '') for p in hit.get('platforms', []))
    release   = hit.get('first_release_date')
    year      = str(datetime.fromtimestamp(release, tz=timezone.utc).year) if release else ''
    summary   = hit.get('summary', '')

    cover_url = ''
    raw_cover = (hit.get('cover') or {}).get('url', '')
    if raw_cover:
        cover_url = raw_cover if raw_cover.startswith('http') else 'https:' + raw_cover

    desc_parts = [p for p in (genres, platforms, year) if p]

    log.info('igdb: hit for %r — %r', title, hit.get('name', '')[:60])
    return LookupResult(
        source      = 'igdb',
        fetched_at  = now_iso(),
        title       = hit.get('name', ''),
        description = summary or ', '.join(desc_parts),
        category    = genres or 'Video Games',
        image_url   = cover_url,
        extra       = {
            'raw':       hit,
            'platforms': platforms,
            'year':      year,
        },
    )
