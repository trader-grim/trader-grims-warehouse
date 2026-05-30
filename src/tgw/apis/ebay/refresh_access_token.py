import json
import logging
import time
import requests
import base64
import os
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
CONFIG_ROOT = TGW_ROOT / 'config'
TGW_CONFIG_PATH = CONFIG_ROOT / 'tgw-api-config.json'

PATHS = get_tgw_paths(TGW_CONFIG_PATH)
STATE_PATH = PATHS['state_root'] / 'ebay_token_state.json'
LOG_PATH = PATHS['log_root'] / 'ebay_token_manager.log'
# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def load_config() -> Dict[str, Any]:
    with open(TGW_CONFIG_PATH) as f:
        return json.load(f)

def get_ebay_config(config: Dict[str, Any]) -> Dict[str, Any]:
    ebay = config.get('ebay', {})
    env = os.getenv('EBAY_ENV', 'production')
    prefix = 'sandbox_' if env == 'sandbox' else ''
    app_id = ebay.get(f"{prefix}app_id")
    cert_id = ebay.get(f"{prefix}cert_id")
    api_root_ebay = 'https://api.sandbox.ebay.com' if env == 'sandbox' else 'https://api.ebay.com'
    scopes = ebay.get('scopes', 'https://api.ebay.com/oauth/api_scope')
    if not app_id or not cert_id:
        raise ValueError(f"Missing eBay {prefix}app_id/cert_id in config")
    return {'api_root_ebay': api_root_ebay, 'app_id': app_id, 'cert_id': cert_id, 'scopes': scopes}

def load_token_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        raise ValueError(f"No state at {STATE_PATH}; init via get_access_token")
    with open(STATE_PATH) as f:
        return json.load(f)

def save_token_state(state: Dict[str, Any]):
    with open(STATE_PATH, 'w') as f:
        json.dump(state, f, indent=2)

def is_token_expired(state: Dict[str, Any]) -> bool:
    expiry = state.get('expiry', 0)
    return time.time() >= expiry - 300  # 5min buffer

def refresh_access_token() -> str:
    config = load_config()
    ebay_config = get_ebay_config(config)
    state = load_token_state()

    if not is_token_expired(state):
        logger.info("Token valid")
        return state['access_token']

    logger.info(f"Refreshing via {ebay_config['api_root_ebay']}")
    url = f"{ebay_config['api_root_ebay']}/identity/v1/oauth2/token"
    auth_b64 = base64.b64encode(f"{ebay_config['app_id']}|{ebay_config['cert_id']}".encode()).decode()
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
        logger.info(f"Paths OK: config={TGW_CONFIG_PATH}, state={STATE_PATH}")
        config = load_config()
        ebay_config = get_ebay_config(config)
        logger.info(f"eBay OK: {ebay_config['api_root_ebay'][:21]}...")
        token = refresh_access_token()
        logger.info(f"PASS: token {token[:20]}...")
    except Exception as e:
        logger.error(f"FAIL: {e}")

if __name__ == '__main__':
    self_test()
