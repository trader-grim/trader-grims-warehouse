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
store/retrieve. Draft-iteration cap resolved (10 min wall-clock OR 8 substantive
redrafts, whichever first, then paused-awaiting-Dave, never auto-resumed);
send authority reaffirmed Dave-only + new "I'm feeling lucky" one-click-send
button; v0-first and fixed target-agent list reaffirmed; stale-card handling
partially resolved (never auto-archive/delete/send, manual only — precise
surfacing policy still open); new: pinned/reusable prompts (template preserved,
each send instance logged separately); new: named gap between mailbox delivery
and *initial* prompting into an agent's active session (candidate: scoped
`tmux send-keys`, not yet authorized); new: clipboard-as-handoff direction
(copy-to-clipboard for Dave to paste, Dave-initiated only, not an ambient
channel). All still design-only, no implementation authorized. Full evaluation +
decisions: `pp/PP-OUTBOX-001.md`.

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
until credentials are in hand. Full writeup: `pp/PP-EBAY-ACCOUNT2-001.md`.

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
- **Built version, corrected from this section's original write-up:** the
  tgw CLI never executes the push/switch itself, under any command name. A
  human runs the real `git push`/`nixos-rebuild switch` themselves, by
  hand, then calls `tgw flake mark-executed <job-id>` purely to record that
  it happened (`queued -> succeeded`, no side effects). This is safer than
  an executing `tgw flake push/switch <id>` command would have been — that
  shape still leaves a callable code path an agent (or a future automated
  wrapper) could invoke; a record-only closing command has none. Deviation
  flagged and reconciled by the executing coder session (todo #1621
  result manifest) — this text originally described the executing shape;
  corrected here to match what was actually built and judged correct.
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

**First live test case:** re-adding far2l to a1131 through this new path,
end to end, verified live (`far2l --version` on a1131 post-switch) — not
yet run as of this writeup.

Todo #1621 — **built, live-verified against the real `state_machine`
Postgres DB, cleared for stitch** (`packets/results/1621-flakegate-RESULT.md`).
Not yet merged to `catio-nix-0.0.1-alpha`.

## Session protocol

Start: thermal → inbox → SESSION-BRIEF/this plan → `tgw plan check` + `tgw ops-digest`
→ register todo + INPROGRESS breadcrumb. End: `/tgw-exit`. One outcome per session,
stated as an observation up front. Triage (digest) and building are separate sessions.
