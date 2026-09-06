"""models.dev — the open model database maintained by the opencode project and
used by the opencode CLI for its model list.

``https://models.dev/api.json`` is a single JSON object keyed by provider id;
each provider carries a ``models`` map of ``model_id -> {tool_call, reasoning,
limit:{context,output}, cost:{input,output}, modalities:{input,output}, ...}``.

The full database lists 200+ providers (aggregators, resellers). By default we
read the providers the opencode CLI wires out of the box; pass ``providers="all"``
(or a custom list) to widen it.
"""

from __future__ import annotations

from ..http import get_json
from ..models import ModelInfo
from ._util import is_zero_price

NAME = "models.dev"
ENDPOINT = "https://models.dev/api.json"

# Providers opencode configures by default / that matter for coding work.
DEFAULT_PROVIDERS: tuple[str, ...] = (
    "opencode",
    "openrouter",
    "groq",
    "anthropic",
    "openai",
    "google",
    "google-vertex",
    "deepseek",
    "xai",
    "mistral",
    "cerebras",
    "github-copilot",
    "openrouter-free",
)


def fetch(*, timeout: float = 20.0, endpoint: str = ENDPOINT) -> dict:
    body = get_json(endpoint, timeout=timeout)
    return body if isinstance(body, dict) else {}


def _wanted(providers) -> set[str] | None:
    if providers in (None, "all", ("all",), ["all"]):
        return None
    return {p for p in providers}


def parse(db: dict, providers=DEFAULT_PROVIDERS) -> list[ModelInfo]:
    want = _wanted(providers)
    out: list[ModelInfo] = []
    for pid, pdata in (db or {}).items():
        if want is not None and pid not in want:
            continue
        if not isinstance(pdata, dict):
            continue
        for mid, m in (pdata.get("models") or {}).items():
            if not isinstance(m, dict):
                continue
            cost = m.get("cost") or {}
            free = (not cost) or (is_zero_price(cost.get("input")) and is_zero_price(cost.get("output")))
            limit = m.get("limit") or {}
            mods = m.get("modalities") or {}
            out.append(
                ModelInfo(
                    provider=pid,
                    model_id=mid,
                    context=int(limit.get("context") or 0),
                    tool_use=bool(m.get("tool_call")),
                    free=bool(free),
                    # models.dev carries no explicit deprecation flag.
                    deprecated=False,
                    source=NAME,
                    name=m.get("name") or mid,
                    modalities_in=tuple(mods.get("input") or []),
                    reasoning=bool(m.get("reasoning")),
                )
            )
    return out
