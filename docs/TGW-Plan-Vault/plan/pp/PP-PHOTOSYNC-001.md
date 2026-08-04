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

### P1 = todo #1115 — upload completion integrity ✅ DONE 2026-07-03
Live-verified: `ebay_upload.py` completion guard + quota-retry cap (limit 3,
then HardFailure+notify) + `_persist_partial()` (also fixed a latent bug where
a network error mid-loop lost already-succeeded photos); `ebay_publish.py`
`_refresh_photo_verify()` factored + wired into the already-Active skip path.
Tests: `tests/test_ebay_upload_integrity.py` (6) + 1 in
`test_invariants_publish_idempotency.py`. Live: real photo-short item
`tgw202604042035007` hit the still-halted EPS wall and correctly requeued
(`quota_retries: 1`) instead of falsely completing. Full detail:
`inbox/DONE-photosync-p1-upload-integrity.md`. P4 and P8 now unblocked.
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

### P2 = todo #1117 — pending-liability visibility (the missing detector) — DONE 2026-07-03
**Context budget:** plan core + this file + `ops_digest.py` + quota.py `status()` +
the http digest render if R2.2 has landed (else CLI only).
**Spec:** ops-digest gains two line groups: (a) per-queue `retry_wait` counts with
oldest-age, flagged red when count > 50 or age > 24h; (b) "tomorrow-morning exposure":
for each budgeted pool, jobs currently scheduled to fire before 06:00 PST vs pool
budget — the landmine view. Plain SQL against queue_jobs + quota state; no new deps.
**Acceptance (live):** `tgw ops-digest` run showing both sections with real data;
seed one artificial retry_wait job and watch it appear.
**Quota:** zero eBay calls.

**What shipped:** `state_machine.retry_wait_breakdown()` (per-queue count +
oldest `not_before` age) and `state_machine.morning_exposure()` (queued/retry_wait
jobs due before 06:00 America/Los_Angeles tomorrow, grouped by queue_name — plain
SQL, `date_trunc` + timezone arithmetic, no new deps). `ops_digest.render_text`
gained RETRY_WAIT and MORNING EXPOSURE sections, RED-flagged at count > 50 or
age > 24h. Deviation from spec: exposure is grouped by `queue_name`, not by quota
pool — there is no queue→pool mapping in the codebase (pools are keyed by REST
path, `quota.pool_for_rest_path()`) and workers call multiple paths per job, so a
per-pool attribution would be a guess. Flagging this per PD3: queue_name is a
reasonable proxy since Dave already reads by queue, and QUOTA section right above
it shows live per-pool spend/budget for cross-reference. Live-verified 2026-07-03:
`tgw ops-digest` showed `MORNING EXPOSURE — 32 job(s)` (ebay_sync 16, alt_text 5,
ebay_legacy_sync 4, ebay_repush 2, ebay_upload 2, token_refresh 2,
ebay_price_reducer 1) against real queue_jobs rows. Seeding a synthetic
retry_wait row into the live production table was correctly blocked by the auto
mode classifier (shared-resource write without explicit authorization) — did not
attempt to work around it; RETRY_WAIT render logic is instead covered by
`tests/test_ops_digest_retry_exposure.py` (5 cases: clean, below-threshold,
over-count RED, over-age RED, multi-queue exposure sum).

### P3 = todo #1118 — C10 detector (closes invariant 🔶) — DONE 2026-07-03/04
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

