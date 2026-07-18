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
that may never be restated as compliant. Full text:
`inbox/claude/TIGWA-NOTE-PP-HR-001-agent-contract-acceptance-suite-2026-07-16.md`.
Design ownership stays with Tigwa/Dave per the existing PP-HR-001 delegation
— recorded here for continuity, not adopted as a Claude action item.

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
test counts: `pp/PP-LISTEDITOR-001.md`.


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
Top operator risk historically: nothing running, work ledger not re-derivable.
**2026-07-10 alarm + durable fix, applied and reboot-verified 2026-07-12**: undeclared
`sdc` mounts (db-backup/itemdata-snap/itemarchive) caused silent dump failures after a
reboot; now declared in the flake with `RequiresMountsFor` so a missing mount is a
loud failure, not silent. Remaining open: `tgw-cloud-sync` rclone rate-limiting (todo
#1264) — first full GDrive sync hit a 403 rate limit, needs pacing/chunking, not a
bare retry. Full incident + fix detail: `pp/PP-BACKUP-001.md`; DR plan:
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
practical impact today. Full findings list + execution history: `pp/PP-COHESION-001.md`.


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
`pp/PP-KNOWLEDGE-001.md`.


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
cross-verification (2026-07-16) confirmed both real; one gap reconfirmed still open
(flake-guard covers `Bash` only, not raw `Edit`/`Write`). Two open follow-ups: #1449
(extend flake-guard's matcher), #1450 (evaluate `settings.worktree.bgIsolation` as a
`tgw-coder` isolation replacement). Full detail: `pp/PP-AGENT-DISCIPLINE-001.md`.


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

**Sequencing decided, 2026-07-16 (Dave): "I believe postgresql is the right
call but this is not the right time yet."** Resolves the premise conflict
with [[PP-CATALOG-INCR-001]] (which assumes JSON stays truth) — that PP is
correct for the CURRENT phase, not a competing design needing reconciliation.
Order: finish the pipeline logic fixes (R1), build the UI out, and only take
on the backend inversion later — "unless it becomes too painful" to keep
deferring. JSON stays the live source of truth until that trigger; this PP
stays PROPOSAL/design-only until then, not started.

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
the planner/stitcher convergence idea: full detail in `pp/PP-CODEGRAPH-001.md`.


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
review). Full detail: `pp/PP-NIXOS-001.md`; plan: `PLAN-nixos-migration.md`,
`nix/CLAUDE-NIX.md`.


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
`reference/TGW-a1131-CLI-Wrapper.md`. Next session should start by verifying the
basic launch/connect path before touching Phase A/B/C. Full detail:
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
`pp/PP-ROUTER-001.md`.


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
detail: `pp/PP-MARKETING-001.md`.


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

**#1077 (eBay Dev Support ticket, undeletable item) — status only, 2026-07-16
(Dave): still waiting.** Bad-sign development: the support rep who hung up
on Dave mid-call (yelling that the ticket was hurting his numbers) has
since been promoted into eBay's business-division decision leadership. No
action available on TGW's side — external, waiting on eBay.

Snapshot baseline completed (19,486 SKUs) — unblocks #1131 Motors census; drift detection baseline set.
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
of superseded/misc-completed PPs and todos: `pp/PP-PLANDB-001.md`.

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

**Misc. completed todos** (full one-line-each list: `pp/PP-PLANDB-001.md`): #1053,
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
accurate tooltip pending that decision. Full detail: `pp/PP-BULKLIST-001.md`.


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

## PP-OPERATOR-QUEUES-001 — saved review-lens queues, browse-page chips
**Todo #1466, reviewed + closed 2026-07-16.** Tigwa built this same-day from a
3-sentence prompt. Code review: APPROVE-WITH-NITS (no SQL injection surface, AI-draft
gate real, durable writes; 3 low-severity nits). UI review: SHIP-INTERNAL-SLICE, not
operator-complete (queue chips visually identical to status chips; AI-drafted queues
have no discover/create/edit UI yet — matches stated scope). Full review detail:
`pp/PP-OPERATOR-QUEUES-001.md`.


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


## Session protocol

Start: thermal → inbox → SESSION-BRIEF/this plan → `tgw plan check` + `tgw ops-digest`
→ register todo + INPROGRESS breadcrumb. End: `/tgw-exit`. One outcome per session,
stated as an observation up front. Triage (digest) and building are separate sessions.
