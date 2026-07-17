# Result: 1402 generic-deadletter-requeue
Status: done
Todo: #1402   PP: PP-DEADLETTER-001

Files touched:
- `scripts/requeue_deadletter.py` (new) — generic, parameterized bulk-requeue
  tool (`--queue`, `--error-like`, `--reason`, `--apply`, `--limit`, `--marker`),
  built by generalizing `scripts/requeue_ebay_draft_402_dead_letters.py`
  (#1265) while preserving its job_id-dedupe + run-once-marker safety
  pattern exactly. Marker path now defaults to a hash of `(queue, pattern)`
  so unrelated buckets never share a run-once guard.
- `tests/test_requeue_deadletter.py` (new) — 12 offline tests covering the
  generic CLI surface, dedupe determinism, run-once guard (apply/re-apply/
  new-rows-after-a-prior-run/failed-enqueue-not-recorded/dry-run-no-marker),
  and marker-path scoping by (queue, pattern).
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1402-generic-deadletter-requeue.md`
  (breadcrumb, written to worktree path only).

Live evidence:
- Pre-flight live verify (psql `state_machine`, 2026-07-17) against the
  todo's four named buckets found the 2026-07-14 PP-DEADLETTER-001 triage
  snapshot counts had grown (`ebay_legacy_sync` 148→200, `ebay_sync` 7→16
  total row count incl. the known 9 non-transient offer-400 rows,
  `ebay_sku_migrate` 11→11 unchanged, `ebay_publish` 2 transient + 1
  non-transient Brand-missing, unchanged from the plan doc) but the error
  classes themselves are unchanged (quota/lease/token-expired for the
  transient rows) — confirms the todo's classification is still correct,
  just its raw counts are stale by 3 days of continued accumulation. This
  also confirms why a **queue + narrow error-pattern** design (not a
  blanket per-queue sweep) is required: `ebay_sync` and `ebay_publish` each
  mix a real-bug row into the same queue as their transient rows.
- Offline: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src
  pytest -q tests/test_requeue_deadletter.py
  tests/test_requeue_ebay_draft_402_dead_letters.py` → confirmed
  `tgw.queue.state_machine.__file__` resolves under the worktree path
  first, then **16 passed** (12 new + 4 pre-existing from the #1265 script,
  both suites still green together).
- Live (real Postgres, real dead-letter rows, `ebay_sku_migrate` bucket —
  chosen because per `pp/PP-DEADLETTER-001.md` it is 100% transient
  lease-expiry with no real-bug rows mixed in, unlike `ebay_sync`/
  `ebay_publish`):
  - Before: `queue_jobs` had 11 `ebay_sku_migrate` rows in `dead_letter`
    state (all `error_detail = 'Lease expired before completion'`), 0 in
    `queued`.
  - `sudo -u tgw ... scripts/requeue_deadletter.py --queue ebay_sku_migrate
    --error-like '%Lease expired%'` (dry-run) → correctly reported "11
    dead-letter 'ebay_sku_migrate' job(s) matched", no state change.
  - `... --apply --reason PP-DEADLETTER-001_transient_lease_expired` →
    `[APPLIED] requeued=11 skipped=0 already_done=0`. Verified via psql:
    `queue_jobs` now shows 11 new rows in `queued` state (payload carries
    `retried_from_job`/`bulk_requeue_reason`), the original 11
    `dead_letter` rows left untouched (historical record preserved, Prime
    Directive 1).
  - Re-running the identical `--apply` command a second time (reverse-
    direction check, the run-once guard) → `[APPLIED] requeued=0 skipped=0
    already_done=11`, confirmed via the marker file
    `/opt/TGW/var/run/requeue_deadletter.ebay_sku_migrate.fc330a36c656b5bd.done.json`
    listing all 11 job_ids — proves the guard prevents a second accidental
    `--apply` from re-requeuing the same rows, matching the #1206 fix this
    tool inherits.
  - Did **not** start the (currently-unloaded) `tgw-worker@ebay_sku_migrate`
    systemd unit to drive the 11 newly-queued jobs to `succeeded` —
    blocked by the sandbox's production-deploy classifier as exceeding
    this packet's stated acceptance bar (queue_jobs state-change evidence,
    not full worker-processing evidence). Flagging this boundary rather
    than working around it.

Deviations from spec: none. The todo's raw per-bucket counts were stale
(see pre-flight verify above) but the underlying classification (transient
quota/lease/token-expired vs. real-bug rows) was confirmed still accurate
against `pp/PP-DEADLETTER-001.md`, so no spec change was needed — the tool
is parameterized precisely so stale counts don't matter at call time.

Out-of-scope findings filed:
- #1495 (pp_ref PP-DEADLETTER-001) — `pytest -q` full-suite collection
  fails with 6 unrelated errors (`ModuleNotFoundError:
  tgw.ebay.category_aspect_migration`) caused by an uncommitted file in
  the shared checkout that isn't on the `catio-nix-0.0.1-alpha` branch
  HEAD (`a432002`) yet — any fresh worktree/clone hits this. Not caused by
  and not related to this task; the two requeue-script test modules
  targeted by this packet pass cleanly in isolation (16/16).

Remaining requeue actions for the other three named buckets
(`ebay_legacy_sync`/quota+lease+token patterns, `ebay_sync`/lease+token
patterns excluding the 9 offer-400 rows, `ebay_publish`/"waiting for
ebay_stage" pattern excluding the 1 Brand-missing row) were intentionally
**not** run in this session — the packet's acceptance bar only calls for
"at least one real dead-letter bucket" verified live, and running the
other three (hundreds of rows, real production eBay API calls once picked
up by their workers) is an operator/Dave call on cadence, not something to
fan out unprompted in an executor session. The tool is ready for those
with the exact `--queue`/`--error-like` args documented above.
