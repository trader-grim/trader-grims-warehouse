"""tgw health surfaces the OpenRouter per-key spend limit (todo #1132).

Real incident 2026-07-04: the OpenRouter *account* balance looked fine
but the specific key TGW uses had its own separate spend limit, silently
near-exhausted -- caused a 402 pile-up that took a live log-dive to
diagnose. This is the fix: surface it directly in `tgw health`.
"""

from unittest import mock

from tgw.health import _openrouter_key_limit, check_quota


def _cfg(tmp_path):
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "openrouter-credentials.json").write_text('{"api_key": "sk-or-test"}')
    return {"secrets_root": str(secrets)}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_openrouter_key_limit_returns_none_without_credentials(tmp_path):
    cfg = {"secrets_root": str(tmp_path / "nowhere")}
    assert _openrouter_key_limit(cfg) is None


def test_openrouter_key_limit_parses_live_response(tmp_path):
    cfg = _cfg(tmp_path)
    payload = {"data": {"limit": 5, "limit_reset": "daily", "limit_remaining": 4.92}}
    with mock.patch("requests.get", return_value=_FakeResponse(payload)):
        result = _openrouter_key_limit(cfg)
    assert result == {"limit": 5, "limit_reset": "daily", "limit_remaining": 4.92}


def test_openrouter_key_limit_swallows_network_errors(tmp_path):
    cfg = _cfg(tmp_path)
    with mock.patch("requests.get", side_effect=OSError("no network")):
        assert _openrouter_key_limit(cfg) is None


def test_check_quota_flags_near_exhausted_key_as_warn(tmp_path):
    cfg = _cfg(tmp_path)
    payload = {"data": {"limit": 15, "limit_reset": "weekly", "limit_remaining": 0.02}}
    with mock.patch("requests.get", return_value=_FakeResponse(payload)), \
         mock.patch("tgw.quota.status", return_value={"incidents_today": 0, "pools": {}}):
        result = check_quota(cfg)
    assert result["warn"] is True
    assert "openrouter" in result["detail"].lower()


def test_check_quota_includes_key_limit_in_detail_when_healthy(tmp_path):
    cfg = _cfg(tmp_path)
    payload = {"data": {"limit": 5, "limit_reset": "daily", "limit_remaining": 4.9}}
    with mock.patch("requests.get", return_value=_FakeResponse(payload)), \
         mock.patch("tgw.quota.status", return_value={"incidents_today": 0, "pools": {}}):
        result = check_quota(cfg)
    assert result["ok"] is True
    assert "$4.90 of $5" in result["detail"]
