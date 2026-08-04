# DONE — todo #1202 (audit#1143)

`src/tgw/queue/ollama_lock.py`'s `acquire_ollama_lock()` did
`from tgw.queue.state_machine import _DSN` — a value import that binds
`_DSN` at module-import time. `state_machine.init(dsn)` reassigns the
module-level global `state_machine._DSN`, but `ollama_lock.py`'s local copy
never sees that reassignment. Any caller whose `cfg` dict was missing
`postgres_dsn` would fall back to whatever `_DSN` happened to be at
process-start import time, not the live-configured DSN — silently
connecting to a stale/wrong DB target instead.

## Fix
Changed the import to `from tgw.queue import state_machine` (a module
import) and read `state_machine._DSN` as an attribute at call time
(`cfg.get('postgres_dsn', state_machine._DSN)`), so it always reflects
whatever `init()` last set, exactly like every other caller in the
codebase that reads `state_machine._DSN`/calls `state_machine.init()`.

## Tests
New `tests/test_ollama_lock_dsn.py` (file had no prior test coverage;
all `psycopg2.connect` calls mocked, no real DB connection made):
- `cfg['postgres_dsn']` is used when present
- a `cfg` missing `postgres_dsn`, with `state_machine.init()` called
  *after* `ollama_lock` was already imported (the real-world sequence),
  correctly falls back to the new live DSN — the regression case for
  #1202
- `cfg['postgres_dsn']` still takes precedence over the live
  `state_machine._DSN` when both are present

`pytest -q tests/test_ollama_lock_dsn.py`: 3/3 pass. Full suite: 1998
passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only + mocked connection, no real DB connection made)
- Confirmed against the real `tgw-api-config.json` via `load_config()`:
  `postgres_dsn` is always populated in production `cfg` (defaults to the
  same string as `state_machine._DSN`'s own default) — so this bug has
  been dormant in the currently-deployed setup, not actively causing wrong
  connections today.
- Grepped every `state_machine.init(...)` call site in the codebase (14
  sites across `mcp_server.py`, `api.py`, `http_server.py`,
  `worker_base.py`, `ops_digest.py`, `sku_migration.py`, `pm_intake.py`):
  all derive their dsn from the same `cfg['postgres_dsn']` source, so no
  live divergence exists today — but the fix matters for any future
  multi-DSN scenario (a different host/port config, or Catio's planned
  per-confined-worker DSN scoping).
- Reproduced the exact bug scenario end-to-end: called
  `state_machine.init('dbname=some_future_multihost_dsn user=tgw')` (a
  DSN deliberately different from the real config's), then called
  `acquire_ollama_lock({})` (cfg missing `postgres_dsn`) with
  `psycopg2.connect` mocked — confirmed the connection now correctly
  targets the live-overridden DSN, not a stale import-time snapshot.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no real Postgres connections made during verification.
