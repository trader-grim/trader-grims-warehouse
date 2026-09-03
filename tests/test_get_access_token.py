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
        monkeypatch.setattr(get_access_token, 'load_token_state', lambda _sandbox=False, **_kwargs: {
            'access_token': '', 'refresh_token': 'rt-123', 'expiry': 0,
        })
        with patch('tgw.apis.ebay.refresh_access_token.refresh_access_token', return_value='new-token') as mock_refresh:
            with patch.object(get_access_token, 'webbrowser'):
                token = get_access_token.get_access_token(prompt_if_needed=False)
        assert token == 'new-token'
        mock_refresh.assert_called_once_with(force=True, is_sandbox=False)

    def test_sandbox_intent_passed_as_parameter_not_env_mutation(self, _creds, monkeypatch):
        # Regression: this used to bridge is_sandbox via a process-global
        # os.environ['EBAY_ENV'] mutation that was never restored, leaking
        # into any later call in the same process. Confirm is_sandbox now
        # goes straight through as a parameter, and EBAY_ENV is left alone.
        monkeypatch.delenv('EBAY_ENV', raising=False)
        monkeypatch.setattr(get_access_token, 'load_token_state', lambda _sandbox=False, **_kwargs: {
            'access_token': '', 'refresh_token': 'rt-123', 'expiry': 0,
        })
        with patch('tgw.apis.ebay.refresh_access_token.refresh_access_token', return_value='new-token') as mock_refresh:
            get_access_token.get_access_token(prompt_if_needed=False, is_sandbox=True)
        mock_refresh.assert_called_once_with(force=True, is_sandbox=True)
        assert 'EBAY_ENV' not in get_access_token.os.environ

    def test_exact_config_refresh_binding_excludes_unrelated_secrets(
        self,
        _creds,
        tmp_path,
        monkeypatch,
    ):
        token_path = _creds / 'ebay-sandbox-token.json'
        token_path.write_text(json.dumps({
            'access_token': '',
            'refresh_token': 'sandbox-refresh',
            'expiry': 0,
            '_tgw_ebay_environment': 'sandbox',
        }))
        cfg = {
            'ebay_environment': 'sandbox',
            'secrets_root': _creds,
            'ebay_credentials_path': _creds / 'ebay-credentials.json',
            'ebay_token_path': token_path,
            'ebay_sandbox_token_path': token_path,
            'unrelated_secret': 'must-not-cross-bootstrap-boundary',
            'raw': {
                'ebay_environment': 'sandbox',
                'ebay': {'oauth': {'sandbox': {'scopes': 'sandbox-scope'}}},
                'unrelated_secret': 'must-not-cross-bootstrap-boundary',
            },
        }
        with patch(
            'tgw.apis.ebay.refresh_access_token.refresh_access_token',
            return_value='new-token',
        ) as mock_refresh:
            assert get_access_token.get_access_token(
                prompt_if_needed=False,
                config=cfg,
            ) == 'new-token'

        refresh_config = mock_refresh.call_args.kwargs['config']
        assert refresh_config['ebay_token_path'] == token_path
        assert refresh_config['ebay_credentials_path'] == cfg['ebay_credentials_path']
        assert refresh_config['ebay_environment'] == 'sandbox'
        assert 'unrelated_secret' not in refresh_config
        assert 'unrelated_secret' not in refresh_config['raw']


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


def test_cli_bootstrap_threads_loaded_config_and_does_not_print_token(
    tmp_path,
    monkeypatch,
    capsys,
):
    from tgw import api

    exact_config = {"config_identity": "exact-loaded-config"}
    exact_token_path = tmp_path / "ebay-sandbox-token.json"
    loaded = {
        "app_id": "sandbox-app",
        "cert_id": "sandbox-cert",
        "environment": "sandbox",
        "token_path": exact_token_path,
    }
    seen = {}
    monkeypatch.setattr("sys.argv", ["tgw", "get-ebay-token", "--code", "AUTH-CODE"])
    monkeypatch.setattr(api, "load_config", lambda _path: exact_config)
    monkeypatch.setattr(
        get_access_token,
        "load_config",
        lambda **kwargs: seen.update({"load": kwargs}) or loaded,
    )
    monkeypatch.setattr(
        get_access_token,
        "exchange_code_for_tokens",
        lambda code, cfg, is_sandbox=False: (
            seen.update({"exchange": (code, cfg, is_sandbox)})
            or {
                "access_token": "SUPER-SECRET-ACCESS-TOKEN",
                "expiry": 9999999999,
                "_tgw_ebay_environment": "sandbox",
            }
        ),
    )
    monkeypatch.setattr(
        get_access_token,
        "save_token_state",
        lambda state, is_sandbox=None, **kwargs: seen.update({
            "save": (state, is_sandbox, kwargs),
        }),
    )

    assert api.main() == 0
    output = capsys.readouterr().out
    assert seen["load"] == {"is_sandbox": None, "config": exact_config}
    assert seen["exchange"] == ("AUTH-CODE", loaded, True)
    assert seen["save"][1:] == (
        True,
        {"token_path": exact_token_path, "environment": "sandbox"},
    )
    assert "SUPER-SECRET-ACCESS-TOKEN" not in output
    assert '"environment": "sandbox"' in output
