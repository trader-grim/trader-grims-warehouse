# IN PROGRESS — #1145 PP-UIPIPE-001: web UI listing pipeline defect audit

Dave's feedback (2026-07-04, s45): recent real listings made through the web
UI pipeline came out wrong — "the web ui pipeline ain't cutting it":
1. Wrong shipping policy on a new listing
2. Item PUBLISHED with no price set in the interface (C1 violation — guard
   bypassed, or price existed in draft_listing but never rendered = display
   bug masquerading as guard failure)
3. Only one photo uploaded, no reattempt (NB: #1115 P1 completion-guard fix
   is marked done — check deploy timing vs listing timing, and whether the
   fix covers the new-listing path or only repair)
4. "etc." — full list to be captured with Dave.

Plan: evidence assembly per item (item JSON + queue_jobs history + journal +
raw E7 fence captures) → defect→root-cause→packet map with Dave → invariant/
detector per root cause (PD5). Awaiting SKUs/item numbers or listing window
from Dave. Possibly some defects predate today's API-provider chaos but Dave
says none are explained by it.

## Evidence sweep (2026-07-04, pre-walkthrough) — Dave's hypothesis CONFIRMED

Dave's guess: "all of them boil down to pipeline order or function combined
and especially mishandling of the draft vs ebay offer." Sweep of the 10
distinct SKUs published in the last 14 days:

1. NO-PRICE PUBLISH confirmed: tgw202605052336026 — local draft_listing.price
   = None, live offer 265455614018 at $40.99 (raw fence capture 2026-07-04).
   UI renders draft (empty); eBay serves offer ($40.99). Local dataset does
   NOT hold the live price — Data Charter inversion.
2. PHOTO PARTIAL confirmed: tgw202605060125081 — 8 local photos, 1 on eBay,
   published 07-04 AFTER #1115 P1 fix marked done → fix gap or new-listing
   path not covered.
3. SHIPPING POLICY pattern: 9/10 items share fulfillment policy 199931446015
   regardless of category (1133367 has 199931450015) → selection likely
   ignores per-category overrides. Awaiting Dave's pointer to the wrong one.
4. SYSTEMIC: same SKUs have dozens of succeeded ebay_publish jobs (07-01/02)
   — C3 says publish is one-time operator-gated; it is silently re-runnable.
5. SYSTEMIC: no published item has a published/live status locally (None/
   Ready/New; three items lack the field entirely) — state machine never
   advanced on publish.

Unifying diagnosis: draft_listing (local intent) vs eBay offer (remote
reality) have no reconciliation discipline in the new-listing path. Proposed
spine for the plan item: publish-time draft==offer verification invariant +
catalog-verify divergence detector + per-defect packets.

## Session pause state (2026-07-04 ~15:00, continue ~4pm)

- HOT DAY: ebay_draft backlog drain PAUSED per Dave (systemctl stop
  tgw-worker@ebay_draft) — 3,052 jobs queued, durable. It was also
  chain-triggering nonstop full 55k catalog rebuilds (~60s CPU+disk each).
  RESTART IN THE EVENING when cool: `sudo systemctl restart
  tgw-worker@ebay_draft.service` (catalog_rebuild follows it back down).
- Thermal at pause: NORMAL 73°C, load dropping after stop.
- #1145 next step at 4pm: Dave identifies the wrong-shipping-policy listing
  + rest of his "etc." defect list; then walkthrough of the evidence above →
  defect→root-cause→packet map. Also verify why #1115 P1 fix didn't prevent
  the tgw202605060125081 1-of-8-photos publish (deploy timing vs path gap).
- a1131 offload: pipeline workers can't move today (need fence + postgres +
  local ItemData); heavy agent-side ops (tests/greps) can run via ssh a1131.
  Real offload design belongs with #1139 (decouple Hermes/AI fleet).

