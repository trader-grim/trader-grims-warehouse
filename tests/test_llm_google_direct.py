"""Tests for the direct-Google (Gemini generateContent REST) call path in
apis/llm.py: a plain-`requests` call with the Google API key — the same shape
as every other provider, no google-genai SDK (the SDK is only for the async
Batch pipeline in tgw.apis.google_genai). A Google failure falls through the
provider chain; it never dead-letters a job.

All HTTP is mocked — tests pass completely offline.
"""

from __future__ import annotations

import json as _json
from typing import Any, Dict

import pytest
import requests

import tgw.apis.llm as llm_mod


def _cfg(tmp_path) -> Dict[str, Any]:
    return {'raw': {
        'quota_state_path': str(tmp_path / 'quota-state.json'),
        'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl'),
    }}


def _ok_body(text='{"ok": true}', *, prompt=10, completion=20, total=30):
    return {
        'candidates': [{
            'content': {'role': 'model', 'parts': [{'text': text}]},
            'finishReason': 'STOP',
        }],
        'usageMetadata': {
            'promptTokenCount': prompt,
            'candidatesTokenCount': completion,
            'totalTokenCount': total,
        },
    }


class _FakeHTTPResponse:
    def __init__(self, *, status=200, body=None, text=None):
        self.status_code = status
        self._body = _ok_body() if body is None else body
        self.text = text if text is not None else _json.dumps(self._body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code}', response=self)


def _patch_google_post(monkeypatch, *responses, capture=None):
    """Patch llm_mod.requests.post to hand back *responses* in order (the last
    repeats). *capture* (a list) collects {url, json, headers} per call."""
    seq = list(responses) or [_FakeHTTPResponse()]
    calls = capture if capture is not None else []

    def _post(url, headers=None, json=None, timeout=None):
        calls.append({'url': url, 'json': json, 'headers': headers})
        return seq[min(len(calls) - 1, len(seq) - 1)]

    monkeypatch.setattr(llm_mod.requests, 'post', _post)
    monkeypatch.setattr('tgw.apis.google_genai.load_google_key', lambda cfg: 'fake-key')
    return calls


class TestCallGoogleDirect:
    def test_returns_text_and_usage(self, monkeypatch, tmp_path):
        calls = _patch_google_post(
            monkeypatch, _FakeHTTPResponse(body=_ok_body('hello world')))
        text, usage = llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))
        assert text == 'hello world'
        assert usage == {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
        assert len(calls) == 1

    def test_sends_system_instruction_and_model_in_url(self, monkeypatch, tmp_path):
        calls = _patch_google_post(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'be terse', 'describe this', _cfg(tmp_path))
        c = calls[0]
        assert c['url'].endswith('/models/gemini-2.5-flash-lite:generateContent')
        assert c['headers']['x-goog-api-key'] == 'fake-key'
        assert c['json']['systemInstruction']['parts'][0]['text'] == 'be terse'
        assert c['json']['contents'][0]['parts'][-1] == {'text': 'describe this'}

    def test_sends_multiple_images_as_inline_parts(self, monkeypatch, tmp_path):
        calls = _patch_google_post(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path),
            img_b64_list=['AAA', 'BBB', 'CCC'])
        parts = calls[0]['json']['contents'][0]['parts']
        image_parts = [p for p in parts if 'inlineData' in p]
        assert len(image_parts) == 3
        assert image_parts[0]['inlineData']['data'] == 'AAA'
        assert image_parts[0]['inlineData']['mimeType'] == 'image/jpeg'

    def test_already_prefixed_model_not_double_prefixed(self, monkeypatch, tmp_path):
        calls = _patch_google_post(monkeypatch)
        llm_mod._call_google_direct(
            'models/gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))
        assert calls[0]['url'].endswith('/models/gemini-2.5-flash-lite:generateContent')
        assert '/models/models/' not in calls[0]['url']

    def test_raises_on_persistent_429(self, monkeypatch, tmp_path):
        _patch_google_post(monkeypatch, _FakeHTTPResponse(status=429, text='rate limited'))
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda s: None)
        with pytest.raises(requests.HTTPError, match='429'):
            llm_mod._call_google_direct(
                'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path), max_retries=1)

    def test_no_candidates_raises(self, monkeypatch, tmp_path):
        _patch_google_post(monkeypatch, _FakeHTTPResponse(
            body={'promptFeedback': {'blockReason': 'SAFETY'}}))
        with pytest.raises(RuntimeError, match='no candidates.*SAFETY'):
            llm_mod._call_google_direct(
                'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))

    def test_retries_on_transient_503_then_succeeds(self, monkeypatch, tmp_path):
        """2026-07-14, Dave: a bare 503 UNAVAILABLE ("high demand... temporary")
        fell straight through to the fallback with zero retry -- unlike 429,
        which already retried with backoff. 503 retries with a short backoff."""
        _patch_google_post(
            monkeypatch,
            _FakeHTTPResponse(status=503, text='high demand'),
            _FakeHTTPResponse(status=503, text='high demand'),
            _FakeHTTPResponse(body=_ok_body('recovered')),
        )
        sleeps = []
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda s: sleeps.append(s))

        text, _ = llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))

        assert text == 'recovered'
        assert sleeps == [2, 4]  # short backoff, not the 429 15s*attempt cooldown

    def test_generation_config_omitted_by_default(self, monkeypatch, tmp_path):
        calls = _patch_google_post(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path))
        assert 'generationConfig' not in calls[0]['json']

    def test_max_output_tokens_passed_through(self, monkeypatch, tmp_path):
        calls = _patch_google_post(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path),
            max_output_tokens=2048)
        assert calls[0]['json']['generationConfig']['maxOutputTokens'] == 2048

    def test_thinking_budget_passed_through(self, monkeypatch, tmp_path):
        """thinking_budget=0 (PP-DEADLETTER-001 fix for gemini-2.5-flash-lite
        eating its whole output budget on invisible 'thinking' tokens)."""
        calls = _patch_google_post(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path),
            thinking_budget=0)
        assert calls[0]['json']['generationConfig']['thinkingConfig'] == {'thinkingBudget': 0}

    def test_503_does_not_feed_quota_circuit_breaker(self, monkeypatch, tmp_path):
        """A transient 503 is not quota exhaustion -- must not call
        quota.record_429 (reserved for actual 429), or a demand spike would
        wrongly trip the llm_google cooldown."""
        _patch_google_post(monkeypatch, _FakeHTTPResponse(status=503, text='high demand'))
        recorded_429 = []
        monkeypatch.setattr('tgw.quota.record_429',
                           lambda cfg, key, detail: recorded_429.append((key, detail)))
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda s: None)

        with pytest.raises(requests.HTTPError, match='503'):
            llm_mod._call_google_direct(
                'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path), max_retries=1)

        assert recorded_429 == []

    def test_429_does_feed_quota_circuit_breaker(self, monkeypatch, tmp_path):
        _patch_google_post(monkeypatch, _FakeHTTPResponse(status=429, text='RESOURCE_EXHAUSTED'))
        recorded_429 = []
        monkeypatch.setattr('tgw.quota.record_429',
                           lambda cfg, key, detail: recorded_429.append(key))
        monkeypatch.setattr(llm_mod.time, 'sleep', lambda s: None)
        with pytest.raises(requests.HTTPError):
            llm_mod._call_google_direct(
                'gemini-2.5-flash-lite', 'sys', 'user', _cfg(tmp_path), max_retries=1)
        assert recorded_429 == ['llm_google']


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
