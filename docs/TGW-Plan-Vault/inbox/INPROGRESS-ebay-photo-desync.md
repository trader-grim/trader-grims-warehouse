# INPROGRESS: eBay photo/quota desync → C10 operator-context build (session 43)

Dave's report (2026-07-03): interface/eBay mismatch on tgw202606021133367, edit not
preserved, photos restricted to a partial set — third consecutive day of EPS quota
exhaustion blocking all listing work.

## Root causes CONFIRMED (journalctl + queue_jobs + quota-state, NOT JSON diffing)
1. `ebay_upload.py:111` masks partial failure as success (`if not uploaded: raise`
   only fires when ZERO photos exist — 17/17 failures logged as "complete, 0 new").
   Still open: todo #1115.
2. s42's redraft-loop fix left ~2,514 SKUs of `ebay_upload` jobs in retry_wait,
   re-arming every ~6h — they raced the midnight-PST quota reset daily and burned
   the whole EPS budget by ~1am. CANCELLED (2,715 jobs) with Dave's authorization.
3. `quota.py`'s 30% operator reserve was structurally unreachable — nothing tagged
   operator-triggered jobs as interactive.

## Invariant C10 BUILT + LIVE-VERIFIED this session (Dave's directive)
"This should be the behavior of List on eBay, it is an operator action…
encode that into all of the operator action surfaces." + "update on ebay also"

- `worker_base._process`: jobs with payload `origin='operator'` run in interactive
  quota context (name `worker:<queue>:operator`), restored to background in finally.
- All 14 operator enqueue sites in `http_server.py` stamp `origin: "operator"`
  (item actions, bulk list_now, bulk pipeline, PATCH auto-redraft, dead-letter
  retry, revision apply, sync_from_ebay, set_ready upload).
- Chain propagation: draft→price/upload, price→stage, stage→publish,
  publish→force-restage, upload→rate-limit-requeue all forward origin.
- Invariant C10 documented in `reference/invariants.md` (🔶 detector pending —
  no CI check yet that NEW operator endpoints stamp origin).
- Tests: `tests/test_operator_origin.py` (11 tests) + fixed stale
  `test_unstaged_item_is_hard_failure` (s42 made unstaged-publish retryable).
  Targeted suite: 68/68 green. Workers + tgw-http restarted.

## REGRESSION CAUGHT AND FIXED during live-fire (important for next session)
First version set context name `operator:<queue>` → fence's X-TGW-Caller header
became `interactive:operator:<queue>` → PATCH auto-redraft guard
(`startswith("background:")`) no longer saw worker writes as machine writes →
**s42 redraft loop resurrected** (upload patches imageUrls → auto-redraft →
draft → upload → …). Caught within 2 cycles via queue inspection. Fix: context
name keeps `worker:` prefix (`worker:<queue>:operator`) AND the guard now treats
any `worker:`-containing caller as machine. Both sides tested
(`test_operator_context_name_reads_as_machine_write`).

## Live-fire result on tgw202606021133367
- Operator ebay_upload job (origin=operator, payload verified in queue_jobs) ran
  interactive and sailed through the 3,500/5,000 halted EPS pool that blocked the
  same photos 3× this morning. All 26 photos now on EPS (17 new uploads,
  journalctl shows each URL).
- Fixed stale photo_order entry (`tgw202606021133367-alt.jpg` → `1-alt.jpg`) via
  the photo-order endpoint.
- ebay_update (force stage, operator) + auto-republish: IN FLIGHT at note time —
  background watcher armed for the publish/photo-verify line. eBay caps images
  at 24; we have 26, so last two in order (21.jpg, exportGif.gif) are cut by the
  [:24] slice in ebay_stage. 1-alt.jpg may sit slightly off its intended slot
  this pass (imageUrls were written before the photo_order fix); a zero-quota
  upload re-run + ebay_update re-places it if Dave cares.

## Still open
1. #1115: ebay_upload completion guard (all-photos-accounted-for, not
   at-least-one) + retry cap so a backlog can't re-arm; ops-digest line for
   retry_wait liability + next-reset quota exposure.
2. C10 detector (🔶): CI/test that new operator endpoints stamp origin.
3. Orphan `ebay_repush` job queued since 2026-07-01 — queue has NO worker;
   either build/remove the queue or cancel the job.
4. Chain-enqueued ebay_price recomputes price on operator updates — watch that
   it can't override an operator-set price (C5 clamp protects raises only).
5. Fleet-wide photo-shortfall rescan (was 492/9,403) now the backlog is gone.
6. NOTHING COMMITTED — Dave controls git. Today's diff: worker_base.py,
   http_server.py, 5 workers, invariants.md, 2 test files.