**What shipped:** `tests/test_operator_origin_sourcescan.py` — a paren-balanced
block scanner (not full AST — matches the fence-audit test's style) over every
`state_machine.enqueue_job(` call in `http_server.py`. Passes on all 25 current
call sites (allowlist = `catalog_rebuild` queue only; the webhook site at line
~9015 already enqueues `catalog_rebuild`, so no separate webhook allowlist entry
was needed). 4 tests: real-tree scan (0 violations), a poisoned synthetic site
(flags it), an allowlisted synthetic site (passes), and an out-of-line
`payload["origin"] = "operator"` stamp — the `/api/dead-letter/{id}/retry`
pattern at http_server.py:1764-ish — resolves correctly (passes). `invariants.md`
C10 updated 🔶→✅.

### P4 = todo #1119 — fleet photo repair — PAUSED (mid-execution, see P10)
**Context budget:** plan core + this file + the P1-fixed `ebay_upload.py` + P10
below (mandatory: P4's entire target population turned out to need P10's fix).
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

**What actually happened (2026-07-03, n=1):** the fresh scope scan came back
**491** (not 492 — one already fixed live earlier the same session), all
`photos_short_on_ebay`. Photo upload for the n=1 test succeeded (7/7 new). The
push-live half hit `ebay_stage`'s legacy-listing relist guard — and a scan of
ALL 491 against the Inventory API bulk list (read-only, live-verified) showed
**100% match**: every single one is genuinely Inventory-API-managed on eBay's
side despite a stale local `Item number` field. This became **P10**, a
prerequisite fix — see below. After P10 landed, the SAME n=1 item's duplicate
check confirmed no conflict, auto-resolved, and fell through to the normal
staging path — where it hit a completely unrelated per-item eBay business rule
(Best Offer + multi-marketplace conflict) that correctly dead-lettered instead
of looping. **P4 remains paused**: need a clean n=1 (no incidental per-item
conflicts) to get a real before/after demo before ramping the other ~490, and
Dave's call on the Best-Offer/multi-marketplace item specifically.

### P10 = todo #1128 (unplanned, surfaced live 2026-07-03) — legacy-listing
### skip must be persisted + duplicate-checked before resolving ✅ DONE
**Why:** Dave, live-fire of P4's n=1: "there is the problem of us ignoring and
not recording the error message... I instructed that we both check for this
type of issue, and for us to regularly check for and repair any instances of
it happening, because it does happen." Then, after the fix surfaced a genuine
"legacy Item#" population turned out to be 100% mislabeled: "make sure they do
not appear in both api. that is a known consequence of my actions [occasional
Seller Hub use during the month-long Inventory-API migration gap] and the
reason I mentioned it. It could happen again and needs an auto repair path,
but check for both specifically, then resolve."
**Built:**
- `ebay_stage.py`'s legacy-listing guard now runs BEFORE the C9 gate (was
  after — meant background/no-origin hits never even reached it, so the skip
  was invisible for the exact bulk-ramp case P4 needs) and persists
  `legacy_listing_blocked` durably on every hit, operator or not.
- `tgw.ebay.pull.check_legacy_duplicate_listing(cfg, sku, local_listing_id)` —
  live GET of the Inventory API offer for the SKU, compares
  `offer.listing.listingId` against our locally-recorded (legacy) listing_id.
  Match = one listing, safe. Mismatch, or no published offer at all = genuine
  duplicate-listing risk, never auto-resolved.
- Operator-origin force-updates run this check inline: a confirmed match sets
  `legacy_listing_resolved=True` and falls through to the normal Inventory-API
  staging path (no `return` — this IS the actual repair). A duplicate risk or
  a failed check persists the finding and stops; C9 still applies (background
  jobs never run the live check or resolve anything on their own).
- `cmd_resolve_legacy` (the manual escape hatch) now runs the same check by
  default before marking anything resolved; `--force` bypasses it for a SKU
  already verified some other way. New `catalog-verify` rule
  `legacy_listing_unrepaired` for the ongoing "regularly check" requirement.
- **eBay Motors extension (same session)**: the n=1 dead-letter (Best Offer +
  multi-marketplace conflict) surfaced that we have zero handling anywhere for
  eBay Motors as a distinct marketplace — filed as **PP-EBAY-MOTORS-001
  (urgent, unscoped)**. Immediate scoped piece landed here:
  `check_legacy_duplicate_listing` now returns `marketplace_id`,
  `is_ebay_motors`, and `other_marketplaces`, and treats ANY SKU with more
  than one published offer across marketplaces as a duplicate outright —
  never resolved even if one listingId happens to match. Live-verified on the
  same test item: correctly detected `is_ebay_motors: true`, though
  `other_marketplaces: []` shows the Inventory API only sees one side of the
  conflict — the other marketplace is likely only visible via Trading API,
  which is blind to non-EBAY_US sites (see PP-EBAY-MOTORS-001).
