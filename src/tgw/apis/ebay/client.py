"""
tgw.apis.ebay.client — Thin eBay REST client.

Handles token loading and authenticated GET/POST calls.
All callers go through ebay_get() / ebay_post() — never build headers by hand.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

_SESSION = requests.Session()
_BASE    = 'https://api.ebay.com'


def load_token(cfg: Dict[str, Any]) -> str:
    """Return the current eBay OAuth access token, raising if missing or expired."""
    token_path: Path = cfg['ebay_token_path']
    if not token_path.exists():
        raise FileNotFoundError(f'eBay token not found: {token_path}')
    state = json.loads(token_path.read_text(encoding='utf-8'))
    if time.time() >= state.get('expiry', 0):
        raise RuntimeError('eBay access token is expired — token_refresh worker should fix this')
    return state['access_token']


_load_token = load_token  # backward-compat alias


def _headers(cfg: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {
        'Authorization':  f'Bearer {_load_token(cfg)}',
        'Content-Type':   'application/json',
        'Accept':         'application/json',
        'Content-Language': 'en-US',
    }
    if extra:
        h.update(extra)
    return h


def ebay_get(cfg: Dict[str, Any], path: str,
             params: Optional[Dict[str, Any]] = None,
             timeout: int = 30) -> Any:
    """Authenticated GET to the eBay REST API. Returns parsed JSON."""
    resp = _SESSION.get(f'{_BASE}{path}', headers=_headers(cfg),
                        params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def ebay_post(cfg: Dict[str, Any], path: str,
              body: Optional[Dict[str, Any]] = None,
              extra_headers: Optional[Dict[str, str]] = None,
              timeout: int = 30) -> Any:
    """Authenticated POST to the eBay REST API. Returns parsed JSON or {} for 204."""
    resp = _SESSION.post(f'{_BASE}{path}', headers=_headers(cfg, extra_headers),
                         json=body or {}, timeout=timeout)
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def ebay_put(cfg: Dict[str, Any], path: str,
             body: Optional[Dict[str, Any]] = None,
             extra_headers: Optional[Dict[str, str]] = None,
             timeout: int = 30) -> Any:
    """Authenticated PUT to the eBay REST API. Returns parsed JSON or None for 204."""
    resp = _SESSION.put(f'{_BASE}{path}', headers=_headers(cfg, extra_headers),
                        json=body or {}, timeout=timeout)
    resp.raise_for_status()
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def ebay_delete(cfg: Dict[str, Any], path: str, timeout: int = 30) -> None:
    """Authenticated DELETE to the eBay REST API. Raises on non-2xx."""
    resp = _SESSION.delete(f'{_BASE}{path}', headers=_headers(cfg), timeout=timeout)
    resp.raise_for_status()
