"""
tgw.apis.llm — Unified LLM/vision model dispatcher.

Routes calls to OpenRouter or local Ollama based on the models config
(tgw-models.json, loaded into cfg['models']).

Usage:
    from tgw.apis.llm import call_model, get_task_model

    raw = call_model('ai_identify', system_prompt, user_prompt, cfg, img_b64=img_b64)
    provider, model = get_task_model(cfg, 'ebay_draft')
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from tgw.queue.ollama_lock import acquire_ollama_lock

# Hardcoded defaults — override via tgw-models.json
_DEFAULTS: Dict[str, tuple[str, str]] = {
    'ai_identify':            ('openrouter', 'google/gemini-2.5-flash-lite'),
    'alt_text':               ('openrouter', 'google/gemini-2.5-flash-lite'),
    'suggestions_classify':   ('openrouter', 'deepseek/deepseek-v4-flash'),
    'bulk_classify':          ('openrouter', 'google/gemini-2.0-flash-lite'),
    'pm_chat':                ('openrouter', 'anthropic/claude-haiku-4-5'),
    'ebay_draft':             ('openrouter', 'google/gemini-2.5-flash'),
    'pm_intake':              ('openrouter', 'deepseek/deepseek-v4-flash'),
}


def get_task_model(cfg: Dict[str, Any], task: str) -> tuple[str, str]:
    """Return (provider, model) for a task from cfg['models'], falling back to _DEFAULTS."""
    entry = cfg.get('models', {}).get(task, {})
    default_provider, default_model = _DEFAULTS.get(task, ('openrouter', 'google/gemini-2.0-flash-lite'))
    return entry.get('provider', default_provider), entry.get('model', default_model)


def call_model(
    task: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    img_b64: Optional[str] = None,
    img_b64_list: Optional[List[str]] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    sku: Optional[str] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """
    Call the model configured for task. Returns raw response text.
    provider/model override cfg['models'] when given explicitly.
    Usage (timing + token counts) is recorded to the ai_usage table.
    Pass sku to attribute the call to a specific item in the per-SKU report.
    Pass messages to supply a pre-built multi-turn list (openrouter only);
    system_prompt/user_prompt are ignored when messages is given.
    Pass img_b64_list for multi-image calls (OpenRouter only); img_b64 used
    as single-image fallback for Ollama or when list has one entry.
    """
    if provider is None or model is None:
        _p, _m = get_task_model(cfg, task)
        provider = provider or _p
        model = model or _m

    # Normalise: img_b64_list takes precedence; single img_b64 becomes a list
    _images: List[str] = img_b64_list or ([img_b64] if img_b64 else [])

    if messages is not None:
        input_chars = sum(
            len(m.get('content') or '') for m in messages
            if isinstance(m.get('content'), str)
        )
    else:
        input_chars = len(system_prompt) + len(user_prompt)
    t0 = time.time()
    text = ''
    usage: Dict[str, Any] = {}
    success = True
    error_msg: Optional[str] = None

    try:
        if provider == 'openrouter':
            text, usage = _call_openrouter(
                model, system_prompt, user_prompt, cfg,
                img_b64_list=_images, messages=messages,
            )
        elif _images:
            # Ollama only supports single image; use the first
            text, usage = _call_ollama_vision(model, system_prompt, user_prompt, cfg, _images[0])
        else:
            text, usage = _call_ollama_text(model, system_prompt, user_prompt, cfg)
    except Exception as exc:
        success = False
        error_msg = str(exc)[:500]
        raise
    finally:
        duration_ms = int((time.time() - t0) * 1000)
        _record_usage(
            task, provider, model, duration_ms,
            input_chars=input_chars,
            output_chars=len(text),
            usage=usage,
            success=success,
            error_msg=error_msg,
            sku=sku,
        )

    return text


def _record_usage(
    task: str, provider: str, model: str, duration_ms: int,
    *, input_chars: int, output_chars: int,
    usage: Dict[str, Any], success: bool, error_msg: Optional[str],
    sku: Optional[str] = None,
) -> None:
    """Record a call to the ai_usage table. Never raises."""
    try:
        from tgw.queue.state_machine import record_ai_usage
        record_ai_usage(
            task, provider, model, duration_ms,
            input_chars=input_chars,
            output_chars=output_chars,
            prompt_tokens=usage.get('prompt_tokens'),
            completion_tokens=usage.get('completion_tokens'),
            total_tokens=usage.get('total_tokens'),
            success=success,
            error_msg=error_msg,
            sku=sku,
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------


def _load_openrouter_key(cfg: Dict[str, Any]) -> str:
    """Load OpenRouter API key from secrets file, falling back to env var."""
    import os

    cred_path = cfg.get('openrouter_credentials_path')
    if cred_path and Path(cred_path).exists():
        try:
            return json.loads(Path(cred_path).read_text())['api_key']
        except (KeyError, ValueError, OSError):
            return ''
    return os.environ.get('OPENROUTER_API_KEY', '')


def _call_openrouter(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    img_b64: Optional[str] = None,
    img_b64_list: Optional[List[str]] = None,
    max_retries: int = 3,
    messages: Optional[List[Dict[str, Any]]] = None,
) -> tuple:
    """Call OpenRouter chat completions. Returns (text, usage_dict).

    If *messages* is provided it is used as-is; system_prompt/user_prompt are ignored.
    img_b64_list sends multiple images; img_b64 is a single-image fallback.
    """
    api_key = _load_openrouter_key(cfg)
    if not api_key:
        raise RuntimeError('OpenRouter API key not found in secrets or config')

    # Resolve image list — prefer img_b64_list, fall back to single img_b64
    images = img_b64_list or ([img_b64] if img_b64 else [])

    if messages is not None:
        msg_list: Any = messages
    else:
        user_content: Any
        if images:
            user_content = [{'type': 'text', 'text': user_prompt}]
            for _b64 in images:
                user_content.append(
                    {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{_b64}'}}
                )
        else:
            user_content = user_prompt
        msg_list = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',   'content': user_content},
        ]

    payload = {
        'model': model,
        'messages': msg_list,
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://tgw.local',
        'X-Title': 'TGW',
    }

    for attempt in range(max_retries):
        resp = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=60,
        )
        if resp.status_code == 429 and attempt < max_retries - 1:
            time.sleep(15 * (attempt + 1))
            continue
        break

    resp.raise_for_status()
    body = resp.json()
    text = body['choices'][0]['message']['content']
    raw_usage = body.get('usage') or {}
    usage = {
        'prompt_tokens':     raw_usage.get('prompt_tokens'),
        'completion_tokens': raw_usage.get('completion_tokens'),
        'total_tokens':      raw_usage.get('total_tokens'),
    }
    return text, usage


def _call_ollama_vision(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    img_b64: str,
) -> tuple:
    """Call local Ollama vision (generate) endpoint. Returns (text, usage_dict)."""
    with acquire_ollama_lock(cfg):
        resp = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model': model,
                'prompt': user_prompt,
                'system': system_prompt,
                'images': [img_b64],
                'stream': False,
            },
            timeout=600,
        )
    resp.raise_for_status()
    body = resp.json()
    text = body['response']
    usage = {
        'prompt_tokens':     body.get('prompt_eval_count'),
        'completion_tokens': body.get('eval_count'),
        'total_tokens':      (
            (body.get('prompt_eval_count') or 0) + (body.get('eval_count') or 0)
        ) or None,
    }
    return text, usage


def _call_ollama_text(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
) -> tuple:
    """Call local Ollama chat endpoint (text only). Returns (text, usage_dict)."""
    from tgw.apis.ollama import chat_full as ollama_chat_full
    with acquire_ollama_lock(cfg):
        text, prompt_tokens, completion_tokens = ollama_chat_full(
            model=model,
            messages=[{'role': 'user', 'content': user_prompt}],
            system=system_prompt,
        )
    total = None
    if prompt_tokens is not None and completion_tokens is not None:
        total = prompt_tokens + completion_tokens
    elif prompt_tokens is not None:
        total = prompt_tokens
    elif completion_tokens is not None:
        total = completion_tokens
    usage = {
        'prompt_tokens':     prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens':      total,
    }
    return text, usage