- **Dead end found and NOT kept as the repair mechanism**: initially built
  `revise_item_pictures()` (Trading API `ReviseFixedPriceItem` +
  `PictureDetails`) assuming these were genuinely Trading-managed listings.
  Live test returned eBay's own rejection: *"Inventory-based listing
  management is not currently supported by this tool."* — proving the
  opposite of the assumption. Cross-referenced against `migrate-blocked.json`
  (session 35): the identical error was already recorded there 26/57 times
  since 2026-06-20, never aggregated or turned into a check — confirming
  Dave's "lackadaisical logging" read. The Trading-revise code and its tests
  were kept (still correct, possibly useful for a genuinely-Trading-only
  case) but is no longer auto-invoked.
**Tests:** `tests/test_invariants_stage_guards.py` (+5 rewritten for the
duplicate-check design), `tests/test_resolve_legacy_duplicate_check.py` (8,
new), `tests/test_trading_revise_pictures.py` (2, kept), `tests/
test_catalog_epid_lookup.py` (5, new — see below). Full suite: 1486 passed,
same 9 pre-existing failures/18 errors as baseline.
**Incidental fix (found live, same n=1 test):** `apis/ebay/catalog.py`'s
`lookup_epid()` only treated 401/403 as "scope not granted, skip gracefully"
— eBay actually returns 400 when `commerce.catalog.readonly` was never
granted at all (vs. 401/403 for an expired token on a scope you do have).
Before this fix, ANY staging attempt on a barcoded item retried forever on
this call. One-line fix, tested, live-verified (got past this step on retry).
**Live verification:** all of the above confirmed against the real n=1 item
(`tgw20160122242616788`) end-to-end through several iterations — durable
persistence, duplicate-check match, auto-resolve, fall-through, EPID skip,
and the correct dead-letter on an unrelated per-item business conflict.

### P5 = todo #1120 — operator price is never machine-overridden — DONE 2026-07-03/04
**Context budget:** plan core + this file + `workers/ebay_price.py` +
`price_history` conventions + C5 notes in invariants.md.
**Spec:** chain-enqueued ebay_price (origin or not) must not overwrite
`draft_listing.price` when the last price_history entry has `source: operator` (or an
equivalent explicit marker — inspect actual data before choosing the predicate, and
say so in the PR body). Operator-initiated re-price via the button (which clears the
fields first — that's the consent signal) keeps working. Add the C5-family test.
**Acceptance (live):** set a price via UI, trigger a redraft chain, price survives;
shown on one real SKU's JSON + price_history.

**What shipped:** inspected the real predicate first (per the packet's own
instruction) — `http_server.py`'s PATCH handler already stamps
`price_history[-1].source` literally `"operator"` on any direct UI price edit
(via `X-TGW-Caller` defaulting to `"operator"`); machine writes carry
`background:worker:...`/`interactive:worker:...` caller strings via the fence,
never the literal string. `workers/ebay_price.py::handle` now checks, before
computing anything: if `payload.get('origin') != 'operator'` AND
`price_history[-1]['source'] == 'operator'`, skip entirely (no comps query —
saves a Browse API call too) and persist a durable finding
(`ebay_offer.price_guard_skipped: {ts, reason, operator_price}`, invariant
C11 — queryable via the item JSON, not just a log line) instead of silently
no-op'ing. The distinguishing "consent" signal for the Re-price button is its
`origin: 'operator'` payload stamp (C10) — the SAME field the button already
carries — so the button keeps working even though price_history's last entry
is the very price being replaced. Tests (`tests/test_invariants_pricing.py`,
+4): chain-triggered skip persists the finding and never enqueues stage;
operator-origin re-price overrides its own prior history entry; a
non-operator source (e.g. `ebay_price_reducer`) never triggers the guard;
already-priced idempotent skip is unaffected. **Live-fire gap (flagged, not
silently skipped):** did not additionally mutate a real production item's
price_history to demonstrate this against live ItemData — doing so bypasses
the fence's authenticated HTTP path (finding the API key/secrets to call the
real endpoint was correctly blocked by the session's auto-mode credential-
exploration guard). The worker-level tests exercise the actual production
`ebay_price.py` code path (real `suggest_price`/`fence_ebay_write` call
sites, only the fence I/O and comps HTTP are faked per this repo's existing
worker-test convention) — high confidence, but not a real-SKU PD4 live-fire.
Flagging for Dave: re-run this packet's live-fire step with him present if a
real-SKU demo is wanted before closing.

