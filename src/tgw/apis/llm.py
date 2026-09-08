"""
tgw.apis.llm — Unified LLM/vision model dispatcher.

Routes calls to a direct provider gateway (nous, groq, google_direct,
deepseek_direct, anthropic_direct, opencode_zen), OpenRouter, or local Ollama
based on the models config (tgw-models.json, loaded into cfg['models']).

Failover (PP-STATEMACHINE-002 A4): the task's configured (provider, model) is
tried first, then a fixed provider ORDER — text: nous → groq → deepseek_direct
→ openrouter; vision: google_direct → deepseek_direct → openrouter. The MODEL
each failover provider uses is config, not code (cfg['models']['failover'] /
['failover_vision']); a failover provider with no configured model is skipped.
OpenRouter is the paid last resort: every hop to it is logged loudly and
raises an operator notification (its spend ceiling is enforced on the
OpenRouter account, not here). When the whole chain is exhausted the call
raises — worker_base requeues the job with backoff (a visible hold), never a
silent OpenRouter success and never an immediate dead-letter.

Usage:
    from tgw.apis.llm import call_model, get_task_model

    raw = call_model('ai_identify', system_prompt, user_prompt, cfg, img_b64=img_b64)
    provider, model = get_task_model(cfg, 'ebay_draft')
"""

from __future__ import annotations

import logging
import time
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

# The google-genai SDK otherwise leaves its synchronous HTTP request unbounded.
# Keep this below AIIdentifyWorker's job deadline so a stalled connection is
# retried instead of keeping a live worker stuck indefinitely.
_GOOGLE_REQUEST_TIMEOUT_S = 75

# Dave, 2026-07-09: which provider/model serves a task is a CONFIG decision,
# never a code decision — "why change code just to change models?" This file
# used to carry a hardcoded per-task _DEFAULTS dict as a silent fallback,
# which drifted out of sync with the real decision more than once (e.g. it
# still said OpenRouter-primary/Google-free-tier weeks after Dave flipped to
# direct-provider-primary with paid keys — audit#1143 code-review, #1252).
# The only source of truth now is /opt/TGW/config/tgw-models.json (loaded
# into cfg['models'] by tgw.config.load_config; see its own '_comment' entry
# for the current live decision and the provider/model-id conventions).
#
# Example shape (see tgw-models.json for the real, current values):
#   {"ai_identify": {"provider": "google_direct", "model": "gemini-2.5-flash-lite"}}


def get_task_model(cfg: Dict[str, Any], task: str) -> tuple[str, str]:
    """Return (provider, model) for *task* from cfg['models'][task] — the
    ONLY source; see /opt/TGW/config/tgw-models.json. Raises KeyError with a
    clear message if the task isn't configured there — a task's model must
    never be a silent code-level guess (Dave, 2026-07-09).

    An entry is either a full explicit {'provider', 'model'} override, or a
    {'use_default': '<name>'} pointer into cfg['models']['defaults'] (invariant
    E15, 2026-07-20) — never both, no partial merge. This stays a simple
    two-branch lookup, not a config-merging engine."""
    entry = cfg.get('models', {}).get(task)
    if entry and 'use_default' in entry:
        default_name = entry['use_default']
        default_entry = cfg.get('models', {}).get('defaults', {}).get(default_name)
        if not default_entry or 'provider' not in default_entry or 'model' not in default_entry:
            raise KeyError(
                f"models[{task!r}]['use_default'] names {default_name!r}, which "
                f"has no entry in tgw-models.json's 'defaults' block (need "
                f"{{'provider': ..., 'model': ...}}) — see TGW-Config-Reference.md"
            )
        return default_entry['provider'], default_entry['model']
    if not entry or 'provider' not in entry or 'model' not in entry:
        raise KeyError(
            f"No models[{task!r}] entry in tgw-models.json (need "
            f"{{'provider': ..., 'model': ...}} or {{'use_default': ...}}) — "
            f"see TGW-Config-Reference.md"
        )
    return entry['provider'], entry['model']


