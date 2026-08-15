"""Tests for get_task_model() — config-only model routing (audit#1143
code-review, #1252): a task's provider/model comes ONLY from
cfg['models'][task] (sourced from /opt/TGW/config/tgw-models.json by
tgw.config.load_config). There is no hardcoded per-task fallback in code —
Dave, 2026-07-09: "why change code just to change models?" A missing/
incomplete config entry raises KeyError rather than silently guessing.

All tests are offline — no API calls, no DB.
"""

from __future__ import annotations

import pytest

from tgw.apis.llm import get_task_model

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(models: dict) -> dict:
    return {"models": models}


# ---------------------------------------------------------------------------
# get_task_model — config is the only source
# ---------------------------------------------------------------------------

def test_get_task_model_reads_from_config():
    cfg = _cfg({"ai_identify": {"provider": "google_direct", "model": "gemini-2.5-flash-lite"}})
    p, m = get_task_model(cfg, "ai_identify")
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_get_task_model_config_overrides_alt_text():
    cfg = _cfg({"alt_text": {"provider": "openrouter", "model": "google/gemini-2.5-flash-lite"}})
    p, m = get_task_model(cfg, "alt_text")
    assert p == "openrouter"
    assert m == "google/gemini-2.5-flash-lite"


def test_get_task_model_config_overrides_pm_intake_deepseek():
    cfg = _cfg({"pm_intake": {"provider": "deepseek_direct", "model": "deepseek-v4-flash"}})
    p, m = get_task_model(cfg, "pm_intake")
    assert p == "deepseek_direct"
    assert m == "deepseek-v4-flash"


def test_get_task_model_config_overrides_pm_chat_anthropic():
    cfg = _cfg({"pm_chat": {"provider": "anthropic_direct", "model": "claude-haiku-4-5-20251001"}})
    p, m = get_task_model(cfg, "pm_chat")
    assert p == "anthropic_direct"
    assert m == "claude-haiku-4-5-20251001"


def test_get_task_model_raises_when_task_not_configured():
    cfg = _cfg({})
    with pytest.raises(KeyError, match="ai_identify"):
        get_task_model(cfg, "ai_identify")


def test_get_task_model_raises_for_unknown_task():
    cfg = _cfg({"ai_identify": {"provider": "google_direct", "model": "gemini-2.5-flash-lite"}})
    with pytest.raises(KeyError, match="nonexistent_task"):
        get_task_model(cfg, "nonexistent_task")


def test_get_task_model_raises_when_provider_missing_from_entry():
    cfg = _cfg({"alt_text": {"model": "google/gemini-2.5-flash-lite"}})
    with pytest.raises(KeyError, match="alt_text"):
        get_task_model(cfg, "alt_text")


def test_get_task_model_raises_when_model_missing_from_entry():
    cfg = _cfg({"alt_text": {"provider": "openrouter"}})
    with pytest.raises(KeyError, match="alt_text"):
        get_task_model(cfg, "alt_text")


# ---------------------------------------------------------------------------
# get_task_model — 'use_default' pointer resolution (invariant E15, 2026-07-20)
# ---------------------------------------------------------------------------

def test_get_task_model_resolves_use_default():
    cfg = _cfg({
        "defaults": {
            "default": {"provider": "google_direct", "model": "gemini-2.5-flash-lite"},
        },
        "alt_text": {"use_default": "default"},
    })
    p, m = get_task_model(cfg, "alt_text")
    assert p == "google_direct"
    assert m == "gemini-2.5-flash-lite"


def test_get_task_model_resolves_use_default_deepseek_nonthinking():
    cfg = _cfg({
        "defaults": {
            "default_deepseek_nonthinking": {"provider": "deepseek_direct", "model": "deepseek-v4-flash"},
        },
        "pm_intake": {"use_default": "default_deepseek_nonthinking"},
    })
    p, m = get_task_model(cfg, "pm_intake")
    assert p == "deepseek_direct"
    assert m == "deepseek-v4-flash"


def test_get_task_model_explicit_override_still_works_unchanged():
    cfg = _cfg({
        "defaults": {
            "default": {"provider": "google_direct", "model": "gemini-2.5-flash-lite"},
        },
        "ebay_draft": {"provider": "google_direct", "model": "gemini-3.1-pro-preview"},
    })
    p, m = get_task_model(cfg, "ebay_draft")
    assert p == "google_direct"
    assert m == "gemini-3.1-pro-preview"


def test_get_task_model_raises_for_unknown_use_default_name():
    cfg = _cfg({
        "defaults": {
            "default": {"provider": "google_direct", "model": "gemini-2.5-flash-lite"},
        },
        "alt_text": {"use_default": "nonexistent_default"},
    })
    with pytest.raises(KeyError, match="nonexistent_default"):
        get_task_model(cfg, "alt_text")


def test_get_task_model_raises_when_neither_provider_model_nor_use_default():
    cfg = _cfg({"alt_text": {}})
    with pytest.raises(KeyError, match="alt_text"):
        get_task_model(cfg, "alt_text")