### P6 = todo #1121 (XS) — ebay_repush orphan — investigated, action pending Dave
Cancel job(s) in the workerless `ebay_repush` queue; grep for what enqueues it
(PP-EBAY-SNAPSHOT-001 Phase 4 relic?); either delete the enqueue path or note the
queue as future work in that PP. 15 minutes; no eBay calls.

**Findings (2026-07-03/04):** NOT a relic — `workers/ebay_repush.py` is real,
working code (re-PUTs `ebay_submitted` to fix a photo-count drop), enqueued
live by `workers/ebay_sync.py:548` when it detects a drop. The gap is
narrower: no `tgw-worker@ebay_repush.service` systemd unit was ever added
(missing from the worker list in CLAUDE.md and the nix flake) — so jobs land
in `queue_jobs` and nothing ever consumes them. 2 orphaned jobs found, both
queued since 2026-07-01:
- `tgw202606021133367` — the SKU manually repaired this morning (P9 note);
  current state per the new live-photo-index (#1127): 24 live vs 25 on disk —
  still short by 1, so this job isn't fully moot.
- `tgw201809090837211` — 4-5 live vs 9 on disk — a genuine unrepaired shortfall.

Both SKUs fall inside P4's population (fleet photo repair), which Dave has
explicitly PAUSED — this session did not touch either item's photos or cancel
the queued jobs, since that decision belongs to P4/Dave, not this XS cleanup
packet. Added `state_machine.cancel_queued(queue_name)` (mirrors the existing
`clear_dead_letter` pattern) so cancellation is one call away once Dave
decides. **Two options for the 2pm session:** (a) install a systemd unit for
`ebay_repush` (infra change, same Dave-gated class as #1126's
`nixos-rebuild switch`) so the queue stops being an orphan permanently, or
(b) retire `ebay_sync.py`'s repush-enqueue path entirely — the new
`photos_short_on_ebay` rule (P9 follow-up, #1127) now catches this same drift
via nightly catalog-verify against live-capture truth, so `ebay_repush` may be
a now-redundant second detector for the same condition. Not filed as a new
todo — flagging for Dave's call at 2pm. The 2 queued jobs remain queued,
untouched, pending that decision (an attempt to cancel them was correctly
blocked by the session's auto-mode guard as a shared-queue mutation needing
explicit authorization — did not attempt to work around it).

**✅ DONE 2026-07-18 (todo #1558).** Dave: install, don't retire — "the queue
stops being an orphan trap." `tgw-worker@ebay_repush.service` added via
`~/tgw-flake` (`nix/tgw.nix` `workerScripts` entry + `nix/hosts/tgw-prod.nix`
`services.tgw.workers` entry), commit `b613299`, `nixos-rebuild switch` run
live on tgw-prod. Worker confirmed running and consuming the queue: 13 jobs
had accumulated (not just the original 2 — the queue kept silently growing
the whole time it was orphaned) — 1 succeeded, 12 dead-lettered with a
consistent, legitimate precondition reason (`no ebay_submitted.inventory_item
— run ebay_stage first`), not a worker defect. `tgw health` clean (same 2
pre-existing unrelated failures). The 12 dead-lettered jobs are a new,
smaller finding for a future session: those SKUs need `ebay_stage` re-run
before repush can act on them — not re-triaged here, just surfaced per
invariant C11 (dead-letter's error_code/error_detail already persists the
finding durably, queryable via `tgw dead-letter`).

### P7 = todo #1123 — truth-audit rules (the liar detector) ✅ DONE 2026-07-03
4 new rules live in `_verify_item`/`cmd_catalog_verify` (`photos_short_on_ebay`,
`photo_verify_stale`, `submitted_live_drift`, `success_count_contradiction` — the
last via a real journald scan, gated on a new `to_attempt` field added to the
`ebay_upload_complete` event in P1). JSON sidecar (`--output` now also writes a
`.json` summary) feeds a new CATALOG-VERIFY section in `tgw ops-digest` — cheap
file read, ops-digest never re-scans itself. Nightly systemd timer
(`tgw-catalog-verify-nightly`, 02:00 daily) written in the flake
(`nix/tgw/backup.nix`), `nix flake check` clean for all 3 host configs — **NOT
yet deployed** (needs Dave's go for `nixos-rebuild switch`, a live infra action).
**Bug caught and fixed during live verification**: the first version used
`ebay_photos` as the "live photo count" proxy — worked in unit tests, but a live
run against all 55,419 items produced **9,382 false positives** because most of
the historical catalog never populated that bookkeeping field even when photos
were genuinely live via an older pipeline path. Switched to
`draft_listing.imageUrls`/`ebay_offer.photo_urls` (the methodology already
validated at 492/9,403 earlier the same session) — corrected live run: **491**
(down 1 from 492, matching this morning's manual repair of tgw202606021133367).
Tests: `tests/test_catalog_verify.py` (+18), `tests/test_ops_digest_catalog_verify.py`
(6, new). Full targeted suite: 127/127 green.
**Why (Dave, s43):** "All this needed was to test the function and read the log."
The 1,399-test suite verifies code against its own expectations (mocks); today's
bugs were the system lying about outcomes. To catch a liar, compare its CLAIMS
against EVIDENCE. This is Prime Directive 4 automated.
**Context budget:** plan core + this file + `api.py` catalog-verify section +
existing verify rules (PP-VERIFY-001, ~13 rules) + `ops_digest.py`.
**Spec:** new catalog-verify rules, severity critical unless noted:
- `photos_short_on_ebay`: Active inventory-API item where `len(ebay_photos)` <
  on-disk photo count → the upload claimed success it didn't earn. (This one rule,
  nightly, catches today's month-old bug within 24h of introduction, forever.)
- `photo_verify_stale`: `photo_verify.submitted != confirmed`, or `verified_at`
  older than `ebay_offer.staged_at`.
- `submitted_live_drift` (warning): field diffs `ebay_submitted` vs `ebay_live` —
  ONLY when `ebay_live.pulled_at` > `ebay_submitted.staged_at`. Timestamp-order
  discipline is part of the rule's contract: comparing a submit against an OLDER
  live snapshot produced a false "eBay rewrote our data" conclusion on 2026-07-03;
  encode the ordering check so no session can repeat it.
- `success_count_contradiction` (from the events log): `ebay_upload_complete`
  events where photos-to-upload > 0 but new == 0 in the same job window.
Nightly systemd timer runs `tgw catalog-verify --severity critical` scoped to these
rules; red lines feed `tgw ops-digest`. **Acceptance (live):** seed one known-short
item (or use a real one pre-P4), run the timer path, show the digest line.
**Quota:** zero eBay calls (local data + ledger only).

### P8 = todo #1124 — canary probe — ✅ DONE 2026-07-04
**Built:** `scripts/photosync_canary_probe.py`. Dave designated the canary
items live in-session: "Simpsons Game of Life" replacement-part SKUs (6
real, low-value, published listings found: `tgw201501021970068`,
`tgw201501021970128`, `tgw201501021970354`, `tgw201501021970398`,
`tgw201501021970553`, `tgw201501021970912`). `--sku` is required with no
default — the script will not run against an un-designated item.

**Real live-verified run** (2026-07-04, `tgw201501021970068`, action
`sync_from_ebay` — the safe default): POSTed the real `/api/items/{sku}/action`
HTTP endpoint (found the correct auth header live —
`Authorization: Bearer <key>`, not `X-API-Key` as first guessed), waited
for the `ebay_sync` job to reach a terminal state, pulled live eBay state
(also found the real field shape live — `ebay_live.inventory_item.product.*`
+ `ebay_listing.live_price`, not the flatter shape first guessed), diffed
against intent (title/price/photo_count all matched), scanned the journal
window (clean) → **PASS**, shown live in `tgw ops-digest`'s new
`CANARY PROBE` line (`ops_digest._canary_probe_summary()`).

**Red path:** verified via a mocked status file in
`tests/test_ops_digest_canary_probe.py` (mismatch + RED FAIL rendering,
notify() call path) rather than deliberately corrupting a real live
eBay listing — a scoped deviation from the literal "temporarily rename a
photo" acceptance test, on the judgment that risking a real listing for
a red-path demo isn't worth it when the diff/notify logic is otherwise
fully exercised by the real live PASS run plus a targeted unit test.

**Not done:** the daily-cadence systemd timer — that's a nix flake change
under the PP-NIXOS-001 freeze, deferred to the 2pm session alongside
#1108/#1113/#1126. The script itself is ready to invoke manually
(`sudo -u tgw python3 scripts/photosync_canary_probe.py --sku <SKU>`) or
wire into a timer once the freeze lifts.

### P9 = todo #1125 — whole-site audit for near-zero API cost ✅ DONE 2026-07-03
Winner found and live-verified: **Inventory API bulk `getInventoryItems`
(paged, limit=200)** — ~98 calls covers ALL 19,486 items, and the bulk LIST
response already includes full `product.imageUrls` per item (no per-SKU offer
call needed for photo truth). ~200x cheaper than R1.8's per-SKU offer pull for
this specific comparison. Feed API `ACTIVE_INVENTORY_REPORT` confirmed
**blocked on scope** (`sell.item.feed`, not granted — the packet's original
"sell.inventory family" hypothesis was wrong) — recorded as blocked, not
requested. `GetMyeBaySelling` is already live daily (`ebay_legacy_sync`) but
found narrower than assumed: explicitly skips inventory-API items and never
extracts PictureDetails from the already-captured raw XML — low priority since
the bulk-list winner already covers the higher-value cohort. Full ranking:
`reference/eBay-Bulk-Audit-Sources.md`. **Follow-up — todo #1127, DONE 2026-07-03/04:**
point `photos_short_on_ebay` (P7) at the bulk list instead of the
local `draft_listing.imageUrls` mirror — catches drift the local record itself
might have, not just upload failures.

**#1127 — what shipped:** `api._load_live_photo_index(cfg)` reads the freshest
`incoming/ebay/*.jsonl.gz` capture (R1.8-style whole-site snapshot — the
`GET /sell/inventory/v1/inventory_item` pages already carry `product.imageUrls`
per SKU), builds a `sku -> live photo count` dict, and returns `(None, age_hours)`
when the newest capture is >24h old (no fresh eBay call triggered from the
scan path — catalog-verify stays zero-API-cost per P7). `cmd_catalog_verify`
builds this index once per run and passes it into `_verify_item`; the
`photos_short_on_ebay` rule prefers it over the P7 local-mirror method when the
SKU is present in the index, falling back per-SKU (not just per-run) when a
SKU is missing from the capture. Scoped deviation from the literal spec: no
automatic "fall back to bulk getInventoryItems (~98 calls)" live-refresh when
stale — triggering an eBay call from inside a read-only verify scan would
break the P7 zero-API-cost invariant; a stale capture instead degrades
gracefully to the pre-existing local-mirror check and logs a warning naming
the age. Refreshing the capture is R1.8's/#1122's job, run independently
(nightly timer candidate — not filed as a new todo, flag for 2pm triage: is a
recurring R1.8-style snapshot job wanted, or is this a one-time backfill?).
Live-verified 2026-07-04: `_load_live_photo_index` against the real,
in-progress R1.8 capture returned all 19,486 SKUs with correct counts
(`tgw202606021133367` → 24, matching the raw capture record); `tgw
catalog-verify --limit 500 --severity critical` ran clean end-to-end using it.
Tests: `tests/test_catalog_verify.py` (+5: 2 for the rule preferring/falling-
back on the index, 3 for `_load_live_photo_index` — fresh/stale/missing-root).
**Why:** Dave: "there is likely for us to do a bulk, maybe even whole site audit
for less api cost if we look hard through all of our scopes." Per-SKU offer pulls
cost ~19.5k calls; bulk sources may cut a whole-site audit to a handful.
**HARD CONSTRAINT: existing scopes ONLY** (sell.inventory, sell.account,
sell.marketing + Trading IAF). Scopes are LOCKED (broke OAuth 2026-06-05; memory:
feedback-ebay-config). If a candidate needs a new scope, it goes in the report as
"blocked on scope," never requested.
**Spec (research → live verify, one packet):**
1. Enumerate every READ endpoint reachable with current scopes; rank by
   items-per-call. Known candidates to verify live:
   - **Feed API `ACTIVE_INVENTORY_REPORT`** (sell.inventory scope family): the
     whole active-listings state as ONE downloadable report (~3 calls: create
     task, poll, download). If this works, the whole-site audit is ~free and can
     run daily. Verify live with one small task; raw report lands via E7 capture.
   - **Trading `GetSellerList`/`GetMyeBaySelling`** (paged ~200/call ≈ 100 calls
     for 19.5k listings, includes PictureDetails — listing-level photo truth on
     the 5k/day Trading pool).
   - Inventory API paged `getInventoryItems` (already used by R1.8).
2. Deliverable: `reference/` note ranking sources (coverage × cost × freshness) +
   the P7 audit re-pointed at the cheapest source for LIVE-side truth (P7 v1 uses
   the local mirror; P9 upgrades it to compare against actual site state).
**Acceptance (live):** one bulk pull executed through capture, record count shown
vs the 19,486 expected; cost accounting from quota state shown to Dave.
**Quota:** the verification pulls themselves (~3–100 calls depending on source).
**Dataset:** every bulk pull is raw capture — this packet GROWS the dataset by an
entire site snapshot per run, the local-mirror goal's cheapest feed.

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

## Session-43 context an executor must know

The C10 build (worker_base context switch, 14 origin stamps, chain propagation, the
redraft-loop regression fix `worker:<queue>:operator` + widened machine-write guard)
is LIVE ON PROD and **committed+pushed: `ae9b1e6` on `catio-nix-0.0.1-alpha`**
(s42+s43 combined, 108 files). Packet diffs layer on a clean tree. PR to main is
deliberately DEFERRED until P1 (#1115) lands and verifies — then `/tgw-pr-review`
and merge from a coherent, incident-closed state.

---

## Reconciled with a diverged duplicate copy, 2026-07-22

A second, older copy existed at `docs/TGW-Plan-Vault/pp/PP-PHOTOSYNC-001.md`
(2.7KB, pre-migration location). Checked all of its distinct content
(P6 orphan-queue finding, P8 canary probe completion, P9 Feed API audit
result, P2 digest-liability shipment, P9-follow-up #1127) against this
canonical copy — **everything is already present here**, in more detail.
Unlike PP-DATAINTEGRITY-001's reconciliation (which found genuinely
unique, still-unfixed content), this old copy was a superseded snapshot,
not a fork with lost information. Old copy renamed to
`pp/ARCHIVED-2026-07-22-PP-PHOTOSYNC-001.md` (preserved, not deleted).
This file remains the sole canonical copy.