**Correction (Dave):** the a1131 offload meant CLAUDE'S CHECKS (tests, greps,
review sweeps), not pipeline workers — pipeline load is not a thermal problem
absent our own error loops. ebay_draft drain RESTARTED 15:0x (active, zero
429s). a1131 confirmed reachable, idle (load 0.08), repo present — agent-side
heavy checks run there for the rest of the hot day. Note: a1131 checkout may
be stale (todo #1082, no GitHub access) — sync repo state before trusting
test results from it.

## DESIGN DIRECTIVE from Dave (2026-07-04 evening) — the reconciliation broker

Dave, after reviewing today's item resolutions: "Concentrate on making sure
the draft matches ebay offer, then edit offer via ai or editor, get
interface of operator in a consistent state. We have nothing brokering
there, making sure the item fits our specs and repairing if not."

The shape this sets:
1. **draft ⇄ offer consistency is enforced continuously**, not assumed at
   publish — a broker/reconciler detects divergence (price, policy, photos,
   category, aspects) and drives convergence.
2. **All edits (AI or operator editor) route through the same convergence** —
   offer edits land in draft, draft edits land on the offer; one truth.
3. **The operator interface always renders a consistent state** — never a
   draft that silently disagrees with the live offer (today's phantom-$40.99
   case is the canonical counterexample).
4. **Spec validation + self-repair**: the broker checks each item against
   our specs (required policy = config's, price present, photo count matches
   disk, category sane) and repairs automatically where safe, surfaces to the
   operator where not (C11: a finding, not a log line; matches
   feedback-self-healing-system).

Sequencing per Dave: broker/pipeline correctness FIRST; feature work after.
"We have a lot of items in states caused by pipeline errors that would not
normally exist if the pipeline was functioning" — the broker + a one-time
state-repair sweep drains that standing population.

Existing pieces the broker composes (build on, don't duplicate): the nightly
catalog-verify truth audit (P7, detects), the canary probe (P8, detects),
C10 operator lane (acts), revision.py drift-gated apply (writes safely),
todays' #1152 policy fix (one spec rule of many).

## New defects/ideas from today's items (same conversation)
- **Keychain category was wrong** (Dave manually corrected on the BASS
  keychain) — investigate category selection for keychains specifically;
  relates PP-CATPICK-001 (group-shortlist + operator-correction learning:
  his manual fix should have been captured as training signal).
- **Prefill the short search-term description from vision scans** — the raw
  vision scan data (PP-DERIVED-001 principle: scan once, derive many) can
  generate it. EXPLICITLY DEFERRED by Dave until the pipeline fires correct
  actions at correct times.

## Shipping-policy repair executed (2026-07-04 evening, Dave: "all fc4")

Root cause fully traced: config has ONLY fulfillment_policy_id;
_get_listing_policies (ebay/sync.py) is ALL-OR-NOTHING — missing
payment/return ids make it discard the valid FC4 and fall back to
_get_policies() = first-policy-from-API = 150147260015 'PS'. eBay confirms
199931446015 = 'FC4' (live policy list pulled).

REPAIRED: all 8 wrong offers PUT to FC4 (fresh GET → strip read-only →
mutate → PUT → read-back verify, revision.py pattern), every one OK.
Mirrors refreshed; audit regenerated: 0 published-wrong remain among
mirrored items. Report: /opt/TGW/var/reports/ship-policy-audit-2026-07-04.tsv

STILL OPEN:
- Close the config gate: add payment_policy_id + return_policy_id — needs
  Dave's picks: payment 246544838015 'eBay Managed Payments' (only real
  option) + return = 246544837015 'Free Returns' OR 290664933015 'free 30
  days money back'?
- #1152 code fix: make _get_listing_policies per-field (use config where
  present) + C11 finding on fallback.
- Fleet-wide sweep (~2k getOffer) still pending Dave go — mirrored coverage
  is only 29 items.
- Account has 31 fulfillment policies incl ~10 'Copy' clutter — cleanup
  candidate (operator task, low priority).

## TOOL FIX #1152 DONE + live-verified (2026-07-04 late)

Dave's course correction (now memory feedback-fix-the-tool-not-the-list):
fix the tool, don't make him review data lists. Done:
- _get_listing_policies now PER-FIELD (config wins where present; account
  first-listed fills only genuinely missing fields, logged loudly as a
  finding). 3 regression tests; 130 policy/sync tests green.
- Config completed from live evidence (no Dave gate): payment 246544838015
  'eBay Managed Payments' + return 246544837015 'Free Returns' — what every
  live listing already carries.
- LIVE-VERIFIED: forced restage of tgw201501021970354 through the fixed
  path → fresh offer GET shows all three policies from config, no fallback,
  status still PUBLISHED. Every future stage/publish is now correct.
- Un-gated decisions sweep: fleet getOffer sweep will just run (read-only);
  broker proceeds on recommended whitelist; 0125081 photo repush goes via
  C10 without review; ONLY genuine Dave call remaining on these items:
  2336026's intended price — via his (now-working) listing tool.

## Four-item forensics COMPLETE (2026-07-04 night) — one root shape

Dave's 4 items, 4 symptoms, 1 cause: three state planes (TRUTH=disk/ItemData,
PLAN=draft_listing, LIVE=offer) with nothing reconciling them — an error
freezes into the PLAN and becomes invisible because everything downstream
validates against the plan.

1. tgw202605052336026 (price): stale auto-pricer remnant in ebay_offer.price
   (2026-06-17, browse-comps, disabled system) published unreviewed via
   stage's fallback; also made ebay_price skip forever. TOOL FIXED tonight
   (draft-only price + no_price_set finding). Live listing awaits Dave's
   price via editor.
2. tgw202605060125081 (photos): July 1-2 EPS exhaustion + pre-P1 masking
   dropped 7 photos AND left draft.imageUrls=1; today's P1 photo-verify
   passed "1/1 OK" because it used the PLAN as denominator, not the disk.
   HEALED tonight via C10 repush chain: 8/8 live, verified fresh.
3. tgw202605131827555 + tgw202606021107459 (churn): #1107 redraft loop — 57
   full draft→price→upload→stage→publish chains EACH (07-02, and 06-26→07-04
   for 1107459); worker fence-writes misread as operator intent regenerated
   the plan endlessly. Root fixed s42/s43 (X-TGW-Caller); final states
   converged; the chaos Dave experienced was loop-era churn.

BROKER SPEC INSIGHT (added to ai-plans/reconciliation-broker.md): the broker
must validate against TRUTH planes (disk, config, live reads), NEVER against
the plan — the plan is the thing that drifts. P1's plan-as-denominator
verify is the canonical counterexample.

**Corrupt-photo roster (data hygiene, s45 drain):** tgw201601011311007,
tgw201601011312446 (truncated), tgw201707050929532 (broken data stream) —
2016/2017-era files damaged on disk. Candidate broker/verify rule:
photo_files_readable (PIL-open sweep, disk truth). Repair needs source
photos from ItemArchive/originals if they exist — bulk check when the
fleet sweep runs.
