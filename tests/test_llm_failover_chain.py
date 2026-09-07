"""Tests for the Nous / Groq providers and the provider failover chain in
apis/llm.py (PP-STATEMACHINE-002 A4).

The task's configured (provider, model) is tried first; on failure the call
walks a fixed provider ORDER — text: nous -> groq -> deepseek_direct ->
openrouter — using the MODEL configured for each in cfg['models']['failover'].
A failover provider with no configured model is skipped. OpenRouter is the loud
paid last resort. An exhausted chain raises (worker_base then holds the job).

All HTTP is mocked — tests pass completely offline.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

import tgw.apis.llm as llm_mod


def _cfg(tmp_path, **models) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {'raw': {
        'quota_state_path': str(tmp_path / 'quota-state.json'),
        'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl'),
    }}
    if models:
        cfg['models'] = models
    return cfg


class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {
            'choices': [{'message': {'content': 'hi'}}],
            'usage': {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7},
        }

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')


class TestCallNous:
    def test_hits_nous_endpoint_with_bearer(self, monkeypatch, tmp_path):
        captured = {}

        def _fake_post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, payload=json)
            return _FakeResp()

        monkeypatch.setattr(llm_mod.requests, 'post', _fake_post)
        monkeypatch.setattr(llm_mod, '_load_nous_key', lambda cfg: 'sk-nous')

        text, usage = llm_mod._call_nous(
            'meituan/longcat-2.0:free', 'sys', 'user', _cfg(tmp_path),
        )

        assert text == 'hi'
        assert usage['total_tokens'] == 7
        assert captured['url'] == 'https://inference-api.nousresearch.com/v1/chat/completions'
        assert captured['headers']['Authorization'] == 'Bearer sk-nous'
        assert captured['payload']['model'] == 'meituan/longcat-2.0:free'
        assert captured['payload']['messages'][0]['role'] == 'system'

    def test_429_then_success_retries(self, monkeypatch, tmp_path):
        calls = [_FakeResp(status=429), _FakeResp()]
        monkeypatch.setattr(llm_mod.requests, 'post',
                            lambda *a, **k: calls.pop(0))
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda *_a: None)
        monkeypatch.setattr(llm_mod, '_load_nous_key', lambda cfg: 'k')

        text, _ = llm_mod._call_nous('m', 'sys', 'user', _cfg(tmp_path))
        assert text == 'hi'
        assert calls == []


class TestCallGroq:
    def test_hits_groq_openai_endpoint(self, monkeypatch, tmp_path):
        captured = {}
        monkeypatch.setattr(
            llm_mod.requests, 'post',
            lambda url, headers=None, json=None, timeout=None: (
                captured.update(url=url, payload=json) or _FakeResp()
            ),
        )
        monkeypatch.setattr(llm_mod, '_load_groq_key', lambda cfg: 'gsk')

        text, _ = llm_mod._call_groq('qwen/qwen3.8-27b', 'sys', 'user', _cfg(tmp_path))
        assert text == 'hi'
        assert captured['url'] == 'https://api.groq.com/openai/v1/chat/completions'
        assert captured['payload']['model'] == 'qwen/qwen3.8-27b'


class TestTextFailoverChain:
    def _mock_all(self, monkeypatch):
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

    def test_primary_success_no_failover(self, monkeypatch, tmp_path):
        self._mock_all(monkeypatch)
        seen = []
        monkeypatch.setattr(llm_mod, '_call_nous',
                            lambda *a, **k: seen.append('nous') or ('nn', {}))
        monkeypatch.setattr(llm_mod, '_call_groq',
                            lambda *a, **k: seen.append('groq') or ('gg', {}))

        text = llm_mod.call_model('ebay_draft', 'sys', 'user',
                                  _cfg(tmp_path, failover={'groq': 'g'}),
                                  provider='nous', model='longcat')
        assert text == 'nn'
        assert seen == ['nous']

    def test_walks_nous_then_groq_then_deepseek(self, monkeypatch, tmp_path):
        self._mock_all(monkeypatch)
        cfg = _cfg(tmp_path, failover={
            'groq': 'gmodel', 'deepseek_direct': 'dmodel', 'openrouter': 'omodel',
        })
        seen = []
        monkeypatch.setattr(llm_mod, '_call_nous',
                            lambda *a, **k: seen.append('nous') or (_ for _ in ()).throw(RuntimeError('nous down')))
        monkeypatch.setattr(llm_mod, '_call_groq',
                            lambda model, *a, **k: seen.append(('groq', model)) or (_ for _ in ()).throw(RuntimeError('groq down')))
        monkeypatch.setattr(llm_mod, '_call_deepseek_direct',
                            lambda model, *a, **k: seen.append(('deepseek', model)) or ('ds ok', {}))
        or_calls = []
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: or_calls.append(1) or ('', {}))

        text = llm_mod.call_model('ebay_draft', 'sys', 'user', cfg,
                                  provider='nous', model='longcat')
        assert text == 'ds ok'
        assert seen == ['nous', ('groq', 'gmodel'), ('deepseek', 'dmodel')]
        assert or_calls == []  # deepseek recovered before the paid last resort

    def test_openrouter_last_resort_is_loud(self, monkeypatch, tmp_path):
        self._mock_all(monkeypatch)
        cfg = _cfg(tmp_path, failover={'openrouter': 'omodel'})
        monkeypatch.setattr(llm_mod, '_call_nous',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('nous down')))
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda model, *a, **k: ('or ok', {}))
        notes = []
        monkeypatch.setattr(llm_mod, '_notify', lambda t, b, **k: notes.append((t, k.get('level'))))

        text = llm_mod.call_model('ebay_draft', 'sys', 'user', cfg,
                                  provider='nous', model='longcat')
        assert text == 'or ok'
        assert any('OpenRouter' in t and lvl == 'warning' for t, lvl in notes)

    def test_exhausted_chain_raises_and_notifies_error(self, monkeypatch, tmp_path):
        self._mock_all(monkeypatch)
        cfg = _cfg(tmp_path, failover={'openrouter': 'omodel'})
        monkeypatch.setattr(llm_mod, '_call_nous',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('nous down')))
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('or down')))
        notes = []
        monkeypatch.setattr(llm_mod, '_notify', lambda t, b, **k: notes.append((t, k.get('level'))))

        with pytest.raises(RuntimeError, match='or down'):
            llm_mod.call_model('ebay_draft', 'sys', 'user', cfg,
                               provider='nous', model='longcat')
        assert any(lvl == 'error' for _t, lvl in notes)

    def test_quota_halted_provider_is_skipped_not_failed(self, monkeypatch, tmp_path):
        from tgw import quota

        cfg = _cfg(tmp_path, failover={'groq': 'gmodel'})
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        def _precheck(_cfg, pool):
            if pool == 'llm_nous':
                raise quota.QuotaBudgetExceeded('nous cooling down')

        monkeypatch.setattr('tgw.quota.precheck', _precheck)
        nous_calls = []
        monkeypatch.setattr(llm_mod, '_call_nous',
                            lambda *a, **k: nous_calls.append(1) or ('', {}))
        monkeypatch.setattr(llm_mod, '_call_groq', lambda *a, **k: ('groq ok', {}))

        text = llm_mod.call_model('ebay_draft', 'sys', 'user', cfg,
                                  provider='nous', model='longcat')
        assert text == 'groq ok'
        assert nous_calls == []

    def test_records_usage_against_the_provider_that_served(self, monkeypatch, tmp_path):
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        cfg = _cfg(tmp_path, failover={'groq': 'gmodel'})
        monkeypatch.setattr(llm_mod, '_call_nous',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('nous down')))
        monkeypatch.setattr(llm_mod, '_call_groq', lambda *a, **k: ('groq ok', {'total_tokens': 9}))
        rec = {}
        monkeypatch.setattr(llm_mod, '_record_usage',
                            lambda task, provider, model, *a, **k: rec.update(provider=provider, model=model))

        llm_mod.call_model('ebay_draft', 'sys', 'user', cfg,
                           provider='nous', model='longcat')
        assert rec == {'provider': 'groq', 'model': 'gmodel'}
