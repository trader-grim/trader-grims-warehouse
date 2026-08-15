"""Deterministic, non-provider normalization for known condition aliases."""

from __future__ import annotations

_ALIASES = {
    "pre-owned": "Used",
    "preowned": "Used",
    "for parts": "For parts or not working",
    "parts only": "For parts or not working",
}


def normalized_condition(value: object) -> str | None:
    """Return a canonical condition only for an explicit, lossless alias."""
    if not isinstance(value, str):
        return None
    return _ALIASES.get(" ".join(value.strip().lower().split()))