def get_task_generation_config(cfg: Dict[str, Any], task: str) -> Dict[str, Any]:
    """Return the optional `generation` sub-dict for *task* from
    cfg['models'][task]['generation'], or {} if absent/not configured.

    General per-task generation knobs (max_output_tokens, thinking_budget,
    ...) — any task entry may set these; absence means "provider default,
    unchanged behavior". This is NOT a bulk_classify-only special case —
    every provider path that supports these knobs reads the same field
    (PP-DEADLETTER-001, 2026-07-17: gemini-2.5-flash-lite's default
    'thinking' budget was silently consuming the entire output token
    budget before any visible text was emitted, causing genuine
    mid-generation truncation of bulk_classify's JSON responses).

    NOTE (2026-07-20, invariant E15 pass): a task pointing at a 'use_default'
    profile does NOT inherit a 'generation' block from that default profile —
    generation knobs stay per-task-only, deliberately, to keep this a simple
    single-entry lookup rather than a config-merging engine. A task that
    needs custom generation knobs must set its own 'generation' key alongside
    its 'use_default' pointer (or use a full explicit {'provider', 'model'}
    entry).
    """
    entry = cfg.get('models', {}).get(task) or {}
    gen = entry.get('generation')
    return gen if isinstance(gen, dict) else {}


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
    Usage (timing + token counts) is recorded to the ai_usage table against the
    provider/model that actually served the call (not the configured primary
    when a failover served it).
    Pass sku to attribute the call to a specific item in the per-SKU report.
    Pass messages to supply a pre-built multi-turn list; system_prompt/
    user_prompt are ignored when messages is given (OpenAI-shaped providers and
    Anthropic honour it; google_direct falls back to the prompt args).
    Pass img_b64_list for multi-image calls; img_b64 is the single-image form.

    A cloud provider's failure moves to the next provider in the fixed order
    (see the module docstring); OpenRouter is the loud paid last resort; an
    exhausted chain raises so worker_base requeues the job (a visible hold).
    Local Ollama has no cloud failover.
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
    provider_used, model_used = provider, model
    success = True
    error_msg: Optional[str] = None

    try:
        if provider in _CLOUD_PROVIDER_POOL:
            text, usage, provider_used, model_used = _run_cloud_chain(
                task, system_prompt, user_prompt, cfg,
                primary_provider=provider, primary_model=model,
                images=_images, messages=messages,
                generation_config=get_task_generation_config(cfg, task),
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
            task, provider_used, model_used, duration_ms,
            input_chars=input_chars,
            output_chars=len(text),
            usage=usage,
            success=success,
            error_msg=error_msg,
            sku=sku,
        )

    return text


# ---------------------------------------------------------------------------
# Provider failover chain (PP-STATEMACHINE-002 A4)
# ---------------------------------------------------------------------------

# Which quota pool each cloud provider records against.  Membership of this
# map is also "is a cloud provider that goes through the failover chain".
_CLOUD_PROVIDER_POOL: Dict[str, str] = {
    'nous':            'llm_nous',
    'groq':            'llm_groq',
    'deepseek_direct': 'llm_deepseek',
    'opencode_zen':    'llm_opencode_zen',
    'anthropic_direct': 'llm_anthropic',
    'google_direct':   'llm_google',
    'openrouter':      'llm_openrouter',
}

# The failover ORDER is architecture, not a model choice (Dave, 2026-07-09:
# "why change code just to change models?").  The MODEL each failover provider
# uses is config — cfg['models']['failover'][<provider>] (text) or
# cfg['models']['failover_vision'][<provider>] (a call carrying images).  A
# failover provider with no configured model is skipped (logged).  The task's
# own configured (provider, model) is always step 0.
_TEXT_FAILOVER_ORDER = ('nous', 'groq', 'deepseek_direct', 'openrouter')
_VISION_FAILOVER_ORDER = ('google_direct', 'deepseek_direct', 'openrouter')


