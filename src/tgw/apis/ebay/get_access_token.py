#!/usr/bin/env python3
"""
TGW eBay Token Manager: get_access_token v1
Generates initial user access/refresh tokens via OAuth flow.
Dynamic paths from tgw-api-config.json, shares logic with refresh_v1.
"""

from __future__ import annotations

import json
import logging
import os
import time
import webbrowser
from base64 import b64encode
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from tgw.apis.ebay._token_io import (
    TOKEN_ENVIRONMENT_KEY,
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

TGW_ROOT   = Path(os.getenv('TGW_ROOT', '/opt/TGW'))
CONFIG_PATH = TGW_ROOT / 'config' / 'tgw-api-config.json'

def _load_raw_config() -> Dict[str, Any]:
    with open(CONFIG_PATH) as f:
        return json.load(f)

def _secrets_root() -> Path:
    return Path(_load_raw_config().get('secrets_root', '/opt/TGW/secrets'))

try:
    # CI/portability fix: this all used to run unconditionally at import
    # time, so merely importing this module (e.g. this session's own new
    # tests/test_get_access_token.py additions, or any CI runner without
    # /opt/TGW) crashed with FileNotFoundError before a single test could
    # run — every CI run on main/PRs since 2026-06-15 failed collection on
    # this exact line. Fall back to a harmless stream-only-logging default
    # when the real config isn't present; any function that actually
    # reads/writes TOKEN_PATH still surfaces a clear error at call time if
    # truly unconfigured, rather than crashing on mere import.
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

def _validated_token_path(token_path: Path, environment: str) -> Path:
    expected_name = ebay_environment_settings(environment)['token_filename']
    if token_path.name != expected_name:
        raise ValueError(
            f'bound {environment} token path must end in {expected_name}'
        )
    return token_path


def _refresh_config_binding(
    config: Dict[str, Any],
    ebay_config: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the exact refresh inputs without copying unrelated secrets."""
    raw_value = config.get('raw', {})
    raw = raw_value if isinstance(raw_value, dict) else {}
    raw_ebay = raw.get('ebay', {})
    if not isinstance(raw_ebay, dict):
        raw_ebay = {}
    return {
        'ebay_environment': ebay_config['environment'],
        'secrets_root': Path(config.get(
            'secrets_root', ebay_config['credentials_path'].parent
        )),
        'ebay_credentials_path': ebay_config['credentials_path'],
        'ebay_token_path': ebay_config['token_path'],
        'raw': {'ebay': raw_ebay},
    }


def load_config(
    is_sandbox: bool | None = None,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Load eBay non-secret config (redirect_uri, scopes) from main config.

    audit#1143 #1238: previously always returned production app_id/cert_id
    regardless of is_sandbox — a sandbox OAuth run would silently
    authenticate with production eBay credentials. Mirrors
    refresh_access_token.py's get_ebay_config(), which already applies this
    sandbox_ prefix correctly.
    """
    if config is None:
        raw = _load_raw_config()
        environment = normalize_ebay_environment(
            raw.get('ebay_environment') if is_sandbox is None
            else ('sandbox' if is_sandbox else 'production')
        )
        secrets_root = _secrets_root()
        creds_path = secrets_root / 'ebay-credentials.json'
        token_path = _validated_token_path(
            _token_path(environment == 'sandbox'),
            environment,
        )
    else:
        raw_value = config.get('raw', {})
        raw = raw_value if isinstance(raw_value, dict) else {}
        configured_environment = configured_ebay_environment(config)
        environment = normalize_ebay_environment(
            configured_environment if is_sandbox is None
            else ('sandbox' if is_sandbox else 'production')
        )
        secrets_root = Path(config.get(
            'secrets_root', raw.get('secrets_root', '/opt/TGW/secrets')
        ))
        creds_path = Path(config.get(
            'ebay_credentials_path', secrets_root / 'ebay-credentials.json'
        ))
        environment_path_key = f'ebay_{environment}_token_path'
        token_path_value = config.get(environment_path_key)
        if token_path_value is None and configured_environment == environment:
            token_path_value = config.get('ebay_token_path')
        if token_path_value is None:
            token_path_value = (
                secrets_root
                / ebay_environment_settings(environment)['token_filename']
            )
        token_path = _validated_token_path(Path(token_path_value), environment)
    selected_sandbox = environment == 'sandbox'
    if not creds_path.exists():
        raise FileNotFoundError(f'eBay credentials not found: {creds_path}')
    creds = json.loads(creds_path.read_text())
    prefix = 'sandbox_' if selected_sandbox else ''
    app_id = creds.get(f'{prefix}app_id')
    cert_id = creds.get(f'{prefix}cert_id')
    if not app_id or not cert_id:
        raise ValueError(f'Missing {prefix}app_id/{prefix}cert_id in {creds_path}')
    oauth = ebay_oauth_settings(raw, environment)
    return {
        'app_id': app_id,
        'cert_id': cert_id,
        'environment': environment,
        'redirect_uri': oauth['redirect_uri'],
        'ru_name': oauth['ru_name'],
        'scopes': oauth['scopes'],
        'credentials_path': creds_path,
        'token_path': token_path,
        **ebay_environment_settings(environment),
    }

def _token_path(is_sandbox: bool = False) -> Path:
    return SANDBOX_TOKEN_PATH if is_sandbox else TOKEN_PATH


def load_token_state(
    is_sandbox: bool = False,
    *,
    token_path: Path | None = None,
    environment: str | None = None,
) -> Dict[str, Any]:
    selected_environment = normalize_ebay_environment(
        environment if environment is not None
        else ('sandbox' if is_sandbox else 'production')
    )
    token_path = _validated_token_path(
        Path(token_path) if token_path is not None
        else _token_path(selected_environment == 'sandbox'),
        selected_environment,
    )
    if token_path.exists():
        with open(token_path) as f:
            state = json.load(f)
        validate_token_environment(state, selected_environment)
        return state
    return {
        'access_token': '',
        'refresh_token': '',
        'expiry': 0,
        TOKEN_ENVIRONMENT_KEY: selected_environment,
    }

def save_token_state(
    state: Dict[str, Any],
    is_sandbox: bool | None = None,
    *,
    token_path: Path | None = None,
    environment: str | None = None,
) -> None:
    # audit#1143 #1162+#1177: atomic tmp+rename — TOKEN_PATH is the sole
    # copy of the eBay refresh token; a partial write (crash/kill mid-write)
    # corrupts it and forces full browser re-consent.
    state_environment = state.get(TOKEN_ENVIRONMENT_KEY)
    if environment is None:
        if is_sandbox is not None:
            environment = 'sandbox' if is_sandbox else 'production'
        elif state_environment is not None:
            environment = normalize_ebay_environment(state_environment)
        else:
            environment = 'production'
    environment = normalize_ebay_environment(environment)
    if is_sandbox is not None and (environment == 'sandbox') != is_sandbox:
        raise ValueError('OAuth environment flag differs from token environment')
    token_path = _validated_token_path(
        Path(token_path) if token_path is not None
        else _token_path(environment == 'sandbox'),
        environment,
    )
    persisted = stamp_token_environment(state, environment)
    atomic_write_token_json(token_path, json.dumps(persisted, indent=2) + '\n')
    logger.info('State saved: %s', token_path)

def is_token_expired(state: Dict[str, Any]) -> bool:
    return time.time() >= state.get('expiry', 0)

def get_ebay_base(is_sandbox: bool = False) -> str:
    environment = 'sandbox' if is_sandbox else 'production'
    return ebay_environment_settings(environment)['rest_api_root']

def get_auth_base(is_sandbox: bool = False) -> str:
    environment = 'sandbox' if is_sandbox else 'production'
    return ebay_environment_settings(environment)['auth_root']


def _selected_oauth_environment(
    ebay_config: Dict[str, Any], is_sandbox: bool,
) -> str:
    selected = 'sandbox' if is_sandbox else 'production'
    configured = ebay_config.get('environment')
    if configured is not None and normalize_ebay_environment(configured) != selected:
        raise ValueError('OAuth environment flag differs from loaded OAuth configuration')
    return selected

def generate_auth_url(ebay_config: Dict[str, Any], is_sandbox: bool = False) -> str:
    environment = _selected_oauth_environment(ebay_config, is_sandbox)
    client_id = ebay_config['app_id']
    scopes = ebay_config.get('scopes', 'https://api.ebay.com/oauth/api_scope https://api.ebay.com/oauth/api_scope/sell.account https://api.ebay.com/oauth/api_scope/sell.inventory https://api.ebay.com/oauth/api_scope/sell.fulfillment')
    redirect_uri = ebay_config.get('redirect_uri', 'http://localhost')
    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': scopes
    }
    url = (
        f"{ebay_environment_settings(environment)['auth_root']}"
        f"/oauth2/authorize?{urlencode(params)}"
    )
    logger.info(f"Auth URL: {url}")
    return url

def exchange_code_for_tokens(code: str, ebay_config: Dict[str, Any], is_sandbox: bool = False) -> Dict[str, Any]:
    environment = _selected_oauth_environment(ebay_config, is_sandbox)
    creds = f"{ebay_config['app_id']}:{ebay_config['cert_id']}"
    auth_header = f"Basic {b64encode(creds.encode()).decode()}"
    data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': ebay_config.get('redirect_uri', 'http://localhost')
    }
    resp = requests.post(
        f"{ebay_environment_settings(environment)['rest_api_root']}"
        "/identity/v1/oauth2/token",
        headers={'Authorization': auth_header, 'Content-Type': 'application/x-www-form-urlencoded'},
        data=data
    )
    resp.raise_for_status()
    tokens = resp.json()
    tokens['expiry'] = time.time() + tokens['expires_in']
    # Preserve the endpoint choice until save_token_state(), including the
    # direct-code CLI path whose historical caller does not pass the flag on.
    tokens['_tgw_ebay_environment'] = environment
    return tokens

