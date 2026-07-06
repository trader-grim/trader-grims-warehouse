# DONE — #1145 console error-state fix → broker B1a/B1b → fleet baseline (todos #1155-#1160)

NEXT SESSION (Dave, end of s46 — operator test on tgw202605052336026):
1. Price edit works but UX confusing: (a) no visible Save button for the
   draft after editing price; (b) Update Item button stays GREY after the
   edit (should turn yellow = unpushed changes; likely _diverged computed
   server-side at render, doesn't react to client-side edit — needs
   client-side dirty tracking or reload after save).
2. While listing is LIVE: Archive + Delete should be non-functional
   (disabled), becoming available only after End Listing succeeds.
3. REFINEMENT: "Update Item" and "Reset Draft" are the same brokering
   operation from opposite directions — one makes offer match draft, the
   other makes draft match offer; both arrive at the matching/baseline
   state. Unify them around that symmetry (next session's design center).
4. Dave: "We seem to have a broker now functioning at the critical
   junction." — B1a core accepted; live-fire checks still pending
   (publish-baseline on next real publish; suggest-only on next re-price).

PART 4 (latest): FLEET BASELINE DONE — 19,256 items at draft_listing_state=
baseline, converged to zero writes (fleet_baseline_sweep.py, 4 report files
in var/reports/). 388 legacy no-mirror listed items + 3,050 sold + ~33k
never-listed excluded by design. INCIDENT during run 1: sweep's non-machine
caller triggered fence auto-enqueue of 8,183 forced ebay_stage pushes; 73
executed (no-op content, all republished OK), 18 dead-lettered on
pre-existing orphaned offers (#1077-class, triage list in queue ledger),
8,183 cancelled; root-caused + fixed (background: caller prefix) same turn.
Second code review: 4 findings CONFIRMED/PLAUSIBLE, all fixed (caller flood;
reducer baseline erosion — reducer now maintains baseline; sweep rollback;
TypeError hardening). DAVE DIRECTIVE encoded: auto-pricer (ebay_price) sets
price ONLY on initial identification; re-runs refresh comps + write
ebay_offer.suggested_price/suggested_at, never touch draft/offer price or
repricer floor, no draft_listing write (state preserved). Workers restarted:
ebay_price, ebay_price_reducer, ebay_stage, ebay_publish, tgw-http.
STILL PENDING LIVE-FIRE: publish-baseline hook (next real publish);
suggest-only path (next re-price on a priced item).

Dave reported: editor for tgw202605052336026 showed "failed / dead letter /
Retry" while the listing is LIVE (327246911402, $40.99, ACTIVE). Root cause:
two stacked defects in http_server.py, both FIXED and verified live 2026-07-05:

1. pipeline_error schema mismatch — s45's no_price_set guard writes
   {code, detail, ts, source}; the console reader only understood the old
   {worker, error, raw, at}. The "needs price" finding was invisible.
   Fix: reader normalizes both schemas; guard findings render as
   "<source> stopped: <code>" with full detail + Clear error.
2. Action-line ranking — `_has_error` (any dead_letter job) outranked
   `is_active`, so a live listing rendered a blind red Retry (which would
   just dead-letter again). Fix: live state wins; error becomes a directed
   affordance — no_price_set → red "Set Price" (scrolls/focuses
   dl-price-input); other errors → "Needs attention" (scrolls to error box).

Verified live: page renders Set Price + "ebay_stage stopped: no_price_set"
box, no Retry button. tgw-http restarted, health run (fails are pre-existing:
backups stamp, nats asyncio, #1077 sync fallback, quota daily counter).

Context: the 05:47 dead_letter was s45's own deliberate guard-verification
job (dedupe s45-nopriceguard-verify-2336026) — the guard worked as designed.
The reconciliation broker (ai-plans/reconciliation-broker.md) is NOT built
yet — still awaiting Dave's B0 sign-off; this fix is the minimal console
piece of that gap.

FOLLOW-UP same session (todo #1156, DONE): Dave — the already-specified
operator component for resolving draft⇄live is the Reset Draft
(reset_draft_from_live) button. Found it broken since birth: (a) only
rendered when `_diverged` (which ignores draft.price=None, i.e. hidden in
exactly the broken states), (b) the action was never added to
PIPELINE_ACTIONS, so clicking it always returned "unknown action". Fixed:
button now always renders on live items; action whitelisted; a successful
reset also clears the now-stale pipeline_error (flagged deviation, Dave saw
rationale); "Needs attention" indicator now keys off the persisted C11
finding, not historical dead_letter ledger rows. Exercised live on
tgw202605052336026: draft re-pinned (price 40.99 = live), pipeline_error
cleared, page shows Reset Draft + "Draft matches the live listing", no
Set Price / Needs attention / error box.

OPERATOR NEXT: tgw202605052336026 draft now mirrors live at $40.99 — that
price is stale auto-pricer output (comps median $14.58, p25 $9.97); set the
real price in the editor → Update Item.

Side note: 3× Browse-API 429s (ebay_other pool) overnight during the
ebay_draft comps batch, self-resolved, queue drained — watch item only.
ebay_trading at 3340/5000 (67%) today from legacy sync.

SAME SESSION, PART 3 — broker B1a/B1b built (todos #1157/#1158 DONE).
Dave gave the B0 lifecycle directive: draft is a working surface; AI
manipulation starts from live; operator edits/approves/lists; THEN the
manager makes draft match offer. State table (M1-M4/S1/N1-N4) encoded in
ai-plans/reconciliation-broker.md "Draft lifecycle — B0 design decision".
Built + live-verified: src/tgw/draft_sync.py; draft_listing_state
editing/baseline lifecycle (PATCH hook + publish-success baseline + Reset
Draft pin); C11-safe pipeline_error resolution incl. self-clear on the
fixing edit; writer schema unified (code/detail/ts/source/raw) in
ebay_stage+ebay_publish with reader shim; console Set Price for any state,
baseline-superseded dead_letters silent. Services restarted: tgw-http,
tgw-worker@ebay_stage, tgw-worker@ebay_publish. tgw health: only the
pre-existing failures (backups stamp, nats asyncio, #1077, quota counter).
PENDING LIVE-FIRE: publish-baseline hook fires on next real publish —
check draft_listing_state=baseline lands on it. NOT BUILT YET: M3 (ebay_draft
seeds from live), S1 reconcile worker (B3), spec_rules/detect (B1), B5 drain.
Schema doc updated (draft_listing_state, baseline_at, pipeline_error).
