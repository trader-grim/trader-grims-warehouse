import json
import logging
import time
import requests
import base64
import os
from pathlib import Path
from typing import Dict, Any

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

def get_ebay_config() -> Dict[str, Any]:
    raw = _load_raw_config()
    creds_path = Path(raw.get('secrets_root', '/opt/TGW/secrets')) / 'ebay-credentials.json'
    if not creds_path.exists():
        raise FileNotFoundError(f'eBay credentials not found: {creds_path}')
    creds = json.loads(creds_path.read_text())
    env = os.getenv('EBAY_ENV', 'production')
    prefix = 'sandbox_' if env == 'sandbox' else ''
    app_id  = creds.get(f'{prefix}app_id')
    cert_id = creds.get(f'{prefix}cert_id')
    if not app_id or not cert_id:
        raise ValueError(f'Missing {prefix}app_id/cert_id in {creds_path}')
    ebay_cfg = raw.get('ebay', {})
    return {
        'api_root_ebay': 'https://api.sandbox.ebay.com' if env == 'sandbox' else 'https://api.ebay.com',
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
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(state, indent=2) + '\n')
    TOKEN_PATH.chmod(0o600)

def is_token_expired(state: Dict[str, Any]) -> bool:
    expiry = state.get('expiry', 0)
    return time.time() >= expiry - 300  # 5min buffer

def refresh_access_token() -> str:
    ebay_config = get_ebay_config()
    state = load_token_state()

    if not is_token_expired(state):
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
