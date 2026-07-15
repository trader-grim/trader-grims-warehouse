---
title: TGW Master Plan
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
---

# TGW Master Plan

**Redrawn 2026-07-02 (session 42)** around the data axiom, at Dave's direction, replacing
the 6,000-line verb-organized plan. The complete pre-redraw plan is preserved at
`archive/TGW-Master-Plan-2026-07-02-preredraw.md`; every PP design's full text lives in
`pp/<PP-REF>.md` or `archive/sections/` (byte-exact split, nothing discarded).

## How to read this file

Load order for any session: `CLAUDE.md` (prime directives) → this file → the reference
doc(s) named by your work packet — and nothing else. This file stays ≤500 lines; full
designs live in `pp/`, history in `archive/`. If a section here grows past a screen,
it moves out and leaves a pointer.

- **Tracker beats plan**: `tgw todo` is the canonical work queue. This file is the
  reference spec — architecture, status, and the map of what exists.
- **`tgw ops-digest`** every morning before building anything.
- Work is issued as **packets** (format below), one packet = one todo = one session.

---

## The axiom (why this plan is shaped this way)

> **eBay is a rented window. The local dataset IS the business.**

TGW is a dataset — acquired from eBay, derived by AI, accumulated through operations —
with scripts around it. Every feature exists to grow, refine, or act on the dataset;
every packet states what it adds to the dataset, and a feature that touches external
data while growing nothing is a red flag to raise with Dave.

**The operator gate is the design, permanently** (Dave, s42): AI output is a proposal;
Dave inspects and approves everything that goes live (invariant C9 — policy, not a
stopgap). The quality target is 99% approvable-as-is with seconds-fast approval — the
same ceiling an expert human hits with real sold comps. Optimize the approval surface,
never the removal of the gate. Friction in approval ranks equal to a wrong proposal. Full charter with asset
inventory and rules: `reference/TGW-Data-Charter.md`. This axiom was implicit for the
project's first month and was violated for three weeks (API responses discarded after
field-extraction); it is now Prime Directive 1 and enforced in code (invariant E7).

## The dataset — assets and their state (2026-07-02)

