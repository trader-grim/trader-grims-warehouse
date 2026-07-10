# RESEARCH-1143: queue/state-machine subsystem cohesion+correctness audit

Part of todo #1143 (full-codebase cohesion+correctness audit, staged per-subsystem).
This slice: `src/tgw/queue/`, 5 files / 1,255 lines — the PostgreSQL-backed work
ledger (`state_machine` DB) every worker depends on via `QueueWorker`. Fourth
subsystem after `workers/`, `apis/ebay/`, `http_server.py`.

## Method

Workflow tool, 2 file-groups (state_machine.py core; worker_base.py + launcher.py +
ollama_lock.py + `__init__.py` harness), each candidate finding then adversarially
verified by 3 independent agents (2-of-3 survival bar). 14 agents, ~589k subagent
tokens, ~6.7 min wall.

## Result: 4/4 candidate findings confirmed, 0 refuted

Both file-groups independently surfaced the same top bug from different angles
(Python wrapper vs. SQL schema) — merged into one todo.

| Todo | Severity | File:line | Summary |
|------|----------|-----------|---------|
| #1200 | **correctness/invariant** | state_machine.py:285, schema.sql:213 | `recover_expired_jobs()` demotes an attempts-exhausted, lease-expired job to `'failed'` but never promotes it to `'dead_letter'` the way `mark_failed()` does — the job becomes a permanent zombie, invisible to `tgw health`'s dead-letter alert, the dead-letter CLI/MCP tools, and the stall watchdog. Also silently performs a `leased→failed` transition not present in `ALLOWED_TRANSITIONS` — exactly the DB/Python drift risk invariant D1 warns about. Violates Prime Directive 2 (act on alarms) since the alarm never fires. |
| #1201 | correctness | worker_base.py:231 | The tuned transient-error backoff table (900s for expired token, 1800s for quota/429) is only consulted on the **final** retry attempt — every earlier attempt uses the generic 30-240s exponential backoff, re-hammering an already-broken dependency up to 4 times before the real backoff applies. Same failure class as the 3-day EPS quota exhaustion incident PP-QUOTA-001 was built to prevent. |
| #1202 | cohesion | ollama_lock.py:30 | `acquire_ollama_lock`'s DSN fallback imports `_DSN` **by value** from `state_machine` at module-import time — never reflects a later `state_machine.init(dsn)` override. A caller whose config omits `postgres_dsn` silently connects to a stale/wrong DB target instead of the live one. |

**Priority note:** #1200 and #1201 are both p6 — real alarm-suppression / quota-repeat
risks that belong in the same near-term remediation window as the security batch
(#1174, #1184-#1188) already flagged.

## Remaining subsystems queue

scripts/, nix flake.
