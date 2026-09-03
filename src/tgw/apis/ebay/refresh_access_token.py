import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict

import requests

from tgw.apis.ebay._token_io import (
    atomic_write_token_json,
    stamp_token_environment,
    validate_token_environment,
)
from tgw.config import (
    configured_ebay_environment,
    ebay_environment_settings,
    ebay_oauth_settings,
    normalize_ebay_environment,
)

TGW_ROOT = Path(os.getenv('TGW_ROOT', '/opt/TGW'))
TGW_CONFIG_PATH = TGW_ROOT / 'config' / 'tgw-api-config.json'

def _load_raw_config() -> Dict[str, Any]:
    with open(TGW_CONFIG_PATH) as f:
        return json.load(f)

def _secrets_root() -> Path:
    raw = _load_raw_config()
    return Path(raw.get('secrets_root', '/opt/TGW/secrets'))

try:
    # CI/portability fix: this all used to run unconditionally at import
    # time, so merely importing this module (e.g. this session's own new
    # tests/test_refresh_access_token.py additions, or any CI runner
    # without /opt/TGW) crashed with FileNotFoundError before a single test
    # could run — every CI run on main/PRs since 2026-06-15 failed
    # collection on this exact line. Fall back to a harmless stream-only-
    # logging default when the real config isn't present; any function that
    # actually reads/writes TOKEN_PATH still surfaces a clear error at call
    # time if truly unconfigured, rather than crashing on mere import.
    TOKEN_PATH = _secrets_root() / 'ebay-token.json'
    SANDBOX_TOKEN_PATH = _secrets_root() / 'ebay-sandbox-token.json'
    LOG_PATH   = Path(_load_raw_config().get('log_root', '/opt/TGW/runtime/logs')) / 'ebay_token_manager.log'
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _log_handlers = [logging.FileHandler(LOG_PATH), logging.StreamHandler()]
except OSError:
    TOKEN_PATH = TGW_ROOT / 'secrets' / 'ebay-token.json'
    SANDBOX_TOKEN_PATH = TGW_ROOT / 'secrets' / 'ebay-sandbox-token.json'
    _log_handlers = [logging.StreamHandler()]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

