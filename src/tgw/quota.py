"""
tgw.quota — central metered-API budget layer (PP-QUOTA-001, session 42 / R0.1+R0.2).

Invariant this module asserts: NO code calls a metered API except through a
counted choke point. Every eBay REST/Trading/EPS call and every cloud-LLM call
records against a named daily pool; background callers (workers) are halted
before they can exhaust a pool that the operator's interactive work needs.

Design rules:
  * Accounting must never break the API call it wraps — every state-file
    operation is fail-open (log and continue).
  * Day boundary is midnight America/Los_Angeles (eBay's documented reset).
  * 429s are INCIDENTS, not weather: each one is appended to
    var/log/quota-incidents.jsonl with the caller's identity, and surfaces in
    `tgw health` / the ops digest. A 429 always means a drain bug or a budget
    breach.
  * Enforcement (background halt at `quota_background_halt_fraction`, default
    0.70) applies only to pools with a known budget; unknown pools are
    count-only. Interactive callers are never blocked, only counted.

Budgets default to the limits read live from eBay's Developer Analytics
getRateLimits on 2026-07-02; override per-pool via the `quota_budgets` dict in
tgw-api-config.json.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_RESET_TZ = ZoneInfo('America/Los_Angeles')

_DEFAULT_STATE_PATH = '/opt/TGW/var/run/quota-state.json'
_DEFAULT_INCIDENT_LOG = '/opt/TGW/var/log/quota-incidents.jsonl'

# Daily budgets per pool — from the live getRateLimits probe (2026-07-02),
# snapshot at /opt/TGW/var/run/ebay-rate-limits-probe.json. None = count-only
# (no enforcement) — used where the real limit is unconfirmed (LLM free tiers).
# llm_google/llm_anthropic: paid direct-API keys with no balance endpoint —
# these stay as PROVISIONAL SAFETY CAPS (invariants.md E8 superseded, E9): a
# prior session's requeue script caused a runaway resubmission storm, so the
# cap is the circuit-breaker until todo #1250 is confirmed resolved. Override
# per-pool via `quota_budgets` in tgw-api-config.json.
#
# llm_deepseek: cap REMOVED 2026-09-03 (Dave, direct instruction) — count-only.
# DeepSeek has a real balance endpoint and most DeepSeek-class jobs now route
# to the free OpenCode Zen tier, so the pay-as-you-go key is allowed to run to
# $0 (at which point _call_deepseek_direct's real API error falls back to
# OpenRouter) rather than being cut off early at a synthetic call count. The
# post-429 cooldown in precheck() still applies — that's a rate-limit guard,
# not a cost cap.
_DEFAULT_BUDGETS: Dict[str, Optional[int]] = {
    'ebay_taxonomy':      5_000,
    'ebay_taxonomy_bulk':   100,
    'ebay_inventory': 2_000_000,
    'ebay_account':      25_000,
    'ebay_metadata':      5_000,
    'ebay_marketing':   100_000,
    'ebay_fulfillment': 100_000,
    'ebay_trading':       5_000,
    'ebay_eps':           5_000,
    'ebay_other':          None,
    'llm_google':           300,
    'llm_deepseek':        None,
    'llm_anthropic':        100,
    'llm_openrouter':      None,
    # llm_opencode: OpenCode Zen free tier (deepseek-v4-flash-free). Unmetered,
    # no prepaid balance, no documented rate cap (Dave, 2026-09-03) — count-only,
    # like llm_openrouter. Deliberately not a low-balance pool: routing a task
    # here is how you get OUT of the 'background halted' stall, not into it.
    'llm_opencode':       None,
}

_DEFAULT_HALT_FRACTION = 0.70

# ---------------------------------------------------------------------------
# PP-QUOTA-001 / todo #1337 — real balance / spend-estimate layer
#
# Research (live-verified 2026-07-17, see LLM-Providers-Quotas.md): of the
# three direct-LLM providers, only DeepSeek exposes a real live account
# balance via API (`GET /user/balance`, confirmed live against the real
# key: returned an actual USD balance). Google's Gemini API key has no
# balance/spend endpoint at all — its only billing surface is the separate
# GCP Cloud Billing API, which needs project-level OAuth this key doesn't
# have; this is a permanent gap, not a bug to fix here. Anthropic's
# `/v1/organizations/usage_report/*` needs a separate Admin API key
# (confirmed live: the regular ANTHROPIC_API_KEY gets `authentication_error`
# on it) — not provisioned in secrets_root/tgw.env today; fixable later if
# Dave provisions one, but out of reach right now.
#
# For the two providers with no reachable balance signal, this layer
# hardens the existing call-count budget into an actual USD estimate using
# each provider's own published per-token pricing (checked live against
# each provider's own pricing page 2026-07-17) applied to the real token
# counts already recorded per call in the `ai_usage` table — turning "300
# calls used" into "~$X spent today", which is what actually matters for a
# low-balance warning. This does NOT know the account's real balance for
# Google/Anthropic (impossible without an endpoint) — it estimates SPEND,
# which is the best available proxy improvement.
# ---------------------------------------------------------------------------

# USD per 1,000,000 tokens, keyed by the bare model id as configured in
# tgw-models.json. Update whenever a task's model changes there. DeepSeek's
# entry is kept for documentation/cross-check only — check_deepseek_balance()
# below is the authoritative live signal for that provider, not this table.
_PRICING_USD_PER_1M: Dict[str, Dict[str, float]] = {
    'gemini-2.5-flash-lite':     {'input': 0.10, 'output': 0.40},
    'gemini-3.1-pro-preview':    {'input': 2.00, 'output': 12.00},
    'deepseek-v4-flash':         {'input': 0.14, 'output': 0.28},
    'claude-haiku-4-5-20251001': {'input': 1.00, 'output': 5.00},
}

# Warn when today's estimated spend for a pricing-only pool (no live
# balance API) crosses this many dollars. Provisional default, same spirit
# as quota.py's other PROVISIONAL SAFETY CAPS above — override per-pool via
# `quota_cost_warn_usd` in tgw-api-config.json.
_DEFAULT_COST_WARN_USD: Dict[str, float] = {
    'llm_google': 3.00,
    'llm_anthropic': 3.00,
}

# DeepSeek is a paid pay-as-you-go balance with a live `/user/balance`
# endpoint. Default low-balance threshold is 0.0 (Dave, 2026-09-03): the key
# is meant to run to $0 before falling back to OpenRouter, so `low` never
# trips and `tgw health` shows the balance figure without a [LOW] tag or a
# "background halted" line. `check_deepseek_balance` still reports the real
# number. Set `quota_deepseek_low_balance_usd` in tgw-api-config.json to
# re-arm the early warning if a top-up cushion is wanted again.
_DEFAULT_DEEPSEEK_LOW_BALANCE_USD = 0.0


def estimate_cost_usd(model: str, prompt_tokens: Optional[int],
                       completion_tokens: Optional[int]) -> Optional[float]:
    """USD cost estimate for one call from real token counts, or None if
    *model* has no pricing entry or token counts are missing (e.g. a
    provider that doesn't return usage, or a failed call).

    Fail-open by design, matching this module's other accounting calls
    (never crash a job over a cost estimate) — but a missing pricing entry
    for a model that DID return real token counts is a staleness signal
    worth surfacing (invariant E15 sweep, 2026-07-20: a tgw-models.json
    edit that introduces a new model id without a matching
    `_PRICING_USD_PER_1M` entry would otherwise silently and permanently
    zero out that model's cost tracking with no visible trace)."""
    price = _PRICING_USD_PER_1M.get(model)
    if price is None:
        if prompt_tokens is not None and completion_tokens is not None:
            log.warning(
                "estimate_cost_usd: no pricing entry for model %r — cost "
                "tracking for this model is silently untracked; add it to "
                "_PRICING_USD_PER_1M in quota.py if it's now in active use",
                model,
            )
        return None
    if prompt_tokens is None or completion_tokens is None:
        return None
    return ((prompt_tokens / 1_000_000) * price['input']
            + (completion_tokens / 1_000_000) * price['output'])


def today_cost_usd_by_provider(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, float]:
    """USD spend estimate for today, summed per provider, from real
    `ai_usage` token counts x _PRICING_USD_PER_1M. Fail-open: returns {} on
    any DB error (mirrors this module's other fail-open accounting calls).

    Note on precision: `ai_usage.recorded_at` is queried on a rolling
    24h/UTC-day basis (query_ai_usage's existing bucketing), while this
    module's other day-boundary logic (_day_key) uses Pacific midnight —
    this is an estimate for operator visibility, not exact provider
    billing, and that mismatch is small enough not to matter for a warning
    signal. Documented here so it's not mistaken for a bug later.
    """
    try:
        from tgw.queue.state_machine import query_ai_usage
        rows = query_ai_usage(since_days=1)
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.warning('today_cost_usd_by_provider: ai_usage query failed: %s', exc)
        return {}
    out: Dict[str, float] = {}
    for row in rows:
        cost = estimate_cost_usd(row.get('model') or '', row.get('prompt_tokens'),
                                  row.get('completion_tokens'))
        if cost is None:
            continue
        provider = row.get('provider')
        out[provider] = out.get(provider, 0.0) + cost
    return out


def check_deepseek_balance(cfg: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Live remaining USD balance from DeepSeek's own `/user/balance`
    endpoint (confirmed live 2026-07-17) — the only one of the three
    direct-LLM providers that exposes a real account balance via API; see
    LLM-Providers-Quotas.md. Returns None on any failure or missing key —
    a nice-to-have signal, never a reason to block a call (mirrors
    `health._openrouter_key_limit`'s fail-open contract)."""
    try:
        import requests

        from tgw.apis.secrets import get_api_key
        try:
            key = get_api_key('deepseek')
        except RuntimeError:
            return None
        if not key:
            return None
        resp = requests.get(
            'https://api.deepseek.com/user/balance',
            headers={'Authorization': f'Bearer {key}'}, timeout=5,
        )
        resp.raise_for_status()
        d = resp.json()
        infos = d.get('balance_infos') or []
        usd = next((b for b in infos if b.get('currency') == 'USD'),
                   infos[0] if infos else {})
        total = float(usd.get('total_balance', 0) or 0)
        raw = (cfg or {}).get('raw', cfg or {})
        threshold = float(raw.get('quota_deepseek_low_balance_usd',
                                   _DEFAULT_DEEPSEEK_LOW_BALANCE_USD))
        return {
            'total_balance_usd': total,
            'currency': usd.get('currency', 'USD'),
            'is_available': d.get('is_available'),
            'low': total < threshold,
            'threshold_usd': threshold,
        }
    except Exception as exc:  # noqa: BLE001 — fail-open, nice-to-have signal
        log.warning('check_deepseek_balance failed (fail-open): %s', exc)
        return None


# After any 429 on a pool, background callers stand down for this long. The
# spend counter only knows calls made since this layer existed — a 429 is
# ground truth that the pool is exhausted regardless of what we counted.
_429_COOLDOWN_S = 1800

# Process-wide caller context; workers set 'background' at startup, the web
# server and CLI set 'interactive'. Unset defaults to interactive (never block
# a caller we can't identify — count it and let health surface the gap).
_context_kind = 'interactive'
_context_name = f'pid:{os.getpid()}'
_mem_lock = threading.Lock()


class QuotaBudgetExceeded(RuntimeError):
    """Raised BEFORE a background API call when the pool's halt threshold is
    reached. Message matches a _TRANSIENT_ERRORS pattern in worker_base, so
    jobs requeue with delay (until the PST reset) instead of dead-lettering."""


def set_context(kind: str, name: str) -> None:
    """Declare this process's caller identity: kind 'background'|'interactive'."""
    global _context_kind, _context_name
    _context_kind = kind
    _context_name = name


def context_kind() -> str:
    """This process's current caller kind ('background'|'interactive').

    Reflects C10 operator-lane overrides: worker_base flips the context to
    interactive while running an origin=operator job, so gates keyed on this
    (e.g. the llm.py operator emergency reserve) honour the operator lane."""
    return _context_kind


def pool_for_rest_path(path: str) -> str:
    """Map an eBay REST path to its quota pool (pools = eBay's billing pools)."""
    if '/commerce/taxonomy' in path:
        return 'ebay_taxonomy_bulk' if 'fetch_item_aspects' in path else 'ebay_taxonomy'
    if path.startswith('/sell/inventory'):
        return 'ebay_inventory'
    if path.startswith('/sell/account'):
        return 'ebay_account'
    if path.startswith('/sell/metadata'):
        return 'ebay_metadata'
    if path.startswith('/sell/marketing'):
        return 'ebay_marketing'
    if path.startswith('/sell/fulfillment'):
        return 'ebay_fulfillment'
    return 'ebay_other'


def _day_key() -> str:
    return datetime.now(_RESET_TZ).strftime('%Y-%m-%d')


def _state_path(cfg: Optional[Dict[str, Any]]) -> Path:
    raw = (cfg or {}).get('raw', cfg or {})
    return Path(raw.get('quota_state_path', _DEFAULT_STATE_PATH))


def _incident_log_path(cfg: Optional[Dict[str, Any]]) -> Path:
    raw = (cfg or {}).get('raw', cfg or {})
    return Path(raw.get('quota_incident_log', _DEFAULT_INCIDENT_LOG))


def _budgets(cfg: Optional[Dict[str, Any]]) -> Dict[str, Optional[int]]:
    raw = (cfg or {}).get('raw', cfg or {})
    merged = dict(_DEFAULT_BUDGETS)
    merged.update(raw.get('quota_budgets', {}) or {})
    return merged


def _halt_fraction(cfg: Optional[Dict[str, Any]]) -> float:
    raw = (cfg or {}).get('raw', cfg or {})
    try:
        return float(raw.get('quota_background_halt_fraction', _DEFAULT_HALT_FRACTION))
    except (TypeError, ValueError):
        return _DEFAULT_HALT_FRACTION


def _mutate_state(cfg: Optional[Dict[str, Any]], fn) -> Optional[Dict[str, Any]]:
    """Locked read-modify-write of the shared state file. Fail-open: any error
    is logged and swallowed — accounting must never break the wrapped call.

    The visible file is replaced atomically (temp + rename) so READERS never
    see a partial write. The original truncate-in-place version let a reader
    racing a writer see garbage, fail open, and make one real call against an
    exhausted pool — a stray 429 every minute or two under load (found live
    2026-07-03 during the eBay application-review period, of all times).
    Writers still serialize on a lock file."""
    path = _state_path(cfg)
    lock_path = path.with_suffix('.lock')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _mem_lock, open(lock_path, 'a+', encoding='utf-8') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                state = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                state = {}
            day = _day_key()
            if state.get('day') != day:
                state = {'day': day, 'pools': {}, 'incidents_today': 0}
            fn(state)
            tmp = path.with_suffix(f'.tmp.{os.getpid()}')
            tmp.write_text(json.dumps(state), encoding='utf-8')
            tmp.chmod(0o664)
            os.replace(tmp, path)
        return state
    except Exception as exc:  # noqa: BLE001 — deliberately fail-open
        log.warning('quota state update failed (fail-open): %s', exc)
        return None


def record(cfg: Optional[Dict[str, Any]], pool: str, n: int = 1) -> None:
    """Count n calls against pool for today."""
    def bump(state):
        p = state['pools'].setdefault(pool, {'spent': 0, 'last_429': None,
                                             'callers': {}})
        p['spent'] += n
        p['callers'][_context_name] = p['callers'].get(_context_name, 0) + n
    _mutate_state(cfg, bump)


def record_429(cfg: Optional[Dict[str, Any]], pool: str, detail: str = '') -> None:
    """A 429 is an incident: log it with caller identity + surface in health."""
    now_iso = datetime.now(timezone.utc).isoformat()

    def mark(state):
        p = state['pools'].setdefault(pool, {'spent': 0, 'last_429': None,
                                             'callers': {}})
        p['last_429'] = now_iso
        state['incidents_today'] = state.get('incidents_today', 0) + 1
    _mutate_state(cfg, mark)

    try:
        inc_path = _incident_log_path(cfg)
        inc_path.parent.mkdir(parents=True, exist_ok=True)
        with open(inc_path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({
                'ts': now_iso, 'pool': pool, 'caller': _context_name,
                'kind': _context_kind, 'detail': detail[:300],
            }) + '\n')
    except Exception as exc:  # noqa: BLE001
        log.warning('quota incident log write failed: %s', exc)
    log.error('QUOTA 429 incident: pool=%s caller=%s %s', pool, _context_name, detail[:200])


def precheck(cfg: Optional[Dict[str, Any]], pool: str) -> None:
    """Gate a background call: raise QuotaBudgetExceeded once the pool's halt
    threshold is spent, or while the pool is in post-429 cooldown. Interactive
    callers always pass (counted only)."""
    if _context_kind != 'background':
        return
    state = None
    try:
        text = _state_path(cfg).read_text(encoding='utf-8')
        state = json.loads(text)
    except Exception:  # noqa: BLE001 — no state yet → nothing spent
        return
    if state.get('day') != _day_key():
        return
    pool_state = state.get('pools', {}).get(pool, {})

    last_429 = pool_state.get('last_429')
    if last_429:
        try:
            age_s = (datetime.now(timezone.utc)
                     - datetime.fromisoformat(last_429)).total_seconds()
            if age_s < _429_COOLDOWN_S:
                raise QuotaBudgetExceeded(
                    f'quota budget exhausted for {pool}: 429 received '
                    f'{int(age_s)}s ago — background stand-down for '
                    f'{_429_COOLDOWN_S}s (pool resets 00:00 America/Los_Angeles)')
        except ValueError:
            pass  # unparseable timestamp — ignore, fall through to spend check

    budget = _budgets(cfg).get(pool)
    if not budget:
        return
    spent = pool_state.get('spent', 0)
    if spent >= budget * _halt_fraction(cfg):
        raise QuotaBudgetExceeded(
            f'quota budget exhausted for {pool}: {spent}/{budget} spent '
            f'(background halt at {int(_halt_fraction(cfg) * 100)}% — '
            f'operator reserve protected; resets 00:00 America/Los_Angeles)')


def status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Per-pool spent/budget/last-429 for tgw health and the ops digest."""
    budgets = _budgets(cfg)
    out: Dict[str, Any] = {'day': _day_key(), 'incidents_today': 0, 'pools': {}}
    try:
        state = json.loads(_state_path(cfg).read_text(encoding='utf-8'))
    except Exception:  # noqa: BLE001
        state = {}
    if state.get('day') == _day_key():
        out['incidents_today'] = state.get('incidents_today', 0)
        for pool, p in state.get('pools', {}).items():
            budget = budgets.get(pool)
            out['pools'][pool] = {
                'spent': p.get('spent', 0),
                'budget': budget,
                'fraction': round(p.get('spent', 0) / budget, 3) if budget else None,
                'last_429': p.get('last_429'),
            }
    return out


def _cost_warn_usd(cfg: Optional[Dict[str, Any]]) -> Dict[str, float]:
    raw = (cfg or {}).get('raw', cfg or {})
    merged = dict(_DEFAULT_COST_WARN_USD)
    merged.update(raw.get('quota_cost_warn_usd', {}) or {})
    return merged


def balance_status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Proactive low-balance signal for the three direct-LLM providers
    (todo #1337 / PP-QUOTA-001).

    Returns::

        {'deepseek': {...} | None,       # check_deepseek_balance() — real balance
         'estimated_cost_usd': {'llm_google': 1.23, 'llm_anthropic': 0.01},
         'cost_warn': {'llm_google': False, 'llm_anthropic': False},
         'low_balance': bool}            # True if ANY provider is in warn state

    'deepseek' is None if the live balance call failed/key missing (see
    check_deepseek_balance — fail-open, not itself a warning). Google and
    Anthropic have no balance API (see module docstring above) — their
    entries are estimated spend-today from real ai_usage token counts x
    published pricing, which is the best signal available without a
    provider endpoint that doesn't exist.
    """
    ds = check_deepseek_balance(cfg)
    cost_by_provider = today_cost_usd_by_provider(cfg)
    warn_usd = _cost_warn_usd(cfg)

    provider_to_pool = {'google_direct': 'llm_google', 'anthropic_direct': 'llm_anthropic'}
    est_cost: Dict[str, float] = {}
    cost_warn: Dict[str, bool] = {}
    for provider, pool in provider_to_pool.items():
        amount = round(cost_by_provider.get(provider, 0.0), 4)
        est_cost[pool] = amount
        threshold = warn_usd.get(pool)
        cost_warn[pool] = bool(threshold is not None and amount >= threshold)

    low_balance = bool((ds and ds.get('low')) or any(cost_warn.values()))

    return {
        'deepseek': ds,
        'estimated_cost_usd': est_cost,
        'cost_warn': cost_warn,
        'low_balance': low_balance,
    }