def _notify(title: str, body: str, *, level: str) -> None:
    """Best-effort operator notification; never raises."""
    try:
        from tgw.notify import notify
        notify(title, body, level=level)
    except Exception:  # noqa: BLE001 — notification must never break a call
        log.debug('notify failed (%s): %s', title, body)


def _dispatch_provider(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    *,
    images: List[str],
    messages: Optional[List[Dict[str, Any]]],
    generation_config: Dict[str, Any],
) -> tuple:
    """Call one cloud provider by name. Returns (text, usage). Raises on failure."""
    if provider == 'google_direct':
        return _call_google_direct(
            model, system_prompt, user_prompt, cfg, img_b64_list=images,
            max_output_tokens=generation_config.get('max_output_tokens'),
            thinking_budget=generation_config.get('thinking_budget'),
        )
    if provider == 'nous':
        return _call_nous(model, system_prompt, user_prompt, cfg, messages=messages)
    if provider == 'groq':
        return _call_groq(model, system_prompt, user_prompt, cfg, messages=messages)
    if provider == 'deepseek_direct':
        return _call_deepseek_direct(
            model, system_prompt, user_prompt, cfg, messages=messages,
            img_b64_list=images,
        )
    if provider == 'opencode_zen':
        return _call_opencode_zen(
            model, system_prompt, user_prompt, cfg, messages=messages,
        )
    if provider == 'anthropic_direct':
        return _call_anthropic_direct(
            model, system_prompt, user_prompt, cfg, messages=messages,
        )
    if provider == 'openrouter':
        return _call_openrouter(
            model, system_prompt, user_prompt, cfg,
            img_b64_list=images, messages=messages,
        )
    raise ValueError(f'unknown cloud provider {provider!r}')


def _cloud_chain_steps(
    cfg: Dict[str, Any],
    task: str,
    primary_provider: str,
    primary_model: str,
    *,
    vision: bool,
) -> List[tuple]:
    """Ordered [(provider, model), ...]: the configured primary, then each
    failover provider that has a model configured for it."""
    order = _VISION_FAILOVER_ORDER if vision else _TEXT_FAILOVER_ORDER
    cfg_key = 'failover_vision' if vision else 'failover'
    failover_models = (cfg.get('models', {}) or {}).get(cfg_key, {}) or {}

    steps: List[tuple] = [(primary_provider, primary_model)]
    for prov in order:
        if prov == primary_provider:
            continue
        mdl = failover_models.get(prov)
        if not mdl:
            log.warning(
                'llm chain %s: no cfg models.%s[%r] — %s not used as a failover',
                task, cfg_key, prov, prov,
            )
            continue
        steps.append((prov, mdl))
    return steps


