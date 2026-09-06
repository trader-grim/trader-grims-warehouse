"""Groq ``/openai/v1/models``.

Requires ``GROQ_API_KEY``. The response is the OpenAI ``/models`` shape plus
Groq extras (``active``, ``context_window``, ``max_completion_tokens``).

Groq's model endpoint exposes no per-token pricing and no tool-use flag:

* ``free`` — Groq serves its chat models on a rate-limited free tier, so chat
  models are marked free with a note. (Per-token developer-tier pricing exists
  but is not returned here.)
* ``tool_use`` — assumed True for chat model families, False for the
  transcription / TTS / safety families that cannot do agentic work anyway.
"""

from __future__ import annotations

from ..http import SourceUnavailable, get_json
from ..models import ModelInfo

NAME = "groq"
ENDPOINT = "https://api.groq.com/openai/v1/models"

# Families that are not chat/coding models regardless of anything else.
_NON_CHAT_HINTS = ("whisper", "-tts", "tts-", "guard", "prompt-guard", "-embed", "embedding", "rerank")


def fetch(api_key: str | None, *, timeout: float = 15.0, endpoint: str = ENDPOINT) -> list[dict]:
    if not api_key:
        raise SourceUnavailable("groq: GROQ_API_KEY is not set")
    body = get_json(endpoint, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    if not isinstance(body, dict):
        return []
    return list(body.get("data") or [])


def _is_chat(model_id: str) -> bool:
    low = model_id.lower()
    return not any(h in low for h in _NON_CHAT_HINTS)


def parse(raw_models: list[dict]) -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for m in raw_models or []:
        mid = m.get("id")
        if not mid:
            continue
        chat = _is_chat(mid)
        notes = ["Groq free tier — rate-limited"] if chat else ["non-chat model family"]
        out.append(
            ModelInfo(
                provider=NAME,
                model_id=mid,
                context=int(m.get("context_window") or 0),
                tool_use=chat,
                free=chat,
                deprecated=(m.get("active") is False),
                source=NAME,
                name=str(mid),
                modalities_in=("text",) if chat else (),
                notes=tuple(notes),
            )
        )
    return out
