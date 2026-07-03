# PP-PHOTOSYNC-001 — upload integrity + operator lane hardening + fleet photo repair

**Opened:** 2026-07-03 (session 43). **Owner track:** runs AS the fix track, parallel
with the forward track (R1.8 #1122, PP-BACKUP-001, #1102). Dave's framing: "resolve
this and continue moving forward in parallel."

## Why this PP exists (incident summary — full detail: journalctl + s43 handoff entry)

Three consecutive days (07-01/02/03) of EPS quota exhaustion blocked all operator
listing work. Root causes, all confirmed from logs/queue/quota-state, not inference:

1. `workers/ebay_upload.py` **masks partial failure as success**: the completion guard
   `if not uploaded: raise` fires only when ZERO photos exist. An item with any
   pre-existing `ebay_photos` logs "complete (0 new)" while every new photo fails.
   Confirmed 17/17 quota failures logged as success on `tgw202606021133367`.
2. s42's redraft-loop fix left **~2,514 SKUs of ebay_upload jobs in retry_wait**,
   re-arming every ~6h forever; they raced each midnight-PST reset and burned the
   full 5,000/day EPS budget by ~1am. Backlog cancelled (2,715 jobs) 2026-07-03 with
   Dave's authorization. Nothing prevents recurrence yet.
3. quota.py's 30% operator reserve was structurally unreachable → **fixed s43 as
   invariant C10** (operator origin end-to-end, live-verified). Detector still 🔶.

**Fleet damage measured:** 492 of 9,403 inventory-API published items have fewer live
eBay photos than exist on disk (scan 2026-07-03, pre-backlog-cancel; P4 re-measures).

## Dataset statement (Prime Directive 1)

P4 repairs push local photos → EPS and record the returned URLs into `ebay_photos` /
`draft_listing.imageUrls` — growing the eBay-mirror asset for ~492 items. P1–P3/P5–P6
add no external data (integrity/detector work) — flagged per charter, justified as
protecting the acquisition path itself.

## Authorizations already granted (do not re-ask)

- **P4 ramp PRE-AUTHORIZED by Dave 2026-07-03**: 1 → 5 → ramp. Inspection at n=1 and
  n=5; then remaining items paced within the background EPS budget (~3,500/day soft,
  never past the 70% halt — the halt itself is the pacer). Est. 2–3 days.
- Backlog cancel already executed; do not re-cancel without fresh evidence.

## Packets (one packet = one todo = one session; specs below are the contract)

### P1 = todo #1115 — upload completion integrity  ← START HERE, blocks P4
**Context budget:** plan core + this file + `workers/ebay_upload.py` +
`ebay/upload.py` + `queue/worker_base.py` (read) + `tests/test_operator_origin.py`
(pattern reference).
**Spec:**
- Success requires ALL expected photos accounted for: `len(uploaded) ==
  len(ordered_photos(...))`. Anything less: persist partial progress (keep the
  existing fence patch of what succeeded), then raise RuntimeError naming the
  shortfall (`uploaded=N expected=M`) so worker_base's transient-requeue path
  reschedules it. NEVER report `ebay_upload_complete` on a shortfall.
- Retry cap against quota walls: a job whose failure is quota-classified may
  transient-requeue at most 3 times (count in payload, e.g. `quota_retries`); the
  4th goes to dead_letter WITH a notify() — visible, not immortal. This kills the
  immortal-backlog class (cause #2) at the worker level.
- The rate-limit self-requeue branch must preserve `origin` (already done s43) AND
  the new retry counter.
- `photo_verify` in `ebay_listing` must also be refreshed on the ebay_update/re-stage
  path, not only at first publish (it currently shows 9/9 from the morning publish on
  an item that has had 24 live since 09:55).
**Out of scope:** ordering cosmetics; ebay_stage's [:24] cap policy; P4's sweep.
**Acceptance (live):** pick any photo-short item; run operator upload with EPS pool
artificially near halt (or stub) → job must requeue, not "complete"; then with room →
completes, `photo_verify` counts match, shown for one real SKU.
**Quota:** no new call types; strictly fewer wasted EPS calls.

### P2 = todo #1117 — pending-liability visibility (the missing detector)
**Context budget:** plan core + this file + `ops_digest.py` + quota.py `status()` +
the http digest render if R2.2 has landed (else CLI only).
**Spec:** ops-digest gains two line groups: (a) per-queue `retry_wait` counts with
oldest-age, flagged red when count > 50 or age > 24h; (b) "tomorrow-morning exposure":
for each budgeted pool, jobs currently scheduled to fire before 06:00 PST vs pool
budget — the landmine view. Plain SQL against queue_jobs + quota state; no new deps.
**Acceptance (live):** `tgw ops-digest` run showing both sections with real data;
seed one artificial retry_wait job and watch it appear.
**Quota:** zero eBay calls.

### P3 = todo #1118 — C10 detector (closes invariant 🔶)
**Context budget:** plan core + this file + `tests/test_operator_origin.py` +
`http_server.py` (scan target only).
**Spec:** source-scan test (fence-grep-audit pattern): every
`state_machine.enqueue_job(` site in `http_server.py` must either stamp
`origin: "operator"` or appear in an explicit allowlist (catalog_rebuild sites,
webhook). New unlisted site without origin = test failure with a message telling the
author which invariant they hit. Update `invariants.md` C10 🔶→✅.
**Acceptance:** test fails when a deliberately-unstamped enqueue is added locally,
passes on current tree. Tests-only packet — live-fire N/A (flag per PD4 that this is
a detector, not behavior).

### P4 = todo #1119 — fleet photo repair (GATED ON P1; ramp pre-authorized)
**Context budget:** plan core + this file + the P1-fixed `ebay_upload.py` + s43 scope
scan method (in session transcript / re-derive: compare `ebay_photos` count vs
on-disk photo count for Active inventory-API items).
**Spec:** re-run the scope scan post-backlog (fresh number, was 492). Then: enqueue
operator-origin `ebay_upload` + `ebay_stage(force, origin)` for ONE item → verify live
imageUrls on eBay (ebay-pull, not local state). Then 5 items → show Dave the
before/after table. Then ramp the remainder as BACKGROUND jobs (no origin — the fleet
sweep is not a button press; it must yield to Dave's live work and stop at the 70%
halt, resuming next day). Every repaired item's job carries a dedupe key; log a
per-day repaired-count line into the digest.
**Out of scope:** items whose disk photos are corrupt/unreadable (list them, don't
fix); legacy `api != inventory` items.
**Acceptance (live):** n=1 and n=5 eBay listings show full photo sets via ebay-pull;
ramp-phase daily digest line; final rescan shows shortfall count ≤ unrepairable list.
**Quota:** ~2–4k EPS calls total, paced by the halt across days; inventory-pool calls
for re-stages (negligible vs 2M).

### P5 = todo #1120 — operator price is never machine-overridden
**Context budget:** plan core + this file + `workers/ebay_price.py` +
`price_history` conventions + C5 notes in invariants.md.
**Spec:** chain-enqueued ebay_price (origin or not) must not overwrite
`draft_listing.price` when the last price_history entry has `source: operator` (or an
equivalent explicit marker — inspect actual data before choosing the predicate, and
say so in the PR body). Operator-initiated re-price via the button (which clears the
fields first — that's the consent signal) keeps working. Add the C5-family test.
**Acceptance (live):** set a price via UI, trigger a redraft chain, price survives;
shown on one real SKU's JSON + price_history.

### P6 = todo #1121 (XS) — ebay_repush orphan
Cancel job(s) in the workerless `ebay_repush` queue; grep for what enqueues it
(PP-EBAY-SNAPSHOT-001 Phase 4 relic?); either delete the enqueue path or note the
queue as future work in that PP. 15 minutes; no eBay calls.

## Parallel forward track (NOT this PP — listed so the two sessions don't collide)

- **#1122 R1.8 snapshot — GO granted 2026-07-03.** `scripts/ebay_snapshot_all.py`,
  inventory pool, capture layer. File-disjoint and pool-disjoint from every packet
  here. Safe to run same-day as P1–P3; if P4's ramp is active, still fine (different
  pool), but run snapshot first if starting both cold — it's pure reads.
- **PP-BACKUP-001** (top operator risk, R2.5 recut, todos #61/#146/#147/#1052) —
  systemd/scripts only, zero overlap.
- **#1102 test-suite repair** — tests/ only. Note: session 43 already fixed one stale
  test (`test_unstaged_item_is_hard_failure` → retry semantics); don't re-litigate.

**Collision rule:** the fix track owns `workers/`, `queue/`, `ops_digest.py`,
`http_server.py`; the forward track owns `scripts/`, `etc/`, `tests/` (except
operator-origin tests). Neither touches `apis/ebay/client.py`.

## Session-43 context an executor must know (uncommitted!)

The C10 build (worker_base context switch, 14 origin stamps, chain propagation, the
redraft-loop regression fix `worker:<queue>:operator` + widened machine-write guard)
is LIVE ON PROD but NOT COMMITTED. First action of the first executor session:
ask Dave to commit (or get his go and commit) so packet diffs stay reviewable.
Diff surface: `queue/worker_base.py`, `http_server.py`, 5 `workers/ebay_*.py`,
`reference/invariants.md`, `tests/test_operator_origin.py`,
`tests/test_invariants_publish_idempotency.py`.
