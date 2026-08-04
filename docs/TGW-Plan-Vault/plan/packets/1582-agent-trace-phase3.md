# Packet: Agent trace logging — Phase 3 (`/form/runs` HTTP UI)
Todo: #1582   PP: PP-AGENTTRACE-001   Track: new capability

## Context budget (ALL the model may load)
This packet + `src/tgw/http_server.py`'s `/form/todos` route (`_render_todos_html()`
+ `todos_form()`, ~line 4078-4160 — the exact pattern to replicate: query→render→
200-even-on-DB-error) + the `_STATIC_HEAD`/`_STATIC_FOOT` constants (~line 3180) +
the global `_session_guard` middleware (~line 311-323, confirms `/form/*` auth model
— session cookie via middleware, NOT a per-route `dependencies=[AUTH]`) +
`src/tgw/queue/state_machine.py`'s `list_agent_runs()`/`get_agent_run()` (from Phase
2, already merged — read signatures only). Nothing else — do not load the master
plan or unrelated routes.

## Verified live before this packet was written
- Phase 1 (`agent_runs` table + CLI) and Phase 2 (`list_agent_runs()`, Obsidian
  render) are both merged and live in `catio-nix-0.0.1-alpha` by the time this
  packet runs — build against the real `list_agent_runs()` function, no stubbing.
- `/form/*` pages are gated by a global middleware session-cookie check, not
  per-route Bearer auth — confirm `_session_guard`'s behavior is unchanged before
  copying the pattern; do NOT add `dependencies=[AUTH]` to this new route, that
  would be inconsistent with every sibling `/form/*` page and likely double-gates
  or breaks the network-trust model this app already uses.
- `/form/todos`'s error-handling shape (`try/except` around the query, still
  return `HTMLResponse(body, status_code=200)` with an error banner rather than
  letting a DB-down state 500) is the exact shape to copy.

## Spec
1. New route `GET /form/runs` in `src/tgw/http_server.py`, placed near
   `/form/todos` for locality. Query via `state_machine.list_agent_runs()`
   (Phase 2). Same try/except-still-200 error handling as `todos_form()`.
2. New renderer function `_render_runs_html(rows) -> str`, matching
   `_render_todos_html()`'s structure: `_STATIC_HEAD`/`_STATIC_FOOT` shared dark
   theme, an `<h2>Agent Runs</h2>` heading, a summary count line, then a
   `<table>` with columns: `Run ID` (truncated display, full id in a `title=`
   attribute), `Agent Type`, `PP/Todo` (linked to the master plan doc the same
   way `_extract_pp_refs()`/`pp-badge` links work in `_render_todos_html`, reuse
   that helper if it fits, don't rebuild it), `Host`, `Status` (color-coded:
   green-ish for completed, red-ish for failed/killed, amber for
   running/escalated — match the existing color vocabulary already used
   elsewhere in this file, e.g. `#c44` for error states, don't invent new hex
   values ad hoc), `Started`, `Duration`, `Summary`. Every cell value goes
   through `html.escape()` — no exceptions, this is user/agent-controlled text
   (summary, agent_type) rendering into HTML.
3. Client-side filtering (no new server-side query params needed — Phase 2's
   `list_agent_runs()` has no filter args and this packet doesn't need to add
   any): a JS filter bar with `<select>`/`<input>` for `agent_type` and
   `status`, filtering the already-rendered rows client-side, same technique
   `/form/todos` already uses for its agent dropdown (`_TODOS_JS`'s filtering
   logic — read it, reuse the pattern, don't invent a different one). Add a
   free-text search box filtering visible rows by `pp_ref`/`todo_id`/`summary`
   substring match, client-side only.
4. Each row's `Run ID` cell (or a small icon/link next to it) links to the raw
   transcript file path if `transcript_path` is set on that row — since
   transcript files live on the local filesystem (`/opt/TGW/var/agent-traces/`),
   this can't be a normal `<a href>` to an arbitrary local path from a browser;
   render the path as plain escaped text (not a clickable link) unless there's
   an existing file-serving route in this codebase already suited to it — check
   first (grep for an existing static-file-serving pattern), and if none exists,
   don't build a new one in this packet, just show the path as text. Flag this
   explicitly as a known limitation in the result manifest rather than silently
   deciding it doesn't matter.
5. Add a nav link to `/form/runs` wherever `/form/todos` is linked from
   elsewhere in the app's nav (`_STATIC_HEAD`'s nav bar, or `nav.js` — check
   which mechanism actually adds nav entries and follow it, don't hand-edit HTML
   nav strings if there's a declarative list somewhere).

## Dataset
None new — pure read-only view over Phase 1/2's existing `agent_runs` table.

## Out of scope
- Any server-side filter/pagination query params — client-side only, per spec
  item 3.
- Building a new file-serving route for transcript files if one doesn't already
  exist — note as a limitation, don't build it here (see spec item 4).
- Auto-refresh/polling/live-update of the page — a static render-on-load page is
  sufficient for this packet; a future enhancement, not required now.
- Any change to `/form/todos` itself beyond adding the new nav link.

## Acceptance (live)
1. Start the `tgw-http` server (or use `fastapi.testclient.TestClient` against
   `http_server.app`, matching Phase 1's own acceptance-testing approach) and
   `GET /form/runs` with a valid session — show the actual rendered HTML
   contains real rows for runs created via `tgw trace start`/`end` (Phase 1),
   correctly escaped, correctly color-coded by status.
2. Confirm the DB-down error path: temporarily break the query path (e.g. mock
   `list_agent_runs` to raise) and confirm the route still returns 200 with an
   error banner, not a 500.
3. Confirm `/form/runs` requires a valid session cookie the same way
   `/form/todos` does (unauthenticated request redirects to `/login`) — this is
   the middleware's existing behavior, just confirm this new route isn't
   accidentally exempted from it.
4. Show the client-side filter actually working in a real browser session (or
   via a JS-execution-capable test if this codebase has one) — filtering by
   agent_type/status/search text visibly changes which rows are shown.
5. Full offline suite (`pytest -q`) passes, including new tests for
   `_render_runs_html()` (pure-enough to test directly with synthetic row data,
   asserting escaping/color-coding/column presence).
6. `tgw health` clean/unchanged.

## Quota/risk
None — no LLM/API cost, pure read-only HTTP route work.
