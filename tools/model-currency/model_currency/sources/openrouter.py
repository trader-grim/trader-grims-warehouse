"""OpenRouter ``/api/v1/models``.

The endpoint is public; an API key is optional and only used if present in the
environment (it does not change the response today, but keeps us honest if
OpenRouter starts gating it).
"""

from __future__ import annotations

from ..http import get_json
from ..models import ModelInfo
from ._util import is_past, is_zero_price

NAME = "openrouter"
ENDPOINT = "https://openrouter.ai/api/v1/models"

_TOOL_PARAMS = {"tools", "tool_choice"}
_REASONING_PARAMS = {"reasoning", "include_reasoning", "reasoning_effort"}


def fetch(api_key: str | None = None, *, timeout: float = 15.0, endpoint: str = ENDPOINT) -> list[dict]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    body = get_json(endpoint, headers=headers, timeout=timeout)
    if not isinstance(body, dict):
        return []
    return list(body.get("data") or [])


def parse(raw_models: list[dict]) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in raw_models or []:
        mid = m.get("id")
        if not mid:
            continue
        params = set(m.get("supported_parameters") or [])
        pricing = m.get("pricing") or {}
        top = m.get("top_provider") or {}
        arch = m.get("architecture") or {}

        is_free_variant = mid.endswith(":free")
        zero_priced = is_zero_price(pricing.get("prompt")) and is_zero_price(pricing.get("completion"))
        expiry = m.get("expiration_date")

        notes: list[str] = []
        if expiry:
            notes.append(f"expires {expiry}")
        if is_free_variant and not zero_priced:
            notes.append("free variant (may be rate-limited)")

        out.append(
            ModelInfo(
                provider=mid.split("/", 1)[0] if "/" in mid else "openrouter",
                model_id=mid,
                context=int(m.get("context_length") or top.get("context_length") or 0),
                tool_use=bool(params & _TOOL_PARAMS),
                free=is_free_variant or zero_priced,
                deprecated=is_past(expiry),
                source=NAME,
                name=m.get("name") or "",
                modalities_in=tuple(arch.get("input_modalities") or []),
                reasoning=bool(params & _REASONING_PARAMS),
                notes=tuple(notes),
            )
        )
    return out
