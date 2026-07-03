# DONE: PP-PHOTOSYNC-001 P1 — ebay_upload completion integrity

Todo: #1115. Executing per packet spec in `plan/pp/PP-PHOTOSYNC-001.md`.

## What I'm doing
Fixing `src/tgw/workers/ebay_upload.py`'s completion guard, which currently reports
"complete" even when every new photo fails (only fails if uploaded list is totally
empty). This is the bug that caused 3 days of silent partial photo uploads.

## Spec (from the packet)
1. Success requires ALL expected photos accounted for — `len(uploaded) ==
   len(ordered_photos(...))`. Shortfall: persist partial progress (fence patch of
   what succeeded), raise RuntimeError naming the shortfall so worker_base's
   transient-requeue reschedules it. Never log `ebay_upload_complete` on shortfall.
2. Quota-wall retry cap: track `quota_retries` in payload; cap at 3 requeues, then
   dead_letter with notify() (visible, not immortal — kills the immortal-backlog
   class from s43).
3. Rate-limit self-requeue keeps `origin` (done s43) AND the new retry counter.
4. `photo_verify` refreshed on ebay_update/re-stage path too, not just first
   publish (ebay_publish.py's photo_verify block, currently only runs at publish).

## Done
All 4 spec items implemented:
1. `ebay_upload.py`: any per-photo failure (quota or otherwise) now raises/requeues
   instead of silently completing; `_persist_partial()` factored out and called at
   every exit point so partial progress is never lost (also fixed a latent bug:
   network-error mid-loop used to abandon already-succeeded photos unpersisted,
   causing wasteful re-uploads on retry).
2. `quota_retries` tracked in payload, capped at `QUOTA_RETRY_LIMIT=3`; 4th
   quota-blocked pass raises `HardFailure` + `notify()` — visible dead-letter,
   not an immortal backlog.
3. Origin propagation confirmed intact through the new quota-retry path (test:
   `test_quota_retry_preserves_operator_origin`).
4. `ebay_publish.py`: factored `_refresh_photo_verify()`, now called on BOTH the
   full-publish path and the already-Active skip path (the gap that left
   tgw202606021133367's photo_verify stale at 9/9 after a 24-photo update).

## Tests
New: `tests/test_ebay_upload_integrity.py` (6 tests) +
`test_already_active_item_refreshes_photo_verify` in
`test_invariants_publish_idempotency.py`. Full targeted suite: 95/95 green.
Full repo suite: 1444 passed / 9 failed / 18 errors — same pre-existing counts as
the committed baseline (verified via git stash diff), zero new failures.

## Live verification (PD4)
Workers restarted. Real quota was still halted (3,517/5,000 today's actual spend)
so used a real photo-short item (`tgw202604042035007`, 7/8 photos) as a live test:
enqueued a plain background `ebay_upload` job. Log confirms the fix:
`quota wall hit ... after 0/2 new photos this pass` → `quota-blocked (retry 1/3)
— saving 7 uploaded so far, requeueing remainder` — NOT `ebay_upload_complete`.
Continuation job verified in queue_jobs: `quota_retries: 1`, `not_before` ~6h out.
No `origin` on this job (correct — it was a background test, not an operator job).

## Status: COMPLETE. Todo #1115 marked done. Next packet per plan: P7 (#1123) or
the parallel forward track #1122 (now top of `--next`).
