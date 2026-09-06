"""model-currency — a standalone ranked catalogue of live, coding-appropriate LLMs.

Queries OpenRouter, Groq, and the opencode / models.dev model database, filters
to models that are still live and usable for agentic coding work, and returns a
ranked list. Short-TTL on-disk cache; a checked-in fallback list keeps it useful
with no network.

This package deliberately has **zero third-party dependencies** and never
imports anything project-specific. It is meant to be copied wholesale into
another repository.

Public API::

    from model_currency import collect, rank, ModelInfo

    result = collect()                 # CollectResult
    for m in rank(result.models):      # ranked list[ModelInfo]
        print(m.provider, m.model_id, m.context, m.tool_use, m.free)
"""

from __future__ import annotations

from .catalog import CollectResult, collect
from .filters import DEFAULT_MIN_CONTEXT, coding_appropriate, is_coding_appropriate
from .models import ModelInfo
from .ranking import rank, score

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CollectResult",
    "collect",
    "ModelInfo",
    "rank",
    "score",
    "coding_appropriate",
    "is_coding_appropriate",
    "DEFAULT_MIN_CONTEXT",
]
