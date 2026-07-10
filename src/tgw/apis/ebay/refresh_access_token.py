import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests

from tgw.apis.ebay._token_io import atomic_write_token_json

TGW_ROOT = Path(os.getenv('TGW_ROOT', '/opt/TGW'))
TGW_CONFIG_PATH = TGW_ROOT / 'config' / 'tgw-api-config.json'

def _load_raw_config() -> Dict[str, Any]:
    with open(TGW_CONFIG_PATH) as f:
        return json.load(f)

def _secrets_root() -> Path:
    raw = _load_raw_config()
    return Path(raw.get('secrets_root', '/opt/TGW/secrets'))

TOKEN_PATH = _secrets_root() / 'ebay-token.json'
LOG_PATH   = Path(_load_raw_config().get('log_root', '/opt/TGW/runtime/logs')) / 'ebay_token_manager.log'
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def get_ebay_config(is_sandbox: bool | None = None) -> Dict[str, Any]:
    """is_sandbox=None (default) falls back to the EBAY_ENV env var, preserving
    token_refresh.py's existing (env-var-only) behavior. Pass is_sandbox
    explicitly (audit#1143 #1211 follow-up) to select sandbox/production by
    parameter instead — avoids callers having to mutate process-global
    EBAY_ENV, which would leak across unrelated calls in the same process."""
    raw = _load_raw_config()
    creds_path = Path(raw.get('secrets_root', '/opt/TGW/secrets')) / 'ebay-credentials.json'
    if not creds_path.exists():
        raise FileNotFoundError(f'eBay credentials not found: {creds_path}')
    creds = json.loads(creds_path.read_text())
    if is_sandbox is None:
        is_sandbox = os.getenv('EBAY_ENV', 'production') == 'sandbox'
    prefix = 'sandbox_' if is_sandbox else ''
    app_id  = creds.get(f'{prefix}app_id')
    cert_id = creds.get(f'{prefix}cert_id')
    if not app_id or not cert_id:
        raise ValueError(f'Missing {prefix}app_id/cert_id in {creds_path}')
    ebay_cfg = raw.get('ebay', {})
    return {
        'api_root_ebay': 'https://api.sandbox.ebay.com' if is_sandbox else 'https://api.ebay.com',
        'app_id':  app_id,
        'cert_id': cert_id,
        'scopes':  ebay_cfg.get('scopes', 'https://api.ebay.com/oauth/api_scope'),
    }

def load_token_state() -> Dict[str, Any]:
    if not TOKEN_PATH.exists():
        raise ValueError(f'No token state at {TOKEN_PATH}; run get_access_token first')
    with open(TOKEN_PATH) as f:
        return json.load(f)

def save_token_state(state: Dict[str, Any]) -> None:
    # audit#1143 #1162+#1177: atomic tmp+rename — TOKEN_PATH is the sole
    # copy of the eBay refresh token; a partial write (crash/kill mid-write)
    # corrupts it and forces full browser re-consent.
    atomic_write_token_json(TOKEN_PATH, json.dumps(state, indent=2) + '\n')

def is_token_expired(state: Dict[str, Any]) -> bool:
    expiry = state.get('expiry', 0)
    return time.time() >= expiry - 300  # 5min buffer

def refresh_access_token(force: bool = False, is_sandbox: bool | None = None) -> str:
    """Refresh the eBay access token.

    When force=True the internal expiry guard is bypassed — the caller is
    responsible for deciding whether a refresh is needed.  The worker
    (token_refresh.py) uses force=True because it owns the scheduling
    decision; using the internal guard there would create a double-buffer
    bug that delays the actual eBay call until the last 5 minutes of
    token life instead of the intended 30-minute window.

    is_sandbox=None (default) falls back to EBAY_ENV, unchanged from before —
    token_refresh.py's worker call (force=True, no is_sandbox) is unaffected.
    """
    ebay_config = get_ebay_config(is_sandbox=is_sandbox)
    state = load_token_state()

    if not force and not is_token_expired(state):
        logger.info("Token valid")
        return state['access_token']

    logger.info(f"Refreshing via {ebay_config['api_root_ebay']}")
    url = f"{ebay_config['api_root_ebay']}/identity/v1/oauth2/token"
    auth_b64 = base64.b64encode(f"{ebay_config['app_id']}:{ebay_config['cert_id']}".encode()).decode()
    data = {
        'grant_type': 'refresh_token',
        'refresh_token': state['refresh_token'],
        'scope': ebay_config['scopes']
    }
    headers = {'Content-Type': 'application/x-www-form-urlencoded', 'Authorization': f'Basic {auth_b64}'}

    resp = requests.post(url, data=data, headers=headers)
    resp.raise_for_status()
    token_data = resp.json()

    state.update({
        'access_token': token_data['access_token'],
        'refresh_token': token_data.get('refresh_token', state['refresh_token']),
        'expiry': time.time() + token_data['expires_in']
    })
    save_token_state(state)
    logger.info("Refreshed")
    return state['access_token']

def self_test():
    try:
        logger.info(f"Paths OK: config={TGW_CONFIG_PATH}, token={TOKEN_PATH}")
        ebay_config = get_ebay_config()
        logger.info(f"eBay OK: {ebay_config['api_root_ebay'][:21]}...")
        token = refresh_access_token()
        logger.info(f"PASS: token {token[:20]}...")
    except Exception as e:
        logger.error(f"FAIL: {e}")

if __name__ == '__main__':
    self_test()
