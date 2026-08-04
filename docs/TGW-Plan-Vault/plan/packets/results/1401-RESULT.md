# Result: 1401 pm-intake-deadletter-triage
Status: done
Todo: #1401   PP: PP-DEADLETTER-001

Files touched: none (read-only investigation; no code changes made or needed)

Live evidence:
- Queried `queue_jobs` (state_machine DB) live via `sudo -u tgw psql`:
  `queue_name='pm_intake' AND state='dead_letter' AND error_detail LIKE '%PermissionError%'`
  returns exactly 3 rows, matching the todo's "3 pm_intake dead-letters,
  PermissionError" description:
  - job_id 38494c29-1300-4938-b3f0-37d4cbd13653, payload filename
    `INPROGRESS-session39-webui-taxonomy-conditions-actionconsole.md`,
    created_at 2026-07-01 22:27:47 UTC
  - job_id 199258fd-9add-4046-abf0-b0a43b979cda, payload filename
    `INPROGRESS-session40-actionconsole-build.sync-conflict-20260701-235003-CMHLXE2.md`,
    created_at 2026-07-02 10:31:39 UTC
  - job_id e20382fe-4d9a-4e74-82e5-76616be3ae82, payload filename
    `DONE-session40-actionconsole-design-pass.md`,
    created_at 2026-07-02 11:25:10 UTC
  - error_detail on all 3: `PermissionError(13, 'Permission denied')`
  - (14 other pm_intake dead-letters exist with different error classes —
    `HardFailure("section not found in plan: ...")` — out of scope for this
    todo, which named only the PermissionError trio.)
- Traced the code path: `pm_intake.handle()` (`src/tgw/workers/pm_intake.py`)
  calls `_archive(note_path, processed_dir, ...)` which does
  `shutil.move(str(note_path), archive_path)`, moving a file from
  `docs/TGW-Plan-Vault/inbox/queued/` to `docs/TGW-Plan-Vault/inbox/archive/`.
  Both dirs are `tgw:tgw` with group-write ACLs today, and the systemd unit
  template (`tgw-worker@<queue>.service`) runs as `User=tgw`/`Group=tgw` —
  so under the *current* permission scheme this would succeed.
- Root-caused the *historical* failure via git history, not guesswork:
  commit `a7e7439` ("fix: eBay quota drains + data-loss bugs found via live
  incident review (session 41)", 2026-07-02 11:47:41 -0700 = 18:47 UTC — a
  few hours after the 3rd dead-letter at 11:25 UTC same day) fixes exactly
  this bug in `atomic_write_json()` (`src/tgw/items.py` +
  `src/tgw/catalog.py`, the shared fence-side JSON writer used across the
  whole codebase): `tempfile.NamedTemporaryFile` creates its temp file at
  mode 0600 regardless of the parent directory's ACL/umask, which silently
  reverted files under the shared-write `docs/TGW-Plan-Vault` tree to
  owner-only on every atomic write — confirmed in that commit's own message
  as "confirmed live in session 41 on docs/TGW-Plan-Vault." Once a
  queued/*.md note got touched by any atomic-write path at owner-only mode,
  a subsequent `shutil.move()` by another actor/process hit
  `PermissionError(13)`. The fix (`_existing_mode_or_default()` +
  `os.chmod(tmp_path, want_mode)` before `os.replace()`) is present in the
  current worktree copy of both `items.py` and `catalog.py` (verified via
  `grep -n "os.chmod(tmp_path" src/tgw/items.py src/tgw/catalog.py`).
- Confirmed on disk that the 3 payload files are still sitting unarchived
  in `docs/TGW-Plan-Vault/inbox/queued/` today (2 of the 3 filenames found
  directly, mode `tgw:tgw rw-rwx---+` — i.e. currently fine under the
  post-fix policy; they were simply never retried/reprocessed since
  pm_intake was stopped before a retry happened).

Verdict: **real fence-adjacent bug, but already fixed** — this was not
pm_intake-specific cruft. `atomic_write_json()` is shared fence-side code
used by every active worker that writes JSON files under directories that
also see human/Syncthing/other-actor writes (docs/TGW-Plan-Vault
specifically, per the fix commit's own note). The bug could in principle
have hit any process doing a `shutil.move`/rename on a file most recently
touched by an unfixed `atomic_write_json()` call in that window. It was
independently found and fixed same-day (2026-07-02, commit `a7e7439`),
before this todo was even filed, and remains fixed in the current
codebase. No code change is needed from this packet.

Deviations from spec: none — packet said "read-only investigation... no
code changes expected unless a genuine fence-adjacent bug worth a tiny fix
is found." One was found, but it turned out to already be fixed upstream,
so per the packet's own "make the reasonable call" framing, the correct
action is to report and close, not to re-touch already-fixed shared code.

Out-of-scope findings filed: none. (Noted but not filed: the other 14
pm_intake dead-letters with `HardFailure("section not found in plan: ...")`
errors are pm_intake-specific — its own `_patch_plan_append()` section-name
matching against a moving master-plan-heading target — and are moot since
pm_intake is permanently stopped; not worth a todo. Also noted in passing:
`src/tgw/apis/ebay/_cache_io.py`, `_token_io.py`, `aider_mcp_server.py`,
and `api.py` also use `NamedTemporaryFile` but for owner-only-by-design
targets (secrets/tokens/cache) or one-off suggestion files, not the
shared-write `docs/TGW-Plan-Vault` pattern — reviewed, no residual gap
found worth filing.)
