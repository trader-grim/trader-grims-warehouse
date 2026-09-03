"""Tests for the proactive low-balance warning layer (todo #1337 /
PP-QUOTA-001): tgw.quota.check_deepseek_balance / estimate_cost_usd /
today_cost_usd_by_provider / balance_status, and their wiring into
tgw.health.check_quota().

Real research this is built on (see quota.py's module comment and
LLM-Providers-Quotas.md, live-verified 2026-07-17): DeepSeek's own
`/user/balance` endpoint returns a real account balance (confirmed live
against the real key); Google's Gemini API key and Anthropic's regular
API key have no reachable balance endpoint (Anthropic's usage_report
needs a separate Admin key; Google has none at all) — those two get a
real-pricing spend-today estimate from ai_usage token counts instead.
"""

from __future__ import annotations

from unittest import mock

import pytest

from tgw import quota


def _cfg(tmp_path=None, **raw):
    base = {}
    if tmp_path is not None:
        base = {
            'quota_state_path': str(tmp_path / 'quota-state.json'),
            'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl'),
        }
    base.update(raw)
    return {'raw': base}


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError('bad status')

    def json(self):
        return self._payload


class TestDeepseekBalance:
    def test_returns_none_without_credentials(self):
        with mock.patch('tgw.apis.secrets.get_api_key',
                         side_effect=RuntimeError('DEEPSEEK_API_KEY not set')):
            assert quota.check_deepseek_balance({}) is None

    def test_parses_live_response_shape(self):
        # This is the exact real shape confirmed live 2026-07-17 against
        # https://api.deepseek.com/user/balance.
        payload = {
            'is_available': True,
            'balance_infos': [
                {'currency': 'USD', 'total_balance': '9.83',
                 'granted_balance': '0.00', 'topped_up_balance': '9.83'},
            ],
        }
        with mock.patch('tgw.apis.secrets.get_api_key', return_value='sk-ds-test'), \
             mock.patch('requests.get', return_value=_FakeResponse(payload)):
            result = quota.check_deepseek_balance({})
        assert result['total_balance_usd'] == 9.83
        assert result['is_available'] is True
        assert result['low'] is False

    def test_flags_low_when_below_threshold(self):
        payload = {'is_available': True,
                   'balance_infos': [{'currency': 'USD', 'total_balance': '1.50'}]}
        cfg = _cfg(quota_deepseek_low_balance_usd=2.0)
        with mock.patch('tgw.apis.secrets.get_api_key', return_value='sk-ds-test'), \
             mock.patch('requests.get', return_value=_FakeResponse(payload)):
            result = quota.check_deepseek_balance(cfg)
        assert result['low'] is True
        assert result['threshold_usd'] == 2.0

    def test_swallows_network_errors(self):
        with mock.patch('tgw.apis.secrets.get_api_key', return_value='sk-ds-test'), \
             mock.patch('requests.get', side_effect=OSError('no network')):
            assert quota.check_deepseek_balance({}) is None


