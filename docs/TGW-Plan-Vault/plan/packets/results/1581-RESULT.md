# Result: todo #1581 PP-AGENTTRACE-001 Phase 2 — agent-trace Obsidian view
Status: done
Todo: #1581   PP: PP-AGENTTRACE-001

## Files touched
- `src/tgw/queue/state_machine.py` — added `import time`, `list_agent_runs()`
  (recent-first, capped at `limit=200`), `_enqueue_agent_run_render()` helper,
  and wired it into `start_agent_run()` (after successful INSERT) and
  `end_agent_run()` (after successful UPDATE, i.e. never on the
  zero-rows-matched `ValueError` path).
- `src/tgw/agent_trace_render.py` (new) — `AGENT_RUNS_DOC_NAME`,
  `agent_runs_doc_path()`, pure `build_agent_runs_doc()`, impure
  `render_agent_runs_doc()` (atomic tempfile-write + `os.chmod(0o664)` +
  `os.replace()`, same pattern as `plan_render.render_taskboard()`).
- `src/tgw/workers/agent_run_render.py` (new) — `AgentRunRenderWorker`
  (`QUEUE_NAME = 'agent_run_render'`), `main()` CLI entrypoint, same shape
  as `workers/plan_render.py`.
- `tests/test_agent_trace.py` — added tests for `list_agent_runs()` (rows,
  custom limit, ensures table), the coalesced-enqueue wiring in
  `start_agent_run()`/`end_agent_run()` (including "never enqueues on the
  unknown-run_id ValueError path" and "a queue failure never breaks the
  trace-recording call" cases), and patched the two pre-existing Phase 1
  `assert_called_once()` tests (`test_start_agent_run_inserts_and_returns_run_id`,
  `test_end_agent_run_updates_row`) to mock out `_enqueue_agent_run_render`
  so they stay focused on the row insert/update they were testing.
- `tests/test_agent_trace_render.py` (new) — `build_agent_runs_doc()` pure-function
  tests (truncation, running/duration cells, PP/todo link cell with and
  without a heading match, pipe-escaping, empty-state placeholder),
  `render_agent_runs_doc()` atomic-write tests (writes file, reports tracker
  failure, idempotent replace with no leftover temp file), and
  `AgentRunRenderWorker.handle()` tests (calls render, raises `RuntimeError`
  on `not ok`).
- `docs/TGW-Plan-Vault/plan/TGW-Agent-Runs.md` — real generated file, written
  live during acceptance (see Live evidence below); this is the packet's
  actual deliverable output, not test debris.

## Live evidence
1. `tgw trace start`/`tgw trace end` (real CLI, real Postgres) recorded 3 new
   rows with varying status: `test-agent-phase2` (completed),
   `test-agent-phase2b` (failed), `test-agent-phase2c` (left running, no
   `tgw trace end` called). `list_agent_runs(limit=5)` called directly
   returned all 4 real rows (including the pre-existing Phase-1 acceptance
   row `test-packet-1580`), most-recently-started first:
   ```
   bf86c443bdce test-agent-phase2c running   2026-07-20 15:00:16 None
   5d889b2fdc45 test-agent-phase2b failed    2026-07-20 15:00:15 2026-07-20 15:00:29
   6fcfa7a6f87f test-agent-phase2  completed 2026-07-20 15:00:14 2026-07-20 15:00:28
   ba48c3922f02 test-packet-1580  completed  2026-07-20 14:33:06 2026-07-20 14:33:30
   ```
2. `render_agent_runs_doc(cfg)` called against the real config wrote
   `docs/TGW-Plan-Vault/plan/TGW-Agent-Runs.md` — full content confirmed via
   direct read, e.g.:
   ```
   | `bf86c443bdce` | test-agent-phase2c | [[TGW-Master-Plan#PP-AGENTTRACE-001 …\|PP-AGENTTRACE-001]] #1581 | tgw-prod | running | 2026-07-20 15:00 UTC | running |  |
   | `5d889b2fdc45` | test-agent-phase2b | [[TGW-Master-Plan#PP-AGENTTRACE-001 …\|PP-AGENTTRACE-001]] #1581 | tgw-prod | failed | 2026-07-20 15:00 UTC | 13s | phase2 render test run 2 (failed) |
   ```
   `count: 4` returned, matching the row count above.
3. Manually claimed the real queued `agent_run_render` job
   (`claim_queue_jobs` → `mark_running` → `AgentRunRenderWorker.handle(job)` →
   `mark_succeeded`) and confirmed the file's mtime changed
   (`1784559669` → `1784559682`, i.e. re-rendered) and job state moved to
   `succeeded` in `queue_jobs`.
4. Coalesced-enqueue confirmed: 3 `tgw trace start` calls + 2 `tgw trace end`
   calls (5 total `agent_runs` mutations within the 30s window) produced
   exactly **one** `agent_run_render` job —
   `SELECT queue_name, dedupe_key, state, count(*) FROM queue_jobs WHERE
   queue_name='agent_run_render' GROUP BY 1,2,3` returned
   `agent_run_render | agent_run_render:pending | queued | 1` before the
   manual claim, and `... | succeeded | 1` after — never 2+.
5. `PYTHONPATH=.../src pytest -q` (worktree-scoped, confirmed via
   `tgw.agent_trace_render.__file__` resolving under the worktree path, not
   the shared checkout): full suite **2708 passed, 1 skipped**.
6. `tgw health`: ran identically on the worktree and on the unmodified
   shared checkout — same two pre-existing failures both times
   (`backups`, `ebay_sync_fallback`; also a NATS connection traceback in
   the `nats` check) — unrelated to this change, confirmed unchanged
   before/after.

## Deviations from spec
- `list_agent_runs()` signature: implemented as `list_agent_runs(limit:
  int = 200)`, dropping the packet's proposed `cfg=None` leading parameter.
  Verified live before writing code: **no** function in `state_machine.py`
  (including `get_agent_run()`, the function this was explicitly placed
  "next to") takes a `cfg` argument — the module holds its own DSN via
  module-level `init()`, set once by the caller (CLI/worker startup). Adding
  an unused `cfg=None` parameter that nothing in the module's established
  convention uses would have been the actual silent deviation; matching the
  local convention instead. Flagging per Prime Directive 3 rather than
  silently picking one.
- Everything else (table/module/worker shapes, atomic-write pattern,
  coalesced-enqueue shape, `_plan_heading_map()` reuse, run_id truncation to
  12 hex chars, out-of-scope systemd/flake/UI boundaries) implemented as
  specified.

## Out-of-scope findings filed
None — no new operational friction or adjacent bugs encountered this
packet.
