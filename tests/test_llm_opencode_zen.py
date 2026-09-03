"""Tests for the OpenCode Zen call path in apis/llm.py (Dave, 2026-09-03).

Zen's free 'deepseek-v4-flash-free' id is an OpenAI-compatible chat/completions
gateway with no prepaid balance and no documented rate cap. call_model() must
dispatch to it, fall back to OpenRouter on any failure (stripping the '-free'
suffix Zen adds), and never dead-letter a job on a Zen-side error.

All HTTP is mocked — tests pass completely offline.
"""

from __future__ import annotations

from typing import Any, Dict

import tgw.apis.llm as llm_mod


def _cfg(tmp_path) -> Dict[str, Any]:
    return {'raw': {
        'quota_state_path': str(tmp_path / 'quota-state.json'),
        'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl'),
    }}


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {
            'choices': [{'message': {'content': 'zen says hi'}}],
            'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18},
        }

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class TestCallOpencode:
    def test_direct_success_hits_zen_gateway_with_bearer_and_does_not_touch_openrouter(
        self, monkeypatch, tmp_path,
    ):
        captured = {}

        def _fake_post(url, headers=None, json=None, timeout=None):
            captured['url'] = url
            captured['headers'] = headers
            captured['payload'] = json
            return _FakeResp()

        or_calls = []
        monkeypatch.setattr(llm_mod.requests, 'post', _fake_post)
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: or_calls.append(1) or ('', {}))
        monkeypatch.setattr(llm_mod, '_load_opencode_zen_key', lambda cfg: 'sk-test-zen')
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model(
            'simple_llm_jobs', 'sys', 'user', _cfg(tmp_path),
            provider='opencode_zen', model='deepseek-v4-flash-free',
        )

        assert text == 'zen says hi'
        assert or_calls == []
        assert captured['url'] == 'https://opencode.ai/zen/v1/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer sk-test-zen'
        assert captured['payload']['model'] == 'deepseek-v4-flash-free'

    def test_failure_falls_back_to_openrouter_stripping_free_suffix(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            llm_mod, '_call_opencode_zen',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('zen 500')),
        )
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('fallback text', {}),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model(
            'pm_intake', 'sys', 'user', _cfg(tmp_path),
            provider='opencode_zen', model='deepseek-v4-flash-free',
        )

        assert text == 'fallback text'
        assert or_calls == ['deepseek/deepseek-v4-flash']

    def test_fallback_does_not_double_prefix_an_already_qualified_model(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            llm_mod, '_call_opencode_zen',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')),
        )
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('t', {}),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        llm_mod.call_model(
            'pm_intake', 'sys', 'user', _cfg(tmp_path),
            provider='opencode_zen', model='deepseek/deepseek-v4-flash',
        )

        assert or_calls == ['deepseek/deepseek-v4-flash']

    def test_get_task_model_routes_a_task_to_opencode_zen(self, tmp_path):
        cfg = {'models': {
            'pm_intake': {'provider': 'opencode_zen', 'model': 'deepseek-v4-flash-free'},
        }}
        provider, model = llm_mod.get_task_model(cfg, 'pm_intake')
        assert (provider, model) == ('opencode_zen', 'deepseek-v4-flash-free')


class TestOpencodeQuotaPool:
    def test_llm_opencode_zen_pool_is_count_only_never_halts_background(self, tmp_path):
        from tgw import quota

        cfg = _cfg(tmp_path)
        # count-only pool: recording is fine and precheck never raises
        quota.record(cfg, 'llm_opencode_zen', 5)
        quota.precheck(cfg, 'llm_opencode_zen')  # must not raise
        assert quota._budgets(cfg).get('llm_opencode_zen') is None
