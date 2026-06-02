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


def _load_token(cfg: Dict[str, Any]) -> str:
    token_path: Path = cfg['ebay_token_path']
    if not token_path.exists():
        raise FileNotFoundError(f'eBay token not found: {token_path}')
    state = json.loads(token_path.read_text(encoding='utf-8'))
    if time.time() >= state.get('expiry', 0):
        raise RuntimeError('eBay access token is expired — token_refresh worker should fix this')
    return state['access_token']


def _headers(cfg: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    h = {
        'Authorization': f'Bearer {_load_token(cfg)}',
        'Content-Type':  'application/json',
        'Accept':        'application/json',
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
              timeout: int = 30) -> Any:
    """Authenticated POST to the eBay REST API. Returns parsed JSON."""
    resp = _SESSION.post(f'{_BASE}{path}', headers=_headers(cfg),
                         json=body or {}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()
