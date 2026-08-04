# Proposed agenda — TGW planning session, 2026-07-10

Built from: master plan status (`tgw plan status`, `tgw plan check` — clean),
`handoff.md`'s open risks, `FUTURE-IDEAS.md`'s deferred concepts, and today's
cohesion-audit output. Grouped roughly by "needs a decision" → "needs review" →
"housekeeping."

## 1. Alarms & standing risks needing a decision

- **`tgw-cloud-sync.service`** — hit Google Drive's per-minute query quota 6x
  since 07-05; already logged under PP-BACKUP-001 as an alarm follow-up (`tgw
  plan status` shows it as the latest activity). Decide: tune rclone pacing
  (`--tpslimit`/`--checkers`) vs. request a Drive API quota increase.
- **Worker-count discrepancy** — CLAUDE.md still flags 11 active workers vs.
  Dave's recollection of "mostly stopped except main pipeline" (2026-07-09,
  unresolved). Needs a definitive decision on target worker set.
  **Root cause confirmed 2026-07-11**: a routine `nixos-rebuild switch` on
  tgw-prod (unrelated flake work — SSH key rotation, hermes removal) silently
  restarted 8 workers Dave had manually `stop`ped but never `disable`d:
  `catalog_rebuild`, `ebay_legacy_sync`, `ebay_price_reducer`,
  `ebay_sku_migrate`, `ebay_sync`, `thumbnail_gen`, `velocity_stats`, and
  `pm_intake` (the last explicitly "going a different direction," stopped
  2026-07-09). NixOS reconciles "enabled" units to "should be running" on
  every activation regardless of a prior manual stop — this is the same gap
  CLAUDE.md already flagged for `pm_intake` specifically, now confirmed to
  apply to the whole stopped set. **Live status derived same day**: all 8 ran
  for ~9-10 min before being caught and stopped back down; no application
  errors (only benign 404s + one legitimate orphan-listing finding);
  `pm_intake` did nothing (3.5s CPU, no jobs claimed); `ebay_legacy_sync`
  completed one full sync (19,611 listings, 501 updated) plus a partial
  second cycle (66/99 pages) via ~165 Trading API calls; `ebay_sync` made
  ~1,600+ Inventory API offer-check calls (SKUs ~4700–6300) before being
  killed — real quota consumption, no 429s/quota-exhaustion seen. Both
  `ebay_sync`/`ebay_legacy_sync` needed `SIGKILL` after a stop-timeout (mid
  in-flight HTTP call) — shows as `systemctl` "failed", not an app bug.
  **Decision needed**: how to make "stopped" durable across rebuilds —
  `systemctl disable` (breaks re-enable-on-next-intentional-start ergonomics),
  a Nix-level default-off list per worker in `tgw.nix`, or an explicit
  `services.tgw.workers.<name>.enable` option read at eval time. Tracked as
  todo #1322.
- **PP-BACKUP-001: 5 open items** — "no backup running" has been on
  `handoff.md`'s risk list since session 42 (weeks old, todos #61/#146/#147).

## 2. Today's cohesion audit — triage

