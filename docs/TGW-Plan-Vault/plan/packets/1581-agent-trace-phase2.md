# Packet: Agent trace logging — Phase 2 (Obsidian view)
Todo: #1581   PP: PP-AGENTTRACE-001   Track: new capability

## Context budget (ALL the model may load)
This packet + `src/tgw/plan_render.py` (whole file — `build_taskboard()`,
`render_taskboard()`, `taskboard_path()`, the atomic-write block ~line 180-210 — this
is the exact pattern to replicate) + `src/tgw/workers/plan_render.py` (whole file —
the worker wrapper pattern) + `src/tgw/todo.py`'s `_enqueue_plan_render()` (~line
139-155, the coalesced-enqueue pattern) + `src/tgw/queue/state_machine.py`'s
`start_agent_run()`/`end_agent_run()`/`get_agent_run()` (just signatures, already
merged in Phase 1 — read only enough to know the row shape) + `src/tgw/queue/worker_base.py`
(the `QueueWorker` base class this new worker subclasses). Nothing else.

## Verified live before this packet was written
- Phase 1 (`agent_runs` table, `start_agent_run`/`end_agent_run`/`get_agent_run` in
  `state_machine.py`) is merged and live in `catio-nix-0.0.1-alpha` — build against it
  directly, no stubbing needed.
- `plan_render`'s coalesced-enqueue pattern (`dedupe_key='plan_render:pending'`,
  `not_before=time.time()+30`, `max_attempts=3`, wrapped in a bare `try/except: pass`
  so a queue problem never breaks the calling operation) is the exact shape to copy —
  confirm this still matches `todo.py`'s `_enqueue_plan_render()` before copying it.
- `render_taskboard()`'s atomic-write block (`tempfile.mkstemp` in the same target
  dir, `os.chmod(tmp, 0o664)` — mkstemp defaults to 600 but vault files are
  group-shared, then `os.replace()`, with a `try/except: os.unlink(tmp)` cleanup on
  failure) is the exact pattern to copy for `TGW-Agent-Runs.md`.
- There is no existing query function for `agent_runs` beyond
  `get_agent_run(run_id)` (single-row lookup) — this packet needs a new **list**
  query (e.g. `list_agent_runs()` in `state_machine.py`, recent-first, reasonably
  capped) since nothing like it exists yet.

