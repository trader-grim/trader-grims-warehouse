"""Thin bridge: TGW → the standalone ``tools/model-currency`` package (LEAF-11-9 W7).

The model-currency tool is deliberately dependency-free and must never import
``tgw.*`` — it is meant to be lifted wholesale into another repo. This module is
the ONLY place the dependency points the other way: it puts ``tools/model-currency``
on ``sys.path`` and re-exposes just what the harness needs.

W2 owns this adapter (per the W7 commit note: "TGW consumes it later via a thin
W1/W2 adapter"). Onboarding's free-model canary tier (W3b) and, later,
``tgw.model_selector`` read free-route availability through here.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_TOOL_ROOT = Path(__file__).resolve().parent.parent.parent / "tools" / "model-currency"


def _model_currency():
    root = str(_TOOL_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import model_currency  # noqa: PLC0415 — deferred: the tool is not a normal dependency

    return model_currency


def reachable_free_models(*, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Ranked free, coding-appropriate models from a **live** source.

    Empty when the tool is offline or fell back to its checked-in list — i.e.
    there is no *reachable* free route right now, so the free-model canary tier
    must SKIP rather than pretend.
    """
    try:
        mc = _model_currency()
        result = mc.collect(timeout=timeout)
    except Exception:
        return []
    if getattr(result, "offline", True) or getattr(result, "used_fallback", True):
        return []
    return [
        {
            "provider": m.provider,
            "model_id": m.model_id,
            "context": m.context,
            "tool_use": m.tool_use,
            "free": m.free,
        }
        for m in mc.rank(result.models)
        if m.free
    ]


def free_route_reachable(*, timeout: float = 10.0) -> bool:
    return bool(reachable_free_models(timeout=timeout))
