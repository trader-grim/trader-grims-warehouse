"""
tgw.apis.ebay.client — Thin eBay REST client.

Handles token loading and authenticated GET/POST calls.
All callers go through ebay_get() / ebay_post() — never build headers by hand.

Quota (PP-QUOTA-001, session 42): every call here is counted against its eBay
billing pool via tgw.quota; background callers are halted before exhausting a
pool (QuotaBudgetExceeded → transient requeue in workers), and any 429 is
recorded as an incident with caller identity. This module is the choke point —
do not add eBay REST calls that bypass it.

Raw capture (PRIME DIRECTIVE 1, session 42): every response eBay sends us is
appended to incoming/ebay/YYYY-MM-DD.jsonl.gz before any worker touches it.
eBay's data is the business's data — preservation happens HERE, at the fence,
so no worker can forget it. Capture is fail-open (a capture error never breaks
the call) and bodies over _CAPTURE_MAX_BYTES are recorded as metadata only
(the bulk taxonomy download keeps its own raw asset).
"""

from __future__ import annotations

import fcntl
import gzip
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from tgw import quota

log = logging.getLogger(__name__)

_SESSION = requests.Session()
_BASE    = 'https://api.ebay.com'

_CAPTURE_MAX_BYTES = 5 * 1024 * 1024
# Dave's directive (s42): ALL inbound data lives under /opt/TGW/incoming — its own
# top-level structure, group-only permissions. See /opt/TGW/incoming/README.md.
_DEFAULT_CAPTURE_ROOT = '/opt/TGW/incoming/ebay'


def capture_response(cfg: Dict[str, Any], api: str, name: str,
                     params: Optional[Dict[str, Any]], status: int,
                     content: bytes) -> None:
    """Append one eBay response to today's capture archive (gzip-member JSONL,
    safe for concurrent appenders via flock). Fail-open by design."""
    try:
        raw_cfg = cfg.get('raw', cfg) if isinstance(cfg, dict) else {}
        if not raw_cfg.get('ebay_capture_enabled', True):
            return
        root = Path(raw_cfg.get('ebay_capture_root', _DEFAULT_CAPTURE_ROOT))
        root.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        rec: Dict[str, Any] = {
            'ts': now.isoformat(),
            'api': api,
            'name': name,
            'params': params or None,
            'status': status,
            'bytes': len(content),
        }
        if len(content) <= _CAPTURE_MAX_BYTES:
            rec['body'] = content.decode('utf-8', errors='replace')
        else:
            rec['body_omitted'] = 'over size cap — large downloads keep their own raw asset'
        member = gzip.compress((json.dumps(rec) + '\n').encode('utf-8'))
        day_file = root / f"{now.strftime('%Y-%m-%d')}.jsonl.gz"
        fresh = not day_file.exists()
        with open(day_file, 'ab') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(member)
        if fresh:
            # umask would leave it world-readable; incoming/ is group-only policy
            day_file.chmod(0o660)
    except Exception as exc:  # noqa: BLE001 — never break the call it preserves
        log.warning('eBay capture failed (call unaffected): %s', exc)


def _counted(cfg: Dict[str, Any], method: str, path: str, **kwargs: Any) -> requests.Response:
    """One metered request: precheck budget, count the call, flag any 429,
    capture the raw response."""
    pool = quota.pool_for_rest_path(path)
    quota.precheck(cfg, pool)
    resp = getattr(_SESSION, method)(f'{_BASE}{path}', **kwargs)
    quota.record(cfg, pool)
    if resp.status_code == 429:
        quota.record_429(cfg, pool, f'{method.upper()} {path}')
    capture_response(cfg, 'rest', f'{method.upper()} {path}',
                     kwargs.get('params'), resp.status_code, resp.content)
    resp.raise_for_status()
    return resp


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
    resp = _counted(cfg, 'get', path, headers=_headers(cfg),
                    params=params, timeout=timeout)
    return resp.json()


def ebay_get_bytes(cfg: Dict[str, Any], path: str,
                   params: Optional[Dict[str, Any]] = None,
                   timeout: int = 300) -> bytes:
    """Authenticated GET returning the raw response body (for file-download
    endpoints like Taxonomy fetch_item_aspects, whose body is a .gz file)."""
    resp = _counted(cfg, 'get', path, headers=_headers(cfg),
                    params=params, timeout=timeout)
    return resp.content


def ebay_post(cfg: Dict[str, Any], path: str,
              body: Optional[Dict[str, Any]] = None,
              extra_headers: Optional[Dict[str, str]] = None,
              timeout: int = 30) -> Any:
    """Authenticated POST to the eBay REST API. Returns parsed JSON or {} for 204."""
    resp = _counted(cfg, 'post', path, headers=_headers(cfg, extra_headers),
                    json=body or {}, timeout=timeout)
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def ebay_put(cfg: Dict[str, Any], path: str,
             body: Optional[Dict[str, Any]] = None,
             extra_headers: Optional[Dict[str, str]] = None,
             timeout: int = 30) -> Any:
    """Authenticated PUT to the eBay REST API. Returns parsed JSON or None for 204."""
    resp = _counted(cfg, 'put', path, headers=_headers(cfg, extra_headers),
                    json=body or {}, timeout=timeout)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def ebay_delete(cfg: Dict[str, Any], path: str, timeout: int = 30) -> None:
    """Authenticated DELETE to the eBay REST API. Raises on non-2xx."""
    _counted(cfg, 'delete', path, headers=_headers(cfg), timeout=timeout)
