"""Tests for PP-MULTIMODEL-001 cheap model routing — tgw-models.json + llm._DEFAULTS.

Verifies that get_task_model returns the correct (provider, model) for each task
both when the config is populated and when it falls back to _DEFAULTS.
All tests are offline — no API calls, no DB.
"""

from __future__ import annotations

from tgw.apis.llm import _DEFAULTS, get_task_model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(models: dict) -> dict:
    return {"models": models}


# ---------------------------------------------------------------------------
# _DEFAULTS coverage — all routing decisions from PERPLEXITY-007
# ---------------------------------------------------------------------------

def test_defaults_ai_identify_vision_lite():
    # Session 41: moved to google_direct — verified live against the configured
    # key that gemini-2.5-flash-lite is free-tier on this project (see llm.py
    # _DEFAULTS comment). Falls back to OpenRouter automatically on failure.
    p, m = _DEFAULTS["ai_identify"]
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_defaults_alt_text_vision_lite():
    p, m = _DEFAULTS["alt_text"]
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_defaults_suggestions_classify_deepseek():
    p, m = _DEFAULTS["suggestions_classify"]
    assert p == "openrouter"
    assert m == "deepseek/deepseek-v4-flash"


def test_defaults_bulk_classify_gemini_2_5_flash_lite():
    # Session 41: moved off gemini-2.0-flash-lite (deprecated, 0 free quota on our
    # project) to gemini-2.5-flash-lite via google_direct — free, current, at
    # least as capable.
    p, m = _DEFAULTS["bulk_classify"]
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_defaults_ebay_draft_openrouter():
    p, m = _DEFAULTS["ebay_draft"]
    assert p == "google_direct"
    assert m == "gemini-2.5-flash"


def test_defaults_pm_intake_openrouter():
    p, m = _DEFAULTS["pm_intake"]
    assert p == "openrouter"


# ---------------------------------------------------------------------------
# get_task_model — config overrides
# ---------------------------------------------------------------------------

def test_get_task_model_config_overrides_ai_identify():
    cfg = _cfg({"ai_identify": {"provider": "openrouter", "model": "google/gemini-2.5-flash-lite"}})
    p, m = get_task_model(cfg, "ai_identify")
    assert p == "openrouter"
    assert m == "google/gemini-2.5-flash-lite"


def test_get_task_model_config_overrides_alt_text():
    cfg = _cfg({"alt_text": {"provider": "openrouter", "model": "google/gemini-2.5-flash-lite"}})
    p, m = get_task_model(cfg, "alt_text")
    assert p == "openrouter"
    assert m == "google/gemini-2.5-flash-lite"


def test_get_task_model_config_overrides_pm_intake_deepseek():
    cfg = _cfg({"pm_intake": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"}})
    p, m = get_task_model(cfg, "pm_intake")
    assert p == "openrouter"
    assert m == "deepseek/deepseek-v4-flash"


def test_get_task_model_config_overrides_suggestions_classify_deepseek():
    cfg = _cfg({"suggestions_classify": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash"}})
    p, m = get_task_model(cfg, "suggestions_classify")
    assert p == "openrouter"
    assert m == "deepseek/deepseek-v4-flash"


def test_get_task_model_config_overrides_bulk_classify():
    cfg = _cfg({"bulk_classify": {"provider": "openrouter", "model": "google/gemini-2.0-flash-lite"}})
    p, m = get_task_model(cfg, "bulk_classify")
    assert p == "openrouter"
    assert m == "google/gemini-2.0-flash-lite"


def test_get_task_model_falls_back_to_defaults_when_config_empty():
    cfg = _cfg({})
    p, m = get_task_model(cfg, "ai_identify")
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_get_task_model_falls_back_for_bulk_classify_when_config_empty():
    cfg = _cfg({})
    p, m = get_task_model(cfg, "bulk_classify")
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_get_task_model_unknown_task_uses_openrouter_fallback():
    cfg = _cfg({})
    p, m = get_task_model(cfg, "nonexistent_task")
    assert p == "openrouter"
    assert m == "google/gemini-2.0-flash-lite"


def test_get_task_model_partial_config_uses_defaults_for_missing_fields():
    cfg = _cfg({"alt_text": {"model": "google/gemini-2.5-flash-lite"}})
    p, m = get_task_model(cfg, "alt_text")
    assert m == "google/gemini-2.5-flash-lite"
    assert p == "google_direct"  # falls back to _DEFAULTS provider
