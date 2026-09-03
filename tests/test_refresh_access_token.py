"""audit#1143 #1211-followup — refresh_access_token.py's get_ebay_config()
gains an explicit is_sandbox parameter.

The default selection now follows the exact ``ebay_environment`` config value;
an absent value remains production and an explicit OAuth CLI flag still wins.
"""

import json
from unittest.mock import patch

import pytest

from tgw.apis.ebay import refresh_access_token as rat


@pytest.fixture
def _creds(tmp_path, monkeypatch):
    secrets_root = tmp_path / 'secrets'
    secrets_root.mkdir()
    (secrets_root / 'ebay-credentials.json').write_text(json.dumps({
        'app_id': 'prod-app-id',
        'cert_id': 'prod-cert-id',
        'sandbox_app_id': 'sbx-app-id',
        'sandbox_cert_id': 'sbx-cert-id',
    }))
    monkeypatch.setattr(rat, '_load_raw_config', lambda: {'secrets_root': str(secrets_root), 'ebay': {}})
    return secrets_root


class TestGetEbayConfigIsSandboxParameter:
    def test_is_sandbox_true_selects_prefixed_keys_regardless_of_env(self, _creds, monkeypatch):
        monkeypatch.setenv('EBAY_ENV', 'production')  # explicit param must win over env
        cfg = rat.get_ebay_config(is_sandbox=True)
        assert cfg['app_id'] == 'sbx-app-id'
        assert cfg['cert_id'] == 'sbx-cert-id'
        assert cfg['api_root_ebay'] == 'https://api.sandbox.ebay.com'

    def test_is_sandbox_false_selects_prod_keys_regardless_of_env(self, _creds, monkeypatch):
        monkeypatch.setenv('EBAY_ENV', 'sandbox')  # explicit param must win over env
        cfg = rat.get_ebay_config(is_sandbox=False)
        assert cfg['app_id'] == 'prod-app-id'
        assert cfg['api_root_ebay'] == 'https://api.ebay.com'

    def test_is_sandbox_none_follows_config_sandbox(self, _creds, monkeypatch):
        monkeypatch.setattr(rat, '_load_raw_config', lambda: {
            'secrets_root': str(_creds), 'ebay_environment': 'sandbox', 'ebay': {},
        })
        cfg = rat.get_ebay_config(is_sandbox=None)
        assert cfg['app_id'] == 'sbx-app-id'
        assert cfg['environment'] == 'sandbox'

    def test_is_sandbox_none_defaults_to_production(self, _creds, monkeypatch):
        cfg = rat.get_ebay_config(is_sandbox=None)
        assert cfg['app_id'] == 'prod-app-id'


class TestRefreshAccessTokenIsSandboxPassthrough:
    def test_default_call_unaffected_matches_token_refresh_worker_usage(self, _creds, monkeypatch):
        # token_refresh.py calls refresh_access_token(force=True) with no
        # is_sandbox; absent config remains production.
        monkeypatch.setattr(rat, 'load_token_state', lambda _sandbox=False: {
            'access_token': 'old', 'refresh_token': 'rt', 'expiry': 0,
        })
        monkeypatch.setattr(rat, 'save_token_state', lambda state, _sandbox=False: None)
        fake_resp = type('R', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: {'access_token': 'new', 'expires_in': 7200},
        })()
        with patch.object(rat.requests, 'post', return_value=fake_resp) as mock_post:
            token = rat.refresh_access_token(force=True)
        assert token == 'new'
        assert mock_post.call_args[0][0] == 'https://api.ebay.com/identity/v1/oauth2/token'

    def test_explicit_is_sandbox_overrides_env(self, _creds, monkeypatch):
        monkeypatch.setenv('EBAY_ENV', 'production')
        monkeypatch.setattr(rat, 'load_token_state', lambda _sandbox=False: {
            'access_token': 'old', 'refresh_token': 'rt', 'expiry': 0,
        })
        monkeypatch.setattr(rat, 'save_token_state', lambda state, _sandbox=False: None)
        fake_resp = type('R', (), {
            'raise_for_status': lambda self: None,
            'json': lambda self: {'access_token': 'new', 'expires_in': 7200},
        })()
        with patch.object(rat.requests, 'post', return_value=fake_resp) as mock_post:
            rat.refresh_access_token(force=True, is_sandbox=True)
        assert mock_post.call_args[0][0] == 'https://api.sandbox.ebay.com/identity/v1/oauth2/token'