def _run_cloud_chain(
    task: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    *,
    primary_provider: str,
    primary_model: str,
    images: List[str],
    messages: Optional[List[Dict[str, Any]]],
    generation_config: Dict[str, Any],
) -> tuple:
    """Try the configured provider, then the fixed failover order. Returns
    (text, usage, provider_used, model_used). Raises the last error when every
    provider in the chain has failed or been skipped."""
    from tgw import quota

    steps = _cloud_chain_steps(
        cfg, task, primary_provider, primary_model, vision=bool(images),
    )
    last_exc: Optional[Exception] = None

    for idx, (prov, mdl) in enumerate(steps):
        pool = _CLOUD_PROVIDER_POOL.get(prov)
        if pool:
            try:
                quota.precheck(cfg, pool)
            except quota.QuotaBudgetExceeded as exc:
                last_exc = exc
                log.warning(
                    'llm chain %s: skip %s/%s — quota pool %s halted: %s',
                    task, prov, mdl, pool, exc,
                )
                continue

        if prov == 'openrouter':
            log.warning(
                'llm chain %s: FALLING BACK TO OPENROUTER (%s) — paid last '
                'resort, primary providers exhausted%s',
                task, mdl,
                '' if last_exc is None else f'; last error: {repr(last_exc)[:200]}',
            )
            _notify(
                'LLM failover → OpenRouter',
                f'{task}: primary provider chain exhausted, using openrouter/{mdl}',
                level='warning',
            )
        elif idx > 0:
            log.warning(
                'llm chain %s: failover step %d → %s/%s (prior error: %s)',
                task, idx, prov, mdl, repr(last_exc)[:200],
            )

        try:
            text, usage = _dispatch_provider(
                prov, mdl, system_prompt, user_prompt, cfg,
                images=images, messages=messages,
                generation_config=generation_config,
            )
        except Exception as exc:  # noqa: BLE001 — try the next provider
            last_exc = exc
            log.warning(
                'llm chain %s: %s/%s failed: %s', task, prov, mdl, repr(exc)[:300],
            )
            continue

        if idx > 0:
            log.warning(
                'llm chain %s: recovered on failover %s/%s (step %d of %d)',
                task, prov, mdl, idx, len(steps) - 1,
            )
        return text, usage, prov, mdl

    log.error(
        'llm chain %s: EXHAUSTED — tried %s; last error: %s',
        task, [p for p, _ in steps], repr(last_exc)[:300],
    )
    _notify(
        'LLM provider chain exhausted',
        f'{task}: every provider failed ({[p for p, _ in steps]}); '
        f'last error: {str(last_exc)[:160]}',
        level='error',
    )
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f'llm chain for task {task!r} produced no result and no error')


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
    max_output_tokens: Optional[int] = None,
    thinking_budget: Optional[int] = None,
) -> tuple:
    """Call Gemini's generateContent REST API directly with the Google API key.

    No SDK — the same plain-`requests` shape as every other provider here (the
    google-genai SDK is now only needed by the async Batch pipeline in
    tgw.apis.google_genai, which nothing in the per-item hot path calls). This
    removes the "worker runtime is missing google-genai" failure class that had
    every google_direct call silently falling through the chain.

    *model* is a bare Gemini model id (e.g. 'gemini-2.5-flash-lite'), not the
    'google/...' OpenRouter form. img_b64_list sends the full photo set as
    inlineData parts. Raises on any failure; call_model()'s provider chain
    moves on. Returns (text, usage_dict).

    max_output_tokens/thinking_budget are optional per-task generation knobs
    (see get_task_generation_config / tgw-models.json's per-task "generation"
    field) — None means "leave the model default alone". PP-DEADLETTER-001
    (2026-07-17): gemini-2.5-flash-lite's default "thinking" budget can consume
    the entire output token budget on invisible reasoning before any visible
    text — silent mid-JSON truncation. thinking_budget=0 in config is the fix.
    """
    from tgw.apis.google_genai import load_google_key

    api_key = load_google_key(cfg)
    model_id = model[len('models/'):] if model.startswith('models/') else model
    endpoint = (
        'https://generativelanguage.googleapis.com/v1beta/models/'
        f'{model_id}:generateContent'
    )

    parts: List[Dict[str, Any]] = [
        {'inlineData': {'mimeType': 'image/jpeg', 'data': b64}}
        for b64 in (img_b64_list or [])
    ]
    parts.append({'text': user_prompt})

    payload: Dict[str, Any] = {'contents': [{'role': 'user', 'parts': parts}]}
    if system_prompt:
        payload['systemInstruction'] = {'parts': [{'text': system_prompt}]}
    generation_config: Dict[str, Any] = {}
    if max_output_tokens is not None:
        generation_config['maxOutputTokens'] = max_output_tokens
    if thinking_budget is not None:
        generation_config['thinkingConfig'] = {'thinkingBudget': thinking_budget}
    if generation_config:
        payload['generationConfig'] = generation_config

    headers = {'x-goog-api-key': api_key, 'Content-Type': 'application/json'}

    from tgw import quota

    resp = None
    for attempt in range(max_retries):
        resp = requests.post(
            endpoint, headers=headers, json=payload,
            timeout=_GOOGLE_REQUEST_TIMEOUT_S,
        )
        quota.record(cfg, 'llm_google')
        if resp.status_code == 429:
            quota.record_429(cfg, 'llm_google', f'{model}: {resp.text[:150]}')
            if attempt < max_retries - 1:
                time.sleep(15 * (attempt + 1))
                continue
        elif resp.status_code == 503:
            # Google's own transient-overload signal ("high demand... try again
            # later"), not quota exhaustion — short backoff, do NOT trip the
            # llm_google circuit breaker (2026-07-14, Dave: a bare 503 used to
            # fall straight through with zero retry).
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
        break

    resp.raise_for_status()
    body = resp.json()

    candidates = body.get('candidates') or []
    if not candidates:
        block = (body.get('promptFeedback') or {}).get('blockReason')
        raise RuntimeError(
            f'gemini {model_id}: response has no candidates'
            + (f' (blocked: {block})' if block else f' — {str(body)[:200]}')
        )
    out_parts = ((candidates[0].get('content') or {}).get('parts')) or []
    text = ''.join(
        p.get('text', '') for p in out_parts if isinstance(p, dict)
    )

    um = body.get('usageMetadata') or {}
    usage = {
        'prompt_tokens':     um.get('promptTokenCount'),
        'completion_tokens': um.get('candidatesTokenCount'),
        'total_tokens':      um.get('totalTokenCount'),
    }
    return text, usage


