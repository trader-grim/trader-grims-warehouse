# Result: 1582 agent-trace-phase3-runs-ui
Status: done
Todo: #1582   PP: PP-AGENTTRACE-001
Files touched:
- src/tgw/http_server.py (new `_render_runs_html()`, `_runs_status_class()`, `_RUNS_EXTRA_CSS`/`_RUNS_JS` constants, `GET /form/runs` route)
- src/tgw/static/nav.js (added "Agent Runs" link to Admin dropdown, next to "Todos")
- tests/test_http_server.py (11 new tests: render/empty/db-error/auth-gate/escaping/color-coding/filter-controls/transcript-as-text/static-css + 2 direct `_render_runs_html()` unit tests)
- tests/test_invariant_c12_field_set_accessors.py (refreshed one pre-existing allowlist line number that shifted from 5855→6041 due to this packet's insertion — pure position shift, re-verified same `revision_draft.delta` access, no accessor-routing behavior changed)

Live evidence:
- Rendered `_render_runs_html()` against the **real** `agent_runs` table (via
  `sudo -u tgw ... python -c "from tgw.queue import state_machine; from
  tgw.http_server import _render_runs_html; ..."`, worktree's own copy
  confirmed via `tgw.http_server.__file__`): 4 real rows from Phase 1/2's
  own test-packet runs (`test-packet-1580` completed, `test-agent-phase2`
  completed, `test-agent-phase2b` failed, `test-agent-phase2c` running),
  all tied to `todo_id`/`pp_ref=PP-AGENTTRACE-001`. Output confirmed:
  `rows: 4`, `contains PP-AGENTTRACE-001: True`, `st-completed`/`st-failed`/
  `st-running` all present (color-coding working), `<div class="runs-total">4
  run(s)</div>`.
- `pytest -q` full offline suite: **2719 passed, 1 skipped** (worktree's own
  code confirmed via `tgw.http_server.__file__` resolving under the
  worktree path, `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH` set for
  `psycopg2`/`libz.so.1`).
- Session-guard: `test_runs_form_requires_session` confirms an
  unauthenticated `GET /form/runs` returns 303 → `/login?next=/form/runs`,
  same as `/form/todos` — no per-route auth added, confirmed `_session_guard`
  middleware alone gates it.
- DB-down path: `test_runs_form_db_error_still_200` confirms a raising
  `list_agent_runs()` still returns 200 with an error banner, not a 500.
- `tgw health`: unchanged — `backups` and `ebay_sync_fallback` were already
  failing before this change (pre-existing, unrelated infra state, not
  touched by this packet).

Deviations from spec: none.

Known limitation (flagged per packet spec item 4, not a deviation): the
`Transcript` column renders `transcript_path` as escaped plain text, not a
clickable link — grepped `http_server.py` for an existing file-serving
route (`FileResponse`/`StaticFiles`/`app.mount`) that could safely serve
`/opt/TGW/var/agent-traces/...`; the only matches (`/media/{sku}/{filename}`,
`/thumb/{sku}`) are scoped to `ItemData`/thumbnail roots specifically and
aren't a general-purpose fit. Per the packet's explicit out-of-scope list,
did not build a new file-serving route — this is the intended interim state,
not an oversight.

Out-of-scope findings filed: none — no new adjacent issues found during
this packet; the one incidental fix (C12 allowlist line-number refresh) is
the same known/accepted "detector shifts on unrelated edits" pattern
already documented inline in that test file from prior packets (todo
#1499/#1500/#1506/#1507/#1562), not a new finding worth a separate todo.
