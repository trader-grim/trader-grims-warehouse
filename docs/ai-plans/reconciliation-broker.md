# reconciliation-broker: the draft⇄offer consistency broker — spec validation, convergence, self-repair

**Status:** Draft — 2026-07-04 (session 45)
**PP ref:** PP-UIPIPE-001 (#1145 spine). Dave's directive, verbatim anchor: "Concentrate
on making sure the draft matches ebay offer, then edit offer via ai or editor, get
interface of operator in a consistent state. We have nothing brokering there, making
sure the item fits our specs and repairing if not."

## Problem / motivation

The pipeline is a one-way conveyor: draft → stage → publish, and nothing ever
re-checks that local intent and live offer still agree. Every confirmed s45 defect is
that one gap in a different costume: phantom price ($40.99 live, draft None), missing
fulfillment policy (account default on 8/8 recent listings), 1-of-8 photos with no
reattempt, publish re-runnable dozens of times, `status` never advanced on publish,
operator UI rendering a draft that silently disagrees with eBay. Additionally a
standing population of items sits in states "caused by pipeline errors that would not
normally exist if the pipeline was functioning" (Dave) — nothing drains it.

**Design stance (feedback-improvements-are-missing-pieces):** the broker is NOT a new
subsystem. Detection, action, and safe-write organs all exist; only the brokering
loop is missing. This plan is a composition.

| Existing organ | Provides | Where |
|---|---|---|
| catalog-verify (`_verify_item`, api.py) + nightly timer (P7) | rules engine, severity, `--fix` plumbing, JSON sidecar, ops-digest consumption | live since s43/s45 |
| canary probe (P8) | daily real-button end-to-end check | PP-PHOTOSYNC |
| R1.8 whole-site captures + `_load_live_photo_index` | zero-API live-side truth (photo counts today; generalizable) | api.py:1193 |
| revision.py (`detect_drift`, `_apply_live_revision`) | drift fingerprinting + drift-gated full-body PUT (R1.1 live-verified) | src/tgw/revision.py |
| C10 operator lane | operator-priority quota + origin propagation for repairs | worker_base |
| C11 invariant | violations persist on the item, queryable — not log lines | settled |
| QueueWorker + dedupe_key | idempotent, auditable repair execution | state_machine |
| s45 policy repair script | proven repair pattern (fresh GET → strip → mutate → PUT → read-back) | this session |

## Constraints (from settled architecture)

- All ItemData writes through the tgw-api fence; the broker's repairs are queue jobs
  and fence calls, never direct file writes.
- Workers are thin; repair logic lives in library code (revision.py / ebay/sync.py),
  the reconcile worker just routes.
- `{ok, ...}` contract on every surface; catalog rebuild stays a job.
- Operator gate is the design (memory): the broker's default posture is
  DETECT → SURFACE; auto-repair only from an explicit whitelist Dave approves.
- Zero-API-cost detection by default (P7 principle): mirror + capture based; fresh
  eBay reads only for flagged items or explicit `--live` runs.
- C11: every violation persists on the item (`spec_violations` block), so the
  console and catalog-verify can query it.

## Proposed approach

Three functions, one loop. Cardinal rule (s45 four-item forensics): **the
broker validates against TRUTH planes — disk, config, fresh live reads —
never against the plan (draft)**. The plan is the thing that drifts; a
validator that uses the plan as its denominator certifies the drift as
correct (P1's "1/1 photos OK" on an 8-photo item is the canonical case).

**1. SPEC — one declarative rule table** (new `src/tgw/spec_rules.py`, rendered to
`reference/ITEM-SPEC.md` for humans). Each rule: id, scope (draft / offer / both /
disk), severity, comparator, and `repair` = one of `auto:<action>` |
`surface` | `none`. Seed rules, all earned by s45 evidence:

| rule | check | severity | repair |
|---|---|---|---|
| policy_matches_spec | offer.fulfillmentPolicyId == resolved config policy (FC4 or category/size override) | critical | auto:put_policy |
| price_present_and_converged | draft.price set AND == offer price | critical | surface (price is money — operator decides direction) |
| photos_converged | live photo count == DISK count (cap 24) — NEVER the draft's imageUrls: s45's 0125081 passed "1/1 OK" because P1's verify used the plan as denominator while 8 photos sat on disk | critical | auto:repush_photos (C10 lane, proven P4 path) |
| status_advanced | published offer ⇒ local status reflects it | warning | auto:set_status (fence) |
| category_sane | category set + passes CATPICK shortlist sanity | warning | surface (+ capture operator correction as CATPICK training signal) |
| aspects_converged | draft item_specifics ⊆ live aspects | warning | surface |
| title/description_converged | draft == live | warning | surface |
| payment/return_policy_matches | == config | warning | auto:put_policy |

**2. DETECT — extend catalog-verify, don't build a scanner.** `_verify_item` gains a
`drift` rule family that compares `draft_listing` ⇄ `ebay_live.offer`/`inventory_item`
(mirror-based, zero API) and consults the R1.8 capture index (photo counts already
implemented; generalize the loader to also index offers/policies from capture pages).
Violations persist to the item's `spec_violations` block (C11) and flow into the
existing nightly report + ops-digest untouched. Post-publish hook: `ebay_publish`
success enqueues a single-item verify — divergence is caught in minutes, not at 2am.

**3. REPAIR — one thin `reconcile` queue worker.** Consumes violations whose rule
says `auto:*`; each action is a small library function reusing proven paths
(`_apply_live_revision` extended `_SUPPORTED_FIELDS` += listingPolicies fields;
photo repush via the C10 operator-lane path; status via fence PATCH). dedupe_key =
`sku:rule` (one open repair per rule per item). Every repair records what/why/
before/after into the item's history (archive-before-after memory). Everything not
whitelisted lands in the **action console** as a "needs repair" state with a
one-click approve (state drives interface — PP-ACTIONCONSOLE principle, target
99%-approvable / seconds-fast per operator-gate memory).

**Backlog drain:** the same machinery in bulk = run detect over the fleet (needs the
~2k `getOffer` sweep for full offer mirrors, already pending Dave's go), then feed
the violation list through reconcile with operator approval per rule-class. This is
the "items in states caused by pipeline errors" cleanup — not a separate tool.

**Sequencing gate:** #1152 (stage sends policy explicitly) lands FIRST — fix the tap
before mopping. Broker packets then can't be repairing a live leak.

## Files to change

| File | Change |
|------|--------|
| `src/tgw/spec_rules.py` | NEW — declarative rule table (spec of record) |
| `src/tgw/api.py` | `_verify_item`: drift rule family; generalize capture index loader; persist `spec_violations` via fence |
| `src/tgw/revision.py` | `_SUPPORTED_FIELDS` += listingPolicies fields; expose policy/status repair helpers |
| `src/tgw/workers/reconcile.py` | NEW thin worker: violation → whitelisted action → record |
| `src/tgw/queue/…` + systemd | register `reconcile` queue/unit |
| `src/tgw/workers/ebay_publish.py` | on success: enqueue single-item verify + advance status |
| `src/tgw/http_server.py` | console: "needs repair" state + approve endpoint (C10 origin) |
| `docs/TGW-Plan-Vault/reference/ITEM-SPEC.md` | NEW — human-readable spec table |
| tests | rule table unit tests; detect-on-seeded-defect; repair round-trip mocks |

## Packets (file as todos on Dave's go)

- **B0 (Dave decision, 20min):** approve the rule table + the auto-repair whitelist
  boundary (recommended whitelist: policy, photos, status — deterministic, spec-
  defined, reversible; price/category/aspects surface-only).
- **B1 (S):** spec_rules.py + drift rule family in `_verify_item` + `spec_violations`
  persistence. Live acceptance: seeded-defect test item flags all violations.
- **B2 (S):** post-publish verify hook + console "needs repair" state + approve flow.
- **B3 (S):** reconcile worker + policy/status auto-repairs. Live acceptance: break a
  safe test item's policy → broker repairs it unattended, records before/after.
- **B4 (S):** photo repush action + price-converge surface flow via revision path.
- **B5 (M, after fleet sweep):** backlog drain — fleet detect + operator-approved
  bulk reconcile. Depends: ~2k getOffer sweep (Dave's go), #1152 landed.

## Acceptance criteria

- [ ] A deliberately broken safe test item (wrong policy, missing photo, stale price)
      is fully flagged within one verify pass — all three violations persisted on the
      item and visible in the console
- [ ] Whitelisted violations repair unattended: policy PUT read-back-verified, photo
      count converges live, status advances — with before/after recorded on the item
- [ ] Non-whitelisted violation (price) appears as console "needs repair" and a
      one-click approve converges it through the drift-gated revision path
- [ ] Nightly digest on a clean fleet reports zero unexplained drift
- [ ] The s45 defect set (phantom price, PS policy, 1/8 photos, stale status), if
      replayed, is caught by rules — regression test encodes each

## Open questions

- B0 whitelist boundary — agree policy/photos/status auto, price/category/aspects
  surface-only? (recommended above)
- Post-publish verify: also re-verify on every operator "Update Listing" push, or
  nightly + post-publish only?
- Trading-API legacy listings: broker scope now, or explicitly deferred until the
  Motors/legacy track (PP-EBAY-MOTORS-001) settles? (recommend: defer, detect-only)
- Where should `spec_violations` live in the item JSON — new top-level block vs
  inside `ebay_listing`? (recommend top-level; schema doc update either way)