class TestCostEstimate:
    def test_estimate_cost_usd_known_model(self):
        # gemini-2.5-flash-lite: $0.10/1M in, $0.40/1M out (live pricing
        # page, 2026-07-17).
        cost = quota.estimate_cost_usd('gemini-2.5-flash-lite', 1_000_000, 1_000_000)
        assert cost == pytest.approx(0.50)

    def test_estimate_cost_usd_unknown_model_returns_none(self):
        assert quota.estimate_cost_usd('some-unlisted-model', 100, 100) is None

    def test_estimate_cost_usd_missing_tokens_returns_none(self):
        assert quota.estimate_cost_usd('gemini-2.5-flash-lite', None, None) is None

    def test_estimate_cost_usd_unknown_model_with_real_tokens_warns(self, caplog):
        """Invariant E15 sweep (2026-07-20): a new tgw-models.json model with
        no matching pricing entry must be a visible staleness signal, not a
        silent zero — real token counts + unpriced model should log a
        warning naming the model."""
        with caplog.at_level("WARNING", logger="tgw.quota"):
            cost = quota.estimate_cost_usd('some-new-unpriced-model', 100, 100)
        assert cost is None
        assert any(
            "some-new-unpriced-model" in rec.message for rec in caplog.records
        )

    def test_estimate_cost_usd_unknown_model_no_tokens_does_not_warn(self, caplog):
        """No token counts (failed/no-usage call) is the expected/common
        case, not a staleness signal — must not warn."""
        with caplog.at_level("WARNING", logger="tgw.quota"):
            cost = quota.estimate_cost_usd('some-unlisted-model', None, None)
        assert cost is None
        assert not caplog.records

    def test_today_cost_usd_by_provider_sums_real_token_rows(self):
        rows = [
            {'provider': 'google_direct', 'model': 'gemini-2.5-flash-lite',
             'prompt_tokens': 2_000_000, 'completion_tokens': 500_000},
            {'provider': 'google_direct', 'model': 'gemini-3.1-pro-preview',
             'prompt_tokens': 100_000, 'completion_tokens': 50_000},
            {'provider': 'anthropic_direct', 'model': 'claude-haiku-4-5-20251001',
             'prompt_tokens': 10_000, 'completion_tokens': 5_000},
            {'provider': 'deepseek_direct', 'model': 'unknown-model',
             'prompt_tokens': 999, 'completion_tokens': 999},  # unpriced -> skipped
        ]
        with mock.patch('tgw.queue.state_machine.query_ai_usage', return_value=rows):
            out = quota.today_cost_usd_by_provider({})
        expected_google = (2_000_000 / 1e6 * 0.10 + 500_000 / 1e6 * 0.40
                            + 100_000 / 1e6 * 2.00 + 50_000 / 1e6 * 12.00)
        expected_anthropic = 10_000 / 1e6 * 1.00 + 5_000 / 1e6 * 5.00
        assert out['google_direct'] == pytest.approx(expected_google)
        assert out['anthropic_direct'] == pytest.approx(expected_anthropic)
        assert 'deepseek_direct' not in out

    def test_today_cost_usd_by_provider_fails_open_on_db_error(self):
        with mock.patch('tgw.queue.state_machine.query_ai_usage',
                         side_effect=RuntimeError('db down')):
            assert quota.today_cost_usd_by_provider({}) == {}


class TestBalanceStatus:
    def test_low_balance_false_when_all_healthy(self):
        with mock.patch('tgw.quota.check_deepseek_balance',
                         return_value={'total_balance_usd': 9.83, 'low': False,
                                       'threshold_usd': 2.0, 'is_available': True,
                                       'currency': 'USD'}), \
             mock.patch('tgw.quota.today_cost_usd_by_provider', return_value={}):
            result = quota.balance_status({})
        assert result['low_balance'] is False
        assert result['estimated_cost_usd'] == {'llm_google': 0.0, 'llm_anthropic': 0.0}

    def test_low_balance_true_when_deepseek_low(self):
        with mock.patch('tgw.quota.check_deepseek_balance',
                         return_value={'total_balance_usd': 0.50, 'low': True,
                                       'threshold_usd': 2.0, 'is_available': True,
                                       'currency': 'USD'}), \
             mock.patch('tgw.quota.today_cost_usd_by_provider', return_value={}):
            result = quota.balance_status({})
        assert result['low_balance'] is True

    def test_low_balance_true_when_google_spend_over_warn_threshold(self):
        cfg = _cfg(quota_cost_warn_usd={'llm_google': 1.00})
        with mock.patch('tgw.quota.check_deepseek_balance', return_value=None), \
             mock.patch('tgw.quota.today_cost_usd_by_provider',
                        return_value={'google_direct': 5.00}):
            result = quota.balance_status(cfg)
        assert result['cost_warn']['llm_google'] is True
        assert result['low_balance'] is True

    def test_balance_status_never_raises_when_deepseek_check_fails(self):
        # check_deepseek_balance already fails open (returns None); confirm
        # balance_status() tolerates that cleanly.
        with mock.patch('tgw.quota.check_deepseek_balance', return_value=None), \
             mock.patch('tgw.quota.today_cost_usd_by_provider', return_value={}):
            result = quota.balance_status({})
        assert result['deepseek'] is None
        assert result['low_balance'] is False


