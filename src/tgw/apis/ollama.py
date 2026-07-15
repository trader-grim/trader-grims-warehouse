"""
tgw.apis.ollama — Thin wrapper around the local Ollama HTTP API.

Assumes Ollama is running at localhost:11434 (default). All calls are
synchronous and raise on HTTP or connection errors — callers decide retry.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests

_BASE = 'http://localhost:11434'
_DEFAULT_TIMEOUT = 600  # seconds; CPU-only inference on large prompts can be slow


def _post(path: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    resp = requests.post(f'{_BASE}{path}', json=payload, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def generate(
    model: str,
    prompt: str,
    system: str = '',
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Single-turn generation. Returns the response text."""
    body: Dict[str, Any] = {'model': model, 'prompt': prompt, 'stream': False}
    if system:
        body['system'] = system
    return _post('/api/generate', body, timeout)['response']


def chat(
    model: str,
    messages: List[Dict[str, str]],
    system: str = '',
    timeout: int = _DEFAULT_TIMEOUT,
) -> str:
    """Multi-turn chat. messages = [{'role': 'user'|'assistant', 'content': '...'}]."""
    text, _, _ = chat_full(model, messages, system=system, timeout=timeout)
    return text


def chat_full(
    model: str,
    messages: List[Dict[str, str]],
    system: str = '',
    timeout: int = _DEFAULT_TIMEOUT,
) -> tuple:
    """Like chat() but also returns Ollama token counts.

    Returns ``(text, prompt_eval_count, eval_count)``. Counts are None when the
    Ollama version does not include them in the response.
    """
    all_messages = []
    if system:
        all_messages.append({'role': 'system', 'content': system})
    all_messages.extend(messages)
    result = _post('/api/chat', {'model': model, 'messages': all_messages, 'stream': False}, timeout)
    text = result['message']['content']
    return text, result.get('prompt_eval_count'), result.get('eval_count')


def extract_json(text: str) -> Any:
    """Parse JSON from model output, stripping markdown fences if present.

    Handles both a closed fence (```json ... ```) and an *open-only* fence
    (```json with no closing ``` — e.g. a response truncated before the
    closing marker, or a provider that never emits one). #1393: 95
    ebay_draft dead-letters were a complete-but-unfenced response failing
    to parse purely because the old regex required both markers. If the
    fence-stripped text still isn't valid JSON (genuine truncation
    mid-object), this still raises json.JSONDecodeError -- that's a real
    truncated response, not a parsing bug, and callers should treat it as
    such (see ebay_draft.py's aspect-fill HardFailure path).
    """
    text = text.strip()
    # Strip ```json ... ``` fences (closed case)
    fenced = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
    if fenced:
        text = fenced.group(1)
    else:
        # Open-only fence: strip a leading ``` or ```json marker even with
        # no closing fence present.
        open_fence = re.match(r'```(?:json)?\s*', text)
        if open_fence:
            text = text[open_fence.end():]
    return json.loads(text)


def is_available(model: Optional[str] = None, timeout: int = 5) -> bool:
    """Return True if Ollama is reachable (and optionally has the named model)."""
    try:
        resp = requests.get(f'{_BASE}/api/tags', timeout=timeout)
        resp.raise_for_status()
        if model:
            names = [m['name'] for m in resp.json().get('models', [])]
            return any(n == model or n.startswith(model.split(':')[0]) for n in names)
        return True
    except Exception:
        return False
