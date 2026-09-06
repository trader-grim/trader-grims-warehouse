"""Rank models best-first for coding use."""

from __future__ import annotations

from dataclasses import replace

from .models import ModelInfo

_CONTEXT_CAP = 1_000_000


def score(model: ModelInfo) -> float:
    """Higher is better. Free and tool-use dominate; context is the tie-breaker.

    * free                     +1000
    * tool_use                  +100
    * context / 10_000     up to +100
    * reasoning                    +5
    """
    s = 0.0
    if model.free:
        s += 1000.0
    if model.tool_use:
        s += 100.0
    s += min(max(model.context, 0), _CONTEXT_CAP) / 10_000.0
    if model.reasoning:
        s += 5.0
    return round(s, 3)


def rank(models: list[ModelInfo]) -> list[ModelInfo]:
    """Return a new list, each item carrying its ``score``, sorted best-first.
    Ties break on provider then model id for a stable, readable order."""
    scored = [replace(m, score=score(m)) for m in models]
    scored.sort(key=lambda m: (-m.score, m.provider, m.model_id))
    return scored
