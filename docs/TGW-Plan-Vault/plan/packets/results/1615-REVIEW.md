# Review: 1615 alt-text-history-staging

Status: cleared
Reviewer: Claude (same session as dispatcher — no separate /tgw-packet spec
file exists for this todo; original dispatch prompt in-session serves as
the de facto spec, checked against below)
Todo: #1615   PP: PP-DATALEARN-001

## Process note (operational friction, filed as todo)

No packet file exists at `docs/TGW-Plan-Vault/plan/packets/1615-*.md` — the
dispatcher (same reviewer, this session) sent the spec directly as an Agent
tool prompt instead of running `/tgw-packet` first. Not a Dave-directed
deviation; a review-skill process gap. Filed as todo #1617 (see below) so
this doesn't silently recur. Review proceeded anyway since the dispatcher's
original prompt (verbatim, same conversation) fully covers Spec/Out-of-
scope/Acceptance — treated as equivalent to a packet's Spec section for
this one case.

## Checked

- **Spec**: both `cmd_alt_text()` and `_apply_alt_text_result()` now write
  archive copies to `_history_staging_sku_dir()` →
  `/opt/TGW/data/history-staging/<sku>/`, never resolving or touching the
  `history` symlink. `_history_root_reachable()` and the
  `archive_target_unmounted` C11-finding branch removed entirely, matching
  spec item 3/4. Unused imports (`os`, `tgw_logging`, `fence_patch_item`)
  removed, matching spec item 4. Docstring/comments updated to describe the
  new always-local behavior, matching spec item 6. Confirmed via full diff
  read (`git diff catio-nix-0.0.1-alpha todo/1615-history-staging`).
- **Out of scope**: `history` symlink, `/media/tgw/MasterArchive`, the
  future sweep/merge job, `-alt.jpg` derivative logic, `store_hash`/
  `lookup_hash` cache, `ai_identify.py` — none touched. Diff stat confirms
  only `src/tgw/alt_text.py` + `tests/test_alt_text.py` (+ this task's own
  inbox note and result manifest) changed.
- **Live evidence**: real, not simulated — `cmd_alt_text()` run against
  real config paths as the `tgw` user (only the vision-model network call
  mocked), throwaway SKU deleted after. Confirmed file landed at
  `/opt/TGW/data/history-staging/<sku>/<sku>.jpg` and that the `history`
  symlink was never touched. `PYTHONPATH` override to the worktree
  confirmed in the manifest (step 1 sanity check passes) — test results
  trusted.
- **Invariants**: C11 ("skip/guard is a finding, not a log line") no longer
  applies to this code path — correctly so, since the fix eliminates the
  failure mode itself (staging is always local, nothing left to skip/guard
  against) rather than silently dropping a finding that used to exist. No
  invariant violated.
- **Tests**: `tests/test_alt_text.py` diff reviewed — updated paths and two
  rewritten symlink tests correctly assert the new behavior (symlink
  untouched, staging path used); one obsolete helper test removed since its
  function no longer exists. In scope per the test-file carve-out (same
  module already in the packet's declared scope). 49/49 passed; full suite
  2741 passed/1 skipped/2 pre-existing unrelated failures (confirmed
  reproducing identically on base branch, different file).

## Trigger check

None fired. No spec deviation, no invariant violation, no out-of-scope file
touched (test file carve-out applies), no live/production write attempted
before stitch (throwaway SKU only, cleaned up), pp_ref matches
(PP-DATALEARN-001), manifest sanity check passed.

## Summary

Clean, well-scoped, live-verified fix. Cleared for stitch.
