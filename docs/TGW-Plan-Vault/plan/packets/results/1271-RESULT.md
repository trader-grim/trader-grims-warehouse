# Result: 1271 sold-order-history-gaps-in-health
Status: done
Todo: #1271   PP: PP-DATAINTEGRITY-001

Files touched:
- src/tgw/health.py — added `check_sold_order_gaps(cfg)` (reads
  `sold-order-history-gaps.jsonl`, same default path + `raw.sold_order_gap_log`
  cfg-override convention as `tgw.ebay.pull._record_sold_order_gap`), and
  registered it in `check_all()`'s checks list (between `check_offers_unresolved`
  and `check_quota`, matching the existing precedent for JSONL/JSON-registry
  "finding, not a log line" checks per invariant C11).

Live evidence:
- Pre-flight (invariant C11): confirmed live that
  `/opt/TGW/var/log/sold-order-history-gaps.jsonl` does NOT currently exist on
  tgw-prod (no gap has occurred yet — expected, not a bug) — writer code
  read directly from `src/tgw/ebay/pull.py` (`_record_sold_order_gap`,
  lines 639-666) to get the exact record shape:
  `{ts, requested_from, clamped_from, gap_days}` JSONL, appended, with
  `raw.sold_order_gap_log` as the cfg override key.
- Pattern source read directly: `src/tgw/quota.py` (`record_429` →
  `quota-incidents.jsonl` → `quota.status()` → `health.check_quota()`) and
  `health.py`'s `check_offers_unresolved()` (near-identical
  JSON-registry-count-and-surface shape) — new check follows the
  `check_offers_unresolved` shape most closely since both are simple
  file-backed count/detail surfacers, not the persistent quota daily-state
  file.
- Ran `python3 -m tgw.api health` (via `python3 -m tgw.api health`, not
  `tgw health`, since the shared `tgw` executable wrapper isn't on PATH in
  the worktree venv override) with
  `PYTHONPATH=/opt/TGW/var/worktrees/1271-sold-order-gap-health/src`
  and `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH`, confirmed
  `tgw.health.__file__` resolves under the worktree path (not the shared
  checkout), and confirmed `sold_order_gaps` appears in the live checks
  list: `sold_order_gaps True None` (healthy — matches the live-verified
  absent-file state).
- Simulated a populated gap log (2 synthetic records, 173 total gap-days)
  via `raw.sold_order_gap_log` cfg override and re-ran `check_sold_order_gaps`
  directly plus through `check_all()`: returned
  `ok=False, warn=True, sold_order_gap_count=2, sold_order_gap_days_total=173`
  with a human-readable detail string naming the most recent gap
  (`most recent 50 day(s) at 2026-07-10T00:00:00+00:00 ...`), and appeared
  in `check_all()['failed']` — confirming both directions (absent →
  healthy, present → surfaced/warn) work as designed, satisfying invariant
  C11 ("durable, queryable, never just a log line").
- Confirmed no ops_digest.py changes were needed: `ops_digest.py` (lines
  168-180) pulls ANY non-ok/warn check generically from
  `health.check_all()`'s `checks` list — the new `sold_order_gaps` check
  flows into `tgw ops-digest` automatically once wired into `check_all()`.
- Ran `tests/test_health_openrouter_key_limit.py` (the only existing test
  file touching `health.py`'s check functions) — 5 passed, no regression.

Deviations from spec: none. Packet spec was the todo body itself (no
separate packet file exists for #1271); implemented exactly as described —
wired into `health.py`/`check_all()` (the `tgw health` surface named in the
todo), following the `quota.record_429` pattern via the closer-matching
`check_offers_unresolved` precedent already in the same file. Chose not to
duplicate any code into `ops_digest.py` since that module already surfaces
all `check_all()` findings generically — adding a second explicit
ops_digest hook would have been a silent unstated addition, not called for
by the spec, and was verified live to be unnecessary.

Out-of-scope findings filed: none — no adjacent broken thing encountered
during this task.
