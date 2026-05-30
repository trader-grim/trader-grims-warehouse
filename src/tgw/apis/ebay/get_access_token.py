#!/usr/bin/env python3
"""
TGW eBay Token Manager: get_access_token v1
Generates initial user access/refresh tokens via OAuth flow.
Dynamic paths from tgw-api-config.json, shares logic with refresh_v1.
"""

import json
import logging
import time
import os
import webbrowser
import requests
from urllib.parse import urlparse, parse_qs, urlencode
from base64 import b64encode
from pathlib import Path
from typing import Dict, Any

def get_tgw_paths(config_path: Path) -> Dict[str, Path]:
    """Load/ensure all *_root paths."""
    with open(config_path) as f:
        config = json.load(f)
    paths = {}
    for key, value in config.items():
        if key.endswith('_root'):
            path = Path(value)
            path.mkdir(parents=True, exist_ok=True)
            paths[key] = path
    return paths

TGW_ROOT = Path(os.getenv('TGW_ROOT', '/opt/TGW'))
CONFIG_PATH = TGW_ROOT / 'config' / 'tgw-api-config.json'
PATHS = get_tgw_paths(CONFIG_PATH)
STATE_PATH = PATHS['state_root'] / 'ebay_token_state.json'
LOG_PATH = PATHS['log_root'] / 'ebay_token_manager.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        config = json.load(f)
    return config.get('ebay', {})

def load_token_state() -> Dict[str, Any]:
    if STATE_PATH.exists():
        with open(STATE_PATH) as f:
            return json.load(f)
    return {"access_token": "", "refresh_token": "", "expiry": 0}

def save_token_state(state: Dict[str, Any]):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)
    logger.info(f"State saved: {STATE_PATH}")

def is_token_expired(state: Dict[str, Any]) -> bool:
    return time.time() >= state.get('expiry', 0)

def get_ebay_base(is_sandbox: bool = False) -> str:
    return 'https://api.sandbox.ebay.com' if is_sandbox else 'https://api.ebay.com'

def get_auth_base(is_sandbox: bool = False) -> str:
    return 'https://auth.sandbox.ebay.com' if is_sandbox else 'https://auth.ebay.com'

def generate_auth_url(ebay_config: Dict[str, Any], is_sandbox: bool = False) -> str:
    client_id = ebay_config['app_id']
    scopes = ebay_config.get('scopes', 'https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.account https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment')
    redirect_uri = ebay_config.get('redirect_uri', 'http://localhost')
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': scopes
    }
    url = f"{get_auth_base(is_sandbox)}/oauth2/authorize?{urlencode(params)}"
    logger.info(f"Auth URL: {url}")
    return url

def exchange_code_for_tokens(code: str, ebay_config: Dict[str, Any], is_sandbox: bool = False) -> Dict[str, Any]:
    creds = f"{ebay_config['app_id']}:{ebay_config['cert_id']}"
    auth_header = f"Basic {b64encode(creds.encode()).decode()}"
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': ebay_config.get('redirect_uri', 'http://localhost')
    }
    resp = requests.post(
        f"{get_ebay_base(is_sandbox)}/identity/v1/oauth2/token",
        headers={'Authorization': auth_header, 'Content-Type': 'application/x-www-form-urlencoded'},
        data=data
    )
    resp.raise_for_status()
    tokens = resp.json()
    tokens['expiry'] = time.time() + tokens['expires_in']
    return tokens

def get_access_token(prompt_if_needed: bool = True, is_sandbox: bool = False) -> str:
    """Get valid token: auto-refresh if possible, browser only for initial consent."""
    ebay_config = load_config()
    state = load_token_state()

    # Fast path: valid token exists
    if not is_token_expired(state) and state.get('access_token'):
        logger.info("Valid token found.")
        return state['access_token']

    # Auto-refresh if refresh_token exists (99% cases, NO BROWSER)
    if state.get('refresh_token'):
        try:
            from tgw_ebay_token_manager_refresh_access_token_v1 import refresh_access_token
            refreshed = refresh_access_token(is_sandbox=is_sandbox)
            logger.info("Auto-refreshed token - no browser needed.")
            return refreshed
        except Exception as e:
            logger.warning(f"Refresh failed (%s), falling back to prompt.", e)

    # True first-time: browser + consent (once)
    if prompt_if_needed:
        logger.info("Initial OAuth needed (no refresh_token).")
        print("\n=== eBay Login Required (one-time) ===")
        auth_url = generate_auth_url(ebay_config, is_sandbox)
        print(f"Open: {auth_url}")
        webbrowser.open(auth_url)
        full_url = input("Paste FULL redirect URL: ").strip()
        parsed = urlparse(full_url)
        code = parse_qs(parsed.query).get('code', [None])[0]
        if not code:
            raise ValueError("No code=... in URL")
        tokens = exchange_code_for_tokens(code, ebay_config, is_sandbox)
        save_token_state(tokens)
        return tokens['access_token']

    raise RuntimeError("No valid token and prompt disabled.")

def self_test(is_sandbox: bool = False):
    try:
        logger.info("Paths OK: config=%s, state=%s", CONFIG_PATH, STATE_PATH)
        token = get_access_token(is_sandbox=is_sandbox)
        logger.info("SUCCESS: Got token %s...", token[:20])
    except Exception as e:
        logger.error("FAIL: %s", e)
        raise

if __name__ == '__main__':
    is_sandbox = os.getenv('EBAY_ENV') == 'sandbox'
    self_test(is_sandbox)
