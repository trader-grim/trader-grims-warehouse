"""tgw health surfaces the OpenRouter per-key spend limit (todo #1132).

Real incident 2026-07-04: the OpenRouter *account* balance looked fine
but the specific key TGW uses had its own separate spend limit, silently
near-exhausted -- caused a 402 pile-up that took a live log-dive to
diagnose. This is the fix: surface it directly in `tgw health`.

Updated 2026-07-13 (todo #1289): the OpenRouter key is read via
tgw.apis.secrets.get_api_key('openrouter') (the single secrets facility
from the 2026-07-09 migration, #1252), not a per-provider credentials
JSON file -- the old file-based fixture made this check silently dead
since that migration (cred_path.exists() was always False).
"""

from unittest import mock

from tgw.health import _openrouter_key_limit, check_quota


def _cfg():
    # secrets_root is no longer read by _openrouter_key_limit(); the key
    # comes from tgw.apis.secrets.get_api_key(), which is mocked per-test.
    return {}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_openrouter_key_limit_returns_none_without_credentials():
    cfg = _cfg()
    with mock.patch(
        "tgw.apis.secrets.get_api_key",
        side_effect=RuntimeError("OPENROUTER_API_KEY not set"),
    ):
        assert _openrouter_key_limit(cfg) is None


def test_openrouter_key_limit_parses_live_response():
    cfg = _cfg()
    payload = {"data": {"limit": 5, "limit_reset": "daily", "limit_remaining": 4.92}}
    with mock.patch("tgw.apis.secrets.get_api_key", return_value="sk-or-test"), \
         mock.patch("requests.get", return_value=_FakeResponse(payload)):
        result = _openrouter_key_limit(cfg)
    assert result == {"limit": 5, "limit_reset": "daily", "limit_remaining": 4.92}


def test_openrouter_key_limit_swallows_network_errors():
    cfg = _cfg()
    with mock.patch("tgw.apis.secrets.get_api_key", return_value="sk-or-test"), \
         mock.patch("requests.get", side_effect=OSError("no network")):
        assert _openrouter_key_limit(cfg) is None


def test_check_quota_flags_near_exhausted_key_as_warn():
    cfg = _cfg()
    payload = {"data": {"limit": 15, "limit_reset": "weekly", "limit_remaining": 0.02}}
    with mock.patch("tgw.apis.secrets.get_api_key", return_value="sk-or-test"), \
         mock.patch("requests.get", return_value=_FakeResponse(payload)), \
         mock.patch("tgw.quota.status", return_value={"incidents_today": 0, "pools": {}}):
        result = check_quota(cfg)
    assert result["warn"] is True
    assert "openrouter" in result["detail"].lower()


def test_check_quota_includes_key_limit_in_detail_when_healthy():
    cfg = _cfg()
    payload = {"data": {"limit": 5, "limit_reset": "daily", "limit_remaining": 4.9}}
    with mock.patch("tgw.apis.secrets.get_api_key", return_value="sk-or-test"), \
         mock.patch("requests.get", return_value=_FakeResponse(payload)), \
         mock.patch("tgw.quota.status", return_value={"incidents_today": 0, "pools": {}}):
        result = check_quota(cfg)
    assert result["ok"] is True
    assert "$4.90 of $5" in result["detail"]
