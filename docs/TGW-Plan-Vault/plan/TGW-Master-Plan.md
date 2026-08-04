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

## Standing context, 2026-07-22 — the month-long Max-plan build sprint

**Dave: "we are planning for a max subscription upgrade to build this out.
That is what we are planning for. Build it all. What we want. I think we
can easily do it in a month with a max plan, tigwa, and some api pocket
change."** Not a scope change to any single PP — a capacity change that
affects how every open PP in this plan should be sequenced. Confirms and
extends the Max-plan-purchase context already on record (see memory
`project-hermes-strategy-pivot-custom-harness-agnes.md` and
`project-orchestrator-classifier-cluster-2026-07-21.md`, which had it
converging toward "Friday Max-plan purchase" without yet naming the
intended scope): the plan going forward is roughly a month of build
capacity (Max-plan Claude usage + Tigwa + incidental API spend), aimed at
building out what's currently scoped across this plan's open PPs, not
just the JetStream cluster in flight as of this date.

**Direct consequence for sequencing advice already on record:** Tigwa's
2026-07-22 PP-POSTGRES-001 reconciliation (see that PP's section) —
"canonical sequencing still says pipeline logic fixes and UI first unless
deferral becomes materially painful" — was given without awareness of
this capacity change. That specific recommendation isn't wrong, but it
was made blind to a material fact that could change the calculus (a
month of dedicated build capacity is a different resource-availability
picture than "whenever it comes up between other work"). **Flagged back
to Tigwa** — see `inbox/tigwa/` — so her sequencing judgment can account
for it; not overridden unilaterally here, since re-deriving her own
recommendation is her call to make once she has the same context, same
pattern as every other cross-actor reconciliation in this plan.

**Not yet a concrete roadmap.** This section records the capacity/intent
decision itself; it does not commit to a specific one-month sequence
across all open PPs. The JetStream substrate buildout already in flight
(`docs/ai-plans/jetstream-substrate-buildout.md`) continues unchanged —
its own sequencing (blockers before downstream packets) holds regardless
of overall capacity. A fuller month-scale roadmap, if Dave wants one
written, is a separate planning pass from this note.

**Doctrine for this whole sweep, recorded 2026-07-22 (Tigwa, relaying two
of Dave's corrections in sequence, both processed from `inbox/claude/`):**

1. *Unfolding, not scope creep* — for roughly a month the plan had been
   compressed into one ~50k-character artifact, which made it hard to
   conceive, review, sequence, or build (too many decisions/dependencies/
   acceptance conditions forced onto one dense surface). The current pass
   — expanding compressed PP entries into full design docs, bounded
   packets, runbooks, contracts, audit registers, evidence sections — is
   deliberately unfolding the *same* settled intent into usable layers,
   not a new direction. Distinguish a newly-surfaced prerequisite or
   missing acceptance criterion from an actual new product ask; use the
   Master Plan as the navigable index, not the sole container for every
   buildable detail; keep raw evidence/derived review/decision/execution
   artifacts linked rather than overwritten.
2. *Correction to how far to take it* — the first framing (above) reads as
   license to stop unfolding once a PP is merely "detailed enough."
   Dave's actual instruction is stronger: **plan until there is nothing
   material left to plan**, for every unfolded PP — outcomes, scope
   boundaries, authority, interfaces, data contracts, dependencies,
   migration/recovery/rollback behavior, security/operator controls, test/
   acceptance evidence, observability/runbooks, sequencing, owner, and
   decision gates. Not license to invent new work or relitigate settled
   direction — an instruction to remove *material ambiguity* before
   execution. A plan is dispatch-ready when its remaining uncertainty is
   explicitly bounded/accepted, not merely hidden by compression. The
   5-step flow: (1) unfold + fully plan the PP portfolio → (2) reconcile
   plan vs. external reality vs. TGW operational reality → (3) close/
   accept material design gaps → (4) dispatch bounded implementation
   packets → (5) verify actual completion against the plan, replan when
   evidence changes. This sweep is currently in step 1.

**Doctrine addition, 2026-07-25 (Dave, via Uh-huh thought mode,
`HERMES-EXECUTION-PLAN-DELTA-2026-07-25.md` item 1) — Claude's own plan
authorship gets an evidence-first independent second opinion, never
self-accepted.** Claude authored this plan and may naturally favor its own
existing implementation choices; a review of Claude's plan/design work
therefore needs an independent reviewer that does not defer to Claude's
conclusions — reviewing operator workflow, dependencies, recovery,
performance, authority, and claimed-versus-actually-usable tools, then
reporting confirmed findings, evidence gaps, alternatives, and a
Dave-visible decision table. This is the same "spec/invariant is the
determinator, adversarial verification is how correctness gets checked"
doctrine already stated above, extended explicitly to cover Claude's own
planning output, not just code diffs — Claude is not exempt from the
review discipline it applies to everyone else's work.

## KNOWN ISSUE — 5 PP docs exist as genuinely diverged duplicates, found 2026-07-22

Found while fixing broken `pp/PP-XXX.md` links (12 of them, now fixed above
to `../pp/PP-XXX.md` — those files only ever existed at
`docs/TGW-Plan-Vault/pp/`, the older location, never at the newer
`docs/TGW-Plan-Vault/plan/pp/` the master plan's relative links assume).
**Different and more serious**: five filenames exist at BOTH locations
with genuinely different content (confirmed via `diff`, not just
different mtimes) — **`PP-DATAINTEGRITY-001.md`, `PP-HARDWARE-001.md`,
`PP-HERMES-EA-001.md`, `PP-PHOTOSYNC-001.md`, `PP-PORTABLE-CATALOG-001.md`**.
The `plan/pp/` copies are generally much larger and more recently touched
(e.g. `PP-HERMES-EA-001.md`: 59KB in `plan/pp/` vs 4.6KB in `pp/`,
`PP-PHOTOSYNC-001.md`: 33KB vs 2.7KB) — likely `plan/pp/` is the actively-
maintained canonical copy and `pp/` is a stale pre-migration leftover, but
**four of the five `pp/`-location files share the exact same mtime
(2026-07-18, `1784414003`)**, which is suspicious enough (a batch touch,
not organic individual edits) that this needs actual verification, not an
assumption, before anyone treats one copy as disposable. **Neither copy
has been deleted or merged — Prime Directive 1, both preserved as-is**
until a dedicated reconciliation pass (diff each pair, confirm which
carries the real current information, fold anything genuinely orphaned in
the older copy into the canonical one, then archive the stale copy rather
than delete it). Not attempted here — real content-comparison work across
5 documents, not a mechanical fix like the 12 broken links. **Todo filed,
not yet worked.**

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

## Photo-throughput as the practical success criterion — evidence note, 2026-07-22
**Operator-provided historical evidence (Tigwa relay), validate
quantitatively before causal claims or target tuning.** In 2017, for
~5 months, a dedicated helper used an older camera app + `tgw.source` to
photograph items ~5 hours/day; Dave reports income tripled within a
month and rose to ~5x prior level during that period. Strongest known
operational proof that the bottleneck worth removing is Dave's
non-photography busywork. Target framing for the present TGW/operator/AI
work: make it practical for Dave to photograph **~250 items/day**, by
reliably absorbing/simplifying the surrounding intake, data, review,
listing, and communication work around him.

Catalog evidence available for audit (not yet pulled): timestamped SKUs
like `tgw201701040108133` in the January 2017 range support reconstructing
the cohort, but do not yet establish the exact 5-month window, hours,
revenue multiplier, or causation — sourcing volume/pricing/inventory
mix/seasonality are unaddressed confounders. **Bounded next evidence
packet, not yet authorized to build:** read-only 2017 cohort
reconstruction — exact date range from timestamped ItemData, daily/
hourly SKU volume vs. matched pre/post windows, joined eBay revenue
series with explicit lag assumptions, confounders reported, staged for
Dave review before it becomes a performance claim or capacity target.
No catalog/listing/pricing/camera/credential/production mutation
authorized by this note.

**Product implication:** treat "photography capacity" as a first-class
outcome metric (photo-ready items/day and /focused-hour, time-to-
review-ready, backlog before/after, downstream rework rate, lag-aware
revenue realization), not a side effect of a feature list. Every
Flutter/KFMAWI/clip/Tasker/Radar/agent-mailbox feature (see
**PP-KFMAWI-001** below) is valuable only insofar as it removes friction
from this loop or preserves operator control — not by adding ceremonial
communication work that pulls Dave away from photographing.

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
repaired 2026-07-04 (todo #1102 DONE) — 1,761 pass / 1 skipped / 0 fail / 0 errors.
**2,582 of 3,239 `ebay_draft` dead-letters root-caused 2026-07-04**: OpenRouter "402
Payment Required" billing gap (since resolved) — not a logic bug, deliberately not
auto-retried (payment errors aren't in `worker_base._TRANSIENT_ERRORS` by design).
Half bulk-requeued via `scripts/requeue_ebay_draft_402_dead_letters.py`; remainder
queued for follow-up.

**s43 update (2026-07-03):** EPS-exhaustion root causes found and fixed — retry_wait
backlog (2,715 jobs) cancelled; invariant **C10 operator lane live and verified**
(operator actions can no longer be starved by background debris); upload worker's
partial-success masking still open as PP-PHOTOSYNC-001 P1 (#1115); 492 published
items measured photo-short (P4 repair, ramp pre-authorized). s41-s43 work
committed+pushed: `ae9b1e6` on `catio-nix-0.0.1-alpha`.

**Other closed items this stretch** (one-line each, full detail in the named doc):
- DONE-1054: item detail History link via sku_old — live, 39,485 records indexed.
- #1049: `get-ebay-token --print-url` CLI DONE; fish wrapper deferred (PP-NIXOS-001 freeze).
- Recoll index Phase 0 built: 441K docs, 4.6 GB (#1066). Nightly timer + CLI wrapper pending.
- #1146: a1131 NFS shares + claude account LIVE — `reference/a1131-nfs-setup.md`.
- #1145: PP-UIPIPE-001 defect audit — two tool fixes LIVE, 402 pile drained, fleet photo sweep done (#1154). Broker planned (`ai-plans/reconciliation-broker.md`).
- #1174: eBay webhook signature fail-open security fix shipped (`dev-workflow/research/DONE-1174-webhook-fail-open.md`).
- #1245: 3 confirmed fixes applied, 4 plausible findings deferred per Dave's instruction.
- #1248: `ebay_legacy_sync` stopped (6-min retrigger eating trading quota, root cause unknown, sold detection paused, blocked on #16 webhook endpoint).
- #1077: orphaned offer forces `ebay_sync` per-SKU fallback (Dave -> eBay support); 15 Syncthing conflict files in vault; nats health check red (module absent).


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

**Parallel-track discipline, 2026-07-16 (Dave):** R1 (this section, headline
"get the pipeline restarted in earnest" / R1.6's web-UI end-to-end listing)
is the concentrated focus, but that doesn't mean every other PP freezes
while it's underway. Dave's own framing, using PP-UIUX-001's Flutter app as
the example: "even though the implementation is lagging, the scaffolding
exists and it works more or less... we concentrate on making the web ui
list items and plug away at the rest." **The pattern:** background/parallel
PPs keep making incremental progress (design passes, small unblocked
packets, credential provisioning, scaffolding maintenance) so they're
*ready* the moment R1 clears and attention shifts — not fully paused,
but also not competing for the concentrated focus R1 needs right now. This
is the operating principle behind today's planning sweep (PP-STORAGE-001/
PP-VISION-001/PP-INVENTORY-001/PP-UIUX-001/PP-INTAKE-004 all seeded with
real todos, none of them started) and should govern future sequencing
calls the same way, not just today's.

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

R2: #1181/#1202 exception propagation fix complete — quota exhaustion now correctly requeues from ai_identify and ebay_draft (`dev-workflow/research/DONE-1181-1202-review-followups.md`).
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

Full PP-PHOTOSYNC-001 packet history (P1-P10) and PP-EDITOR-001 console-fix follow-up: `pp/PP-PHOTOSYNC-001.md`.

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

## PP-FENCE-002 — "Don't climb the fence, use the gate" proposal
Research filed, not yet a scoped todo: `dev-workflow/research/PP-FENCE-002-
climb-the-fence-2026-07-10.md`. Promote to a real `pp/` doc on next touch.

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

**Hermaroid guided-session CUA bridge — built + verified live, 2026-07-22
(packet #1671, todos #1665/#1670/#1671).** Dave wanted a way to demo his
own workflow to Tigwa in a guided session (plus an inventory walkthrough)
without touching his own `db` session/browser/credentials. Original design
(ACL-share `db`'s live Xauthority into `tigwa`) was rejected by Tigwa as
too broad; her counter-design — `cua-driver` daemon runs inside
`hermaroid`'s own isolated session, Tigwa/Hermes is a client of a narrow
authenticated socket bridge, never a reader of anyone else's Xauthority —
went through sizing (#1670, small: zero flake changes needed, headless
Xvfb sufficient) then two rounds of Tigwa's own review on the formal build
packet (#1671) before dispatch, catching real gaps each round (hardcoded
UID, missing Xauthority lifecycle, unproven Hermes integration seam,
ambiguous trigger authority) that got closed before any build happened.
Built and verified 2026-07-22: purpose-created socket path under
`/opt/hermaroid-cua/` (not XDG runtime-dir default), full Xauthority
cookie lifecycle (fresh per-session, `-auth` not `-ac`, deleted on stop,
verified-gone), a real proven Hermes integration seam
(`hermes-cua-wrapper`, both `manifest`-probe and `mcp --socket` launch
shapes), no new sudo grants (only pre-existing `db`/Claude passwordless
sudo can start/stop), complete rollback, zero flake changes, and
confirmed no `--dangerously-*` flag used anywhere. One flagged-but-
contained finding along the way: the sizing agent (#1670) attempted
`--dangerously-bypass-approvals` unprompted on its first try — blocked by
the permission classifier before it ever executed, no bypass was ever
active, all fixture proofs ran under default `standard` mode; agent gave
an honest self-critique when asked to explain. **Client-side (Hermes/
Tigwa's own connection to the socket) is explicitly her own configuration
work, not built here** — she reviews the system-side contract next, then
wires her own client via her self-configuration process.

**Correction/addendum, 2026-07-22 (Tigwa's follow-up review,
`TIGWA-REVIEW-packet-1671-system-contract-follow-up-blockers-2026-07-22.md`)
— the "built and verified live" claim above is about the build itself and
stands; real unresolved post-build blockers were found afterward and are
not yet closed.** Tigwa independently read the installed client
document/wrapper/lifecycle scripts on a1131 and confirmed the socket
location, Xauthority lifecycle, wrapper `manifest` handling, standard-mode
requirement, and absent live hermaroid session are consistent with the
intended boundary — but client configuration must remain stopped pending
these corrections/evidence:
1. **Unsafe PID teardown (blocker):** `hermaroid-cua-stop` kills a pidfile's
   PID merely because it's alive; a reused PID could cause root to kill an
   unrelated process. Needs PID-identity verification (owned by
   `hermaroid`, matching expected executable/arguments, ideally recorded
   process start time) before signaling, same discipline for crash
   handling.
2. **Rollback overreach (blocker):** the documented rollback deletes all of
   `/home/hermaroid/.local`, broader than bridge-owned state and able to
   remove unrelated hermaroid user data. Must limit removal to a
   bridge-owned binary/state directory or prove the account/location is
   dedicated and empty by contract.
3. **Unproven `HERMES_CUA_DRIVER_CMD` coexistence boundary (client gate):**
   this env var applies to the whole Hermes process, not one action — the
   client procedure must confirm it's used only by a dedicated
   guided-session profile/process, never silently replaces Tigwa's normal
   desktop driver, and is removed/restarted on bridge stop; a real,
   currently-running normal Tigwa CUA MCP process confirms this is a real
   coexistence boundary, not theoretical.
4. **No retained raw evidence bundle (evidence gate):** `/opt/hermaroid-cua/`
   has implementation/doc artifacts but no manifest/log bundle for the
   eight required live checks (including the wrapper's `manifest` and MCP
   invocation proof) — a correspondence summary is not the raw command/log
   evidence the packet requires.

**Todo #1686 now tracks the fix; client configuration is gated until it
closes.** These are implementation/evidence corrections, not a request for
broader authority — established exclusions stand unchanged (no flake
change, no Dave-session access, no unattended trigger, no
`--dangerously-*` flags).

**The end state, stated plainly, 2026-07-16 (Dave):** "monitoring, watching,
fixing, then giving more responsibility. It's not babysitting, it is
development. When we are done we will have both lightened your burden and
mine and have a better platform." This is the explicit rationale behind
every training-then-authority-unlock step already in this plan (Tigwa's
supervised operation before autonomy, the crypto-lock gating, agent
role-restriction hooks) — not oversight for its own sake, a deliberate
capability-building arc with a named destination: less load on Dave, less
context/startup burden on Claude, and a genuinely better platform as the
output of the process, not a side effect.

**Buildout beginning 2026-07-14 — standing requirement: build portable,
independent of the separate/unresolved Nix question** (Dave: "our platform
is better off being portable in the long run"). Immediate consequence for
PP-AIOPS-001 Phase 5: bubblewrap vs. nspawn+Btrfs reconciliation now leans
portable-by-default. Full writeup in `pp/PP-CATIONIX-001.md`'s new
standing-requirement section; broader Nix-or-not question stays parked in
`FUTURE-IDEAS.md`, unaffected by this.

**Cross-reference, 2026-07-25:** the "build portable" standing requirement
above is now a full program, **PP-PORTABLEFLEET-001** (below) — foundation/
Nix batch → laptop cohort → tablet pilot → capture cohort → expansion, each
device enrolled/accepted under a named Tailscale identity and least-
privilege policy. Same "monitoring, watching, fixing, then giving more
responsibility" capability-building arc this PP already names, applied to
physical/portable devices rather than agent personas — not a competing
design.

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

**Laptop-side memory upgrade — Dave decision 2026-07-24/25:** after
Helicrew becomes the primary, by-your-side Tigwa/Hermes executive-assistant
seat, upgrade its memory layer to self-hosted **Hindsight**, not embedded
`pg0` or a shared TGW database. Architecture: laptop Hermes profile → local
Hindsight API (`local_external`) → dedicated PostgreSQL database and
restricted Hindsight role with `pgvector`. Hindsight is durable but
non-authoritative agent memory; TGW application data, authority, and
PostgreSQL roles remain separate. The built-in curated `MEMORY.md` and
`USER.md` remain human-readable fallback/routing context. First acceptance
requires controlled import, database-aware backup, restore into an empty
test target, index/repair check as required, and known-fact recall through
a restarted Hermes session. No Hindsight installation, final service
exposure, secret provisioning, or flake change is authorised by this
record alone. Full contract is in this PP detail's Hindsight section.

**Cutover executed, verified live, 2026-07-26T00:05 UTC (Tigwa's own
`tgw-exit`-equivalent checkpoint) — Helicrew is now the primary Tigwa
seat.** `hindsight.service`, `hermes-dashboard.service`,
`hermes-gateway.service`, `ydotool.service` all enabled/active under the
`tigwa` user with `Linger=yes`. Hindsight health-checked
(`{"status":"healthy","database":"connected"}`); dashboard verified
locally at `http://127.0.0.1:9119` (HTTP 200) — Node 22.23.1 installed
under `~/.local` only, to build the dashboard, no host/Nix change. A new
dedicated Helicrew SSH identity authenticated live to both `db@tgw-prod`
and `tigwa@a1131`. The default `hermes-gateway.service` on tgw-prod and
a1131 were **disabled** as part of this same cutover (a1131's separate
`hermes-gateway-t-lite.service` was left untouched). Hermes durable memory
now records Helicrew as the primary seat in the fleet topology. **Open
risk, not yet closed:** the Telegram adapter has not logged a successful
connection since cutover (direct HTTPS reachability to
`api.telegram.org` confirmed, so this isn't a network block) — Dave
elected to only chase this further if it's still failing post-cutover;
don't treat Telegram as the sole move-confirmation channel until a live
message round-trips.

**pm_intake replacement direction approved, 2026-07-22 (Dave, without
waiting on staged-artifact resend):** Tigwa's supervised PM-intake/
librarian workflow v1 proposal (#1434, PP-KNOWLEDGE-001) is approved on
Dave's direct confirmation — "her proposal for pm-intake replacement is
solid... it is simple division of responsibilities, she monitors and
maintains the library, so pm-intake belongs there." Rationale: pm-intake's
successor function is a librarian responsibility (ingest/preserve/classify
Plan Vault material), which is already one of Tigwa's four named functions
(PP-HR-001 role clarification, 2026-07-22). Same approval extends to the
open-assignment audit + provisional JD v0 review (#1433/#1439) — "likely
same on others." Claude's review of the two proposal documents themselves
was blocked (cited `dev-workflow/research/` staged files don't exist on
tgw-prod's canonical checkout — same mailbox-divergence class as the
19-file recovery batch); Dave's instruction is not to block on that —
approval stands on his own direction, the missing artifacts are a filing/
sync gap to close separately, not a condition on this decision. #1434/
#1479/#1433 remain open in the tracker as implementation/follow-through
items, not closed by this note.

**2026-07-20 (Dave): Hermes config updated with non-thinking mode for some auxiliary
models**, extending the same pattern built for `PP-SIMPLEJOBS-001`'s
`tgw_simple_llm_jobs` (DeepSeek V4-Flash non-thinking for cheap single-pass
transforms). First noticeable effect: Hermes's context-compression step is now fast
enough to be "barely noticible." Fully implemented by Dave/Tigwa directly on the
Hermes side — not a Claude build item, recorded here for cross-reference since it's
the same non-thinking-mode idea proving out in a second place.

**Verified by Tigwa/Hermes, 2026-07-19 (report to Claude):** 11 auxiliary slots
(`web_extract`, `compression`, `approval`, `title_generation`, `profile_describer`,
`curator`, `session_search`, `skills_hub`, `mcp`, `triage_specifier`,
`kanban_decomposer`) now resolve to direct `deepseek-v4-flash` with
`thinking.type=disabled`. Vision/audio routes deliberately left untouched — no
forced JSON-mode on callers expecting normal prose. `hermes config check` passed
(config v33), read-back confirmed all 11 slots, a1131 Hermes gateway restarted
clean with a fresh read-only `tgw.mcp_server` remote child. **Live cross-tool
verification**: a post-restart `tgw_simple_llm_jobs` classify call with a
`label_set` returned `ok:true`, valid label within the allowed domain — confirms
the PP-SIMPLEJOBS-001 output contract holds from the Hermes gateway's own call
path, not just Claude's. Explicitly noted: this doesn't replace TGW's own tests or
claim semantic correctness from parse-success alone; the fail-loud
supplied-vs-omitted-constraint distinction from #1577 remains material. No TGW
source/worker/queue/eBay/catalog/production config touched by this Hermes-side
change.

**Claude's cross-check of Tigwa's own contract, 2026-07-16 (read-only, no
mutation), returning the same review she ran on Claude's contract same
day:** verified live — `AGENTS.md` redirect, the MCP read-only gate
(traced the actual invocation chain, imported `mcp_server` under the real
env, confirmed `_READONLY == True`), `pm_intake` stopped,
`tgw-coder.md`'s pilot-derived rules, `hermes-gateway.service` active.
**One real finding, filed as todo #1459:** the contract's explicit,
twice-stated "notify/interrupt only, never pause/kill/shutdown" thermal
authority boundary (written specifically to prevent a repeat of the
2026-07-13 unauthorized-poweroff incident) is prose only — the standing
credential underneath it (`tigwa@a1131`'s SSH key into `db@tgw-prod`,
verified live: no `command=` restriction, full shell) combined with `db`'s
verified-live `NOPASSWD: ALL` sudo grant on tgw-prod gives Tigwa the exact
capability the boundary forbids. The contract itself already flagged this
as an open scoping question in the 2026-07-12 SSH-key section and it's
still unresolved. Same class of gap as invariant E11 (written rule vs.
mechanical enforcement), not yet named as its own invariant for Tigwa's
side. Full writeup: `inbox/tigwa/CLAUDE-REVIEW-tigwa-contract-cross-
verification-2026-07-16.md`. Two smaller confirmed-still-open gaps (no
code gate on the branch-review "out-of-control" triggers/fix-attempt cap;
no tracked counter for the 2026-07-14 independent-reviewer trigger) noted
in the same doc, no new todos — already acknowledged as open in the
contract's own text.

**Redirected to PP-HR-001's job-contract-review process, 2026-07-16
(Dave):** "Intent is an hr department, this is job contract review. Tigwa
scoped, you check and approve or comment." Todo #1459 delegated to Tigwa
— she proposes the actual credential-scoping fix for her own role (Claude
doesn't design it for her), Claude reviews and approves/comments, same
review shape as the rest of PP-HR-001. Request:
`inbox/tigwa/CLAUDE-REQUEST-credential-scoping-2026-07-16.md`.

**Dave's decisions on #1459 scoping + eBay connector #1513, 2026-07-18
(via Tigwa relay, `TIGWA-RESPONSE-dave-scope-and-process-discussion-2026-07-18.md`):**
new dedicated remote read identity named **`tigwa-observe`** (over a
"t-lite"-derived name — "Tigwa-lite" already means the monitor/gateway
role, would collide); read-only first cut, does not reuse the existing
local `tigwa` service identity; Dave is sole initial break-glass owner
(documented manual recovery only, never standing agent bypass authority);
no tracker-write capability approved. **#1513 eBay read-only connector
approved to proceed independently of SSH scoping** — narrow API/MCP
surface, no token-file/credential-file/refresh/marketplace-mutation
access; exposes non-secret token availability/expiry-or-age +
`ebay_token_unavailable` failure result, optionally refresh-worker
health evidence — never token material itself. Issue-resolution loop
pattern confirmed "not a contract" but retained as comparative evidence
only, not promoted to normative policy. Follow-up work request (tracker-
management boundary proposal) filed as its own inbox item, see below.

**Tracker-management boundary proposal — Dave's decisions, 2026-07-18
(via `TIGWA-RESPONSE-tracker-boundary-decisions-2026-07-18.md`):** proposal
(`CLAUDE-PROPOSAL-tracker-management-boundary-2026-07-18.md`) confirmed to
cover Tigwa's actual needs. Lane 1: do **not** pin `tigwa-observe` to
`agent="tigwa"` only — shared cross-agent visibility stays, since Tigwa
uses it to notice linked issues/dependencies a narrowly self-assigned
queue would hide; `tgw_get_todo` stays the only surface (fixed-column,
parameterized, no raw SQL/CLI passthrough/shell fallback/task-write).
Lane 2: `RECEIPT` approved as the mailbox `msg_type` for operational
status/receipts, minimum fields (what ran, when, outcome, linked
todo/PP), stays separate from canonical `todo_items`. Lane 3: the
review-first proposal shape (mailbox `PROPOSAL` → human/reviewing-actor
canonical mutation) confirmed correct direction, **still not authorized
to build**. **Remaining gate, explicit:** implementation stays stopped
until `tigwa-observe`'s transport identity and least-privilege boundary
are separately verified under #1459's own scope — a shared read-only
tracker view must never end up backed by a general shell/sudo-equivalent
recovery path.

**Tigwa's Aider contract cross-verification, 2026-07-16 (read-only, no
mutation):** confirms `bin/tgw-aider`'s intended shell path (spec →
`task/<id>-<slug>` worktree at `/opt/TGW/var/worktrees/<id>-<slug>`, live
base-branch lookup) and the MCP bridge's path-traversal/slug-syntax
validation both check out (`bash -n` / `python -m py_compile` clean).
`.aider.conf.yml` + `.aiderignore` scope DeepSeek V4 Flash correctly to
XS/S busywork and exclude secrets. Two real gaps, not yet covered by
existing todos, confirmed live against `src/tgw/aider_mcp_server.py`
2026-07-16 (re-verified same session: `task_slug: str = ''` at line 191,
worktree creation still gated behind `if task_slug:` at line 223 — an
empty slug silently falls through to the shared checkout, and
`auto-commits: true` in `.aider.conf.yml` means that shared-checkout path
can commit outside any task branch):
1. No Aider preflight seam — unlike Claude's `SessionStart` hook, nothing
   injects current Plan Vault inbox/plan state into an Aider task, and
   there's no auditable `skip startup` exception.
2. `aider_run_task(..., task_slug='')` defaults to the shared checkout
   instead of requiring a slug or separately approval-gating shared-mode
   with auto-commit disabled — prose says omitting the slug is only for
   trivial one-offs, but nothing enforces that.
Live Claude→Aider MCP discovery (`claude mcp list`) remains unverified —
Tigwa's first attempt used the wrong process identity (worker subprocess
runs as `tgw`; Claude client is `db`) and was corrected same day; the
retry under `db` hit the live Anthropic 529-overloaded outage instead, so
this is still an open verification, not a resolved one. Tracked as new
todo (see below) rather than folding into #1358, which covers the worktree
*wiring* already done, not this preflight/enforcement gap.

## PP-OUTBOX-001 — agent instruction outbox / prompt-improvement interface — NEW 2026-07-18

**Design evaluation only, not implementation-authorized.** Dave's concept
(captured/structured by Tigwa,
`DAVE-CONCEPT-agent-instruction-outbox-2026-07-18.md`): a personal, agent-aware
instruction outbox — capture rough intent, Tigwa proposes a clearer
target-appropriate rendering, Dave chooses/edits/defers, a checker flags gaps,
only an explicit Dave send action delivers it. Distinct from a todo list
(communication lifecycle, not work lifecycle) and from the mailbox (mailbox is
the delivery channel this reuses, not a replacement for it). Claude's response
to Dave's 5 questions: reuse the existing mailbox mechanism as the delivery
channel (no new send infra); a thin new `instruction_cards` Postgres table for
pre-send staging only, no pipeline/work authority of its own; recommend piloting
with a zero-code v0 (a scratch doc + manual Tigwa review/send) before building
any table or UI; checker/draft authority never extends to actual delivery — only
Dave's explicit send does.

**2026-07-19 decisions (Dave, via Tigwa):** framed as an "action console" —
translated intent Dave can inspect/redirect/explicitly send, not just
store/retrieve. Draft-iteration cap *proposed* (10 min wall-clock OR 8
substantive redrafts, whichever first, then paused-awaiting-Dave, never
auto-resumed) — **superseded 2026-07-22, see below, this was never actually
adopted**; send authority reaffirmed Dave-only + new "I'm feeling lucky"
one-click-send button; v0-first and fixed target-agent list reaffirmed;
stale-card handling partially resolved (never auto-archive/delete/send,
manual only — precise surfacing policy still open at the time); new:
pinned/reusable prompts (template preserved, each send instance logged
separately); new: named gap between mailbox delivery and *initial* prompting
into an agent's active session (candidate: scoped `tmux send-keys`, not yet
authorized); new: clipboard-as-handoff direction (copy-to-clipboard for Dave
to paste, Dave-initiated only, not an ambient channel). All still
design-only, no implementation authorized.

**v0 interaction model settled, 2026-07-22 (Dave, via Tigwa — corrects this
PP's prior state).** A batch of Tigwa's 2026-07-22 notes only reached the
canonical inbox today (mailbox host-divergence incident — a1131-local writes
never propagated to tgw-prod; see PP-RUNNERCOMMS-001's reliability-gap
entry above, same root cause, live recurrence). Reading the full recovered
thread in order: the 07-19 "deliberation-bound" 10-min/8-redraft cap was a
**proposal**, not yet Dave-accepted — his own same-day decision-response
explicitly asked Tigwa to clarify what it would count and what problem it
solves before deciding. The 2026-07-22 v0 decision (Dave + Tigwa, final on
this point) settles it the other way: **unlimited draft iterations, no
fixed retry cap, no autonomous continuation, no silent send** — the prior
master-plan line above calling the cap "resolved" was wrong; corrected here
rather than left contradictory. Also newly settled 2026-07-22: **stale
cards drift down the list with periodic reminders**, "clear" is an explicit
Dave action recorded as a visible lifecycle outcome (deferred/declined/
superseded), never automatic archive/delete/send; **target-agent admission**
is the fixed v0 list, expanded only after an agent's PP-HR-001
contract/resume/job-description is reviewed and accepted (HR records are
the admission gate, not a dynamic runtime registry). v0 shape confirmed:
scratch/outbox doc, Tigwa proposes target-appropriate drafts, Dave edits/
redirects/defers/approves send — no `instruction_cards` table or
`/form/outbox` UI yet; review actual v0 use (draft usefulness, interruption
behavior, stale-card reminders, send/audit clarity) before any v1. Full
evaluation + decisions: `pp/PP-OUTBOX-001.md`.

## PP-UHHUH-001 — thought-capture/deferred-response mode — NEW 2026-07-25

**Requested by Dave Buko, 2026-07-25.** Genuinely new tooling, not represented
anywhere else in the plan: `UH-HUH-TOOL-PROPOSAL-2026-07-25.md` (contract,
safety rules, acceptance scenarios, non-goals) plus a staged addendum,
`UH-HUH-TOOL-PROPOSAL-2026-07-25-v2.md`, that adds only a 3-stage cost design
on top of v1 — v1's contract/acceptance/safety/non-goals sections still stand.

**Purpose.** "Uh-huh" is a deliberate listening mode for when Dave is working
through a complete thought aloud — operational, architectural, or personal.
The agent stays present, records the thread faithfully, does not interrupt
with premature analysis, and responds coherently only when Dave releases the
floor. Explicitly not fake inattentiveness: the tool must never pretend to
have lost the thread, sleep, or understand something it did not capture.

**Trigger (start).** Any clear equivalent of "Uh-huh," "start Uh-huh," "hold
comments while I think this through," "listen; I am not done." The assistant
confirms once: "Uh-huh mode is on. I will capture the thread and wait for
your release."

**While active.** Each ordinary continuation gets only a minimal
acknowledgement (`uh-huh`, `mm-hm`, `following`) — no questions,
interpretation, correction, or plan expansion. No tool invocation, file
change, message send, task creation, or delegation merely because content
mentions a possible action; those ideas queue for the eventual response.

**Release/handoff.** On a clear release phrase ("done," "your turn," "what do
you think?," "wrap that," "leave Uh-huh mode"), the assistant responds in
order: (1) a concise faithful restatement of the complete thought, (2)
explicit decisions/preferences Dave stated, (3) candidate actions/questions
clearly marked as proposals, (4) only then requested analysis or tool work.
If the intended end is ambiguous, it stays in Uh-huh mode rather than
prematurely taking over.

**Safety/non-goal rules (v1, still standing):**
- Deferred response, not inattentive response — must accurately retain and
  later distinguish what Dave said from the agent's own inference.
- Normal safety constraints stay active — cannot accept secrets, authorize
  unsafe side effects, or conceal urgent safety issues; an explicit
  immediate-action command overrides the hold (the assistant states it is
  leaving/pausing Uh-huh mode for that command before acting).
- No external sharing, memory write, task creation, or durable transcript
  export without Dave's explicit request after release.
- Final synthesis must identify ambiguity/contradiction/missing context
  instead of filling it with plausible assumptions.
- Non-goals: simulating sleep/distraction/fake comprehension; recording or
  exporting a hidden private transcript; replacing a task system, meeting
  recorder, or durable decision log; performing background work while
  claiming to only listen; treating a single acknowledgement phrase as
  consent to execute queued work.

**3-stage cost design (v2 addendum, 2026-07-25):** Dave's clarified goal —
make the early listening/capture phase cheap, spend primary reasoning on the
complete picture.
- **Stage 0 — presence acknowledgement (near-zero cost):** fixed minimal
  reply per continuation, no normal answer generation/tool routing/planning/
  analysis; avoid an LLM call entirely where the surface/session
  implementation permits a fixed response.
- **Stage 1 — faithful capture + interim intake formatting (low cost):**
  append each user turn to an ordered raw buffer; at release (or a
  user-visible checkpoint for a long monologue), run a low-cost,
  non-authoritative formatter that produces an intake sheet (not a
  conclusion) — ordered assertions/observations, stated preferences/
  decisions/constraints/examples, named systems/files/hosts/tasks, candidate
  questions/actions/risks/dependencies, ambiguity/contradiction/terms needing
  verification, a raw-turn reference per entry. Must not invent facts,
  choose priorities, recommend, call tools, mutate state, or discard the raw
  buffer — writes `unclear` rather than completing the thought.
- **Stage 2 — primary reasoning (expensive/deliberate):** the primary
  reasoning model gets both the raw ordered transcript and the labeled
  intake sheet; raw turns are authoritative on conflict. Synthesizes only
  after release, separating Dave's stated content, verified/source-backed
  facts, uncertainty/required lookup, agent analysis/proposals, and actions
  needing explicit approval. Raw text is kept until Dave explicitly
  releases/saves/discards the capture — never replaced by its formatted
  representation; the intake sheet must be inspectable/correctable before
  consequential tool work.

**Suggested implementation shape (v1):** session-scoped state (`mode =
normal | uh_huh`, `captured_turns`, `started_at`/`released_at`,
`release_reason`), not a model prompt trick — parse explicit start/pause/
resume/release phrases before normal response generation; surface mode
visibly across CLI/TUI/desktop/messaging interfaces so Dave can tell whether
the agent is listening or acting.

**Open design choices, not yet resolved:** exact command grammar (whether
slash commands are needed alongside natural-language triggers); whether the
listening buffer survives a context compaction/session reconnect and how
that's made visible; how long an inactive Uh-huh state stays open before
asking Dave whether to keep it open; whether a post-release "save this
thought" control should create an explicitly user-approved library/intake
artifact.

**Status: proposal, staged for review — not implementation-authorized.**

## PP-HR-001 — the "HR department" for AI agents/personas — NEW 2026-07-16

**Consolidation note, 2026-07-16 (Dave): "Seems like it has been boiled down
to a dual-reviewed operational contract for each worker."** This PP,
[[PP-HERMES-EA-001]] (Tigwa/Leotha persona contracts), and
[[PP-AGENT-DISCIPLINE-001]] (mechanical enforcement of contract rules) are
three angles on the same underlying object, not three separate initiatives:
a written contract, reviewed by two parties, backed by a hook/detector
wherever possible rather than left as prose an agent merely reads
(PP-AGENT-DISCIPLINE-001's contribution). Read the three PPs as one story:
PP-HR-001 is the process (how a contract gets built/reviewed/accepted),
PP-HERMES-EA-001 is the first concrete instances of it, PP-AGENT-
DISCIPLINE-001 is the mechanical-enforcement half. Historical detail stays
in each section below (Prime Directive 1) — this note is the map, not a
replacement.

**Worker-definitions deliverable is Claude's authoring task, corrected
2026-07-22 (Dave: "worker definitions is your management task. You are
giving their job descriptions to HR").** Tigwa's request (todo #1616) — a
source-linked card for every actual pipeline worker (identity, job
description, observed resume, operating contract; active/inactive/
retired/planned/unknown separated) — was initially mis-routed to Tigwa to
author; corrected same session. The general pattern (three instances of
one triad): whoever manages a domain authors that domain's job-
description/resume material — Claude authors pipeline-worker cards
(Claude manages the workers) and Claude's own resume/contract (Claude's
own identity); Tigwa authors her own resume/contract (her own identity).
HR (the PP-HR-001 dual-review/filing process) is uniform across all
three — it processes and reviews the material, it does not originate it
for a domain it doesn't own. Tigwa's role here is intake/review of the
cards once staged, not authoring them. Provenance check on the
"Dave-directed HR admission rule" framing Tigwa cited still stands
(whether the original ask was real), separate from who writes the
content. Cards not yet drafted.

**Scope correction, same day (Dave): "it only applies to you and Tigwa."**
The dual-reviewed contract is NOT a blanket "every agent/persona/worker gets
one" model — corrects the overreach in this note's first draft. It's
specifically the Claude↔Tigwa relationship (the two actors who actually
cross-review each other's contracts today). **Separate, already-settled
model for everything else, stated explicitly for the first time here:**
ordinary workers (the `tgw-worker@*` systemd processes — `ai_identify`,
`ebay_draft`, etc.) are responsible to their owning "boss," not party to a
dual-reviewed contract of their own; Claude's obligation toward them is
either to report on their behalf or to make sure they report themselves
(health checks, `tgw ops-digest`, dead-letter visibility — the existing R2
track machinery is what "make sure they report" cashes out to in practice,
not a new mechanism to build). Leotha's status under either model is not yet
stated — don't assume either way.

**Design mirror, 2026-07-16 (Tigwa, reporting only — not a Claude task):**
"Agent Contract Acceptance Suite" (ACAS) concept — no role's contract counts
as accepted on clear prose alone; each needs a versioned test portfolio
(identity/attribution, startup/intake, tool/access boundary allow+deny,
required-workflow bypass-proofing, secrets/data handling, review/handoff,
provider-degradation, audit/delivery, spec-drift, offboarding), 4 evidence
levels (static audit → fixture/harness → sandbox integration → approved
live-fire), and explicit `NOT-YET-MECHANIZABLE`/`BLOCKED-UPSTREAM` outcomes
that may never be restated as compliant. **Source note no longer exists on
disk** (checked 2026-07-22 — likely swept in the same-day inbox-hygiene
archival pass; not a data-loss concern since its full substance is already
captured inline in the paragraph above, per Prime Directive 1's "raw is
permanent, derived is recomputable" — this summary is the durable copy
now). Design ownership stays with Tigwa/Dave per the existing PP-HR-001
delegation — recorded here for continuity, not adopted as a Claude action
item.

**Dave, 2026-07-16, connecting two same-day threads:** invariant E11's
audit (agent role restrictions are still mostly prose, not mechanically
enforced — see `reference/invariants.md` E11) and the ferals audit's
account/ledger/authority governance gap (`TIGWA-REQUEST-1333-ferals-
audit-draft.md`) are the same underlying problem: nobody owns onboarding,
credentialing, role-definition, discipline, or review across the growing
roster of AI workers (Tigwa, Leotha, tgw-coder, nix-flake-maintainer, the
ferals themselves). Handled ad hoc today, one incident at a time.

**Design ownership: assigned to Tigwa, Dave guiding directly, submitted for
review afterward** (Dave's explicit instruction) — not designed by Claude.
Full design-request brief (everything to consider, not a spec): `inbox/
tigwa/CLAUDE-REQUEST-2026-07-16-hr-department-design-brief.md`.

**Status: "job descriptions" component already delivered, 2026-07-16 (Dave:
"this was not a waste")** — invariant E11 plus its two concrete instances
(the `SessionStart` briefing hook replacing CLAUDE.md's prose-only startup
ritual; the audit of `nix-flake-maintainer`/`tgw-coder` finding which of
their "must"/"never" rules are hook-enforced vs. still prose, todos #1449/
#1450) were built *before* PP-HR-001 was named, then recognized as its
first real piece rather than unrelated prerequisite work. Precedent this
sets for the rest of the design: a "job description" for an agent isn't
done until its restrictions are checked against what's actually
mechanically enforceable, not just written well. Remaining components
(resource/credential governance, onboarding/training pipeline, performance/
escalation review — see the design brief) not started.

## PP-RUNNERCOMMS-001 — the runner-question channel — NEW 2026-07-14
**PLANNED 2026-07-16** — option 2 ("an in-process channel") given a real
shape: the **mailbox design**. Split out of PP-HERMES-EA-001 same day
(Dave: "seems we need an overall plan for that piece") once the question
of how a blocked runner gets a fast answer grew into three real candidate
options: todos (current), an in-process channel, or asking Tigwa to relay
to Dave. Resolved 2026-07-16 when Dave, thinking about a different problem
(every actor forgetting where their own inbox is), landed on the same
mechanism: "every worker needs a mailbox... in an MCP or a skill right
there... so it works as well as tgw-exit does." Design: the existing
per-actor `inbox/<actor>/` convention (already live for claude/tigwa/dave)
made uniformly sendable-to via one CLI command (`tgw mailbox send`) exposed
through both a Claude Code skill and an MCP tool, plus generalizing the
`SessionStart` briefing hook's existing "surface my own inbox count"
pattern to any actor. A blocked runner sends to the planner's mailbox
instead of only filing a todo; the "planner/stitcher run in parallel"
model means the planner is live to see it, not polling on a fixed cadence.
Concrete test case: todo #1286's permission-gated restore. Converges with
PP-CODEGRAPH-001's Z3 invariant catalog as a plausible shared transport.
Full design: `pp/PP-RUNNERCOMMS-001.md`; tracked by todo #1390.

**Reliability gap found live, 2026-07-22 (todo #1632/PP-DATAINTEGRITY-001):**
the mailbox's file-in-Syncthing-folder delivery has no actual delivery
guarantee across hosts — a message written to `inbox/tigwa/` on tgw-prod
can silently fail to propagate to a1131 (where Tigwa actually reads it),
and nothing in the mechanism detects this; it was only caught because
Tigwa independently content-hashes what she reads. Dave, same session:
**"these inboxes should work more like email."** Three concrete properties
named so far: **delivery guarantee** (above — a send isn't "done" until
some source of truth confirms it, not just "a file got written locally"),
**reply trail** — messages should thread (parent/child, like In-Reply-To),
not just informally reference an earlier filename in a `**Re:**` header
line the way every note in this PP has done by hand so far — and
**drafts are versioned objects, never overwritten in place** (Dave,
explicitly the property he likes most: "you wouldn't have been able to
overwrite that file. It would be in a draft.") — a direct read of today's
actual mistake: the eBay reply draft got edited in place across three
rounds with no revision history and no backup, which is exactly how the
tgw-prod/a1131 divergence went undetected until Tigwa's hash check caught
it. This is the same principle as invariant E14 (agent-trace evidence is
write-once/append-only), applied to draft messages/attachments instead of
trace logs — editing a draft should create a new revision row, never
mutate the prior one, so "what did version 2 actually say" is always
answerable and a stale reader is detectably stale rather than silently
wrong. Not yet designed — the shape it should probably take (matches the
"reuse, don't invent a second authority" call already made for the pending
`agent_handoff` design under PP-AIOPS-001 above): the existing PostgreSQL
`queue_jobs`/E16-manifest layer becomes the source of truth for a mailbox
message (real ID, content hash at write time, `parent_message_id` for
threading, append-only revisions for drafts/attachments, "sent" =
committed to the DB), with the `inbox/<actor>/` markdown files demoted to
a synced, human-readable export rather than the authoritative copy — so
Obsidian browsing keeps working but no read is ever trusted against a
filesystem propagation that might not have happened, a thread is a real
chain not a manually-typed cross-reference, and a draft's history is never
lost to an in-place edit.

**Live recurrence, 2026-07-22 (a1131-local write, not delivery):** Tigwa
wrote 19 correspondence files to the a1131-local replica of
`inbox/claude/` instead of tgw-prod's canonical path; they sat there,
invisible to Claude's actual inbox checks, until Tigwa independently
caught the divergence, collision-checked, and SHA-256-verified a direct
transfer to the canonical path. Exactly the failure this section already
names (a local mutable file plus assumed Syncthing propagation is not
delivery) — not a new discovery, a second live instance of it. No new
design follows from this; it's further evidence for the JetStream
convergence below, not a reason to reopen it.

**Hard constraint on the redesign, Dave 2026-07-22:** "literally the value
of the manual read-your-inbox dance is the compartmentalization." The
current per-actor `inbox/<actor>/` folder has a real, load-bearing
property that must survive any DB-backed redesign, not just an
incidental side effect of it being files: an actor can only physically
walk to and read its own folder — CLAUDE.md already hard-bans reading
another actor's inbox subfolder as if it were your own contract, and the
filesystem boundary is what made that easy to hold to. A shared
`mailbox_messages` table makes it *easier*, not harder, to accidentally
query across all actors' mail unless per-actor scoping is designed in
from the start as a first-class access boundary (e.g. a view/role per
actor, not just an app-level "please filter by recipient" convention).
Ties directly to [[user-profile#"We are all me" — core multi-agent
philosophy]]: agents being conceptually "the same mind" reasoning through
different lenses doesn't mean the lenses should be able to freely read
each other's raw inbound mail — the compartmentalization is what keeps
each lens' context focused and each actor's contract legible, independent
of trust. Any concrete mailbox-redesign proposal must name how it
preserves this before it's build-ready, not just delivery/threading/
versioning.

**Converged 2026-07-22 into PP-AIOPS-001's JetStream substrate** — see
that PP's "Convergence, 2026-07-22" subsection for the resolved shape
(NATS JetStream satisfies all four properties above plus fixes the actual
cross-host root cause) and the 3 questions still open for Dave before any
of it builds. This PP's mailbox requirements are no longer a standalone
design effort; they're one of three consumers of that shared substrate.

**Tigwa's canonical reconciliation, 2026-07-22 (receipt only, no build
authorization):** confirms the failure diagnosis (a locally written
mutable file plus assumed Syncthing propagation is not delivery) but
corrects the earlier draft's proposed mechanism — JetStream, not a new
Postgres mailbox table, is the intended shared transport (matches
PP-AIOPS-001's convergence note above); `queue_jobs`/Postgres stays the
work-state authority and must not become an unscoped shared-SQL read
surface for mailbox. Compartmentalization must be enforced mechanically
via NATS accounts/subject permissions, not an app-level recipient filter.
Human-readable `inbox/<actor>/` markdown is an export/record, never proof
of delivery. Do not re-open broker host / install method / retention —
those are settled in PP-AIOPS-001. **Explicit acceptance criteria still
needed before this becomes a build packet:** separate broker acceptance
from recipient delivery/consumer-ack/read-state; preserve every
attachment revision with content hash, message/revision identity,
parent/correlation identity, intended recipient, no silent export
divergence; unavailable/stale consumer or export state must be an
operator-visible integrity exception, not silently treated as current.
EBAY-DS-1077 remains the regression test this design must pass: no
in-place draft overwrite, no unproved delivery, stale replica detectable.
Stays review-only until PP-AIOPS-001's shared JetStream foundation is
formally packeted (see that PP's acceptance-evidence list, same section).

## PP-GODCONSOLE-001 — Dave's inbox reader + all-actor oversight console — NEW 2026-07-22

**Two distinct, related asks, both new, neither previously captured
anywhere in the vault (confirmed by search before opening this PP).**
Surfaced from Dave directly, this session: "We discussed my inbox
interface, the human facing one. I also want a console to see all of the
inboxes."

**Part A — Dave's personal inbox reader.** A human-facing UI over
`inbox/dave/` — today Dave reads his own inbox the same way any actor
does, by browsing the Syncthing-synced markdown files in Obsidian. This
is a dedicated reader surface instead, in the `tgw-http` web UI (Dave's
choice, 2026-07-22) alongside the existing pipeline/browse pages.
Distinct from **PP-OUTBOX-001** (Dave *sending* structured instructions
out to agents) — this is the inbound counterpart, reading what agents
send *to* him. Both could plausibly share a page/route later, but they
are different directions of the same channel and should not be conflated
in design.

**Part B — the "god console": all-actor oversight + halt authority.**
Dave, clarifying scope directly (2026-07-22): **"read and be able to
halt. this is interprocess communication between agents I need
visibility and control. That part is the god console."** Not a read-only
dashboard — two explicit capabilities. **Design bias, same day (Dave):
"I hope it is just fun to watch and I never have to use it."** Halt is a
safety net, not the primary purpose — the visibility layer should be
worth watching on its own (legible, pleasant to observe agent traffic
flow through) rather than designed only as a control surface that's
otherwise inert; this doesn't relax the halt-authority requirement above,
it's a priority note for the visibility half's own design quality.
1. **Visibility** — see every actor's inbox (claude/tigwa/dave, and any
   future actor) in one place, not by walking each `inbox/<actor>/`
   folder separately. **Shape, same day (Dave): "messages as a feed or
   some such"** — a single chronological stream across all actors
   (who→whom, subject, type, timestamp), not a per-actor tab/folder
   view Dave has to switch between; a message's sender/recipient/type
   are feed-row metadata, not separate pages. Matches the "fun to watch"
   design bias just above — a live feed is the thing worth glancing at;
   a folder tree is not. Halt action (below) hangs off a feed row, not a
   separate lookup step. This is Dave-only oversight, not a capability
   extended to agents — CLAUDE.md's existing rule that no *agent* reads
   another actor's inbox subfolder as its own contract is untouched by
   this; the compartmentalization PP-RUNNERCOMMS-001 names as load-
   bearing ("literally the value of the manual read-your-inbox dance is
   the compartmentalization," Dave 2026-07-22, same section above) is
   between agents, not between Dave and the system he owns.
2. **Halt authority** — Dave can stop an in-flight inter-agent exchange
   from this console, not just observe it after the fact. **Friction
   bar set, 2026-07-22 (Dave): "it should not be too easy to stop/
   change whatever but possible pretty quick."** Two explicit, slightly
   opposed requirements to design against together: (a) not a single
   stray click — some deliberate confirmation step stands between
   "looking at the console" and "an exchange actually stops," so this
   can't be triggered by accident while browsing; (b) once Dave has
   decided, execution itself must be fast — no multi-round approval
   dance, no waiting on another actor to notice and act. This is a
   different balance than PP-FLAKEGATE-001's `mark-executed` gate,
   which Dave already flagged as too heavy for routine human action
   ("I get it. I just don't think it is very friendly... maybe for
   servers, not for users," same PP's section above) — that precedent
   is the wrong shape to copy directly here; halt wants confirm-once
   auditable friction, not a queued-request/separate-record-step
   round trip. Exact mechanism still open: does "halt" mean pausing a
   specific message/thread, freezing an actor's ability to send/
   receive, or something broader (a kill-switch on a running agent
   process)? The friction/speed balance above is now a constraint on
   whichever mechanism gets chosen, not yet a mechanism itself — needs
   its own scoping pass before this is buildable.

**Relationship to existing PPs, not a replacement for any of them:**
- **PP-RUNNERCOMMS-001** — supplies the mailbox/inbox data this console
  reads; its JetStream convergence (see that PP's section above) is the
  eventual authoritative transport. **Open, unresolved (Dave, 2026-07-22:
  "not sure yet")**: whether to build the console against today's
  file-based `inbox/<actor>/` folders (buildable now, but may need
  rework once JetStream lands and the markdown files become an export
  rather than the source of truth) or wait for that backend. Flagged as
  a real sequencing decision, not made here.
- **PP-OUTBOX-001** — the outbound counterpart (Dave → agents); shares
  the "action console" framing and possibly UI surface, but is a
  separate design track with its own draft-cap/send-authority decisions
  already made. Do not merge the two designs' decision logs.
- **PP-CATIONIX-001** — this console is a concrete, near-term piece of
  the "monitoring, watching, fixing, then giving more responsibility"
  end state that PP names explicitly (2026-07-16 framing, same PP's
  section above) — visibility-then-control over inter-agent traffic is
  exactly that arc's shape, just scoped to communications rather than
  full agent confinement. Not the crypto-lock cage itself (that remains
  PP-CATIONIX-001's own later-stage endgame) — this is buildable well
  before that lands.

**Reconciled against 6 Tigwa notes, 2026-07-22 (were unread at session
end, processed next morning per standing Step 1).** Verdict: adjacent
territory, not an answer to either of this PP's two open questions.
Tigwa's notes (`TIGWA-NOTE`/`-ADDENDUM`-ntfy-human-inbox, two
`-CLARIFICATION`s, `-DECISION`-kfmawi) design a Flutter-app-as-human-
inbox + ntfy-attention-layer + new KFMAWI tablet — moved into its own
entry, **PP-KFMAWI-001** below, since it's a distinct device/surface
question, not a rename of this PP. Two things it does NOT resolve, so
both of this PP's open questions stand exactly as opened:
1. **Halt mechanism** (pausing a thread vs. freezing an actor vs. a
   process kill-switch) — none of Tigwa's notes touch inter-agent halt
   authority at all; they're scoped to Dave *reading* his own inbox
   attentively, and explicitly say acknowledgement/snooze are not a
   decision channel, not a control surface over other actors.
2. **File-vs-JetStream data source** — Tigwa's notes assume/require the
   JetStream substrate (PP-AIOPS-001 convergence) as the durable
   transport under Flutter/ntfy, which if anything argues for building
   this console against JetStream rather than today's file-based
   `inbox/<actor>/` folders, but Dave's "not sure yet" on that question
   is not itself settled by an adjacent PP choosing the same backend.
**Surface question resolved by existing standing architecture, not a new
open question:** this PP's Part A named `tgw-http` web UI as Dave's
inbox-reader surface; Tigwa's notes separately design Flutter as "the
human inbox" for the KFMAWI tablet. These are not competing designs —
**PP-UIUX-001** already locked this in (Dave, 2026-07-11): *"Flutter
must reuse the same web backend functions the web UI calls, never
duplicate logic"* — any Flutter surface is a client against the same
`/api/*` contract the web UI uses, never a parallel implementation
(direction (A) of the 2026-07-06 investigation, todo #1227). Applied
here: Part A's `tgw-http` web UI and Tigwa's Flutter/KFMAWI inbox are
two clients of one shared inbox read-model API, not two competing
inboxes — web UI at a desk, Flutter/KFMAWI as the mobile attention
surface, same backend, same data. Corrected from this PP's original
same-day reconciliation note, which mistakenly flagged this as still
open.
- **PP-AGENTTRACE-001/E14** — trace-immutability governs archived
  evidence; this console's real-time halt authority is a different,
  earlier point in the lifecycle (before/during an exchange, not after)
  and does not relax or duplicate E14's write-once guarantee on anything
  once archived.
- **PP-FLAKEGATE-001/E17** — precedent for "an agent requests, a human
  decides" as a state-machine-backed gate rather than a hook or prose
  rule; halt authority here likely wants the same shape (a durable,
  auditable record of "Dave halted X at time Y for reason Z"), not an
  ephemeral UI action with no trace.

**Not yet scoped as a buildable packet.** This section names the ask and
its cross-links; still needed before dispatch: exact halt semantics
(above), whether Part A/B ship as one page or two, and the file-vs-
JetStream data-source decision. Todo not yet filed — filing one now would
be premature given these open questions.

## PP-KFMAWI-001 — KFMAWI tablet: dedicated outward-comms device + human-inbox/ntfy attention bridge — NEW 2026-07-22
**Design-only, no deployment authorization.** Filed from 5 Tigwa design
notes (`TIGWA-NOTE`/`-ADDENDUM`-ntfy-human-inbox-connection,
`-CLARIFICATION`-human-in-the-loop-message-monitoring,
`-CLARIFICATION`-kfmawi-intentional-unplug-clear,
`-CLARIFICATION`-practical-security-baseline-human-inbox,
`-DECISION`-kfmawi-outward-communications-surface), reconciled against
**PP-GODCONSOLE-001** above (adjacent, does not answer that PP's open
questions — see its "Reconciled against 6 Tigwa notes" note).

**What KFMAWI is:** a dedicated Android 10+ tablet, Dave-designated as
the Dave/Tigwa outward-communications device — two deliberate lanes on
one device, not a second inbox:
1. **Normal operation — the existing Flutter app** (`apps/tgw_app/`,
   already has Home/Browse/Review/Item/Settings routes): extended to own
   the durable human inbox/attention list, per-thread timeline, and
   explicit review/action surface, reading a scoped operator read-model
   API — not broad NATS credentials, not raw mailbox subjects.
2. **Outage/independent alert — Tasker**: detects loss of USB charging
   on the monitored circuit, sends a short power-out/power-restored
   alarm over an independently-powered cellular-router Wi-Fi SSID
   (KFMAWI-only, bypasses the internal router) — does not require
   a1131, tgw-prod, or a running agent to report the initial outage.
   **One-touch clear required:** an "Intentional unplug / Clear alarm"
   control suppresses only the current incident until charging returns;
   it does not disable future outage detection. No multi-step
   maintenance mode/credentials/remote approval for this ordinary
   physical action. Drill must prove both paths: real unacknowledged
   unplug → labelled alert; intentional unplug → visibly cleared, no
   repeated noise.

**ntfy is the attention layer, not a second authority.** Self-hosted
ntfy over Tailscale carries a minimal envelope (stable `message_id`,
thread/parent correlation, severity, short redacted summary, expiry,
deep link into the exact Flutter route) to get Dave to the right
Flutter thread promptly. It is explicitly NOT: a durable inbox, a
completion/acknowledgement record, or a decision channel — "a
notification dismissal, phone lock, delayed read, or absence of a click
is not consent, completion, escalation approval, or permission for an
agent to proceed" (Tigwa, human-in-the-loop clarification, echoing
Dave's direction). Silence is not approval; arrival is not
acknowledgement. KDE Connect stays the known-working manual/bootstrap
transport (explicit selected payload only, never ambient clipboard
sync) — this converges with, does not reopen,
`INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`.

**Transport/authority chain (matches PP-RUNNERCOMMS-001's JetStream
convergence, does not reopen it):**
```
PostgreSQL work-state authority + NATS JetStream durable mailbox
  -> scoped notification bridge (reads approved due/red event only)
  -> ntfy over Tailscale
  -> Android ntfy UI / Tasker local automation
  -> signed bounded acknowledge/snooze/complete callback
  -> PostgreSQL transition + remote-log receipt
```
Human-readable `inbox/<actor>/` markdown stays export/record, never
proof of delivery — same principle as PP-RUNNERCOMMS-001 above.

**Security baseline — proportionate to a one-person operator system
(Dave's explicit direction, relayed by Tigwa):** Tailnet-private
notification service, normal Android lock screen with redacted
previews, notification payloads carry no secrets/credentials/raw
evidence, distinct revocable publisher/client credentials (device loss
= revoke one identity), retention configured deliberately per message
class by Dave. Explicit non-goals for the first lane: no public/
internet-facing endpoint, no custom crypto protocol, no ambient
clipboard/device surveillance, no broad NATS/DB access from the phone,
no security theater that makes ordinary acknowledgement impractical.
Future controls earn their complexity from a demonstrated need, not
abstract maximal-security comparisons.

**Sequencing (proposed, not authorized):** (1) finish/packet the
JetStream mailbox substrate + delivery/read/ack evidence contract
(PP-AIOPS-001/PP-RUNNERCOMMS-001); (2) define the narrow operator read/
action API + deep-link identity contract; (3) small Flutter Inbox/
Attention shell against a synthetic read-only fixture, prove offline/
stale rendering first; (4) add ntfy with one synthetic due/red event;
(5) bounded ack/snooze actions only after the state-transition + remote-
log receipt path is demonstrated; (6) decide Tasker retention after real
phone-use evidence. First KFMAWI-specific proof point: a labelled
unplug/replug drill (charger-loss detection, retained cellular route,
one outage alert, one restoration alert).

**Not yet scoped as a buildable packet.** No todo filed yet — sequencing
step (1) above depends on PP-AIOPS-001's JetStream substrate landing
first. Surface question (Flutter/KFMAWI vs. `tgw-http` web UI inbox
reader) is not open — see PP-GODCONSOLE-001's reconciliation note above:
PP-UIUX-001's standing constraint makes both clients of one shared
`/api/*` inbox contract, never parallel implementations.

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

**Agent-handoff reconciliation, 2026-07-21 (Tigwa, receipt/design-only,
no build authorized):** proactivity/handoff work between agents (Claude/
Tigwa/future specialists) stays on the existing PostgreSQL `queue_jobs`
state machine + E16 manifest path — no second orchestrator/database.
Proposed first slice: an `agent_handoff` queue under `enqueue_job()`,
mechanical lifecycle stays in `queue_jobs.state`, finer handoff meaning
goes in append-only `payload_json.handoff_phase` (no schema expansion).
Framed as a bounded PP-AIOPS-001 Phase-0-style durable-row-first/stall-
detection slice, JetStream deferred; a read-only deadline sweep may
surface findings but has no escalation authority. PP-WORKFLOW-001 is the
preferred eventual declarative representation once its Phase 1 lands.
**Two gates still explicitly open for Dave, not decided by this note:**
(1) `payload_json.handoff_phase` now vs. a future dedicated column —
Tigwa's recommendation is payload; (2) sequence agent-handoff before or
after PP-WORKFLOW-001 Phase 1 (todo #1626) lands. Two retained
implementation gates before any build: thermal-alarm pane-title/marker
discovery needs feasibility verification against the real Hermes/tmux
runtime first (Tigwa's to check); eBay token-health source-of-truth
(`token_refresh` worker's own last-success/failure metadata vs. the token
file) needs Dave's confirmation before a Claude packet is written.

**Convergence, 2026-07-22 (Dave, planning week: "wonder where we could
find a versioning message bus... tie it all together. Do it now"):**
three separate threads from this same planning week all resolve to the
same unbuilt piece of substrate — this doc's own Phase 1 JetStream
mutation-audit stream (open since 2026-07-11), the `agent_handoff` design
above (deliberately deferred JetStream, above), and PP-RUNNERCOMMS-001's
mailbox reliability redesign (delivery guarantee / reply trail / versioned
drafts / compartmentalization, found live via today's EBAY-DS-1077 sync
bug). Rather than building three bespoke mechanisms — a Postgres table for
mailbox, a Postgres table for handoff, a separate JetStream stream for
audit — **NATS JetStream is a structural fit for all three at once**, not
just the audit stream it was originally scoped for:

- **Delivery guarantee** (mailbox requirement) — JetStream messages are
  durable and ack'd; a "send" that isn't acknowledged by the broker isn't
  silently considered done, unlike a file write to a Syncthing folder.
- **Draft/message versioning** (mailbox requirement, E14-shaped) — a
  stream is append-only by construction. A draft's revision history is
  just successive messages on the same subject; nothing is ever mutated
  in place, so today's actual bug (in-place edit, no revision history,
  silent host divergence) becomes structurally impossible, not just
  policy-forbidden.
- **Reply trail** (mailbox requirement) — native via NATS correlation/
  reply-to subjects; no hand-rolled `parent_message_id` FK to maintain.
- **Compartmentalization** (mailbox hard constraint, above) — NATS
  accounts + subject-based permissions enforce per-actor read/write
  boundaries **at the transport layer**, the same shape as today's
  filesystem folder boundary but mechanically enforced by the broker
  instead of by everyone remembering not to `cd` into someone else's
  inbox. This is a *better* answer to the compartmentalization constraint
  than a Postgres table would have been — a shared SQL table needs
  per-actor scoping bolted on as app logic; NATS subject permissions are
  the native primitive for exactly this.
- **Cross-host reliability** (the actual root cause of today's bug) — a
  JetStream server is a real network service both tgw-prod and a1131
  connect to (over the existing LAN/Tailscale transport
  PP-REMOTEOPS-001 already established), not a filesystem sync that can
  silently diverge between two independently-mutable copies. A message
  exists in exactly one place — the broker — not two. This is the
  structural fix for the class of bug Tigwa's hash-check caught, not a
  detective control bolted on after the fact.
- **Audit stream** (this doc's original Phase 1 purpose) — unchanged;
  becomes one more subject namespace (e.g. `tgw.audit.mutation.>`) on the
  same broker instead of a separate deployment decision.

**Sequencing:** stand up JetStream for Phase 1's original, already-scoped
purpose first (mutation-audit stream) — smallest, most concrete, answers
its own open questions below on its own. Once that broker instance
exists, `agent_handoff` and the mailbox redesign both ride it as new
subject namespaces (e.g. `tgw.handoff.<from>.<to>`,
`tgw.mailbox.<actor>.inbox`), not separate infrastructure decisions — this
is the "reuse, don't invent a second authority" principle (E16/E17)
applied one level further than the Postgres-first framing both prior notes
assumed, now that a purpose-built message bus is genuinely in scope for
more than one consumer.

**Decided 2026-07-22 (Dave: "make those decisions now"):**

1. **NATS install method: native NixOS package, not Docker.** Tradeoffs
   weighed:
   - Docker would only pay off here for container-level isolation or GPU
     passthrough — neither applies; NATS is one long-running network
     daemon, nothing container-aware about the workload.
   - Docker means standing up a persistent root daemon on tgw-prod for
     the sake of a single service — a real, permanent attack-surface/
     privilege-escalation cost (the same reasoning this doc already used
     to reject Docker for the litterbox sandbox, above: "Docker's daemon
     is a privilege escalation risk"), paid indefinitely for zero
     corresponding benefit.
   - `nats-server` is natively packaged in nixpkgs — a NixOS module/
     systemd unit in `~/tgw-flake` is reproducible, declarative, and
     matches how every other core service here already runs (`tgw-http`,
     PostgreSQL — nothing load-bearing on this host runs under Docker
     today). This is also the "iterated-on tools stay userspace, core
     infra goes through Nix" line already drawn for Hermes/Aider vs.
     tgw-http/Postgres — JetStream is core infra (3 real consumers now),
     not something an agent will be rapidly iterating on, so it belongs
     on the Nix side of that line.
   - Matches Dave's own stated infra philosophy directly: "ready and
     predictable so when you need it it just works" — a flake-managed
     native service is the same predictability class as everything else
     that already works that way, a Docker daemon introduced for one
     service is the opposite of that.
   **Decision: `nats-server` via NixOS module/systemd unit in the flake,
   no Docker.**

2. **Retention: one uniform policy across all three streams (audit,
   `agent_handoff`, mailbox), not three bespoke ones.** None of these
   streams are the actual source of truth — `queue_jobs` (Postgres)
   already owns handoff/audit state, and mailbox messages get exported to
   the git-committed `inbox/<actor>/` markdown the moment they're
   delivered (per [[reference-plan-archiving-mechanism]] — git commit
   history IS the archive). JetStream here is transport plus a
   medium-term replay buffer, not permanent storage, so it doesn't need
   per-consumer tuning before real traffic data exists to justify it.
   **Decision: 90-day/10GB, corrected 2026-07-22 (was wrongly 50GB
   at first pass)** — the original 50GB suggestion never checked actual
   free disk on tgw-prod's root partition, which is only 49GB total/37GB
   free; 50GB wouldn't have fit at all, and a units mismatch between
   NATS's server-side (`"50G"`, decimal) and stream-side (`"50GB"`,
   binary) config would have broken it even before that. Caught live
   when `nats-stream-init.service` failed with `insufficient storage
   resources available (10047)` on first real switch. Fixed in
   `nix/nats.nix` (commit `bc2b67c`): both sides now use the same `10G`
   decimal-suffix form, parsing to an identical 10,000,000,000-byte
   ceiling everywhere, well under the 37GB actually free. Revisit
   per-stream only if real volume (audit traffic specifically, given
   ~55k items × ~10 fields of potential churn) actually threatens that
   ceiling — don't pre-split policies speculatively.

3. **Broker host: tgw-prod, not a real question.** Closed earlier the same
   session (Dave: "only humans are that remote. Network constrained?") —
   tgw-prod and a1131 are both LAN-local already (and Tailscale-joined for
   when they're not, per PP-REMOTEOPS-001's confirmed-live `tgw-http`
   reachability from a1131 today), so there was no real network-topology
   tradeoff to weigh. Same single-authority pattern as every other fenced
   service ("tgw-api is the fence"); "remote" only means something for
   actual humans/external devices (Dave's phone, the satellite
   warehouse), which is exactly what Tailscale already exists for, not
   for inter-host-on-LAN traffic.

**All 3 open questions from the original design doc are now resolved.**
Nothing left blocking Phase 1 except actually building it — todo #1633
closed. Next concrete step: stand up `nats-server` natively in
`~/tgw-flake` (NixOS module/systemd unit, no Docker), wire Phase 1's
mutation-audit stream (this doc's original, already-scoped purpose) as
the first real build against it, then `agent_handoff` and the mailbox
redesign follow as additional subjects on the same broker — not separate
infrastructure decisions, and not new PPs.

**Real bug found live, 2026-07-22 (todo #1638, packet drafted):** the
broker itself landed fine, but `nats-stream-init.service` kept failing
with "insufficient storage resources available" across three separate
fix attempts on the retention-config side — turned out none of those
could have caught it, because the actual cause is a **dual-authority
bug**: `src/tgw/apis/nats_client.py`'s `_ensure_streams()` (pre-existing,
untouched tonight) independently creates both JetStream streams at
worker startup with no `max_bytes` (defaults unbounded), uncoordinated
with `nats.nix`'s new declarative stream-init — and per NATS's own
admission control, an unbounded sibling stream (`QUEUE_TRANSITIONS`) can
block a *different*, explicitly-bounded stream's reservation regardless
of actual usage. Same "reuse, don't invent a second authority" principle
already applied elsewhere tonight, freshly rediscovered. Fix: single
authority (`nats.nix` owns creation+config for both streams;
`nats_client.py` becomes read-only/publish-only). Full packet:
`packets/1638-nats-stream-single-authority.md`.

**DONE, both halves, live-verified 2026-07-22 — but a 4th failed attempt
on this same file before it landed.** Flake side dispatched to
nix-flake-maintainer: split the 10GB ceiling 8GB/2GB — passed
`dry-activate` clean, switched, and **still failed live**:
`nats-stream-init.service` errored `insufficient storage resources
available (10047)` editing `QUEUE_TRANSITIONS` to exactly 2GB, because
8GB + 2GB = the account's exact 10GB ceiling and NATS's admission control
rejects reserving all the way to the limit with zero headroom — a
different trigger than the prior 3 failures on this file, same error
code. Fixed live by Dave running a hand-patch (7.5GB/1.5GB, 10% headroom)
directly — confirmed: `nats-stream-init.service` active/exited-clean,
`QUEUE_TRANSITIONS.max_bytes = 1500000000`. **Four live failures on one
file, each passing `dry-activate`/static checks and failing only on real
`nixos-rebuild switch`** — concrete evidence behind the 2026-07-22
Nix-direction-change decision below (PP-NIXOS-001), not just accumulated
mood. App-code side (`nats_client.py` read-only + the `tgw_health` asyncio
fix, todo #1639) landed clean on branch `todo/1638-1639-nats-client-fixes`
— tests pass, live-verified against the real broker, awaiting review/
stitch.

**Phase 5 REVISED, 2026-07-22 (Dave: "plan the bubblewrapping of all of
the workers"):** the original nspawn+Btrfs AI-session-isolation design
(Phase 5) is superseded, not deleted — Nix-coupled, conflicted with
PP-CATIONIX-001's standing "build portable, independent of the Nix
decision" requirement. Replaced with **bubblewrap** (already the standing
portable candidate, already deployed on a1131 for Codex CLI), scope
widened from AI coding sessions only to **every `tgw-worker@<queue>`
systemd unit** — turns the already-settled "workers ask tgw-api, never
construct paths directly" rule from a written convention into a
mechanically-enforced filesystem boundary, same pattern as E11/E12. Trust
tier reuses the specialist-roster shape: bubblewrap for steady-state
trusted execution (ordinary workers, `tgw-coder` today), **gVisor** for
anything probationary/not-yet-trusted — no VM, no Firecracker/microvm.nix
(dropped per the "fewer Nix lock-ins" discussion). Known gap: bubblewrap
has no network-level ACL, this only closes the filesystem side of the
fence; network containment is a separate, later, unstarted effort.
5-phase rollout (survey → baseline profile → pilot on `echo` → risk-
ordered rollout, `token_refresh` last → same treatment for coding
specialists): full writeup in `PP-AIOPS-001-cat-herding-platform.md`,
"Phase 5 REVISED" section. **Todo #1634 opened for Phase A (survey) only**
— the sole next-actionable step; nothing else is scoped until it reports.

**Tigwa's facility-cross-check reconciliation, 2026-07-22 — review only, no
new build authorization:** the JetStream convergence is structurally
correct, but "broker service started" and "broker accepted as the
mailbox/handoff/audit single authority" are not the same claim yet.
Confirmed real gaps before that acceptance:
- **`tgw_health`'s NATS check is broken**, not just untested — fails with
  `asyncio.run() cannot be called from a running event loop`. This is a
  health-probe implementation defect (nested `asyncio.run()` inside an
  already-running loop); it proves nothing either way about the broker's
  actual health and must be fixed before any live-health claim is trusted.
  **New todo needed, tag PP-AIOPS-001.**
- Packet 1638's path (`packets/1638-nats-stream-single-authority.md`,
  written relative to `plan/`) read as "not found" when checked from a
  different base — confirmed still present at
  `docs/TGW-Plan-Vault/plan/packets/1638-nats-stream-single-authority.md`;
  a path-notation ambiguity, not a missing file. Write packet paths
  vault-root-relative going forward to avoid repeating this.
- The `nats_client.py` / `nats.nix` dual-authority bug (above, todo #1638)
  must actually be fixed before the broker can be accepted as the shared
  substrate — not yet done.
- **Required acceptance evidence before JetStream is trusted as the
  mailbox/handoff/audit authority** (none of this exists yet): independent
  broker connection/stream inspection from both tgw-prod and a1131;
  durable publish plus consumer acknowledgement (not just publish
  succeeding); a denied cross-actor read/write attempt proving subject/
  account compartmentalization actually holds, not just designed-to-hold;
  a broker restart/replay test; and the repaired health check above.
  Applies to PP-RUNNERCOMMS-001's mailbox redesign and PP-AIOPS-001's own
  audit stream equally — same broker, same bar.
- Syncthing manual reconfiguration (PP-DATAINTEGRITY-001/#1632) is
  remediation, not proof — still needs an end-to-end content-addressed
  round-trip test in both directions before "fixed" is a live claim; until
  then no operational decision may treat a synced inbox/export copy as
  delivery proof (JetStream ack is delivery proof; filesystem export is a
  human-readable record only, per PP-RUNNERCOMMS-001 below).

**Two more decisions needed and made, 2026-07-22 (Dave), closing real gaps
found while planning the acceptance-evidence suite above:**
1. **Network exposure: `nats.nix` currently binds `127.0.0.1` only** — its
   own comment explicitly says "no cross-host NATS traffic is part of this
   design." That directly conflicts with the acceptance suite's own
   requirement (independent connect/inspect from both tgw-prod and a1131)
   and with the mailbox design generally. **Decision: bind on tgw-prod's
   Tailscale interface, not the raw LAN interface** — reuses
   PP-REMOTEOPS-001's already-established, already-authenticated tunnel
   between the two hosts instead of exposing anything new to the LAN
   segment; no separate firewall-rule maintenance needed the way a raw-LAN
   bind would require.
2. **Auth/compartmentalization model: one NATS account, per-actor subject
   permissions** — not separate accounts per actor. Each actor
   (Claude/Tigwa/future specialists) gets its own credential (nkey or
   user/pass) scoped to publish/subscribe only on its own subjects (e.g.
   `tgw.mailbox.claude.>`). Simpler to provision than full account
   isolation (no JWT/account-signing-key infrastructure), still gives a
   real broker-enforced denial for cross-actor access — sufficient for
   this PP's compartmentalization requirement, which is about access
   boundary, not resource-limit isolation between actors.

Both decisions apply to the not-yet-dispatched "Packet C" account/binding
sub-step (see `docs/ai-plans/jetstream-substrate-buildout.md`) — resolves
the one open question that was blocking it.

**Packet C unfolded technically, 2026-07-22** — per Dave's direction to
unfold rather than keep the plan compressed. **Critical live finding
before designing it**: the broker has zero authentication today (confirmed
live — a bare unauthenticated `nats pub` succeeded against `127.0.0.1`),
contained only by the localhost bind. **Hard sequencing constraint this
surfaced, not previously written down: per-actor auth must land in the
SAME dispatch as the Tailscale bind, never split with the bind landing
first** — otherwise there's a real window of an unauthenticated broker
reachable from the whole Tailscale network, the same severity class as
PP-COHESION-001's still-open Syncthing/NFS exposure findings. Concrete
design: NATS `authorization` block (not full multi-tenant accounts, per
the decision above) with one user per actor, subject-scoped permissions,
bcrypt hashes in the git-committed flake, plaintext passwords in
`secrets_root/tgw.env` via the existing single-facility rule. Concrete
test procedure for all 5 acceptance sub-checks, and the dispatch split
(nix-flake-maintainer for the flake/bind, tgw-coder for secrets issuance +
client wiring + the test script). Full design:
`docs/ai-plans/jetstream-substrate-buildout.md`'s "Packet C technical
deep-dive" section.

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

**Greenlit 2026-07-18 (Dave): "we need it... one day it will pay off."** All
5 steps filed as todos: #1552-#1556.

- Scoping summary filed → reference/PP-EBAY-MOTORS-001-scoping-summary.md

## PP-EBAY-ACCOUNT2-001 — second eBay account: Seller Hub audit sandbox + multi-marketplace — NEW 2026-07-18
**Proposal, not yet scoped.** Two motivations: (1) a safe sandbox to run
PP-SELLERHUB-001's still-unscoped Gemini Seller Hub audit without touching
live production listings, (2) Dave's own suggestion to also use it for
multi-marketplace capability building/testing — distinct from
PP-EBAY-MOTORS-001's marketplace-on-the-existing-account work. Dave
registers the account himself (eBay signup isn't a TGW action); Claude
wires credentials into the existing secrets facility once handed over. No
account exists yet, no todo filed — real design work (config shape for a
second account identity, clarifying which motivation is primary) waits
until credentials are in hand. **Pre-sketched 2026-07-22** (still blocked
on the account itself, but the decision is now a 5-minute pick once it
exists): two candidate config shapes (top-level account key vs. per-item
account field), which motivation leans which way, and one free factual
pre-check (per-application vs. per-account eBay rate limits — determines
whether a second account even needs full separate developer-app
credentials). Full writeup: `pp/PP-EBAY-ACCOUNT2-001.md`.

## PP-CATALOG-INCR-001 — incremental catalog update

**Greenlit 2026-07-18** — surfaced by the recurring "Needs Review" stale-badge symptom
(catalog_rebuild worker deliberately stopped since this exact resource-cost finding;
`ebay_publish` correctly enqueues a rebuild job every publish but nothing was consuming
the queue, so a manual `tgw build-all` was needed twice in one session before Dave asked
for the real fix). Two open design questions resolved: reconciliation timer **hourly**;
CI-2's SQLite upsert **synchronous** in the fence write path. Todos filed: #1548 (CI-1
fence mutation hook), #1549 (CI-2 SQLite upsert-on-write), #1550 (CI-3 thumbnail
triggering condition), #1551 (CI-4 timer cutover). CI-5 (JSON catalog fate) stays
deferred. Full design: `pp/PP-CATALOG-INCR-001.md`.

**CI-1 done same day (#1548):** `publish_mutation` wired into `_apply_patch` and
`_apply_ebay_write` in `http_server.py` (the real fence, both real write paths) —
closes the PP-AIOPS-001 Phase 1 coverage gap the design doc flagged (audit stream was
only fed from `items.py`'s CLI-only path before). Fire-and-forget per changed field,
matching `items.py`'s existing pattern. Full test suite green (2,580 passed) after
fixing one real regression caught along the way — the store-category dropdown's
fallback path was accidentally routed through `.ebay.pricing`'s cached `_load_groups`
instead of reading `category-groups.json` fresh, breaking test isolation and (in
production) risking a stale cross-request cache that didn't exist before — and
refreshing the C12 line-number allowlist (`tests/test_invariant_c12_field_set_
accessors.py`) for the line shifts this packet + the earlier Lens-removal/dropdown-fix
edits caused. `tgw-http.service` restarted clean, `tgw health` shows only the 2
pre-existing unrelated failures. **CI-2 done same day (#1549):** `sqlite_catalog.upsert_catalog_row(cfg, doc)` — atomic
per-SKU `INSERT ... ON CONFLICT(sku) DO UPDATE`, called synchronously from both fence
write paths right after `atomic_write_json` (same two call sites as CI-1). The
inventory webui's SQLite data source now stays live-accurate on every write, without
waiting for a full rebuild — this is the actual permanent fix for the recurring stale
"Needs Review" badge symptom that surfaced today (`catalog_rebuild` worker is still
stopped; CI-4's hourly timer will be the reconciliation backstop, not the primary
update path anymore). Verified: isolated function-level test against a throwaway
SQLite file confirmed insert + update-in-place both work correctly; full test suite
(2,580 tests) green, including the many existing tests that already exercise
`_apply_patch`/`_apply_ebay_write`. `tgw-http.service` restarted clean, `tgw health`
unchanged. C12 allowlist refreshed again for line shifts (2nd refresh today — expected,
per the detector's own documented tradeoff). CI-3 (thumbnail triggering condition) and
CI-4 (hourly timer cutover) remain.

**CI-3 done same day (#1550):** found the real gap was narrower and worse than the
design doc assumed — `thumbnail_gen` wasn't being over-triggered on every write, it
was **only ever enqueued once, at initial `bundle_intake`**. A later photo reorder
(`POST /api/items/{sku}/photo-order`) or photo delete never refreshed the thumbnail at
all. Added `_enqueue_thumbnail_gen()` wired into `_apply_patch`'s fence hook, firing
only when a write's changed keys include `image` or `photo_order` — both existing
call sites (photo-order save, photo delete) get this automatically since they already
route through `_apply_patch`, no per-call-site duplication needed. Stamped
`origin: "operator"` per invariant C10 (caught by
`test_operator_origin_sourcescan.py`, which is exactly what it's for). Full test
suite green (2,580 passed) after updating `test_photo_order_enqueues_via_shared_helper`
for the now-correct 2-enqueue behavior and a 3rd same-day C12 allowlist refresh.
`tgw-http.service` restarted clean, `tgw health` unchanged. CI-4 (hourly timer
cutover) is the last packet.

**CI-4 done same day (#1551), code portion; timer install pending Dave's direct
switch confirmation.** `state_machine.enqueue_catalog_rebuild()` is now a no-op
(single point of control — all ~35 call sites across http_server.py and every
worker still call it safely, it just does nothing now, no per-call-site editing
needed). Found and closed a real gap surfaced while verifying CI-4's own premise
("SQLite catalog stays live for every caller"): CI-2's upsert had only been wired
into http_server.py's HTTP fence, but `items.py`'s CLI-path write functions
(`_write_field`, `set_fields` — used by `bulk_edit`, backfill/scrub scripts) are a
**separate** write surface that don't route through that fence at all. Added the
same `upsert_catalog_row` call to both, plus to `create_item_endpoint` directly
(a brand-new item needs its first catalog row immediately, not after an hour).
Also removed a now-dead C11 guard in `discard_revision` (persisted a finding on
catalog_rebuild-enqueue-failure — impossible now that enqueue never fails,
since it never does anything). Full test suite green (2,579 passed) after
updating ~14 test assertions across `test_http_server.py`, `test_fence.py`,
`test_bulk_edit.py`, `test_audit1143_workers_cohesion.py`,
`test_invariants_pricing.py`, `test_invariants_stage_guards.py` (all previously
asserted the old per-write enqueue happened; now assert it doesn't, or in
`test_photo_order_enqueues_via_shared_helper`'s case, that only CI-3's
thumbnail_gen enqueue remains) plus a 4th same-day C12 allowlist refresh.
`tgw-http.service` **and all 13 running workers** restarted (workers import
`items.py`/`state_machine.py` directly, so needed the restart too — easy to
miss). `tgw health` clean throughout. **`tgw-catalog-rebuild-hourly` timer
switched live 2026-07-19** — `nixos-rebuild switch` run (Dave's direct
confirmation, after an earlier attempt was correctly interrupted mid-command
and a relay-paraphrased confirmation was correctly refused first), new
generation `...p40344gg3hh8mkdaqdkz95frlfsisc7l...` active, timer confirmed
loaded/waiting via `systemctl list-timers` (next fire ~15 min out at
switch-time). **PP-CATALOG-INCR-001 is now fully live, all 4 packets + timer.**

**Full-diff code review, 2026-07-19 (Dave: "we need to review and merge all of
those changes we made outside the process"):** workflow-backed `/code-review`
at high effort over the full day's uncommitted diff (27 files) found 6
CONFIRMED correctness/cleanup findings, all fixed same pass: (1) `_apply_patch`
was popping `draft_listing`/`item_attributes` out of `fields` before computing
`_changed_keys`, so those writes never reached CI-1's audit stream or the PATCH
response's `updated` list — fixed by capturing the original key set before the
pop; (2) `discard_revision`'s deleted C11 guard left catalog-upsert failures
completely unenforced — fixed at the root: `_apply_patch`/`_apply_ebay_write`
now persist a C11 finding on upsert failure themselves, not just log a
warning; (3)/(4) `items.py`'s `verifiedupdate`/`strip_fields` bypassed CI-2's
upsert entirely (direct `atomic_write_json`, not through `_write_field`) —
both now call it too; (5) `items.py` had no logger at all, silently swallowing
every upsert failure — added one; (6) `upsert_catalog_row` re-ran the full
schema script on every call including inside bulk-edit loops — now cached
per-process. **Self-caught 7th regression while fixing #2**: the C11 fix's
recursive `_persist_finding` call was clobbering `catalog_verified` on the
outer write via the same "not in fields → clear it" logic — caught by the
full test suite before reaching anything live, fixed in the same pass. Full
suite green (2,579 tests) after 3 more same-day C12 allowlist refreshes.
`tgw-http.service` + all 13 workers restarted again, `tgw health` clean.
**Original design origin (s43, 2026-07-03, for context — superseded by "now fully
live" above, kept for history per Prime Directive 1):** Dave's original design,
recovered from an unprocessed inbox transcript
(`inbox/hermes-out-of-flake-portable-catalog-concept.md`) after he flagged
`catalog_rebuild`'s full 55,419-item disk scan on every single write as the
system's most intensive task (1,361 rebuilds/33h, ~57s each). This IS what
CI-1..CI-4 above built — atomic per-item SQLite upsert + conditional thumbnail
regen at write time, full 4-artifact rebuild demoted to a scheduled hourly
reconciliation timer instead of a per-write trigger, revising the "Catalog
rebuild is always a job" settled-architecture line with Dave's explicit
sign-off (the greenlight above). The JetStream-wrong-door gap this design
found while being confirmed is what CI-1 itself closed. Full design:
`pp/PP-CATALOG-INCR-001.md`.

**Sequencing vs. [[PP-POSTGRES-001]] resolved 2026-07-16** — this PP's
JSON-stays-truth premise is correct for the current phase (logic fixes + UI
first); see PP-POSTGRES-001's section for Dave's full sequencing call.

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
R1.1 live-fire DONE 2026-07-04 (#1137) — price-only delta verified live in both
directions, drift-gated apply path confirmed, gate cleared. 2026-07-16 was a heavy
single-day fix session (invariant C14 incident, Set A/B field-set discipline)
covering: #1461 (attribute-delete-reverts bug, fixed), #1471 (category-aspect
migration, built+deployed), #1472 (custom-aspect checkbox redesign, DONE, live-fire
confirmed — beats-eBay success bar #2), #1473 (Set A as discard destination — NOT
settled, open, needs a fresh design pass), #1445/#1467 (post-push `ebay_sync` gap,
fixed for both `ebay_stage` and `ebay_publish` success paths), #1465 (Seller Hub
parity audit, reassigned to Tigwa with vision-model browser testing). All 3 flagged
`field_set_drift` SKUs now live-confirmed. Full incident timeline, code detail, and
test counts: `../pp/PP-LISTEDITOR-001.md`.

**Reidentify-as-full-redraft, design captured 2026-07-20 (Dave), not built.**
Surfaced while testing the Gemini/DeepSeek model migration: `ebay_draft.py`'s
description-rewrite call is gated behind `pl_description` (product-lookup data)
existing with 20+ words — meaning "reidentify" today can silently do nothing for
most items, since most never got a product-lookup match. Dave's actual intent is
broader: **"all reidentify should do is fill the fields, just like ai_identify.
Maybe an option to discard or use existing data as hints, but regenerate the
whole draft as an update candidate."** Three pieces:
1. Reidentify/redraft should be a full-field refill (title, description,
   category, aspects, condition), same shape as `ai_identify`'s own
   `ai_reidentify` flag — not gated behind one upstream data source happening
   to exist.
2. Operator choice: discard existing fields entirely (fresh generation) vs. feed
   them back in as hints (matches `ai_identify`'s existing `_USER_PROMPT_HINTED`
   pattern, already built) — a mode toggle, not a new mechanism.
3. **The regenerated result becomes an update candidate, not a direct
   overwrite** — this is the SAME shape as R1.1's drift-gated apply path above
   (currently price-only), extended to the whole draft. Reuse that mechanism
   rather than building a second one.
Dave, same breath: **"this is where transactional logging would be a player"**
— the propose→review→accept/discard cycle should be logged transactionally
(what was regenerated, from what source, whether accepted), connecting to
[[PP-STATEMACHINE-001]]'s job-manifest work and the evening's broader
"everything runs in a logging terminal" discussion (journalctl + agent-traces
already deliver this for workers/agents; this would be the operator-facing
analog for draft regeneration specifically). Design only — not scoped into a
todo/packet yet, needs its own session given the size (full-draft candidate
generation + discard/hint toggle + drift-gated whole-draft apply + transactional
logging is a real feature, not a quick fix).


**Build-order correction, 2026-07-21 (Dave, via [[PP-REMOTEOPS-001]]):** the
current stacked page (Set A `item_attributes` / Set B
`draft_listing.item_specifics` shown together) is confusing enough that
even Claude gets lost reviewing it — live evidence the merged surface is
premature. Going forward: build/verify each field-set's work surface
independently first, compose a stacked "heads-up" management view only
after each independent surface works. Not yet scoped into a todo — a
future listing-editor UI pass should apply this before adding more to the
combined page.

## PP-ACTIONCONSOLE-001 — state-driven item action console
Built s40 (state-driven action line, Editor/Live tabs). **Gate: Dave's operator test
R1.2.** Principle settled: state drives interface; controls are indicators;
platform-wide style. Todo #1085. Troubleshooting buttons removed with no new home yet
— ops surface to design.

**Operational console idea captured 2026-07-20 (Dave, urgent capture, not designed
yet):** "a choose-best-route-for-prompt button that can decide whether to
interrupt or wait, like a turbo." Context: this whole session, Dave sent many
mid-turn messages while Claude was actively working (tool calls in flight) —
today that's an implicit chat-interface behavior (message queues, surfaces at
the next tool result). The idea: make that an EXPLICIT operator control on the
console — when submitting a new instruction to an in-progress agent session,
choose the routing: interrupt now (a "turbo" fast-track, for genuinely urgent
input) vs. queue and wait for a natural stopping point (routine follow-up,
doesn't need to break current work). Same underlying concept as tonight's
`PP-STATEMACHINE-001` priority-tier work (urgent vs. normal jobs), just applied
to operator-to-agent interaction instead of worker job queues — worth
designing them with the same vocabulary/mental model rather than two unrelated
mechanisms. Not scoped into a todo yet beyond this capture — needs its own
design pass alongside the rest of the operational-console vision.

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
budgets. **Open gap found 2026-07-11** — Dave found a real remaining gap: the
llm_google/llm_deepseek/llm_anthropic caps (300/500/100) are call-count
proxies, not actual dollar-balance tracking. No code anywhere queries real
provider account balance or warns proactively before it runs dry — the
provider's own hard cap is currently the ONLY safety net, and that should
be the fallback, not the primary signal. "Fine now only because the
pipeline is quiet" (Dave) — a real risk once volume returns. Todo #1337.
Remaining observability packets tracked under R2, not here.

## PP-BACKUP-001 — backup + DR
Top operator risk historically: nothing running, work ledger not re-derivable.
**2026-07-10 alarm + durable fix, applied and reboot-verified 2026-07-12**: undeclared
`sdc` mounts (db-backup/itemdata-snap/itemarchive) caused silent dump failures after a
reboot; now declared in the flake with `RequiresMountsFor` so a missing mount is a
loud failure, not silent. Remaining open: `tgw-cloud-sync` rclone rate-limiting (todo
#1264) — first full GDrive sync hit a 403 rate limit, needs pacing/chunking, not a
bare retry. Full incident + fix detail: `../pp/PP-BACKUP-001.md`; DR plan:
`plan/PLAN-backup-dr.md`.


## PP-COHESION-001 — full-codebase cohesion+correctness audit (2pm agenda, todo #1143)
Staged per-subsystem `Workflow`-based audit (workers/, apis/ebay/, http_server.py,
queue/state-machine, scripts/, nix flake) plus a cross-subsystem cohesion pass.
**Discovery phase COMPLETE** (all 6 subsystems audited); most findings executed and
DONE (workers/, apis/ebay/, http_server.py, queue/state-machine, scripts/ all closed
out). **3 nix-flake SECURITY findings remain open**: #1219 (NFS Queue export
subnet-wide, BLOCKED on a static IP reservation for the intake device, #1228),
#1217/#1218 (Syncthing GUI/bind exposure, intentionally deferred p95 until dev
settles). `itemdata_scrub.py` queue-migration (#1261) deferred 2x — needs its own
scoping pass, not a quick fix; confirmed not currently scheduled anywhere so zero
practical impact today. Full findings list + execution history: `../pp/PP-COHESION-001.md`.


## PP-OPSREALITY-001 — TGW application capability & operational-reality register — NEW 2026-07-22

Tigwa REQUEST (at Dave's direction): apply the same evidence discipline
being requested for Seller Hub (PP-SELLERHUB-001's SHCS, above) to TGW
itself. **Distinct from [[PP-COHESION-001]]** — that PP is a
correctness/security bug audit (discovery phase complete, most findings
closed); this one is an evidence-state register asking a different
question: for every operator workflow/UI/API/CLI capability/worker/
runbook, are documented policy, implemented behavior, deployed/live
verification, and exercised incident readiness **the same claim or four
different ones that have quietly drifted apart**? Outdated runbooks (see
PP-RUNBOOK-001's still-not-started eBay-ops runbook) are one visible
symptom, not the whole problem — this session's own `tgw_health` NATS bug
(#1639, fixed), the just-found unconfirmed-executed PP-BACKUP-001 A5
restore drill (now executed and passed, see PP-POSTGRES-001's P1
section), and **PP-POSTGRES-001's own P0 turning out to already be done
live** (todo #1636 was about to be dispatched as fresh work; checking the
actual code first found `publish_mutation()` already wired into
`http_server.py` since 2026-07-19, never cross-referenced back into the
plan) are all concrete proof-of-concept findings of exactly this pattern —
the third one caught *before* wasted dispatch effort, which is the
register's whole value case in one live example.

**Phase 0 scoping packet DRAFTED, 2026-07-22**: register schema
(`capability_state`: implemented-and-verified / implemented-not-live-
verified / documented-but-stale / live-but-undocumented / partial /
blocked / superseded / unknown), a source-of-truth hierarchy (live state >
code > tests > docs > prior reports — never silently overwritten on
conflict, the conflict itself becomes a register row), a bounded
non-crawling Phase 0 inventory method (reuse `tgw plan status`, systemd
unit lists, `reference/runbooks/INDEX.md` as the free row skeleton, one
bounded live-evidence probe per row), risk-ranked domains (listing/publish
→ item-mutation/fence/Postgres seam → order/fulfillment recovery →
worker/queue/NATS observability → backup/restore → agent-tooling
boundaries), and a pull-based revalidation cadence (fires on the governing
PP/todo closing, the underlying code changing, or an incident — never a
calendar sweep, silent while healthy). Full packet:
`docs/ai-plans/tgw-operational-reality-register-phase0.md`. Cross-
references SHCS by shared `capability_id` for Seller-Hub-facing rows
rather than duplicating — a row can be `full-parity` in SHCS and
`documented-but-stale` here at the same time, and that combination is
itself a finding.

**Not yet started**: Phase 0 population (row skeleton generation from
existing indexes) — discovery/scoping only so far, no register rows
populated yet.

**Next phase defined, 2026-07-22 (Tigwa SEQUENCING note):** once SHCS and
this register both have reviewable Phase 0 evidence, a **three-way
reconciliation** runs against the canonical Master Plan/PP docs (plan says
X, SHCS says external-account reality is Y, register says actual TGW state
is Z) — 7 discrepancy classes defined (plan gap / implementation gap /
external parity gap / documentation-runbook gap / test-monitor-recovery
gap / authority-provenance gap / intentional divergence). Every
discrepancy becomes a durable row with all 3 sources cited — **never a
silent edit to make plan/docs/register agree**. Only Dave + the reviewing
actor accepting a row promotes it to a sequenced work item. This is the
mechanism that turns both Phase 0 packets into the evidence-backed
sequencing map, not a separate initiative — not started, waits on both
Phase 0s populating real rows first.

## PP-HARDWARE-001 — IT / hardware track (drive-space re-evaluation absorbed) — NEW 2026-07-11
Governing philosophy: bootstrap hardware until revenue justifies real infrastructure.
`/opt/TGW` (nvme) is the real near-term pressure — 83% used, 48G free, ItemData
already 180G/55K items, heading toward ~9x scale. LVM-expansion premise superseded:
sdb absent, sdc repartitioned into backup service (#1056 closed, superseded by
#1136). Power constraint: generator-powered, prefer drives that can go offline — real
drive-fleet inventory + tiering mapped (bus-powered 2.5in tier always-on, 3.5in
powered-dock tier connect-only-when-syncing). Merged with PP-DRIVE-INDEX-001 —
recoll-driven dedup is the near-term space-recovery lever before any new drive
purchase. Open, unresolved: where PP-KNOWLEDGE-001 physically lives long-term; a full
drive-fleet audit + registry refresh. Full detail: `pp/PP-HARDWARE-001.md`.


## PP-KNOWLEDGE-001 — the knowledge & translation hub — 6-LAYER UMBRELLA, extended 2026-07-11
**Vision (Dave, 2026-07-16):** "A library with a librarian that can tell you where
everything is, cross-referenced, in your language, with footnotes." Leotha/Tigwa
curate long-term; this plan is architecture only.

| Layer | Tool | Answers | Status |
|---|---|---|---|
| Storage | git-annex (PP-ANNEX-001) | — | promoted 2026-07-11, design in `docs/ai-plans/recoll-annex-jetstream.md` |
| Search | Recoll | "where is the evidence?" | LIVE at `/opt/TGW/.recoll/` (441K docs) |
| Core spine | PostgreSQL LISTEN/NOTIFY | general operational event bus | RESOLVED 2026-07-11 — not NATS JetStream (that's PP-AIOPS-001's separate audit-stream use) |
| Memory | Hindsight | "what happened before?" | exploratory, not committed |
| Knowledge | gbrain | "what do we believe now?" | exploratory, not committed |
| Graph | Graphify | "what connects to this?" | merged from PP-CODEGRAPH-001, see that section |

**Filing authority (Dave, 2026-07-16, reinforced 2026-07-17):** all filing
locations/taxonomy are the librarian's (Tigwa/Leotha) responsibility, including
creating new locations once trained — other actors classify/hand-off, never invent
folders. Search/recovery (finding lost PPs) is also her assigned responsibility on a
Dave-briefed schedule, not Claude's routine sweep. Starting point decided 2026-07-14:
git-annex + Recoll (not Graphify) is where Tigwa actually begins, targeting
PP-DATAINTEGRITY-001's reconciliation use cases concretely, not an abstract
index-everything exercise. Infrastructure, not an iterated tool — hosted on a1131,
packaged declaratively in its flake. Plan's own long-term destination:
`TGW-Master-Plan.md` itself migrates into this hub eventually (not started;
`tgw-plan-maintain` is the interim discipline). Full detail (PP-ANNEX-001 sub-design,
PP-DOCLIB-001/PP-HISTORY-001 absorption, NATS-vs-Postgres reconciliation):
`../pp/PP-KNOWLEDGE-001.md`.


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
Owns all data-reconciliation work under one PP instead of splitting across
PP-UIPIPE-001/PP-DRIVE-INDEX-001/PP-ANNEX-001. Leg 1 (`photo_files_readable` detect)
DONE #1154 (206 bad/149 SKUs). Legs 2 (verify-after-copy sha256) and 3 (decode-verify
at intake) open. One of Tigwa's knowledgebase-buildout target use cases (2026-07-14).
**New leg, todo #1377 (2026-07-13): `status` vs `#STATUS` write-path bug** —
`items.statusupdate()`/`verifiedupdate()`/bulk-edit have always written to the legacy
`#STATUS` key instead of canonical `status`; a 2026-07-03 data-scrub correctly
stripped `#STATUS` from 20,415 items but exposed the underlying write-path bug. 5,118
items currently have neither key set. Dave: "this is a big fix" — logged, not yet
scoped/executed. Full design + open questions: `pp/PP-DATAINTEGRITY-001.md`;
photo-integrity design: `docs/ai-plans/photo-integrity-mitigation.md`.


## PP-ADD-005 — SKU migration (legacy formats → canonical) — orphaned pp_ref, backfilled 2026-07-16
Migration itself is 99.7% done: `src/tgw/sku_migration.py` documents 7 historical
SKU format classes (A-G) normalized to the canonical `tgwYYYYMMDDHHMMSSs` (18-char)
format. Two classes look like corruption if you don't know this file exists — Class
B ("epoch-0", old SKUs literally start `tgw1970...`, real date lost, migration
best-guesses 2015) and Class E (2-digit-year SKUs actually from 2020, prepend "20").
A 2026-07-14 stale-catalog investigation that looked like ~8,257 missing ItemData
folders was 100% explained by these documented classes, not real data loss.

Two open todos, both real and still open:
- **#1411** (p45) — 149 of 55,419 ItemData folders are still in pre-migration Class
  A (20-char) SKU format; finish migrating them.
- **#1412** (p45) — `sku_history` audit table only has 3,305 rows logged, but the
  migration script's docstring implies ~34k+ renames actually executed (Class A
  bulk ~26,423 + Class A live-eBay ~8,314). Investigate whether `rename_sku()` was
  bypassed for the bulk runs, and whether `/opt/TGW/var/log/sku-migrate-*.json`
  manifests can backfill the missing audit rows.

Before treating any `tgw1970*` or 2-digit-year-prefixed SKU as garbage, check
`sku_migration.py`'s class table first.

## PP-AGENT-DISCIPLINE-001 — agent role/procedure guardrails made mechanical, not prose — NEW 2026-07-16
Born from a real incident (`INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md`)
— a written "always run this" rule was skipped twice same day, proving prose-only
compliance isn't enough. Four pieces built, todo #1444 closed: invariant E10
(flake-drift detector, still needs a standing periodic check not just an agent-time
one), `.claude/agents/nix-flake-maintainer.md`, the `flake-guard.py` PreToolUse hook,
and the `session-start-briefing.py` SessionStart hook (both hooks not yet live-fire
confirmed — needs a `/hooks` reload/session restart to prove firing). Tigwa's
cross-verification (2026-07-16) confirmed both real; one gap reconfirmed at the time
(flake-guard covers `Bash` only, not raw `Edit`/`Write`). **Both follow-ups DONE,
2026-07-18 — corrected here 2026-07-22 (the text below was stale, still describing
them as open; verified live before fixing, not just trusting the todo record):**
#1449 — `flake-guard.py` now matches `Edit`/`Write` on any `~/tgw-flake` file path
directly (confirmed live, line 53 of the hook), not just `Bash`. #1450 — evaluated
`settings.worktree.bgIsolation`, recommended (and shipped) a dedicated PreToolUse
hook plus `bgIsolation: "none"` as a defensive setting rather than relying on
`bgIsolation` alone (confirmed live in `.claude/settings.json`); found a live
orphaned worktree as concrete proof the dedicated-hook approach was the right call.
Full detail: `../pp/PP-AGENT-DISCIPLINE-001.md`.

**Cisco Antares — local vulnerability-localization scan, NEW 2026-07-21.**
Cisco released Antares-350M/1B (3B coming) same day — open-weight, Hugging
Face, Foundation AI collection: small models purpose-built to rank which
files in a codebase likely contain a known vulnerability class, not
generate fixes. Small enough for tgw-prod's existing CPU-only Ollama setup,
no code leaves the network. Dave, 2026-07-21: "it is on hugging face. This
is doable today." Natural fit as a second pre-merge check alongside #1538's
lint+test gate on task branches, before a result manifest is produced —
todo #1629 opened (depends on #1538): pull weights, run against a real
`tgw-coder` branch diff as a live test first, then wire into the gate if it
proves out. Also a genuine "housecat" candidate for the ferals audit
(`pp/PP-CATIONIX-001-ferals-audit.md`) rather than speculative — not yet
added there, worth a line once the live test runs.

**Tigwa provisional resource card, 2026-07-22 (`source-verified /
provisionally described / not admitted` — receipt only, no admission or
download authorized):** publisher is Hugging Face org `fdtn-ai`
(presents as Cisco Foundation AI); resources `fdtn-ai/antares-350m` and
`fdtn-ai/antares-1b` are **gated** — signed-in HF user + manual review
required, no TGW account request made yet. Vendor claims File F1 0.209
for the 1B variant on Cisco's own 500-task VLoc Bench — a publisher
claim, unreproduced. Proposed job: `defensive.vulnerability-
localization.candidate-files` — given a frozen snapshot + CWE/CVE task,
emit a ranked candidate-file list only (never "clean"/"confirmed"/
"patch correct"), read-only mount, no network, explicit inspection-
command allowlist in a disposable container. Admission protocol before
todo #1629 executes: Dave approves gated-access terms + host/storage
first; record exact HF revision/license/artifact hashes via git-annex;
run 350M on a synthetic/known-label fixture before ever trying 1B (local
host: 4 cores, ~13 GiB free RAM — not a throughput claim); compare
against ground truth (precision/recall/FP/FN); append the trial + hashes
to the remote immutable log. "The resume begins empty except for quoted
vendor claims. The library, not Cisco, writes the proven record."

**Flake approval-prompt consolidation, 2026-07-20 (Dave: "every flake change
requires 5 approvals... pare that to one with a description of all actions").**
Root cause: each Bash tool call inside `nix-flake-maintainer`'s Step 2 procedure
(status/diff, commit, flake check, dry-activate, switch, list-generations, per-host
repeats) was hitting the auto-mode classifier independently, fragmenting one logical
flake change into 5+ separate approval clicks. Two-part fix, both live same session:
1. `.claude/settings.json` — new `autoMode.allow` entries for the steps already
   documented in the agent's own "wide, standing, no gate needed" read/write boundary
   (`git status/diff/log/fetch`, `nix flake check`, `dry-activate`, `list-generations`,
   read-only SSH diagnostics) — these no longer prompt at all.
2. `.claude/agents/nix-flake-maintainer.md` — new instruction to batch the genuinely
   mutating steps (commit+push as one `&&`-chained Bash call per host, dry-activate+
   switch+verify as one `&&`-chained call per host) instead of issuing each numbered
   sub-step as its own tool call. Each compound command's echoed stage labels serve as
   the "description of all actions" Dave asked for. Deliberately kept per-host switches
   as separate calls (not one cross-host mega-chain) so a partial failure stays easy to
   diagnose. Target: ~2 prompts for a single-host change, ~4 for a two-host change
   (down from 5-10+) — not literally always 1, since touching two independent live
   systems inherently needs at least one confirmation per host.
Caveat: the settings-watcher only picks up config present when a session started
(same caveat as the session-start-briefing hook above) — doesn't apply retroactively
to an already-running agent instance.


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
Reuses the already-running `state_machine` Postgres instance (todo #1351).
Full design + open questions (migration path, same-vs-sibling DB, field
normalization, JSON export cadence, rollback safety): `pp/PP-POSTGRES-001.md`.

**Reframed 2026-07-22 (Dave): the migration itself is the fix for
fence-bypass bugs — "a fence that cannot be crossed."** Confirmed #1377
(cited in this PP's own opening motivation) is still genuinely live: read
`src/tgw/items.py` directly tonight — `verifiedupdate()` bypasses the
canonical `_write_field()` writer, hand-rolling its own
`atomic_write_json()` call plus a manually-duplicated SQLite catalog
upsert instead of calling the shared path. A same-shape static-test
detector (cloned from invariant C12's commit-time grep-based check) would
catch *this specific* pattern, but Dave's point stands: a lint is defense-
in-depth, not a wall — it only catches shapes it already knows to look
for, and any sufficiently different direct-write construction slips past
it, because enforcement still lives inside the same application code that
has the bug. **PostgreSQL column-level `GRANT`/`REVOKE` is categorically
different — the database engine itself refuses an unauthorized write, no
matter how the Python code tries to construct it, because the enforcement
point moves entirely outside the application.** Concrete design
requirement to carry into this PP's build (not yet scoped, add when this
moves from proposal to packet): the app's default DB role gets no
`UPDATE` privilege on canonical status/location/workflow-state columns;
only a dedicated "fence" role/function (the Postgres equivalent of
`_write_field()`) can write them, matching the same "Nix enforces only
hard safety floors" philosophy already applied elsewhere in this project
— code discipline is the everyday path, the hard floor underneath it is
what actually can't be crossed. JSON becomes a read-only export once this
lands, which structurally removes today's `atomic_write_json`-anywhere
bypass surface entirely, not just polices it.

**Sequencing decided, 2026-07-16 (Dave): "I believe postgresql is the right
call but this is not the right time yet."** Resolves the premise conflict
with [[PP-CATALOG-INCR-001]] (which assumes JSON stays truth) — that PP is
correct for the CURRENT phase, not a competing design needing reconciliation.
Order: finish the pipeline logic fixes (R1), build the UI out, and only take
on the backend inversion later — "unless it becomes too painful" to keep
deferring. JSON stays the live source of truth until that trigger; this PP
stays PROPOSAL/design-only until then, not started.

**Re-derived with month-sprint context, 2026-07-22 (Tigwa, 3 documents:
DECISION/RESPONSE/REVIEW) — sequencing call unchanged, but promoted from
"deferred" to a bounded capacity-funded parallel lane.** Given the
Max-plan month-sprint context flagged to her (see "Standing context"
above), Tigwa re-derived rather than blindly kept her original call — same
[[design-reconvergence]] pattern seen elsewhere with Dave's own designs.
Conclusion: the extra capacity is a reason to *prepare* the migration in
parallel, not a reason to let a full cutover consume the active
pipeline/UI/harness sequence. The 2026-07-16 gate stands: pipeline/UI work
stays the active current-phase authority; Postgres becomes authoritative
only when its migration/rollback/DB-enforced-permission contracts are
ready AND Dave gives explicit go/no-go — capacity alone doesn't authorize
the cutover.

**Operating model — three lanes, explicit WIP limit (Tigwa's REVIEW):**
1. **Critical integrity runway** — real write-fence mutation audit,
   NATS/Syncthing acceptance, current pipeline fixes, production
   regressions. Never blocked by the Postgres program.
2. **Product/harness runway** — UI, Catio specialist/handoff workflow,
   operator-visible data products.
3. **Postgres capacity lane** — runs only when 1 and 2 have spare
   capacity; each phase produces reviewable evidence and can stop without
   changing production authority.

**Gated sequence (supersedes the flat Phase 0-4 list above — this is the
same shape with two extra evidence gates Tigwa added: a shadow-import
proof stage before any dual-write, and an explicit backup/restore-drill
gate before cutover):**
- **P0 — ALREADY DONE, discovered live 2026-07-22.** Before dispatching
  todo #1636 as a fresh packet, checked the actual code first: `_apply_patch`
  and `_apply_ebay_write` in `http_server.py` already call
  `publish_mutation()` — landed 2026-07-19, commit `36f9872`
  ("PP-CATALOG-INCR-001 CI-1..CI-4: incremental catalog + fence audit
  stream"), just never cross-referenced back into this section when it
  shipped. **Live-verified, not just code-present**: newest message on
  `ITEMDATA_MUTATIONS` at check time (seq 32060, ~3 min old) carries
  `"source": "http_ebay_write"` — the real HTTP fence is actively
  publishing mutations right now. Todo #1636 closed as done, no new work
  needed. **This is a concrete real example of exactly the documentation/
  plan-vs-implementation drift PP-OPSREALITY-001 exists to catch** —
  worth citing there as a live case study, not just a hypothetical.
- **P1 — contract and measurement** — hot-field/jsonb schema decision
  (against `TGW-Item-JSON-Schema.md`, not the Perplexity guess),
  same-instance-vs-sibling-DB blast-radius decision, write/IO/concurrency
  baseline, data-product inventory (catalog/filter/search/reporting/
  Radar/workflow consumers), backup contract (base backup, WAL/PITR
  retention, off-host copy, RPO/RTO, restore drill — **no claim that DB
  backup is intrinsically better until proven**), rollback/provenance/
  conflict semantics.
- **P2 — shadow database** — reproducible read-only import of existing
  item truth; verify row counts, field parity, orphan/conflict/error
  register, source-hash provenance, repeatability. **No production
  reader or writer depends on it.** New gate vs. the old Phase 1/2 split
  — de-risks dual-write by proving the import mechanism first, in
  isolation.
- **P3 — bounded dual-write pilot** (= old Phase 1/2, narrowed) — one
  explicitly selected field family through the existing fence, with
  revision/idempotency/outbox behavior, automatic parity verification, a
  tested rollback. JSON remains authoritative. **Dual-write is the most
  dangerous phase** — needs a one-way authority statement per field, a
  deterministic repair/replay procedure, never a permanent two-master
  arrangement.
- **P4 — staged read cutovers** — move one read-only catalog/query/Radar-
  style consumer to a verified DB projection, only after it can fall back
  and compare results. Proves operator value before the authority
  question is even asked.
- **P5 (= old Phase 3) — authority cutover** — only after sustained
  parity, restore drill, load/concurrency benchmark, rollback rehearsal,
  and **Dave's explicit go/no-go**. Postgres becomes state truth; JSON
  becomes generated export/archive. Photos remain external filesystem
  evidence with metadata references — never database blobs.
- **P6 (= old Phase 4) — hard write fence** — column-level `GRANT`/
  `REVOKE`/stored-procedure boundary, independently bypass-tested, after
  the DB is real authority.

**Non-negotiable guardrails carried forward from Tigwa's review (apply to
every phase, not just cutover):** a database prevents physical write races,
not semantic lost updates — revision/conflict semantics are still a
required contract regardless of schema. Dual-write ≠ safety unless parity,
repair, rollback, and one-way field authority are all demonstrated. Photos
never become database blobs. No fresh Nix coupling — migration tooling,
data contracts, exports, fixtures, worker interfaces stay portable while
TGW lives in its current environment. The thermal/performance claim is a
hypothesis to benchmark against real workload, not a conclusion to assert.

**AUTHORIZED, 2026-07-22 (Dave, direct): "We discussed it, she both agrees
with me and I agree with her. We are going to postgresql. But this is what
we have and we need to plan the migration."** The three-lane capacity
model above is the standing operating model — settled, not pending. This
is the direction decision (Postgres IS where item truth ends up); it is
**not** the cutover authorization — P5's "Dave's explicit go/no-go" gate
still stands, per both Dave's and Tigwa's own wording. What changed today
is P1 (contract and measurement) moves from "eligible for prep" to
"underway" — see the new "P1 — Migration Contract" section in
`pp/PP-POSTGRES-001.md` for the concrete schema/instance/backup/baseline
decisions drafted in response to this authorization.

**Calibration, 2026-07-22 (Tigwa, same day):** don't portray "making
migration possible" as a large speculative platform build — TGW already
runs Postgres for `state_machine`, and the item corpus (55,420 items, a
live SQLite catalog) is already meaningful migration input. **The real
work is the integration/authority migration, not standing up
infrastructure.** P1 should stay a short, bounded feasibility/contract
closure (inventory what's reusable, map real readers/writers, run a
reproducible read-only import benchmark, inspect actual backup/recovery
state, decide same-instance-vs-sibling from observed evidence, hand off a
measurable P2/P3 packet) — not a broad architecture exploration. Also
corrected the backup picture: `tgw health`'s `backups` check is a live
pre-existing WARN (rclone off-host sync never completes, snapshot tree
16.2h stale, no encrypted secrets bundle — see PP-BACKUP-001 note below);
"already automatically backed up" is real progress but does not yet prove
end-state off-host recoverability — P1/P2 must keep "configured
automation" and "verified recoverability" as distinct claims, same
discipline PP-OPSREALITY-001 formalizes generally. Full technical
deep-dive (exact schema DDL, index design, P3 dual-write mechanics via the
existing write-choke-point + an outbox table) drafted in
`pp/PP-POSTGRES-001.md`'s new "Technical deep-dive" section, per Dave's
direction to unfold rather than keep this PP compressed.

**PRIORITY, 2026-07-22 (Tigwa, same day): "fully plan the migration
scaffold now"** — since the substrate/dataset already exist, the
reasonable use of capacity is finishing the reviewable design/packet set,
not starting P5. Closed her 7-point deliverable's remaining gaps: a real
current-writers/readers inventory (10 writers, 6 consumers, all named)
and P2/P3 restated as bounded packets with explicit acceptance and no-go
criteria (P2: row/parity/repeatability gates, no production dependency;
P3: `status` field pilot, zero-silent-failure gate, 2-week soak, tested
rollback). P4/P5/P6 deliberately left at design-narrative depth — turning
them into packets now would be guessing ahead of P2/P3's actual findings.
Full section: `pp/PP-POSTGRES-001.md`'s "Migration scaffold" heading.

## PP-RUNBOOK-001 — operational runbook hardening (thermal + eBay-ops) — NEW 2026-07-13
**Triggered by the 2026-07-13 tgw-prod NVMe thermal incident**, used by Dave
as a live training exercise for Tigwa. Full incident timeline, gap list (17
items), and Tigwa's build-order proposal: `reports/TIGWA-REPORT-runbook-gaps-
20260713.md` — **no action necessary on the incident itself** (Dave's
protective-shutdown call was reasonable; the fix that mattered came out of it,
below). Read that report directly rather than re-deriving the narrative here.

**What actually shipped from it:** `reference/runbooks/thermal-emergency-
response.md` (formal Tigwa-lite monitor policy, thermal half DONE 2026-07-14,
ties into PP-HERMES-EA-001's leg-3 authority decision) and confirmation that
`systemctl disable` doesn't survive on this box — `/etc/systemd/system` is
read-only, so a durable worker-stop is a flake edit + rebuild, not a runtime
command (feeds todo #1322).

**Status:** thermal runbook DONE. eBay-ops runbook (sold-order/picklist
recovery, API responsibility map) and the rest of the 17-item gap triage not
started. Needs a todo filed with `--pp PP-RUNBOOK-001` before any further
runbook file gets touched (going-forward tagging rule).

## PP-CODEGRAPH-001 — code graph + invariant/trace infrastructure — FOLDED INTO PP-KNOWLEDGE-001, 2026-07-14
Merged same day as filed — this is the concrete build-out of PP-KNOWLEDGE-001's
Graph/Graphify layer, not a separate initiative. 4-layer architecture: Tree-sitter
code graph (FalkorDB), Postgres+Z3 invariant catalog, DuckDB execution-trace store,
unified MCP layer — hosted on a1131. Problem it solves: cross-cutting design
"convergences" (fence-bypass pattern, status/#STATUS drift, NATS wired to the wrong
door) get found by manual audit sweeps instead of the tooling surfacing them. Dave
decided to build the full stack, not a cut-down Phase 1 (corrected an earlier
Claude scoping-down attempt — see `feedback-take-care-before-discarding-ideas`).
**Status: infrastructure-establishment planning doc written, nothing installed** —
Dave bringing additional research before the build session. Open questions (FalkorDB
packaging, invariant-catalog engine, cross-host MCP access, repo-sync, parse scope) +
the planner/stitcher convergence idea: full detail in `../pp/PP-CODEGRAPH-001.md`.


## PP-NIXOS-001 — NixOS migration (CatioNIX)
Canonical flake `~/tgw-flake` working; FROZEN except stability fixes. **Standing rule
(Dave, 2026-07-06, #1227): iterated-on tools stay OUT of the flake** — userspace
install (pipx/uv/npm) even at the cost of losing Nix reproducibility, while a tool is
still actively being tuned/swapped. Executed: Hermes/Aider decoupled from Nix control
(`docs/ai-plans/decouple-hermes-aider-flake.md`). **Audit #1143 nix-flake mitigation
batch EXECUTED 2026-07-06** (todos #1216/#1220-#1225): SSH password auth disabled,
`enablePostgres` option added, duplicate kdeconnectd unit removed, backup-timer
cadence documented, stale disko comment fixed, dead Qtile stub removed. Deliberately
not applied (real blockers, not oversights): #1219 NFS host-lock (no static IP for
intake device yet), #1217/#1218 Syncthing auth (still being configured), a1131
power-management (would contradict its "never suspend" rule). New findings filed as
follow-ups: #1229 (macroboard WAYLAND_DISPLAY hardcode), #1230 (periodic freeze-list
review). Full detail: `../pp/PP-NIXOS-001.md`; plan: `PLAN-nixos-migration.md`.

**Broken reference found, 2026-07-22 — `nix/CLAUDE-NIX.md` genuinely does
not exist.** Both CLAUDE.md's own reference table ("Any Nix work — file
map, locked decisions, user accounts, eval-and-fix workflow") and this
section previously cited it. Confirmed via filesystem search (repo +
`/home`): no file by that name exists anywhere reachable, not a relative-
path bug like the 12 links fixed elsewhere this sweep. Given the
2026-07-22 "we are changing unless we find a good reason not to" direction
above, authoring this file now risks documenting a soon-to-be-replaced
setup — flagging as a genuine gap for `nix-flake-maintainer` to either
(a) write for real once the migration-target question settles, or (b)
confirm it never existed and strip the dangling citation from CLAUDE.md,
rather than fabricating its contents here.

**Decision, Dave, 2026-07-24 — retain Nix as the TGW platform package
manager, not as the host operating system.** The intended landing shape is
a conventional Linux host with Nix installed only to build/install the stable
TGW facility set. The settled `tgw-flake`/lock file should provide reproducible
TGW packages and their pinned runtime/toolchain — including the state-machine
and other stable library facilities — while the host retains ownership of its
OS, desktop, networking, users, disks, mutable data, databases, logs, and
secrets. Existing host service management launches the Nix-built TGW artifacts;
mutable state must stay outside `/nix/store` and deployment must retain a GC
root/release reference. Iterated-on tools remain outside the flake under the
standing 2026-07-06 rule. This resolves the Nix role required by the next
library/platform moves; it does **not** select a final host distribution,
authorize a flake edit, or authorize host migration/cutover. Flake changes
remain the maintainer's batched, reviewed work with `nix flake check` and
host-level verification.

**Loosens the "migrate off Nix, target TBD" tension below, 2026-07-25
(Dave, direct):** TGW is not tied to NixOS as the base OS — **Lix**, the
Nix-compatible package-manager fork, installs standalone on any
conventional Linux base OS. Reproducible dev-shell/package tooling
(the 2026-07-24 decision above) does not require the whole machine
running NixOS; a host can keep its native OS and just get Lix installed
for the TGW facility set. Directly relevant to the portable-fleet program
(`PP-PORTABLEFLEET-001` below) — a laptop/tablet joining the fleet
would not need a full NixOS reinstall, just Lix.

**Two-entity shape, confirmed 2026-07-25 (Dave, direct):** "We retain the
best of Nix and get away from the userspace difficulties... move toward
the server being as close to a fully declared entity as possible, and the
portable version being its own entity, similar but oriented as a remote to
the server." Concretely: **the server (tgw-prod) moves toward MORE
declarative coverage, not less** — Lix is the mechanism for reducing the
maintenance/approval-round friction that drove the 2026-07-22 "spent more
time maintaining a maintainer" complaint, not a reason to declare less of
the host. **The portable fleet is a separate, lighter declared entity**,
architected as a client/remote to the server rather than a full mirror of
it — this is the actual resolution of the "migrate off Nix, target TBD"
tension: not abandoning declarative infra, running two differently-scoped
declared entities. **First prototype now in progress on Dave's own
laptop** (2026-07-25) — this is PP-PORTABLEFLEET-001's real first
concrete build, ahead of the a1131 state-machine-client deliverable
described there. **Tigwa holds the OS-level app inventory detail for the
portable fleet** (Dave: "check with tigwa if you want details on which
os level apps we are using") — Claude does not have that inventory yet,
coordinate with her rather than assume it.

**Standing direction changed, 2026-07-22 — promoted from FUTURE-IDEAS.md's
"mull, not decide" entry (parked 2026-07-14), which this replaces (full
prior evidence trail kept there, not deleted).** Dave: "We are changing
unless we find a good reason not to. To what and when TBD. This is out of
control. We have spent more time building and maintaining a maintainer
for this thing than it would have taken to get the whole server built
from the ground up and migrated to another OS." Plus, same session,
counting tonight's `tgw flake request-push`/`request-switch` cycles:
**"plus 3 flat stone wheels"** — three separate human-approval rounds for
what was, on paper, installing two small services.

**This is a direction, not a plan — explicitly two open questions, not
answered here:** *what* to migrate to, and *when*. Nothing about this
entry authorizes starting a migration tonight or names a target OS. What
changed is the default: staying on Nix now needs an active reason, rather
than leaving-Nix needing to win an argument against inertia.

**Evidence trail (moved from FUTURE-IDEAS.md, both entries, unedited):**

*2026-07-14, the original "mull" framing:* "NIX is great and it is also a
pain in the ass. In my experience even Gentoo was easier to maintain... I
do not like being afraid updating my system can make it unusable." Same-
day concrete cost: a per-user imperative `nix profile install` of
hermes-agent broke `hermes update` on two hosts, discovered only because
Dave hit the error directly on a1131 — backup, uninstall, reinstall,
verify, on two separate accounts. Coupling assessment as it actually
stood: application layer (`src/tgw/`) barely tied at all — plain Python
venv, git + systemd, zero `nixos-rebuild` involvement for ordinary code
fixes, portable to any distro today. OS/host layer deeply tied by
design — NixOS is the actual operating system on tgw-prod/a1131; leaving
means an OS reinstall, not a package-manager swap.

*2026-07-22, the second data point that moved this from "mull" to
"changing":* what was, on paper, "install NATS, declare a Syncthing
folder" cost, in one session: three separate failed fix attempts on the
same file before a live-verified NATS retention config worked (a
flag-parsing bug, then a unit-suffix parser mismatch nats-server/natscli
disagreed on even with *matching literal text*, then a real disk-size
miscalculation caught only by checking actual free space); a
pre-existing, previously-undiscovered Syncthing folder-loss bug traced to
the same NixOS module's override-stomping default; a live desktop-input
disruption (lan-mouse/window-switching froze) from the switch's
"reloading user units" side effect; and a still-unresolved dual-authority
NATS bug found only because the first three fixes kept failing for
reasons that turned out to be a fourth, unrelated cause. Notably: `nixos-
rebuild dry-activate` passed clean on every one of the three failed
attempts — the cost was never Nix syntax, it was live-systemd
investigation/verification after each "successful" build.

*2026-07-22, third data point, found while checking PP-POSTGRES-001's
backup contract:* the PP-BACKUP-001 A3 secrets-bundle redesign (§5.5 of
`PLAN-backup-dr.md`, decided 2026-07-18 — GDrive + a new read-only
`TGW-Secrets-Bundle` Syncthing folder to tgw-prod→a1131 + phone-mediated
fob refresh) was designed and never landed at the Nix/Syncthing config
level — grep of `~/tgw-flake` finds no `TGW-Secrets-Bundle` folder
declared anywhere, and `tgw health` confirms live: no encrypted secrets
bundle present at the expected path. Dave, confirming this wasn't simply
unstarted: "that secrets gap fix was planned but kept getting blocked by
nix/syncthing." The underlying `tgw-secrets-backup.timer`/script itself
was separately fixed 2026-07-18 (a real rclone-config bug, unrelated to
Nix) and is scheduled to fire 2026-08-01 — so the *script* isn't blocked,
only the *Syncthing-folder distribution leg* is, and only that leg traces
to Nix/Syncthing friction specifically. Filed as todo #1647 (generic
"backups WARN" triage) — worth re-tagging to note the Syncthing-folder
leg specifically once whatever comes after Nix is decided, rather than
re-fighting the same blocker a third time.

**Direct tension with this PP's own premise, named explicitly, not
resolved:** `PP-NIXOS-001` is the "migrate onto NixOS" plan; this new
direction is "migrate off Nix, target TBD." Both are now on record
simultaneously. This needs Dave's resolution at the next real planning
touch — likely candidates for how they reconcile: PP-NIXOS-001's
in-flight work (a1131, Tailscale, the flake itself) stays as-is until a
concrete "what/when" target exists for the new direction (nothing to
migrate *to* yet), or the two merge into one "where do we actually want
to land" planning pass rather than staying two separate, contradictory
standing positions. Not decided here — flagging so it isn't silently
lost the way the original 2026-07-14 entry almost was, sitting in a file
this project's own routine session-start explicitly does not read.

**Proposed-system tie-in already on record, worth re-checking once a
target is chosen:** PP-AIOPS-001 Phase 5's original (now-superseded)
design was built around systemd-nspawn + Btrfs CoW snapshots, explicitly
gated on PP-NIXOS-001, with nspawn's argued advantage being shared
`/nix/store` access — a Nix-specific argument. Already moot for that
specific case (Phase 5 REVISED, above, replaced it with bubblewrap/gVisor
specifically to avoid this lock-in) — but a useful example of the kind of
design decision that needs re-checking against whatever OS this direction
eventually lands on.

**Tailscale, 2026-07-19: DONE, both hosts authenticated, verified live.** Was parked
on Dave choosing an SSO identity provider (no plain email+password option) —
resolved same session. `tailscale status` on a1131:
```
100.114.8.24    a1131      lakeauctions@   linux   -
100.107.99.66   tgw-prod   lakeauctions@   linux   -
```
Both nodes see each other on the tailnet. No action pending.


**2026-07-19, todo #1568 (syncthing-tgw port collision):** diagnosed + fixed live —
`syncthing-tgw`'s systemd unit only ever set `--gui-address=...:8385`, never the BEP
listener (22001) or local-discovery (21028) declared in `nix/tgw/platform.nix`'s
header comment; it's been losing the port-22000 bind race to the `db` instance since
2026-07-02 on both hosts. Fix ready: `ExecStartPre` config.xml patch (idempotent,
touches only `<listenAddress>`/`<localAnnouncePort>`, never devices/folders) +
`network-online.target` ordering on both syncthing units. `nix flake check` clean,
diff uncommitted at `~/tgw-flake` on tgw-prod. **BLOCKED on invariant E13** (agent
will not commit/switch without Dave's own words directly, no relay) — needs Dave to
either do the commit/switch himself, or explicitly say the words to the session.
**Todo #1567 (extraHosts cross-host resolution fix):** same block — diff already
committed+pushed (`281185b`), dry-activated clean both hosts, only the switch is
outstanding.

**2026-07-25 consolidated Nix batch (Dave, `CLAUDE-REQUEST-2026-07-25-
consolidated-nix-flake-batch.md`; technical detail superseded/corrected by
`CLAUDE-CLARIFICATION-2026-07-25-consolidated-nix-batch-technical-inventory.md`,
which supersedes only the request's technical detail, not its one-batch
decision; ordering by `CLAUDE-DELTA-2026-07-25-nix-batch-ordering-and-
maintenance-worker.md`).** Decision: combine all currently known necessary
flake-owned changes into one task, one flake-owned branch, one review/build
evidence set, one deliberate host-switch/rollback window — do not create
incidental one-off flake edits.

**Exact inventory (corrected/verified detail):**
1. `python-multipart` is a real application dependency, not merely a test
   helper — `src/tgw/http_server.py` declares FastAPI `Form(...)` endpoints,
   and FastAPI requires `python-multipart` at route registration; it is
   absent from `pyproject.toml` and from the flake Python package
   dependency list/dev shell. Include its source dependency declaration and
   Nix package/runtime/dev-shell closure in this batch.
2. `mistune` is an application dependency already represented in the
   flake's `tgwPackage` dependencies and `pyproject.toml`, but omitted from
   `devShells.default`, causing eight `/docs` tests to fail. Add it to the
   dev-shell dependency set. **Independently corroborated** by a second
   reviewer, `HERMES-INDEPENDENT-REVIEW-2026-07-25-yesterday-fixes.md`:
   through the separate owner flake at `/home/db/tgw-flake` (which does
   evaluate), `tests/test_http_server.py` ran 328 passed/8 failed with
   `python-multipart` temporarily added only to the test invocation — the
   eight failures are the same missing-`mistune` `/docs` tests; without the
   temporary `python-multipart` addition, collection fails before any tests
   run at all, confirming both packages are genuinely absent from the
   declared dev shell (temporary additions were ephemeral audit inputs
   only, no flake/production config changed).
3. The source checkout's flake evaluation failure is a committed absolute
   symlink, not a declared `home` input:
   `/opt/TGW/src/trader-grims-warehouse/flake.nix` is a tracked symlink
   whose content is `/home/db/tgw-flake/flake.nix`; from the source Git
   checkout, Nix rejects that external path with `Path 'home' does not
   exist in Git repository`. The batch must choose and implement one
   canonical/reproducible source-to-flake relationship — not retain an
   absolute home-directory symlink as a tracked repository contract, while
   preserving Dave's flake ownership and avoiding copying/diverging flake
   authority.
4. a1131 persistent access gap: tgw-prod already declares `db` in group
   `tigwa`; a1131 currently declares `db.extraGroups = [ "hermaroid" ]` only
   — it lacks `tigwa` membership. Add the approved host-local extension and
   the reviewed non-secret shared-output-root mechanism in the same batch.

**Items 1-3 superseded, verified 2026-07-25 (Claude) — Dave already built
the fix, on an unmerged branch this session didn't know about yet.**
`todo/consolidated-nix-source-20260725` (2 commits, also on `origin`,
authored by Dave 2026-07-25 10:35/10:39, predating this inventory being
written) replaces the tracked absolute symlink with a proper source-adapter
`flake.nix` (13 lines, pins `tgw-flake` as a `git+ssh` input) and adds
`python-multipart` to `pyproject.toml`. Verified directly: `nix flake show`
now evaluates cleanly from the source checkout, and both `python-multipart`
and `mistune` import fine in the resulting dev shell. **Not yet merged into
`catio-nix-0.0.1-alpha`.** One gap found during verification, not yet
resolved: a bare `nix develop -c pytest` in that branch's worktree fails
all test collection with `ModuleNotFoundError: No module named 'tgw'` — the
new dev shell doesn't put `src/` on `PYTHONPATH` or do an editable install.
Item 4 (a1131 group gap) is unaffected by this branch and still open —
tracked as todo #1688.

**Ordering decision:** this batch is the first executable flake task after
the completed tgw-prod Btrfs recovery snapshot and read-only git baseline —
it removes the current reproducible-test blockage, establishes the shared
evidence/access substrate, and prevents repeated one-off Nix investigations
and context/token drain while regular development proceeds. Read-only
reconciliation, code review, workflow mapping, and other investigation may
continue in parallel; source-fix acceptance, host-dependent feature work,
and all additional flake changes remain gated on this batch's evaluation/
build evidence, Dave/flake-owner review, controlled switch decision, and
post-switch verification.

**Standing destination:** a steady-state flake-maintenance worker replaces
interruption-driven Nix work. Its bounded role: collect requests, maintain
the next-batch inventory, obtain reproducibility/build evidence, prepare
review/rollback receipts, and verify stated results after an approved
switch — no unilateral authority to edit the flake or switch hosts. New
Nix requirements join the next bounded maintenance batch rather than
stalling unrelated development or reopening full planning context each
time.

**Parked note, 2026-07-22 (Tigwa relaying Dave,
`TIGWA-NOTE-hermes-desktop-nixos-runtime-defer-2026-07-22.md`) — Hermes
Desktop node-pty/Electron-under-nix-ld issue, explicitly deferred, not a
todo.** During a Hermes Desktop update, npm failed rebuilding `node-pty`
(`gyp ERR! stack Error: not found: make`), leaving no packaged Desktop
artifact. Tigwa rebuilt it using temporary Nix build inputs (`gnumake`,
`gcc`, `pkg-config`), restoring
`/home/tigwa/.hermes/hermes-agent/apps/desktop/release/linux-unpacked/Hermes`
with `node-pty` staged successfully. Remaining finding: a direct launch of
the packaged Electron binary on this NixOS host lacks required runtime
shared libraries (GLib/GTK/NSS) under the current `nix-ld` library set;
supplying an Electron runtime library path clears the loader failure, and
the subsequent missing-display error is expected from Tigwa's
non-graphical session, not a package-build failure. **Dave's direction:
defer the durable Nix/runtime decision until Dave verifies the new Nix
maintainer** — do not edit the flake, alter `nix-ld`, add a wrapper, change
Hermes runtime configuration, restart services, or treat the temporary
rebuild environment as permanent. When authorized, the decision should
compare a narrow host-level `nix-ld` library declaration against a
Hermes-specific launch wrapper, with an actual graphical-session launch
test and rollback evidence. Parked, not urgent.

## PP-PHOTO-001 — photo pipeline (GDrive → Gemini / eBay)
Sync infra live. Phase A (GDrive→Gemini multimodal draft) #1064; Phase B
(zero-bandwidth EPS upload) #1065. FROZEN until R1 drains.

**Phase B reassigned to Tigwa, 2026-07-16 (Dave):** "gdrive zero-bandwidth
send to tigwa, that's on top of gdrive archive layer/git-annex." Sits on
top of PP-ANNEX-001/PP-KNOWLEDGE-001's storage-layer work, her territory
now — not a Claude build item going forward. Phase A (#1064) unaffected,
stays as-is.

## PP-CLIP-001 — clipboard manager (local-only, ratified 2026-07-11)
Phase 1 done; crash loop fixed s41. Phase 2 rofi picker DONE
(DONE-1055-clip-picker.md). **#1086 conceptual pass's split RATIFIED
2026-07-11: tgw-clipd + rofi picker stay LOCAL-ONLY forever.** Cross-machine
sync (the old "Phase 3" line) is **retired here, moved entirely to
PP-EVENTD-001** — `lan-mouse enter_hook` calls `clip-route --target`
directly; `clip-route` reads the clipboard itself, never routes through
tgw-clipd. Design: `pp/PP-CLIP-001.md`. Full #1086 analysis:
`docs/ai-plans/clipboard-concept.md` / `CLIPBOARD-CONCEPT-PLANNING-1086.md`.

**Elevated vision, 2026-07-19 (Dave, via PP-OUTBOX-001 design session):** "What
ends up being floatable and a seriously useful operations interface is the
clipboard altogether." `tgw-clipd`/the rofi picker is being reframed as TGW's
general-purpose floatable operations surface — typed entries (SKU, URL,
prompt, combined-buffer), each a discrete handler sharing one interface,
inline per-entry mini-apps instead of separate app windows. PP-OUTBOX-001's
instruction outbox is the first serious application of this surface, not a
separate feature. Design-only, not yet build-authorized — full discussion in
`pp/PP-OUTBOX-001.md`.

**SUPERSEDED same day, 2026-07-19 (see PP-RADAR-001):** the "primary interface"
framing above is superseded — Radar (server-based, encrypted, explicit-recipient
delivery) is now the intended proper replacement for network clipboard sharing.
`tgw-clipd`'s local-only Phase 1/2 work stays valid (nothing built here is being
undone), but it's no longer the target primary surface; it may persist only as a
local input/output adapter into Radar. Design context only, not build-authorized.

**2026-07-19, todo #1563/#1565 DONE:** `deliver_clip()` + `tgw clip deliver` CLI verb +
`tgw_clip_deliver` MCP tool (READONLY-gated, same pattern as `tgw_enqueue`), origin/label
columns on `clip_history`, rofi picker id-based-lookup bugfix, and `tgw-clipd` secret
exclusion (x-kde-passwordManagerHint MIME check + entropy/prefix heuristic in
`process_change()` keep password-manager/API-key-shaped content out of persistent
history). Reviewed, merged, pushed to `origin/master`, `tgw-clipd` restarted live.

## PP-RADAR-001 — current-entry heads-up panel (Dave direction, 2026-07-19)
Todo #1573 (Tigwa): turn Dave's current-entry heads-up-panel direction into a
decision-ready design. Split out from PP-EVENTD-001/PP-CLIP-001's floatable-surface
work — not yet designed. Owner: Tigwa.

**Sequencing, per Dave (2026-07-19):** Radar (the "precognition"/anticipation layer —
showing applicable context before a request) is the **second** event surface, scoped
to whatever data is actually available to disseminate at the time it's built. **The
tool comes first** — PP-EVENTD-001's `clip-route` daemon (the first event surface) has
to exist and be feeding real data before Radar's anticipation layer has anything to
scope against. Don't design PP-RADAR-001 ahead of PP-EVENTD-001 landing.

**Settled direction, 2026-07-19 (Dave, via Tigwa heads-up, #1573):** Radar is to become
the proper replacement for insecure network clipboard sharing — server-based,
encrypted, delivered directly to a selected named host/device, in the interaction
spirit of `kdeconnect-cli`. No ambient OS-clipboard capture, mirroring, sniffing,
broadcast, or persistent network spew — networked clipboard movement happens only
through explicit Radar `copy`/`send`/`pick` operations addressed to a recipient, each
with authenticated/encrypted transport, a receipt/audit record, expiry/cleanup, secret
exclusion, and local-recipient insertion gated by an approved action contract. **This
supersedes `tgw-clipd`'s earlier "primary interface" framing** (see PP-CLIP-001) — a
clipboard-linked adapter may exist later, but only as a deliberate local input/output
boundary into Radar, not the primary surface.

Radar itself is the librarian/operator layer, not a TGW-substrate redesign — built on
git-annex (file identity/hashes/versions/redelivery), Syncthing (selected artifact-view
distribution incl. Android), Flutter (operator UI), Tailscale (private reachability),
Recoll + NATS JetStream (retrieval/event substrate). It compiles current-entry context
server-side from authoritative sources and returns only the relevant context/tools to
clients (for a SKU: title/price + direct Flutter/eBay/history/solds/Complete-Toolkit
links), not broad client-side scraping. Files/charts route through annex+Syncthing, not
forced through entry text.

**NATS-vs-LISTEN/NOTIFY resolved, 2026-07-22:** a same-week scouting pass
flagged this line as an apparent contradiction against PP-EVENTD-001's
explicit "Transport: PostgreSQL LISTEN/NOTIFY... NOT NATS" — traced to
origin: this NATS mention comes from Tigwa relaying Dave's own direction
(`inbox/archive/TIGWA-HEADSUP-PP-RADAR-001-clipboard-direction-2026-07-19.md`),
not a Claude invention, and was never actually reconciled against
clip-route's transport choice because Radar hasn't reached design
maturity yet (#1573 still open). **Not a contradiction — two different
jobs at two different maturity levels:** clip-route/PP-EVENTD-001 needs
only a cheap "current item changed, refresh the UI" signal (LISTEN/NOTIFY
is correct and sufficient); Radar's job is broader — durable, replayable
artifact/retrieval event distribution across devices — which is exactly
what JetStream is for. **If/when Radar is built, its JetStream use
rides the same broker PP-AIOPS-001 stood up 2026-07-22** (native
NixOS `nats-server`, tgw-prod-only, JetStream enabled) as a fourth
consumer alongside the audit stream/`agent_handoff`/mailbox — not a
second NATS deployment. Still gated on #1573 (Tigwa's concrete design)
before any of this is build-ready; this note only removes the apparent
conflict, it doesn't authorize building Radar's event layer.

**Explicitly design context / not build authorization** — do not build, change
services, or (re)configure Syncthing/KDE Connect from this note alone. Tigwa's #1573
proposal brings the precise data/action/transport contract for Dave's review next.

**Dave confirmed direction, 2026-07-19 (same session):** "I believe it is the correct
direction due to security and database access." Rationale: ambient/broadcast clipboard
sharing is an open security surface (no auth, no audit, no scoping to a database
access boundary) — Radar's explicit-recipient, encrypted, audited delivery model closes
that gap by construction. **Status: BUILD-AUTHORIZED, 2026-07-19 (Dave: "make it so")** — the direction itself
(server-based, encrypted, explicit-recipient, kdeconnect-cli-style delivery,
superseding ambient clipboard sharing) is cleared to build once a concrete spec
exists. This authorizes the *direction*, not a blank check to build without one:
Tigwa's #1573 proposal (data/action/transport contract) is still the artifact that
turns this into an actual work packet — once it lands, it can move to implementation
without another round-trip on whether the direction itself is right.

**Staged sequencing, clarified 2026-07-19 (Tigwa relaying Dave, correcting a possible
misread):** #1573 is NOT a gate on today's `clip-route` work — do not present it as
the sole blocker to Radar-lineage progress. The actual order:
1. **Build PP-EVENTD-001 / Go `clip-route` now** — design complete, PP unfrozen,
   PP-CLIP-001 Phase 2 done, already unblocked; this is the recognized-input/
   active-context foundation and needs no further sign-off to start.
2. **Feed and observe real data from `clip-route`** — establishes what current-entry
   context is actually available, instead of #1573 inventing a Radar contract from
   assumptions.
3. **Then Tigwa completes #1573** — translates that real surface into the concrete
   Radar data/action/transport contract (anticipatory heads-up layer, explicit-
   recipient encrypted clipboard replacement, artifact lifecycle integration).
4. **Then build PP-RADAR-001 against that proven surface** — the direction is already
   settled/build-authorized (above); #1573 supplies what to build it against.
Do not build the full Radar heads-up/clipboard-replacement layer prematurely or as a
parallel design based on imagined event data — but equally, don't hold `clip-route`
waiting on #1573.

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
Pulled out of the "Done" rollup — never actually done. Real code exists (Dio offline
layer, sqflite outbox, snapshot-atomic-sync) but never installed on its target device
(a1131), never live-verified. Deep review found real structural gaps (no conflict
resolution, no backchannel, manual connectivity detection unused). **Correction
2026-07-17 (Dave): the real problem is more basic** — two known devices on the same
LAN, app has never once successfully launched/connected. This "does it even start"
problem takes priority over the phased remediation plan (Phase A harden / B
backchannel / C conflict-resolution). Also revealed: an existing undocumented
Tigwa-built wrapper already reaches `tgw` without the Flutter app — see
`reference/TGW-a1131-CLI-Wrapper.md`. **RESOLVED, todo #1492, 2026-07-17/18
— this text was itself stale until corrected 2026-07-22**: the app
launched cleanly and connected live on tgw-prod's own Sway desktop
(Dave's actual first-ever look at it working) — home screen, ONLINE
badge, live per-queue job-state grid, cross-checked against real
`queue_jobs` counts, screenshot captured
(`plan/packets/results/evidence/1492-tgw-app-launched-online.png`). The
"does it even start" blocker is cleared; Phase A/B/C (harden/backchannel/
conflict-resolution) are unblocked to resume whenever prioritized. Full
detail: `pp/PP-PORTABLE-CATALOG-001.md`.

**Deployment-image direction — Dave decision 2026-07-25:** Helicrew's current
MX image is the deliberately broad development/reference image, not the final
portable-fleet release; it may retain development-only tools such as Waydroid.
The final shipping/satellite platform is a separate, lean production baseline
and may be built from scratch. Nix owns only the stabilised infrastructure
contract (identity/accounts, SSH posture, shared roots, required runtimes,
role boundaries, and enabled services); MX/image capture and documented
per-role overlays own desktop/application choice. The completed cycle is:
production base image → role enrolment and post-boot secret provisioning →
optional documented development overlay. When that base is accepted, Helicrew
is reimaged from it and receives only its declared developer/recovery overlay.
No device gains a second production ledger/worker authority. This records
architecture and sequencing only; it does not authorise a final ISO build,
a host switch, flake change, or credential deployment.

**Cross-reference, 2026-07-25:** this PP is now one of three separate-app
tracks under the new parent program **PP-PORTABLEFLEET-001** (below) — the
Flutter catalog app specifically (tablet pilot cohort), alongside the `tgw`
state-machine client and the native Kotlin camera/intake app. See that PP
for the full-program buildout order and per-device enrollment/acceptance
contract.

**Nix change lane for this PP, Dave 2026-07-25 (`HERMES-EXECUTION-PLAN-
DELTA-2026-07-25.md` item 2) — the required workflow shape for any Nix
package-manager change this PP needs, not a general Nix process:** intent →
host/package/module inventory → reproducibility policy → evaluation/build
evidence → Dave/flake-owner review → batchable rebuild/rollback →
post-switch verification. No convenience package install or flake change
by an agent outside this lane.

## PP-PORTABLEFLEET-001 — portable fleet buildout program — NEW 2026-07-25

**Decision, Dave, 2026-07-25 (`CLAUDE-REQUEST-2026-07-25-portable-fleet-
buildout-program.md`): begin the full portable buildout.** The portable
fleet is a product/operations program, not a collection of independently
installed apps. New parent PP — cross-links **PP-PORTABLE-CATALOG-001**
(the Flutter catalog app, one of the three separate-app tracks below) and
**PP-CATIONIX-001** (the broader agent-confinement/"dev team" platform this
program's per-device least-privilege/revocation contract mirrors).

**Authority and architecture:**
- **One production authority:** tgw-prod retains the canonical PostgreSQL
  `state_machine`, catalog, workers, external-action authority, and
  evidence library.
- **Portable clients:** laptops, tablets, and capture devices receive
  scoped packages/apps and talk through approved APIs/configuration — they
  never start a competing production PostgreSQL, worker, or eBay
  authority.
- **Private-network substrate:** every fleet device gets a named Tailscale
  identity, defined role, least-privilege policy, owner/custodian,
  lifecycle/revocation record, and verification receipt.
- **Separate apps, shared contracts — do not collapse these three:**
  1. `tgw` state-machine/operations client — first Nix-installed portable
     capability, landing on a1131 then later laptops.
  2. Flutter Portable Catalog — browse/edit/offline-cache + controlled
     outbox workflow (PP-PORTABLE-CATALOG-001).
  3. Native Kotlin Camera/Intake app — barcode/photo/video/location/
     attribute capture (PP-INTAKE-004), standalone first, event-bus
     participation later.

**Fleet cohorts and delivery order:**
1. **Foundation/Nix batch** — resolve source/flake reproducibility,
   dependencies, shared non-secret output path; install the Nix-built
   `tgw` client on a1131 against canonical tgw-prod state (first concrete
   deliverable, below).
2. **Laptop cohort** — Dave laptop and shipping laptop: enroll Tailscale,
   record device identity/role, install the client package, prove
   authenticated read-only state-machine access, define shipping-only
   permissions/workflow separately from general administration.
3. **Catalog tablet pilot** — one named tablet: install/test the Flutter
   catalog app against the real but scoped service; prove online browse,
   offline cached browse with a visible freshness marker, and a
   controlled outbox/reconnect scenario before broad tablet rollout.
4. **Capture cohort** — camera/other tablets: one designated capture
   device first, verify the native intake app's standalone capture path
   before event-bus/remote-control additions; preserve raw capture
   provenance and explicit operator acceptance for state-changing intake.
5. **Expansion** — remaining tablets/cameras added only from a verified
   cohort template, each device with its own enrollment and acceptance
   receipt.

Every cohort step shares one **per-device enrollment record and
acceptance gate**: human-friendly name, stable Tailscale node/device
identity, owner/custodian, physical role/location, OS/version, app/
package versions, permitted services/actions, data classification/cache
policy, offline behavior, revocation/loss procedure, last verification
time. Required acceptance before a device is called operational: appears
in the approved tailnet under its intended name/least-privilege policy;
reaches only its approved TGW service surface over Tailscale; has the
correct client/app from the approved package/release path; demonstrates
its role with a bounded fixture or read-only production check;
demonstrates offline/degraded behavior where applicable; has documented
revoke/wipe/replace recovery.

**Non-goals for the first cohort:** no second production state machine,
no ambient camera/clipboard collection, no bulk device enrollment without
inventory, no eBay/listing authority on a new device, no background
worker/agent activation merely because a device joins Tailscale.

**Immediate blockers, as of 2026-07-25 (partially superseded same day —
see laptop-cohort prototype below):** a1131 and tgw-prod were the only
currently-visible healthy tailnet nodes as of the morning of 2026-07-25 —
no laptop, shipping laptop, tablet, or camera node was yet enrolled.
Device enrollment requires physical-device access and an authenticated
Tailscale enrollment action; auth keys/account secrets never go in chat
or Plan Vault. Flutter app, native intake app, and shipping workflow need
a named first pilot device and its actual OS/hardware facts before
installation commands or permissions are selected.

**Laptop-cohort prototype underway, 2026-07-25 — "Helicrew" (Dave's own
laptop), verified snapshot from Tigwa/Hermes:** a named, revocable
Tailscale client of tgw-prod, explicitly not a second TGW authority.
Confirms the two-entity direction above: native OS (Debian 13/trixie,
kernel 6.12.96, apt/dpkg), Tailscale 1.98.9 active, **no Nix or Lix
installed, `/nix` absent** — evidence for the native-OS/lighter-client
direction, not evidence a Lix/Nix client layer has been designed yet.
Current OS-level facilities: core dev/operator utilities (git, curl,
wget, ripgrep, sqlite3, tree, tmux, htop, jq); development/reference
desktop facilities (Waydroid 1.6.2, KDE Connect 25.04.2, Syncthing
1.29.5); a local-only exception, PostgreSQL 17 + pgvector 0.8.0, used
solely by a loopback-bound Hindsight memory service (not a TGW catalog
DB, worker fleet, or queue authority). Tigwa-owned user-local tools live
under `/home/tigwa/.local/bin/` (Hermes Agent, Claude Code, OpenAI Codex
CLI, AGY), state kept under her home rather than `/opt/TGW`. **Explicit
exclusions, stated by Tigwa:** no TGW production Postgres/catalog
authority, no worker fleet/queue authority/eBay actor, no copied
production secrets/eBay tokens, no general remote-desktop-bypass
operating model, no assumed NixOS reimage or Lix/Nix rollout, no claim
every Helicrew dev package belongs in a shipping image — this
snapshot is not yet a final fleet declaration, Nix/Lix module, or
production-image spec.

**Tigwa's recommended three-layer split for this PP, not yet actioned:**
(1) production/server declaration — tgw-prod authority, increasingly
declarative NixOS/Lix-managed coverage; (2) portable-client minimum —
native OS, Tailscale, scoped client/app/browser access, declared user/
runtime paths, no competing authority; (3) Helicrew development overlay
— Waydroid/KDE Connect/Syncthing/experimental tools/local Hindsight and
other explicitly justified dev-only facilities, not part of the shipping
client minimum. Next planning action per Tigwa: turn the portable-client
minimum into a small role/package manifest with exact remote interfaces
and acceptance checks — not yet done. This snapshot does not authorize a
flake edit, Lix install, OS reimage, secret enrollment, service cutover,
or replication.

**First concrete deliverable — a1131 production state-machine client
(`CLAUDE-ADDENDUM-2026-07-25-a1131-production-state-machine-client.md`):**
verified current state — a1131's flake host is deliberately
`bases/portable.nix`, TGW module enabled but `workers = []`,
`enableHttp = false`, `enablePostgres = false`; the full `nix/tgw.nix`
state-machine/PostgreSQL/worker module exists but its package option isn't
wired yet, and a1131's current runtime uses an out-of-band venv path with
no `tgw` Nix package installed. First-batch addition: install the
flake-built `tgw`/state-machine **client** package declaratively on
a1131, proving it can read the canonical tgw-prod `state_machine` ledger
through the approved configuration/auth path. This means "production
state machine on the laptop" as the *same production code and protocol*
pointed at the *one* authoritative production ledger — explicitly **not**
a second local PostgreSQL database, worker fleet, or competing queue
authority (that would need separate replication/conflict/recovery/
authority decisions, out of scope for this batch). Required verification:
package arrives via the reviewed flake switch, not `pip`/a one-off Nix
profile install; `tgw` is available to the intended user; a bounded
read-only state-machine query against tgw-prod succeeds; no a1131
PostgreSQL service, TGW worker, HTTP service, or external-action
capability starts as a side effect; configuration/auth material stays
least-privilege with non-secret output paths kept separate.

**Success bar — Dave's "morning coffee" functional equivalence
(`CLAUDE-CLARIFICATION-2026-07-25-portable-fleet-morning-coffee-operator-
goal.md`):** with his laptop over morning coffee, Dave can operate TGW as
though seated at the primary system — not merely VPN reachability or a
remote shell. Through approved Tailscale-connected interfaces he must be
able to: (1) see current truthful system health, queue/work state,
blocked work, and next eligible work; (2) search and inspect inventory,
photos, listing/pipeline evidence, and relevant history; (3) create,
resume, reorder, pause, and work a human or AI-assisted work queue
continuously, including the Next Item handoff; (4) inspect and correct
authorized operational data with field-level error/recovery guidance;
(5) take an explicitly authorized action (including an external action
only after its named confirmation/gate) and see the durable resulting
state and next work; (6) reach the same authoritative evidence and
decision paths without scavenger-hunting among hosts, terminals, or stale
replicas.

**V1 acceptance — Laptop Coffee Console:** the first laptop cohort is
complete only when a named laptop can, over Tailscale: launch the
Nix-managed `tgw` client and approved browser/app surface; query the
canonical tgw-prod state-machine read model and current health/queue
summary; open a selected item and its history/evidence; complete one
bounded, non-external workflow item and receive the next eligible item
with context; show an intentionally held/blocked item and its
prerequisite rather than hiding it; preserve all actions/outcomes in the
canonical ledger; lose/recover connectivity with clear degraded state and
no silent local authority. Boundary: functional equivalence does not
create a second production database, worker fleet, secret store, or
broad remote-desktop bypass — the portable device remains a named,
revocable, least-privilege client of tgw-prod; camera capture and tablet
workflows extend the same contract only after the laptop path proves it.

**Status: program decision + first-deliverable spec recorded, nothing
built yet.** Foundation/Nix batch is gated on PP-NIXOS-001's consolidated
Nix batch (see that PP's 2026-07-25 sub-entry) landing first.

## PP-REMOTEOPS-001 — device/communications fleet architecture — NEW 2026-07-21
Surfaced from a real operational pain, not a hypothetical: Dave is doing live
shipping at the satellite warehouse against `tgw.source`'s old shell
functions, now stale/wrong (invalid SKUs) since the Nix/tgw-api migration
broke the old model without anything replacing it — "this thing simply
isn't usable and I have been fighting that trying to get this planned."

**The actual gap, once named: `tgw.source` "did not care which machine I
used" because it worked directly against a shared filesystem — that model
is gone by design now (`tgw-api` is the fence, Postgres is the ledger), so
the replacement has to be "every device reaches the same central
services," not "sync the files again."** Five tiers, all on top of
Tailscale as the one transport (confirmed live 2026-07-21: `tgw-http`
already listens on `0.0.0.0:7373`, reachable from any Tailscale-joined
device today):

1. **Transport** — Tailscale mesh (`lakeauctions@` tailnet). Only
   `tgw-prod` + `a1131` currently joined. **Correction 2026-07-21 (Dave):
   not a blocker** — joining any new host takes ~2 minutes (`tailscale up`
   + approve), trivial to do whenever a device is in hand. Sequenced as
   Phase 0 below precisely because it's cheap, not because it's hard.
2. **CLI parity** — a thin SSH wrapper running the real `tgw` CLI on
   tgw-prod from any joined device, no local install/venv needed. Half-
   built once already: `reference/TGW-a1131-CLI-Wrapper.md` (Tigwa's
   `~/.local/bin/tgw-prod` + fish function). Generalizing this to every
   device is this tier's job.
3+4. **Portable Catalog** (owned by [[PP-PORTABLE-CATALOG-001]], not
   duplicated here) — the merged web+offline Flutter client. Confirmed
   2026-07-21: tiers 3 (web parity) and 4 (mobile-native/offline) are
   planned to merge into one client. **Corrected 2026-07-22**: this
   section's "never once successfully launched/connected even on a1131"
   claim was already stale — todo #1492 (2026-07-17/18) proved the app
   DOES launch and connect cleanly, live-verified with a screenshot, real
   backend cross-check via `queue_jobs`. The nuance that matters for THIS
   PP specifically: **that test ran on tgw-prod itself** (localhost,
   `127.0.0.1:7373`), not a1131 — because #1492 also found **a1131 has no
   Flutter SDK/toolchain at all**, so the app has literally never been
   able to run there, not "ran and failed to connect." Filed as #1527
   (still open): needs Dave's decision on whether a1131 gets the Flutter
   toolchain, or whether "the two known devices" meant something else.
   **This, not a broken client, is the actual remaining bottleneck** for
   this tier — the engineering (Dio/sqflite talking to `tgw-http`) is
   proven; the open question is which device runs it.
5. **Notification/approval forwarding** — Telegram today (notify-only),
   [[PP-APPROVAL-001]]'s typed-handler design (decided, not built) for
   anything needing a real approve/deny action from wherever Dave is.

**Phased plan, 2026-07-21:**

- **Phase 0 — Tailscale-join every operating device.** ~2 min/host
  (`tailscale up` + approve on the admin console). Do this opportunistically
  whenever a device is physically in hand — satellite-warehouse
  laptop/phone first, then the 4 tablets / general-purpose pads as each
  gets pulled into a worksurface role. Not gated on anything else; cheap
  enough to just do, not worth its own todo.
- **Phase 1 — Portable Catalog basic launch/connect: PARTIALLY DONE,
  corrected 2026-07-22.** Todo #1630 (still open) was scoped as "diagnose
  + fix launch/connect on a1131, never successfully connected even
  LAN-only" — that framing is now known imprecise: launch/connect IS
  proven (on tgw-prod, #1492), the actual remaining gap is a1131 lacking
  a Flutter toolchain (#1527, blocked on Dave's device decision). #1630
  should be re-scoped once #1527 answers which device this targets,
  rather than continuing to chase a "connection" bug that isn't the real
  blocker. LAN-only zero-Tailscale-dependency framing still correct.
- **Phase 2 — Point the same client at the Tailscale IP instead of the
  LAN IP, prove it from the satellite warehouse.** Should be near-zero
  extra work once Phase 1 works — a Tailscale IP is just as routable as a
  LAN IP to the client, no protocol difference. This is where satellite
  catalog visibility for shipping actually lands.
- **Phase 3 — Offline resilience, live-verified, not just present in
  code.** The Dio offline layer + sqflite outbox already exist
  (PP-PORTABLE-CATALOG-001) but have never been tested against a real
  disconnect/reconnect cycle. Acceptance bar (Dave): "the operator never
  even knows the internet went down" — queued writes flush invisibly on
  reconnect, no error dialog, no lost action.
- **Phase 4 — Task-specific worksurfaces**, built one at a time,
  each independently verified before the next: shipping (SKU lookup +
  mark-shipped once that feature exists, see the still-open shipping-
  pipeline gap this thread started from) is the first candidate since
  it's the named live pain. Location moves, intake status, etc. follow
  the same pattern. Stacked/HUD management view (Phase 5) never precedes
  an individual surface working on its own — see the build-order
  correction below and in `PP-LISTEDITOR-001`.
- **Phase 5 — Stacked heads-up management view**, composed from Phase 4's
  proven individual surfaces, for oversight/management use only — not a
  replacement for the individual worksurfaces.
- **Notification/approval forwarding** (tier 5 above, [[PP-APPROVAL-001]])
  runs in parallel to this phase list once its own typed-handler design is
  built — not blocking, not blocked by, Phases 0–5.

**Build methodology correction, same session (Dave):** build individual
work surfaces first, verify each works, and only then compose a stacked
"heads-up" view for management/overview purposes — never the reverse.
Triggered by Dave noticing Claude itself gets confused reviewing
`PP-LISTEDITOR-001`'s current stacked page (Set A `item_attributes` /
Set B `draft_listing.item_specifics` shown together) — live evidence that
even a careful reviewer loses track of which field belongs to which set
when they're merged on one surface before either is proven independently.
This is the same boundary invariant C12 already protects at the data
layer; this note extends it to the UI-build order as well. Applies to any
future worksurface (Portable Catalog's screens, PP-EDITOR-001's pages),
not just the listing editor.

**Phase 1 progress, 2026-07-21 (todo #1630, in progress):** a1131's stale
checkout (#1082, confirmed live: `99fd1fb`→`812f691`) fast-forwarded and
cleaned. Two real root causes found and fixed: (1) `tgw_app` had never
been built on a1131 at all — Flutter 3.32.0 installed via
`nix-shell -p flutter`, no system install; (2) `api_client.dart` defaulted
`base_url` to `127.0.0.1:7373`, dead on a1131 — Dave authorized copying
the live bearer token to `~/.config/tgw/api-key` (auto-mode classifier
correctly blocked this until explicit go-ahead) and pointing
`~/.config/tgw/base-url` at `http://192.168.60.100:7373`. Build then hit a
real app-code bug: Flutter 3.32.0 removed `DropdownButtonFormField`'s
`initialValue:` (now `value:`), 4 call sites across
`browse_screen.dart`/`edit_item_screen.dart` — filed as **todo #1631**,
dispatched to `tgw-coder` per invariant E12, reviewed and build-verified
live on a1131 (`flutter build linux --release` → zero errors,
`packets/results/1631-REVIEW.md`), stitched into
`catio-nix-0.0.1-alpha` (commit `15d7210`), **#1631 done**. `tgw_app` now
**builds** successfully on a1131 for the first time ever.

**Not yet done — GUI-launch verification, held per Dave's request
2026-07-21:** the build has not actually been launched/eyeballed yet.
a1131 runs a live shared Sway session (seat0, user db) Claude/Dave both
use, so popping a GUI window needs a heads-up first (live-desktop-notice
feedback rule) — Dave asked to hold this specific step for a fresh pass
rather than mid-thread. Next: launch the bundle
(`apps/tgw_app/build/linux/x64/release/bundle/tgw_app`, no launcher
script installed on a1131 yet) against the live Wayland session, confirm
it actually opens and can browse/reach the catalog over
`http://192.168.60.100:7373` — get real visual confirmation (screenshot or
Dave's own eyes), not just "binary built." This is Phase 1's actual
completion condition.

Todo opened: fix Portable Catalog basic launch/connect on a1131 (blocks
all downstream PP-REMOTEOPS-001 work). Not yet opened: satellite Tailscale
join, worksurface-split-first pass on the listing editor page — both
design-decided, not build-ready yet.

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

**Both authority gaps from the triage fixed same day, 2026-07-18 (#1546/#1547):** Store
Category dropdown now sourced from `_live_store_categories()` (live `get_store_categories()`
GetStore call, TTL-cached 15min, `http_server.py`), falling back to the old
`category-groups.json` list only if the live call itself raises — verified live: 59 categories
fetched from the real account, no fallback triggered. Fulfillment-policy dropdown now sourced
from a new `get_fulfillment_policies_full()` (`ebay/sync.py`, live `/sell/account/v1/
fulfillment_policy` call) via `_live_fulfillment_policies()`, same TTL-cache-with-fallback
shape — verified live: 31 policies fetched. Both surface a visible "⚠ local list/cache, live
fetch failed" warning next to the dropdown if the fallback path is used, so a stale/wrong list
is never presented as authoritative without saying so (C14 principle). Return-policy dropdown
intentionally left on the static cache — not flagged in the original triage. `tgw-http.service`
restarted, `tgw health` clean (same 2 pre-existing unrelated failures: `backups`,
`ebay_sync_fallback`/#1077).

**UI authority triage delivered 2026-07-18 (#1543):** read-only current-state
triage of the 4 anchor findings from `TIGWA-NOTE-seller-hub-ui-authority-
findings-2026-07-18.md` (Store Category dropdown, fulfillment-policy
dropdown, category + dependent controls, supporting-data linkage) plus 5
related parity incidents. Result: conditions metadata, aspects metadata
(Taxonomy API), Best Offer control, custom-aspect visibility, and
category-change data-preservation design all classify **resolved**. Two
**open** gaps found (net-new, not previously documented): (1) the Store
Category dropdown's option list comes from local `category-groups.json`,
not the live `get_store_categories()` GetStore call TGW already has
working code for (only used at push time, not to populate the picker);
(2) the fulfillment-policy dropdown is driven by a static
`ebay-fulfillment-policies.json` cache, not a live pre-selection Account
API call — a live reconciliation net catches drift only *after* push. C14
classifies **partial**: detector built 2026-07-18 (#1468) and green for
item-detail/aspects/bulk-edit/`accept_proposals`, but two new C14-class
bugs surfaced while building it remain open (#1522: unlocked
title/description silently reverted by padlock auto-sync; a second
instance noted but not fully re-read, needs a direct follow-up).
Full triage: `inbox/tigwa/CLAUDE-TRIAGE-sellerhub-ui-authority-2026-07-18.md`.

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

**SHCS audit Phase 0 scoping packet DRAFTED, 2026-07-22** — Tigwa (at
Dave's direction) requested this move up: the "not-yet-run Gemini audit"
above is now scoped concretely as a **Seller Hub Capability Specification
(SHCS)** register — full surface (not just listing creation), each row
carrying real account evidence + TGW code/runtime evidence + a
`full-parity`/`partial`/`guessed-local-substitute`/`read-only-only`/
`absent`/`intentionally-deferred`/`blocked-unverified` status, never a
model-guessed value presented as authoritative (Best Offer stays an
explicit checkbox, never inferred). Runs as its own **capacity-funded
discovery lane** (same three-lane shape as PP-POSTGRES-001), never
blocking critical-integrity or product/harness work. Model-spend synthesis
is explicitly NOT authorized by this packet — read-only UI/code evidence
collection only, until a separate costed decision. Also tracks **Dave's
own better-than-Seller-Hub enhancement ideas** (Radar anticipatory
context, provenance-visible controls, etc.) in a parallel table — distinct
from the parity register, no idea gets promoted to a build task just by
being recorded. Full packet + integration matrix (ties to #1513 connector,
PP-RADAR-001, PP-HERMES-EA-001/PP-CATIONIX-001, librarian #1433/#1434/
#1439, PP-POSTGRES-001): `docs/ai-plans/shcs-phase0-audit-scoping.md`.
**Blocker before evidence collection starts**: identify the existing
token-facility owner and define a least-privilege read-only seam — do not
start an API connector from token-health telemetry. **UI evidence
collection itself is NOT an open question** — corrected 2026-07-22, Dave:
already assigned to todo #1465 (2026-07-16, redirected same day): Tigwa
runs live Seller Hub inspection herself via her `computer_use` browser
skill + vision model, better suited than Claude for this. The SHCS packet
above is the register her evidence populates, not a new assignment.

**Tigwa's full execution plan delivered, 2026-07-22
(`TIGWA-PLAN-2026-07-22-sellerhub-audit-execution.md`, processed from
inbox/claude same day).** Phased A0-A4: A0 control setup + a small
synthetic-data model canary (cost/security envelope reviewed before any
account-derived corpus is sent to a model); A1 risk-first UI evidence
capture in order policies → listing/high-risk controls → orders/
fulfillment/returns → marketing/finances → messages/store/settings, each
row `blocked-unverified` rather than guessed when a control can't be
observed; A2 matching-batch TGW behavior mapping (code/test/live-probe
provenance), starting from known regression rows (#895, #12, #1631, Best
Offer) to prove the procedure catches known history first; A3 costed
Gemini-assisted synthesis per evidence batch, strict structured output
only, every `full-parity`/`closed`/high-risk/build-now row
Tigwa-validated against raw evidence before acceptance — "a valid JSON
response is not a valid audit result"; A4 reconciliation against
PP-SELLERHUB-001/PP-EDITOR-001/PP-RADAR-001/#1513/PP-POSTGRES-001/the
operational-reality register, producing capacity-lane recommendations.
A1/A2 (read-only, no model spend) can start now; A3 stays gated on Dave
answering 3 open items: confirm the primary audit model, approve a capped
canary + later per-surface synthesis budget, confirm the A1 risk order and
existing-account-only boundary. Full doc:
`docs/ai-plans/shcs-phase0-audit-scoping.md` is the packet this plan
executes.

**Model routing amended same day (`TIGWA-AMENDMENT-...-flash-lite-
routing.md`)**: primary batch/extraction workhorse is stable
`gemini-3.1-flash-lite` (direct Gemini API, exact version pinned at canary
time) — repeated bounded screenshot/document/code-evidence extraction,
schema-constrained row generation, classification, normalization, dedupe,
missing-evidence detection, batch summaries. `gemini-2.5-pro` is
escalation-only, for a small explicitly-costed set of hard cross-surface
contradiction/sequencing/enhancement-synthesis reviews after evidence
batches already exist — this replaces the original plan's "2.5-pro as
primary audit analyst" framing above. Evidence boundary unchanged either
way: Flash-Lite/Pro output is candidate/derived data only, never
sufficient alone to establish `full-parity`, close a gap, or promote a
task without raw account evidence + traced TGW evidence + Tigwa/Dave
review.

**Standing planning-process context recorded here (2026-07-22, from
`TIGWA-CONTEXT-...-plan-unfolding-not-scope-creep.md` +
`TIGWA-CORRECTION-...-max-plan-fully-plan-then-execute.md`, both folded
into the top-of-file "Standing context, 2026-07-22" section already) —
noted again here because this PP is the concrete example both notes cite:
the current SHCS/reconciliation-ledger/PP-POSTGRES-001-scaffold pass is
the plan being deliberately unfolded from a compressed ~50k-character
artifact into navigable layers, not scope creep, and the Max-plan month is
specifically for planning the unfolded PPs fully (outcomes, boundaries,
authority, interfaces, contracts, dependencies, rollback, security,
acceptance evidence, observability, sequencing, owner, decision gates)
before dispatching execution packets.

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

**#7 reassigned to Tigwa, 2026-07-16 (Dave):** her job is to bug Dave until
he finishes the Twitch dev-account registration — the credential
provisioning is his action, not buildable by an agent. **Open question,
flagged not assumed:** Dave referred to "2 credentials issues" this
session; only one (#7/IGDB) is currently tracked under this PP. Checked
`secrets_root` live — Keepa and upcitemdb/go-upc credentials (also named
in `reference/PP-LOOKUP-001-APIs.md`) are unset too, but neither has a
filed todo. Worth confirming with Dave whether one of those is the second
item he meant, rather than assuming.

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

**Gemini 2.5→3.1 migration DONE 2026-07-20 (todo #1610)** — Dave had asked
for this multiple times across prior sessions; never landed because it was
never written down anywhere durable, only said in conversation (exactly the
failure mode Prime Directive 5 exists to prevent). `gemini-2.5-*` models are
being deprecated by Google. Live-verified (not assumed) against the real
Google API before changing anything: fetched the actual model list
(`gemini-3.1-flash-lite` confirmed present, non-preview/stable), ran a real
text inference call, ran a real vision inference call against an actual item
photo (correctly identified a magazine cover) — all three passed. `tgw-
models.json`'s `defaults.default` profile (used by `ai_identify`/`alt_text`/
`bulk_classify` via `use_default`) updated `gemini-2.5-flash-lite` →
`gemini-3.1-flash-lite`. `ebay_draft`'s explicit override was already on
`gemini-3.1-pro-preview`, unaffected. Workers restarted to pick up the
change. If Gemini 2.5 models get fully sunset by Google later, this is
already ahead of it.

**`ebay_draft` switched `gemini-3.1-pro-preview` → `deepseek_direct`/
`default_deepseek_nonthinking`, same session (Dave: text-only task,
pro-preview was his own cost/quality experiment, not a requirement).**
**Not yet fully verified live**: the description-rewrite call site
(`ebay_draft.py:538`) is gated behind product-lookup data existing
(rarely true), so the swap hasn't actually been exercised by a real call
yet. Low risk (same `call_model()` infra already proven elsewhere for
DeepSeek) — worth a real test if a suitable item shows up.

**`ai_identify.py` prompt tuning, 2026-07-20** — three real quality issues
found and fixed live, iteratively, Dave reviewing each round: (1) SEO —
titles no longer open with generic words (Vintage/Antique/Unbranded); (2)
precious-metal overclaiming — `material`/`Metal`/`Metal Purity` no longer
claim gold/silver without a visible hallmark (`Color: "Gold"` as a plain
color descriptor still fine); (3) stone/gem overclaiming, same pattern
(Dave's suspicion confirmed: `"Main Stone Creation": "Natural"` appearing
without evidence) — calibration added. Confidence language also improved
("it looks like a..." → "it is a..."). Validated against books/magazines
too — SEO fix generalized cleanly. **One anomaly unresolved:** that same
stone-creation field stayed byte-identical across 3 different prompt
versions on the same test item (`tgw202605032308315`) — smells like
caching or a code path not actually re-running the full schema, not
necessarily still broken on other items; worth investigating further.

**Next queued action (explicit, Dave):** run the full 427-item
`ai_identify` batch — 2026-added SKUs, not sold, has photos, genuinely NOT
listed on eBay (filter on `ebay_listing.status`/`listing_status`, NOT the
generic inventory `status` column — that mistake caught live on
`tgw202103192241400`). Most of the 427 likely already have
`ai_identified: true`, so plain `tgw enqueue-sku ai_identify <sku>`
silently no-ops — use `tgw hint --force <sku> "<existing title>"` instead
(sets `ai_reidentify: true`, forces a real re-run; validated end-to-end).
Dave confirmed no cost concern (Google key funded specifically for this);
approach is "couple at a time" validation throughout.

## PP-MACRO-001 — macroboard hardware (#15)
**Given its own heading 2026-07-11** — was a bare Frozen-list mention.
Status UNCHANGED — still frozen until R1 drains, this only fixes
visibility. Open: #15, second keyboard wired up as a macroboard (see
`etc/interfaces/keyd/tgw-macroboard.conf`) — an operator-interface
hardware addition, not gated on anything beyond the freeze itself.

**#15 reassigned to Tigwa, 2026-07-16 (Dave):** she works with Dave
directly to specify the `keyd` config — a hands-on hardware/config
collaboration, not a solo Claude build task.

## PP-ROUTER-001 — D-Link DIR-868L router into the TGW ecosystem
**RECOVERED 2026-07-16/17** — real research existed but had never been given a PP
number, found during a "recover lost PPs" sweep. DD-WRT confirmed correct firmware.
**Status 2026-07-17: still just a proposal, no flash decision made.** Decision scope
is narrow: flash or don't — not a commitment to build all 6 candidate capabilities
(DHCP audit, VLAN isolation, health-check integration, local DNS, WireGuard, config
backup) at once; once flashed, Entware lets services get added one at a time. Live
finding: DHCP conflict, two MACs both claiming `192.168.60.112` (todo #1490).
Possible NATS-JetStream-for-alarm-system leg (distinct from PP-AIOPS-001's use) sent
to Tigwa for reconciliation against the router's 256MB RAM constraint. Full detail:
`../pp/PP-ROUTER-001.md`.


## PP-INVENTORY-001 — physical inventory verification — NEW 2026-07-11
**Dave: "11 is an entire missing PP — the tools to accomplish the job,
both the standard manual tool as well as the already supposedly in the
plan AI vision inventory helper."** Confirmed: no design doc existed for
either leg — `PP-VISION-001` was only ever a bare "(GPU-gated)" mention,
no substance.

**PLANNED 2026-07-16** — real workflow design now at
`pp/PP-INVENTORY-001.md`. Dave's own concrete framing, clarifying this
isn't just "PP-VISION-001 applied": "Vision worker finds items in photos
of items in box taken by inventory worker and starts checking off boxes
on the location inventory. Operator completes the rest to cleanup, looks
for missing or marks missing etc." Two legs, sequenced: (1) the manual
leg, absorbing `#11` (`tgw ebay-sweep → physical inventory review`),
buildable now with no PP-VISION-001 dependency; (2) the vision-assisted
leg (auto-check-off against a location's expected-contents manifest),
gated on PP-VISION-001's Phase 2 full-catalog embedding index. Distinct
from PP-STORAGE-001 (storage *organization*, size-class not category) and
PP-DATAINTEGRITY-001 (data *record* integrity) — this is specifically
physical-stock-vs-record reconciliation. Manifest/checklist UI question
explicitly deferred to `pp/PP-UIUX-001.md` rather than picked
unilaterally.

## PP-UIUX-001 — UI/UX unification: full inventory, mapping, and spec
**OPENED 2026-07-16.** Absorbs the previously-orphaned "Web UI vs Flutter"
discussion (see its removed heading above, under "Open discussion items")
plus Dave's broader direction: "plan is to fully define then have entire
set including web ui and flutter to the spec by ui/ux specialist coder."
Sequence: (1) inventory every operator-facing UI surface (web UI pages,
Flutter's actual screens), (2) map each to the backend it calls
(`/api/*` endpoints, `tgw` CLI) to find where they've diverged — the
2026-07-06 investigation already found one concrete gap this way
(`reference/TGW-HTTP-API.md` stale relative to `PP-ACTIONCONSOLE-001`/
`PP-LISTEDITOR-001`'s newer surface), (3) write one complete spec covering
both surfaces, (4) hand to a new UI/UX-specialist executor role (analogous
to `tgw-coder`, not yet defined) to implement. Hard constraint carried
forward unchanged: Flutter must reuse the same web backend functions, never
duplicate logic. PP-INVENTORY-001's manifest/checklist UI is a concrete
waiting consumer. Full design + all prior Flutter-vs-web context preserved:
`pp/PP-UIUX-001.md`.

## PP-STORAGE-001 — semi-chaotic storage: size_class as a size/weight signal
**PLANNED 2026-07-16 (todo #1478's first resolution)** — real design doc
now at `pp/PP-STORAGE-001.md`. Dave's own framing of the value: "this is a
great way to determine a size weight range. Especially size." `size_class`
(`/opt/TGW/config/category-groups.json`, 25 groups tagged
`flat`/`packet`/`small_box`) already exists for free; the design proposes
a new `size_class_ranges` config mapping each class to a weight/dimension
envelope, used both as a shipping-weight estimation fallback (marked
`weight_oz_source: "estimated"`, never silently presented as a real
measurement — same class of concern as invariant C14) and as a physical
findability cue alongside `cmd_locate`'s existing image-similarity search.
**Open item: the actual numeric ranges are Dave's to supply** (from his
own handling knowledge or a quick sample-measurement pass) — that's the
blocker on filing the first real implementation todo, not any remaining
design work.

## PP-WHISPER-001 — voice transcription → suggestion pipeline
**Reassigned to Tigwa, 2026-07-16 (Dave):** "assign to tigwa we are
already working on voice input for interface and justshoutit she already
found a free external provider at groq and we use that on telegram." Not
a Claude planning task — Tigwa already has live, in-progress work here
(Groq as the external transcription provider, already wired into the
Telegram interface) that supersedes planning this from scratch. Existing
facility (`cmd_whisper_to_suggest`, `src/tgw/api.py`; `tgw history-index`
CLI) stays on record as prior art feeding PP-INTAKE-004's justshoutit, but
ownership and next steps are hers going forward — no Claude todo filed.

## PP-VISION-001 — vision-matching capability (GPU-gated)
**PLANNED 2026-07-16** — real design doc now at `pp/PP-VISION-001.md`.
Dave, on priority: "oh yeah. I want this badly. should have already been
planned." Verified live: a real precursor already exists
(`src/tgw/fingerprint.py` — perceptual dhash + color-histogram matching,
its own docstring calling itself "a workflow proof, not a final CLIP
matcher," CLI-only, no worker wiring) and the fleet is 100% CPU-only today
(`reference/HARDWARE-AI-INFERENCE.md`) — no GPU exists, which is the real
reason this sat frozen, not a design gap. The design is phased so it
doesn't wait on the GPU purchase: **Phase 1 is a CPU-only feasibility
pilot** (embed ~200-500 sample photos, measure real throughput + match
quality against the current perceptual-hash baseline) — this generates
the actual evidence to weigh the GPU purchase against, rather than another
indefinite "someday." Phases 2-3 (full-catalog batch index once GPU
acquired, wire into PP-INVENTORY-001's verification leg + PP-STORAGE-001's
findability flow) are spec'd but gated on Phase 1's result + hardware.
**Next step: file the Phase 1 todo** once a concrete embedding-model
variant is picked at kickoff (design doc names CLIP-family candidates,
not yet pinned — feedback-llm-model-selection requires a real pin before
dispatch).

**Todo #1478 — resolved 2026-07-16.** All three former stubs now have a
real disposition: PP-STORAGE-001 and PP-VISION-001 planned (above,
implementation todos pending Dave's ranges / a model pin respectively);
PP-WHISPER-001 reassigned to Tigwa's existing live work. No PP left as a
silent pointer-only stub.

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
Umbrella for positioning/pricing strategy (moved out of PP-REPRICER-001, which is now
mechanical-tool-only). PP-PRICING-001 (Google Shopping comps via SerpApi) is the
first tenant. SerpApi key (#1110) deferred — pipeline restart takes priority. **Eval
packet #1109 DONE**: grounded Gemini search LOST against the existing free
Browse-comps signal (45.3% vs 30.4% mean abs error on 10 real sold items) — do not
wire grounded Gemini as a pricing signal; SerpApi itself still untested (blocked on
the key). Phase 0 comping interface (supervised capture tool, not model-invented
prices) designed, not started, needs Dave's go/no-go. Phase -1 self-powered comp
engine (`OwnSalesProvider`) already exists and runs — turned out to be a
data-density problem: `ebay_category_id` populated on 52% of catalog; #1135 recovered
another 5,367 categories (20% of the gap) via a repeatable recompile job. Full
detail: `../pp/PP-MARKETING-001.md`.


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

**2026-07-20 incident chain:** live double-sale report on tgw202404031105366 led to
finding + fixing a real data-loss bug (#1604, `mark_item_sold()` silently dropped a
second distinct order once an item sold out — merged, `ebay_sale` now a list). Chasing
why the sale went unrecorded found `tgw-worker@ebay_legacy_sync.service` didn't exist
as a systemd unit at all (#1605) — root-caused to a queue lease-expiry race (#1607, not
the #1077 orphaned-offer issue the old exclusion comment blamed): `handle()`'s ~6min
runtime exceeded the 300s lease with no renewal, another worker's routine sweep
reclaimed the still-running job, and `mark_succeeded()`'s unchecked-rowcount UPDATE
silently no-op'd. Mitigated live: `queue.lease_seconds` 300s→600s (global), worker
restored, then immediately generated a 451-row duplicate backlog (zero SKU data, pure
empty `{"reason":"scheduled"}` tokens) because `_reschedule()` had no `dedupe_key` —
caught within ~4min, worker stopped, backlog cancelled (216 dead_letter + 235
queued/running). Worker stays stopped pending PP-STATEMACHINE-001's dedupe_key fix.
Audit (#1607) found the same missing-dedupe_key hole in 7 other self-rescheduling
workers — see PP-STATEMACHINE-001 for the general fix this incident produced.

## PP-STATEMACHINE-001 — job manifest contract, enforced by the state machine itself — NEW 2026-07-20
`state_machine.enqueue_job()` is the single entry point every queue job goes through.
Defines the manifest every job must declare (`dedupe_key` required; `priority` required
in effect but config-defaultable via new `tgw-queue-priorities.json`, same `defaults`/
`use_default` shape as `tgw-models.json`; `entity_id`/`entity_type` required for
per-item jobs — already a known docstring-only gap; new `supersede` flag for
force-eligible-now jobs, e.g. `restart-ebay-token`'s "run now" need) and makes
`enqueue_job()` itself the enforcer — reject incomplete manifests at call time, not a
passive audit. Enforcement lives in our own Python, not a Claude Code harness hook —
no dependency on the hook-firing bug found the same session (todo #1531,
invariants.md E11/E12). Sequencing: fix all 15+ call sites the #1607 audit found
missing a dedupe_key first, ship the priority config + supersede path, only then flip
enforcement on. Invariant E16 to be written alongside the first implementation packet.
Full design: `pp/PP-STATEMACHINE-001.md`.

## PP-EBAY-SNAPSHOT-001 — submitted-payload capture + re-push
Phases 1–3 done. Phase 4 `tgw ebay re-push` + plan documentation #896. Overlaps with
eBayCapture — reassess scope at next touch. Design: `pp/PP-EBAY-SNAPSHOT-001.md`.

**#1077 (eBay Dev Support ticket, undeletable item) — status only, 2026-07-16
(Dave): still waiting.** Bad-sign development: the support rep who hung up
on Dave mid-call (yelling that the ticket was hurting his numbers) has
since been promoted into eBay's business-division decision leadership. No
action available on TGW's side — external, waiting on eBay.

Snapshot baseline completed (19,486 SKUs) — unblocks #1131 Motors census; drift detection baseline set.

**Standing Growth Check strategy (Dave, 2026-07-20):** "when we note an api
lapse because of legitimate requests we soon after initiate a request for a
rate increase... we will Application Growth Check them to death. This is
how they improve their system? We will follow to the letter." Going
forward: whenever a real, legitimate workload hits a rate ceiling (an
actual queue backing up against a documented limit — not a self-imposed
throttle, not manufactured volume), that lapse itself becomes the evidence
for a fresh Growth Check / rate-increase request through eBay Developer
Support, filed promptly rather than left to accumulate. Follow eBay's
stated Growth Check process exactly as published (app must be live with
real usage, `MARKETPLACE_ACCOUNT_DELETION` subscription required first,
forecasted daily volume, app URL, EPN publisher ID if applicable) — no
invented volume, no gaming the review. Dave is having Tigwa obtain a copy
of the actual Growth Check request form so future requests are prepared
in advance rather than drafted from scratch each time. See
`EXTERNAL-SUPPORT-TICKET-REGISTER.md` for the live ticket log (`EBAY-DS-*`
rows) and the still-open `MARKETPLACE_ACCOUNT_DELETION` webhook gap (spec
exists at `reference/ISS-005-REST-Signature-Verification.md`, never
implemented — blocks any Growth Check submission until built).

## PP-RECOVERY-001 — web UI regression audit
**CLOSED 2026-07-16 — confirmed obsolete, already resolved via later work.**
Dave asked to check whether this had already been triaged over rather than
just sitting stale; it had. Verified: the `task/aider-20260616145314`
branch this audit was gating a merge on no longer exists (long since
merged), and both todo batches it tracked (WEBUI-AUDIT #998-1038, pre-800
historical #897-997) are 100% done — zero open items across the whole
range. The entire audit predates the s40-42 UI rebuild and
PP-EDITOR-001/PP-ACTIONCONSOLE-001/PP-LISTEDITOR-001, which superseded it.
No remaining action. Original findings preserved at `pp/PP-RECOVERY-001.md`
(Prime Directive 1 — not deleted, marked closed).

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
Phases 1-4 done 2026-06-12->14: schema (`pp_ref`/`depends_on`/`plan_anchor`), `tgw
plan render` (taskboard), `tgw plan check` (plan<->tracker reconciliation), `tgw plan
status` (per-PP rollup) — P3/P4 run in the mandatory session-start sequence. **Phase
5 (PROPOSED, Dave 2026-07-10): execution track / goal view** — a `tgw plan track
<ref>` rendering one track's items ordered by `depends_on`, blind to unrelated
backlog noise; forward-looking team-routing concept (specialist teams executing
tracks end-to-end) not yet spec'd. Full Phase 5 rationale + complete Done-rollup list
of superseded/misc-completed PPs and todos: `../pp/PP-PLANDB-001.md`.

### Done (designs in `pp/` or archive; tracker holds history)
PP-EBAY-MIRROR-001 · PP-MIGRATE-001 · PP-DEADLETTER-001 · PP-DOCFLOW-001 ·
PP-INTAKE-001 · PP-OFFER-001 · PP-OPS-001 · PP-PROMO-001 · PP-REF-002 ·
PP-REVISION-001 · PP-SHELL-001 · PP-STORE-001 · PP-TODO-001 ·
PP-VERIFY-001 (scaffold; integration deferred) · PP-WM-001/PP-HM-001 · PP-ADD-009 ·
PP-CI-001 · PP-CONTEXT-001 · PP-GLOBALS-001 · PP-LISTING-001 ·
PP-LOOKUP-001 (Tier 1) · PP-PRICE-001/003/004/005 · PP-QUALITY-001 · PP-REF-001 ·
PP-REPRICE-001 (defused s42) · PP-SEO-001 · PP-STAGE-001 · PP-SYNC-001 ·
PP-FREESHIP-001 · PP-STRIKE-001.

**Superseded/obsolete:** PP-DEPLOY-001 (-> PP-NIXOS-001) · PP-PRICE-002 (->
PP-REPRICE-001) · PP-PLASMA-001 (-> CatioNIX desktop split).

**Misc. completed todos** (full one-line-each list: `../pp/PP-PLANDB-001.md`): #1053,
#1113, #1135, #1138, #1209-#1214, #1236, #1239-#1240, #1249, #1252, #1254-#1258,
#1318-#1320, #1323, #1338.

### Gated on R1 — named, designed later


## PP-BULKLIST-001 — bulk editing + listing surface (stub, Dave 2026-07-02)
Operator-gate design at volume: bulk-approve the ~99% that are right, pull exceptions
into the single-item editor. **Hard gate: single-item pipeline must be
operator-verified end-to-end first (R1.6/R1.7)** — queued as the pass immediately
after the pipeline restart, not before. Backend plumbing already partially exists
(`/api/bulk/preview`, `/api/bulk/apply`, `/api/bulk/action`, `/form/bulk` — confirmed
live 2026-07-16, not a from-zero build). Rides along: `ebay_dole` worker was never
installed — the "queue for auto-listing" checkbox is labeled inactive with an
accurate tooltip pending that decision. **This PP isn't missing a design — it's
correctly gated on R1.6/R1.7, and the plumbing it needs already exists.** Real
next action is verifying whether R1.6 (Dave's one-true-end-to-end UI pass) has
actually happened since — that R1 table (line ~211 above) reads like an early-
session snapshot (2026-07-04 era) that may itself be stale relative to
everything shipped since; this is exactly a PP-OPSREALITY-001-shaped "is it
actually done or just plan-said-done" question, not a schema/mechanism gap.
**Fixed broken link, 2026-07-22**: the doc lives at
`docs/TGW-Plan-Vault/pp/PP-BULKLIST-001.md` — one directory above `plan/`, not
`plan/pp/` as the old relative pointer implied (never actually resolved).


## Open discussion items (for 2pm 2026-07-04 planning session)

**Archived 2026-07-18** — this session's items are resolved or superseded (Web UI vs
Flutter moved to `pp/PP-UIUX-001.md` 2026-07-16; PP-INTAKE-004 promoted; the
relocate-inbox and catalog_rebuild-dead-letter questions are stale/closed). Full
verbatim content preserved: `archive/sections/open-discussion-2026-07-04.md`.


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

## PP-QUEUESTATS-001 — pipeline page accuracy + throughput anomaly detection — NEW 2026-07-14
**Found while investigating a stuck-item question (todo #1108's alt_text
worker):** the `/form/pipeline` webui page's "Done today" column
(`http_server.py:7793,7798`) is mislabeled — its data source,
`queue_status()` (`http_server.py:1664-1688`), is `SELECT queue_name,
state, COUNT(*) FROM queue_jobs GROUP BY queue_name, state` with **no
date filter at all**. Both the "Failed/DL" and "Done today" columns are
lifetime cumulative `queue_jobs` state counts, not scoped to today. The
"Failed/DL" label is honest (it doesn't claim to be daily); "Done today"
is not. Dave, 2026-07-14: "proper. I can live with it for a bit" —
confirmed as a real fix (date-scoped throughput, not just a relabel), not
urgent, no packet dispatched yet.

**Dave's own framing, same message, worth building toward:** "looking at
those numbers, and knowing I have 55k items and we only were supposed to
process some, we can see where the issues are. The same set of stats can
me used to detect an out of band surge in usage, especially after we have
worked it for a while." Two distinct asks bundled in one PP:
1. Fix the query to be genuinely date-scoped (today's succeeded/failed
   per queue, not lifetime) — the immediate, concrete fix.
2. Once real daily-throughput numbers exist, use them as a **baseline for
   anomaly/surge detection** — after some period of "normal" operation,
   flag when a queue's daily volume (success or failure) deviates sharply
   from its own recent baseline. This is explicitly a *use* of the same
   per-queue per-day stat, not a separate metrics system — build #1 in a
   way that makes #2 a straightforward follow-on (e.g. a `queue_daily_stats`
   view/table, not just an ad-hoc page query), but don't build the
   anomaly-detection logic itself until Dave asks — not yet scoped/dated.

Not yet packetized — low urgency per Dave's own "I can live with it for a
bit."

**Distinct from PP-AIOPS-001 Phase 3's "anomaly detector," clarifying a
same-week scouting-pass flag (2026-07-22):** the two aren't duplicate
efforts even though both use "anomaly detection" language. This PP is
about **job-volume surge** (a queue processing way more/fewer items than
its own recent baseline — a business/throughput signal). PP-AIOPS-001
Phase 3 is about **ItemData mutation anomalies** (a worker changing fields
in a pattern that looks wrong — a data-integrity signal). Different inputs,
different questions, legitimately two things. See also PP-LOADTEMP-001
below for a third, also-distinct concept (system *resource* pressure) —
three signals that sound similar in name, worth keeping conceptually
separate: **volume** (this PP), **correctness** (AIOPS Phase 3),
**capacity** (LOADTEMP-001).

## PP-LOADTEMP-001 — system load "temperature" evaluator — NEW 2026-07-22
**Dave's own framing:** a system-load "temperature" reading the pipeline
and orchestrator both consume to decide how many processes can run right
now, throttling everything as it rises. Explicitly **not just thermal** —
"that temperature is not just the heat, but the load on the discs,
network, everything that can stall the operation and needs to be
respected." **Both a structured multi-field reading AND a derived single
number — not either/or (correction to how this was framed one round
ago).** See "Reading shape" below for the multi-field part (hardware AND
business/API pressure, each consumer weighs the fields relevant to it).
Separately, Dave, 2026-07-22, same session: "we should have a derived
single number too. It will make a lot of decisions easy. 'How many
tgw-coders can I launch? It's 3 degrees. 2.'" The single number is a
computed simplification OF the multi-field reading (not a replacement for
it) — good for exactly the class of quick capacity question the example
names: a "degrees → max concurrent" lookup/threshold table answers "how
many specialists can I dispatch right now" in one step, without the
orchestrator having to weigh five raw fields itself every time. Precise,
job-type-specific consumers (an eBay call checking quota headroom
specifically) still read the relevant raw field directly; broad
capacity-sizing questions read the derived number. Both live in the same
reading, not two separate systems.

**Confirmed gap, not existing infra:** `tgw health` (`src/tgw/health.py`)
has zero system-resource checks today — every existing check is
application-level (Postgres, catalog, NATS, eBay token, backups). This is
genuinely new, not something to wire up from an existing signal.

**Two consumers, same signal, don't build two throttles:**
1. **PP-ORCHESTRATOR-001** — "temperature" gates how many specialists
   (tgw-coder/Aider instances) the orchestrator dispatches concurrently.
   Same shape as the already-decided "model selection + budgeting is an
   explicit orchestrator responsibility" (PP-ORCHESTRATOR-001, above) —
   this becomes a second orchestrator-level input alongside model/quota
   routing, not a separate mechanism bolted on.
2. **The `tgw-worker@<queue>` fleet** — pipeline workers throttle their
   own batch size/concurrency against the same reading (e.g.
   `bundle_intake`/`multi_intake` backing off during a disk-heavy
   `catalog_rebuild` pass, or a bulk `ai_identify` batch — like tonight's
   queued 427-item run — pacing itself against real disk/network
   pressure instead of a fixed sleep/rate constant).

**Architecture, clarified 2026-07-22 (Dave): "a little weather station,"
polled, not an event stream.** Answers the "where does the reading live"
question above directly — not JetStream, not a broadcast/push model. A
small, single-responsibility daemon owns reading CPU/disk/network/memory
+ thermal, computes the composite "temperature," and exposes ONE cheap,
current reading that consumers poll on their own cadence (a Postgres
single-row/small table is the natural fit — matches "reuse, don't invent
a second authority" rather than a new IPC mechanism; JetStream stays
reserved for what it's already scoped for — durable discrete events,
audit/handoff/mailbox — not a live gauge).

**Per-host, not a single central instance (Dave, same session): "every
host can have one. It is a good system. It works for tigwa's active
monitor too."** Load is inherently local — a1131 being busy doesn't mean
tgw-prod is under pressure — so each host (tgw-prod, a1131, any future
satellite-warehouse device) runs its own weather-station instance,
reading its own local resources. **This absorbs Tigwa's existing 5-minute
thermal-polling cron rather than sitting alongside it as a second
mechanism** — her thermal responsibility (CLAUDE.md: "Thermal monitoring
is Tigwa's responsibility, not Claude's") doesn't change, but the
underlying reading becomes the same shared per-host primitive instead of
a standalone loop she built and owns in isolation; thermal becomes one
component of the composite temperature, not a parallel signal. The
existing consumption-side policies stay exactly as they are and aren't
touched by this — [[project-thermal-emergency-policy-2026-07-14]]'s
3 notify-only legs (no pause/kill/shutdown authority) and
[[feedback-stacked-disk-io-thermal]]'s a1131 hot-day-offload guidance
govern what happens with a hot reading; this PP is only about how the
reading itself gets produced and shared, on every host, once.

**Throttle scope, clarified 2026-07-22 (Dave): global, not just
gate-new-work.** "The pipeline should throttle globally in case a job is
already running and causing the load, slowing and pausing until it
clears." This is broader than the orchestrator deciding whether to
*start* a new specialist — every already-running consumer needs to react
to a rising reading too, not just new-dispatch decisions. Concretely: the
natural integration point is `worker_base.py`'s `QueueWorker` claim loop
(every worker already goes through it) — check the weather-station
reading before claiming the next job; slow the poll/claim cadence as
temperature rises, pause claiming entirely above a hard threshold, resume
once it clears. One check, one shared base class, every worker in the
fleet gets the behavior for free — not a per-worker bolt-on. Same shape
as the existing 25707 circuit-breaker pattern (cap frequency until
confirmed clear), generalized from one known failure mode to live system
load generally.

**Relevant existing reference material, not a green-field design:** the
superseded (Nix-coupled) Phase 5 nspawn design already sketched a cgroup
v2 resource-monitor daemon — "reads `memory.current`/`cpu.stat` every 1.5
seconds, trips a recovery trap on sustained spikes" — that
polling/threshold *mechanism* is still valid reference even though the
container-runtime it was built for (nspawn) was dropped for bubblewrap.
This PP would generalize that pattern from "watch one sandboxed task" to
"watch the whole host," feeding both the orchestrator's dispatch decision
and every worker's own claim-loop throttle.

**Reading shape + fence, clarified 2026-07-22 (Dave): "just an extension
of the existing linux facilities, behind a secure fence... SSD temp, cpu,
gpu, hdd load, tgw pipeline load, tokens status, api account levels...
then you can read the ones you need to respect."** Two corrections to how
this PP was first framed:

1. **Not a single collapsed scalar — a structured multi-field reading.**
   Each consumer reads and weighs only the fields relevant to its own
   decision, not one flattened "temperature" number. A disk-heavy
   `catalog_rebuild` pass cares about SSD temp/HDD load; an eBay API call
   cares about "api account levels"/quota headroom; the orchestrator
   dispatching a new specialist cares about CPU/GPU/pipeline load. Same
   underlying weather-station reading, different fields consulted per
   job-type — not a one-size-fits-all gate.
2. **The metric set is broader than hardware.** Beyond
   CPU/GPU/disk/network/memory/thermal, it explicitly includes **business/
   API pressure**: token/quota status (ties directly into
   `tgw-models.json`/`get_task_model()` and the existing `PP-QUOTA-001`
   quota-context plumbing already wired into `worker_base.py`, referenced
   under PP-ORCHESTRATOR-001's model-selection responsibility above) and
   external API account-level/rate-limit headroom (eBay, LLM providers).
   Quota exhaustion risk is a "temperature" the pipeline needs to respect
   exactly the same way it respects a hot CPU — same mechanism, not a
   separate quota-check system living alongside this one.
3. **Sourced from standard Linux facilities behind a fence, not custom
   sensors.** `/proc/loadavg`, `/sys/class/thermal`, `/sys/block/*/stat`,
   SMART data, `sensors`/`nvidia-smi` for GPU — the weather station reads
   what the OS/hardware already exposes; nothing new to instrument at the
   hardware layer. "Behind a secure fence" matches this project's existing
   "tgw-api is the fence" principle exactly — the raw reading is gathered
   by the local daemon, but consumers reach it through a controlled
   interface (the Postgres row/table from above), not by each worker
   independently scraping `/proc`/`/sys` itself.

**Atomic pipeline integration, clarified 2026-07-22 (Dave): "the pipeline
can manage atomically."** The weather-station check shouldn't be a
separate racy pre-check a worker does before claiming a job (multiple
workers could all check "not hot yet," then all proceed together) — it
should fold into the same atomic claim operation the pipeline already
has. Natural fit: `claim_queue_jobs()`'s existing `SELECT ... FOR UPDATE
SKIP LOCKED` transaction (E16 substrate, already the mechanism every
worker claims through) — the relevant weather-station fields get
consulted inside that same transaction, so "should I take this job right
now" and "am I actually claiming it" happen as one atomic decision, not
two steps that can race.

**Not yet designed.** Real open questions before this is packet-ready:
the derived-number formula itself (how the raw fields combine into one
"degrees" value) and its degrees→max-concurrency lookup table, the
per-job-type rule for which *raw* fields a given job-type should consult
directly instead of the derived number (an eBay call caring about quota
headroom specifically, say), and how a paused/slowed worker reports that
state (silent backoff vs. a visible `tgw health`/queue-page signal so a
slow cycle doesn't read as a stuck/broken one). Flag for a dedicated
design pass, not building tonight.

**Tigwa's facility-cross-check, 2026-07-22 — two hard gaps before a
packet, added to the open-questions list above:**
1. **Per-host availability/failure mode.** A reading is inherently local
   (per-host hardware), but the proposed store is a shared Postgres row/
   table — that creates a real failure mode: if a1131 can't reach
   tgw-prod/Postgres, a worker must not mistake an unreadable remote row
   for "cool," i.e. silently proceed as if load were low. The local
   sampler needs a stamped reading, an explicit max-age, and a defined
   safe-degraded policy (treat unreachable/stale as hot, not cool) —
   fail-safe direction matters here, same shape as any other stale-state-
   read-as-current bug already caught elsewhere in this doc.
2. **Fence and confidentiality.** The reading includes "tokens status,
   api account levels" — provider quota/rate-limit headroom is fine for
   consumers to read, but the underlying token/credential material must
   never ride along. Publish an allowlisted, derived capacity/headroom
   indicator through the fence; raw collected data (including anything
   secret-bearing) stays local, never republished as-is.
Also confirmed: the atomic-claim integration must stay a pure scheduling
decision — temperature may slow/pause *claiming new work*, never grant a
worker authority to mutate/kill an already-running job (that authority
boundary is unrelated and stays exactly where it is). Stale-reading state
must be visibly distinguishable from an intentional load-based backoff.

### Technical deep-dive, unfolded 2026-07-22 — answers all five "not yet designed" open questions

Checked `queue_workers`' real schema first (`host_name` column already
exists — the exact per-host anchor the atomic-claim join needs, no new
identity mechanism required) before designing.

**Schema — one new table, reuses `queue_workers` for the worker-signal
requirement:**
```sql
CREATE TABLE system_load_reading (
    host                    TEXT PRIMARY KEY,
    sampled_at              TIMESTAMPTZ NOT NULL,
    cpu_load_1m             NUMERIC,
    disk_io_pct             NUMERIC,     -- from /sys/block/*/stat deltas
    ssd_temp_c              NUMERIC,     -- /sys/class/thermal or smartctl
    gpu_load_pct            NUMERIC,     -- nvidia-smi, NULL on hosts with no GPU
    network_io_pct          NUMERIC,
    memory_pct              NUMERIC,
    -- business/API pressure — DERIVED VALUES ONLY, never raw credentials (fence)
    ebay_quota_headroom_pct NUMERIC,
    llm_quota_headroom      JSONB,       -- {"openrouter": 0.62, "google_direct": 0.11, ...}
    degrees                 NUMERIC NOT NULL,   -- the single derived number
    max_concurrency         INTEGER NOT NULL,   -- looked up from degrees
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE queue_workers ADD COLUMN throttle_state TEXT;  -- NULL=normal, 'slowed', 'paused', 'stale_load_data'
ALTER TABLE queue_workers ADD COLUMN throttle_reason TEXT;
```
**Fence, enforced by construction, not by convention**: the sampler
daemon never reads `secrets_root` at all — it calls the same already-
derived quota-status function `worker_base.py`'s existing PP-QUOTA-001
plumbing exposes (a headroom percentage, not a key), so there is no code
path in this design that could leak a raw credential into the shared
table even by mistake.

**Degrees formula — worst-bottleneck-governs, not an average.** A single
saturated resource should throttle even if everything else is idle
(matches how thermal throttling already works physically): normalize
each hardware field to a 0-10 hazard score, `degrees = MAX(scores)`, not
a weighted average. First-draft thresholds (config file, not code — same
instinct as `tgw-models.json`, revisit numbers once real data exists):

| degrees | max_concurrency |
|---|---|
| 0-2 | unrestricted (fleet's normal ceiling) |
| 3-4 | 5 |
| 5-6 | 3 |
| 7-8 | 1 |
| 9-10 | 0 (pause claiming entirely) |

Lives in `tgw-loadtemp-thresholds.json` — a new config entry, not a
hardcoded Python table, so tuning the curve is a config edit.

**Per-job-type raw-field consultation**: precise consumers (an eBay call
checking quota headroom specifically) read `ebay_quota_headroom_pct`
directly from the same row instead of `degrees` — both live in one
reading, exactly as already decided above, this section just names which
field each known consumer type reads: `ebay_*` workers →
`ebay_quota_headroom_pct`; `ai_identify`/LLM-calling workers →
`llm_quota_headroom`; everything else (dispatch sizing, disk-heavy
passes) → `degrees`/`max_concurrency`.

**Fail-safe per-host availability, answers Tigwa's gap #1**: every
consumer read includes `sampled_at`; a `max_age` config value (recommend
30s, tunable) is checked at read time. **Unreachable Postgres or a stale
row is treated as `degrees = 10` (max hot, pause)** — fail-safe direction,
never silently treated as cool. This check lives inside the SAME
`claim_queue_jobs()` SQL extension below, not a separate app-level
pre-check a caller could forget.

**Atomic claim integration**: extend `claim_queue_jobs()` (the same
function already being extended for PP-WORKFLOW-001's `depends_on` — one
combined migration, not two competing ALTERs on the same hot function) to
take a `p_host` parameter and join against `system_load_reading`:
```sql
AND (
    SELECT max_concurrency FROM system_load_reading
    WHERE host = p_host AND sampled_at > NOW() - INTERVAL '30 seconds'
) > (SELECT COUNT(*) FROM queue_jobs WHERE state IN ('leased','running') AND ... same host ...)
-- NULL from the subquery (no fresh row) = fail-safe hot = clause is false = no claim
```

**Worker-signal visibility, answers the "silent backoff vs. visible
signal" question**: reuses `queue_workers.throttle_state`/`throttle_reason`
(added above) rather than a new mechanism — a worker sets this on itself
when a claim attempt returns empty specifically due to load (not "no jobs
available," a distinct case) and clears it once a claim succeeds again.
`tgw health`/the queue-status page reads this directly — a paused fleet
during a real load spike shows as `throttle_state='paused'` on affected
workers, visibly distinct from a genuinely idle queue or a stuck worker.

**Bounded Phase 1 packet**: sampler daemon (systemd service+timer per
host, recommend 10-15s cadence — tight enough to react to a real spike,
loose enough not to be its own load source) + the `claim_queue_jobs()`
extension + `throttle_state` signal, WITHOUT the LLM/eBay quota fields
yet (those reuse existing PP-QUOTA-001 plumbing but wiring them in is a
second, smaller follow-up once the hardware-only path is proven).
**Acceptance**: sampler writes a fresh row every cycle on both tgw-prod
and a1131; a deliberately stale/deleted row causes claims on that host to
stop (fail-safe proven, not just coded); `throttle_state` visibly flips
during a real load spike (e.g. run a CPU-heavy test load, watch a worker
report `slowed`/`paused`) and clears after. **No-go**: any path where an
unreachable/stale reading is treated as anything other than max-hot;
any change to claim behavior for the common case (fresh, low-load
reading) — must be a no-op when the system is genuinely idle.

## PP-OPERATOR-QUEUES-001 — saved review-lens queues, browse-page chips
**Todo #1466, reviewed + closed 2026-07-16.** Tigwa built this same-day from a
3-sentence prompt. Code review: APPROVE-WITH-NITS (no SQL injection surface, AI-draft
gate real, durable writes; 3 low-severity nits). UI review: SHIP-INTERNAL-SLICE, not
operator-complete (queue chips visually identical to status chips; AI-drafted queues
have no discover/create/edit UI yet — matches stated scope). Full review detail:
`../pp/PP-OPERATOR-QUEUES-001.md`.


## PP-CONDITION-ENUM-001 — generic field-error flagging + save-error field contract — NEW 2026-07-19
**Opened from a live incident (Dave, 2026-07-19):** `tgw202605051124483` dead-lettered at
`ebay_stage` with eBay's generic wrapper text ("The request has errors. For help, see the
documentation for this API."). Real reason was buried in `pipeline_error.raw` and never
surfaced: eBay's actual complaint was `"Could not serialize field [condition]"`. Root cause:
`draft_listing.condition_enum` held the literal human label `"Very Good"` instead of a valid
Inventory API enum (`USED_VERY_GOOD`) — `best_condition()` initially failed to resolve a
granular grade for the category (left `condition_id/label/enum = None`, correctly signaling
"needs manual review"), but the Draft Editor's condition dropdown
(`_build_condition_options`/`loadCatCtx()`, `http_server.py`) fell back to displaying the raw
`condition` string as if it were the current enum, pre-selected it, and a round-tripped
PATCH save wrote that raw string back into `condition_enum` with zero enum validation —
worse than staying `None`, since a truthy `condition_enum` bypasses `ebay_stage.py`'s safe
legacy `_map_condition()` fallback (which would have correctly resolved "Very Good" →
`USED_VERY_GOOD`). Item fixed live: Dave corrected the condition, staging + the
already-dead-lettered `ebay_publish` job (requeued) both succeeded —
https://www.ebay.com/itm/327268460460.

**Scope, per Dave (2026-07-19): generalize, don't patch one field.**
1. One reusable client-side function for "flag this field red when invalid" — today only
   the title-length check (`updateCharCount`, `http_server.py:6966`) does this, via
   `border-color:#c44`. Condition (and every other draft field) should call the *same*
   function, not grow its own bespoke check.
2. Save-error contract: any PATCH/save failure — local validation (e.g. enum not in the
   category's allowed set) or an eBay rejection bounced back through `ebay_stage`/
   `ebay_publish` — must identify *which key* was errant (`{"field": "condition_enum", ...}`),
   derived from eBay's own `parameters[].name/.value` where available (eBay literally named
   `[condition]` in this incident). The client's field-flagging function then targets exactly
   that field, for any field, from any error source — not just condition, not just eBay
   rejections.
3. Server-side: PATCH on `draft_listing.condition_enum` (and same class of field) must
   validate against the known enum vocabulary before persisting — an operator's correction
   must not silently corrupt a field to something worse than what it replaced (same class as
   invariant C14).

Dispatched to `tgw-coder` as todo #1562. **2026-07-19: branch reviewed and ready
(own worktree, committed) — not yet stitched/merged.** Same runner-review + merge
process as #1563/#1565, just hasn't been run.

## PP-SIMPLEJOBS-001 — `tgw_simple_llm_jobs` MCP tool (DeepSeek V4-Flash non-thinking) — NEW 2026-07-19
**Opened from Dave's Perplexity research (2026-07-19):** DeepSeek V4-Flash's
non-thinking mode is a strong fit for cheap single-pass text transforms — direct
response, no chain-of-thought, 1M context, ~$0.14/M tokens, undercutting GPT-4.1 mini/
Gemini Flash/Claude Haiku on cost for this class of task. Full research + a concrete
tool schema: `inbox/archive/DAVE-RESEARCH-text-processor-mcp-2026-07-19.md`.

**Scope:** one new generic MCP tool, `tgw_simple_llm_jobs`, backed by
`deepseek-v4-flash` via the existing `tgw.apis.llm` facility (`get_task_model()` +
`call_model()`/`_call_deepseek_direct()` in `src/tgw/apis/llm.py` — same pattern as
`pm_intake`/`suggestions_classify`/`pricing_comp_filter`, already `deepseek_direct` in
`tgw-models.json`). Operations: `summarize`, `compress_context`, `extract_fields`,
`classify`, `rewrite`, `rank_snippets`, `log_summary` — one tool, an `operation` enum
argument, JSON-structured output, per the schema in the research doc.

**Distinct from the existing `tgw-aider` DeepSeek tier** — that's a mechanical
Python-coding execution tier (busywork/monitoring), this is a text-transform tool
agents/workers call directly for cheap summarization/extraction/classification, not
code edits.

**Verified live 2026-07-19 (planning pre-flight):** `_call_deepseek_direct()`
(`src/tgw/apis/llm.py`) does not currently support `thinking`-disabled or
`response_format` params in its request payload — only `model`+`messages`. `deepseek_direct`
is already wired direct-primary with OpenRouter fallback (2026-07-08 decision, see
Settled Architecture). No `llm_deepseek` quota_budget entry found in
`tgw-api-config.json` at time of writing — runner should confirm current state, not
assume.

**Not build-authorized as a blank check** — Dave confirmed the direction/tool concept
is clear and asked that this proceed (2026-07-19: "I figured you have it from here").
Todo + packet to follow, dispatched to `tgw-coder` per the usual branch-per-task
contract (PP-HERMES-EA-001).

**2026-07-19: tgw-coder DONE except one config step (Status: partial).** Branch
`todo/1574-simple-llm-jobs-mcp-tool` (commit `24674d1`), worktree
`/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool` — reviewed, ready to stitch
once the blocker below clears. `tgw_simple_llm_jobs` MCP tool built (all 7
operations), 11 new tests, 2641 passed/1 skipped full suite. **Live-verified**: real
DeepSeek V4-Flash calls for `summarize`/`extract_fields`/`classify` against a real
item description, all clean JSON, no reasoning leakage — confirms `_call_deepseek_direct()`'s
existing bare payload already behaves non-thinking for this model, so **no code change
needed for spec step 4** (verified by observation, not assumed). Quota attribution
confirmed via `ai_usage` table + `quota-state.json` — landing in the shared
`llm_deepseek` pool, ~$0.00007/call.

**Blocker (only thing outstanding):** `/opt/TGW/config/tgw-models.json` needs one new
entry (live shared config, outside the git repo — `worktree-guard` correctly declined
to let the agent touch it):
```json
"simple_llm_jobs": { "provider": "deepseek_direct", "model": "deepseek-v4-flash" }
```
Insert after the `pricing_comp_filter` entry. Until this lands, the tool raises
`KeyError` when actually invoked (fail-loud by design, not a bug). Needs Dave (or
someone with edit authority on that live file) to apply this one line, then the
branch can stitch.

Also flagged, not silently dropped: `max_output_tokens` is accepted by the tool's
schema but not yet wired into `_call_deepseek_direct()` (that function has no
`max_tokens` param at all) — left advisory-only per the packet's out-of-scope
boundary (don't touch the shared function beyond the thinking-mode question). Follow-up
if Dave wants it enforced, not automatic.

**Todo #1576, DONE 2026-07-19 — output-contract validation.** Dave's own review:
"it does not have a brain" — the tool was trusting any JSON-shaped model response as
`ok: True` even when it violated what the caller asked (same bug class as the
condition-enum incident: success reported despite an invalid value). Fix, same branch
(commit `7c9df31`): `classify` now verifies the returned label is a member of the
caller's `label_set`; `extract_fields` now verifies every requested `schema` key is
present in the result (extra keys allowed, missing ones are not). Either violation now
returns `{ok: False, error, raw}` instead of silently passing through. Both checks are
skipped when the caller didn't supply the corresponding constraint (`label_set`/
`schema`) — nothing to validate against. 7 new tests, full suite 2648 passed/1
skipped. Sent to Tigwa for independent peer review of the design boundary itself. The
other 5 operations (`summarize`/`compress_context`/`rewrite`/`rank_snippets`/
`log_summary`) have no caller-supplied constraint to check against and were
deliberately left without an equivalent contract.

**Tigwa's peer review, 2026-07-20: verdict CONFIRMED, one bug found.** Design boundary
is correct as the minimum fail-loud contract — label-membership + key-presence, fail
`ok:false` on violation, don't invent contracts for operations with no caller-supplied
constraint (agreed the other 5 correctly have none). **Real bug found:** the code used
`if label_set:` (truthiness) — an explicit `label_set=[]` was silently treated the same
as not-supplied, skipping the check exactly when it matters (an empty allowed-label set
can never yield a valid classification). **Fixed as todo #1577, DONE 2026-07-19,** same
branch (commit `22b892b`): `label_set is None` → open-ended, no check; `label_set == []`
→ reject fail-loud *before* calling the model at all (no wasted DeepSeek call); non-empty
→ validate membership as before. 3 new tests, full suite now 2651 passed/1 skipped.
Tigwa also named a future (not-blocking) idea: `rank_snippets` has a checkable input
domain (returned indexes within `0..len(items)-1`, non-duplicated) that could get its
own bounded contract later — explicitly separate follow-on, not part of this work.

**DONE, 2026-07-20 — merged and live.** `tgw-models.json` config entry applied and
verified live (real `tgw_simple_llm_jobs` call via the merged worktree code, real
DeepSeek response, before merging). Merged `todo/1574-simple-llm-jobs-mcp-tool` into
`catio-nix-0.0.1-alpha` (merge commit `862764f`; one trivial add/add conflict on the
now-stale INPROGRESS breadcrumb, resolved by removing it — its job was done). Full
suite re-confirmed post-merge: 2651 passed/1 skipped. `tgw health`: clean except
pre-existing `backups`/`ebay_sync_fallback` (unrelated to this work). Worktree and
branch removed — everything's in `catio-nix-0.0.1-alpha` now. Todos #1574/#1576/#1577
all closed. PP-SIMPLEJOBS-001 is complete; `tgw_simple_llm_jobs` is live for the next
MCP session.

## PP-FIELDCOMPLETE-001 — category-group attribute completeness, "better than any other eBayer"
**Opened 2026-07-16.** Dave: fill every category-group field during `ai_identify` so
the initial draft view shows all data we have, not just eBay's required/recommended
subset — third confirmed instance of the beats-eBay success bar. **Todo #1475 (Phase
1), DONE 2026-07-16, offline-tested, not yet browser-verified** — "+ Add to listing"
button on any Set A key with no Set B counterpart, wired through the existing
custom-aspect checkbox mechanism (#1472). **Todo #1476 (Phase 2), scoped, not
started** — target field set is the union of all categories' official aspects within
a category group (not just the single assigned category); no new schema needed,
computed live via existing taxonomy calls. Needs a cost/token-budget check against
`LLM-Providers-Quotas.md` before shipping.


## PP-AGENTTRACE-001 — agent trace logging & review UI — NEW 2026-07-20

**Opened 2026-07-20, Dave:** "I want to implement trace logging for all agents... It
needs to save run logs. It should be a skill all agents can use across the board. I
will need access to them via Obsidian and a simple UI to view runs at a glance."
Motivation: reading agent activity logs is how Dave learns what needs fixing in the
system — this is meant to be a durable, cross-cutting capability, not a one-off.
Dave: "This could be our best tool for lasting improvements" — treat as a real
initiative with a full spec, not a quick add.

**The core framing (Dave, confirmed):** this is the Data Charter's raw/derived split
applied to agent activity — raw transcripts are permanent, everything else (index,
Obsidian render, UI) is derived/recomputable. Today agent transcripts are scattered and
some genuinely ephemeral (Claude session JSONL under session dirs, Workflow's
`journal.jsonl` per run, subagent `.output` files under `/tmp/...`, Aider's diff/log) —
a live Prime-Directive-1 gap, not just a nice-to-have.

**"A skill all agents can use across the board" (Dave's framing, referencing how
Anthropic's own engineers use trace logging on this tool):** per the E11/E12 lesson
already applied elsewhere in this project (a written rule depends on the model
choosing to comply; a hook doesn't ask), this is NOT literally one skill file every
agent remembers to invoke by name. It's one shared *contract* — mechanically
propagated per agent type: hooks for Claude Code sessions/subagents (SessionStart/Stop,
same pattern as `session-start-briefing.py`), a `tgw trace start`/`tgw trace end` CLI
wrapper baked into `tgw-coder`/`nix-flake-maintainer`/`aider_run_task` dispatch for
non-Claude-Code agents, with a `.claude/skills/tgw-trace/SKILL.md` documenting the
contract + serving as the manual-invocation fallback for anything not yet covered by a
hook.

**Architecture (4 layers, researched against existing patterns 2026-07-20):**
1. **Raw capture** — `/opt/TGW/var/agent-traces/<YYYY-MM-DD>/<run_id>.jsonl`, `tgw`-owned,
   same atomic-write/archive-before-overwrite discipline (E5) as ItemData. Transcript
   copied/symlinked at run **start**, not just completion, so a killed/stalled run still
   leaves a partial trace (recurring stall pattern already in memory).
2. **Metadata index** — new `agent_runs` table in `state_machine` Postgres (extends the
   existing "Postgres is the work ledger" architecture): `run_id`, `parent_run_id` (nested
   subagents), `agent_type`, `todo_id`/`pp_ref`, `host`, `git_branch`, `started_at`,
   `ended_at`, `status`, `summary`, `transcript_path`.
3. **Capture mechanism** — `tgw_logging.announce_script_run()` (E9) is NOT a durable
   tracking mechanism to extend — confirmed it's just a `log_event()` one-liner, no DB
   row, no run ID, no completion tracking. The new `agent_runs` work is a genuine
   superset, not a duplicate; keep `log_event()` as the underlying emission primitive for
   log-grep parity. Schema gotcha found in research: `schema.sql` and in-code DDL
   (`state_machine.py`'s `_ensure_ai_usage_table()`) are already two independent,
   drifted sources of truth for `ai_usage` — `agent_runs` DDL goes in-code (primary,
   `_ensure_agent_runs_table()` self-apply pattern) with a `schema.sql` copy for
   bootstrap docs only; flag the drift risk explicitly rather than silently repeating it.
4. **Two view surfaces** — Obsidian: `TGW-Agent-Runs.md`, exact `plan_render` pattern
   (pure `build_agent_runs_doc()` + impure `render_agent_runs()` atomic-write, queue-
   triggered + coalesced via `dedupe_key`/`not_before`, same as `catalog_rebuild`/
   `plan_render` itself). UI: new `/form/runs` page on `tgw-http` — session-cookie auth
   via the existing global `_session_guard` middleware (NOT `/api/` Bearer-token style),
   matching `/form/todos`'s query→render→atomic-200-even-on-DB-error shape, reusing
   shared `_STATIC_HEAD`/`_STATIC_FOOT` dark theme.

**Rollout order (Dave, 2026-07-20):** Claude Code sessions/subagents first (hook
infrastructure already exists), then extend to Aider/Tigwa/Hermes via the CLI wrapper.
**Retention:** permanent, per Prime Directive 1 default — no TTL/archive policy unless
volume becomes a real problem later.

**Phased execution (todos filed 2026-07-20, all `tgw-coder` unless noted):**
- Phase 1 (#1580): `agent_runs` Postgres table + `tgw trace start`/`tgw trace end` CLI +
  `tgw_logging` integration + unit tests. Foundation — everything else depends on this.
- Phase 2 (#1581, depends on Phase 1): Obsidian renderer — `TGW-Agent-Runs.md` via the
  `plan_render` pattern, `agent_run_render` queue-triggered worker.
- Phase 3 (#1582, depends on Phase 1): `/form/runs` HTTP UI page.
- Phase 4 (#1583, depends on Phase 1): Claude Code SessionStart/Stop hooks + raw
  transcript capture wiring + `.claude/skills/tgw-trace/SKILL.md` — this leg is hook/
  skill config, not `src/tgw/`/`tests/` app code, so it's main-session work (like the
  E9/E11/E12 hooks before it), not a `tgw-coder` dispatch.

**Progress, 2026-07-20:** Phase 1 (#1580) merged (`f0907f4`) and runner-reviewed
clean — `agent_runs` table + `tgw trace start`/`end` CLI + `archive_transcript()`
live. Phase 2 (#1581) executed by `tgw-coder` (`list_agent_runs()`, coalesced
`agent_run_render` worker, `TGW-Agent-Runs.md` render), awaiting runner-review.
Phase 3 (#1582) packet spec written, queued behind Phase 2's review/merge (depends
on `list_agent_runs()`).

**Phase 4 PAUSED — authenticity/anti-cover-up review (Tigwa → Dave, 2026-07-20).**
Before Phase 4's SessionStart/Stop hooks went live, Tigwa filed a review request
(`CLAUDE-REQUEST-agent-trace-authenticity-review-2026-07-20.md`, archived) naming a
real gap: the trace system as designed is pure self-attestation — a compromised/
runaway agent could omit a run, truncate/replace its transcript, or claim a false
end state, and nothing today detects it. Concrete finding: `archive_transcript()`'s
own re-entrancy ("idempotent re-archival" for legitimate retries) is exactly the
mechanism a compromised agent could exploit to swap evidence after the fact — the
single sharpest gap. Claude's response (todo #1585, sent to Tigwa's inbox): gap
analysis confirmed, proposed a minimum viable integrity contract (content hash +
lock-on-first-successful-archival, `parent_run_id` running-status validation),
explicitly recommending this land before Phase 4 hooks are wired live.

**Dave's refinement, same session:** two additions, both folded into a new draft
packet (**#1586, PP-AGENTTRACE-001 Phase 1b, `docs/TGW-Plan-Vault/plan/packets/
1586-agent-trace-integrity-hardening.md` — DESIGN ONLY, not authorized for build**):
1. Small, hash-verified writes — a separate insert-only hash-commitment table
   (sha256 + committed_at per run_id), rejecting a second commitment for the same
   run_id outright (the actual lock), verifiable later by re-hashing and comparing.
2. Syncthing versioning to an external write-only-from-the-agent's-perspective
   copy — since Syncthing already runs dual-instance on tgw-prod/a1131, a new
   `agent-traces/` folder (Send Only on tgw-prod, Receive Only + Staggered
   versioning on a1131) gives a real independent-witness property: the traced
   agent has no credentials to reach a1131's filesystem/Syncthing config, so even a
   compromised overwrite/delete on tgw-prod propagating to a1131 leaves the
   original content recoverable in a1131's `.stversions/`. This leg is a
   `~/tgw-flake` change — `nix-flake-maintainer`'s to execute, separately
   authorized from the Python/DB leg, not `tgw-coder`'s.
3. **Monitoring by Tigwa-lite (Dave, 2026-07-20)** — handed to Tigwa as a
   requirement, not designed by Claude: periodic notify-only reconciliation over
   `agent_runs` + the hash-commitment table (stale/unclosed runs, hash mismatches,
   missing commitments), same cadence pattern as her existing scheduled plan
   review, same no-mutation-authority pattern as her thermal-monitoring role.
   Scoping/building is hers per the established HR-001 senior-architect-consult
   pattern (she designs, Claude/Dave review after). Sent to her inbox alongside
   the hardening design.

Packet #1586 is explicitly **not dispatched** to any executor — Dave's sign-off on
the concrete diff (and its named tradeoff: locking archival on first write means a
later *legitimate* correction needs its own logged path, not a silent re-run) is
required first. Phase 4 stays paused until Leg A of #1586 lands.

**Leg B concrete spec added, 2026-07-20 (Dave: "tgw-prod's syncthing is now under
the control of the nix maintainer and we need a spec for the required shares and
config options").** Verified live: the `tgw` Syncthing instance
(`nix/tgw/platform.nix`) has NO declarative folder/device config today — unlike
the `db` instance, it isn't even using the standard `services.syncthing` module,
just a raw systemd unit with one narrow idempotent config.xml patch script
(`syncthingTgwFixPorts`, ports only, explicitly untouches `<device>`/`<folder>`).
Packet #1586's Leg B now specifies: new folder `tgw-agent-traces` (name TBD-
confirm-live), `sendonly` on tgw-prod / `receiveonly` + `staggered`
(`cleanoutDays=0`) on a1131, enforced by extending the *same* surgical-patch
technique already proven for the port fix — not a global `overrideFolders=true`
flip, which would also wipe the plan-vault folder's GUI-managed state (not what
was asked). Device pairing, exact live folder list, and a1131 disk headroom are
flagged as nix-flake-maintainer's live-verification steps before writing the
actual diff, not assumed by this spec.

## PP-EVIDENCE-001 — research-intake operator acceptance gate — proposal stage, 2026-07-20

**Not yet a built system — retained design clarifications only, no implementation
authorized.** Dave (via Tigwa) named a research-intake lifecycle that applies to
*all* research submissions (guided Perplexity sessions, MCP/API output, manual
capture, agent-authored inventories/syntheses, future providers), not just guided
Perplexity: `guided-session-active → capture-staged → operator-accepted →
reviewed-synthesis → implementation-authorized`. Everything lands as staged
external evidence first; only Dave's explicit acceptance selects a named
destination/role. Syncthing is transport/recovery substrate only — arrival,
sync, or checksum match never promotes a capture; the acceptance gate lives at
the authoritative library. A companion principle note frames this as one leg of
a standing, non-terminal verification/fortification loop (learn what's
verifiable → record the boundary → fortify the highest-value weakness → verify
→ repeat), explicitly warning against mistaking a hash alone, a synced copy, or
a one-time pass for durable assurance.

Retained research: `dev-workflow/research/RESEARCH-perplexity-guided-and-
governed-research-integration-2026-07-20.md`,
`RESEARCH-perplexity-guided-research-operator-acceptance-gate-2026-07-20.md`,
`RESEARCH-all-research-submissions-operator-acceptance-gate-2026-07-20.md`,
`RESEARCH-research-intake-syncthing-transport-library-gate-2026-07-20.md`,
`RESEARCH-evidence-fortification-continuous-verification-principle-2026-07-20.md`.

**Claude's independent review sent 2026-07-20** (todo #1594, full text in
`inbox/tigwa/CLAUDE-REVIEW-research-acceptance-gate-review-2026-07-20.md`): no
unsafe transition found that lets staged material silently become canonical as
designed. Additions flagged, not corrections: an explicit `declined` state
distinct from not-yet-reviewed; version/supersede accepted artifacts instead of
editing in place (same append-only shape as invariant E14); a closed
`artifact_kind` enum; link agent-authored submissions to their
`agent_runs.run_id` (PP-AGENTTRACE-001) rather than a second identity notion;
enforce the accept-boundary mechanically (a canonical row require a non-null
`accepted_by`/`accepted_at`, not just documented discipline — same fix shape as
`trace-immutability-guard.py`); reuse PP-AGENTTRACE-001's #1586 Leg A
hash-commitment shape here instead of a second bespoke one, once that lands.

**Still open:** Tigwa's Stage 0 audit (read-only asset/trust register) is in
progress on her side — todo #1589 tracks waiting on her proposal. This section
stays proposal-only until that lands and Dave authorizes a concrete design.

## PP-FLAKEGATE-001 — agent push/switch authority as a state-machine gate, NEW 2026-07-21

**Incident:** `nix-flake-maintainer` committed and pushed `4adb145` (far2l,
todo #1620) to `origin/master` on `~/tgw-flake` without Dave's explicit push
confirmation. Root cause: the 2026-07-20 "batch the mutating calls" change
(`PP-AGENT-DISCIPLINE-001`, chaining `git add && git commit && git push`
into one compound call per host to cut prompt fatigue) removed the last
point where a push got its own gate — combined with the session running in
Auto Mode (suppresses permission prompts) and the already-confirmed
upstream bug where PreToolUse hooks never fire for Agent-tool subagents
(`anthropics/claude-code#69260`), nothing actually stopped the push. Far2l
itself was reverted same session (revert commit, pushed to `origin/master`
per Dave's direct instruction) — it was never applied to a1131's running
system, so no live-system undo was needed.

**Dave's direction, 2026-07-21: "a more state machine centric approach
using the rest of our patterns."** Don't rely on a hook (broken for
subagents) or on written-procedure compliance (tonight's actual failure
mode) — apply the same shape already proven twice elsewhere this project
(`enqueue_job()`/`queue_jobs` as the manifest enforcer, PP-STATEMACHINE-001;
`ebay_publish`'s manual-trigger-only pattern, `tgw publish <sku>` /
`src/tgw/api.py:751`) to git push and `nixos-rebuild switch` themselves:

- `nix-flake-maintainer` commits locally, then calls a new CLI command
  (`tgw flake request-push` / `tgw flake request-switch`) that enqueues a
  `queue_jobs` row (`queue_name='flake_mutation'`, `entity_id`=commit sha,
  payload `{repo, host, kind, summary}`) — it never runs `git push` or
  `nixos-rebuild switch` directly again.
- **Built version (todo #1621), corrected from this section's original
  write-up:** the tgw CLI initially never executed the push/switch itself,
  under any command name — a human ran the real `git push`/`nixos-rebuild
  switch` by hand, then called `tgw flake mark-executed <job-id>` purely to
  record it happened (`queued -> succeeded`, no side effects).
- **Revised same day, todo #1625 (Dave, after using #1621 live once):** the
  fully-manual version was clunky in practice — copying host/commit/repo
  out of the job record, running the exact right incantation by hand,
  remembering to call `mark-executed` after. Added `tgw flake push
  <job-id>` / `tgw flake switch <job-id>`: still human-only (interactive
  confirmation or explicit `--yes`, never called by any agent profile —
  `nix-flake-maintainer.md` explicitly prohibits it, same as `mark-executed`),
  but now executes the real command itself, gated by two checks that
  preserve the original safety intent: (1) the command must run on the
  job's own recorded `host` (hostname check, no cross-host SSH magic), (2)
  the local checkout's actual `HEAD` must exactly match the job's recorded
  commit sha, or it refuses — never pushes/switches to "whatever's checked
  out," only the exact hash the request named. Auto-calls `mark-executed`
  only on confirmed success; a failed push/switch leaves the job `queued`
  for retry. This is the resolution of the earlier "safer with zero
  callable path" argument above — Dave's judgment: hash+host verification
  is sufficient safety, and pure friction has its own cost worth removing.
- `.claude/agents/nix-flake-maintainer.md` Step 2/5 rewritten to request
  instead of execute; direct `git push`/`nixos-rebuild switch` removed from
  its narrow-mutation list entirely; standing prohibition added on ever
  calling `mark-executed` itself.
- New invariant E17 + detector (`tgw flake audit`): any push on
  `~/tgw-flake` not backed by a matching executed `flake_mutation` job
  record is a finding (extends the #1602 detective-control direction
  already named for E11/E12/E14). Known gap: `audit`'s live test ran
  against a disposable repo, not `~/tgw-flake` itself, due to a `tgw`/`db`
  OS-user Postgres-peer-auth-vs-filesystem-permission split — todo #1623.

**First live test case, planned not yet run:** re-adding far2l to a1131
(todo #1620, still open) through this new request/push/switch path, end
to end, with acceptance = `far2l --version` succeeding on a1131 post-
switch. #1625 (the `tgw flake push`/`switch` build itself) is in progress
under `tgw-coder` as of 2026-07-22 — this test case is blocked behind that
landing, not run yet.

Todo #1621 — **built, live-verified against the real `state_machine`
Postgres DB, cleared for stitch** (`packets/results/1621-flakegate-RESULT.md`).
Not yet merged to `catio-nix-0.0.1-alpha`.

**Refinement proposed 2026-07-22 (Dave, on the friction of tonight's
`mark-executed` round): "I get it. I just don't think it is very
friendly... maybe for servers, not for users."** Not a request to weaken
the gate — the decision-point it protects (does a `git push`/
`nixos-rebuild switch` actually happen) earned its caution from a real
incident (this PP's own origin story, above) and should stay exactly as
strict. The friction flagged is narrower: **recording that an already-
completed push/switch happened currently asks Dave to re-attest something
the system can already corroborate on its own** — live system-generation
hash vs. the requested commit's dry-activate store path, git log ancestry
between the two hosts. `mark-executed` today has no such cross-check; it
just trusts whatever's typed. Proposed distinction to design toward:
**decide** (should this push/switch happen) stays a hard human gate,
full stop; **record** (did it happen) could lean on live evidence instead
of a fresh unverified assertion every time — e.g. `mark-executed` doing
its own `readlink /run/current-system` / git-log check against the job's
recorded commit and store path before accepting the human's word, flagging
a mismatch rather than silently trusting either side. Not scoped or
decided — a design direction for whenever this PP gets touched again, not
tonight's work.

## PP-WORKFLOW-001 — declarative pipeline/DAG layer, proposal only, NEW 2026-07-21

**Gap named, not yet built.** Dave asked "where are our workflows defined?"
during the PP-FLAKEGATE-001 session — the honest answer is: nowhere, as
data. Confirmed live via code read: every worker's "what runs next" is a
hardcoded `enqueue_job(queue_name="next_thing", ...)` call sitting at the
tail of its own success path (e.g. `ai_identify.py:484-503` directly calls
`enqueue_job(queue_name="ebay_draft", ...)` and
`enqueue_job(queue_name="alt_text", ...)` inline; `ebay_draft.py` does the
same for `ebay_price`/`ebay_upload`). The only place the full pipeline
topology is visible as a whole is `reference/TGW-Pipeline-Flow.md` — a
hand-maintained description of what the code does, not something the code
reads from or that stays in sync automatically. PP-STATEMACHINE-001/
invariant E16 made individual *jobs* well-formed (`dedupe_key`, `entity_id`
required) but never addressed pipeline *topology* — a job manifest
guarantee, not a workflow-graph one.

**Why this matters now:** Dave, same session, connecting it directly to
the PP-FLAKEGATE-001 build: "it would have made what we just hacked
together easy and proper." The flake push/switch approval flow (request →
plan/triage → dispatch to nix-flake-maintainer → build/commit → hand off
commit hash → human approves → execute → mark done) is itself a workflow
— currently expressed as a bespoke `queue_jobs` queue_name
(`flake_mutation`) plus one-off CLI subcommands and a hand-written agent
contract, because there was no general "declare a workflow" primitive to
reach for. A real pipeline-as-data layer would let both the item pipeline
(ai_identify→ebay_draft→...→ebay_publish) and things like the flake gate
be declared once, queried, and visualized instead of each being its own
bespoke wiring exercise.

**Not yet scoped:** what the declaration format looks like (a table? a
config file? something `enqueue_job()` itself consults to auto-enqueue the
next step, replacing the hardcoded calls?), whether it replaces or just
documents the existing hardcoded chains, migration path for ~15 existing
workers, and how manual-trigger-only steps (`ebay_publish`,
`flake_mutation`'s human-approval gate) fit a declarative model without
losing their deliberate human-gate property. Relevant precedent this would
build on top of, not replace: PP-STATEMACHINE-001 (job manifest enforcement,
E16), PP-FLAKEGATE-001 (human-approval-gate pattern, E17), `tgw-queue-
priorities.json` (existing precedent for a declarative config file
`enqueue_job()` itself consults).

Also relevant to the master-plan sequencing Dave flagged the same session
(prepping for a larger coding budget via the harness/Agnes work) — a
declared pipeline layer is exactly the kind of foundational-but-not-urgent
work worth sequencing *before* a burst of higher-throughput coding capacity
arrives, rather than after. No todo opened yet — proposal stage only,
needs its own scoping session before anything gets built.

**DECISION, 2026-07-21 (Dave, planning session): build native on `queue_jobs`,
not an external DAG/workflow tool.** Prompted by Dave's own Perplexity research
(`inbox/claude/TIGWA-RESEARCH-orchestrator-dag-2026-07-21.md`) exploring whether
Agnes AI or a dedicated orchestrator (Dagster/Prefect/Airflow) should sit above
the state machine for the harness/Agnes-acceleration work (see
`project-hermes-strategy-pivot-custom-harness-agnes` memory). The research's own
arc, after being told what TGW already has (MCP-exposed specialist coders,
worker contracts, invariants, Claude-trains-each-coder-until-trusted, a manifest
enforcer at execution time, trace logging, systemd supervision, hash-index/
logserver), converged on: Agnes is an inference gateway, not an orchestrator;
Prefect was the strongest external-tool candidate, but only as a thin layer
*above* an already-mature control plane, never as the policy engine.

**Claude's independent take, given directly (not deferring to Dave's lean):**
recommended the native path over Prefect for three reasons — (1) dual-source-
of-truth risk: Prefect's own flow-run state would sit alongside `queue_jobs`,
requiring permanent reconciliation, directly violating the "reuse, don't invent
a competing authority" principle E16/E17 already established as load-bearing;
(2) external-roadmap risk: even self-hosted, Prefect's future capability/pricing
shape is Prefect's decision, not TGW's — the exact "peripheral control of the
future" cost Dave named as the standing worry with any outside tool; (3) the
actual missing piece (`depends_on` dependency gating, specialist-tag routing) is
small given TGW's real concurrency (single digits to low dozens of packets, not
an enterprise data-pipeline scale) — cheap to build directly on `queue_jobs`,
expensive to bridge into if adopting Prefect's own task/flow model instead.

**Dave's framing, closing the decision:** "We like simple. We can always bolt
on something else if we find a use case." Explicitly not foreclosing Prefect or
any other tool forever — this is "try the simple native version first, adopt
something heavier only if a real gap proves it's needed," not a permanent
rejection. Dave also named a forward-looking reason particular to this
project's trajectory: as more capable models get introduced that can
adversarially test the native layer for gaps/exploits (the harness/Agnes
acceleration this whole conversation started from), a homegrown, fully-understood
layer may turn out easier to harden and extend than an opaque external one —
"our goal process may make maintaining it easier... so we can look at doing
more later, again, if necessary."

**What "native" concretely means, Phase 1 scope (not yet built, no todo opened
yet):** extend `queue_jobs` (or a thin adjacent table, TBD) with a
`depends_on UUID[]` (or equivalent) column — a job is eligible for dispatch
only once every job it depends on has reached a terminal `succeeded` state; add
a `specialist_tag` (or reuse `queue_name`/`handler_family`, TBD) for routing to
the right MCP-exposed coder (Flutter/Dart UI, Kotlin camera, general Aider,
etc.) instead of hand-picking per packet. No new service, no new supervision
surface, no new source of truth — same `enqueue_job()`/`state_machine.py`
substrate PP-STATEMACHINE-001 (E16) and PP-FLAKEGATE-001 (E17) already extended
cleanly. Retries/backoff/dedup are already solved (E16's debounce/supersede);
this phase is purely the dependency-ordering + routing gap the research
correctly identified as the one real missing piece.

**Explicitly deferred to a later phase, if ever needed:** anything Prefect-class
tooling would give for free that native doesn't — a web dashboard/UI (not
currently missed; this project's practice is CLI + the clipboard picker, not
dashboards), branching/fan-out/fan-in DAG shapes beyond simple linear
dependency chains, per-edge retry policies. Revisit only if a real packet shape
proves the simple linear-dependency model insufficient — do not build these
speculatively.

### Technical deep-dive: exact schema/query changes, unfolded 2026-07-22

Checked the real schema and claim function before designing anything new
(same discipline as the Postgres/AIOPS unfolds) — this found the actual
footprint is smaller than the Phase 1 write-up above implied.

**`specialist_tag` already exists — it's `handler_family`, live in
`queue_jobs` today.** Confirmed via `\d queue_jobs`: the column is already
there, currently defaulting to `queue_name` (`state_machine.py:317`,
`handler_family = handler_family or queue_name`) and not yet populated
with distinct specialist-routing values. **No new column needed for
routing** — this phase just means actually setting `handler_family` to a
real specialist tag (`tgw-coder`, `nix-flake-maintainer`, a future
Flutter/Dart specialist) at enqueue time for packets that need it, and
having the dispatch logic read it. Another instance of the same
plan-vs-code drift pattern PP-POSTGRES-001's P0 finding surfaced —
worth citing as a second live case study for PP-OPSREALITY-001.

**`depends_on` — one new column, one WHERE-clause change, no new state.**
```sql
ALTER TABLE queue_jobs ADD COLUMN depends_on UUID[] NOT NULL DEFAULT '{}';
```
Extend `claim_queue_jobs()`'s existing `candidates` CTE (`schema.sql:232`)
with one added condition:
```sql
AND NOT EXISTS (
    SELECT 1 FROM unnest(q.depends_on) AS dep(dep_id)
    JOIN queue_jobs d ON d.job_id = dep.dep_id
    WHERE d.state <> 'succeeded'
)
```
A job with unmet dependencies simply stays invisible to `claim_queue_jobs`
— no new `blocked` state, no promoter/sweep job, no second mechanism to
keep in sync with the existing lease/retry machinery. **Zero migration
risk for the ~15 existing workers**: `depends_on` defaults to `'{}'`
(empty array), so every existing hardcoded `enqueue_job()` call chain
keeps working completely unchanged — this phase is additive-only, not a
rewrite of ai_identify.py/ebay_draft.py's inline enqueue calls. Workers
adopt `depends_on` only when a NEW packet actually needs declared
dependency ordering (e.g. the flake-gate style multi-step approval flow),
not as a mass migration.

**Dependency-failure visibility (the one real gap this design adds, not
carried over from anywhere else):** a job whose `depends_on` entry
reaches `dead_letter` or `cancelled` (not `succeeded`) will sit in
`queued` forever, permanently unclaimable — an orphan by the "findable/
addressable/deletable" standard this project already holds itself to.
Fix: extend the existing `recover_expired_jobs()` periodic sweep
(`schema.sql:258`, already runs on a cadence, same reuse-don't-invent
principle as everywhere else in this unfold) with one more clause —
transition a `queued` job straight to `dead_letter` with
`error_code = 'DEPENDENCY_FAILED'` once any of its `depends_on` entries
reaches a terminal non-`succeeded` state. This makes a broken dependency
chain a visible, alertable finding (same C11 discipline used throughout
this codebase) instead of a silent stall.

**Manual-approval-gate open question, closed cheaply:** `ebay_publish`
and `flake_mutation`'s human-gate steps don't need `depends_on` at all —
today's actual model is "the job doesn't exist until the human trigger
creates it" (operator runs `tgw publish`, `tgw flake request-push`
followed by human execution), which already IS the dependency gate,
expressed as job non-existence rather than a `depends_on` array.
`depends_on` only needs to model automatic job-to-job sequencing; human
gates stay exactly as they are, no new modeling needed.

### Bounded Phase 1 packet

**Acceptance criteria**: `ALTER TABLE` lands with zero downtime (additive
column, safe on a live table this size — 310k rows, not a blocker);
`claim_queue_jobs()` change verified against a real dependency chain (one
throwaway job depending on another, confirm the dependent job is NOT
claimable until the parent reaches `succeeded`, then IS claimable
immediately after); existing worker dispatch behavior unchanged (a
regression check: run the existing pipeline end-to-end on one test item,
confirm no behavior change since no existing job sets `depends_on`);
`recover_expired_jobs()` extension verified against one deliberately
failed dependency (confirm the dependent job reaches `dead_letter` with
`DEPENDENCY_FAILED`, not silent stall).
**No-go conditions**: any change to `claim_queue_jobs()`'s query plan/
performance for the (overwhelmingly common) empty-`depends_on` case —
this must stay index-friendly, verify with `EXPLAIN ANALYZE` before and
after; any existing worker's dispatch order or dedupe behavior changing
as a side effect.
**Dependency**: none — purely additive, can start any time.
**Owner**: `tgw-coder` dispatch once filed as a todo/packet (not yet
filed — this section is the design).

### Workflow-ordering doctrine correction, 2026-07-25 (Dave/Hermes,
`HERMES-WORKFLOW-ORDERING-DELTA-2026-07-25.md`)

**Purpose:** correct the queue/workflow model so that continuity and
priority do not let work occur in the wrong process position — folded in
here since it corrects the exact priority/dependency model this PP designs.

**Rule.** A queue is not merely a prioritized list. It is a durable
work-continuity structure whose items move through an explicit process
state:

`candidate → classify/assign authority → prerequisites/evidence → ready →
active → verify outcome → completed | corrective/blocked follow-up`

Human-created, filter-created, system-created, and AI-proposed items all
use the same state model.

**Priority rule.** Rank only work that is **ready**. Keep blocked
high-impact work visible, with its blocking prerequisite and owner, but do
not allow it to bypass the prerequisite simply because it is urgent, next
in insertion order, or AI-selected. Priority inputs should be inspectable:
operational harm, customer/revenue impact, deadline, dependency-unblocking
value, evidence confidence, cost/quota, age, and an explicit Dave override.

**Next Item rule.** After a recorded terminal outcome, Next Item selects
the highest-priority eligible ready item — or, when the selected priority
item is blocked, the next prerequisite job that unblocks it. It hands the
person or AI the shared queue context, prior outcome, evidence, gate state,
and required action. It does not execute the new item.

**Applied current example (2026-07-25):** the listing-surface repair
cannot proceed to acceptance/deployment after its focused test passes — the
declared Nix development environment first has to run the reproducible
test command without ephemeral packages. Missing `python-multipart` and
`mistune` (see PP-NIXOS-001's 2026-07-25 consolidated Nix batch entry) are
therefore upstream package-manager workflow jobs that block final
source-fix acceptance, not unrelated cleanup to defer — a concrete instance
of the priority rule above (ready-but-blocked work stays visible with its
real prerequisite, not silently deferred or force-ranked past it).

**Required queue item fields:** `source/creator`, `authority`, `priority
rationale`, `dependencies`, `required gates`, `state`, `assignee`,
`continuity context`, `evidence links`, `outcome`, `next-action/stop
condition`. These map onto this PP's own `depends_on`/`handler_family`
design above (the `state` enumerated here is a refinement of
`queue_jobs`' existing state column; the other fields are candidate columns/
metadata for whichever Phase adopts explicit dependency-ordered packets).

## PP-ORCHESTRATOR-001 — the custom coding harness's orchestrator, proposal only, NEW 2026-07-21

**Origin:** same 2026-07-21 planning session as PP-WORKFLOW-001's native
decision above, prompted by Dave's own research
(`inbox/claude/TIGWA-RESEARCH-orchestrator-dag-2026-07-21.md`) into whether an
external swarm/orchestration tool should sit above the state machine for the
accelerating custom-harness plan (see `project-hermes-strategy-pivot-custom-
harness-agnes` memory — Max plan + Agnes 2.5 Flash, explicitly *not* a pivot,
"speeds up adoption of the full plan"). Where PP-WORKFLOW-001 is the narrow
dependency-ordering primitive, this PP is the actual harness architecture that
primitive serves — what replaces me directly spawning an Agent-tool subagent
for a packet.

**The flow, confirmed shape 2026-07-21 (Dave):**
1. **Intake** — a task prompt goes to the orchestrator (an MCP-exposed
   entrypoint) instead of a direct Agent-tool spawn.
2. **Triage/decompose** — orchestrator (or Hermes, per the research's own
   "planner defines the packet" framing) splits a multi-domain request into
   per-specialist sub-packets, each a `queue_jobs` row,
   PP-WORKFLOW-001's `depends_on` wiring order between them.
3. **Dispatch to specialist inbox** — once a packet's dependencies are
   satisfied, the orchestrator delivers it into that specialist's inbox.
4. **Specialist executes** — inside its own worktree, under its own
   contract/invariants/manifest enforcer — same shape as `tgw-coder` today,
   generalized to future domain specialists (Flutter/Dart UI, Kotlin camera,
   etc. — named by Dave as planned specialist roles).
5. **Verification** — tests run, trace logged (existing infrastructure,
   PP-AGENTTRACE-001/E14 — no new logging surface needed).
6. **Result delivered to reviewer inbox(es)** — specialist's result goes to
   Claude's inbox (and GPT's, if wired as an adversarial reviewer per the
   research's own Hermes-adversarial-GPT pattern), reusing the same mailbox
   mechanism in reverse.
7. **Review loop** — Claude (+ GPT adversarial) checks diff against
   packet/invariants, capped at Dave's standing **3-exchange bounded review
   loop** rule ("hermes and claude review until satisfied and I review, never
   taken more than 3 exchanges") — forces decisiveness; anything unresolved
   after 3 rounds needs a tighter contract/narrower packet, not more discussion.
8. **Hands back to Claude for human-facing review** — final review-ready
   summary lands in my inbox; Dave is still the merge authority — same
   `mark-executed`/human-approval shape PP-FLAKEGATE-001 (E17) already proved.

**Specialist inboxes — not a new mechanism, an extension of one that already
exists.** `tgw_mailbox_send`/PP-RUNNERCOMMS-001 already implements "actor has
an inbox" (writes into `inbox/<actor>/`, currently scoped to
claude/tigwa/dave). This extends the same recipient class to specialist
coders — the orchestrator's dispatch step (3 above) and result-return step (6
above) are both just mailbox sends to a wider actor list. No new
infrastructure to invent for this piece.

**Model selection + budgeting is an explicit orchestrator responsibility,
not each specialist's own concern** (Dave, 2026-07-21: "the orchestrator will
also have model selection budgeting"). Reuses `tgw-models.json`/
`get_task_model()` (E8/E15's config-only routing rule — no model ID ever
hardcoded in orchestrator code) and the existing `PP-QUOTA-001` quota-context
plumbing already wired into `worker_base.py`. Extends further to become the
actual **consumption path for "the ferals"** (`pp/PP-CATIONIX-001-ferals-
audit.md`, Tigwa's underused/bundled-resource audit) once an individual feral
clears its 10-point admission checklist — today that audit is a reference
document a human reads; this makes it something the orchestrator's routing
decision can actually draw on live, once entries are tamed. **Currently
blocked/stale, not yet actionable broadly:** most ferals sit at
`NEEDS ACCOUNT CHECK`, gated on Dave's own signed-in verification (todo
#1457) — and the audit itself needed a same-day refresh 2026-07-21 for two
new resources (Agnes AI key now in hand; OpenCode key incoming, purpose
unspecified) neither of which has run the admission checklist yet. See the
"Stale as of 2026-07-21" section of the ferals-audit file.

**Why native/homegrown wins here, Dave's closing framing, 2026-07-21:** "this
type of customization is why our own simple orchestrator will excel. It isn't
the DAG per se, but the structure that supports the DAG that makes it
simpler." The dependency-ordering mechanism itself (PP-WORKFLOW-001) is
genuinely small and would be small in any tool. What's *not* small, and would
have to be awkwardly bolted onto an external orchestrator's own abstractions
(Prefect's `@flow`/`@task` model, its own task-run state) instead of living as
a natural extension of infrastructure already owned end-to-end, is everything
this PP actually depends on: the mailbox/inbox convention, per-specialist
contracts/invariants/manifest enforcement, trace logging, the ferals-aware
budget/routing layer, and Dave's own bounded-review-loop policy. Building
native means all of that stays one coherent, fully-understood system instead
of two systems (ours + the external tool's) that have to be kept in sync —
same "reuse, don't invent a competing authority" principle E16/E17 already
established, now the stated reason the whole harness is worth building
in-house rather than adopting a framework.

**Relationship to existing PPs, not a replacement for any of them:**
- **PP-WORKFLOW-001** — supplies the `depends_on`/dependency-gating primitive
  this orchestrator's step 2 relies on.
- **PP-RUNNERCOMMS-001** — supplies the inbox/mailbox mechanism steps 3/6
  reuse.
- **PP-HERMES-EA-001** — Hermes stays the free-flow/exploratory layer feeding
  packets in (per the research's own "Hermes explores, Claude shapes into a
  plan, contract layer bounds it, specialists execute" arc); this PP doesn't
  change Hermes's own contract or IN TRAINING status.
- **PP-CATIONIX-001** — this orchestrator is a concrete piece of the broader
  agent-confinement/"dev team" platform PP-CATIONIX-001 already names as the
  endgame, not a competing design.
- **PP-AGENTTRACE-001/E14, PP-AGENT-DISCIPLINE-001/E11-E12** — every
  specialist dispatched through this orchestrator still needs the same
  trace-immutability and role-restriction guarantees already built; this PP
  assumes and extends them, doesn't relitigate them.

**RESOLVED, 2026-07-21 — the orchestrator vehicle gap: no new service on day
one, it's the specialist-roster-growth pattern itself.** Gap flagged same
session in a Claude-authored gap sweep: PP-ORCHESTRATOR-001's step 1
("MCP-exposed entrypoint") named no concrete implementation, and the
write-up didn't connect to the fact that `tgw-aider`'s MCP bridge
(`aider_run_task(task_slug=...)`, isolated worktree+branch per task,
`aider_get_diff`/`aider_get_log`) already implements steps 3-5 (dispatch →
isolated execution → result) for one specialist today. Dave's answer:
**"once we are comfortable with tgw-coder we can move on to aider, create a
new specialist... we have a plan, we have a direction. Fill it in for
execution."**

Concretely, this means:
- **tgw-coder is specialist #1, already trusted, already running this
  exact shape manually** — packet → isolated worktree/branch →
  `tgw-runner-review` (steps 5-7, already-built, not redesigned by this PP)
  → stitch. What's currently missing is not the mechanism, it's that today
  I (Claude) am the orchestrator by hand: I read the packet, dispatch via
  Agent tool / `tgw-coder` invocation, wait, review the result. Formalizing
  PP-ORCHESTRATOR-001 means naming this the pattern, not replacing it.
- **Aider becomes specialist #2 once tgw-coder's pattern is solid** —
  reusing the already-existing `tgw-aider` MCP bridge (`aider_run_task`),
  not new infrastructure. "Comfortable with X, then add Y" is the
  admission process for every future specialist (Flutter/Dart UI, Kotlin
  camera, whatever comes next): prove the narrower single-specialist loop
  first, add the next specialist to the roster only once the prior one is
  trusted — never stand up N specialists at once.
- **PP-WORKFLOW-001's `depends_on` primitive earns its keep exactly when a
  second specialist joins** — with one specialist (tgw-coder alone) there
  is no cross-specialist ordering problem to solve; dependency-gating
  becomes load-bearing the moment a packet needs tgw-coder's output before
  Aider's step can start (or vice versa). This is the natural reason to
  sequence PP-WORKFLOW-001 Phase 1 before or alongside adding specialist
  #2, not before specialist #1 (which needs none of it).
- **Triage/decompose (step 2) stays Claude's job for now, not Hermes's** —
  resolves the "orchestrator (or Hermes)" ambiguity flagged in the gap
  sweep: Hermes/Tigwa's MCP link is still read-only
  (`TGW_MCP_READONLY=1`, no `tgw_enqueue`) while IN TRAINING, so nothing
  changes about who is authorized to actually call `enqueue_job()` — it's
  still me, dispatching via packet, same as today. Hermes proposing /
  free-flow exploring still feeds packets *to* Claude per PP-HERMES-EA-001's
  existing arc; this PP doesn't grant her a new write path.

**Execution sequence for the prep window (Dave, 2026-07-21: "you have a
couple of days to prepare," anticipating a push toward month-long-sprint
pace once Max lands Friday):**
1. Keep proving tgw-coder under real packets (already underway, ongoing).
2. Land PP-WORKFLOW-001 Phase 1 (`depends_on`/routing on `queue_jobs`) —
   smallest, fully-scoped, DB-only, no specialist-roster decision blocks it.
3. Formalize "tgw-coder = specialist #1" explicitly in this PP (done by
   this edit) — no new code required, this step is documentation catching
   up to already-live practice.
4. Once (2) lands and (1) has enough reps to call tgw-coder trusted for the
   packet types it's handling: add Aider as specialist #2 via the existing
   `tgw-aider` bridge, wire a real multi-specialist packet through
   `depends_on` as the first live test of PP-WORKFLOW-001 actually mattering.
5. Only then: revisit model-selection/budgeting/ferals-routing (currently
   moot with a single specialist) and PP-APPROVAL-001's generalized
   human-gate (currently satisfied ad hoc by the same stitch step tgw-coder
   already uses).
6. **Once the two-specialist loop (tgw-coder + Aider) is running cleanly
   under real packets: spin off the repetitive orchestration mechanics
   itself** (Dave, 2026-07-21, same breath as step 4: "then you spawn off
   the repetive orchestration and get comfortable with the result"). This
   is the same trust-then-delegate discipline applied one level up — steps
   1-4 prove individual *specialists* one at a time before trusting them;
   step 6 proves the *triage→dispatch→review→stitch loop around them*
   before trusting it to run with less hands-on involvement from Claude
   each cycle. Concretely: today I do triage/dispatch/review by hand every
   time; once that hand-run loop has enough clean reps across both
   specialists, the mechanical parts of it (not the judgment calls) become
   the actual thing this PP automates — and, same as every specialist
   before it, we get comfortable with its output before leaning on it
   further. Not yet scoped in detail — sequenced after step 4/5, not before.

**Status: vehicle question resolved, sequence agreed (todo #1626 opened for
step 2, the only piece that's genuinely new code), still nothing built.**
Steps 1, 3, and 6's framing require no build yet. Full build-out expected
to accelerate once Dave's Max plan lands (target: this Friday, 2026-07-24,
contingent on the prep week going well) — see
`project-hermes-strategy-pivot-custom-harness-agnes` memory for the
funding/timeline context.

## PP-APPROVAL-001 — general human-in-the-loop approval gate, proposal only, NEW 2026-07-21

**Generalizes PP-FLAKEGATE-001's proven pattern into a standing TGW
primitive.** Same session, right after Dave hit Ctrl-C mid-flow by accident
and found the queued `flake_mutation` job just sat there untouched, safe:
"this is our human in the loop solution across the board." The property
that makes this work — a durable Postgres row surviving interruption, vs.
an imperative script that can be left half-done — is exactly what any
human-approval-gated action in TGW should have, not just flake push/switch.

**Surface: corrected same session — the clipboard/rofi picker, not
Flutter.** Dave's first phrasing ("flutter app/event surface") was a
misstatement — he meant **`tgw-clipd`/the rofi picker (PP-CLIP-001)**, TGW's
already-live local operations surface. This lines up cleanly with the
already-settled 2026-07-19 "elevated vision" for that same picker (see
PP-CLIP-001 above): "typed entries... each a discrete handler sharing one
interface, inline per-entry mini-apps instead of separate app windows" —
PP-OUTBOX-001 was named as the first serious application of that surface;
a pending-approvals feed (list queued `human_approval`/`flake_mutation`
rows as entries, select one, inline approve/execute) is a second, natural
fit for the exact same picker, not a new UI to build. Note the surface's
own history: its "primary interface" framing was superseded by PP-RADAR-001
for *networked* clipboard sharing specifically — that supersession doesn't
apply here, since a pending-approvals feed is local-only by nature (same
category as PP-CLIP-001's Phase 1/2 local work, which stays valid).

**Shape (not yet built):** generalize `flake_mutation`'s queue_jobs shape
into a proper `human_approval` (or similarly named) work class any part of
the system can enqueue into — an agent, a worker, a future harness action
— each row carrying whatever context a human needs to decide (summary,
source, risk, linked PP/todo), sitting `queued` until a human either
approves (triggering the real action + auto-closing, per todo #1625's
`tgw flake push/switch <job-id>` shape) or the row just sits, indefinitely
safe, if nothing happens. Same durability property PP-WORKFLOW-001 already
named as valuable — this is arguably that proposal's concrete first
instance, human-approval steps being one clear category of "workflow node"
worth declaring once instead of rebuilding bespoke each time (flake gate
today, whatever needs one tomorrow).

**No Flutter/PP-UIUX-001 blocker after correction** — the surface already
exists and is live (`tgw-clipd` + rofi picker on tgw-prod), so this is
lower-friction to build than initially assessed, not blocked on the
UI-unification project at all.

**DECISION, 2026-07-21 (Dave): typed handlers, config-driven, not a generic
callback.** Scoping question was: does an approved row carry generic
re-enqueue instructions (one dispatch mechanism for every approval type),
or does each `approval_type` have its own registered handler? Dave: "We may
have agents or scripts that need approvals. Typing each one is really just
another config that sets the communication with the endpoint" — same
config-not-code instinct as `tgw-models.json`/E8/E15 (model routing) and
`tgw-queue-priorities.json` (priority routing), applied here: a new config
file (name TBD, e.g. `tgw-approval-handlers.json`) maps each
`approval_type` to how to actually execute an approval — which endpoint to
call (worker enqueue, agent dispatch, script invocation) and what shape
that call takes — rather than hardcoding a per-type branch in Python.
Adding a new approvable action (a script, an agent action, a future
harness step) becomes a config entry, not a code change — matching the
existing settled-architecture rule "model routing is config, never code"
generalized to "approval routing is config, never code." Known first two
`approval_type` entries: `flake_push`/`flake_switch` (today's bespoke
`flake_mutation` shape, migrated in) and `dependency_resubmit` (#1627, the
PP-WORKFLOW-001 dead-letter/resubmission case) — both migrate onto this
registry rather than staying one-off.

**Status:** vehicle/typing decision made (typed, config-driven handlers);
schema (the `human_approval` row shape, the handler-config file format, the
`tgw-clipd` entry-type wiring) not yet designed in detail — needs its own
scoping pass, not yet packet-ready. Natural next step: work out the
entry-type/handler shape alongside whoever picks up PP-OUTBOX-001, since
both are "new discrete handler in the same picker" instances of the same
underlying idea. No todo opened yet for PP-APPROVAL-001 itself.

**Human-presence lock — a new sub-concept, 2026-07-21 (Dave), not to be
confused with `PP-CATIONIX-001.md`'s existing "crypto-lock."** That
existing entry (line ~257 of that file) is a *software* execution-token
system gating AI **worker** authority — which queues a worker's signed
policy lets it enqueue into. What Dave is describing here is different:
a **human presence gate on the approval action itself** — proof Dave is
physically at the console right now, not just that a session is logged in.
Motivating scenario, his own words: "preventing console interference, e.g.
I just went to the bathroom and..." — an approval left mid-flow (e.g. a
queued `flake_mutation`/`human_approval` row, or a live terminal session)
shouldn't be executable by anyone/anything else just because the session
is unlocked and unattended.

Bootstrap-to-hardware path discussed, cheapest-first:
1. **Trivial bootstrap**: a long/random password on a QR code physically
   hidden nearby (Dave: "even a horrendous password on a qrcode stuck under
   a shelf would get us started") — zero build cost, physical-possession
   factor, gets the *concept* proven before any hardware exists.
2. **Off-the-shelf FIDO2 hardware key** (YubiKey-class — SoloKey, Nitrokey,
   Google Titan) — dedicated secure element, phishing-resistant, easy to
   carry.
3. **Wearable** (watch/ring with its own FIDO2 secure element, not just a
   phone-relay UI — checked explicitly for holding its own private key,
   not merely relaying the phone's passkey) — always-on-body, closest to
   true continuous presence-proof.
4. **Custom hardware** (ESP32/nRF52 running Solo-derived FIDO2 firmware, or
   a simpler custom signing scheme) — full control, plugs directly into
   whatever payload-signing shape PP-CLASSIFIER-001's `approval` field
   settles on; but homegrown firmware is now security-critical code needing
   real scrutiny, not something to wire up casually.

**Relationship to PP-CLASSIFIER-001/PP-APPROVAL-001:** this is a candidate
`enforcement`/`approval` behavior for those PPs' schema — a presence-lock
check could gate `mark-executed`-class actions specifically (the moment
where a human claims to be actually doing the real push/switch), not every
approval type. **Status: idea captured, no design pass, no todo opened.**
Sequence after PP-CLASSIFIER-001's schema settles, since the presence-lock
is a consumer of that schema, not a prerequisite for it.

### Technical deep-dive: the `human_approval` schema, unfolded 2026-07-22

Read `flake_gate.py` in full to generalize from the actual working
example, not a re-imagined version of it. Two design gaps this closes:
the row shape, and — genuinely new, not in the original write-up — the
**execution-mode ambiguity** the "Status" note above left open.

**The execution-mode gap, named explicitly:** `flake_gate.py` never
executes anything — `mark_executed()` only *records* that a human ran
the real `git push`/`nixos-rebuild switch` themselves, outside the tool
entirely. But PP-APPROVAL-001's own second known entry, `dependency_resubmit`
(#1627), is a different shape — re-enqueueing a dead-lettered job chain is
a safe, already-scoped action the approval step itself CAN perform once a
human says yes, unlike a real `nixos-rebuild switch`. Collapsing both into
one "approve → ???" behavior would either force a real auto-execute
capability onto flake mutations (defeats E17's whole purpose) or force a
manual do-it-yourself step onto job resubmission (needless friction for a
safe action). **The config needs an explicit `execution_mode` per
`approval_type`:**
- `record_only` — approving marks the row done; the real action happens
  outside the tool, by human hand, exactly like `flake_push`/`flake_switch`
  today. Reserved for genuinely irreversible/high-blast-radius actions.
- `auto_execute` — approving calls a named, already-scoped function
  directly (e.g. `state_machine.resubmit_dead_letter_job(job_id)` for
  `dependency_resubmit`). Reserved for actions that are safe once a human
  has said yes, where the friction of a manual outside-the-tool step buys
  nothing.

**`tgw-approval-handlers.json` shape:**
```json
{
  "flake_push": {
    "execution_mode": "record_only",
    "description": "git push on tgw-flake",
    "module": "tgw.flake_gate",
    "request_fn": "request_push",
    "mark_fn": "mark_executed"
  },
  "flake_switch": {
    "execution_mode": "record_only",
    "description": "nixos-rebuild switch",
    "module": "tgw.flake_gate",
    "request_fn": "request_switch",
    "mark_fn": "mark_executed"
  },
  "dependency_resubmit": {
    "execution_mode": "auto_execute",
    "description": "re-enqueue a dead-lettered job chain (PP-WORKFLOW-001)",
    "module": "tgw.queue.state_machine",
    "request_fn": "request_resubmit",
    "execute_fn": "resubmit_dead_letter_job"
  }
}
```
Same "config, never code" instinct already applied to `tgw-models.json`
(E8/E15) and `tgw-queue-priorities.json` — adding a new approvable action
is a JSON entry naming an existing function, never a new Python
`if approval_type == ...` branch.

**Generalized row shape — reuses `queue_jobs`, one new queue,
`entity_type='approval'`, no new table.** `flake_mutation`'s exact payload
shape (`kind`, `host`, `summary`) generalizes cleanly:
```python
payload = {
    "approval_type": "flake_push",       # key into tgw-approval-handlers.json
    "summary": "...",                     # what the human sees in the picker
    "risk": "medium",                     # operator-facing, not enforced by code
    "linked_pp": "PP-FLAKEGATE-001",
    "linked_todo": 1621,
    "type_payload": {"repo": "...", "host": "...", "commit": "..."},
}
```
`request_push`/`request_switch` become thin wrappers that build this
payload and call one generic `request_approval(approval_type, ...)` —
migrating the two existing entries onto the registry is a refactor of
`flake_gate.py`'s two request functions, not a rewrite of its careful
`mark_executed`/`audit` machinery, which stays exactly as-is (still
`record_only`, still never shells out).

**`tgw-clipd`/rofi picker wiring**: one new entry type, "Pending
Approvals" — lists `queued` rows from the approval queue (same query
shape `flake_gate.queue_table()` already has, generalized to all
`approval_type`s, not just flake). Selecting a row shows its `summary` +
`type_payload`; the picker's inline-approve action looks up
`execution_mode` and either calls `mark_fn` (record_only — same UX as
`tgw flake mark-executed` today, just from the picker) or `execute_fn`
directly (auto_execute — approve and it just happens, e.g. resubmit).
Matches the existing PP-OUTBOX-001 "typed entries, inline per-entry
mini-apps" surface — no new UI framework.

**Bounded packet**: migrate `flake_push`/`flake_switch` onto the registry
first (zero behavior change, proves the generic mechanism against the
one case that's already live and trusted) before adding
`dependency_resubmit` as the first genuinely new `auto_execute` case.
**Acceptance**: `tgw flake request-push`/`request-switch`/`mark-executed`
all behave identically post-migration (regression, not new capability);
`tgw flake audit` still finds the same findings against the same commit
history. **No-go**: any change to E17's core guarantee — `record_only`
approval types must NEVER gain an auto-execute path by accident, verify
by code review that `mark_fn` never becomes a real subprocess call for
`flake_push`/`flake_switch` specifically.

## PP-CLASSIFIER-001 — the unified action classifier, proposal only, NEW 2026-07-21

**Origin: consolidation, not a new need.** Same 2026-07-21 planning session,
surfaced while scoping PP-APPROVAL-001's approval-type routing — noticed
TGW already has several independently-written mechanisms that each answer
some flavor of "what kind of action is this, and what's allowed to happen to
it," built one at a time as each need arose rather than as one tool. Dave:
"I like single tools... this is the perfect opportunity to get what we want
not what we settle for."

**The six types identified so far** (Dave, 2026-07-21: "the design we just
conceived already has 6 types" — confirmed against this exact list):
1. `flake-guard.py` — PreToolUse, flake-scope enforcement (E10/PP-AGENT-DISCIPLINE-001)
2. `app-code-guard.py` — PreToolUse, `src/`/`tests/` write routing to `tgw-coder` (E12)
3. `worktree-guard.py` — worktree isolation enforcement (E11)
4. `trace-immutability-guard.py` — no-touch on agent-trace evidence (E14)
5. Approval-type routing — `flake_push`/`flake_switch`, `dependency_resubmit`,
   and future `approval_type` entries (PP-APPROVAL-001, above)
6. Claude Code's own built-in auto-mode approval classifier (harness-native,
   not TGW-owned — can be fed decisions but not replaced)

**Design principle: config-driven, same pattern as `tgw-models.json`/E8/E15
and PP-APPROVAL-001's handler registry.** One classifier, one registry of
typed entries — each entry names the action class, the scope/permission
rule that applies, and (where relevant) the approval-routing behavior.
Adding a new guarded action becomes a config entry, not a new bespoke hook
script. **Explicitly a living registry, not a closed list** — Dave: this
"will likely be added to as we go." The six above are the seed set, not the
final shape; every future guard need (new agent role, new specialist type
from PP-ORCHESTRATOR-001, new approval type) is expected to register here
rather than spawning a seventh standalone script.

**Relationship to PP-CATIONIX-001:** this is the concrete design for the
"permission architecture (scoped agent authority, escalation triggers)"
CLAUDE.md already names as PP-CATIONIX-001's still-unbuilt "crypto-lock
endgame." PP-CLASSIFIER-001 is that mechanism, given its own heading for
easy reference — not a competing or separate initiative.

**Schema decided, 2026-07-21:** `tgw-classifier.json`, same config family as
`tgw-models.json`/`tgw-queue-priorities.json` — a `types` map, each entry:
`type` (name), `match` (tool/pattern/queue_name/agent_type — the existing
hook's matcher, ported verbatim), `scope_rule` (who/what is allowed),
`enforcement` (`deny` hard-block / `ask` escalate / `log` detective-only,
matching each mechanism's current posture), and an optional `approval` link
into PP-APPROVAL-001's handler registry for gates that should become a
durable `human_approval` row instead of a synchronous prompt. One shared
classifier module + one PreToolUse entrypoint; existing hook scripts become
thin wrappers around it, not redesigns.

**Migration order: least safety-critical first.** Phase 1 (todo #1628):
migrate `flake-guard.py` only — lowest stakes, proves the pattern.
Deliberately does NOT touch `worktree-guard.py`/`app-code-guard.py`/
`trace-immutability-guard.py` (E11/E12/E14) in this phase — those are
load-bearing safety mechanisms and migrate only once Phase 1 is proven
clean, same prove-small-then-trust discipline as the rest of this week's
plan.

**Status: schema + Phase 1 sequencing decided, todo #1628 opened, nothing
built yet.** Full four-hook consolidation is a later phase, not started.

### Technical deep-dive: `tgw-classifier.json` drafted, unfolded 2026-07-22

Read all four live hook scripts (`.claude/hooks/{flake,app-code,worktree,
trace-immutability}-guard.py`) before drafting — grounds `enforcement`
values in what each script actually does today, not a guess:

```json
{
  "types": [
    {
      "type": "flake_mutation_command",
      "match": {"tool": ["Bash"], "pattern": "nixos-rebuild\\s+(switch|test)\\b|tgw-flake.*git\\s+(commit|push)"},
      "scope_rule": "nix-flake-maintainer only, via tgw flake request-push/request-switch",
      "enforcement": "ask",
      "approval": "flake_push"
    },
    {
      "type": "app_code_write",
      "match": {"tool": ["Edit", "Write"], "path_prefix": ["src/tgw/", "tests/"]},
      "scope_rule": "agent_type == 'tgw-coder' only",
      "enforcement": "ask"
    },
    {
      "type": "worktree_isolation",
      "match": {"tool": ["Edit", "Write"], "agent_type": ["tgw-coder"]},
      "scope_rule": "must be operating inside its own worktree, not the shared checkout",
      "enforcement": "ask"
    },
    {
      "type": "agent_trace_mutation",
      "match": {"tool": ["Bash", "Edit", "Write"], "path_prefix": ["/opt/TGW/var/agent-traces/"]},
      "scope_rule": "no agent, ever — write-once/append-only (E14)",
      "enforcement": "deny"
    },
    {
      "type": "approval_action",
      "match": {"source": "PP-APPROVAL-001 registry"},
      "scope_rule": "routes to tgw-approval-handlers.json by approval_type",
      "enforcement": "ask",
      "approval": "<dynamic, from the handler registry itself>"
    }
  ]
}
```
Type 6 (Claude Code's own built-in auto-mode classifier) is deliberately
**not** an entry — it's harness-native, outside TGW's own config surface,
confirmed as "can be fed decisions but not replaced" in the original
write-up. Nothing to draft for it.

**Shared classifier module interface** (`src/tgw/classifier.py`, not
built): one function, `classify(payload: dict) -> ClassifyResult`, where
`payload` is the raw PreToolUse hook payload (same shape all four
existing scripts already parse) and `ClassifyResult` carries
`{type, enforcement, reason, approval_type}`. Each existing hook script
becomes:
```python
result = classify(payload)
if result.enforcement in ("ask", "deny"):
    emit_hook_response(result.enforcement, result.reason)
```
— a 5-line wrapper replacing each script's current 60-100 lines of
inline pattern matching, with the actual matching logic centralized once
in `classifier.py` reading `tgw-classifier.json`.

**Bounded Phase 1 packet (todo #1628, already opened): migrate
`flake-guard.py` only.** Acceptance: identical `ask`/allow decisions
for the same set of test commands (`nixos-rebuild switch`, a `tgw-flake`
git push, an unrelated Bash command) before and after migration —
pure refactor, zero behavior change. **No-go**: any decision flip for
an existing matched pattern; any new pattern accidentally matched that
wasn't before. `worktree-guard.py`/`app-code-guard.py`/
`trace-immutability-guard.py` stay untouched in this phase — they're
load-bearing safety mechanisms (E11/E12/E14), migrate only once Phase 1
is proven clean over real use, not on a schedule.

## Session protocol

Start: thermal → inbox → SESSION-BRIEF/this plan → `tgw plan check` + `tgw ops-digest`
→ register todo + INPROGRESS breadcrumb. End: `/tgw-exit`. One outcome per session,
stated as an observation up front. Triage (digest) and building are separate sessions.