class TestHealthCheckQuotaWiring:
    def test_check_quota_surfaces_deepseek_low_balance_as_warn(self):
        # Default threshold is 0.0 (the key is meant to run to $0), so the
        # early-warning must be explicitly re-armed to test the health wiring.
        cfg = _cfg(quota_deepseek_low_balance_usd=2.0)
        with mock.patch('tgw.quota.status', return_value={'incidents_today': 0, 'pools': {}}), \
             mock.patch('tgw.apis.secrets.get_api_key',
                       side_effect=lambda p: 'sk-test' if p in ('openrouter', 'deepseek') else (_ for _ in ()).throw(RuntimeError())), \
             mock.patch('requests.get') as mget:
            def _side_effect(url, **kwargs):
                if 'deepseek' in url:
                    return _FakeResponse({'is_available': True,
                                          'balance_infos': [{'currency': 'USD',
                                                              'total_balance': '0.10'}]})
                return _FakeResponse({'data': {'limit': 5, 'limit_reset': 'daily',
                                               'limit_remaining': 5}})
            mget.side_effect = _side_effect
            from tgw.health import check_quota
            result = check_quota(cfg)
        assert result['warn'] is True
        assert 'deepseek' in result['detail'].lower()
        assert result['balance']['deepseek']['low'] is True

    def test_check_quota_does_not_warn_on_low_deepseek_balance_by_default(self):
        # Cap removed 2026-09-03: a near-empty DeepSeek balance is reported,
        # not flagged — no [LOW], no "background halted" line.
        with mock.patch('tgw.quota.status', return_value={'incidents_today': 0, 'pools': {}}), \
             mock.patch('tgw.apis.secrets.get_api_key',
                       side_effect=lambda p: 'sk-test' if p in ('openrouter', 'deepseek') else (_ for _ in ()).throw(RuntimeError())), \
             mock.patch('requests.get') as mget, \
             mock.patch('tgw.quota.today_cost_usd_by_provider', return_value={}):
            def _side_effect(url, **kwargs):
                if 'deepseek' in url:
                    return _FakeResponse({'is_available': True,
                                          'balance_infos': [{'currency': 'USD',
                                                              'total_balance': '0.10'}]})
                return _FakeResponse({'data': {'limit': 5, 'limit_reset': 'daily',
                                               'limit_remaining': 5}})
            mget.side_effect = _side_effect
            from tgw.health import check_quota
            result = check_quota({})
        assert result['ok'] is True
        assert result.get('warn') is not True
        assert result['balance']['deepseek']['low'] is False
        assert result['balance']['deepseek']['total_balance_usd'] == 0.10

    def test_check_quota_stays_green_when_all_providers_healthy(self):
        with mock.patch('tgw.quota.status', return_value={'incidents_today': 0, 'pools': {}}), \
             mock.patch('tgw.apis.secrets.get_api_key',
                       side_effect=lambda p: 'sk-test' if p in ('openrouter', 'deepseek') else (_ for _ in ()).throw(RuntimeError())), \
             mock.patch('requests.get') as mget, \
             mock.patch('tgw.quota.today_cost_usd_by_provider', return_value={}):
            def _side_effect(url, **kwargs):
                if 'deepseek' in url:
                    return _FakeResponse({'is_available': True,
                                          'balance_infos': [{'currency': 'USD',
                                                              'total_balance': '9.83'}]})
                return _FakeResponse({'data': {'limit': 5, 'limit_reset': 'daily',
                                               'limit_remaining': 5}})
            mget.side_effect = _side_effect
            from tgw.health import check_quota
            result = check_quota({})
        assert result['ok'] is True
        assert result.get('warn') is not True
