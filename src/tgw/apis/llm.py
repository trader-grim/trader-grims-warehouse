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
    Usage (timing + token counts) is recorded to the ai_usage table.
    Pass sku to attribute the call to a specific item in the per-SKU report.
    Pass messages to supply a pre-built multi-turn list (openrouter only);
    system_prompt/user_prompt are ignored when messages is given.
    Pass img_b64_list for multi-image calls (OpenRouter/google_direct only);
    img_b64 used as single-image fallback for Ollama or when list has one entry.
    provider='google_direct' calls Gemini directly via the google-genai SDK
    (no OpenRouter markup) and falls back to OpenRouter automatically on any
    failure — model should be a bare Gemini model id (e.g. 'gemini-2.5-flash-lite').
    provider='openrouter' with a google/* model falls back the other way for
    interactive callers only: the Google free tier (~20 calls/day) is the
    operator emergency reserve, never spent by background jobs.
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
            from tgw import quota

            google_exc: Optional[Exception] = None
            try:
                # Circuit breaker: after a Google 429, background callers stand
                # down for the cooldown instead of burning a doomed attempt per
                # call. The first call after the cooldown expires is the
                # restoration probe — if Google is back, it stays primary.
                quota.precheck(cfg, 'llm_google')
            except quota.QuotaBudgetExceeded as exc:
                google_exc = exc
            if google_exc is None:
                try:
                    gen_cfg = get_task_generation_config(cfg, task)
                    text, usage = _call_google_direct(
                        model, system_prompt, user_prompt, cfg, img_b64_list=_images,
                        max_output_tokens=gen_cfg.get('max_output_tokens'),
                        thinking_budget=gen_cfg.get('thinking_budget'),
                    )
                except Exception as exc:
                    google_exc = exc
            if google_exc is not None:
                # Fail soft to OpenRouter — a Google-side outage/quota/auth error
                # must not dead-letter the job when a paid fallback path exists.
                fallback_model = model if model.startswith('google/') else f'google/{model}'
                log.warning(
                    'google_direct unavailable for task %r (%s) — falling back to '
                    'openrouter/%s: %s', task, model, fallback_model, google_exc,
                )
                text, usage = _call_openrouter(
                    fallback_model, system_prompt, user_prompt, cfg,
                    img_b64_list=_images, messages=messages,
                )
        elif provider == 'deepseek_direct':
            from tgw import quota

            ds_exc: Optional[Exception] = None
            try:
                quota.precheck(cfg, 'llm_deepseek')
            except quota.QuotaBudgetExceeded as exc:
                ds_exc = exc
            if ds_exc is None:
                try:
                    text, usage = _call_deepseek_direct(
                        model, system_prompt, user_prompt, cfg, messages=messages,
                    )
                except Exception as exc:
                    ds_exc = exc
            if ds_exc is not None:
                fallback_model = model if model.startswith('deepseek/') else f'deepseek/{model}'
                log.warning(
                    'deepseek_direct unavailable for task %r (%s) — falling back to '
                    'openrouter/%s: %s', task, model, fallback_model, ds_exc,
                )
                text, usage = _call_openrouter(
                    fallback_model, system_prompt, user_prompt, cfg,
                    img_b64_list=_images, messages=messages,
                )
        elif provider == 'anthropic_direct':
            from tgw import quota

            an_exc: Optional[Exception] = None
            try:
                quota.precheck(cfg, 'llm_anthropic')
            except quota.QuotaBudgetExceeded as exc:
                an_exc = exc
            if an_exc is None:
                try:
                    text, usage = _call_anthropic_direct(
                        model, system_prompt, user_prompt, cfg, messages=messages,
                    )
                except Exception as exc:
                    an_exc = exc
            if an_exc is not None:
                # OpenRouter's alias drops the date suffix Anthropic's direct
                # API requires (e.g. 'claude-haiku-4-5-20251001' ->
                # 'anthropic/claude-haiku-4-5') — strip it for the fallback.
                base_id = model.rsplit('-20', 1)[0] if '-20' in model else model
                fallback_model = model if model.startswith('anthropic/') else f'anthropic/{base_id}'
                log.warning(
                    'anthropic_direct unavailable for task %r (%s) — falling back to '
                    'openrouter/%s: %s', task, model, fallback_model, an_exc,
                )
                text, usage = _call_openrouter(
                    fallback_model, system_prompt, user_prompt, cfg,
                    img_b64_list=_images, messages=messages,
                )
        elif provider == 'openrouter':
            try:
                text, usage = _call_openrouter(
                    model, system_prompt, user_prompt, cfg,
                    img_b64_list=_images, messages=messages,
                )
            except Exception as exc:
                from tgw import quota

                # Operator emergency reserve (Dave, 2026-07-04): Google's ~20
                # free calls/day are held for interactive (C10 operator-lane)
                # callers so the operator can keep working through an OpenRouter
                # outage/credit gap. Background jobs re-raise — worker_base
                # requeues them as transient; they must not drain the reserve.
                if (
                    quota.context_kind() == 'interactive'
                    and messages is None
                    and model.startswith('google/')
                ):
                    reserve_model = model.split('/', 1)[1]
                    log.warning(
                        'openrouter call failed for task %r (%s) — operator '
                        'emergency reserve: google_direct/%s: %s',
                        task, model, reserve_model, exc,
                    )
                    gen_cfg = get_task_generation_config(cfg, task)
                    text, usage = _call_google_direct(
                        reserve_model, system_prompt, user_prompt, cfg,
                        img_b64_list=_images,
                        max_output_tokens=gen_cfg.get('max_output_tokens'),
                        thinking_budget=gen_cfg.get('thinking_budget'),
                    )
                else:
                    raise
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
    max_output_tokens: Optional[int] = None,
    thinking_budget: Optional[int] = None,
) -> tuple:
    """Call Gemini directly via the google-genai SDK — no OpenRouter markup.

    *model* is a bare Gemini model id (e.g. 'gemini-2.5-flash-lite'), not the
    'google/...' OpenRouter form. Raises on any failure (missing SDK, missing
    key, quota, network) — call_model() catches and falls back to OpenRouter.
    Returns (text, usage_dict).

    max_output_tokens/thinking_budget are optional per-task generation knobs
    (see get_task_generation_config / tgw-models.json's per-task
    "generation" field) — None means "leave the SDK/model default alone".
    PP-DEADLETTER-001 (2026-07-17): before this plumbing existed, every
    google_direct call left Gemini's "thinking" budget unset, which for
    gemini-2.5-flash-lite can consume the entire output token budget on
    its internal reasoning before emitting any visible text — producing
    genuine, silent mid-JSON truncation with no error from the SDK. Setting
    thinking_budget=0 for tasks that need bare structured-output (no
    visible reasoning) is a config-only fix once this plumbing exists.
    """
    from tgw.apis.google_genai import _require_genai, load_google_key

    genai = _require_genai()
    api_key = load_google_key(cfg)
    client = genai.Client(
        api_key=api_key,
        http_options={"timeout": _GOOGLE_REQUEST_TIMEOUT_S},
    )

    parts: List[Dict[str, Any]] = [
        {'inline_data': {'mime_type': 'image/jpeg', 'data': b64}}
        for b64 in (img_b64_list or [])
    ]
    parts.append({'text': user_prompt})

    model_ref = model if model.startswith('models/') else f'models/{model}'

    generation_config: Dict[str, Any] = {'system_instruction': system_prompt}
    if max_output_tokens is not None:
        generation_config['max_output_tokens'] = max_output_tokens
    if thinking_budget is not None:
        generation_config['thinking_config'] = {'thinking_budget': thinking_budget}

    from tgw import quota

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_ref,
                contents=[{'role': 'user', 'parts': parts}],
                config=generation_config,
            )
            quota.record(cfg, 'llm_google')
            break
        except Exception as exc:
            last_exc = exc
            quota.record(cfg, 'llm_google')
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            exc_str = str(exc)
            is_quota_exhausted = status == 429 or 'RESOURCE_EXHAUSTED' in exc_str
            # 503/UNAVAILABLE ("high demand... temporary... try again later") is
            # Google's own transient-overload signal, not quota exhaustion --
            # don't feed it into the quota circuit breaker (record_429), just
            # retry with a short backoff (2026-07-14, Dave: saw a bare 503 fall
            # straight to the OpenRouter fallback with zero retry).
            is_transient_overload = status == 503 or 'UNAVAILABLE' in exc_str
            if is_quota_exhausted:
                quota.record_429(cfg, 'llm_google', f'{model}: {exc_str[:150]}')
            if attempt < max_retries - 1:
                if is_quota_exhausted:
                    time.sleep(15 * (attempt + 1))
                    continue
                if is_transient_overload:
                    time.sleep(2 * (attempt + 1))
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
) -> tuple:
    """Call DeepSeek's OpenAI-compatible chat completions API directly — no
    OpenRouter markup. *model* is a bare DeepSeek model id (e.g.
    'deepseek-v4-flash'), not the 'deepseek/...' OpenRouter form. No image
    support — neither current caller (pm_intake, suggestions_classify) sends
    photos. Raises on any failure; call_model() catches and falls back to
    OpenRouter. Returns (text, usage_dict).
    """
    api_key = _load_deepseek_key(cfg)

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
