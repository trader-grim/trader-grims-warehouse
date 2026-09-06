from __future__ import annotations

import io
import json

import pytest
from model_currency import cli
from model_currency.catalog import CollectResult
from model_currency.models import ModelInfo
from model_currency.sources import groq, models_dev, openrouter


def _models():
    return [
        ModelInfo("groq", "llama-3.3-70b-versatile", 131072, True, True, False, "groq", "Llama 3.3 70B"),
        ModelInfo("openrouter", "qwen/qwen3-coder:free", 262144, True, True, False, "openrouter", "Qwen3 Coder"),
        ModelInfo("openai", "openai/gpt-6-astra", 1_000_000, True, False, False, "openrouter", "GPT-6"),
        ModelInfo("x", "x/whisper", 8192, False, True, False, "groq", "Whisper"),
        ModelInfo("y", "y/old-model", 131072, True, True, True, "openrouter", "Old"),
        ModelInfo("z", "z/tiny-3b", 131072, True, True, False, "groq", "Tiny 3B"),
    ]


def run(argv, result, monkeypatch):
    monkeypatch.setattr(cli, "collect", lambda **kw: result)
    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(argv, out=out, err=err)
    return rc, out.getvalue(), err.getvalue()


def test_default_prints_ranked_free_list(monkeypatch):
    res = CollectResult(models=_models(), sources_ok=["openrouter", "groq", "models.dev"])
    rc, out, err = run([], res, monkeypatch)
    assert rc == 0
    # free + coding-appropriate only: the two 70B/coder models, best first
    assert "qwen/qwen3-coder:free" in out
    assert "llama-3.3-70b-versatile" in out
    # paid, deprecated, no-tools, tiny all excluded
    assert "gpt-6-astra" not in out
    assert "whisper" not in out
    assert "old-model" not in out
    assert "tiny-3b" not in out
    # ranking: qwen (bigger context) before llama
    assert out.index("qwen/qwen3-coder:free") < out.index("llama-3.3-70b-versatile")
    assert "sources —" in err


def test_include_paid(monkeypatch):
    res = CollectResult(models=_models(), sources_ok=["openrouter"])
    rc, out, _ = run(["--include-paid"], res, monkeypatch)
    assert "gpt-6-astra" in out


def test_json_output_is_machine_readable(monkeypatch):
    res = CollectResult(models=_models(), sources_ok=["openrouter"])
    rc, out, err = run(["--json"], res, monkeypatch)
    assert rc == 0
    doc = json.loads(out)
    assert doc["schema"] == "model-currency/result/v1"
    assert doc["count"] == len(doc["models"])
    assert {m["model_id"] for m in doc["models"]} == {"qwen/qwen3-coder:free", "llama-3.3-70b-versatile"}
    assert doc["offline"] is False
    assert doc["filter"]["free_only"] is True


def test_offline_line_on_stderr(monkeypatch):
    res = CollectResult(
        models=[ModelInfo("groq", "llama-3.3-70b-versatile", 131072, True, True, False, "fallback")],
        offline=True,
        used_fallback=True,
    )
    rc, out, err = run([], res, monkeypatch)
    assert rc == 0
    assert "offline — using fallback list" in err
    assert "llama-3.3-70b-versatile" in out


def test_offline_flag_in_json(monkeypatch):
    res = CollectResult(models=[], offline=True, used_fallback=True)
    rc, out, _ = run(["--json"], res, monkeypatch)
    doc = json.loads(out)
    assert doc["offline"] is True and doc["used_fallback"] is True and doc["count"] == 0


def test_limit_and_min_context(monkeypatch):
    res = CollectResult(models=_models())
    _, out, _ = run(["--limit", "1"], res, monkeypatch)
    assert out.count("\n") < 12  # header + 1 row + footer, not the whole list
    _, out2, _ = run(["--min-context", "200000"], res, monkeypatch)
    assert "llama-3.3-70b-versatile" not in out2  # 131k filtered out
    assert "qwen/qwen3-coder:free" in out2


def test_explain_lists_rejections(monkeypatch):
    res = CollectResult(models=_models())
    _, out, _ = run(["--explain"], res, monkeypatch)
    assert "rejected" in out
    assert "no tool-use support" in out


def test_unfiltered_shows_everything_ranked(monkeypatch):
    res = CollectResult(models=_models())
    _, out, _ = run(["--unfiltered", "--include-paid", "--include-deprecated"], res, monkeypatch)
    for frag in ("whisper", "old-model", "tiny-3b", "gpt-6-astra"):
        assert frag in out


def test_version(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0


def test_end_to_end_with_fixture_sources(monkeypatch, openrouter_payload, groq_payload, models_dev_payload, tmp_path):
    monkeypatch.setattr(openrouter, "fetch", lambda *a, **k: openrouter_payload["data"])
    monkeypatch.setattr(groq, "fetch", lambda *a, **k: groq_payload["data"])
    monkeypatch.setattr(models_dev, "fetch", lambda *a, **k: models_dev_payload)
    monkeypatch.setenv("MODEL_CURRENCY_CACHE", str(tmp_path))

    out, err = io.StringIO(), io.StringIO()
    rc = cli.main(["--json", "--models-dev-provider", "all"], out=out, err=err)
    assert rc == 0
    doc = json.loads(out.getvalue())
    assert doc["offline"] is False
    assert doc["count"] > 0
    assert all(m["free"] and m["tool_use"] and not m["deprecated"] for m in doc["models"])
    assert all(m["context"] >= 32768 for m in doc["models"])
