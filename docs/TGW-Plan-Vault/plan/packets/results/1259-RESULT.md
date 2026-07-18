# Result: 1259 nats-health-check
Status: done
Todo: #1259   PP: PP-AIOPS-001

Files touched:
- `src/tgw/health.py` — `check_nats()` reclassified
- `tests/test_health_nats.py` — new, 6 cases pinning the classification
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1259-nats-health-check.md` — breadcrumb

## Live pre-flight (invariant C11 — verified before changing anything)

`tgw health`'s nats check no longer shows `ModuleNotFoundError`. `nats-py`
was already installed into the production venv earlier this session
(Dave-authorized). Live run against production config (as `tgw` user)
confirmed the actual current failure mode is `ConnectionRefusedError:
[Errno 111] Connect call failed ('127.0.0.1', 4222)` — the module imports
fine, there is simply no broker listening, which is expected: standing up
the actual NATS broker is todo #1510, explicitly held pending Dave's
go-ahead. The stale todo body's premise ("No module named 'nats'") is
confirmed gone; the packet's own hypothesis (misleading messaging) is
confirmed correct — the check was still landing in the `failed` list and
`ok: false` despite this being a normal, expected, non-blocking state for
a fire-and-forget audit stream that hasn't been given a broker yet.

## Fix

`check_nats()` in `src/tgw/health.py` now distinguishes three cases instead
of collapsing "any non-connect" into `ok=False`:
- **Connected** → `ok=True, warn=False`.
- **Broker unreachable** (error text matches `no servers available`,
  `connection refused`, `connection timeout`/`timed out`) → `ok=True,
  warn=True`, with detail text explicitly naming `#1510` as the tracked
  follow-up and stating the fire-and-forget/non-blocking guarantee. This
  removes `nats` from `check_all()`'s top-level `failed` list for the
  expected no-broker state.
- **Anything else** (dependency regression / `ImportError`, unexpected
  probe exception, e.g. auth failure) → stays `ok=False, warn=True` — a
  real problem still gets reported red, so a future dependency regression
  (e.g. `nats-py` uninstalled again) is not silently swallowed by this
  reclassification.

No broker was installed, started, or configured. No systemd unit added.
No flake file touched. #1510's scope is untouched.

## Live evidence

Before (shared checkout, unmodified `health.py`, still reachable via prod
`tgw health`):
```
{
  "ok": false,
  "check": "nats",
  "detail": "nats: no servers available for connection",
  ...
  "warn": true
}
...
"failed": ["backups", "nats", "ebay_sync_fallback"]
```

After (worktree code, run live against production config/data as `tgw`
user via `sudo -u tgw env LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH
PYTHONPATH=/opt/TGW/var/worktrees/1259-nats-health-check/src
/opt/TGW/.venvironments/tgw/bin/tgw health` — confirmed
`tgw.health.__file__` resolved to the worktree path first):
```
{
  "ok": true,
  "check": "nats",
  "detail": "module installed, broker unreachable (nats: no servers available for connection) — expected until #1510 stands up the NATS broker; fire-and-forget, item mutations unaffected",
  "elapsed_ms": 2109.4,
  "url": "nats://127.0.0.1:4222",
  "latency_ms": null,
  "streams": [],
  "warn": true
}
...
"failed": ["backups", "ebay_sync_fallback"]     # nats no longer present
```

Tests: `pytest tests/test_health_nats.py -v` → 6 passed. Full suite
(`pytest -q`, PYTHONPATH/LD_LIBRARY_PATH pointed at the worktree, confirmed
via `tgw.health.__file__`): **2476 passed, 1 skipped**, no failures/regressions.

## Deviations from spec

None. The fix was applied only in `check_nats()`'s classification logic, as
the packet anticipated ("if the check's error message is now misleading
... fix the health check's messaging/status classification"). The
production shared checkout (`/opt/TGW/src/trader-grims-warehouse`) was
intentionally left untouched per worktree-isolation rules — this branch is
not merged/pushed, so prod's `tgw health` will keep showing the old
`ok:false` nats entry until this branch is reviewed and stitched.

## Out-of-scope findings filed

None. No new friction encountered beyond what the packet already
anticipated (nats-py already installed this session by explicit Dave
authorization, prior to this packet starting).

## #1510 status

**Still open, unchanged by this packet.** Standing up the actual NATS
broker (systemd unit, flake wiring, or otherwise) remains explicitly held
pending Dave's go-ahead. This packet only corrected how `tgw health`
reports the current no-broker state — it does not stand up a broker, does
not start any service, and does not touch the flake. Once #1510 lands and
a broker is reachable, `check_nats()` will naturally report the green
`ok=True, warn=False, "connected (Nms)"` path with no further code change
needed.
