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
# llm_google: Google slashed the flash-lite free tier to 20 requests/day
# (confirmed live 2026-07-04, 2,171 429s). Dave's decision: those 20 calls are
# the OPERATOR EMERGENCY RESERVE — background callers never touch them
# (OpenRouter is primary; see tgw-models.json + llm.py call_model).
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
    'llm_google':            20,
    'llm_openrouter':      None,
}

_DEFAULT_HALT_FRACTION = 0.70

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
