# Result: 1409 queue-daily-stats
Status: done
Todo: #1409   PP: PP-QUEUESTATS-001

Files touched:
- src/tgw/queue/schema.sql — added `queue_daily_stats` view (per-queue,
  per-hour, per-terminal-state job counts sourced from the append-only
  `queue_job_history` ledger joined to `queue_jobs` for `queue_name`) and
  a supporting `idx_queue_job_history_created_at` index.
- src/tgw/http_server.py — new `GET /api/queue/daily_stats` endpoint (date
  param, default today in America/Los_Angeles, matching `quota.py`'s
  existing eBay-reset day-boundary convention); rewired the `/form/pipeline`
  page's `renderQueues()`/`loadAll()` JS to fetch it and render three
  columns instead of the old collapsed pair: **Done today** / **Failed
  today** (both genuinely date-scoped, from `queue_daily_stats`) and **DL
  backlog** (the old lifetime dead-letter count, kept and relabeled
  honestly rather than removed — it's still operationally useful as a
  "how much needs review" backlog metric, just no longer conflated with
  a daily figure).
- tests/test_http_server.py — 5 new tests: auth requirement, bad-date
  rejection, date-scoped per-queue+per-hour counts, default-to-today (LA
  tz) behavior, and pipeline-page label wiring.
- docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1409-queue-daily-stats.md —
  breadcrumb (to be cleaned up by next session-start sweep).

Live evidence:
- Confirmed the todo's claim live BEFORE changing anything: `queue_status()`
  is `GROUP BY queue_name, state COUNT(*)` over all of `queue_jobs` with no
  date filter — e.g. `ebay_upload` lifetime `succeeded` = 84640 vs. actual
  today (UTC day) = 0.
- Applied `schema.sql` live via `sudo -u postgres psql -d state_machine -f
  src/tgw/queue/schema.sql` (idempotent, `CREATE VIEW`/`CREATE INDEX`
  succeeded, no errors).
- Ran the app's actual FastAPI `app` object from this worktree (uvicorn on
  127.0.0.1:7374, read-only queries against the real production Postgres —
  no writes), logged in via `/login`, and fetched `/form/pipeline`
  authenticated — response HTML contains the new `Done today`/`Failed
  today`/`DL backlog` headers and confirms `/api/queue/daily_stats` is the
  JS fetch target.
- Fetched `/api/queue/daily_stats` directly: e.g.
  `catalog_rebuild: {"succeeded": 39, "failed": 0, "dead_letter": 0}`,
  `ebay_legacy_sync: {"succeeded": 0, "failed": 0, "dead_letter": 17}`,
  with a full by-hour breakdown (not collapsed to one number).
- Cross-checked against a **fresh, independent** `psql` query straight
  against `queue_daily_stats` (not the endpoint) for the same LA-tz today —
  numbers matched exactly for every queue (catalog_rebuild 39/0,
  ebay_legacy_sync 0/17, ebay_publish 1/0, ebay_stage 1/0, ebay_sync 49/0,
  plan_render 13/0, token_refresh 48/0).
- `pytest -q tests/test_http_server.py`: 305 passed (300 pre-existing +
  5 new), offline, run with
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src` and
  confirmed `tgw.http_server.__file__` resolves under the worktree path,
  not the shared checkout.

Deviations from spec:
- The packet suggested "e.g. `queue_daily_stats` view" — built exactly
  that (a real Postgres view), sourced from `queue_job_history` rather
  than `queue_jobs.finished_at`, because `finished_at` is reset to `NULL`
  on retry (`requeue_with_backoff()`), so it cannot answer "how many
  succeeded/failed **today**" once a job has been retried — the
  append-only history ledger is the only reliable per-day source. Flagging
  this as a deviation from the literal suggestion, not from the intent.
- Added a `DL backlog` column (renamed from the old "Failed/DL") alongside
  the new date-scoped "Failed today" rather than dropping the lifetime
  dead-letter count outright — the PP's own text says the old "Failed/DL"
  label was "honest" (lifetime, not claiming to be daily) and operationally
  useful as a review-backlog signal; collapsing it into "Failed today"
  alone would have silently discarded that signal. Flagging as a judgment
  call, not silently made.
- Did **not** build anomaly/surge detection logic — explicitly out of
  scope per the PP text ("don't build the anomaly-detection logic itself
  until Dave asks"). The per-hour granularity in `queue_daily_stats` and
  the endpoint's `by_hour` field are the deliberate groundwork for that
  later work.
- Full-repo `pytest -q tests/` (all 178 files) was NOT clean: 2 failures
  in `tests/test_invariant_c12_field_set_accessors.py`, confirmed via
  `git stash` to be **pre-existing at HEAD (a432002)**, unrelated to this
  change (stale hardcoded line numbers in that test's `_ALLOWLIST`, drifted
  out of sync with `http_server.py`'s current line count from an earlier
  session). Filed as todo #1500 rather than fixed inline (out of this
  packet's scope). The directly-relevant `tests/test_http_server.py` file
  (300 pre-existing + 5 new = 305) passes cleanly.

Out-of-scope findings filed:
- #1498 (PP-FIELDCOMPLETE-001) — commit `a3714d4` added
  `from .ebay.category_aspect_migration import (...)` to `http_server.py`
  but never committed `src/tgw/ebay/category_aspect_migration.py` (still
  sits as an untracked file in the shared checkout only); `draft_specifics.py`
  also has an uncommitted `remove_ebay_aspects()` addition that module
  depends on. Any clean `git worktree add`/clone of `catio-nix-0.0.1-alpha`
  fails `ModuleNotFoundError` at import, and `pytest` cannot even collect
  `tests/test_http_server.py`. Worked around locally by copying both
  untracked files into this worktree **for testing only** (never
  committed to this branch, removed again before finalizing) — the fix
  itself (commit or revert the missing files in the shared checkout)
  needs to happen there, not on this branch.
- #1500 (PP-LISTEDITOR-001) — `tests/test_invariant_c12_field_set_accessors.py`'s
  `_ALLOWLIST` line numbers for `http_server.py` are stale relative to
  current HEAD; both its detector tests fail even with zero changes
  applied (confirmed via `git stash`). Needs a line-number refresh or a
  position-independent detector (e.g. marker comments instead of line
  numbers).
