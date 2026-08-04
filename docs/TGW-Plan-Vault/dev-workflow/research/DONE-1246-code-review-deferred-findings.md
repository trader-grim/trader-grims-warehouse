# DONE — todo #1246 (audit#1143, 4 deferred findings from #1245)

## Finding 1 & 4 — multi_intake.py collision notify() spam + unactionable text
`_extract_items()`'s SKU-collision `notify()` fired unconditionally on every
detection, with no dedup. Since `_child_skus()` derives child SKUs
deterministically from `base_sku`, a batch re-drop of the identical zip
reproduces the exact same collision on the exact same SKU every time — the
external notify channel would be spammed once per re-drop instead of once
ever. Separately, the message just said "verify it is not a mistaken
duplicate" with no actionable next step.

Fixed: added a persistent per-SKU registry
(`/opt/TGW/var/multi-intake-collision-notified.json`, same atomic
tmp+rename pattern as `ebay_sku_migrate.py`'s `_BLOCKED_REGISTRY`) —
`notify()` only fires the first time a given SKU collides; the durable
per-item finding (log line + `log_event`) still fires on every hit, since
that's queryable evidence, not a spammy external channel. The message now
tells the operator to run an "operator-forced ebay_stage duplicate-check
pass" on the SKU if it turns out to be a genuine duplicate.

## Finding 2 — state_machine.mark_failed() phantom transitions
Both lease-guarded UPDATEs (`running`→`retry_wait`, `running`→`failed`)
never checked `cur.rowcount`. If the WHERE clause (`state='running' AND
lease_owner=%s`) matched zero rows — e.g. `recover_expired_jobs()`
reclaiming the lease between `mark_failed`'s own SELECT and its UPDATE —
the function still unconditionally returned the transition it *attempted*,
not what actually happened, misleading the caller's terminal-failure
handling (alerting / restarting a self-rescheduling chain).

Fixed: both branches now check `cur.rowcount` immediately after their
lease-guarded UPDATE. On a lost race: the `failed`→`dead_letter` promotion
(unconditioned on `lease_owner`) is skipped entirely — it could otherwise
wrongly fast-forward some *other* owner's legitimately-`failed` row — and
the function re-queries the row's actual current state, returning
`'dead_letter'` only if that real state is `failed`/`dead_letter`,
`'retry_wait'` otherwise (or if the row is now gone entirely).

## Finding 3 — ebay_sku_migrate.py duplicated interval_h computation
`handle()` and `_on_terminal_failure()` each independently computed
`float(migrate_cfg.get('interval_hours', 1))` — a future change to the
config key or default could be applied in one place and missed in the
other. Factored into a single `_interval_hours()` method both call sites
now share.

## Tests
- New `tests/test_mark_failed_lease_race.py` (5 tests): normal retry_wait/
  dead_letter transitions with no race; retry_wait lease race reports the
  real (non-terminal) state instead of a phantom transition; dead_letter
  lease race skips the failed→dead_letter promotion and reports the real
  state; row-now-gone-after-a-race still reports `dead_letter`.
- New `tests/test_ebay_sku_migrate_interval_hours.py` (3 tests): the
  helper reads configured/default values correctly; `handle()` and
  `_on_terminal_failure()` demonstrably use the same value.
- `tests/test_multi_intake.py`: fixed an isolation gap in the existing
  collision test (it never monkeypatched the registry path, so it would
  have read/written the real `/opt/TGW/var` file) and added 2 tests: the
  notify text is now actionable (`ebay_stage`/`duplicate-check` present);
  a batch re-drop only notifies once, not once per re-drop.

`pytest -q` on the 5 affected test files: 15/15 pass. Full suite: 2017
passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only)
- Grepped `/opt/TGW/var/log/*.log*` for `multi_intake_sku_collision`: zero
  historical occurrences — this collision (and its notify-spam bug) has
  never fired in production, so this closes a dormant hole rather than
  repairing already-spammed history.
- Confirmed the real registry file doesn't exist yet (consistent with the
  above).
- Confirmed the real `tgw-api-config.json`'s `ebay_sku_migrate.interval_hours`
  (`0.05`) is read identically by the new shared `_interval_hours()` helper
  as it was by the two previously-duplicated call sites.
- `mark_failed()`'s fix was verified via the 5 mocked unit tests (a
  synthetic lease-race scenario against a real Postgres instance isn't
  practically constructible without deliberately racing two live
  connections, which isn't warranted for this fix).

No deviations from the todo brief. No config/secrets/OAuth scopes touched.