def _user_content(user_prompt: str, images: Optional[List[str]]) -> Any:
    """OpenAI chat 'content' for the user turn: a bare string, or a
    text+image_url parts list when *images* (base64 JPEGs) are supplied."""
    if not images:
        return user_prompt
    parts: List[Dict[str, Any]] = [{'type': 'text', 'text': user_prompt}]
    for b64 in images:
        parts.append({
            'type': 'image_url',
            'image_url': {'url': f'data:image/jpeg;base64,{b64}'},
        })
    return parts


def _openai_style_chat(
    *,
    endpoint: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    quota_pool: str,
    messages: Optional[List[Dict[str, Any]]] = None,
    images: Optional[List[str]] = None,
    max_retries: int = 3,
) -> tuple:
    """POST to an OpenAI-compatible /chat/completions endpoint (Nous, Groq).
    Returns (text, usage_dict). Raises on any HTTP or parse failure — the
    caller's provider chain moves on."""
    if messages is not None:
        msg_list: Any = messages
    else:
        msg_list = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',   'content': _user_content(user_prompt, images)},
        ]

    payload = {'model': model, 'messages': msg_list}
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    from tgw import quota

    for attempt in range(max_retries):
        resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        quota.record(cfg, quota_pool)
        if resp.status_code == 429:
            quota.record_429(cfg, quota_pool, model)
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


def _load_nous_key(cfg: Dict[str, Any]) -> str:
    """Load the Nous Research key (NOUS_API_KEY) — see secrets_root/tgw.env."""
    from tgw.apis.secrets import get_api_key

    return get_api_key('nous')


def _call_nous(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    messages: Optional[List[Dict[str, Any]]] = None,
    max_retries: int = 3,
) -> tuple:
    """Call Nous Research's OpenAI-compatible inference API. *model* is a bare
    Nous model id (e.g. 'meituan/longcat-2.0:free'). Free-tier models carry a
    large hourly token allowance — the primary text provider (PP-STATEMACHINE-002
    A4). No image support. Raises on any failure. Returns (text, usage_dict)."""
    return _openai_style_chat(
        endpoint='https://inference-api.nousresearch.com/v1/chat/completions',
        api_key=_load_nous_key(cfg), model=model,
        system_prompt=system_prompt, user_prompt=user_prompt, cfg=cfg,
        quota_pool='llm_nous', messages=messages, max_retries=max_retries,
    )


def _load_groq_key(cfg: Dict[str, Any]) -> str:
    """Load the Groq key (GROQ_API_KEY) — see secrets_root/tgw.env."""
    from tgw.apis.secrets import get_api_key

    return get_api_key('groq')


