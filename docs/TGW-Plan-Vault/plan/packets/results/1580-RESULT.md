# Result: todo #1580 agent-trace-phase1
Status: done
Todo: #1580   PP: PP-AGENTTRACE-001
Files touched:
- src/tgw/queue/state_machine.py (`_AGENT_RUNS_DDL`, `_ensure_agent_runs_table()`,
  `start_agent_run()`, `end_agent_run()`, `get_agent_run()`)
- src/tgw/queue/schema.sql (bootstrap-doc copy of `agent_runs`, plus a drift-warning
  comment added to the pre-existing `ai_usage` block)
- src/tgw/logging.py (`archive_transcript()`, `DEFAULT_AGENT_TRACES_ROOT`)
- src/tgw/api.py (`tgw trace start`/`tgw trace end` CLI subcommands + parser wiring,
  `cmd_trace_start()`, `cmd_trace_end()`, `_detect_host()`, `_detect_git_branch()`)
- tests/test_agent_trace.py (new — 26 tests, all offline/mocked)
- docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1580-agent-trace-phase1.md (breadcrumb)

## Live evidence
All commands run as `tgw` via `sudo -u tgw`, against the real `state_machine` Postgres
DB and the real `/opt/TGW/var/agent-traces/` filesystem path (self-apply DDL created
the table on first call — it did not exist beforehand, confirmed via `psql \d agent_runs`
failing before the first `tgw trace start`).

1. `tgw trace start --agent-type test-packet-1580 --todo 1580 --pp PP-AGENTTRACE-001`
   → stdout printed **only** the run_id: `ba48c3922f024d6dbbe5cc17a9e6868b`. Row confirmed
   via `psql`:
   ```
   run_id: ba48c3922f024d6dbbe5cc17a9e6868b | agent_type: test-packet-1580 | todo_id: 1580
   | pp_ref: PP-AGENTTRACE-001 | host: tgw-prod | git_branch: (empty — see deviation below)
   | started_at: 2026-07-20 14:33:06 | status: running
   ```
2. `tgw trace end ba48c3922f024d6dbbe5cc17a9e6868b --status completed --summary "acceptance test"`
   → stdout: `Ended run ba48c3922f024d6dbbe5cc17a9e6868b: status=completed`. Fresh `psql`
   query confirmed: `status=completed`, `summary='acceptance test'`, `ended IS NOT NULL = t`.
3. `archive_transcript()` called live against two real small files:
   `/opt/TGW/var/agent-traces/2026-07-20/ba48c3922f024d6dbbe5cc17a9e6868b.jsonl` and
   `.../second-run-id.jsonl`, both present simultaneously (no clobbering), owned
   `tgw:tgw`, mode `0660` (`-rw-rw----+`), directory `0770`
   (`drwxrwx---+`) — confirmed via `ls -la` as `tgw`.
4. `PYTHONPATH=.../src LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH pytest -q` from inside the
   worktree: **2686 passed, 1 skipped** (full suite, confirmed `tgw.api.__file__`
   resolved under the worktree path before running). New test file alone:
   **26 passed**.
5. `tgw health` (as `tgw`, shared checkout — unavoidable, `tgw` invokes the installed
   editable package): `failed: ["backups", "ebay_sync_fallback"]`. Confirmed
   pre-existing/unrelated — `git status` on the shared checkout shows zero diff under
   `src/tgw/`, so these failures predate and are untouched by this packet
   (acceptance item 5's "unchanged from pre-existing state").

## Live bug found + fixed during acceptance testing
`end_agent_run()` originally performed a bare `UPDATE ... WHERE run_id = %s` with no
rowcount check. Live-reproduced: calling `tgw trace end` on a run_id that was never
created via `start_agent_run()` printed `"Ended run second-run-id: status=escalated"`
with exit code 0 — a **silent no-op reported as success**, the same failure class as
invariant C14 ("an operator's correction either takes effect or is visibly reported
as failed — never silently lost"). Fixed: `end_agent_run()` now checks
`cur.rowcount == 0` and raises `ValueError`; `cmd_trace_end()` surfaces this as
`{ok: False, error: ...}` / CLI exit code 1. Re-verified live after the fix: same
command now correctly prints `Error: end_agent_run: no agent_runs row found for
run_id='second-run-id'` and exits 1. Two new tests cover this
(`test_end_agent_run_raises_on_unknown_run_id`,
`test_cmd_trace_end_returns_error_on_unknown_run_id`).

## Deviations from spec
- **`get_agent_run(run_id)` added** — not one of the packet's named "two functions."
  Added as a minimal read-back helper because (a) the spec's own acceptance/test
  requirements need a way to read a row back, and (b) Phase 2/3 (Obsidian render,
  `/form/runs` UI) will need query access to this table regardless. Flagged here per
  the "no silent substitution" rule rather than added quietly.
- **`end_agent_run()` raises `ValueError` on an unknown run_id** — not explicitly
  specified in the packet, added after live-reproducing the silent-no-op bug above.
  This is an addition to the spec's stated behavior (UPDATE semantics), not a removal
  or a substitution of anything the packet asked for.
- **`git_branch` was empty in the live acceptance run** — not a code bug: the worktree
  directory (`/opt/TGW/var/worktrees/1580-agent-trace-phase1`) is owned by `db`, and
  running `git rev-parse` as `tgw` hits git's "dubious ownership" safe-directory guard
  and fails, which `_detect_git_branch()` correctly swallows (best-effort, never
  raises) rather than crashing the command. This is an artifact of running the
  acceptance test as `tgw` inside a `db`-owned worktree, not something Phase 1's real
  deployment path (workers/CLI run as `tgw` against `tgw`-owned checkouts) will hit.
  Noted rather than "fixed" since fixing it would mean bypassing git's ownership
  safety check, which is out of scope here.
- Everything else matches the packet's Spec section exactly (table columns, CHECK
  constraint values, function signatures, CLI flag names/defaults, `archive_transcript`
  atomic-write behavior, `schema.sql` drift-flagging comment on both the new table and
  retroactively on the pre-existing `ai_usage` block).

## Out-of-scope findings filed
None — no new adjacent issues found beyond the live bug fixed inline above (which is
within this packet's own new code, not a pre-existing adjacent issue, so it did not
need a separate todo).

## Observational note (Quota/risk section, disk-growth reference number)
Real Claude Code session JSONL sizes sampled from
`/home/db/.claude/projects/-opt-TGW-src-trader-grims-warehouse/`: **~2.4 MB average**,
~15 MB max observed, across 104 existing sessions (~0.2 GB total today). This is the
real number for future retention-policy conversations — no action taken on it here
per the packet's explicit no-TTL/permanent-retention instruction.
