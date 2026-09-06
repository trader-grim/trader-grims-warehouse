"""Decide whether a model is worth handing to a coding agent, and why not."""

from __future__ import annotations

import re

from .models import ModelInfo

DEFAULT_MIN_CONTEXT = 32_768
DEFAULT_MIN_PARAMS_B = 7.0

# "7b", "3.8 b", "70B" — a parameter count glued to a 'b'. Requires the 'b' to
# end a word so "gpt-4b-turbo" matches but "flash" / "glm-4.5" do not.
_PARAM_B_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)

# Substrings that mark a model as not-for-coding no matter its specs.
_NON_CODING_HINTS = (
    "whisper",
    "-tts",
    "tts-",
    "text-to-speech",
    "-embed",
    "embedding",
    "rerank",
    "guard",
    "moderation",
    "-ocr",
    "-vision-only",
    "image-gen",
    "-diffusion",
    "stable-diffusion",
    "dall-e",
)


def is_coding_appropriate(
    model: ModelInfo,
    *,
    min_context: int = DEFAULT_MIN_CONTEXT,
    min_params_b: float = DEFAULT_MIN_PARAMS_B,
    allow_deprecated: bool = False,
) -> tuple[bool, str]:
    """Return ``(ok, reason)``. ``reason`` explains the rejection (or ``"ok"``)."""
    if model.deprecated and not allow_deprecated:
        return False, "deprecated / past expiry"
    if not model.tool_use:
        return False, "no tool-use support"
    if model.context < min_context:
        return False, f"context {model.context} < {min_context}"

    hay = f"{model.model_id} {model.name}".lower()
    for hint in _NON_CODING_HINTS:
        if hint in hay:
            return False, f"non-coding model family ({hint!r})"

    if model.modalities_in and "text" not in model.modalities_in:
        return False, "does not accept text input"

    match = _PARAM_B_RE.search(hay)
    if match:
        try:
            params = float(match.group(1))
        except ValueError:
            params = None
        if params is not None and params < min_params_b:
            return False, f"{match.group(1)}B parameters < {min_params_b}B (too small)"

    return True, "ok"


def coding_appropriate(
    models: list[ModelInfo],
    *,
    min_context: int = DEFAULT_MIN_CONTEXT,
    min_params_b: float = DEFAULT_MIN_PARAMS_B,
    allow_deprecated: bool = False,
) -> list[ModelInfo]:
    """Keep only the models that pass :func:`is_coding_appropriate`."""
    kept: list[ModelInfo] = []
    for m in models:
        ok, _ = is_coding_appropriate(
            m,
            min_context=min_context,
            min_params_b=min_params_b,
            allow_deprecated=allow_deprecated,
        )
        if ok:
            kept.append(m)
    return kept
