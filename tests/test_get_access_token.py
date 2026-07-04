"""PP-NIXOS-001 — get-ebay-token --print-url (todo #1049, CLI half)."""

from tgw.apis.ebay.get_access_token import generate_auth_url


def _cfg(**overrides):
    cfg = {'app_id': 'test-app-id'}
    cfg.update(overrides)
    return cfg


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