def _call_groq(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    messages: Optional[List[Dict[str, Any]]] = None,
    max_retries: int = 3,
) -> tuple:
    """Call Groq's OpenAI-compatible API. *model* is a bare Groq model id
    (e.g. 'qwen/qwen3.8-27b'). Free tier covers steady-state text volume — the
    second text provider in the chain, after Nous, before paid DeepSeek. No
    image support. Raises on any failure. Returns (text, usage_dict)."""
    return _openai_style_chat(
        endpoint='https://api.groq.com/openai/v1/chat/completions',
        api_key=_load_groq_key(cfg), model=model,
        system_prompt=system_prompt, user_prompt=user_prompt, cfg=cfg,
        quota_pool='llm_groq', messages=messages, max_retries=max_retries,
    )


def _load_deepseek_key(cfg: Dict[str, Any]) -> str:
    """Load DeepSeek API key via the single-facility DEEPSEEK_API_KEY env
    var (tgw.apis.secrets.get_api_key) — see secrets_root/tgw.env."""
    from tgw.apis.secrets import get_api_key

    return get_api_key('deepseek')


def _call_deepseek_direct(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    messages: Optional[List[Dict[str, Any]]] = None,
    max_retries: int = 3,
    img_b64_list: Optional[List[str]] = None,
) -> tuple:
    """Call DeepSeek's OpenAI-compatible chat completions API directly — no
    OpenRouter markup. *model* is a bare DeepSeek model id (e.g.
    'deepseek-v4-flash'), not the 'deepseek/...' OpenRouter form. Passes images
    as OpenAI image_url parts when *img_b64_list* is given (the vision failover
    for ai_identify / alt_text — the model id must be a vision-capable one from
    cfg['models']['failover_vision']); text-only callers pass nothing and the
    request shape is unchanged. Raises on any failure; call_model()'s provider
    chain moves on. Returns (text, usage_dict).
    """
    api_key = _load_deepseek_key(cfg)

    if messages is not None:
        msg_list: Any = messages
    else:
        msg_list = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',   'content': _user_content(user_prompt, img_b64_list)},
        ]

    payload = {'model': model, 'messages': msg_list}
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    from tgw import quota

    for attempt in range(max_retries):
        resp = requests.post(
            'https://api.deepseek.com/chat/completions',
            headers=headers,
            json=payload,
            timeout=60,
        )
        quota.record(cfg, 'llm_deepseek')
        if resp.status_code == 429:
            quota.record_429(cfg, 'llm_deepseek', model)
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


def _load_opencode_zen_key(cfg: Dict[str, Any]) -> str:
    """Load the OpenCode Zen key via the single-facility OPENCODE_ZEN_API_KEY env
    var (tgw.apis.secrets.get_api_key) — see secrets_root/tgw.env."""
    from tgw.apis.secrets import get_api_key

    return get_api_key('opencode_zen')


def _call_opencode_zen(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    messages: Optional[List[Dict[str, Any]]] = None,
    max_retries: int = 3,
) -> tuple:
    """Call OpenCode Zen's OpenAI-compatible chat/completions gateway directly.

    *model* is a bare Zen model id, e.g. 'deepseek-v4-flash-free'. Zen routes
    DeepSeek through https://opencode.ai/zen/v1/chat/completions with Bearer
    auth — the same request shape as _call_deepseek_direct, different host/key.

    Why this provider exists (Dave, 2026-09-03): the free
    'deepseek-v4-flash-free' id on the operator's OpenCode Zen key is
    **unmetered — no prepaid balance to deplete, no documented rate cap** — so
    it removes the 'background halted: direct-LLM provider low balance' stall
    that gates pm_intake and the helper jobs. Its only limit versus the native
    DeepSeek key is context length: 256k tokens (native v4-flash is 1M). Every
    TGW task routed here is a small text transform — pm_intake (a truncated
    inbox note), suggestions_classify (one string), simple_llm_jobs
    (bounded summarize/classify/extract), Aider edits (repo-map + a few files)
    — all far below 256k, so the cap is not a real restriction for any of
    them. No image support, same as _call_deepseek_direct.

    Raises on any failure; call_model() catches and falls back to OpenRouter
    (deepseek/<model without -free>). Returns (text, usage_dict).
    """
    api_key = _load_opencode_zen_key(cfg)

    if messages is not None:
        msg_list: Any = messages
    else:
        msg_list = [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user',   'content': user_prompt},
        ]

    payload = {'model': model, 'messages': msg_list}
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    from tgw import quota

    for attempt in range(max_retries):
        resp = requests.post(
            'https://opencode.ai/zen/v1/chat/completions',
            headers=headers,
            json=payload,
            timeout=60,
        )
        quota.record(cfg, 'llm_opencode_zen')
        if resp.status_code == 429:
            quota.record_429(cfg, 'llm_opencode_zen', model)
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