- 45 new todos filed (#1273-#1317), plus the PP-FENCE-002 proposal ("don't
  climb the fence, use the gate") in the inbox — this is the third time the
  same fence-bypass gap class (A4/E5/A8) has surfaced (2026-06-10 review ->
  audit#1143 -> today).
- Decisions needed: adopt proposed invariants A9 (path-input validation) and
  F1 (untrusted content never reaches a live external write unescaped)? Build
  the CI grep-audit A4/E5 already specified in 2026-06-10 and never built?
  Which of the 12 security-tagged (p35) findings are urgent vs. batchable?

## 3. New discussion item: autosave the draft + pre-flight parameter checks before listing

Raised by Dave (2026-07-10), motivated by a live incident today (todo #1318):
`tgw202605051913468`'s draft title was 83 chars; eBay's Inventory API rejected
it (`ebay_stage` dead-lettered) with "title should be between 1 and 80
characters." The operator had no way to even *save* an edited title without
first discovering an undocumented "Clear error" step — fixed today by
restoring a standalone Save button. A third instance (`tgw202605051752520`,
81 chars) surfaced the actual root cause: `seo/title.py::enhance_title()`
only *flagged* oversized titles (`title_too_long`) instead of enforcing the
limit — nothing downstream acted on the flag, so it sailed through
`ebay_draft`/`ebay_upload` and only failed at the `ebay_stage` eBay API call.
**Fixed today (todo #1319)**: hard word-boundary truncation at generation
time, tested, live-verified against all 3 real titles. That's one instance of
the pattern — the underlying question is bigger than title length alone:

- **Autosave the draft editor**, the way eBay's own listing-draft form does —
  don't rely on an explicit Save click before an edit can be lost or before
  the operator can retry. Scope: which fields, what interval/debounce,
  conflict behavior against a concurrent worker-driven `draft_listing` write
  (session-42 redraft-loop history makes this not a trivial addition — see
  `handoff.md` risk 0/#1107 for the auto-redraft infinite-loop precedent).
- **Test for parameter limits locally before attempting to list** — title
  length (80 chars), and audit what other eBay-enforced limits we don't
  pre-validate (description length, item-specifics value length/count,
  price bounds, image count/dimensions, category-specific required-field
  formats). Goal: catch these before the eBay API round-trip, not after a
  dead-lettered job and a manual investigation.
- Relates to today's cohesion-audit findings #1273-1317 (the "climb the
  fence" PP-FENCE-002 note) — several confirmed findings were exactly this
  shape (untrusted/unvalidated content reaching a live eBay write). Worth
  discussing together rather than as isolated fixes.

## 4. Open PP items — status pass (35 tracked, `tgw plan check` clean)

Items with open work worth a look: PP-BACKUP-001 (5 open), PP-PHOTOSYNC-001
(3 open), PP-NIXOS-001 (1 open — a1131 push still pending, #1233), PP-SOLD-001
(2 open), PP-PHOTO-001 (2 open), PP-REPRICER-001 (1 open), PP-RECOVERY-001
(1 open), PP-EBAY-SNAPSHOT-001 (1 open), PP-BULKLIST-001 (1 open),
PP-ACTIONCONSOLE-001 (1 open — operator test still unexecuted per
`handoff.md` risk #4), PP-PLANDB-001 (1 open — Phase 5 proposed 2026-07-10).

## 5. Carried-over open discussion items (from the 2026-07-04 session, still undecided)

These have been sitting in the master plan's "Open discussion items" section
for a week:

- **Web UI vs. Flutter app fork** — feature gap is widening every week; three
  directions (shared backend / WebView shell / freeze-Flutter) not chosen
  between.
- **Vault inbox location** — stay separate from `/opt/TGW/incoming/` or merge
  in? (Dave recalled discussing this before with no record found — captured
  per Prime Directive 5, still open.)
- **PP-INTAKE-004 platform question** — the bigger "is TGW itself a sellable
  platform" business-model question behind the handheld intake app design.
- **Self-healing quality verification** — Dave wanted this explicitly checked,
  not just assumed working.

## 6. Future ideas — review for promotion/deferral

Six items currently in `FUTURE-IDEAS.md`, none touched since they were
deferred:

- PP-NIXSTORE-001 (move /nix to HDD + LVM cache)
- PP-CATIONIX-001 (CatioNIX standalone platform — sequencing note in memory
  says "stabilize TGW first")
- Alt-text on all item photos
- MC-SYNCTHING-VFS (Midnight Commander plugin)
- PP-ANNEX-001 (git-annex tiered GDrive remotes)
- PP-SEARCH-001 (recoll universal index)

## 7. Housekeeping (mechanical, can go fast)

- **Inbox backlog**: ~30 `DONE-*.md` notes sitting unprocessed in `inbox/`
  since 07-09/07-10 (never folded into master plan/handoff and archived) —
  normal per-session processing seems to have lagged. `inbox/queued/` also has
  3 stale `INPROGRESS-session39/40-*.md` breadcrumbs from 07-01 that look
  abandoned, not just queued.
- **`handoff.md` is stale** — last entry is session 48 (2026-07-06); today's
  session (secrets consolidation, worker stop, cohesion audit) isn't
  reflected yet.
- `SUGGESTIONS.md` is clean (0 unchecked) — nothing pending there.
