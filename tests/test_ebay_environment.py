"""Focused offline checks for production/sandbox eBay isolation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tgw.apis.ebay import client, get_access_token, refresh_access_token, trading
from tgw.config import load_config, normalize_ebay_environment


class _Response:
    status_code = 200
    content = b'<Ack>Success</Ack>'
    text = '<Ack xmlns="urn:ebay:apis:eBLBaseComponents">Success</Ack>'

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {'ok': True}


def _config(tmp_path: Path, environment: str | None = None):
    secrets_root = tmp_path / 'secrets'
    raw = {
        'secrets_root': str(secrets_root),
        # These must never override the closed eBay endpoint set.
        'ebay_rest_api_root': 'https://not-ebay.invalid',
        'ebay_trading_api_endpoint': 'https://not-ebay.invalid/ws/api.dll',
    }
    if environment is not None:
        raw['ebay_environment'] = environment
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    return load_config(path)


def test_production_is_default_and_retains_existing_token_name(tmp_path):
    cfg = _config(tmp_path)
    assert cfg['ebay_environment'] == 'production'
    assert cfg['ebay_rest_api_root'] == 'https://api.ebay.com'
    assert cfg['ebay_trading_api_endpoint'] == 'https://api.ebay.com/ws/api.dll'
    assert cfg['ebay_token_path'].name == 'ebay-token.json'


def test_sandbox_config_selects_only_fixed_roots_and_separate_token(tmp_path):
    cfg = _config(tmp_path, 'sandbox')
    assert cfg['ebay_environment'] == 'sandbox'
    assert cfg['ebay_rest_api_root'] == 'https://api.sandbox.ebay.com'
    assert cfg['ebay_auth_root'] == 'https://auth.sandbox.ebay.com'
    assert cfg['ebay_trading_api_endpoint'] == 'https://api.sandbox.ebay.com/ws/api.dll'
    assert cfg['ebay_token_path'].name == 'ebay-sandbox-token.json'
    assert cfg['ebay_token_path'] != cfg['ebay_production_token_path']


@pytest.mark.parametrize('value', ['', 'staging', 'https://api.ebay.com', True])
def test_environment_selector_rejects_non_exact_values(value):
    with pytest.raises(ValueError, match='exactly'):
        normalize_ebay_environment(value)


@pytest.mark.parametrize(
    ('environment', 'expected'),
    [
        ('production', 'https://api.ebay.com/sell/inventory/v1/inventory_item/SKU'),
        ('sandbox', 'https://api.sandbox.ebay.com/sell/inventory/v1/inventory_item/SKU'),
    ],
)
def test_rest_client_uses_closed_environment_root(environment, expected, monkeypatch):
    seen = []
    monkeypatch.setattr(client.quota, 'precheck', lambda *_args: None)
    monkeypatch.setattr(client.quota, 'record', lambda *_args: None)
    monkeypatch.setattr(client, 'capture_response', lambda *_args: None)
    monkeypatch.setattr(client._SESSION, 'get', lambda url, **_kwargs: seen.append(url) or _Response())

    client._counted(
        {
            'ebay_environment': environment,
            'ebay_rest_api_root': 'https://not-ebay.invalid',
            'raw': {'ebay_environment': environment, 'ebay_rest_api_root': 'https://also.invalid'},
        },
        'get',
        '/sell/inventory/v1/inventory_item/SKU',
    )
    assert seen == [expected]


def test_sandbox_client_reads_sandbox_token_never_production(tmp_path):
    cfg = _config(tmp_path, 'sandbox')
    cfg['secrets_root'].mkdir(parents=True)
    future = time.time() + 3600
    cfg['ebay_production_token_path'].write_text(
        json.dumps({'access_token': 'PRODUCTION', 'expiry': future}), encoding='utf-8'
    )
    cfg['ebay_sandbox_token_path'].write_text(
        json.dumps({
            'access_token': 'SANDBOX',
            'expiry': future,
            '_tgw_ebay_environment': 'sandbox',
        }),
        encoding='utf-8',
    )
    assert client.load_token(cfg) == 'SANDBOX'

    with pytest.raises(ValueError, match='ebay-sandbox-token.json'):
        client.load_token({
            'ebay_environment': 'sandbox',
            'ebay_token_path': cfg['ebay_production_token_path'],
        })


def test_token_markers_fail_closed_for_sandbox_and_cross_environment(tmp_path):
    cfg = _config(tmp_path, 'sandbox')
    cfg['secrets_root'].mkdir(parents=True)
    future = time.time() + 3600
    cfg['ebay_sandbox_token_path'].write_text(json.dumps({
        'access_token': 'UNMARKED',
        'expiry': future,
    }))
    with pytest.raises(ValueError, match='no environment marker'):
        client.load_token(cfg)

    cfg['ebay_sandbox_token_path'].write_text(json.dumps({
        'access_token': 'WRONG',
        'expiry': future,
        '_tgw_ebay_environment': 'production',
    }))
    with pytest.raises(ValueError, match='does not match'):
        client.load_token(cfg)

    production_root = tmp_path / 'production'
    production_root.mkdir()
    production = _config(production_root, 'production')
    production['secrets_root'].mkdir(parents=True)
    production['ebay_token_path'].write_text(json.dumps({
        'access_token': 'LEGACY-PRODUCTION',
        'expiry': future,
    }))
    assert client.load_token(production) == 'LEGACY-PRODUCTION'


@pytest.mark.parametrize(
    ('environment', 'expected'),
    [
        ('production', 'https://api.ebay.com/ws/api.dll'),
        ('sandbox', 'https://api.sandbox.ebay.com/ws/api.dll'),
    ],
)
def test_trading_client_uses_closed_environment_endpoint(environment, expected, monkeypatch):
    seen = []
    monkeypatch.setattr(trading, 'load_token', lambda _cfg: 'TOKEN')
    monkeypatch.setattr(trading.quota, 'precheck', lambda *_args: None)
    monkeypatch.setattr(trading.quota, 'record', lambda *_args: None)
    monkeypatch.setattr(trading, 'capture_response', lambda *_args: None)
    monkeypatch.setattr(
        trading._SESSION,
        'post',
        lambda url, **_kwargs: seen.append(url) or _Response(),
    )

    trading.trading_call(
        {
            'ebay_environment': environment,
            'ebay_trading_api_endpoint': 'https://not-ebay.invalid/ws/api.dll',
        },
        'GetAPIAccessRules',
        '<GetAPIAccessRulesRequest xmlns="urn:ebay:apis:eBLBaseComponents"/>',
    )
    assert seen == [expected]


def test_oauth_state_marker_keeps_direct_sandbox_exchange_out_of_prod_token(tmp_path, monkeypatch):
    production = tmp_path / 'ebay-token.json'
    sandbox = tmp_path / 'ebay-sandbox-token.json'
    production.write_text('{"sentinel":"production"}', encoding='utf-8')
    monkeypatch.setattr(get_access_token, 'TOKEN_PATH', production)
    monkeypatch.setattr(get_access_token, 'SANDBOX_TOKEN_PATH', sandbox)

    get_access_token.save_token_state({
        'access_token': 'sandbox-token',
        '_tgw_ebay_environment': 'sandbox',
    })

    assert json.loads(production.read_text(encoding='utf-8')) == {'sentinel': 'production'}
    assert json.loads(sandbox.read_text(encoding='utf-8')) == {
        'access_token': 'sandbox-token',
        '_tgw_ebay_environment': 'sandbox',
    }


def test_config_driven_get_access_token_reads_sandbox_state_never_prod(tmp_path, monkeypatch):
    production = tmp_path / 'ebay-token.json'
    sandbox = tmp_path / 'ebay-sandbox-token.json'
    future = time.time() + 3600
    production.write_text(json.dumps({
        'access_token': 'production-token', 'expiry': future,
    }), encoding='utf-8')
    sandbox.write_text(json.dumps({
        'access_token': 'sandbox-token', 'expiry': future,
        '_tgw_ebay_environment': 'sandbox',
    }), encoding='utf-8')
    monkeypatch.setattr(get_access_token, 'TOKEN_PATH', production)
    monkeypatch.setattr(get_access_token, 'SANDBOX_TOKEN_PATH', sandbox)
    monkeypatch.setattr(
        get_access_token,
        'load_config',
        lambda is_sandbox=None, config=None: {
            'environment': 'sandbox',
            'token_path': sandbox,
        },
    )

    assert get_access_token.get_access_token(prompt_if_needed=False) == 'sandbox-token'


def test_get_access_token_uses_exact_loaded_config_paths(tmp_path, monkeypatch):
    cfg = _config(tmp_path, 'sandbox')
    cfg['secrets_root'].mkdir(parents=True)
    cfg['ebay_credentials_path'].write_text(json.dumps({
        'app_id': 'production-app',
        'cert_id': 'production-cert',
        'sandbox_app_id': 'sandbox-app',
        'sandbox_cert_id': 'sandbox-cert',
    }))
    cfg['ebay_sandbox_token_path'].write_text(json.dumps({
        'access_token': 'exact-sandbox-token',
        'refresh_token': 'sandbox-refresh',
        'expiry': time.time() + 3600,
        '_tgw_ebay_environment': 'sandbox',
    }))
    monkeypatch.setattr(
        get_access_token,
        'TOKEN_PATH',
        tmp_path / 'wrong-global' / 'ebay-token.json',
    )
    monkeypatch.setattr(
        get_access_token,
        'SANDBOX_TOKEN_PATH',
        tmp_path / 'wrong-global' / 'ebay-sandbox-token.json',
    )

    loaded = get_access_token.load_config(config=cfg)
    assert loaded['token_path'] == cfg['ebay_sandbox_token_path']
    assert loaded['credentials_path'] == cfg['ebay_credentials_path']
    assert get_access_token.get_access_token(
        prompt_if_needed=False,
        config=cfg,
    ) == 'exact-sandbox-token'


def test_config_driven_sandbox_refresh_uses_sandbox_token_and_endpoint(tmp_path, monkeypatch):
    secrets = tmp_path / 'secrets'
    secrets.mkdir()
    production = secrets / 'ebay-token.json'
    sandbox = secrets / 'ebay-sandbox-token.json'
    production.write_text('{"sentinel":"production"}', encoding='utf-8')
    sandbox.write_text(json.dumps({
        'access_token': 'old-sandbox', 'refresh_token': 'sandbox-refresh', 'expiry': 0,
        '_tgw_ebay_environment': 'sandbox',
    }), encoding='utf-8')
    (secrets / 'ebay-credentials.json').write_text(json.dumps({
        'app_id': 'prod-app', 'cert_id': 'prod-cert',
        'sandbox_app_id': 'sandbox-app', 'sandbox_cert_id': 'sandbox-cert',
    }), encoding='utf-8')
    monkeypatch.setattr(refresh_access_token, '_load_raw_config', lambda: {
        'secrets_root': str(secrets), 'ebay_environment': 'sandbox', 'ebay': {},
    })
    monkeypatch.setattr(refresh_access_token, 'TOKEN_PATH', production)
    monkeypatch.setattr(refresh_access_token, 'SANDBOX_TOKEN_PATH', sandbox)
    response = _Response()
    response.json = lambda: {'access_token': 'new-sandbox', 'expires_in': 7200}

    with patch.object(refresh_access_token.requests, 'post', return_value=response) as request:
        assert refresh_access_token.refresh_access_token(force=True) == 'new-sandbox'

    assert request.call_args.args[0] == 'https://api.sandbox.ebay.com/identity/v1/oauth2/token'
    assert json.loads(production.read_text(encoding='utf-8')) == {'sentinel': 'production'}
    assert json.loads(sandbox.read_text(encoding='utf-8'))['access_token'] == 'new-sandbox'