def get_access_token(
    prompt_if_needed: bool = True,
    is_sandbox: bool | None = None,
    config: Dict[str, Any] | None = None,
) -> str:
    """Get valid token: auto-refresh if possible, browser only for initial consent."""
    ebay_config = load_config(is_sandbox=is_sandbox, config=config)
    selected_sandbox = ebay_config['environment'] == 'sandbox'
    state = load_token_state(
        selected_sandbox,
        token_path=ebay_config['token_path'],
        environment=ebay_config['environment'],
    )

    # Fast path: valid token exists
    if not is_token_expired(state) and state.get('access_token'):
        logger.info("Valid token found.")
        return state['access_token']

    # Auto-refresh if refresh_token exists (99% cases, NO BROWSER)
    if state.get('refresh_token'):
        try:
            # audit#1143 #1238: previously imported a nonexistent module, so
            # this branch always raised and silently fell through to the
            # manual browser+paste flow even with a valid refresh_token.
            # audit#1143 #1211-followup: originally bridged is_sandbox into
            # refresh_access_token() via a process-global EBAY_ENV mutation
            # that was never restored (would leak into unrelated later
            # calls in the same process). refresh_access_token() now takes
            # is_sandbox directly, same as load_config() above.
            from tgw.apis.ebay.refresh_access_token import refresh_access_token
            refresh_kwargs: Dict[str, Any] = {
                'force': True,
                'is_sandbox': selected_sandbox,
            }
            if config is not None:
                refresh_kwargs['config'] = _refresh_config_binding(
                    config,
                    ebay_config,
                )
            refreshed = refresh_access_token(**refresh_kwargs)
            logger.info("Auto-refreshed token - no browser needed.")
            return refreshed
        except Exception as e:
            logger.warning("Refresh failed (%s), falling back to prompt.", e)
    # True first-time: browser + consent (once)
    if prompt_if_needed:
        logger.info("Initial OAuth needed (no refresh_token).")
        auth_url = generate_auth_url(ebay_config, selected_sandbox)
        import subprocess as _sp
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            # Running via sudo — tgw cannot open the calling user's display.
            # Print the URL; it's clickable in most terminal emulators.
            print()
            print('=' * 60)
            print('  eBay OAuth — open this URL in your browser:')
            print(f'  {auth_url}')
            print('  After eBay redirects, COPY the full URL from the browser')
            print('  address bar and PASTE IT HERE (in THIS terminal window).')
            print('=' * 60)
        else:
            try:
                _sp.Popen(['xdg-open', auth_url],
                          stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except FileNotFoundError:
                webbrowser.open(auth_url)
            print()
            print('=' * 60)
            print('  eBay OAuth: browser opened. Complete login + consent.')
            print('  After eBay redirects, COPY the full URL from the browser')
            print('  address bar and PASTE IT HERE (in THIS terminal window).')
            print('=' * 60)
        full_url = input('  Paste full redirect URL here → ').strip()
        print('=' * 60)
        parsed = urlparse(full_url)
        code = parse_qs(parsed.query).get('code', [None])[0]
        if not code:
            raise ValueError("No code=... found in URL — did you paste the right URL?")
        tokens = exchange_code_for_tokens(code, ebay_config, selected_sandbox)
        save_token_state(
            tokens,
            selected_sandbox,
            token_path=ebay_config['token_path'],
            environment=ebay_config['environment'],
        )
        logger.info("Token saved.")
        return tokens['access_token']

    raise RuntimeError("No valid token and prompt disabled.")

def self_test(is_sandbox: bool | None = None):
    try:
        ebay_config = load_config(is_sandbox=is_sandbox)
        logger.info("Paths OK: config=%s, token=%s", CONFIG_PATH, ebay_config['token_path'])
        token = get_access_token(is_sandbox=is_sandbox)
        logger.info("SUCCESS: token is available (%d bytes)", len(token))
    except Exception as e:
        logger.error("FAIL: %s", e)
        raise

if __name__ == '__main__':
    self_test()
