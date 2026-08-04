# Result: 1369 announce-script-run-audit
Status: done
Todo: #1369   PP: PP-COHESION-001 (invariant E9)

## Scoping decision (verified live, per invariant C11)

Confirmed via `systemctl list-units 'tgw-worker@*'` that every module under
`src/tgw/workers/*.py` that subclasses `QueueWorker` (24 of 26 files) is
installed as a persistent `tgw-worker@<name>.service` systemd unit —
long-running daemons, not one-off invocations. Invariant E9's own docstring
in `tgw/logging.py` already scopes `announce_script_run()` to "anything run
by hand, not a systemd worker" — confirmed this reading is correct and did
NOT force-add the call to any QueueWorker subclass.

Two files that live under `src/tgw/workers/` are genuine exceptions —
standalone `main()` scripts with no `QueueWorker` class, confirmed **not**
present in `systemctl list-units 'tgw-worker@*'` output and (per the
master plan's own PP-COHESION-001 note on `itemdata_scrub.py`) not wired
to any cron/timer either:
- `src/tgw/workers/itemdata_scrub.py` — file-based batch queue sweep, `main()` only
- `src/tgw/workers/photo_history_recovery.py` — one-shot recovery script, `main()` only

Both got the E9 fix despite their directory location, because their
*execution model* (ad-hoc, hand-invoked) is what the invariant is actually
about, not the path.

`tools/cliptitleup.py` was deliberately excluded — it's a personal
clipboard title-case utility with zero ItemData/queue/eBay interaction
(reads/writes only the X11 clipboard via `pyperclip`), not a one-off
script touching production data in the sense E9 exists to cover.

## Files touched (announce_script_run() added)

`tools/`: `ebay_draft_audit.py`, `itemdata_scrub.py`, `photo_history_recovery.py`,
`repair_itemdata_json.py`, `scrub_description_history.py`, `todo_82_baseline.py`

`scripts/`: `alt_text_model_test.py`, `catpick_backfill_candidates.py`,
`data_scrub_legacy_ebay_fields.py`, `data_scrub_magento.py`, `ebay_audit.py`,
`ebay_backfill_offers.py`, `ebay_motors_census.py`, `ebay_normalize.py`,
`ebay_photo_push.py`, `ebay_snapshot_all.py`, `eval_repricer_gemini_grounding.py`,
`fleet_baseline_sweep.py`, `photo_repair_iss013.py`, `photosync_canary_probe.py`,
`recompile_category_backfill.py`, `vision_test.py`

`src/tgw/workers/`: `itemdata_scrub.py`, `photo_history_recovery.py`

Already compliant, untouched: `scripts/migrate_field_set_envelope.py`,
`scripts/requeue_deadletter_001_fixed.py`,
`scripts/requeue_ebay_draft_402_dead_letters.py` — these already called
`announce_script_run()` correctly (landed after #1308, before this packet).

Out-of-scope, untouched: `tools/cliptitleup.py` (see scoping decision above).

Also touched, unrelated pre-existing issue (see Deviations below):
`tests/test_invariant_c12_field_set_accessors.py`

## A second, deeper gap found and fixed: silent no-op without logging setup

Live-verified (not assumed) that `announce_script_run()` calls `log_event()`,
which emits via `logging.getLogger('tgw.events')`. Without a handler
configured on the `tgw`/root logger chain, Python's logging module silently
drops INFO-level records (default root level is WARNING, no handler at
all). Confirmed this live: even **#1308's own already-`done` fix**
(`src/tgw/workers/photo_history_recovery.py`) never called
`tgw.logging.setup_logging()` or `logging.basicConfig()` anywhere — its
`announce_script_run()` call, while present and passing its own
call-order test, produced **zero durable trace** when the script actually
ran, because the test mocks `announce_script_run` directly and never
exercises the real logging pipeline end-to-end.

Fix applied: for every touched script that had no prior logging
configuration (14 files), added
`from tgw.logging import announce_script_run, setup_logging` and a
`try: setup_logging('tgw.<script_name>') except OSError: pass` guard
immediately before the `announce_script_run()` call — the `except OSError`
matches the exact pre-existing pattern already used elsewhere in this
codebase (`ebay_normalize.py`/`ebay_backfill_offers.py`'s
`FileHandler(LOG_PATH)` try/except) for the same CI/no-writable-log-root
problem, confirmed necessary live (`pytest` running as a non-`tgw` user hit
`PermissionError` on `/opt/TGW/var/log/...` until this guard was added).
Scripts that already called `logging.basicConfig(level=logging.INFO, ...)`
before the announce call (`ebay_audit.py`, `ebay_backfill_offers.py`,
`ebay_normalize.py`, `ebay_photo_push.py`, `ebay_snapshot_all.py`, both
`itemdata_scrub.py` variants) did not need this — confirmed their existing
config already reaches `tgw.events` via logger propagation.

## Live evidence

Full offline suite, `PYTHONPATH` pinned to this worktree's `src/`
(confirmed `tgw.logging.__file__` resolves under the worktree, not the
shared checkout), `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH`:
```
2373 passed, 1 skipped, 1 warning in 30.00s
```

Real durable log evidence — ran two of the fixed scripts for real, as the
`tgw` user, against real production paths/config:

```
$ sudo -u tgw env LD_LIBRARY_PATH=... PYTHONPATH=<worktree>/src python3 -c "
import sys; sys.path.insert(0, '<worktree>/tools')
import todo_82_baseline as t
t.scan_baseline(limit=3)"
...
$ tail -1 /opt/TGW/var/log/tgw_todo_82_baseline.log
2026-07-17 19:43:08 INFO tgw.events [246219]: {"event": "script_run_start",
"ts": 1784342588.095658, "script": "todo_82_baseline.py", "purpose": "scan
a sample of item JSONs and report data-completeness baseline stats by
category group", "limit": 3}
```

```
$ sudo -u tgw env LD_LIBRARY_PATH=... PYTHONPATH=<worktree>/src \
    python3 -m tgw.workers.photo_history_recovery --config <bad-path> --itemdata /nonexistent
(fails on config load, as expected — but only AFTER announcing)
$ tail -1 /opt/TGW/var/log/tgw_photo_history_recovery.log
2026-07-17 19:43:25 INFO tgw.events [246459]: {"event": "script_run_start",
"ts": 1784342605.5286446, "script": "photo_history_recovery.py", "purpose":
"recover missing item photos from history archives into ItemData", "write":
false, "config": "...", "itemdata": "/nonexistent"}
```

This confirms both the fix (a real file lands in `/opt/TGW/var/log/`) and
the invariant's actual intent (the announce fires before the script's own
failure — even a crashed run leaves an attributable trace).

## Deviations from spec

- **The packet's premise "this could be a sizable number of files" held**
  (25 files touched) — no scope reduction taken; all found gaps were fixed,
  none deferred as a follow-up todo.
- **Scope was extended beyond a literal "add the call" fix** to also fix
  the silent-no-op logging gap described above. This wasn't in the
  packet's literal text but is necessary for the fix to actually satisfy
  the invariant's stated purpose ("a durable record... in the logs") —
  flagging per Prime Directive 3 rather than silently expanding scope.
  Without it, every added `announce_script_run()` call (including #1308's
  already-`done` one) would be dead code in real operation.
- **Unrelated pre-existing test breakage found and fixed to unblock full
  offline suite acceptance**: `tests/test_invariant_c12_field_set_accessors.py`
  had a stale line-number allowlist (mismatched against current
  `http_server.py` by a consistent +24-line offset — unrelated to this
  packet's changes, confirmed via `git diff`/`md5sum` that `http_server.py`
  itself is untouched and identical to HEAD). The shared checkout at
  `/opt/TGW/src/trader-grims-warehouse` already had this exact fix sitting
  uncommitted, per its own comment: "the same stale-line-numbers report
  independently rediscovered 4 times by different tgw-coder packets today
  (todo #1499/#1500/#1506/#1507), each correctly declining to fix an
  unrelated file out-of-scope." Applied the identical, already-verified
  fix here too (not invented by me) so this packet's own `pytest -q`
  acceptance requirement isn't blocked by an unrelated concurrent-packet
  collision. Not committed as a fix to that invariant — just carried
  forward so the worktree's tests pass; the stitcher/reviewer should be
  aware multiple branches are independently carrying the same one-line
  fix and only one needs to actually land on the shared branch.
- No cadence/TTL/limit was specified in the packet body (it's an audit +
  mechanical-fix task, not a scheduled-behavior task), so nothing to flag
  there.

## Out-of-scope findings filed

None new — the shared checkout's uncommitted note already documents
todos #1499/#1500/#1506/#1507 for the C12 allowlist staleness (found by
other concurrent packets, not by this one). No new out-of-scope findings
surfaced by this audit.