def _load_anthropic_key(cfg: Dict[str, Any]) -> str:
    """Load Anthropic API key via the single-facility ANTHROPIC_API_KEY env
    var (tgw.apis.secrets.get_api_key) — see secrets_root/tgw.env."""
    from tgw.apis.secrets import get_api_key

    return get_api_key('anthropic')


_ANTHROPIC_MAX_TOKENS = 4096


def _call_anthropic_direct(
    model: str,
    system_prompt: str,
    user_prompt: str,
    cfg: Dict[str, Any],
    messages: Optional[List[Dict[str, Any]]] = None,
    max_retries: int = 3,
) -> tuple:
    """Call Anthropic's Messages API directly — no OpenRouter markup. *model*
    is a full versioned Claude model id (e.g. 'claude-haiku-4-5-20251001'),
    not the 'anthropic/...' OpenRouter alias. No image support — pm_chat is
    the only current caller and it's text-only. Raises on any failure;
    call_model() catches and falls back to OpenRouter. Returns (text, usage_dict).
    """
    api_key = _load_anthropic_key(cfg)

    # Anthropic's Messages API takes system as a top-level field, not a
    # system-role message — pull one out of *messages* if present (pm_chat
    # builds OpenAI-style message lists with a leading system message).
    system = system_prompt
    msg_list: List[Dict[str, Any]]
    if messages is not None:
        msg_list = [m for m in messages if m.get('role') != 'system']
        sys_msgs = [m['content'] for m in messages if m.get('role') == 'system']
        if sys_msgs:
            system = '\n\n'.join(sys_msgs)
    else:
        msg_list = [{'role': 'user', 'content': user_prompt}]

    payload: Dict[str, Any] = {
        'model': model,
        'max_tokens': _ANTHROPIC_MAX_TOKENS,
        'messages': msg_list,
    }
    if system:
        payload['system'] = system

    headers = {
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
    }

    from tgw import quota

    for attempt in range(max_retries):
        resp = requests.post(
            'https://api.anthropic.com/v1/messages',
            headers=headers,
            json=payload,
            timeout=60,
        )
        quota.record(cfg, 'llm_anthropic')
        if resp.status_code == 429:
            quota.record_429(cfg, 'llm_anthropic', model)
        if resp.status_code == 429 and attempt < max_retries - 1:
            time.sleep(15 * (attempt + 1))
            continue
        break

    resp.raise_for_status()
    body = resp.json()
    text = ''.join(
        block.get('text', '') for block in body.get('content', [])
        if block.get('type') == 'text'
    )
    raw_usage = body.get('usage') or {}
    prompt_tokens = raw_usage.get('input_tokens')
    completion_tokens = raw_usage.get('output_tokens')
    usage = {
        'prompt_tokens':     prompt_tokens,
        'completion_tokens': completion_tokens,
        'total_tokens':      (prompt_tokens + completion_tokens)
                              if prompt_tokens is not None and completion_tokens is not None
                              else None,
    }
    return text, usage


def _load_openrouter_key(cfg: Dict[str, Any]) -> str:
    """Load OpenRouter API key via the single-facility OPENROUTER_API_KEY
    env var (tgw.apis.secrets.get_api_key) — see secrets_root/tgw.env."""
    from tgw.apis.secrets import get_api_key

    return get_api_key('openrouter')


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
