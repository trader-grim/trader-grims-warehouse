from __future__ import annotations

import datetime as dt

from model_currency.models import ModelInfo
from model_currency.sources import groq, models_dev, openrouter
from model_currency.sources._util import is_past, is_zero_price


def _by_id(models: list[ModelInfo]) -> dict[str, ModelInfo]:
    return {m.model_id: m for m in models}


# ---------------------------------------------------------------- _util

def test_is_zero_price_variants():
    assert is_zero_price(None) is True
    assert is_zero_price("0") is True
    assert is_zero_price("0.0") is True
    assert is_zero_price(0) is True
    assert is_zero_price("0.00001") is False
    assert is_zero_price("not-a-number") is False


def test_is_past():
    today = dt.date(2026, 9, 6)
    assert is_past("2020-01-01", today=today) is True
    assert is_past("2099-12-31", today=today) is False
    assert is_past("2026-09-06", today=today) is True  # inclusive
    assert is_past("2026-09-30T00:00:00Z", today=today) is False
    assert is_past("", today=today) is False
    assert is_past("garbage", today=today) is False
    assert is_past(None, today=today) is False


# ---------------------------------------------------------------- openrouter

def test_openrouter_parse_flags(openrouter_payload):
    models = _by_id(openrouter.parse(openrouter_payload["data"]))

    free = models["inclusionai/ling-3.0-flash-sante:free"]
    assert free.free is True
    assert free.tool_use is True
    assert free.deprecated is False
    assert free.context == 262144
    assert free.provider == "inclusionai"
    assert free.source == "openrouter"

    paid = models["openai/gpt-6-astra"]
    assert paid.free is False
    assert paid.tool_use is True

    no_tools = models["tencent/hy-mt2-7b"]
    assert no_tools.tool_use is False

    expired = models["nex-agi/nex-n2-mini"]
    assert expired.deprecated is True  # fixture pins expiry to 2020-01-01

    future_expiry = models["dots-studio/dots-3-note-preview:free"]
    assert future_expiry.deprecated is False
    assert any("expires" in n for n in future_expiry.notes)


def test_openrouter_parse_ignores_junk():
    assert openrouter.parse([]) == []
    assert openrouter.parse([{"no_id": True}]) == []


# ---------------------------------------------------------------- groq

def test_groq_parse(groq_payload):
    models = _by_id(groq.parse(groq_payload["data"]))

    chat = models["llama-3.3-70b-versatile"]
    assert chat.tool_use is True
    assert chat.free is True  # Groq free tier
    assert chat.deprecated is False
    assert chat.context == 131072
    assert any("free tier" in n for n in chat.notes)

    whisper = models["whisper-large-v3"]
    assert whisper.tool_use is False
    assert whisper.free is False

    guard = models["meta-llama/llama-prompt-guard-2-86m"]
    assert guard.tool_use is False

    dead = models["llama-3.1-405b-reasoning"]
    assert dead.deprecated is True


def test_groq_requires_key():
    import pytest
    from model_currency.http import SourceUnavailable

    with pytest.raises(SourceUnavailable):
        groq.fetch(None)


# ---------------------------------------------------------------- models.dev

def test_models_dev_parse_default_providers(models_dev_payload):
    models = models_dev.parse(models_dev_payload)  # DEFAULT_PROVIDERS
    ids = {m.model_id for m in models}
    # fixture providers opencode/groq/deepseek/cerebras are all in the default set
    assert "nemotron-3-ultra-free" in ids
    providers = {m.provider for m in models}
    assert providers <= set(models_dev.DEFAULT_PROVIDERS)


def test_models_dev_free_and_toolcall(models_dev_payload):
    models = _by_id(models_dev.parse(models_dev_payload, providers="all"))

    free = models["nemotron-3-ultra-free"]
    assert free.free is True
    assert free.tool_use is True
    assert free.source == "models.dev"

    paid = models["claude-sonnet-4-6"]
    assert paid.free is False
    assert paid.tool_use is True


def test_models_dev_provider_filter(models_dev_payload):
    only_groq = models_dev.parse(models_dev_payload, providers=("groq",))
    assert only_groq
    assert {m.provider for m in only_groq} == {"groq"}