## Spec
1. Add `list_agent_runs(cfg=None, *, limit: int = 200) -> List[Dict[str, Any]]` to
   `src/tgw/queue/state_machine.py`, next to `get_agent_run()`. Returns rows as
   dicts (same `RealDictCursor` pattern `get_agent_run` uses), ordered
   `started_at DESC`, capped at `limit` (default 200 — this is a recent-activity
   view, not a full historical dump; note the cap explicitly in a docstring/comment
   so it's not mistaken for "all runs ever," per the no-silent-caps convention —
   log/comment what's excluded, don't just truncate silently). No filtering
   parameters needed yet (Phase 3's `/form/runs` page can filter client-side or
   add its own query later — don't over-build this function for a UI need that
   doesn't exist yet in this packet).
2. New module `src/tgw/agent_trace_render.py` (parallel to `plan_render.py`,
   not added inside that file — keep the two renderers separate since they read
   from different tables and have no shared logic beyond the atomic-write pattern):
   - `AGENT_RUNS_DOC_NAME = 'TGW-Agent-Runs.md'`
   - `build_agent_runs_doc(rows: List[Dict[str, Any]], now: Optional[datetime] = None) -> str`
     — **pure function**, no IO, unit-testable exactly like `build_taskboard()`.
     Markdown table: `| Run ID | Agent Type | PP/Todo | Host | Status | Started | Duration | Summary |`
     — truncate `run_id` for display (e.g. first 12 hex chars — full id in a
     tooltip/title attr isn't possible in plain markdown, so just note truncation
     in a column header or caption), compute `Duration` from `started_at`/`ended_at`
     (blank/"running" if `ended_at` is null), `PP/Todo` combines `pp_ref`/`todo_id`
     with an Obsidian link to the PP's master-plan heading where `pp_ref` is set
     (reuse whatever heading-lookup helper `plan_render.py` already has — don't
     rebuild it). Generated-file banner at top matching `plan_render`'s exact
     wording style ("GENERATED FILE — DO NOT EDIT... rebuilt by `tgw trace`
     start/end / the `agent_run_render` worker").
   - `agent_runs_doc_path(cfg: Dict[str, Any]) -> Path` — same directory as
     `TGW-Taskboard.md` (`docs/TGW-Plan-Vault/plan/`), just a different filename.
   - `render_agent_runs_doc(cfg: Dict[str, Any]) -> Dict[str, Any]` — impure
     wrapper: calls `state_machine.list_agent_runs()`, `build_agent_runs_doc()`,
     atomic-writes via the exact `render_taskboard()` pattern (tempfile in same
     dir, `os.chmod(0o664)`, `os.replace()`, cleanup-on-exception). Returns
     `{'ok': True, 'path': ..., 'count': N}` or `{'ok': False, 'error': ...}` on
     tracker-unavailable, matching `render_taskboard`'s return shape.
3. New queue worker `src/tgw/workers/agent_run_render.py`, `QUEUE_NAME =
   'agent_run_render'`, subclassing `QueueWorker` exactly like
   `workers/plan_render.py` — `handle()` calls `render_agent_runs_doc(self.config)`,
   raises `RuntimeError` on `not result['ok']` so dead-letter/retry works, logs via
   `tgw_logging.log_event('agent_run_render_start'/'agent_run_render_complete', ...)`.
   Include the standard `main()` CLI entrypoint the other workers have (same
   `argparse` shape as `workers/plan_render.py`'s `main()`).
4. Wire the coalesced enqueue: in `state_machine.py`'s `start_agent_run()` and
   `end_agent_run()` (both already exist from Phase 1), after the successful
   INSERT/UPDATE, enqueue a coalesced `agent_run_render` job — same shape as
   `_enqueue_plan_render()` (`dedupe_key='agent_run_render:pending'`,
   `not_before=time.time()+30`, `max_attempts=3`, wrapped in bare
   `try/except: pass` so this never breaks the actual trace-recording operation).
   Either inline the enqueue call in both functions, or factor a small
   `_enqueue_agent_run_render()` helper next to them (mirrors `todo.py`'s
   `_enqueue_plan_render()` naming) — your call, but don't duplicate the
   enqueue-job call verbatim in two places if a one-line helper avoids it.
5. This worker needs a systemd unit for the queue to actually process jobs in
   production — **do NOT create/install a systemd unit or touch
   `~/tgw-flake` in this packet** (that's `nix-flake-maintainer` territory,
   explicitly out of scope, flagged below). The worker module + queue wiring is
   this packet's job; getting it *running* continuously is a follow-up.

## Dataset
No new raw data — this renders existing `agent_runs` Postgres rows (Phase 1's
work) into a derived Obsidian markdown view. Fully recomputable/regenerable,
same as `TGW-Taskboard.md` — if the file is deleted, the next enqueued render
recreates it from the Postgres source of truth.

## Out of scope
- Creating/installing the `agent_run_render` systemd unit or any `~/tgw-flake`
  change — that's a separate nix-flake-maintainer follow-up once this packet's
  worker module exists and is proven correct via manual/CLI invocation.
- The `/form/runs` HTTP UI page — Phase 3 (#1582), separate packet.
- Any filtering/pagination UI — not needed for a markdown table render.
- Changing `list_agent_runs()`'s default `limit` behavior beyond a simple cap —
  no cursor-based pagination, no date-range params, this is a "recent activity"
  view only.

## Acceptance (live)
1. Insert a few real rows via `tgw trace start`/`tgw trace end` (already live from
   Phase 1) with varying `status` values (running, completed, failed) — call
   `list_agent_runs()` directly and show the real returned rows.
2. Call `render_agent_runs_doc(cfg)` against the real config — show the actual
   generated `docs/TGW-Plan-Vault/plan/TGW-Agent-Runs.md` file content (or a
   representative excerpt) after a real render, not a description of what it
   would contain.
3. Manually invoke the new worker's `handle()` (or run `agent_run_render.py`'s
   `main()` against a real enqueued job) and confirm the file updates — show the
   file's mtime/content changing.
4. Confirm the coalesced-enqueue wiring: call `tgw trace start` twice in quick
   succession, show via `psql` (`SELECT * FROM queue_jobs WHERE queue_name =
   'agent_run_render'`) that only one `agent_run_render:pending`-dedupe-keyed job
   exists, not two.
5. Full offline suite (`pytest -q`) passes, including new unit tests for
   `build_agent_runs_doc()` (pure function — test directly with synthetic row
   dicts, no DB needed, same pattern as `tests/test_plan_render.py`) and
   `render_agent_runs_doc()`'s atomic-write behavior.
6. `tgw health` clean/unchanged from its pre-existing state.

## Quota/risk
None — no LLM/API cost, pure Postgres + filesystem + queue work. The only real
risk is the worker having no running systemd unit yet (explicitly out of scope,
noted above) — jobs will queue but not process until that follow-up lands; call
this out plainly in the result manifest so it's not mistaken for "fully live."
