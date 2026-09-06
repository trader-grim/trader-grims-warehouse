from __future__ import annotations

import json

import pytest
from model_currency.cache import Cache
from model_currency.catalog import collect
from model_currency.http import SourceUnavailable
from model_currency.sources import groq, models_dev, openrouter


def _boom(*a, **k):
    raise SourceUnavailable("source down")


@pytest.fixture
def wire(monkeypatch, openrouter_payload, groq_payload, models_dev_payload):
    """Point every source at its fixture; individual tests override as needed."""
    monkeypatch.setattr(openrouter, "fetch", lambda *a, **k: openrouter_payload["data"])
    monkeypatch.setattr(groq, "fetch", lambda *a, **k: groq_payload["data"])
    monkeypatch.setattr(models_dev, "fetch", lambda *a, **k: models_dev_payload)
    return monkeypatch


def test_collect_all_sources(wire, tmp_path):
    res = collect(cache=Cache(root=tmp_path), models_dev_providers="all")
    assert set(res.sources_ok) == {"openrouter", "groq", "models.dev"}
    assert res.offline is False
    assert res.used_fallback is False
    assert res.models
    sources_seen = {s for m in res.models for s in m.source.split("+")}
    assert {"openrouter", "groq", "models.dev"} <= sources_seen


def test_collect_dedupes_across_sources(wire, tmp_path):
    # groq source and models.dev(groq) both carry llama-3.3-70b-versatile
    res = collect(cache=Cache(root=tmp_path), models_dev_providers="all")
    hits = [m for m in res.models if m.model_id == "llama-3.3-70b-versatile" and m.provider == "groq"]
    assert len(hits) == 1
    assert "+" in hits[0].source  # merged from both


def test_cache_is_used_on_second_call(monkeypatch, tmp_path, openrouter_payload, groq_payload, models_dev_payload):
    calls = {"n": 0}

    def counting_fetch(*a, **k):
        calls["n"] += 1
        return openrouter_payload["data"]

    monkeypatch.setattr(openrouter, "fetch", counting_fetch)
    monkeypatch.setattr(groq, "fetch", lambda *a, **k: groq_payload["data"])
    monkeypatch.setattr(models_dev, "fetch", lambda *a, **k: models_dev_payload)

    cache = Cache(root=tmp_path, ttl=9999)
    collect(cache=cache, cache_ttl=9999)
    collect(cache=cache, cache_ttl=9999)
    assert calls["n"] == 1  # second run served openrouter from cache

    collect(cache=cache, cache_ttl=9999, refresh=True)
    assert calls["n"] == 2  # refresh bypasses cache


def test_stale_cache_used_when_source_fails(monkeypatch, tmp_path, openrouter_payload):
    cache = Cache(root=tmp_path, ttl=1)
    monkeypatch.setattr(openrouter, "fetch", lambda *a, **k: openrouter_payload["data"])
    collect(cache=cache, sources=("openrouter",), cache_ttl=9999)

    # age the openrouter entry, then make it fail — stale copy should be served
    p = cache._path("openrouter")
    payload = json.loads(p.read_text())
    payload["fetched_at"] = 0
    p.write_text(json.dumps(payload))
    monkeypatch.setattr(openrouter, "fetch", _boom)

    res = collect(cache=cache, sources=("openrouter",), cache_ttl=1)
    assert res.sources_stale == ["openrouter"]
    assert res.models
    assert res.offline is True  # no live fetch, no fresh cache


def test_offline_falls_back_to_checked_in_list(monkeypatch, tmp_path):
    monkeypatch.setattr(openrouter, "fetch", _boom)
    monkeypatch.setattr(groq, "fetch", _boom)
    monkeypatch.setattr(models_dev, "fetch", _boom)

    res = collect(cache=Cache(root=tmp_path))
    assert res.used_fallback is True
    assert res.offline is True
    assert res.models
    assert all(m.source == "fallback" for m in res.models)
    assert "as of" in res.summary()


def test_unknown_source_recorded_not_raised(wire, tmp_path):
    res = collect(cache=Cache(root=tmp_path), sources=("openrouter", "bogus"))
    assert "bogus" in res.sources_failed
    assert "openrouter" in res.sources_ok


def test_no_cache_never_touches_disk(monkeypatch, tmp_path, openrouter_payload, groq_payload, models_dev_payload):
    monkeypatch.setattr(openrouter, "fetch", lambda *a, **k: openrouter_payload["data"])
    monkeypatch.setattr(groq, "fetch", lambda *a, **k: groq_payload["data"])
    monkeypatch.setattr(models_dev, "fetch", lambda *a, **k: models_dev_payload)
    monkeypatch.setenv("MODEL_CURRENCY_CACHE", str(tmp_path / "should-not-appear"))
    collect(use_cache=False)
    assert not (tmp_path / "should-not-appear").exists()
