# Result: 1250 harden-oneoff-scripts
Status: done
Todo: #1250   PP: PP-COHESION-001

Files touched:
- scripts/pilot_1481_clip_embed.py (added announce_script_run() call — the
  one real gap found across scripts/*.py)
- scripts/requeue_ebay_draft_402_dead_letters.py (extracted `_make_dedupe_key()`
  helper; added a per-SKU attempt-cap guard layer on top of the existing
  job_id marker; new `--max-attempts-per-sku` flag, default 3)
- scripts/check_announce_script_run.py (new — invariant E9 detector)
- tests/test_requeue_ebay_draft_402_dead_letters.py (new tests: stable
  dedupe key, per-SKU attempt cap, backward-compat marker load)
- tests/test_check_announce_script_run.py (new — detector unit tests incl.
  strip/restore live-style proof)
- docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1250-harden-oneoff-scripts.md
  (breadcrumb, written to worktree path)

Live evidence:
- Detector run against a deliberately-stripped copy of
  requeue_ebay_draft_402_dead_letters.py (announce_script_run() call
  regex-stripped) correctly flags it and exits 1:
  ```
  invariant E9 violation: script(s) with main() but no announce_script_run() call:
    .../scratchpad/detector-demo/requeue_ebay_draft_402_dead_letters.py
  exit=1
  ```
- Same detector run against the real (unmodified) scripts/ directory exits
  0 clean:
  ```
  OK: every scripts/*.py with a main() in .../scripts calls announce_script_run().
  exit=0
  ```
- `_make_dedupe_key('tgw123','job-abc')` returns the identical string across
  5 repeated calls and even when `time.time()` is monkeypatched to jump
  ~11.5 days forward — proves it is a pure function of (sku, job_id), never
  time-dependent (test_dedupe_key_is_deterministic + new
  TestDedupeKeyIsStableNotTimeDependent class).
- `TestAttemptCapPerSku.test_same_sku_stops_being_requeued_after_n_attempts_across_runs`:
  simulates the exact failure mode named in the todo (same SKU, three
  separate dead-letter job_ids across three separate script invocations —
  a job that keeps dying and getting requeued under a fresh job_id every
  time). With `--max-attempts-per-sku 2`, runs 1–2 requeue (1 call each),
  run 3 is blocked by the cap (0 calls) and the marker records
  `sku_attempt_counts[sku] == 2`.
- Full pytest suite, run with the mandatory worktree PYTHONPATH/LD_LIBRARY_PATH
  override (confirmed `tgw.logging.__file__` resolves under
  `/opt/TGW/var/worktrees/1250-harden-oneoff-scripts/src`, not the shared
  checkout): `2476 passed, 1 skipped in 203.14s`, zero regressions.

Deviations from spec:
- The todo's sub-tasks (1) announce retrofit and (3) dedupe-key fix for
  `requeue_ebay_draft_402_dead_letters.py` were **already done and already
  live-applied** in prior commits `65f536d` (#1206, 2026-07-10) and
  `fbbb786` (#1265/#1250, 2026-07-14) — the marker file
  (`/opt/TGW/var/run/requeue_ebay_draft_402_dead_letters.done.json`, 116KB,
  dated Jul 14) confirms the script already ran with `--apply` and
  completed. The todo's "left unrun" framing predates that prior session's
  work. I did not re-run it (nothing pending to run), and added a further
  hardening layer on top (per-SKU attempt cap) since the existing
  job_id-only marker only protects against re-requeuing the *same*
  dead-letter row twice — it does not stop a SKU that keeps re-dead-lettering
  under a *new* job_id on every retry, which is a real residual instance of
  the "loop forever" risk the todo names. This is a deliberate scope
  extension flagged here, not a silent substitution — reviewer's call on
  whether the added cap layer is wanted or should be reverted/simplified.
- Only `requeue_ebay_draft_402_dead_letters.py` (the script explicitly
  named in the todo) got the dedupe-key/attempt-cap hardening.
  `scripts/requeue_deadletter.py` and `scripts/requeue_deadletter_001_fixed.py`
  already use job_id-derived deterministic dedupe keys (checked, not
  timestamp-based) so were left untouched — out of the todo's named scope.
- `_MAIN_RE` detector regex matches only a top-level `def main(` (module
  scope, standard convention across all 22 scripts/*.py files) — a script
  with only a nested/class-method `main` would not be flagged. Judgment
  call, flagged: matches the actual convention observed across every file
  in scripts/, not a hypothetical.

Out-of-scope findings filed: none — the one real gap (pilot_1481_clip_embed.py
missing the announce call) was fixed inline as explicitly named in sub-task
1 ("retrofit onto all scripts/ one-off tooling"), not filed as a separate
todo, since it's exactly this packet's own scope.
