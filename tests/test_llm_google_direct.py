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


def _cfg() -> Dict[str, Any]:
    return {'raw': {}}


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
    fake_genai_module = SimpleNamespace(Client=lambda api_key: fake_client)

    monkeypatch.setattr('tgw.apis.google_genai._require_genai', lambda: fake_genai_module)
    monkeypatch.setattr('tgw.apis.google_genai.load_google_key', lambda cfg: 'fake-key')
    return fake_models


class TestCallGoogleDirect:
    def test_returns_text_and_usage(self, monkeypatch):
        fake = _patch_genai(monkeypatch, response=_FakeResponse(text='hello world'))
        text, usage = llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg())
        assert text == 'hello world'
        assert usage == {'prompt_tokens': 10, 'completion_tokens': 20, 'total_tokens': 30}
        assert len(fake._calls) == 1

    def test_sends_system_instruction_and_model_ref(self, monkeypatch):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct('gemini-2.5-flash-lite', 'be terse', 'describe this', _cfg())
        call = fake._calls[0]
        assert call['model'] == 'models/gemini-2.5-flash-lite'
        assert call['config']['system_instruction'] == 'be terse'
        assert call['contents'][0]['parts'][-1] == {'text': 'describe this'}

    def test_sends_multiple_images_as_inline_parts(self, monkeypatch):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct(
            'gemini-2.5-flash-lite', 'sys', 'user', _cfg(),
            img_b64_list=['AAA', 'BBB', 'CCC'],
        )
        parts = fake._calls[0]['contents'][0]['parts']
        image_parts = [p for p in parts if 'inline_data' in p]
        assert len(image_parts) == 3
        assert image_parts[0]['inline_data']['data'] == 'AAA'

    def test_already_prefixed_model_not_double_prefixed(self, monkeypatch):
        fake = _patch_genai(monkeypatch)
        llm_mod._call_google_direct('models/gemini-2.5-flash-lite', 'sys', 'user', _cfg())
        assert fake._calls[0]['model'] == 'models/gemini-2.5-flash-lite'

    def test_raises_on_persistent_failure(self, monkeypatch):
        _patch_genai(monkeypatch, exc=RuntimeError('quota exhausted'))
        with pytest.raises(RuntimeError, match='quota exhausted'):
            llm_mod._call_google_direct('gemini-2.5-flash-lite', 'sys', 'user', _cfg(), max_retries=1)


class TestCallModelGoogleDirectDispatch:
    def test_success_does_not_touch_openrouter(self, monkeypatch):
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: ('google says hi', {'total_tokens': 5}))
        or_calls = []
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: or_calls.append(1) or ('', {}))
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                                  provider='google_direct', model='gemini-2.5-flash-lite')

        assert text == 'google says hi'
        assert or_calls == []

    def test_failure_falls_back_to_openrouter_with_google_prefix(self, monkeypatch):
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('fallback text', {}),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                                  provider='google_direct', model='gemini-2.5-flash-lite')

        assert text == 'fallback text'
        assert or_calls == ['google/gemini-2.5-flash-lite']

    def test_failure_fallback_does_not_double_prefix(self, monkeypatch):
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
        or_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda model, *a, **k: or_calls.append(model) or ('fallback text', {}),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                           provider='google_direct', model='google/gemini-2.5-flash-lite')

        assert or_calls == ['google/gemini-2.5-flash-lite']


class TestGoogleStandDown:
    """Circuit breaker: during the llm_google post-429 cooldown, call_model
    skips the doomed google_direct attempt and goes straight to OpenRouter."""

    def test_standdown_skips_google_entirely(self, monkeypatch):
        from tgw import quota

        def _raise_precheck(cfg, pool):
            assert pool == 'llm_google'
            raise quota.QuotaBudgetExceeded('429 received 60s ago — stand-down')

        monkeypatch.setattr('tgw.quota.precheck', _raise_precheck)
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

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                                  provider='google_direct', model='gemini-2.5-flash-lite')

        assert text == 'fallback text'
        assert google_calls == []
        assert or_calls == ['google/gemini-2.5-flash-lite']

    def test_no_standdown_google_called_normally(self, monkeypatch):
        monkeypatch.setattr('tgw.quota.precheck', lambda cfg, pool: None)
        monkeypatch.setattr(llm_mod, '_call_google_direct',
                            lambda *a, **k: ('google says hi', {}))
        or_calls = []
        monkeypatch.setattr(llm_mod, '_call_openrouter',
                            lambda *a, **k: or_calls.append(1) or ('', {}))
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                                  provider='google_direct', model='gemini-2.5-flash-lite')

        assert text == 'google says hi'
        assert or_calls == []


class TestOperatorEmergencyReserve:
    """OpenRouter-primary failure falls back to the Google free tier ONLY for
    interactive (C10 operator-lane) callers with a google/* model. Background
    jobs re-raise (transient requeue) and never drain the ~20-call reserve."""

    def _fail_openrouter(self, monkeypatch):
        monkeypatch.setattr(
            llm_mod, '_call_openrouter',
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError('or down')),
        )
        monkeypatch.setattr(llm_mod, '_record_usage', lambda *a, **k: None)

    def test_interactive_google_model_uses_reserve(self, monkeypatch):
        self._fail_openrouter(monkeypatch)
        monkeypatch.setattr('tgw.quota.context_kind', lambda: 'interactive')
        google_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_google_direct',
            lambda model, *a, **k: google_calls.append(model) or ('reserve text', {}),
        )

        text = llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                                  provider='openrouter', model='google/gemini-2.5-flash-lite')

        assert text == 'reserve text'
        assert google_calls == ['gemini-2.5-flash-lite']

    def test_background_reraises_and_never_touches_reserve(self, monkeypatch):
        self._fail_openrouter(monkeypatch)
        monkeypatch.setattr('tgw.quota.context_kind', lambda: 'background')
        google_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_google_direct',
            lambda *a, **k: google_calls.append(1) or ('', {}),
        )

        with pytest.raises(RuntimeError, match='or down'):
            llm_mod.call_model('ai_identify', 'sys', 'user', _cfg(),
                               provider='openrouter', model='google/gemini-2.5-flash-lite')
        assert google_calls == []

    def test_non_google_model_reraises_even_interactive(self, monkeypatch):
        self._fail_openrouter(monkeypatch)
        monkeypatch.setattr('tgw.quota.context_kind', lambda: 'interactive')
        google_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_google_direct',
            lambda *a, **k: google_calls.append(1) or ('', {}),
        )

        with pytest.raises(RuntimeError, match='or down'):
            llm_mod.call_model('pm_intake', 'sys', 'user', _cfg(),
                               provider='openrouter', model='deepseek/deepseek-v4-flash')
        assert google_calls == []

    def test_prebuilt_messages_reraise_even_interactive(self, monkeypatch):
        self._fail_openrouter(monkeypatch)
        monkeypatch.setattr('tgw.quota.context_kind', lambda: 'interactive')
        google_calls = []
        monkeypatch.setattr(
            llm_mod, '_call_google_direct',
            lambda *a, **k: google_calls.append(1) or ('', {}),
        )

        with pytest.raises(RuntimeError, match='or down'):
            llm_mod.call_model('pm_chat', 'sys', 'user', _cfg(),
                               provider='openrouter', model='google/gemini-2.5-flash',
                               messages=[{'role': 'user', 'content': 'hi'}])
        assert google_calls == []
