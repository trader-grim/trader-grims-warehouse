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
    all_messages = []
    if system:
        all_messages.append({'role': 'system', 'content': system})
    all_messages.extend(messages)
    result = _post('/api/chat', {'model': model, 'messages': all_messages, 'stream': False}, timeout)
    return result['message']['content']


def extract_json(text: str) -> Any:
    """Parse JSON from model output, stripping markdown fences if present."""
    text = text.strip()
    # Strip ```json ... ``` fences
    fenced = re.search(r'```(?:json)?\s*([\s\S]+?)\s*```', text)
    if fenced:
        text = fenced.group(1)
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
