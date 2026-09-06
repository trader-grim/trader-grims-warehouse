from __future__ import annotations

from model_currency.filters import coding_appropriate, is_coding_appropriate
from model_currency.models import ModelInfo


def mk(**kw) -> ModelInfo:
    base = dict(
        provider="p",
        model_id="p/m",
        context=131072,
        tool_use=True,
        free=True,
        deprecated=False,
        source="test",
    )
    base.update(kw)
    return ModelInfo(**base)


def test_accepts_a_normal_coding_model():
    ok, reason = is_coding_appropriate(mk())
    assert ok is True
    assert reason == "ok"


def test_rejects_no_tool_use():
    ok, reason = is_coding_appropriate(mk(tool_use=False))
    assert not ok
    assert "tool-use" in reason


def test_rejects_small_context():
    ok, reason = is_coding_appropriate(mk(context=8192))
    assert not ok
    assert "context" in reason
    # threshold is configurable
    ok2, _ = is_coding_appropriate(mk(context=8192), min_context=4096)
    assert ok2 is True


def test_rejects_deprecated_unless_allowed():
    assert is_coding_appropriate(mk(deprecated=True))[0] is False
    assert is_coding_appropriate(mk(deprecated=True), allow_deprecated=True)[0] is True


def test_rejects_tiny_param_models():
    assert is_coding_appropriate(mk(model_id="liquid/lfm-2.5-2.6b:free"))[0] is False
    assert is_coding_appropriate(mk(model_id="meta/llama-3.1-8b-instant"))[0] is True
    assert is_coding_appropriate(mk(model_id="meta/llama-3.3-70b"))[0] is True
    # a '3.8b'-looking version string that is not a param count must not trip it
    assert is_coding_appropriate(mk(model_id="z-ai/glm-4.5-flash", name="GLM 4.5 Flash"))[0] is True


def test_rejects_non_coding_families():
    for mid in ("openai/whisper-large-v3", "x/text-embedding-3", "x/llama-guard-3-8b", "x/sdxl-diffusion"):
        assert is_coding_appropriate(mk(model_id=mid))[0] is False, mid


def test_rejects_non_text_input():
    ok, reason = is_coding_appropriate(mk(modalities_in=("image", "audio")))
    assert not ok
    assert "text" in reason
    assert is_coding_appropriate(mk(modalities_in=("text", "image")))[0] is True


def test_coding_appropriate_list_helper():
    models = [
        mk(model_id="good/one"),
        mk(model_id="bad/no-tools", tool_use=False),
        mk(model_id="good/two", context=40000),
        mk(model_id="bad/tiny-ctx", context=1000),
    ]
    kept = {m.model_id for m in coding_appropriate(models)}
    assert kept == {"good/one", "good/two"}
