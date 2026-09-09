"""The thin TGW → tools/model-currency bridge (LEAF-11-9 W2/W7)."""

from __future__ import annotations

from types import SimpleNamespace

from tgw import model_currency_adapter


def test_offline_or_fallback_means_no_reachable_free_route(monkeypatch):
    fake = SimpleNamespace(
        collect=lambda **_k: SimpleNamespace(offline=True, used_fallback=True, models=[]),
        rank=lambda models: models,
    )
    monkeypatch.setattr(model_currency_adapter, "_model_currency", lambda: fake)
    assert model_currency_adapter.reachable_free_models() == []
    assert model_currency_adapter.free_route_reachable() is False


def test_live_source_returns_only_free_models_ranked(monkeypatch):
    m_free = SimpleNamespace(provider="groq", model_id="groq/x:free", context=131072,
                             tool_use=True, free=True)
    m_paid = SimpleNamespace(provider="oai", model_id="oai/y", context=200000,
                             tool_use=True, free=False)
    fake = SimpleNamespace(
        collect=lambda **_k: SimpleNamespace(offline=False, used_fallback=False,
                                             models=[m_paid, m_free]),
        rank=lambda models: [m_free, m_paid],
    )
    monkeypatch.setattr(model_currency_adapter, "_model_currency", lambda: fake)
    out = model_currency_adapter.reachable_free_models()
    assert [m["model_id"] for m in out] == ["groq/x:free"]
    assert model_currency_adapter.free_route_reachable() is True


def test_import_failure_is_swallowed(monkeypatch):
    def boom():
        raise ModuleNotFoundError("model_currency")
    monkeypatch.setattr(model_currency_adapter, "_model_currency", boom)
    assert model_currency_adapter.reachable_free_models() == []


def test_real_tool_is_importable_through_the_adapter():
    # the standalone package must be reachable on sys.path via the adapter
    mc = model_currency_adapter._model_currency()
    assert hasattr(mc, "collect") and hasattr(mc, "rank")


def test_live_coding_models_offline_reports_sources_failed(monkeypatch):
    fake = SimpleNamespace(
        collect=lambda **_k: SimpleNamespace(
            offline=True, used_fallback=True, models=[],
            sources_ok=[], sources_failed={"openrouter": "timed out"},
        ),
    )
    monkeypatch.setattr(model_currency_adapter, "_model_currency", lambda: fake)
    out = model_currency_adapter.live_coding_models()
    assert out == {
        "models": [], "offline": True, "sources_live": [],
        "sources_failed": ["openrouter: timed out"],
    }


def test_live_coding_models_ranks_and_filters_coding_appropriate(monkeypatch):
    m_ok = SimpleNamespace(provider="groq", model_id="groq/x", context=131072,
                           tool_use=True, free=True)
    m_dropped = SimpleNamespace(provider="oai", model_id="oai/tts-1", context=200000,
                                tool_use=False, free=False)
    fake = SimpleNamespace(
        collect=lambda **_k: SimpleNamespace(
            offline=False, used_fallback=False, models=[m_dropped, m_ok],
            sources_ok=["groq"], sources_failed={},
        ),
        rank=lambda models: models,
        coding_appropriate=lambda models: [m for m in models if m.tool_use],
    )
    monkeypatch.setattr(model_currency_adapter, "_model_currency", lambda: fake)
    out = model_currency_adapter.live_coding_models()
    assert [m["model_id"] for m in out["models"]] == ["groq/x"]
    assert out["offline"] is False
    assert out["sources_live"] == ["groq"]
    assert out["sources_failed"] == []
