status: cleared
reviewer: Claude (main session, catio-nix-0.0.1-alpha)
todo: #1418   pp_ref: PP-LISTEDITOR-001
branch: todo/1418-field-set-schema-foundation @ 8eee0d0

## Pre-work snapshot verification (Dave's explicit instruction)
`bin/tgw-snapshot` run before dispatch (`/opt/TGW/.snapshots/20260715T0734`,
received copy at `/home/snapshot/TGW-SNAPSHOT-0/20260715T0734`). Post-run
verification: live `/opt/TGW/data/ItemData` unchanged vs snapshot — same
item count (55,419 both), same total size (180G both), zero diffs on a
10-item random sample plus the specific item checked earlier in-session
(`tgw202605040949058`). The coder honored the dry-run-only constraint;
nothing live was mutated.

## Checked
- Diff scope: 13 modified + 8 new files, matches the packet's declared
  file list plus the session's pre-built read/write-site inventory
  (`ai_identify.py`, `ebay_draft.py`, `http_server.py`, `ebay/sync.py`,
  `ebay/pull.py`, `draft_sync.py`, `listing_quality.py`,
  `apis/lookup/base.py`) plus one legitimately-found extra site
  (`scripts/photosync_canary_probe.py`). Spot-checked two sites the
  original audit flagged that are NOT in the diff (`seo/title.py`,
  `revision.py`) — both confirmed legitimate non-issues: `seo/title.py`
  takes `item_specifics` as a plain function parameter, not an item-dict
  field access; `revision.py` operates on eBay API body field names
  directly, bypassing `draft_listing` by design (per the original audit).
  No missed sites, no scope creep.
- Envelope shape, accessor modules, banner comments: all match spec items
  1, 4, 5 — the banner comment in `inventory_record.py` in particular is
  exactly the "immediately recognizable to anyone updating around it"
  bar Dave set this session.
- Migration script (spec item 2): dry-run-by-default, invariant E5
  archive-before-overwrite, invariant E9 `announce_script_run`, idempotent
  (skips already-enveloped items), honest `updated_at_backfilled` handling
  matching Prime Directive 1. Independently re-ran it live against the
  real 55,419-item catalog (`--limit 100`, no `--apply`): reproduced
  `round_trip_failures=0` exactly as the manifest claims.
- Provenance history arrays (spec item 3): present, append-only, sourced
  from the accessor modules only.
- New invariant C12 (spec item 6) and CLAUDE.md settled-architecture
  bullet (spec item 8): read in full — format/tone matches the C11
  precedent, cites Dave's direct quote and the #1291/#1313/#1316 evidence
  correctly, cross-references #1416's complementary data-drift detector
  as "not yet built" rather than silently omitting it.
- Schema doc (spec item 7): `item_attributes` documented for the first
  time, cites the `price_history`/`vision_results` precedent as
  instructed.
- Full offline suite (spec item 9): manifest claims `2278 passed, 1
  skipped` — independently re-ran in the worktree (confirmed testing the
  worktree's own copy via `__file__`): identical result, 151.79s. Zero
  regressions confirmed independently, not just trusted from the manifest.
- C12 detector: manifest's live bypass-probe evidence is concrete (a real
  scratch file, a real test failure, then a clean re-run after removal) —
  accepted as-is per the "not 'tests pass' alone" bar, this is a genuine
  adversarial self-test.
- Deviations (6, all disclosed): all are either explicitly within the
  packet's own "your call, flag it" allowances (module locations, detector
  choice) or judged necessary for structural correctness and clearly
  reasoned (the `_apply_patch` envelope-safety fix in particular is a good
  catch — prevents the still-unfixed `saveEbayDraft()` bare-PATCH path
  from corrupting the envelope shape before #1416 lands). None read as an
  unresolved spec violation.
- No invariants.md violation. No file touched outside declared scope
  (test files for already-in-scope modules are the only additions beyond
  the named list, per the standing test-file carve-out). No live/
  production write before stitch — confirmed via the snapshot diff above,
  not just taken on trust.

## One item worth Dave's attention before any future full-catalog run
Deviation 6: the migration script has no hard-coded ceiling — `--apply`
with no `--limit` would run against the entire catalog today if invoked.
The packet's constraint (full-catalog run is a separate go/no-go) is
enforced by operator discipline only, matching the existing
`recompile_category_backfill.py` precedent, not by a guard in the script.
Not a defect in this packet — flagging per the manifest's own explicit
callout, for Dave's awareness before that go/no-go decision arrives.

## Summary
Clean. Independently re-verified (not just trusted) on: live-data
untouched vs. snapshot, migration dry-run reproducibility, full offline
suite, and the two audit sites absent from the diff. Cleared for stitch.
