"""PP-NIXOS-001 — get-ebay-token --print-url (todo #1049, CLI half).

audit#1143 #1238: load_config() previously always returned production
app_id/cert_id regardless of is_sandbox, and the auto-refresh path in
get_access_token() imported a nonexistent module (always fell back to the
manual browser+paste flow even with a valid refresh_token). Both covered
below.
"""

import json
from unittest.mock import patch

import pytest

from tgw.apis.ebay import get_access_token
from tgw.apis.ebay.get_access_token import generate_auth_url


def _cfg(**overrides):
    cfg = {'app_id': 'test-app-id'}
    cfg.update(overrides)
    return cfg


@pytest.fixture
def _creds(tmp_path, monkeypatch):
    """Point load_config() at a fake secrets_root + config with both
    production and sandbox_-prefixed credential keys."""
    secrets_root = tmp_path / 'secrets'
    secrets_root.mkdir()
    (secrets_root / 'ebay-credentials.json').write_text(json.dumps({
        'app_id': 'prod-app-id',
        'cert_id': 'prod-cert-id',
        'sandbox_app_id': 'sbx-app-id',
        'sandbox_cert_id': 'sbx-cert-id',
    }))
    monkeypatch.setattr(get_access_token, '_load_raw_config', lambda: {'ebay': {'scopes': 'x'}})
    monkeypatch.setattr(get_access_token, '_secrets_root', lambda: secrets_root)
    return secrets_root


class TestLoadConfigSandboxPrefix:
    def test_production_by_default(self, _creds):
        cfg = get_access_token.load_config()
        assert cfg['app_id'] == 'prod-app-id'
        assert cfg['cert_id'] == 'prod-cert-id'

    def test_sandbox_selects_prefixed_keys(self, _creds):
        cfg = get_access_token.load_config(is_sandbox=True)
        assert cfg['app_id'] == 'sbx-app-id'
        assert cfg['cert_id'] == 'sbx-cert-id'

    def test_sandbox_raises_clearly_when_prefixed_keys_missing(self, tmp_path, monkeypatch):
        secrets_root = tmp_path / 'secrets'
        secrets_root.mkdir()
        (secrets_root / 'ebay-credentials.json').write_text(json.dumps({
            'app_id': 'prod-app-id',
            'cert_id': 'prod-cert-id',
        }))
        monkeypatch.setattr(get_access_token, '_load_raw_config', lambda: {'ebay': {}})
        monkeypatch.setattr(get_access_token, '_secrets_root', lambda: secrets_root)
        with pytest.raises(ValueError, match='sandbox_app_id'):
            get_access_token.load_config(is_sandbox=True)


class TestGetAccessTokenAutoRefresh:
    def test_valid_refresh_token_triggers_real_refresh_no_browser(self, _creds, monkeypatch):
        monkeypatch.setattr(get_access_token, 'load_token_state', lambda: {
            'access_token': '', 'refresh_token': 'rt-123', 'expiry': 0,
        })
        with patch('tgw.apis.ebay.refresh_access_token.refresh_access_token', return_value='new-token') as mock_refresh:
            with patch.object(get_access_token, 'webbrowser'):
                token = get_access_token.get_access_token(prompt_if_needed=False)
        assert token == 'new-token'
        mock_refresh.assert_called_once_with(force=True)

    def test_sandbox_intent_bridged_to_ebay_env(self, _creds, monkeypatch):
        monkeypatch.setattr(get_access_token, 'load_token_state', lambda: {
            'access_token': '', 'refresh_token': 'rt-123', 'expiry': 0,
        })
        seen_env = {}

        def _fake_refresh(force=False):
            seen_env['EBAY_ENV'] = get_access_token.os.environ.get('EBAY_ENV')
            return 'new-token'

        with patch('tgw.apis.ebay.refresh_access_token.refresh_access_token', side_effect=_fake_refresh):
            get_access_token.get_access_token(prompt_if_needed=False, is_sandbox=True)
        assert seen_env['EBAY_ENV'] == 'sandbox'


def test_generate_auth_url_includes_client_id():
    url = generate_auth_url(_cfg())
    assert 'client_id=test-app-id' in url


def test_generate_auth_url_production_by_default():
    url = generate_auth_url(_cfg())
    assert 'sandbox' not in url


def test_generate_auth_url_sandbox_flag():
    url = generate_auth_url(_cfg(), is_sandbox=True)
    assert 'sandbox' in url


def test_generate_auth_url_uses_configured_redirect_uri():
    url = generate_auth_url(_cfg(redirect_uri='https://example.com/callback'))
    assert 'redirect_uri=https%3A%2F%2Fexample.com%2Fcallback' in url


def test_generate_auth_url_no_secrets_or_browser_needed():
    """--print-url's whole point: build the URL without any browser/secrets
    round-trip. Confirms generate_auth_url takes only the app_id/scope/
    redirect config, never touches the token store."""
    url = generate_auth_url(_cfg())
    assert url.startswith('https://')
    assert 'response_type=code' in url