def get_ebay_config(
    is_sandbox: bool | None = None,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve credentials and endpoints from the exact environment selector.

    ``None`` follows ``ebay_environment`` in the main config; absent config
    remains production. An explicit boolean is retained for the OAuth CLI.
    """
    if config is None:
        raw = _load_raw_config()
        environment = normalize_ebay_environment(
            raw.get('ebay_environment') if is_sandbox is None
            else ('sandbox' if is_sandbox else 'production')
        )
        secrets_root = Path(raw.get('secrets_root', '/opt/TGW/secrets'))
        creds_path = secrets_root / 'ebay-credentials.json'
        token_path = _token_path(environment == 'sandbox')
    else:
        environment = configured_ebay_environment(config)
        if is_sandbox is not None and (environment == 'sandbox') != is_sandbox:
            raise ValueError(
                'explicit OAuth environment differs from bound worker configuration'
            )
        raw = config.get('raw', {})
        raw = raw if isinstance(raw, dict) else {}
        secrets_root = Path(config.get(
            'secrets_root', raw.get('secrets_root', '/opt/TGW/secrets')
        ))
        creds_path = Path(config.get(
            'ebay_credentials_path', secrets_root / 'ebay-credentials.json'
        ))
        token_path = Path(config.get(
            'ebay_token_path',
            secrets_root / ebay_environment_settings(environment)['token_filename'],
        ))
        expected_name = ebay_environment_settings(environment)['token_filename']
        if token_path.name != expected_name:
            raise ValueError(
                f'bound {environment} token path must end in {expected_name}'
            )
    if not creds_path.exists():
        raise FileNotFoundError(f'eBay credentials not found: {creds_path}')
    creds = json.loads(creds_path.read_text())
    is_sandbox = environment == 'sandbox'
    prefix = 'sandbox_' if is_sandbox else ''
    app_id  = creds.get(f'{prefix}app_id')
    cert_id = creds.get(f'{prefix}cert_id')
    if not app_id or not cert_id:
        raise ValueError(f'Missing {prefix}app_id/cert_id in {creds_path}')
    settings = ebay_environment_settings(environment)
    oauth = ebay_oauth_settings(raw, environment)
    return {
        'environment': environment,
        'api_root_ebay': settings['rest_api_root'],
        'token_path': token_path,
        'app_id':  app_id,
        'cert_id': cert_id,
        'scopes': oauth['scopes'],
    }

def _token_path(is_sandbox: bool = False) -> Path:
    return SANDBOX_TOKEN_PATH if is_sandbox else TOKEN_PATH


def load_token_state(is_sandbox: bool = False) -> Dict[str, Any]:
    environment = 'sandbox' if is_sandbox else 'production'
    return _load_token_state_path(_token_path(is_sandbox), environment)


def _load_token_state_path(
    token_path: Path,
    environment: str,
) -> Dict[str, Any]:
    if not token_path.exists():
        raise ValueError(f'No token state at {token_path}; run get_access_token first')
    with open(token_path) as f:
        state = json.load(f)
    validate_token_environment(state, environment)
    return state

def save_token_state(state: Dict[str, Any], is_sandbox: bool = False) -> None:
    # audit#1143 #1162+#1177: atomic tmp+rename — TOKEN_PATH is the sole
    # copy of the eBay refresh token; a partial write (crash/kill mid-write)
    # corrupts it and forces full browser re-consent.
    environment = 'sandbox' if is_sandbox else 'production'
    persisted = stamp_token_environment(state, environment)
    atomic_write_token_json(
        _token_path(is_sandbox),
        json.dumps(persisted, indent=2) + '\n',
    )


def _save_token_state_path(
    state: Dict[str, Any],
    token_path: Path,
    environment: str,
) -> None:
    persisted = stamp_token_environment(state, environment)
    atomic_write_token_json(token_path, json.dumps(persisted, indent=2) + '\n')

def is_token_expired(state: Dict[str, Any]) -> bool:
    expiry = state.get('expiry', 0)
    return time.time() >= expiry - 300  # 5min buffer

def refresh_access_token(
    force: bool = False,
    is_sandbox: bool | None = None,
    config: Dict[str, Any] | None = None,
) -> str:
    """Refresh the eBay access token.

    When force=True the internal expiry guard is bypassed — the caller is
    responsible for deciding whether a refresh is needed.  The worker
    (token_refresh.py) uses force=True because it owns the scheduling
    decision; using the internal guard there would create a double-buffer
    bug that delays the actual eBay call until the last 5 minutes of
    token life instead of the intended 30-minute window.

    is_sandbox=None (default) follows ``ebay_environment`` in main config.
    """
    ebay_config = get_ebay_config(is_sandbox=is_sandbox, config=config)
    selected_sandbox = ebay_config['environment'] == 'sandbox'
    state = (
        _load_token_state_path(
            ebay_config['token_path'],
            ebay_config['environment'],
        )
        if config is not None else load_token_state(selected_sandbox)
    )

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
    if config is not None:
        _save_token_state_path(
            state,
            ebay_config['token_path'],
            ebay_config['environment'],
        )
    else:
        save_token_state(state, selected_sandbox)
    logger.info("Refreshed")
    return state['access_token']

def self_test():
    try:
        ebay_config = get_ebay_config()
        logger.info(
            "Paths OK: config=%s, token=%s",
            TGW_CONFIG_PATH,
            _token_path(ebay_config['environment'] == 'sandbox'),
        )
        logger.info(f"eBay OK: {ebay_config['api_root_ebay'][:21]}...")
        token = refresh_access_token()
        logger.info("PASS: token is available (%d bytes)", len(token))
    except Exception as e:
        logger.error(f"FAIL: {e}")

if __name__ == '__main__':
    self_test()
