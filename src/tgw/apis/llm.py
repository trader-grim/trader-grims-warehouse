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
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from tgw.queue.ollama_lock import acquire_ollama_lock

log = logging.getLogger(__name__)

# Providers that accept multiple images per call and don't need the Ollama
# is_available() liveness check. Session 41: adding google_direct exposed that
# callers (ai_identify, alt_text) had hardcoded `provider == "openrouter"` / `!=
# "openrouter"` checks that silently assumed only two providers ever existed —
# use this set instead of hardcoding provider names at call sites.
CLOUD_PROVIDERS = {'openrouter', 'google_direct'}

# Hardcoded defaults — override via tgw-models.json (cfg['models'])
#
# Session 41: ai_identify/alt_text/ebay_draft/bulk_classify moved to google_direct —
# verified live against the actual configured key (secrets/google-credentials.json)
# that gemini-2.5-flash and gemini-2.5-flash-lite both return 200 on this project's
# free tier (serviceTier: 'standard', real inference, no billing hit). gemini-2.0-flash
# and gemini-2.0-flash-lite are NOT free on this key/project (429 RESOURCE_EXHAUSTED,
# quota limit: 0 for generate_content_free_tier_requests) — Google's own docs now flag
# 2.0-flash-lite as deprecated, which is almost certainly why; bulk_classify moved to
# gemini-2.5-flash-lite (current, free, at least as capable) rather than paying to keep
# using a deprecated model. call_model() falls back to OpenRouter automatically on any
# google_direct failure, so this is a cost change, not a reliability change.
_DEFAULTS: Dict[str, tuple[str, str]] = {
    'ai_identify':            ('google_direct', 'gemini-2.5-flash-lite'),
    'alt_text':               ('google_direct', 'gemini-2.5-flash-lite'),
    'suggestions_classify':   ('openrouter', 'deepseek/deepseek-v4-flash'),
    'bulk_classify':          ('google_direct', 'gemini-2.5-flash-lite'),
    'pm_chat':                ('openrouter', 'anthropic/claude-haiku-4-5'),
    'ebay_draft':             ('google_direct', 'gemini-2.5-flash'),
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
    Pass img_b64_list for multi-image calls (OpenRouter/google_direct only);
    img_b64 used as single-image fallback for Ollama or when list has one entry.
    provider='google_direct' calls Gemini directly via the google-genai SDK
    (no OpenRouter markup) and falls back to OpenRouter automatically on any
    failure — model should be a bare Gemini model id (e.g. 'gemini-2.5-flash-lite').
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
        if provider == 'google_direct':
            try:
                text, usage = _call_google_direct(
                    model, system_prompt, user_prompt, cfg, img_b64_list=_images,
                )
            except Exception as exc:
                # Fail soft to OpenRouter — a Google-side outage/quota/auth error
                # must not dead-letter the job when a paid fallback path exists.
                fallback_model = model if model.startswith('google/') else f'google/{model}'
                log.warning(
                    'google_direct call failed for task %r (%s) — falling back to '
                    'openrouter/%s: %s', task, model, fallback_model, exc,
                )
                text, usage = _call_openrouter(
                    fallback_model, system_prompt, user_prompt, cfg,
                    img_b64_list=_images, messages=messages,
                )
        elif provider == 'openrouter':
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


def _call_google_direct(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    img_b64_list: Optional[List[str]] = None,
    max_retries: int = 3,
) -> tuple:
    """Call Gemini directly via the google-genai SDK — no OpenRouter markup.

    *model* is a bare Gemini model id (e.g. 'gemini-2.5-flash-lite'), not the
    'google/...' OpenRouter form. Raises on any failure (missing SDK, missing
    key, quota, network) — call_model() catches and falls back to OpenRouter.
    Returns (text, usage_dict).
    """
    from tgw.apis.google_genai import _require_genai, load_google_key

    genai = _require_genai()
    api_key = load_google_key(cfg)
    client = genai.Client(api_key=api_key)

    parts: List[Dict[str, Any]] = [
        {'inline_data': {'mime_type': 'image/jpeg', 'data': b64}}
        for b64 in (img_b64_list or [])
    ]
    parts.append({'text': user_prompt})

    model_ref = model if model.startswith('models/') else f'models/{model}'

    from tgw import quota

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_ref,
                contents=[{'role': 'user', 'parts': parts}],
                config={'system_instruction': system_prompt},
            )
            quota.record(cfg, 'llm_google')
            break
        except Exception as exc:
            last_exc = exc
            quota.record(cfg, 'llm_google')
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            if status == 429 or 'RESOURCE_EXHAUSTED' in str(exc):
                quota.record_429(cfg, 'llm_google', f'{model}: {str(exc)[:150]}')
            if status == 429 and attempt < max_retries - 1:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    else:
        raise last_exc  # pragma: no cover — loop always breaks or raises

    text = response.text or ''
    um = getattr(response, 'usage_metadata', None)
    usage = {
        'prompt_tokens':     getattr(um, 'prompt_token_count', None) if um else None,
        'completion_tokens': getattr(um, 'candidates_token_count', None) if um else None,
        'total_tokens':      getattr(um, 'total_token_count', None) if um else None,
    }
    return text, usage


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

    from tgw import quota

    for attempt in range(max_retries):
        resp = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=60,
        )
        quota.record(cfg, 'llm_openrouter')
        if resp.status_code == 429:
            quota.record_429(cfg, 'llm_openrouter', model)
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
