"""Tests for the direct-Google (google-genai SDK) call path in apis/llm.py
(session 41): a synchronous alternative to routing Gemini calls through
OpenRouter's markup, with automatic fallback to OpenRouter on any failure so
a Google-side outage/quota/auth error never dead-letters a job.

All SDK calls are mocked — tests pass completely offline without the
google-genai package installed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict

import pytest

import tgw.apis.llm as llm_mod


def _cfg(tmp_path) -> Dict[str, Any]:
    return {'raw': {
        'quota_state_path': str(tmp_path / 'quota-state.json'),
        'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl'),
    }}


class _FakeUsage:
    def __init__(self, prompt=10, completion=20, total=30):
        self.prompt_token_count = prompt
        self.candidates_token_count = completion
        self.total_token_count = total


class _FakeResponse:
    def __init__(self, text='{"ok": true}', usage=None):
        self.text = text
        self.usage_metadata = usage if usage is not None else _FakeUsage()


class _FakeModels:
    def __init__(self, response=None, exc=None, calls=None):
        self._response = response
        self._exc = exc
        self._calls = calls if calls is not None else []

    def generate_content(self, **kwargs):
        self._calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


class _FakeClient:
    def __init__(self, models):
        self.models = models


def _patch_genai(monkeypatch, response=None, exc=None, calls=None):
    fake_models = _FakeModels(response=response or _FakeResponse(), exc=exc, calls=calls)
    fake_client = _FakeClient(fake_models)
    fake_genai_module = SimpleNamespace(
        Client=lambda api_key, http_options=None: fake_client,
    )

    monkeypatch.setattr('tgw.apis.google_genai._require_genai', lambda: fake_genai_module)
    monkeypatch.setattr('tgw.apis.google_genai.load_google_key', lambda cfg: 'fake-key')
    return fake_models


class TestCallGoogleDirect:
    def test_returns_text_and_usage(self, monkeypatch, tmp_path):
        fake = _patch_genai(monkeypatch, response=_FakeResponse(text='hello world'))
        text, usage = llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))
        assert text == 'hello world'
        assert usage == {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
        assert len(fake._calls) == 1

    def test_sends_system_instruction_and_model_ref(self, monkeypatch, tmp_path):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct('gemini-2.5-flash-lite', 'be terse', 'describe this', _cfg(tmp_path))
        call = fake._calls[0]
        assert call['model'] == 'models/gemini-2.5-flash-lite'
        assert call['config']['system_instruction'] == 'be terse'
        assert call['contents'][0]['parts'][-1] == {'text': 'describe this'}

    def test_sends_multiple_images_as_inline_parts(self, monkeypatch, tmp_path):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path),
            img_b64_list=['AAA', 'BBB', 'CCC'],
        )
        parts = fake._calls[0]['contents'][0]['parts']
        image_parts = [p for p in parts if 'inline_data' in p]
        assert len(image_parts) == 3
        assert image_parts[0]['inline_data']['data'] == 'AAA'

    def test_already_prefixed_model_not_double_prefixed(self, monkeypatch, tmp_path):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct('models/gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))
        assert fake._calls[0]['model'] == 'models/gemini-2.5-flash-lite'

    def test_raises_on_persistent_failure(self, monkeypatch, tmp_path):
        _patch_genai(monkeypatch, exc=RuntimeError('quota exhausted'))
        with pytest.raises(RuntimeError, match='quota exhausted'):
            llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path), max_retries=1)

    def test_retries_on_transient_503_then_succeeds(self, monkeypatch, tmp_path):
        """2026-07-14, Dave: a bare 503 UNAVAILABLE ("high demand... temporary")
        fell straight through to the OpenRouter fallback with zero retry --
        unlike 429, which already retried with backoff. 503 should retry too."""
        calls = []

        class _FlakyModels:
            def generate_content(self, **kwargs):
                calls.append(kwargs)
                if len(calls) < 3:
                    raise RuntimeError(
                        "503 UNAVAILABLE. {'error': {'code': 503, "
                        "'message': 'high demand', 'status': 'UNAVAILABLE'}}"
                    )
                return _FakeResponse(text='recovered')

        fake_client = _FakeClient(_FlakyModels())
        monkeypatch.setattr('tgw.apis.google_genai._require_genai',
                           lambda: SimpleNamespace(
                               Client=lambda api_key, http_options=None: fake_client,
                           ))
        monkeypatch.setattr('tgw.apis.google_genai.load_google_key', lambda cfg: 'fake-key')
        sleeps = []
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda s: sleeps.append(s))

        text, _ = llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))

        assert text == 'recovered'
        assert len(calls) == 3
        assert sleeps == [2, 4]  # short backoff, not the 429 15s*attempt cooldown

    def test_generation_config_omitted_by_default(self, monkeypatch, tmp_path):
        """No max_output_tokens/thinking_budget passed -> generate_content's
        config carries neither key, preserving prior behavior exactly."""
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))
        config = fake._calls[0]['config']
        assert 'max_output_tokens' not in config
        assert 'thinking_config' not in config

    def test_max_output_tokens_passed_through(self, monkeypatch, tmp_path):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path),
            max_output_tokens=2048,
        )
        assert fake._calls[0]['config']['max_output_tokens'] == 2048

    def test_thinking_budget_passed_through(self, monkeypatch, tmp_path):
        """thinking_budget=0 (PP-DEADLETTER-001 fix for gemini-2.5-flash-lite
        eating its whole output budget on invisible 'thinking' tokens)."""
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path),
            thinking_budget=0,
        )
        assert fake._calls[0]['config']['thinking_config'] == {'thinking_budget': 0}

    def test_503_does_not_feed_quota_circuit_breaker(self, monkeypatch, tmp_path):
        """A transient 503 is not quota exhaustion -- must not call
        quota.record_429 (that's reserved for actual 429/RESOURCE_EXHAUSTED),
        or a demand spike would incorrectly trip the llm_google cooldown."""
        _patch_genai(monkeypatch, exc=RuntimeError('503 UNAVAILABLE'))
        recorded_429 = []
        monkeypatch.setattr('tgw.quota.record_429',
                           lambda cfg, key, detail: recorded_429.append((key, detail)))
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda s: None)

        with pytest.raises(RuntimeError, match='503 UNAVAILABLE'):
            llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path), max_retries=1)

        assert recorded_429 == []


class TestGetTaskGenerationConfig:
    def test_absent_returns_empty_dict(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg['models'] = {'ai_identify': {'provider': 'google_direct', 'model': 'gemini-2.5-flash-lite'}}
        assert llm_mod.get_task_generation_config(cfg, 'ai_identify') == {}

    def test_unknown_task_returns_empty_dict(self, tmp_path):
        assert llm_mod.get_task_generation_config(_cfg(tmp_path), 'nonexistent') == {}

    def test_present_returns_generation_dict(self, tmp_path):
        cfg = _cfg(tmp_path)
        cfg['models'] = {
            'bulk_classify': {
                'provider': 'google_direct', 'model': 'gemini-2.5-flash-lite',
                'generation': {'max_output_tokens': 4096, 'thinking_budget': 0},
            },
        }
        assert llm_mod.get_task_generation_config(cfg, 'bulk_classify') == {
            'max_output_tokens': 4096, 'thinking_budget': 0,
        }


class TestCallModelGoogleDirectDispatch:
    def test_generation_config_reaches_google_direct_call(self, monkeypatch, tmp_path):
        """call_model() must read models[task]['generation'] and pass it
        through to _call_google_direct -- this is the plumbing PP-DEADLETTER-001
        needed before a config-only fix for bulk_classify's truncation was
        even possible."""
        cfg = _cfg(tmp_path)
        cfg['models'] = {
            'bulk_classify': {
                'provider': 'google_direct', 'model': 'gemini-2.5-flash-lite',
                'generation': {'max_output_tokens': 4096, 'thinking_budget': 0},
            },
        }
        captured = {}

        def _fake_google_direct(model, system_prompt, user_prompt, cfg_arg, **kwargs):
            captured.update(kwargs)
            return 'ok', {}

        monkeypatch.setattr(llm_mod, '_call_google_direct', _fake_google_direct)
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)

        llm_mod.call_model('bulk_classify', 'sys', 'user', cfg)

        assert captured['max_output_tokens'] == 4096
        assert captured['thinking_budget'] == 0

    def test_task_without_generation_config_passes_none(self, monkeypatch, tmp_path):
        cfg = _cfg(tmp_path)
        cfg['models'] = {'ai_identify': {'provider': 'google_direct', 'model': 'gemini-2.5-flash-lite'}}
        captured = {}

        def _fake_google_direct(model, system_prompt, user_prompt, cfg_arg, **kwargs):
            captured.update(kwargs)
            return 'ok', {}

        monkeypatch.setattr(llm_mod, '_call_google_direct', _fake_google_direct)
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)

        llm_mod.call_model('ai_identify', 'sys', 'user', cfg)

        assert captured['max_output_tokens'] is None
        assert captured['thinking_budget'] is None

    def test_success_does_not_touch_openrouter(self, monkeypatch, tmp_path):
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: ('google says hi', {'total_tokens': 5}))
        or_calls = []
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: or_calls.append(1) or ('', {}))
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(tmp_path),
                                  provider='google_direct', model='gemini-2.5-flash-lite')

        assert text == 'google says hi'
        assert or_calls == []

    def test_failure_with_no_failover_configured_reraises(self, monkeypatch, tmp_path):
        # cfg has no models.failover_vision block → the chain is just the
        # configured primary; its failure re-raises so worker_base holds the job.
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('fallback text', {}),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        with pytest.raises(RuntimeError, match='boom'):
            llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(tmp_path),
                               provider='google_direct', model='gemini-2.5-flash-lite')
        assert or_calls == []

    def test_failure_falls_through_to_configured_vision_failover(self, monkeypatch, tmp_path):
        cfg = _cfg(tmp_path)
        cfg['models'] = {'failover_vision': {
            'deepseek_direct': 'deepseek-vl',
            'openrouter': 'google/gemini-2.5-flash-lite',
        }}
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
        ds_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_deepseek_direct',
            lambda model, *a, **k: ds_calls.append(model) or ('vision text', {}),
        )
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('or text', {}),
        )
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', cfg,
                                  provider='google_direct', model='gemini-2.5-flash-lite',
                                  img_b64='ZmFrZQ==')

        assert text == 'vision text'
        assert ds_calls == ['deepseek-vl']      # first configured failover wins
        assert or_calls == []                    # openrouter not reached


class TestGoogleStandDown:
    """Circuit breaker: during the llm_google post-429 cooldown, call_model
    skips the doomed google_direct attempt and moves to the next provider in
    the configured vision failover chain."""

    def test_standdown_skips_google_and_uses_next_in_chain(self, monkeypatch, tmp_path):
        from tgw import quota

        cfg = _cfg(tmp_path)
        cfg['models'] = {'failover_vision': {'openrouter': 'google/gemini-2.5-flash-lite'}}

        def _precheck(_cfg, pool):
            if pool == 'llm_google':
                raise quota.QuotaBudgetExceeded('429 received 60s ago — stand-down')

        monkeypatch.setattr('tgw.quota.precheck', _precheck)
        google_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_google_direct',
            lambda *a, **k: google_calls.append(1) or ('google', {}),
        )
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('fallback text', {}),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', cfg,
                                  provider='google_direct', model='gemini-2.5-flash-lite',
                                  img_b64='ZmFrZQ==')

        assert text == 'fallback text'
        assert google_calls == []
        assert or_calls == ['google/gemini-2.5-flash-lite']

    def test_no_standdown_google_called_normally(self, monkeypatch, tmp_path):
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: ('google says hi', {}))
        or_calls = []
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: or_calls.append(1) or ('', {}))
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(tmp_path),
                                  provider='google_direct', model='gemini-2.5-flash-lite')

        assert text == 'google says hi'
        assert or_calls == []


class TestChainExhaustion:
    """No operator emergency reserve (Dave, 2026-09-07): when every provider in
    the chain fails, call_model raises so worker_base requeues the job as a
    visible hold. Nothing silently succeeds on a reserve."""

    def test_openrouter_primary_failure_with_no_failover_reraises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('or down')),
        )
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        with pytest.raises(RuntimeError, match='or down'):
            llm_mod.call_model('pm_chat', 'sys', 'user', _cfg(tmp_path),
                               provider='openrouter', model='deepseek/deepseek-v4-flash',
                               messages=[{'role': 'user', 'content': 'hi'}])

    def test_whole_text_chain_exhausted_raises_last_error(self, monkeypatch, tmp_path):
        cfg = _cfg(tmp_path)
        cfg['models'] = {'failover': {
            'groq': 'g', 'deepseek_direct': 'd', 'openrouter': 'o',
        }}
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)
        for fn in ('_call_nous', '_call_groq', '_call_deepseek_direct', '_call_openrouter'):
            monkeypatch.setattr(
                llm_mod, fn,
                lambda *a, _n=fn, **k: (_ for _ in ()).throw(RuntimeError(f'{_n} down')),
            )
        notes = []
        monkeypatch.setattr(llm_mod, '_notify', lambda *a, **k: notes.append((a, k)))

        with pytest.raises(RuntimeError, match='down'):
            llm_mod.call_model('pm_intake', 'sys', 'user', cfg,
                               provider='nous', model='longcat')
        # loud: one openrouter-failover warning + one chain-exhausted error
        assert any(k.get('level') == 'error' for _a, k in notes)
