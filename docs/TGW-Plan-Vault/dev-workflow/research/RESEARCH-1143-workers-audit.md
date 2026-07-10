# RESEARCH — todo #1143 staged cohesion+correctness audit: src/tgw/workers/ (2026-07-05)

First staged slice of the PP-1143 full-codebase audit (Workflow tool, 5 file-groups,
60 agents, ~2.3M tokens, 25 files / 6,817 lines reviewed, adversarial verify 2-of-3
per finding — 17 confirmed, 1 refuted and dropped). Remaining subsystems queued:
apis/ebay/, http_server.py, queue/state-machine, scripts/, nix flake.

## Correctness bugs (data loss, corruption, or crash risk)

1. `itemdata_scrub.py:121` — raw non-atomic overwrite of item JSON, no archive step.
   Crash mid-write truncates/corrupts the file; deleted fields have no backup.
2. `multi_intake.py:170` — bundle child item JSON overwritten (strip "Item number")
   without archiving first. Real data permanently deleted, unrecoverable if wrong.
3. `pm_intake.py:616` — Master Plan doc overwritten with a plain (non-atomic) write.
   Crash mid-write leaves a truncated plan file that Syncthing then propagates.
4. `token_refresh.py:93` — only reschedules its own next check on success; an
   unexpected error ends the refresh chain silently. Token eventually expires,
   every eBay-facing worker starts failing with no alert.
5. `velocity_stats.py:78` — same self-scheduling flaw, nightly analytics job.
6. `ai_identify.py:288` — "force re-identify" flag cleared in memory only, never
   persisted. Every future run re-triggers billed vision-AI calls forever.
7. `ebay_publish.py:250` — condition-rejection fallback (errorId 25021) succeeds on
   eBay but the corrected condition is never written back to draft_listing/ebay_offer.
   Local record permanently disagrees with live eBay state; re-staging repeats the
   400+fallback cycle every time instead of converging.
8. `ebay_sku_migrate.py:252` — eBay-side migration succeeds but local folder rename
   fails: item is never flagged blocked. Re-processed every cycle forever, no alert.
9. `photo_history_recovery.py:118` (workers/ copy) — dropped the dry-run safety gate
   present in the `tools/` near-duplicate. Runs write straight to live records with
   no review step.

## Invariant violations (fence bypass / uncontrolled paths — invariants.md A1/A4)

10. `ebay_sku_migrate.py:220` — several item-field writes bypass the fence write path;
    no protection against a concurrent operator edit; not on the tracked-gap list.
11. `itemdata_scrub.py:101` — SKU/target folder taken from the job message with no
    validation; a malformed job can write outside the ItemData tree.
12. `photo_history_recovery.py:118` — copies photos into an item folder but never
    triggers a catalog refresh; catalog stays stale until an unrelated trigger fires.
13. `bundle_intake.py:212` — hand-assembles item paths instead of the shared fence
    helper; not on invariants.md A4's known-exceptions list.
14. `ebay_draft.py:286` / `ebay_upload.py:76` — same hand-assembled-path pattern, also
    missing from the A4 exceptions list.
15. `ai_identify.py:148` — same hand-assembled-path pattern for reads.

## Cohesion / reuse (no current data risk, future drift risk)

16. `itemdata_scrub.py:84` — doesn't use the standard queue system; invents an ad-hoc
    file-based queue with no visibility in normal queue-status tooling.
17. `ebay_stage.py:46` — `_format_ebay_error()` byte-for-byte duplicated from
    `ebay_publish.py` instead of shared; a future fix to one silently misses the other.

## Prioritization

Items 1-9: real risk of silent data loss, corruption, runaway API cost, or a stalled
critical process (token refresh) — fix first. Items 10-15: structural fence/path gaps,
safe today, will bite silently on the next storage-layout change. Items 16-17:
code-duplication cleanups, no current risk.