| Asset | State | Gap / next |
|-------|-------|------------|
| ItemData (~55k SKUs, canonical) | Live, fenced via tgw-api | E5 archive-before-delete still unenforced in code (todo #1104) |
| **eBayCapture** (raw responses) | **Live since 2026-07-02** — every REST/Trading/EPS response captured at the client fence (E7) | Backfill: full snapshot of 19,486 live items awaits Dave's go (R1.8) |
| eBay mirror fields in item JSON | 2,089 published items have offer_id/listing_id/price; sync propagates | PP-EBAY-SNAPSHOT-001 Phase 4 open |
| Taxonomy assets | Category tree + **all 15,105 categories' aspects** cached permanently (bulk pool); manual refresh only | — |
| AI derivations | ai_identify raw scans persisted; vision aspect-fill live (s41) | PP-DERIVED-001 full-capture design in `pp/` |
| Price/revision history | Append-only in item JSON; reducer persistence bug fixed s41 | — |
| Work ledger (Postgres) | Live; **not re-derivable** | **A1 daily dump running since 2026-06-20 (stale note fixed 2026-07-04)** — moved onto a genuinely separate physical drive (`/opt/TGW/mnt/tgw-db-backup`, was silently on the same nvme filesystem); A2 (off-site GDrive sync) mid-first-sync, not broken — see `PLAN-backup-dr.md` |
| Photos | ItemData + GDrive sync (PP-PHOTO-001 infra live) | EPS zero-bandwidth upload (Phase B) open |
| Catalogs/indexes | Derived tier — legitimately disposable | — |

## Quota (the wall around the window)

Every metered call goes through `tgw.quota` (PP-QUOTA-001, live 2026-07-02): per-pool
daily counters (PST reset), background halt at 70%, 30-min post-429 stand-down, 429s
are logged incidents with caller identity; workers requeue on quota walls instead of
dead-lettering. Real limits (probed live): Taxonomy 5k/day, Taxonomy-bulk 100/day,
Inventory 2M/day, Trading 5k/day, EPS 5k/day. Never add an eBay call outside the
choke points (`apis/ebay/client.py`, `trading.py`, `ebay/upload.py`).

**Operator lane (invariant C10, live s43):** jobs carrying `origin='operator'`
(stamped by every operator surface, propagated worker-to-worker) run in the
interactive quota context — counted, never background-halted. The 30% reserve
(1,500 EPS/day) belongs to Dave's button presses; background debris cannot starve
it. Two rules for new code: every operator-surface enqueue stamps the origin
(detector: #1118), and worker quota-context names keep the `worker:` prefix or the
PATCH auto-redraft guard mistakes fence writes for human edits (this resurrected the
s42 redraft loop for two cycles on 2026-07-03 — see invariants.md C10).

---

## Settled architecture (do not relitigate)

- **tgw-api is the fence** — all ItemData reads/writes go through it; workers are thin
- **One folder per SKU** — `ItemData/<SKU>/<SKU>.json` + media; SKU = `tgwYYYYMMDDHHMMSSmmm`
- **PostgreSQL `state_machine` is the work ledger**; workers use `QueueWorker` base;
  dead_letter never auto-retries (except quota/429 → transient requeue, s42)
- **Output contract** — every CLI/API call returns `{ok, ...}`
- **Secrets from `secrets_root`** only; eBay OAuth scopes are LOCKED (never add)
- **Catalog rebuild is always a job**; catalogs are derived/disposable
- **Raw capture at the fence** (E7) — preservation is not a worker responsibility
- **Timestamps stored tz-aware UTC; local only at render** (E6); eBay reset logic uses
  explicit `ZoneInfo("America/Los_Angeles")`
- Full service map: `reference/TGW-Architecture-Services.md`; invariants: `reference/invariants.md`

---

## Current state (honest, 2026-07-02)

**What Dave can do today:** browse/edit items in the web UI with instant category
search and full aspects for any category (zero live Taxonomy calls); action console +
listing editor built but **not yet operator-verified** — that is the current gate.

**Running:** 14+ workers, tgw-http, thermal watchdog, itemdata-sync. eBay: 19,486
live items. `ebay_sku_migrate` COMPLETE (s35).

**Known red (tracked):** no backup timers (PP-BACKUP-001, operator todos); test suite
repaired 2026-07-04 (todo #1102 DONE) — 1,761 pass / 1 skipped / 0 fail / 0 errors,
up from 1,513/12/236; root cause was `test_http_server.py`'s fixtures never updated
for the s42/43 cookie-login wall (`_web_key` renamed `_web_password`, `/form/*` now
needs a session cookie) plus a handful of unrelated drifts (`test_fence.py` same
fixture rename; `test_ebay_publish_price_drift.py` missing a mock for the s42
ordering-guard DB call; `test_config_hygiene.py`/`test_freeship.py` leaking into the
real `/opt/TGW/secrets` path for non-tgw test runners). **2,582 of 3,239 `ebay_draft`
dead-letters root-caused 2026-07-04**: OpenRouter "402 Payment Required" on
2026-07-02 (billing gap, since resolved — Dave confirmed credits available,
little use since) — not a logic bug; `ebay_draft`'s primary provider is
`google_direct` (free tier), OpenRouter only touched on a Google failure.
Deliberately not auto-retried (payment errors aren't in
`worker_base._TRANSIENT_ERRORS` by design — auto-retrying a billing failure
would hide a real problem). Half (1,291) bulk-requeued 2026-07-04 via
`scripts/requeue_ebay_draft_402_dead_letters.py --apply --limit 1291`
(quota headroom held back deliberately for today's troubleshooting); the
other 1,291 + the ~657 non-402 dead-letters remain queued for a follow-up
run. Zero requeue errors, worker active, 0 incidents at requeue time. Todo

- DONE-1054: item detail History link via sku_old — live, 39,485 records indexed; test suite 1790 pass / 1 skip (was 1786).

- #1049 get-ebay-token --print-url CLI half completed (d8a961c). fish wrapper deferred under PP-NIXOS-001 freeze.

Recoll index Phase 0 built: 441K docs, 4.6 GB per #1066. Follow-up: nightly timer + CLI wrapper pending.

- #1146 a1131 NFS shares + claude account (LIVE 2026-07-04) — full setup doc in reference/a1131-nfs-setup.md

- #1145 PP-UIPIPE-001 defect audit: two tool fixes LIVE, 402 pile drained, fleet photo sweep done (#1154 — 206 bad/149 SKUs). Broker PLANNED (ai-plans/reconciliation-broker.md). Next: Dave B0 sign-off, defect walkthrough, price test, fleet getOffer sweep.

- [#1174] eBay webhook signature fail-open security fix shipped (see `dev-workflow/research/DONE-1174-webhook-fail-open.md`)

- Todo #1245 completed: 3 confirmed fixes applied, 4 plausible findings deferred per Dave's instruction (to process at end of process).

- #1248 ebay_legacy_sync stopped due to 6-minute retrigger eating trading quota; root cause unknown, sold detection paused (blocked on #16 webhook endpoint).
#1077 orphaned offer forces ebay_sync per-SKU fallback (Dave → eBay
support); 15 Syncthing conflict files in vault; nats health check red
(module absent — decide: install or drop check).

**s43 update (2026-07-03):** EPS-exhaustion root causes found and the standing parts
fixed — retry_wait backlog (2,715 jobs) cancelled; invariant **C10 operator lane
live** (operator actions can no longer be starved by background debris); upload
worker's partial-success masking still open as PP-PHOTOSYNC-001 P1 (#1115); 492
published items measured photo-short (P4 repair, ramp pre-authorized). s41–s43 work
committed+pushed: `ae9b1e6` on `catio-nix-0.0.1-alpha`.

---

C10 invariant live + verified (2026-07-03). See SESSION-043.
## Active tracks

Retarget rationale and full packet specs: `RETARGET-2026-07-02.md`. R0 (quota
independence) completed 2026-07-02. Order of work: **R1 until it drains**, R2/R3
packets fill gaps between R1 gates. WIP limit: one packet in flight per track.

**Operating mode (Dave, s42): ONE ITEM AT A TIME — AND IT EITHER WORKS OR IT
DOESN'T.** No bulk operations of any kind until the single-item intake→live path
passes Dave's verification. This is a pass/fail test at n=1, not debugging by
attrition: a defect found on item 1 is fixed at the CLASS level, and item 2 must
then be boring. If item 2 isn't boring, the fix was a patch, and that failure
belongs to the fixing, not the pipeline's temperament. It should not take 20
items. Volume is earned, not scheduled.

### R1 — Prove the critical path (intake → live listing → sync)

Goal: graduate what is already built to *operator-verified in production*.

| # | Packet | Gate |
|---|--------|------|
| R1.1 | Live-fire PP-LISTEDITOR-001 Phase 2: one live item, price-only delta, verify on eBay + revision_history | Dave picks item |
| R1.2 | Dave operator-tests PP-ACTIONCONSOLE-001 on 3 real items; each friction point becomes a packet | Dave |
| R1.3 | **PARTIAL 2026-07-04**: root-caused (OpenRouter 402 billing gap, resolved), 1,291/2,582 requeued with Dave's go — remainder + non-402 dead-letters follow-up | in progress |
| R1.4 | Post-reset: confirm Taxonomy-429 jobs self-clear via new transient-requeue path; confirm Trading reset for ebay_legacy_sync | after 00:00 PST |
| R1.5 | Confirm/correct `tgw202605051933258` live price (reducer-bug victim) | Dave |
| R1.6 | **One true end-to-end**: Dave intakes ONE physical item to live entirely through the UI; every terminal-escape becomes a packet | after R1.1–R1.3 |
| R1.7 | Bulk-queue 2026-vintage unlisted backlog through vision pipeline; batch review | after R1.6 |
| R1.8 | **Dataset backfill**: full snapshot of all live items' inventory+offer records through the capture layer (~40k calls, 2M pool, budgeter-supervised) | **GO (Dave 2026-07-03)** — todo #1122, packet `packets/1122-r18-dataset-snapshot.md` |

### R2 — The system tells Dave when it's sick

Done: `tgw ops-digest` CLI; quota health check; 429 incident log; quota/429 transient
requeue. **Lesson encoded s43: "quiet" must mean no pending liability, not no current
activity** — a queue-depth check saw silence while 2,715 retry_wait jobs sat armed to
burn the day's EPS budget at reset (3 mornings running). Detector packet: **#1117**
(retry_wait liability + next-reset exposure in digest, under PP-PHOTOSYNC-001). Other
open packets: **R2.2** digest rendered on web UI home (stale-red >24h);
**R2.3** push-on-red via KDE Connect/existing channel; **R2.4** restart-counter flags
already in digest — add threshold alerting; **R2.5** PP-BACKUP-001 recut into
≤15-minute admin packets (todos #61/#146/#147/#1050–#1052); **#1103** dataset-growth
lines in digest; **#1102** test-suite repair; thermal PreToolUse hook (blocked on
Dave authorizing agent-config change).

R2: #1181/#1202 exception propagation fix complete — quota exhaustion now correctly requeues from ai_identify and ebay_draft (full doc in dev-workflow/research/DONE-1181-1202-review-followups.md)
### R3 — Plan and process hygiene

Done: this redraw; work-packet protocol below; PRIME DIRECTIVES in CLAUDE.md; Data
Charter; rolling handoff rewrite. Open: keep handoff ≤150 lines; vault conflict-file
cleanup (15 files); drain `inbox/queued/`; tracker prune with Dave (open todos >30d
untouched → packet, FUTURE-IDEAS, or delete); fix `tgw restart-workers` — the
`ebay_dole` module exists (`src/tgw/workers/ebay_dole.py`, in `WORKER_QUEUES`)
but **no systemd unit was ever installed** for it (corrected 2026-07-12,
Fable independent review #1338: "nonexistent" was wrong); a bare
`tgw restart-workers` would run `systemctl restart` against the unbuilt
template unit and could start it unintentionally — latent hazard, not just
a wording nit, see PP-BULKLIST-001/#1113; CLAUDE.md `tgw todo --add` syntax fix.

---

P1 upload integrity fix complete — see document for spec, implementation, tests, and live verification.

P9 bulk audit: Inventory API getInventoryItems winner (~98 calls), Feed API blocked, GetMyeBaySelling narrower than assumed. Full ranking in dev-workflow/research/RESEARCH-photosync-bulk-audit.md. Follow-up #1127 filed.

P2 ops-digest pending-liability lines shipped (see DONE-photosync-p2-digest-liability.md in dev-workflow/research).

- P9 follow-up #127: `photos_short_on_ebay` re-pointed at live capture index; open question on recurring nightly capture flagged for 2pm triage.

- #1145 console error-state fix + broker B1a/B1b built, fleet baseline done — see dev-workflow/research/RESEARCH-1145-console-error-state-broker.md
## Work-packet protocol

One packet = one todo = one model session. Non-trivial packets get
`plan/packets/<todo-id>-<slug>.md`:

```
# Packet: <observable outcome>
Todo: #NNNN   PP: PP-XXX-001   Track: R1.x
## Context budget (ALL the model may load)
plan core + this packet + <specific reference docs> + <specific code paths>
## Spec        — explicit on cadence/TTL/limits/defaults; unstated ≠ delegated
## Dataset     — what this adds to the dataset (or why nothing, flagged)
## Out of scope — the adjacent things you'll be tempted to fix
## Acceptance (live) — the command/URL/SKU + what must be observed; tests necessary, never sufficient
## Quota/risk  — API calls added/removed
```

Sizing: spec >30 lines or >3 files → split. Deviations from spec are flagged, never
silent. "Code complete" is not a status; live-verified is.

---

## Pending projects — index

Tracker is canonical for tasks; this index maps each PP to its status and design home.
Full designs: `pp/<REF>.md` unless noted. On next touch of an archive-section design,
promote it to `pp/`.

### Open — active or gated

Hermes agent architecture research filed: mixture-of-expert-pairs pattern with Claude CLI + Aider + site-specific MCP server.

PP-FENCE-002 — "Don't climb the fence, use the gate" proposal — see document: dev-workflow/research/PP-FENCE-002-climb-the-fence-2026-07-10.md

## PP-CATIONIX-001 — CatioNIX structural kickoff ("catio, dev team, and Dave upgrade")
**PROMOTED from FUTURE-IDEAS 2026-07-11**, by Dave's direct decision, ahead of
its own originally-stated promotion criteria — see `pp/PP-CATIONIX-001.md`
for the full reasoning (not a silent contradiction). Umbrella for the
session's 6-concept advancement: Concept 1 (Hermes personas Tigwa/Leotha +
cat-herder harness — technical substrate is the already-thorough
**PP-AIOPS-001**, `plan/PP-AIOPS-001-cat-herding-platform.md`, not
re-derived), Concept 5 (spec/invariant-as-correctness doctrine, lands in
CLAUDE.md not here). Sequencing explicitly UNCHANGED: R1 critical path keeps
running as before; cats (AI workers) go into the catio one at a time; the
crypto-lock cage comes last. Full design: `pp/PP-CATIONIX-001.md`; persona
design: `pp/PP-HERMES-EA-001.md`.

**Buildout beginning 2026-07-14 — standing requirement: build portable,
independent of the separate/unresolved Nix question** (Dave: "our platform
is better off being portable in the long run"). Immediate consequence for
PP-AIOPS-001 Phase 5: bubblewrap vs. nspawn+Btrfs reconciliation now leans
portable-by-default. Full writeup in `pp/PP-CATIONIX-001.md`'s new
standing-requirement section; broader Nix-or-not question stays parked in
`FUTURE-IDEAS.md`, unaffected by this.

## PP-HERMES-EA-001 — Tigwa & Leotha personas (the "dev team" upgrade)
**New 2026-07-11.** Two personas on one Hermes instance — Tigwa
(business-facing executor, new direction for the stopped `pm_intake`
worker) and Leotha (Dave-facing translator, curates PP-KNOWLEDGE-001's
data long-term). **Both explicitly IN TRAINING** — Tigwa learns to operate
by using `tgw` itself, supervised, before any autonomous authority unlocks
(gated behind the crypto-lock, PP-CATIONIX-001). First concrete
apprenticeship task: justshoutit (PP-INTAKE-004). Execution/isolation
substrate is PP-AIOPS-001, not re-designed here. Full design:
`pp/PP-HERMES-EA-001.md`.

## PP-RUNNERCOMMS-001 — the runner-question channel — NEW 2026-07-14
**OPEN, needs a dedicated planning session — not decided.** Split out of
PP-HERMES-EA-001 same day (Dave: "seems we need an overall plan for that
piece") once the question of how a blocked runner gets a fast answer grew
into three real candidate options: todos (current), an in-process channel,
or asking Tigwa to relay to Dave. Concrete test case: todo #1286's
permission-gated restore. Converges with PP-CODEGRAPH-001's Z3 invariant
catalog as a plausible shared transport. Full design: `pp/PP-RUNNERCOMMS-001.md`;
tracked by todo #1390.

## PP-AIOPS-001 — structured AI/operational resilience platform ("cat herding and litterbox cleaning")
**Given its own heading 2026-07-11** — previously only a bare Frozen-list
mention despite being genuinely central substrate for PP-CATIONIX-001/
PP-HERMES-EA-001 above. Status/gating UNCHANGED by this — still the same
6-phase design (JetStream mutation-audit stream → queue-transition outbox →
anomaly detector → litterbox worker + MCP audit tools → Btrfs/nspawn AI
session isolation → rollback/observability), Phases 1-4 have no PP-NIXOS-001
dependency and could start on the current OS today, Phase 5 gates on
PP-NIXOS-001. **Not started** — its own "Open Questions for Dave Before
Phase 1 Starts" (NATS install method, audit retention, session-ID scoping,
litterbox autonomy level) remain unanswered; this heading only fixes
visibility, it does not authorize starting the build. Full design:
`plan/PP-AIOPS-001-cat-herding-platform.md`.

## PP-EBAY-MOTORS-001 — eBay Motors, now scoped (was URGENT/unscoped)
**Opened 2026-07-04, surfaced live during PP-PHOTOSYNC-001 P10; scoped same
day (todo #1129).** Dave: "we do not have ebay motors accounted for
anywhere... add that as an urgent planning request item." **Scoping pass
complete — this is small, not fleet-wide:** todo #1131's census (parsed
existing raw offer capture, zero live calls) found **202 EBAY_MOTORS SKUs
out of 19,448 marketplace-tagged** (~1%), **zero cross-marketplace
duplicates** in this snapshot. The `trading.py` SiteID hardcoding is also
smaller than feared — one central `trading_call()` function, not a
sprawling per-call-site problem. Recommended order: (1) backfill
`marketplace_id` onto the 202 known SKUs from existing census data — no
API calls needed, (2) add the schema field + wire `ebay_stage` population
going forward, (3) thread `site_id` through `trading_call()` once (1)
exists, (4) audit the 202 SKUs' category/fulfillment config, (5) re-run
the census periodically rather than trusting today's zero-duplicates
result forever. No code changed by the scoping pass — ready to slice into
ordinary todos whenever Dave prioritizes. Full writeup: `pp/PP-EBAY-MOTORS-001.md`.

- Scoping summary filed → reference/PP-EBAY-MOTORS-001-scoping-summary.md
## PP-CATALOG-INCR-001 — incremental catalog update (PROPOSAL, not yet built)
**Opened s43 (2026-07-03).** Dave's original design, recovered from an unprocessed
inbox transcript (`inbox/hermes-out-of-flake-portable-catalog-concept.md`) after he
flagged `catalog_rebuild`'s full 55,419-item disk scan on every single write as the
system's most intensive task (1,361 rebuilds/33h, ~57s each). Design: atomic per-item
SQLite upsert + conditional thumbnail regen at write time; the full 4-artifact
rebuild becomes a scheduled reconciliation timer, not a per-write trigger. Revises
the "Catalog rebuild is always a job" settled-architecture line — needs Dave's
explicit sign-off before implementation. Found while confirming the design: the
JetStream mutation-audit stream this needs (PP-AIOPS-001 Phase 1) already exists but
is wired to the wrong door (CLI path only, not the real HTTP fence). Full design +
itemization of what intersects PP-PHOTOSYNC-001 (P4's ramp, P8's daily canary — both
would multiply this same cost if built before this lands): `pp/PP-CATALOG-INCR-001.md`.
No todos filed yet — proposal stage.

## PP-PHOTOSYNC-001 — upload integrity + operator lane hardening + fleet photo repair
**Opened s43 (2026-07-03) — THE ACTIVE FIX TRACK.** Born from the 3-day EPS quota
exhaustion incident: upload worker masks partial failure as success; s42's retry
policy left a 2,514-SKU immortal backlog (cancelled, Dave-authorized); the operator
quota reserve was unreachable (fixed s43 as invariant C10, live-verified on
tgw202606021133367 — 9→24 live photos). Runs PARALLEL with the forward track
(R1.8 #1122 / PP-BACKUP-001 / #1102) — collision rule in the design doc.
Packets P1–P9 = todos **#1115 #1117 #1118 #1119 #1120 #1121 #1123 #1124 #1125**;
P4 fleet repair (492 photo-short items) ramp **pre-authorized 1→5→ramp by Dave
2026-07-03**. P7–P9 added at Dave's direction same day: truth-audit rules ("test
the function and read the log" as a nightly job — PD4 automated), canary probe
(real buttons on one item, daily), and whole-site audit via the cheapest bulk
source reachable with EXISTING scopes (Feed API report candidate; scopes LOCKED).
**P1 ✅ DONE 2026-07-03** — live-verified against the still-halted real EPS wall;
P4/P8 now unblocked; full suite regression-clean (1444 pass, same 9 pre-existing
failures as baseline).
Full design + packet contracts: `pp/PP-PHOTOSYNC-001.md`.
s42+s43 committed+pushed (`ae9b1e6`); PR to main deferred until P1 verifies.

P10 complete (legacy duplicate check + eBay Motors awareness). P4 paused per P10 findings. See RESEARCH-photosync-p10-legacy-duplicate.md.

P6 investigated: ebay_repush orphan queue diagnosed (2 orphan jobs, no systemd unit). Needs Dave decision: install unit or retire enqueue path. Full detail in `plan/pp/PP-PHOTOSYNC-001.md`.

- #1124 P8 canary probe completed: `scripts/photosync_canary_probe.py` built and live-verified against `tgw201501021970068`. Fixed two bugs (auth header, live-state field shape). 4 new tests, full suite 1814 passed. Daily timer deferred to 2pm.
## PP-LISTEDITOR-001 — listing editor + revision apply
**R1.1 live-fire DONE 2026-07-04 (todo #1137).** Price-only delta
(`tgw201501021970128`, $7.99→$8.49) via `revision.py`'s drift-gated apply
path (`tgw revise <sku> --set price=X --show` then `--apply --live`).
Live-verified in both directions with fresh uncached eBay API reads (not
just job-succeeded logs): real price changed on the actual listing, then
reverted; `revision_history` correctly recorded delta + baseline hash +
the exact API call made (`PUT offer/264095634018`), hash_match=true, zero
drift. **Gate cleared.** Real bug found along the way (todo #1138, minor):
the CLI's `--set` help text claims dotted-path support
(`draft_listing.price`) but the live-apply path only accepts bare field
names (`price`) — use bare names; dotted paths raise a clear "unsupported
delta field" error at apply time, not silently ignored. Next: wire the
Update-Item button to this same apply path. Design:
`archive/sections/Pending-projects-revisit.md` (promote on touch).

**Todo #1062 closed as satisfied, not built new (2026-07-04).** Its scope
("item detail page restructure + editable aspects") is already fully
covered by PP-ACTIONCONSOLE-001's s40 build — verified in code: Editor
tab + Live/Sold Listing tab, 3-layer live/proposed/edit aspect merge,
condition select, price history, reprice schedule. Consolidated into
#1085's "operator eyeball" gate instead of duplicating.

**Same-day fix, todo #1114 — auto-redraft-clobbers-operator-edit, DONE and
live-verified.** Investigated per Dave's request ("verify why we did it that
way before changing") rather than jumping straight to a fix. Root cause: the
HTTP PATCH auto-enqueue trigger (`patch_item()`) conflated two different
things under one condition — "a raw fact changed, regenerate the AI draft"
vs. "the operator polished the final draft content directly." In practice
only the second ever happens (the editor UI only ever PATCHes into
`draft_listing.*` — no code path sends bare top-level `title`/
`item_attributes` through this endpoint), so regenerating was never
correct: every operator edit to an already-live item's draft got silently
overwritten by a fresh AI regeneration before it was ever seen. Cost impact
(Dave's own estimate, confirmed): each needless regen burns 2 AI calls
(primary draft + `bulk_classify` aspect-fill) for zero benefit — a typical
2-3-edit polish session tripled the AI cost of a step that should cost
nothing. Fixed to mirror the existing "Update Listing" button exactly: push
(`ebay_stage`, `force=True`, `origin=operator`) instead of regenerate
(`ebay_draft`). Live-verified against a real published listing
(`tgw201501021970354`) all the way to a real eBay title change, confirmed
via a fresh uncached API read, then reverted. 3 new tests.

## PP-ACTIONCONSOLE-001 — state-driven item action console
Built s40 (state-driven action line, Editor/Live tabs). **Gate: Dave's operator test
R1.2.** Principle settled: state drives interface; controls are indicators;
platform-wide style. Todo #1085. Troubleshooting buttons removed with no new home yet
— ops surface to design.

## PP-EDITOR-001 — web UI (listing pipeline, editor, dashboard)
**Given its own heading 2026-07-11** — previously only a bare "Done" rollup
mention (31 todos) despite being the umbrella for the whole web UI. **Absorbs
PP-UIPIPE-001** (Dave, 2026-07-11: "the web ui pipeline ain't cutting it") —
real production defects surfaced through the web UI on live listings: wrong
shipping policy on new listings, items PUBLISHED with no price set (C1
invariant violation — guard bypassed or broken), incomplete photo upload
with no reattempt (relates to #1115/P1 completion-guard — verify deployed
coverage). Todo #1145: go over the full defect list with Dave, produce a
defect→root-cause→packet map. Next: Dave B0 sign-off, defect walkthrough,
price test, fleet getOffer sweep.

## PP-QUOTA-001 — metered-API budget layer (call-count budgets built s42; balance monitoring open)
`tgw.quota`, raw capture, ops digest, health check live for CALL-COUNT
budgets. **✅ removed 2026-07-11** — Dave found a real remaining gap: the
llm_google/llm_deepseek/llm_anthropic caps (300/500/100) are call-count
proxies, not actual dollar-balance tracking. No code anywhere queries real
provider account balance or warns proactively before it runs dry — the
provider's own hard cap is currently the ONLY safety net, and that should
be the fallback, not the primary signal. "Fine now only because the
pipeline is quiet" (Dave) — a real risk once volume returns. Todo #1337.
Remaining observability packets tracked under R2, not here.

## PP-BACKUP-001 — backup + DR
**Top operator risk: nothing running; work ledger not re-derivable.** Scripts+timers
exist in `etc/systemd/`. Operator todos #61/#146/#147; restore script #1052; DR
drills #1050/#1051. Plan: `PLAN-backup-dr.md`.

**2026-07-10 ALARM + fix (todo #1258):** `tgw health` reported db dump stale
124h (limit 26h) and rclone cloud-sync had never completed. Root cause:
`tgw-db-backup`'s script was moved 2026-07-04 to also write onto a dedicated
physical drive (`/dev/sdc1`, LABEL=`tgw-db-backup`, btrfs) mounted at
`/opt/TGW/mnt/tgw-db-backup` — but that mount was done by hand, never
declared in the NixOS flake, and the 2026-07-06 reboot silently dropped it.
Every nightly dump since failed with a bare `mkdir: Permission denied`
against the empty, root-owned mountpoint; `tgw-cloud-sync` failed
independently and separately (unrelated to the mount — its first-ever full
run had just never completed).

Immediate fix (done live, with Dave's sign-off): remounted `/dev/sdc1`,
ran `tgw-db-backup.service` to catch up the dump (confirmed via `tgw
health` — staleness cleared), kicked off `tgw-cloud-sync.service` (first
full run, long-running, left running in background).

**Durable fix — APPLIED and reboot-verified 2026-07-12** (corrected from
"NOT yet applied," Fable independent review #1338: the master plan's own
warning had gone stale — the fix was live-verified before this correction
landed). Changes shipped in `~/tgw-flake`:
- `nix/hosts/tgw-prod.nix` — new `fileSystems` entries (by-label, `nofail`)
  for all three `sdc` partitions: `tgw-db-backup`, `tgw-itemdata-snap`,
  `tgw-itemarchive` — the latter two were equally undeclared and at the
  same silent-unmount risk, just not yet symptomatic.
- `nix/tgw/backup.nix` — `tgw-db-backup.service` gets
  `unitConfig.RequiresMountsFor = "/opt/TGW/mnt/tgw-db-backup"` (same
  pattern `tgw-snapshot` already uses for its own mount) so a missing mount
  is a loud, correctly-attributed service failure instead of a confusing
  `mkdir` error.
- Validated: `nix flake check` clean for all 3 hosts; `/etc/fstab` confirmed
  containing all three `LABEL=...  nofail,x-systemd.device-timeout=5s`
  entries; **the 2026-07-11 11:11 reboot proved the fix live** — `/dev/sdc1`
  came back mounted at `/opt/TGW/mnt/tgw-db-backup` without manual
  intervention.
- Remaining open item: only the rclone rate-limit issue below (#1264) —
  the mount-durability risk itself is closed.

**Also fixed:** `tgw-restore.sh` bug; `TGW-VAULT-RESTORE.md` written
covering both restore paths, live-verified dry-run.

**Separate, newly-discovered issue (todo #1264):** the `tgw-cloud-sync.service`
run kicked off above did NOT succeed — it failed after 43 minutes with a
Google Drive API 403 `RATE_LIMIT_EXCEEDED` (`defaultPerMinutePerProject`,
840000/min), from listing the entire `/opt/TGW` tree in one burst on its
first-ever completed run. `tgw health`'s "backups" check is still WARN on
this. Distinct root cause from the mount issue above (which is genuinely
fixed) — needs rclone rate-limiting (`--tpslimit`/`--drive-pacer-min-sleep`)
or a chunked first sync, not a bare retry (the underlying cause is
unchanged, a retry now would likely hit the same wall).

## PP-COHESION-001 — full-codebase cohesion+correctness audit (2pm agenda, todo #1143)
**Given a real PP designation 2026-07-11** — was source-tagged only
(`audit#1143`, `audit#COHESION-2026-07`) despite being a real, recurring,
already-substantial body of work with its own section here. Now also
covers the 2026-07-07 follow-up cohesion pass (45 findings, todos
#1273-1317), not just the original #1143 batch — both batches share this
heading/PP going forward.

**Dave: "I want to right the ship... check the whole thing and make sure
each part and the whole are cohesive."** Prompted by discovering that a
full week of code (2026-06-24 through 2026-07-02, the `ae9b1e6` commit
and everything before it) never went through `/code-review`/ultrareview
— diffs had grown too large to review by the time anyone tried. Same-day
finding: an 8-angle review of just today's 47-file/3,800-insertion diff
(todo #1114 fix + drive-index work) found 7 real confirmed bugs, all
fixed same session — real signal that unreviewed accumulation is a
genuine regression source, not a hypothetical.

**Plan:** a `Workflow`-based audit, staged per-subsystem (workers/,
apis/ebay/, `http_server.py` on its own — it's grown into a multi-
thousand-line file, queue/state-machine, scripts/, the Nix flake) rather
than by git history — sidesteps the "one commit mixes noise and signal"
problem that blocked ultrareview entirely. Two passes per subsystem:
correctness-bug finding (same 8-angle method as today) plus a **cohesion
pass** checking cross-subsystem consistency (is "tgw-api is the fence"
actually honored everywhere, do invariants.md's rules hold everywhere
they claim to, are there now-drifted duplicate implementations across
files).

**Sizing (calibrated from today's real pass):** ~830K tokens for one
47-file diff-sized review. Full codebase ≈ 8-10 subsystem-sized chunks
+ a cohesion pass ≈ **~8-11M tokens total**, order-of-magnitude. Deliberately
NOT scoped to one session — each subsystem chunk is independent and
resumable (Workflow's run-caching), so this runs opportunistically
whenever usage allows (Dave: "having a project like this would be an
excellent use of that [bonus] usage"), picking up wherever a prior run
left off. Not started — gated on Dave's go-ahead at 2pm.

**Prevention going forward** (Dave: "I need to do the reviews more
regularly"): review each day's diff before it accumulates — plain
`/code-review` for a free/quick inline pass, `/code-review ultra` for a
periodic cloud pass while diffs are still small enough to clear its
size guard.

**Status (2026-07-10, refactored — the "Remaining subsystems" note below was
stale): the discovery phase is COMPLETE.** All 6 planned subsystem audits
have research docs — `workers/` (2026-07-05), `apis/ebay/`, `http_server.py`,
`queue/state-machine`, `scripts/`, and the nix flake ("FINAL SLICE",
confirmed in `RESEARCH-1143-nix-flake-audit.md`). What's left is executing
the findings each audit spun off, not more discovery.

Findings-execution status by subsystem (2026-07-10 check):
- `workers/` — DONE. #1162-#1170 (9 correctness bugs) fixed earlier; #1171
  (8 batched cohesion findings) fixed 2026-07-10 (path-construction cleanup,
  itemdata_scrub.py root/sku validation hardening, photo_history_recovery.py
  catalog-refresh trigger, shared `_format_ebay_error`, ebay_sku_migrate.py
  write-pattern documented in invariants.md A5). One follow-up deferred as
  todo #1261 (itemdata_scrub.py's ad-hoc queue — bigger execution-model
  change, out of scope for a cohesion batch). **2026-07-10, re-examined and
  deliberately left deferred again (Dave: document for a future planning
  session rather than force a fix now)** — see below.

**PLANNING ITEM — itemdata_scrub.py queue migration (deferred 2x, needs a
real scoping pass):** `itemdata_scrub.py`'s `main()` uses a bare
`queue_dir = Path.cwd()` file-based queue (job = a file in the cwd; success
= the file gets deleted) instead of `state_machine`/`QueueWorker` like every
other worker — no visibility in `tgw queue-status`, no postgres-backed
retry/dead-letter semantics. **Checked live 2026-07-10: it isn't currently
scheduled anywhere** — no cron entry, no systemd timer, no reference in the
nix flake (`grep -rn itemdata_scrub ~/tgw-flake` → nothing). So the
practical impact of the visibility gap is zero today; nobody is missing
status on jobs that aren't flowing through it.

The real fix is a genuine migration, not a quick conversion: a new systemd
service + timer (or on-demand queue entry point), converting the dequeue
model from "file exists in cwd" to postgres rows, deciding how
`ScrubRules`/`--config` get supplied in that model (currently CLI args to a
one-shot batch run), and deciding whether this becomes a `tgw-worker@` unit
like everything else or stays a manual on-demand tool with better status
reporting bolted on. That's real design work — worth scoping properly in a
dedicated session rather than forcing a partial fix (e.g. just logging queue
depth somewhere `tgw health` can see, without fixing the underlying
file-vs-postgres model split) into a batched cleanup pass. Todo #1261
remains open, now explicitly framed as "needs a scoping pass," not "needs a
quick fix."
- `apis/ebay/` — DONE. #1182 fixed 2026-07-10 (conditions.py policy-cache
  memoization, trading.py 429-retry shared across all 3 Trading API
  generators).
- `http_server.py` — DONE. #1198 fixed 2026-07-10 (shared catalog_rebuild
  enqueue helper, sku traversal guards on 2 routes, store-category dropdown
  dead-code + fragile-fallback cleanup, deduped price formatter).
- `queue/state-machine` — findings executed in earlier sessions (see commit
  history around #1202, #1206 fixes); no open audit#1143 todos remain for
  this subsystem.
- `scripts/` — DONE. #1213 fixed 2026-07-10 (photo_repair_iss013.py
  ITEMDATA_ROOT now config-derived, matching sibling
  photosync_canary_probe.py). Todo #1203 is `done` — this section used to
  say "INPROGRESS," which was stale.
- **nix flake — 3 SECURITY findings remain open, not yet fixed** (sentence
  reunited 2026-07-12, Fable independent review #1338 — this list had been
  severed mid-clause by misfiled notes for over a week):
  - #1219 (NFS Queue export writable to the whole 192.168.60.0/24 subnet,
    should be host-locked like the ro exports below it) — **BLOCKED** on
    #1228 (no static IP/DHCP reservation exists yet for the intake
    camera/phone device; checked live ARP table 2026-07-10, several
    unidentified LAN hosts, none confirmable as the intake device from
    tgw-prod alone — needs Dave to identify the device + reserve its lease
    on the router).
  - #1217/#1218 (Syncthing GUI/second-instance bind exposure) — explicitly
    set to p95 by Dave 2026-07-07, deferred until dev settles (see
    `feedback-deprioritize-syncthing-auth` memory) — intentionally not
    being worked, not an oversight.

**Other audit#1143 fixes landed this stretch** (misfiled notes consolidated
2026-07-12): #1168 (ebay_publish condition fallback now writes corrected
condition back to draft_listing, tests added), #1171 (workers-audit cohesion
findings, see `DONE-1171`), #1173 (`lookup_epid` re-raises
`QuotaBudgetExceeded`), #1181 (`best_category()` fallback chain fixed),
#1182 findings #2/#3 (ebay conditions memoization + trading retry backoff,
[DONE-1182-ebay-cohesion-cache-retry.md](reference/DONE-1182-ebay-cohesion-cache-retry.md)),
#1206 (requeue-402 dedupe guard), #1235 (atomic-write sweep: 6 sites fixed,
8 new tests, 1861 passing — deviation: `itemdata_scrub.py` write stays
outside fence, PP-FENCE-001 gap documented). Session 48 (2026-07-06)
completed dead-letter/atomic-write/multi_intake fixes; code reviews
addressed all critical findings except 4 PLAUSIBLE deferred as todo #1246;
PR #8 not yet merged.
## PP-HARDWARE-001 — IT / hardware track (drive-space re-evaluation absorbed) — NEW 2026-07-11
**Dave, triaging #1136: "it and #1136 and similar need an IT or hardware
PP."** Previously PP-HARDWARE-001 was only referenced by name from other
docs (GPU upgrade), never had its own heading. Governing philosophy:
"we get it running, we make money, we get server. We no make money we use
this thing" — bootstrap hardware until revenue justifies real
infrastructure. Near-term concrete plan (Dave's own words): M.2-to-SATA
adapter to bring a 1TB USB SSD onto the board replacing an HDD; a 4-bay
SSD enclosure + 4 spare SSDs for a real storage tier; heat sinks on the
SSDs. **Open, unresolved, flagged for a dedicated pass:** where should
knowledge-hub work (PP-KNOWLEDGE-001) physically live so it doesn't fill
`/opt/TGW` — the existing tiered-remote design (PP-ANNEX-001, the
power-tiered drive inventory below) points away from the NVMe but this
hasn't been explicitly confirmed for this specific question; and Dave's
own ask for "a real analysis of what we need, what we want, what we will
need" — not done, this PP is the placeholder for it, not a substitute.
Full design: `pp/PP-HARDWARE-001.md`.

**Dave: "put revaluation item into plan for drive space."** Todo #1056
(extend `vg_tgw` into HDD space) turned out blocked on a stale premise:
checked live `lsblk`/`pvs` — sdb no longer appears in the disk list at
all, and sdc (the other candidate) was fully repartitioned and put into
active service the same session for backup infra (`sdc1`=tgw-db-backup,
`sdc2`=tgw-itemdata-snap, `sdc3`=tgw-itemarchive). No free/unclaimed disk
currently exists to grow `vg_tgw` into (PV `nvme0n1p2` has 96MB free), and
`reference/DRIVE-REGISTRY.md` itself is stale against today's real layout
(doesn't reflect sdc's repartition, `TGW-VAULT`, or several other drives
now in service). **Needed:** a full physical-disk-fleet audit + registry
refresh, then a fresh decision on where `vg_tgw`/nix growth room comes
from — new hardware, or an explicit repurpose of something already in
service. Not started; the original LVM-expansion plan (sdb/sdc as
candidate PVs) is superseded by this finding.

**Real current pressure (checked 2026-07-04):** not `/nix` (52% used, 33G
free, fine) — `/opt/TGW` (nvme, ItemData/ItemCatalog/incoming) is at
**83% used, only 48G free**, and `ItemData` alone is already 180G for 55K
items. Dave: "I have half a million items here ready to process" once
the pipeline is fixed — heading toward that ~9x scale, this is the
partition that will actually run out first.

**Power constraint (Dave, 2026-07-04):** generator-powered — prefer
drives that can come offline when not needed. Real drive inventory
mapped (`lsblk` + `TRAN`/model): `nvme0n1` (internal NVMe) + `sda`
(internal SATA HDD) can't be unplugged but draw modest power; `sdc`
(700G) + `sdi` (465G, currently idle) are 2.5" USB laptop drives —
bus-powered, no external brick, the reliable always-on tier; `sdd`
(MasterArchive, 1.8T) + `sdh` (tgw-backup, 931G) are 3.5" drives in a
powered dock — connect only when actively syncing, matches the existing
PP-BACKUP-001 A7 "rotating offline drive tier" design exactly, just
applied for power reasons too, not only DR rotation. Planned upgrade:
a 4-bay USB3 NVMe dock (bus-powered, low-heat) — Dave has the SSDs
already, multi-terabyte capacity once built, likely retires the need to
keep `sdd`/`sdh` connected as often.

**Merged with PP-DRIVE-INDEX-001** (see below) — recoll-driven dedup
across the already-mounted data is the near-term space-recovery lever,
before deciding what (if anything) to offload onto `sdi`.

Audited sdb/sdc live: sdb absent, sdc repartitioned into backup services. No free disk to grow vg_tgw. Closing #1056 as superseded, opened #1136 for re-evaluation.
## PP-KNOWLEDGE-001 — the knowledge & translation hub — 6-LAYER UMBRELLA, extended 2026-07-11
**Corrected from "5-LAYER" 2026-07-12 (Fable independent review #1338) — the
Graph/Graphify row was added this session without updating the count.**
**PLANNED s45 (2026-07-04), extended this session into the full 5-layer
umbrella (Concept 2).** Leotha (PP-HERMES-EA-001) curates/organizes the data
long-term; this plan is the architecture only.

**Absorbed 2026-07-11 (Dave):** PP-DOCLIB-001 and PP-HISTORY-001 fold in
here — "it's recoll+mcp on the knowledgebase." Both were existing facilities
(document cross-referencing, `tgw history-index`) that sit ON TOP of this
hub's Search (Recoll) and MCP (agent front door) layers, not separate PPs.
No standalone design docs existed for either — folded as todos, not
promoted to their own `pp/` files.

| Layer | Tool | Answers | Status |
|---|---|---|---|
| Storage | git-annex (canonical files, dedupe) | — | **PP-ANNEX-001, promoted 2026-07-11 — see below** |
| Search | Recoll (full-text/metadata) | "where is the evidence?" | **PP-SEARCH-001, LIVE** at `/opt/TGW/.recoll/` (441K docs) |
| Core spine | **PostgreSQL LISTEN/NOTIFY** (event bus) | pays off broadly: charting/forecasting, photo-set production-time analytics, the event server (PP-EVENTD-001), research feeding AI workers | **RESOLVED 2026-07-11** — not NATS JetStream, see note below |
| Memory | Hindsight (timelines/experiences) | "what happened before?" | exploratory — prebuilt layered ON the core spine, not committed |
| Knowledge | gbrain (curated "working truth") | "what do we believe now?" | exploratory — same, not committed |
| Graph | Graphify (code + doc relationships) | "what connects to this?" | **detailed design merged in from PP-CODEGRAPH-001, 2026-07-14** — 4-layer stack (Tree-sitter/FalkorDB code graph, Postgres+Z3 invariant catalog, DuckDB execution-trace store, unified MCP layer), hosted on a1131, see PP-CODEGRAPH-001 section below for full design; awaiting Dave's research before build |

**Core-spine NATS-vs-Postgres note (2026-07-11, do not conflate with
PP-AIOPS-001's separate JetStream use):** PostgreSQL LISTEN/NOTIFY wins for
this general operational event bus (clip-route/knowledge-hub/UI routing) —
`FUTURE-IDEAS.md`'s old NATS mention here is superseded. **This is a
DIFFERENT question from PP-AIOPS-001's JetStream audit/CDC stream**, which
is still the intended mechanism for durable, replayable mutation-history
logging (Dave, 2026-07-11: "we want the transactional logging") — that use
of NATS is NOT superseded, the currently-failing `nats` health check
("No module named 'nats'") is a real gap for PP-AIOPS-001 Phase 1 whenever
picked up, not a moot pre-existing nuisance.

**Full plan + soundness review (4 system-specific guards, reject list):**
`docs/ai-plans/recoll-annex-jetstream.md` — treat as the design doc of
record for the storage/search/annex legs (Graphify/Hindsight/gbrain are new
this session, not yet in that doc).

**Dave's stage 1 (s45): "organize and make accessible all of our valuable
data," as a concerted parallel lane alongside the fix/execution tracks** —
the knowledge dataset becomes a better discovery search than the catalog
(catalog stays the structured/UI projection; recoll is the find-anything
layer). recoll already paid for itself in week one (real recovery/audit
queries, s44/s45).
Stage-1 packets: #1147 (R2 search surface — priority), #1148 (R1 field
mapping), #1149 (A0 Syncthing/annex boundary decision, Dave 15min), #1150
(A1 annex pilot on archive corpus). Drive-fleet manifests continue under
PP-DRIVE-INDEX (#1136).

**Starting point for Tigwa's knowledgebase work, decided 2026-07-14 (Dave):**
"my first research started with graphify, but I found the better solution."
The Graph/Graphify layer (FalkorDB/Z3/DuckDB/MCP, PP-CODEGRAPH-001 section
below) was Dave's original research target, but he's since decided
git-annex + Recoll (Storage + Search, A0/A1 above — A0's boundary decision
is already set, A1 is unblocked) is the better starting point for Tigwa to
actually begin on and for Dave to get familiar with the tools hands-on.
Event fabric (Track E / "JetStream", see `recoll-annex-jetstream.md`) is
explicitly deferred — not part of this starting point. Graph/Graphify
still needs its own planning pass (5 open packaging questions, see
PP-CODEGRAPH-001 section) before Tigwa or anyone builds it — not skipped,
just sequenced after git-annex/Recoll.

**Target use cases, same decision (Dave, 2026-07-14):** PP-DATAINTEGRITY-001
(see its own master-plan section) is what Tigwa targets with this — not
a generic "index everything" exercise. Concrete starting scope: the
photo-integrity design's open legs 2/3, and the `status`/`#STATUS`
write-path reconciliation once scoped. Grounds the buildout in real,
already-identified reconciliation work instead of an abstract capability.

**This is infrastructure, not an iterated/churny tool (Dave, 2026-07-14):**
"I want this to be an infrastructure piece. When it is mature and we have
better hardware they may live side by side." a1131 hosts it for now (good
workspace, thermal-relief compute, no production traffic), but unlike
Hermes/Aider (deliberately kept in userspace — PP-NIXOS-001's standing
rule, see `decouple-hermes-aider-flake.md`) the knowledgebase stack is
meant to mature into real settled infrastructure that could eventually run
alongside tgw-prod's production stack on better hardware. Practical
consequence: package it **declaratively in a1131's flake** (`git-annex`,
`recoll`), not via imperative `nix profile install` — directly applying
today's lesson from the Hermes incident (imperative per-user nix-profile
installs broke `hermes update` on two hosts because they're just as
immutable as a flake package but without any of the declarative tracking).
This also leans the still-open FalkorDB packaging question (Graph layer,
PP-CODEGRAPH-001) toward "NixOS service" over "userspace nix-profile" —
not decided yet, but the precedent points that way.

### PP-ANNEX-001 — the archiving/librarian layer — PROMOTED 2026-07-11
**"A librarian/archivist tool built into the library itself"** (Dave) —
git-annex doesn't manage the library from outside, it replaces the file
with a symlink and tracks location/metadata directly in the repo. Full
prior design moved from `FUTURE-IDEAS.md` into `docs/ai-plans/recoll-annex-jetstream.md`
(Track A, packets A0-A5); do not relitigate what's already settled there:
git-annex replaces Syncthing for data trees; LAN hosts (a1131) are plain ssh
git-annex remotes (wire-speed); Google Drive is the off-site/portable/backup
tier ONLY, never the LAN rendezvous; plan vault stays plain git, never
annex; scope = history/archive corpus consolidation ONLY, ItemData stays
fence-owned and untouched (A4 rescoped away from live-data migration);
`numcopies=2`; date-partitioned `gdrive-archive-YYYY`; Dave approves every
deletion (C9); A5 (Go companion tool) deferred until stock remotes proven.

**A3 cloud backend — SETTLED 2026-07-11: Google Drive** (not GCS/S3 — new
metered spend not justified below the $4k-server budget line, even though
git-annex's native `type=S3` would be the cleanest integration technically).
Current capacity: 2TB Google One @ $100/yr, upgrade path to 5TB @ +$140/yr.
**Adapter kept genuinely open, evaluate empirically**: rclone special remote
(already proven in production for PP-PHOTO-001 photo sync, zero new auth)
vs. native `git-annex-remote-googledrive` (Lykos153, direct API, needs its
own OAuth credential) vs. anything else found during the A2 pilot.

**"The archivist" reframe (Dave, 2026-07-11):** archiving stops being a
hardcoded library call (`items.atomic_write_json(..., archive_root=...)`
zipping inline) and becomes a delegated hand-off to one authoritative
service that owns the full chain — archive (zip, existing E5/#1104
mechanism) → log → index (Recoll) → place (git-annex → GDrive) — driven by
a filing policy Leotha curates over time. **Open design constraint, not yet
solved:** E5/#1104 is explicitly fail-closed (write must not proceed unless
archive succeeds) — delegating to an external service risks losing that
synchronous guarantee unless the hand-off blocks for ack or there's a
durable write-ahead step. Real design work, candidate for "model the
worker in Hermes first" (PP-HERMES-EA-001) before it touches the live
`items.py` write path.


## PP-DRIVE-INDEX-001 — drive survey, dedup, universal index (merged 2026-07-04)
**Pre-existing plan (session 40, 2026-07-01) found and merged into the
live drive-space conversation, per Dave's direction.** Long-horizon
project: catalog, deduplicate, and index everything across Dave's ~11
external drives + Google Drive (Track A = TGW business data, Track B =
personal), full design in `plan/PP-DRIVE-INDEX-plan.md`.

**Phase 4's Track A recoll piece landed today, ahead of the plan's own
sequencing** — todo #1066 (PP-SEARCH-001 Phase 0) built the exact recoll
index this plan calls for, independently, before knowing this plan
existed. 441,374 docs indexed (`ItemArchive`, `ItemCatalog`, plan vault),
live-verified real recovery queries. The rest of this plan (drive-survey
tooling, per-drive manifests/SMART checks, cross-drive dedup report,
Track B, Google Drive inventory) is still fully open — no external
drives have been surveyed yet.

**Why it matters right now:** this plan's own Phase 1.2 (cross-drive
dedup report) is exactly the space-recovery mechanism Dave described for
`/opt/TGW`'s 83%-full pressure above — years of ItemData/ItemArchive
history plausibly has real duplicate files recoverable via a dedup scan
(fclones/rmlint/sha256 fingerprinting, per Phase 0.1's own tooling list),
achievable against what's already mounted, no new drives needed first.

## PP-DEADLETTER-001 — pipeline dead-letter root-cause triage — NEW 2026-07-14
Surfaced monitoring #1265's bulk requeue (only covers the 402 pattern,
2658 of ebay_draft's 2771 dead-letters). Dave: "those are our known edge
cases, let's get them covered. Let's run it through the process" — same
PP-COHESION-001 discipline (packets before dispatch, tgw-coder,
tgw-runner-review). ~350 dead-letters across 8 queues triaged into
transient-only (safe requeue, no fix) vs. real bug findings (own
packet+todo each). Full breakdown + execution plan: `pp/PP-DEADLETTER-001.md`.

## PP-DATAINTEGRITY-001 — data reconciliation & integrity track — NEW 2026-07-11
**Dave: "there should be a data integrity track, for all of the data
reconciliations — there is a planning item or two unaddressed."** Correct
diagnosis — `docs/ai-plans/photo-integrity-mitigation.md` already existed
as a real 3-legged design (detect/recover/prevent) but had no single
owning PP (split across PP-UIPIPE-001+PP-DRIVE-INDEX-001+PP-ANNEX-001),
which is exactly why legs 2/3 (#1266, #1267) sat untagged with nowhere
clean to live — doubly true since PP-UIPIPE-001 no longer exists as its
own PP (folded into PP-EDITOR-001 same session). Leg 1 (detect,
`photo_files_readable` catalog-verify rule) DONE #1154 2026-07-05 — 206
bad/149 SKUs found. Legs 2 (verify-after-copy sha256 helper) and 3
(decode-verify at intake) open. Recovery still rides PP-DRIVE-INDEX-001
Phase 1; prevention's structural endgame still depends on PP-ANNEX-001.
Full design: `docs/ai-plans/photo-integrity-mitigation.md`; PP index:
`pp/PP-DATAINTEGRITY-001.md`.

**Target use cases for Tigwa's knowledgebase buildout (Dave, 2026-07-14):**
"I will have tigwa target these use cases in her knowledgebase build out."
This PP's own reconciliation work (photo integrity detect/recover/prevent
legs, the `status`/`#STATUS` write-path forensics below) is exactly the
shape of problem the git-annex/Recoll knowledgebase (PP-KNOWLEDGE-001,
buildout starting 2026-07-14) is meant to make fast — archive-snapshot
diffing, write-path history tracing, catalog-verify cross-checks were all
done by hand this project's history (see the `#1377` writeup below: found
via "ItemArchive snapshot diffs + `data-scrub-1053-report.json`," exactly
the kind of search a mature knowledgebase should make trivial). Concrete
starting scope for her, not abstract: legs 2/3 of the photo-integrity
design (open), and the `status`/`#STATUS` reconciliation pass (not yet
scoped/executed) once Dave scopes the "fun inventory."

**New leg 2026-07-13: `status` vs `#STATUS` write-path bug (todo #1377).**
Found while fixing the web UI's Eligible filter (it was silently excluding
items with blank status). Root-caused via ItemArchive snapshot diffs +
`/opt/TGW/var/log/data-scrub-1053-report.json`: on 2026-07-03 22:21,
`scripts/data_scrub_legacy_ebay_fields.py --apply` stripped the legacy
`#STATUS` key from 20,415 items, treating it like the script's other
genuinely-obsolete Magento artifact fields — but unlike its own sibling
guard for legacy category fields (which correctly refuses to delete until
the value is confirmed promoted to the canonical field first, #1209/#1252),
`#STATUS` had no equivalent protection. **Dave, 2026-07-13: `status`
(lowercase) was always the real canonical field — `#STATUS` was a manual
convenience alias (the `#` sorted it to the top of the JSON for hand
inspection) that was "sometimes not updated."** That inverts the obvious
read of the incident: the Jul 3 strip wasn't the core bug (removing a
stale convenience key is arguably correct), the core bug is that
`items.statusupdate()`, `items.verifiedupdate()`, and `bulk_edit`'s status
field (`BULK_FIELD_KEYS['status'] = '#STATUS'`) have **always written to
the wrong key** — every operator status update via `tgw update-verified`
or the bulk editor has been silently landing on the stale/legacy field,
never the canonical one. Live scope: 5,118 items currently have neither
key set (810 of those genuinely unlisted/unsold, the rest already resolved
via `ebay_listing`/`ebay_offer`). Dave: "this is a big fix" — logged only,
not yet scoped/executed. Needs: (1) write-path fix (point status writes at
`status`, stop writing `#STATUS`), (2) `data_scrub_legacy_ebay_fields.py`
either drops `#STATUS` from `FIELDS_TO_CHECK` entirely or gets the same
promotion-first guard as the category fields, (3) `items.create_item()`
still has no default status for intake paths that omit it, (4) real
reconciliation pass across all items with any status signal once Dave has
scoped the "fun inventory" — not attempted yet.

## PP-POSTGRES-001 — PostgreSQL item source-of-truth migration — NEW 2026-07-13
**PROPOSAL — design doc only, nothing built.** Dave, same session as the
#1376/#1377/#1378 incident chain above: "we have been futzing around with
json for too long. Time to grow up. Plus the ssd overheat today." Confirms
a hybrid design Dave separately worked out with Perplexity research
(`inbox/RESEARCH-55K-ITEMDATA-POSTGRES-SOURCE-OF-TRUTH.md`): **PostgreSQL
becomes the source of truth for item identity/status/location/workflow
state** (normalized columns for hot fields, `jsonb` for evolving content),
**photos stay untouched on disk** (Dave: "I really don't like my photos in
a database, and they aren't hit as hard as the data"), **JSON becomes a
generated export/archive artifact**, not the primary store — a real
inversion of today's architecture. Role split, Dave explicit: "once you
have a message bus like that use it. It just isn't our state master" —
NATS JetStream (already partially built, `ITEMDATA_MUTATIONS` stream +
`publish_mutation()`, PP-AIOPS-001 Phase 1) carries the durable
change-event log; Postgres holds current queryable truth. The JetStream
piece is wired to the wrong door today (only `items.py`'s narrow CLI write
path, not `http_server.py`'s real fence) — recommended as the first,
small, independent packet regardless of the larger migration timeline.
Needs reconciliation with [[PP-CATALOG-INCR-001]] (opened 2026-07-03,
explicitly assumes JSON stays truth — premise conflict, not yet resolved).
Reuses the already-running `state_machine` Postgres instance (todo #1351).
Full design + open questions (migration path, same-vs-sibling DB, field
normalization, JSON export cadence, rollback safety): `pp/PP-POSTGRES-001.md`.

## PP-RUNBOOK-001 — operational runbook hardening (thermal + eBay-ops) — NEW 2026-07-13
**Triggered by the 2026-07-13 tgw-prod NVMe thermal incident** and Tigwa's
two follow-up inbox reports (`TIGWA-EMERGENCY-tgw-prod-thermal-mitigation-20260713.md`,
`TIGWA-REPORT-runbook-gaps-20260713.md`) — used by Dave as a live training
exercise for Tigwa (told her to stop, re-read the plan and runbooks). Not
started; this section exists to capture the full operational context before
it's lost, per Prime Directive 5.

**Incident timeline, reconciled from the journal (`journalctl`,
`journalctl --list-boots`) same session:**
1. 16:09 NVMe hit 75°C (WARM) → 16:10 84°C (THROTTLE, watchdog killed disk
   hogs + stopped workers + took an emergency Btrfs snapshot) → 16:12 87°C
   (CRITICAL) → the on-host 88°C-class watchdog SIGKILLed an in-flight
   `pytest` full-suite run and the box did a clean power-off. Root cause of
   the empty/stale `todo/1370-llm-google-direct-test-isolation` worktree
   found this session (branch existed, zero commits) — the prior session's
   acceptance run got killed mid-flight, not a framework bug.
2. Boot at 16:15, only ~45s uptime, ended in an SSH-triggered `sudo poweroff`
   (session opened for root by `db`, immediately preceding "system will
   power off now") from 192.168.60.101 (a1131).
3. Boot at 16:18, ran 9 min — `catalog_rebuild`/`ebay_sync`/`ebay_legacy_sync`
   all came up unattended (they are `systemctl enabled`, confirmed still
   true this session) and `ebay_legacy_sync` ran a real live
   `GetMyeBaySelling` pull (19,604 listings) — ended in a second SSH-triggered
   poweroff, same pattern.
4. **Dave's own account of event 3, more nuanced than Tigwa's self-report:**
   he was trying to boot the machine back up; she was independently trying
   to shut it down, reading real thermal-destruction risk — "seems like a
   reasonable response," not a violation, even though it produced a
   confusing tug-of-war. Once he noticed the conflict he told her to stop
   and re-read the plan/runbooks. See
   [[feedback-tigwa-protective-override-2026-07-13]] (session memory) — the
   real gap is a fast operator-in-the-loop escalation channel for a
   protective override, not a harder lockdown of her authority.
5. Boot since 16:31 (current) — same three workers resurrected again
   (`systemctl enabled` survives every reboot). Stopped live this session
   (Dave's explicit go-ahead, `AskUserQuestion` confirmed "stop and
   disable"); **`disable` failed — `/etc/systemd/system` is read-only on
   this box.** This is the concrete mechanism behind todo #1322's
   still-open "durable stop" gap: enable/disable state is flake-controlled,
   not runtime-settable, so a real fix is a flake edit + rebuild, not a
   systemctl command. Confirms the risk was real, not hypothetical.

**Tigwa's mitigation already in place, reviewed and not objected to:**
temporary read-only SSH-reachability + NVMe-temperature watchdog on a1131
(`temporary-tgw-prod-independent-watch`, 1 min / 5 min cadence depending on
temp, alerts via Telegram, does not touch power), plus a documented
cool-boot-immediate-Btrfs-snapshot recovery window (snapshot
`20260713T1632`, 27s, verified local+received read-only subvolumes).

**Full gap list from `TIGWA-REPORT-runbook-gaps-20260713.md` (17 items,
not restated in full here — read that file directly, it stays in
`inbox/` until triaged):** no canonical thermal incident/drill runbook;
same-host monitoring is a failure domain (needs external detection + a
proven external→Dave→full-Tigwa handoff); intervention authority
(monitor vs. mitigate vs. shut down) needs to be explicit in the runbook,
not inferred mid-incident; the cool-boot snapshot window needs documenting
as a procedure with a temperature gate; a least-destructive workload
mitigation ladder is missing (alert → identify I/O producer → stop new
work → ask before pausing/killing an active job → preserve output →
observe → let the 88°C service be the final backstop); the physical-ops
section of `TGW-Quickstart.md` §9 is still a stub; runbooks have no
owner/last-verified/applicability/last-drill metadata; several restore
docs have stale command syntax and ambiguous snapshot-target naming
(`TGW-SNAPSHOT-0` vs `TGW-VAULT`); old MX/pre-NixOS restore material isn't
labeled as historical; a remote-backup path references the `dbukove`
rclone remote which conflicts with Tigwa's current never-touch boundary;
USB restore path is undrilled and `tgw-usb-stamp.service` failed on a
reviewed boot; `PP-RECOVERY-001`'s false code-loss conclusion is cited as
a cautionary example of weak evidence-searching. **eBay-ops study area
(Dave's explicit next-assignment for Tigwa, partially done — she reviewed
the eBay API already):** sold-order source-of-truth + picklist recovery
runbook, an eBay API responsibility map (Trading completed-orders vs.
Inventory items/offers/listings vs. OAuth/webhooks/rate-limits vs.
legacy-vs-platform-native), a token/scope incident procedure, and
acceptance criteria that check against Seller Hub counts, not just HTTP
200s.

**Recommended review/build order (Tigwa's own proposal, unmodified):**
(1) thermal/drill + external-alert runbook, (2) eBay sold-order/picklist
runbook + API responsibility map, (3) current-OS restore/snapshot index
with explicit media names, (4) Quickstart command validation against live
CLI, (5) physical station procedures, (6) owner/date/applicability/drill
metadata pass across all active runbooks.

**Additional Tigwa training queued this session (Dave's question, session
context):** `TGW-NixOS-Reference.md` (explains the immutable-`/etc`
surprise from item 5 above), `LLM-Providers-Quotas.md` + a skim of
`src/tgw/quota.py` (her poweroff interrupted a real metered `GetMyeBaySelling`
call mid-pagination — ties to the #1250 resubmission-storm lesson already
standing in this codebase).

**Status:** thermal half DONE 2026-07-14 — `reference/runbooks/thermal-emergency-response.md`
written (formal Tigwa-lite monitor policy, ties into PP-HERMES-EA-001's
leg-3 authority decision). eBay-ops runbook half and the remaining 17-item
gap-report triage still not started. Needs a todo filed with
`--pp PP-RUNBOOK-001` before any further runbook file gets touched
(going-forward tagging rule).

## PP-CODEGRAPH-001 — code graph + invariant/trace infrastructure (agents see design convergences) — FOLDED INTO PP-KNOWLEDGE-001, 2026-07-14

**Merged same day as filed (Dave, 2026-07-14 afternoon): "pp-codegraph also
same project now"** — PP-CODEGRAPH-001 is no longer tracked as a separate
PP; it's the concrete build-out of PP-KNOWLEDGE-001's "Graph | Graphify"
layer (see that section's 6-layer table above), which already existed as a
placeholder row before this PP was filed this morning. Both are hosted on
a1131, both were "awaiting Dave's research before build," and today's
"knowledge project on a1131" request refers to this single merged project
going forward — don't treat them as two separate initiatives requiring
separate scaffolding decisions. This section is kept in place (not deleted
— Prime Directive 1) as the detailed design record for the Graphify layer;
new work should be logged under PP-KNOWLEDGE-001 going forward, with this
section as its Graph-layer appendix.

**Origin:** filed as a deferred FUTURE-IDEAS.md entry 2026-07-14 morning
after Dave's directed Perplexity research (not blind — grounded against
this actual repo) proposed a 4-layer architecture: Tree-sitter code graph
(FalkorDB), Postgres+Z3 invariant catalog, DuckDB execution-trace store,
unified MCP layer. **Promoted to active PP same day** once Dave confirmed
he's building it — not deferred.

**The problem it solves (Dave's own framing, not a borrowed pattern):**
coders and planners lack insight into the interconnections of the design,
so cross-cutting "convergences" get missed until a manual audit sweep
finds them — and even then the finding doesn't get resolved into working
process, just logged. Real, already-paid cost: the fence-bypass pattern
(direct `ItemData/` writes skipping the tgw-api fence) was found
independently across 9+ separate files over multiple PP-COHESION-001 audit
sessions instead of in one pass; `status`/`#STATUS` write-path divergence
went undetected until forensic archive-diffing; NATS/JetStream built under
PP-AIOPS-001 wired to the wrong door relative to PP-POSTGRES-001's later
needs; PP-CATALOG-INCR-001 vs PP-POSTGRES-001 still has an unreconciled
premise conflict sitting in this plan.

**Decision (Dave, 2026-07-14): build the full stack, not a cut-down Phase
1.** An earlier Claude-authored planning pass
(`docs/ai-plans/pp-codegraph-001.md`) had proposed deferring Z3/DuckDB and
substituting Postgres-on-tgw-prod for FalkorDB, reasoning from "keep the
flake surface minimal" and "no demonstrated need yet." Dave corrected
this twice: the research was grounded in the actual repo, not generic
literature (evidence for the design was already stronger than that
Postgres-first framing credited), and the standing rule going forward is
more care before scoping down what he's already reasoned toward — see
memory `feedback-take-care-before-discarding-ideas`.

**Host: a1131, not tgw-prod.** Full stack (FalkorDB, Z3, DuckDB, Tree-sitter,
a new unified MCP server) hosted on a1131 — already Tigwa's office and
TGW's thermal-relief compute, client-shaped (no production traffic
dependent on it), 4 cores/19GB RAM/169GB free disk confirmed live
2026-07-14. This placement is what actually resolves the flake-minimal-
surface tension from the earlier draft — new infrastructure on a
non-production, already-less-minimal host doesn't compete with tgw-prod's
constraint the way it would have on tgw-prod itself.

**Status:** infrastructure-establishment planning doc written 2026-07-14 —
`docs/ai-plans/pp-codegraph-001-a1131-infrastructure.md` (components,
packaging options, data-flow, access model, resource budget, open
questions). **Dave is bringing additional research before the actual build
session** — nothing installed, no code written yet. Open questions
flagged for that session: FalkorDB packaging (flake vs. userspace),
invariant-catalog storage engine (DuckDB vs. a1131-local Postgres),
cross-host MCP access mechanism (tgw-prod packets need to reach a1131's
graph), repo-sync mechanism (a1131's checkout is known-stale, #1082), and
parse scope (`src/tgw/` only vs. also `tools/`/`scripts/`).

**Convergence with PP-HERMES-EA-001's planner/stitcher, flagged 2026-07-14
(Dave, still ideation — not yet a build decision):** the Z3 invariant
catalog isn't just a lookup an agent queries — it's a candidate trigger
for the planner's replanning decisions. If a runner's output gets checked
against the invariant catalog and Z3 confirms it holds, that's the
planner's "yeah, that's what I designed" signal to move forward; a failed
confirmation is a replan trigger, not just a bug flag. That makes the
planner/stitcher (see PP-HERMES-EA-001's "operating console/decision gate"
framing) the consumer of PP-CODEGRAPH-001's invariant-confirmation output,
and the in-process question channel (todo #1390) the plausible wire it
rides on. Not designed yet — Dave was still building this idea aloud when
it got captured; treat as a design lead for the eventual build session
(#1386), not a spec.

## PP-NIXOS-001 — NixOS migration (CatioNIX)
Canonical flake `~/tgw-flake` working; main-repo merge + workflow rules pending; a1131
no-GitHub-access (todo #1082); no process supervision for agent processes (design
requirement). FROZEN except stability fixes. Plan: `PLAN-nixos-migration.md`,
`nix/CLAUDE-NIX.md`.

**Standing rule (Dave, 2026-07-06, todo #1227): iterated-on tools stay out of the
flake.** Every `nixos-rebuild switch` carries risk, and wrangling the flake has
repeatedly burned whole day-usage-budgets against tasks that should be ordinary
coding — that cost is a signal the flake's surface area is too large, not a skill
gap. Rule going forward: before adding anything to the flake, ask whether it's
settled infrastructure (OS layer, the TGW service stack, secrets wiring,
user/group + hardening) or something still being actively iterated on (a tool
Dave is tuning/swapping versions of/prototyping with). Iterated-on tools default
to userspace install (pipx/uv/npm/git checkout) even at the cost of losing Nix's
reproducibility for that one tool — not worth the rebuild-risk + usage-cost tax
while it's still moving. **EXECUTED same day:** Hermes' `settings.model` and
Aider's package pin pulled out of Nix control (`nixos-rebuild switch` succeeded,
Hermes stayed healthy through the switch, Aider now pipx-managed) — see
`docs/ai-plans/decouple-hermes-aider-flake.md`. Hermes' primary model live-edited
to `deepseek-v4-flash` same session (Dave purchased DeepSeek + Google credits);
`hermes-agent` deliberately NOT restarted yet — `DEEPSEEK_API_KEY` doesn't exist
until Dave generates it, restart pending that.

**Audit #1143 nix-flake mitigation batch, EXECUTED 2026-07-06 (todos #1216,

a1131 SSH + kdotool/ydotool follow-up fixed — see document: dev-workflow/research/DONE-a1131-ssh-kdotool-followup.md
#1321 nix flake: SSH key rotation, hermes removal, vivaldi, lan-mouse/firefox fixes — see document: dev-workflow/research/RESEARCH-INPROGRESS-1321-nix-flake-changes.md
#1220-#1225):** all 10 findings reconciled against live state first (all
confirmed still real, none stale) before any fix — same discipline as the
Hermes/Aider plan. Fixed: SSH password auth disabled (#1216 — new ed25519 key
generated + verified working *before* the flip, password auth now confirmed
rejected); `services.tgw.enablePostgres` option added so the portable/client
tier genuinely skips PostgreSQL (#1220 — this fix itself regressed
`nix/tgw/users.nix`'s unconditional `postgres` user extraGroups line, caught by
`nix flake check` before it ever reached a1131, then fixed); a1131 no longer
imports production-only `keyd.nix` (#1221); duplicate `kdeconnectd` unit
removed from Home Manager, single definition in `os/sway.nix` now governs both
hosts, live-verified running from the correct unit path post-rebuild (#1222);
backup timer renamed/documented to match its confirmed-intentional 30-min
cadence, cadence itself untouched (#1223 — Dave: "we changed to every half
hour on purpose"); stale disko free-space comment corrected to match live
`vgs` (96MB free, not 292G) (#1224); dead `tgw/desktop.nix` Qtile stub deleted
+ gid-assertion symmetry added to portable.nix (#1225, partial).
**Deliberately NOT applied, filed as follow-ups:** #1219 NFS export — no
static IP exists for the actual intake camera/phone device (only tgw-prod
.100/a1131 .101 are reserved), so host-locking would break real intake; left
as-is pending a reservation (todo #1228). #1217/#1218 Syncthing GUI auth —
Dave is still actively configuring Syncthing peers/folders; deferred
alongside the earlier SSH deferral logic, explicitly not done yet. #1225's
other 2 sub-items — a1131 power-management (blocked: the "fix" would import
`IdleAction=suspend`, directly contradicting a1131's own standing "never
suspend, iMac12,1 bug" note) and the portable/master.nix boot-loader line
duplication (cosmetic, lowest priority) — filed as todo #1231 rather than
silently marked done. New findings surfaced while reconciling, not part of
the original 10: keyd-macroboard's `tgw-macro`/`tm` hardcode
`WAYLAND_DISPLAY=wayland-0` as a fallback but tgw-prod's live Sway session
runs `wayland-1` — likely broken for any macro invoked outside the graphical
session's own env (todo #1229, needs dynamic discovery not a hardcoded
guess). Also: a governance follow-up filed (todo #1230, Dave 2026-07-06) to
periodically review standing conventions/freeze-lists so none quietly
become development-blocking without cause.

**todo #1049 split (2026-07-04):** `--print-url` flag on the Python `tgw get-ebay-token`
CLI was **already fully implemented** (found while checking, not built new) — live-
verified, real auth URL generated, zero eBay calls. DONE, 5 new tests. The other half
(upgrading the `tgw` fish wrapper in `nix/tgw/home.nix` to call `xdg-open` automatically)
is a flake change under the freeze — left untouched, deferred to whenever PP-NIXOS-001
thaws or Dave wants a targeted exception.

## PP-PHOTO-001 — photo pipeline (GDrive → Gemini / eBay)
Sync infra live. Phase A (GDrive→Gemini multimodal draft) #1064; Phase B
(zero-bandwidth EPS upload) #1065. FROZEN until R1 drains.

## PP-CLIP-001 — clipboard manager (local-only, ratified 2026-07-11)
Phase 1 done; crash loop fixed s41. Phase 2 rofi picker DONE
(DONE-1055-clip-picker.md). **#1086 conceptual pass's split RATIFIED
2026-07-11: tgw-clipd + rofi picker stay LOCAL-ONLY forever.** Cross-machine
sync (the old "Phase 3" line) is **retired here, moved entirely to
PP-EVENTD-001** — `lan-mouse enter_hook` calls `clip-route --target`
directly; `clip-route` reads the clipboard itself, never routes through
tgw-clipd. Design: `pp/PP-CLIP-001.md`. Full #1086 analysis:
`docs/ai-plans/clipboard-concept.md` / `CLIPBOARD-CONCEPT-PLANNING-1086.md`.

## PP-EVENTD-001 — event server ("Radar") — UNFROZEN 2026-07-11, #1086 gate cleared
**Go `clip-route` daemon, design complete (2026-06-29), not yet built.**
Prerequisite (PP-CLIP-001 Phase 2) is DONE — Phase 1 here is now unblocked.
This is Concept 3's "real Radar O'Reilly": write a SKU to the clipboard, the
system already has an answer waiting. Restores + automates the old
`tgw.source`-era active-item context (`CurrentItem`/`CurrentItem.json`/
`CurrentLocation` symlinks) — trigger becomes implicit (any recognized
clipboard write swaps context) instead of an explicit macro/command.
**Regression to fix: `CurrentLocation` was silently dropped** when
PP-CONTEXT-001 replaced the old symlink dance — restore in
`src/tgw/context.py`. Trigger scope: recognized content only (SKU/URL/part
number regex, reuses tgw-clipd's classifier) — no noise on incidental
copies. Surface: folds into ActionConsole/tgw-http (not native DE widgets,
not the Flutter HUD) — active-item panel (photos, JSON, links, location).
Transport: PostgreSQL LISTEN/NOTIFY (already the settled design, NOT NATS —
distinct from PP-AIOPS-001's separate JetStream audit-log use). Full design
+ Radar requirements: `reference/PP-EVENTD-001-design.md`.

## PP-PORTABLE-CATALOG-001 — offline/portable catalog sync (Flutter) — first real design doc 2026-07-11
**Given its own heading, pulled OUT of the "Done" rollup — it was never
actually done.** Real, substantive code exists (Dio offline data layer,
sqflite outbox, snapshot-atomic-sync) but has **never been installed on
a1131** (its target device), never live-verified, and a documented
precedent exists of this exact feature self-marking "done" while the
Flutter build was actively failing (`SUGGESTIONS.md:209-210`, todo #151).
Deep architecture review (2026-07-11, Dave: "see where it lacks or
shines") found real structural gaps, not just missing tests: connectivity
detection is 100% manual despite the packages for automating it being
installed and unused; zero conflict resolution; offline reads don't
reflect the device's own queued edits; no retry cap on failed mutations;
several sync-state UI providers are computed and never rendered; and
**the backchannel Dave flagged as still-needed is confirmed missing** — no
server-initiated communication of any kind exists. A planning doc
(`PP-EVENTD-001-design.md`) had separately and incorrectly claimed a
Flutter HTTP listener was "already implemented" — corrected same day.
The backchannel fix is PP-EVENTD-001's own already-scoped Phase 5 (Flutter
HUD WebSocket) — not new work, just now confirmed necessary rather than
assumed-someday. Full assessment + phased remediation plan (Phase A:
harden the existing manual model; Phase B: build the backchannel, depends
on PP-EVENTD-001; Phase C: conflict resolution, needs its own design pass):
`pp/PP-PORTABLE-CATALOG-001.md`.

## PP-CATPICK-001 — smart category picker
**Phase 1 DONE 2026-07-04** (#1079): `category_candidates` (id/name/full ancestor
path) backfilled onto all 25 `category-groups.json` groups from the on-disk eBay
category tree cache — zero live API calls. `scripts/catpick_backfill_candidates.py`
(dry-run default, `--apply` to write); 4 unit tests. 2 stale category IDs
(`manuals: 34210`, `tools_hand: 43994`) not found in the tree cache — kept as
bare-ID fallback rather than dropped, flagged for review. Phase 2 (the actual
group-shortlist-first picker UI/logic) remains FROZEN until R1 drains. Memory:
project-smart-category-picker.

## PP-SELLERHUB-001 — TGW as a full Seller Hub replacement — NEW 2026-07-11
**Dave: "our app needs to be able to do everything eBay Seller Hub does,
but better."** Surfaced while triaging a homeless todo (#895) — not a
one-off config gap, a previously-unstated principle. Scope deliberately
unlimited (Dave declined to bound it) — this PP is the durable home for any
"TGW should do X the way Seller Hub does" note going forward, even ones
that won't get built soon. **Priority #1, concrete: category management +
business policies** (shipping etc.) — TGW has category *data*
(PP-CATPICK-001) but no live management/sync surface; absorbs #895
(shipping-cost config) and #12 (9 wrong-shipping Seller Hub listings — same
gap class). Everything else (profile editing, broader policy management,
and whatever else surfaces) parked pending a proposed but **not-yet-run**
Gemini audit of Seller Hub's full feature surface vs. TGW's current
capability — real work needing its own scoping pass (mechanism, cost/quota
estimate) before it runs. Full design: `pp/PP-SELLERHUB-001.md`.

## PP-DATALEARN-001 — alt-text / vision data pipeline
**Given its own heading 2026-07-11** — previously only a bare "Done"
rollup mention despite still having open work (#1108: `alt_text` queue has
no consumer, no `tgw-worker@alt_text` unit exists though `ai_identify`
writes to it; #144: full alt-text batch via Gemini Batch API). See also
FUTURE-IDEAS.md's deferred "alt-text on all item photos" item (multi-photo
pass, still gated on model routing settling) — related but not the same
scope as these two open todos.

## PP-LOOKUP-001 — product enrichment / barcode lookup (Tier 1)
**Given its own heading 2026-07-11** — previously only a bare "Done"
rollup mention (Tier 1). Open: #7, IGDB credentials (Twitch dev account →
register app → save client_id/client_secret) for game/media identification
lookups. See `reference/PP-LOOKUP-001-APIs.md`.

## PP-MULTIMODEL-001 — LLM provider/model routing
**Given its own heading 2026-07-11** — previously only a bare "Done"
rollup mention despite still having open work. Open: #1251, provisional
quota-cap revisit for the DeepSeek/Anthropic direct-API integrations
(`llm.py _call_deepseek_direct`/`_call_anthropic_direct`, quota caps
300/500/100) — **blocked on #1250** (`PP-COHESION-001`, the resubmission-
storm bug-class hardening) per `tgw-models.json`'s own config comment;
dependency now explicit (`depends_on=[1250]`), not just discoverable by
re-reading the comment. Also needs confirming `deepseek-v4-flash` and
`claude-haiku-4-5-20251001` are still the correct/current direct-API model
ids after a few days of real traffic.

## PP-MACRO-001 — macroboard hardware (#15)
**Given its own heading 2026-07-11** — was a bare Frozen-list mention.
Status UNCHANGED — still frozen until R1 drains, this only fixes
visibility. Open: #15, second keyboard wired up as a macroboard (see
`etc/interfaces/keyd/tgw-macroboard.conf`) — an operator-interface
hardware addition, not gated on anything beyond the freeze itself.

## PP-INVENTORY-001 — physical inventory verification — NEW 2026-07-11
**Dave: "11 is an entire missing PP — the tools to accomplish the job,
both the standard manual tool as well as the already supposedly in the
plan AI vision inventory helper."** Confirmed: no design doc existed for
either leg — `PP-VISION-001` was only ever a bare "(GPU-gated)" mention,
no substance. Two complementary tools: (1) manual sweep, absorbs #11
(`tgw ebay-sweep → physical inventory review`); (2) AI-vision-assisted
verification, consumes PP-VISION-001's capability. Distinct from
PP-STORAGE-001 (storage *organization*, size-class not category) and
PP-DATAINTEGRITY-001 (data *record* integrity) — this is specifically
physical-stock-vs-record reconciliation. Not started, needs its own
scoping pass before either leg is buildable. Full design:
`pp/PP-INVENTORY-001.md`.

## PP-STORAGE-001 — semi-chaotic storage (size-class, not category)
**Added to index 2026-07-12** (Fable independent review #1338 found it
relied on by PP-INVENTORY-001 and referenced above as a settled sibling,
but absent from this index entirely — recurrence of the s42 27-PP-dropped
failure mode). Items stored by size class, not category;
`size_class` lives in `category-groups.json`. No fresh design pass done
this session — pointer only; promote to a real heading + `pp/` doc on
next touch.

## PP-WHISPER-001 — voice transcription → suggestion pipeline
**Added to index 2026-07-12** (Fable independent review #1338 — `pp/PP-INTAKE-004.md`
cites this as an existing proven pipeline, but it had zero entry anywhere
in this plan). Real facility, verified live: `cmd_whisper_to_suggest`
(`src/tgw/api.py`) + `tgw history-index` CLI. No design doc or todo history
captured here yet — pointer only; promote to a real heading + `pp/` doc on
next touch, feeds PP-INTAKE-004's justshoutit voice-operated listing.

## PP-VISION-001 — vision-matching capability (GPU-gated)
**Given its own heading 2026-07-11** — was a bare Frozen-list mention with
zero design substance. FROZEN, GPU-gated, unchanged. The underlying
vision-matching capability consumed by PP-INVENTORY-001's automated leg;
originally conceived for findability ("locate this specific known item"
in PP-STORAGE-001's semi-chaotic size-class storage) — PP-INVENTORY-001's
verification use case ("does physical stock match records") is a related
but distinct application of the same capability. No design doc exists yet
for the capability itself, only its use cases.

## PP-REPRICER-001 — market-data repricer (the tool)
**Rescoped 2026-07-11 (Dave): this PP is the mechanical tool, not pricing
strategy** — "repricer a tool I believe?" Confirmed: schedule minting, the
markdown reducer, cliff-guard logic. Strategy (what price is right, why,
comps/positioning) moved to **PP-MARKETING-001** below — this section no
longer carries that content.

Read-only foundation done. **Context (s42): automated pricing is DEFUSED** —
schedule minting disabled, reducer cliff-guarded, prices are operator-only
until PP-MARKETING-001 delivers trustworthy data for the tool to act on.

## PP-MARKETING-001 — marketing strategy (pricing, positioning, promotions) — NEW 2026-07-11
**New PP (Dave, 2026-07-11): "pricing is really marketing strategy."**
Umbrella for positioning/pricing-strategy work, previously miscategorized as
part of the repricer tool. PP-PRICING-001 is its first tenant — likely not
its last (comps, listing-copy strategy, promotions could land here too).

### PP-PRICING-001 — Google Shopping comps via SerpApi (paid)
Two candidate data sources for comps, not mutually exclusive:
1. **eBay sold data** via `buy.marketplace_insights` — BLOCKED external: scope
   request in the eBay application review (#79, Dave answers DS questions).
2. **Google Shopping comps via SerpApi (paid)** — the designed interim
   substitute for marketplace_insights, dropped from the s42 redraw index by
   mistake and restored at Dave's flag. Full Phase 1 design (title-based
   Shopping SERP in ai_identify, `apis/lookup/shopping_search.py`, key via
   `secrets_root/tgw.env` per settled architecture, corrected 2026-07-12):
   `pp/PP-PRICING-001.md`.
   Cross-market active prices (Google Shopping: eBay/Amazon/Walmart) — a
   real floor signal, unlike same-marketplace Browse asking prices.
3. **Google-grounded price check** (Dave's 2026-06-09 suggestion, also
   dropped — "not accessible via API" is now stale: Gemini supports Search
   grounding as an API tool on our free-tier direct key). Zero-cost eval
   before paying for SerpApi.

Eval packet (#1109) — DONE 2026-07-04: ran grounded Gemini (gemini-2.5-flash +
Google Search grounding) against 10 real sold TGW items, scored vs the existing
free `BrowseCompsProvider` signal. **Result: Gemini grounding LOST** — 45.3%
mean abs error vs 30.4% for Browse comps; it kept finding plausible-but-wrong
comps for near-generic/vintage items. **Do not wire grounded Gemini as a
pricing signal.** SerpApi (Shopping SERP) still untested — blocked on #1110's
key. Full writeup: `docs/TGW-Plan-Vault/inbox/DONE-1109-repricer-eval.md`,
raw data `/opt/TGW/var/log/repricer-eval-1109.json`.

**Phase 0 comping interface** (research inbox, `pp/PP-PRICING-001.md` Phase 0
section): the #1109 result directly validates a Perplexity research thread's
thesis — don't let a model invent prices, build a supervised capture tool
instead. Proposed: 3-pane web UI (item / embedded eBay Product Research
browser / structured comp+pricing capture), `comp_snapshot` +
`pricing_recommendation` schema, Marketplace Insights as a later drop-in
upgrade to the same schema. Design capture only, not started — needs Dave's
go/no-go. Related: PP-AGENTIC-PRICE-001 candidate-query design composes
with either.

**Phase -1 — self-powered comp engine (Dave request, todo #1134):** the
infrastructure (`OwnSalesProvider` + `velocity_stats` worker) already exists
and runs — this turned out to be a data-density problem, not a missing
feature. **Initial 71%-uncategorized figure was checking the wrong field**
(Magento `attribute_set`, not what the pricing engine reads) — corrected via
todo #1135: the real field (`ebay_category_id`) is already populated on 52%
of the catalog (28,710/55,419).

**Todo #1135 — DONE, applied 2026-07-04.** Built
`scripts/recompile_category_backfill.py` as a **repeatable recompile
job** (Dave: "build it like we are going to go back in with a stronger
dataset every so often") — modular sources, additive-only via the new
`items.set_fields(only_if_absent=True)` fence helper, safe to re-run.
Checked 3 structured sources (historical-tgwcatalog.json,
historical-master-catalog.json via sku_old, `searchcatalog.csv`'s real
`ebaycat` values) against the 26,709 gap: **5,367 recoverable (20%),
applied live, 0 errors, idempotent on re-run.** 21,342 genuinely
unrecoverable from flat exports — that's the real target for the Phase 0
comping interface. Full detail: `pp/PP-PRICING-001.md` Phase -1 section.

## PP-AMAZON-001 — Amazon FBM for books/media (exploration, 2026-07-04)
**Opened same day as the comp-engine request** ("let's also start looking
into branching out to Amazon fulfilled by merchant for books and media").
Research-only pass, no account/code yet. Real findings: Amazon's Books
category ungating has tightened significantly (often requires 10+-unit
supplier invoices — a structural blocker for thrift/estate-sourced
inventory); DVD/CD/Video Games/Magazines are less gated and match TGW's
existing inventory well. Fee math is real and needs modeling before
committing: Professional plan $39.99/mo + ~15% referral + $1.35–1.80/item
media closing fee + Media Mail shipping could eat most of the margin on a
typical $10–20 media item — worth a per-category price model before
listing anything. Recommended order: check live gating status → price
model → SP-API read-only comp-data integration first (lower risk, "second
data source" half of the ask) → full listing pipeline only after margin
confirmed. Full writeup: `pp/PP-AMAZON-001.md`.

## PP-SOLD-001 — sold-event webhook (Tier 4)
Code done. BLOCKED: webhook infra (#16) gated on ISS-005 signature verification
(invariant C8) — forged notifications could mark items sold.

## PP-EBAY-SNAPSHOT-001 — submitted-payload capture + re-push
Phases 1–3 done. Phase 4 `tgw ebay re-push` + plan documentation #896. Overlaps with
eBayCapture — reassess scope at next touch. Design: `pp/PP-EBAY-SNAPSHOT-001.md`.

Snapshot baseline completed (19,486 SKUs) — unblocks #1131 Motors census; drift detection baseline set.
## PP-RECOVERY-001 — web UI regression audit
Findings doc'd (#1039, admin). Reassess against s40–42 UI rebuild — much may be
obsolete. Design: `pp/PP-RECOVERY-001.md`.

## PP-INTAKE-004 — camera app, "our core data acquisition tool" — scope expanded 2026-07-11
**PROMOTED from `docs/ai-plans/tgw-intake-app.md`.** Unified native Kotlin
handheld intake app (barcode/photo/video, SKU assignment, template/size/
location entry) — supersedes PP-INTAKE-002/003, absorbs PP-TASKER-001.
**Separate app from TGW's existing Flutter app — do not conflate** (see Open
discussion items below). **2026-07-11 expansion:** must be a full
bidirectional event-bus participant (producer AND consumer of PP-EVENTD-001,
not just an HTTP-POST delivery target) while remaining fully standalone with
zero event-server dependency; absorbs 3 live Tasker capabilities natively
(send-attributes→JSON, save-item, advance-location); hybrid barcode scanner
(native ML Kit/ZXing default + configurable external-app override, Dave's
current app may be faster, tuned to 8 cores). **This app IS the write
surface for justshoutit** (Tigwa's first apprenticeship task,
PP-HERMES-EA-001) — voice-parsed attributes land through the same interface
manual entry uses. Backend incremental-ID trigger (threshold=6, session-
completion fallback) is small/additive/already-fully-specified and unblocks
justshoutit independent of the rest of the app. 3-phase build: (1) core
capture, standalone; (2) event-bus integration, depends on PP-EVENTD-001;
(3) remote-control surface (VNC/terminal/macro-grid panes, macro-grid
sequenced after PP-EVENTD-001). Turntable hardware + dedicated
data-collector device remain a separate, deferred, non-blocking track.
Full design: `pp/PP-INTAKE-004.md`.

## PP-PLANDB-001 — plan/tracker tooling
Phases 1-4 done 2026-06-12→14: **P1** todo_items schema (`pp_ref`, `depends_on`,
`plan_anchor` columns, #109) · **P2** `tgw plan render` — wholly-generated
`plan/TGW-Taskboard.md` (#110) · **P3** `tgw plan check` — reconciles
plan↔tracker (orphaned pp_refs, stale anchors, mismatched done/open, #112) ·
**P4** `tgw plan status [PP-REF]` — one-line open/done/blocked rollup per PP
item (#132). P3 and P4 (`tgw plan check` / `tgw plan status`) run in the
mandatory session-start sequence in CLAUDE.md (Step 3); P2's output
(`TGW-Taskboard.md`) is read as reference, but `tgw plan render` itself is
not invoked at session start — it runs via the `plan_render` worker.

### Phase 5 — execution track / goal view (PROPOSED, Dave 2026-07-10)

**The ask, in Dave's words:** "all of the tasks to achieve the intended
product should be able to be viewed in order without the noise of equally
weighted items in other tracks." Concrete pain point: the audit#1143 cleanup
work just completed (todos #1171/#1182/#1198/#1213 this session, plus
earlier #1162-#1170/#1202/#1206/#1235/#1246) had to be gleaned by hand-
grepping `source=audit-1143` across the flat todo list — there was no single
view of "everything needed to finish this track, in order." Compounding it:
some of the same track's items live in Dave's own todo queue (agent=`db`),
not just Claude's, and today's flat per-agent lists never united them.

**What exists today that a v1 could use as-is (no new schema required):**
`source` (free-text, e.g. `audit-1143`), `pp_ref` (PP-* item), `depends_on`
(ordering signal already in the schema per Phase 1), and `agent`
(claude/admin/gemini/db — the cross-agent-unification piece). A track view
doesn't need new columns to exist; it needs a render mode that **filters** to
one track's items across all agents and **orders** by the dependency graph
(topological, using `depends_on`) rather than each todo's global priority
number — global priority is exactly the "equally weighted noise" problem:
an audit#1143 item at p95 reads identically to an unrelated p95 item from a
totally different track in the flat list, even though within its own track
it might be the very next thing to do.

**Shape (sketch, not yet designed in detail):** something like `tgw plan
track <pp_ref-or-source-value>` producing a rendered, ordered list — same
spirit as `tgw plan render`'s taskboard but scoped to one track and blind to
everything outside it. Test case once built: run it for `audit-1143` and
confirm it reproduces (in the right order) exactly the items worked this
session, with none of the unrelated backlog visible.

**Where this is headed (Dave, forward-looking — more to be planned, not
speced yet):** specific teams executing specific tracks end-to-end. The nix
flake is the working example that surfaced this need — today's session
independently arrived at exactly this pattern by hand: multiple
nix-touching findings (todo #1258's backup-mount durability fix, more
pending) got batched into one pending changeset in `~/tgw-flake` rather than
applied one at a time, because Dave wants "a bunch of flake updates to apply
... all at once." The anticipated future model: Dave (or Claude) submits
requirements against a track, a specialist team (e.g. a "nix specialist
team") compiles the accumulated requests into a single coherent deliverable
(one flake update, one PR, etc.) instead of a stream of one-off changes.
That implies track/goal becomes a first-class routing concept, not just a
display filter — todo metadata may eventually need an explicit
`track`/`owner_team` field once the team-routing design lands. **Not
building that yet** — this phase entry captures the initial ask (the view)
only; the team-routing piece is intentionally left unspec'd until Dave's
next planning pass.

### Done (designs in `pp/` or archive; tracker holds history)

**PP-EDITOR-001, PP-DATALEARN-001, PP-MULTIMODEL-001 removed from this list
2026-07-12** (Fable independent review #1338) — each was given its own
"Open" heading above on 2026-07-11 specifically because it has real open
work (PP-EDITOR-001: #1145 defect map; PP-DATALEARN-001: #1108/#144;
PP-MULTIMODEL-001: #1251), but the promotion never removed the matching
Done-rollup entry. See their headings under "Open — active or gated" above.

PP-EBAY-MIRROR-001 (P1/P1.5/P2) · PP-MIGRATE-001 ✅
2026-06-20 · PP-DEADLETTER-001 · PP-DOCFLOW-001 · PP-INTAKE-001 ·
PP-OFFER-001 · PP-OPS-001 · PP-PROMO-001 · PP-REF-002 ·
PP-REVISION-001 · PP-SHELL-001 · PP-STORE-001 · PP-TODO-001 · PP-VERIFY-001 (scaffold;
integration deferred) · PP-WM-001/PP-HM-001 (Sway/HM desktop) · PP-ADD-009 ·
PP-CI-001 · PP-CONTEXT-001 · PP-GLOBALS-001 (analysis) · PP-LISTING-001 ·
PP-LOOKUP-001 (Tier 1) · PP-PRICE-001/PP-PRICE-003/PP-PRICE-004/PP-PRICE-005 ·
PP-QUALITY-001 · PP-REF-001 ·
PP-REPRICE-001 (the markdown reducer — **defused s42**: minting off, cliff guard) ·
PP-SEO-001 · PP-STAGE-001 · PP-SYNC-001 · PP-FREESHIP-001 · PP-STRIKE-001.

**Superseded/obsolete:** PP-DEPLOY-001 (MX Linux image → superseded by PP-NIXOS-001) ·
PP-PRICE-002 (absorbed into PP-REPRICE-001) · PP-PLASMA-001 (delivered via CatioNIX
desktop split, a1131 Plasma).

DONE-1053: data-scrub legacy eBay Trading API fields — 20,419 items modified, zero exceptions (see filed document)

- PP-PRICING-001 (self-powered comp engine extension) design doc complete. PP-AMAZON-001 (Amazon FBM exploration) design doc complete. Research doc filed.

* #1113 ebay_dole interim fix — dead code removed, test added (2026-07-10)

- todo #1138: revised help text for `tgw revise --set` to clarify dotted-path claim (bare field names only, no nested expansion). Live evidence: 2046 passed, ruff clean.

#1338 Fable independent review of 2026-07-11 master-plan retarget — 13 confirmed findings (1 live incident: reboot-resurrected workers incl. pm_intake, re-stopped; 12 doc/plan corrections applied same session) — see document: dev-workflow/research/DONE-1338-plan-retarget-followup-triage.md
#1323 master plan retarget — catio development framework — see document: dev-workflow/research/DONE-1323-master-plan-catio-retarget.md
#1320 title-length guard — see document: dev-workflow/research/DONE-title-length-guard-2026-07-10.md
#1319 title-length enforcement fixed — see document: dev-workflow/research/DONE-1319-title-length-enforcement.md
#1318 restore save-draft button fixed — see document: dev-workflow/research/DONE-1318-restore-save-draft-button.md
#1258 backup alarm (db dump stale, rclone never completed) — see document: dev-workflow/research/DONE-1258-backup-alarm.md
#1257 stale ai_reidentify flags cleared — see document: dev-workflow/research/DONE-1257-clear-stale-ai-reidentify-flags.md
#1256 per-item Best Offer control — see document: dev-workflow/research/DONE-1256-best-offer-control.md
#1255 Motors category tree cache built — see document: dev-workflow/research/DONE-1255-motors-category-tree-cache.md
#1254 sync marketplaceId hardcoding fixed — see document: dev-workflow/research/DONE-1254-sync-marketplace-id-hardcoding.md
#1252 condition scrub + secrets facility — see document: dev-workflow/research/DONE-1252-condition-scrub-and-secrets-facility.md
#1249 diagnose dead letters — see document: dev-workflow/research/DONE-1249-diagnose-dead-letters.md (flagged: #1265 bulk requeue needs Dave's go)
#1240 fix broken tests (ebay_price.py) — see document: dev-workflow/research/DONE-1240-fix-broken-tests.md
#1239 code-review follow-ups (atomic write fixes) — see document: dev-workflow/research/DONE-1239-review-followups.md
#1238 get_access_token refresh + sandbox bugs fixed — see document: dev-workflow/research/DONE-1238-get-access-token-refresh-and-sandbox-bugs.md
#1236 ebay_backfill_offers fence bypass fixed — see document: dev-workflow/research/DONE-1236-ebay-backfill-offers-fence-bypass.md
#1214 ebay_motors_census stale-data + ambiguity fix — see document: dev-workflow/research/DONE-1214-motors-census-stale-ambiguous-fix.md
#1213 ITEMDATA_ROOT hardcoded fixed — see document: dev-workflow/research/DONE-1213-photo-repair-itemdata-root.md
#1211 photo-repair unlink safety fixed — see document: dev-workflow/research/DONE-1211-photo-repair-unlink-safety.md
#1210 photosync-canary price diff fixed — see document: dev-workflow/research/DONE-1210-photosync-canary-price-diff.md
#1210/#1211/#1238 code-review follow-ups fixed — see document: dev-workflow/research/DONE-1210-1211-1238-review-followups.md
#1209 order-dependency bug fixed — see document: dev-workflow/research/DONE-1209-category-legacy-order-dependency.md
#1135 category recompile completed — 5,367 categories recovered.
### Gated on R1 — named, designed later

## PP-BULKLIST-001 — bulk editing + listing surface (stub, Dave 2026-07-02)
The operator-gate design at volume: review MANY pending proposals in one sitting —
bulk-approve the ~99% that are right, pull exceptions into the single-item editor,
batch-publish approved items. **Hard gate: the single-item pipeline must be
operator-verified end-to-end first (R1.6/R1.7 pass)** — a bulk surface over a broken
pipeline bulk-applies the breakage. Design draws on the action-console principle
(state drives interface) and the 550 pending re-drafts as the first real workload.

**Rides along (todo #1113):** the "queue for auto-listing" checkbox's `ebay_dole`
worker was never installed — decide at this design pass whether to build it (+ set
a dole rate) or remove the checkbox permanently. Interim UI fix already shipped
2026-07-10: checkbox labeled "(inactive)" with an accurate tooltip, backend
`set_ready` response says the same, and a stray unreachable confirm-dialog still
claiming "next dole cycle" was dead code and removed.

### Frozen — parked, not cancelled (thaw only if it blocks an R1 packet)

PP-MC-001 (Midnight Commander UI) · PP-MCP-001 (MCP server — partial, tools live) ·
PP-FULFILLMENT-001 ·
PP-TASKER-001 (functions being absorbed into PP-INTAKE-004) · PP-PERP-AUTO-001 · PP-EMAIL-001 · PP-CLAUDE-HELP-001 ·
PP-DERIVED-001 (design feeds Data Charter) · PP-DATA-OWN-001 (axiom absorbed into
charter; mirror work continues as R1.8 + mirror fields) · PP-UI-INTEGRITY-001 ·
PP-REVIEW-001 ·
PP-RESCUE-001 · PP-AGENTIC-PRICE-001 ·
PP-CANONICALIZE-001 · PP-CAPTURE-001 ·
PP-HINT-001 (revisit) · PP-IFDIR-001 · PP-REMOTE-001 · PP-REF-003 · PP-GIT-001.
Long-horizon concepts: `FUTURE-IDEAS.md` (planning sessions only).

*(Frozen list: "LVM expansion (#1056)" removed 2026-07-12, Fable independent
review #1338 — #1056 is closed, superseded by #1136 under PP-HARDWARE-001,
see that section's own "Closing #1056 as superseded" note above.)*

*(Index completeness: restored 2026-07-02 after Dave caught PP-PRICING-001 missing —
the s42 redraw had dropped 27 PPs from the index; all archived designs remain
byte-complete in `archive/sections/` and promote to `pp/` on touch.)*

---

## Open discussion items (for 2pm 2026-07-04 planning session)

**Web UI vs Flutter app — duplicate-development fork, not yet resolved (Dave,
2026-07-06, todo #1227 planning session).** Confirmed while investigating why
the Flutter app is "basically ready but unusable": `reference/TGW-HTTP-API.md`
(dated 2026-06-04) documents the Bearer-token `/api/*` surface Flutter actually
calls (item search/detail/PATCH, action enqueue, eBay aspects, templates) — but
all the operator-facing feature work since then (PP-ACTIONCONSOLE-001's
state-driven action line + Editor/Live tabs, PP-LISTEDITOR-001's drift-gated
revision apply, live-fire 2026-07-04) landed on a different, newer surface this
doc doesn't cover. Flutter isn't broken — it's calling a real API that's now
missing the capabilities the current web UI has, and the gap widens with every
new web-UI feature. Also found while locating the app: `flutter/` in this repo
is the vendored Flutter **SDK source** (engine/, dev/bots/, examples/), not
Dave's project — the actual app is `apps/tgw_app/`; `apps/` also has orphaned
top-level `android/ios/web/windows/macos` folders alongside `tgw_app/` (likely
leftover from an earlier `flutter create` before the project settled into
`tgw_app/`), worth a cleanup pass regardless of which unification direction is
chosen. Three directions discussed, not decided: (A) extract the action
console's server-side logic into `/api/*` so both surfaces share one backend
contract — most work, keeps Flutter's real offline-catalog advantage
(PP-PORTABLE-CATALOG-001); (B) make Flutter a thin WebView shell over the
existing web pages — fastest, loses native/offline feel; (C) freeze Flutter,
converge on a web PWA — simplest, but doesn't deliver true offline satellite
catalog use. Dave: not ready to decide this session, flagging for a dedicated
pass.

**2026-07-11 update — nuanced answer, still NOT resolved (do not mark
settled):**
- Web UI is primary/most-complete today — pragmatic choice ("I had to get
  something working to test the site so I directed the web UI build"), not
  a philosophical commitment against Flutter.
- Flutter is NOT abandoned — real capabilities worth leveraging later ("I
  really like the flutter app and I believe we will be able to take
  advantage of its capabilities"). Current state: browse-only, no write/
  action capability ("really nice to browse around in, just does nothing
  else").
- Neither surface does everything; web UI is closer to complete.
- **The actual division of responsibility is explicitly deferred** —
  "divide later."
- **Hard constraint going forward, decided now:** Flutter must **reuse the
  same web backend functions** the web UI calls, not duplicate logic — "so
  we do not have too much extra dev." Any future Flutter work is a client
  against existing `/api/*` endpoints, never a parallel implementation.
- Note: this is entirely separate from PP-INTAKE-004's new Kotlin camera
  app (a different, unrelated app) — an earlier synthesis pass this session
  wrongly conflated the two before being corrected.

**Relocate the plan-vault document inbox into `/opt/TGW/incoming/`?** Dave recalled
discussing this before (2026-07-04) but no record of it was found in this plan, any
PP design doc, or memory — capturing now per Prime Directive 5 so it isn't lost
again. `/opt/TGW/incoming/` was built session 42 as the general "root of ALL inbound
data" (Data Charter) — `newitems/` (camera/intake drops), `ebay/` (raw API capture,
E7), `lookups/` (reserved) — but `docs/TGW-Plan-Vault/inbox/` (research docs,
PP-intake notes, Syncthing-synced across workstations) remains separate, its own
thing. Open question: should the vault inbox move under `/opt/TGW/incoming/`
alongside the other inbound streams, or does it stay separate since it's
document/note intake (human research, Syncthing-native) rather than raw
API/photo capture (machine-written, group-only perms, different retention model)?
Dave is linking his existing `docs/TGW-Plan-Vault/inbox/` via Syncthing across his
workstations in the meantime — no filesystem move happening until this is decided.

**Self-healing philosophy is visibly working — verify quality at 2pm (Dave, 2026-07-03).**
Observed live tonight during the overnight queue: the agent is finding, investigating,
and logging anomalies as part of the normal workflow, not just executing tasks blind —
e.g. the R1.8 snapshot's per-SKU error counter jumping 3→23→29 was checked against the
quota-incident log and confirmed benign (silently-counted 404s for items with no offer,
not 429s/quota exhaustion) before being written off, rather than either ignored or
mis-flagged as an alarm. Matches the standing design philosophy (memory:
feedback-self-healing-system — auto-detect, auto-sanitize or surface, self-service
resolution, never just patch-and-move-on). Dave wants this specifically verified for
quality at the 2pm session — i.e. confirm the investigations are actually correct and
thorough, not just reassuringly-worded, before trusting the pattern going forward.

**PP-INTAKE-004 — PROMOTED to active PP 2026-07-11**, no longer just a
discussion item — see its own heading above (Pending projects index) and
full design `pp/PP-INTAKE-004.md`. The platform-question half (is TGW a
sellable platform) remains genuinely open and is now explicitly
acknowledged-and-parked rather than an unstructured loose end — three
business models named (multi-tenant host / licensed self-host / open-core
services), not chosen between, flagged as its own future planning topic.
Also still open, unsolved: "Tasker Permissions" companion-app absorption
(todo #1227, revisit when this track thaws); `clip-route`'s capture-before-
SKU-exists correlation ID (lands in PP-INTAKE-004's Phase 2).

**catalog_rebuild dead-letter root cause (2026-07-04): SKU-rename races, not a bug.**
15 `catalog_rebuild` dead-letters, all "No such file: ItemData/<old-sku>/..." —
confirmed via `sku_history`: each old SKU was renamed by `ebay_sku_migrate`
(e.g. `tgw20171218042138799` → `tgw201712180421387`, `normalize_class_a`,
2026-06-29) and the new SKU directory exists fine. A rebuild scan just caught
the old path mid-rename; catalog rebuilds have clearly succeeded since (fresh
catalog data used all night). Cancelled (Dave's go). Not fixed at the source —
worth a small robustness pass later (`_verify_item`/`build_all_catalogs`
tolerating a missing file mid-scan as a skip-and-continue rather than failing
the whole rebuild) if this recurs during a future migration batch.

- DRAFT-1076-eps-support-ticket.md filed — pending Dave's review/submit.

2026-07-10 planning session agenda filed as reference (see reference/AGENDA-planning-session-2026-07-10.md).
## Standing gates (human-only)

Never: alter eBay OAuth scopes · auto-publish · **push AI-regenerated content to a
live listing without operator inspection (invariant C9 — enforced in ebay_stage)** ·
commit without Dave · bulk ItemData mutation without dry-run · bulk eBay ops without
Dave's go + quota note in handoff.

## Tool routing (summary — full tree in `next-process.md`)

Design/diagnosis/cross-cutting → Claude (Fable/Opus, full plan context). Well-written
packet → Claude (Sonnet) or Aider, loading only the packet's context budget. Every
LLM task names its model (memory: feedback-llm-model-selection). Human-only: OAuth,
Seller Hub edits, infra deploy, hardware.

## Session protocol

Start: thermal → inbox → SESSION-BRIEF/this plan → `tgw plan check` + `tgw ops-digest`
→ register todo + INPROGRESS breadcrumb. End: `/tgw-exit`. One outcome per session,
stated as an observation up front. Triage (digest) and building are separate sessions.
