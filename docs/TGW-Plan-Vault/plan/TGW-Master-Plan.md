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

### R3 — Plan and process hygiene

Done: this redraw; work-packet protocol below; PRIME DIRECTIVES in CLAUDE.md; Data
Charter; rolling handoff rewrite. Open: keep handoff ≤150 lines; vault conflict-file
cleanup (15 files); drain `inbox/queued/`; tracker prune with Dave (open todos >30d
untouched → packet, FUTURE-IDEAS, or delete); fix `tgw restart-workers` (references
nonexistent `ebay_dole`); CLAUDE.md `tgw todo --add` syntax fix.

---

P1 upload integrity fix complete — see document for spec, implementation, tests, and live verification.

P9 bulk audit: Inventory API getInventoryItems winner (~98 calls), Feed API blocked, GetMyeBaySelling narrower than assumed. Full ranking in dev-workflow/research/RESEARCH-photosync-bulk-audit.md. Follow-up #1127 filed.

P2 ops-digest pending-liability lines shipped (see DONE-photosync-p2-digest-liability.md in dev-workflow/research).

- P9 follow-up #127: `photos_short_on_ebay` re-pointed at live capture index; open question on recurring nightly capture flagged for 2pm triage.
## Work-packet protocol

One packet = one todo = one model session. Non-trivial packets get
`plan/packets/<todo-id>-<slug>.md`:

```

Fixed tgw-restore.sh bug, wrote TGW-VAULT-RESTORE.md (both restore paths, live-verified dry-run).
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

## PP-QUOTA-001 — metered-API budget layer ✅ (built s42)
`tgw.quota`, raw capture, ops digest, health check live. Remaining observability
packets tracked under R2, not here.

## PP-BACKUP-001 — backup + DR
**Top operator risk: nothing running; work ledger not re-derivable.** Scripts+timers
exist in `etc/systemd/`. Operator todos #61/#146/#147; restore script #1052; DR
drills #1050/#1051. Plan: `PLAN-backup-dr.md`.

## Drive-space re-evaluation (flagged 2026-07-04, todo #1136)
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

## PP-NIXOS-001 — NixOS migration (CatioNIX)
Canonical flake `~/tgw-flake` working; main-repo merge + workflow rules pending; a1131
no-GitHub-access (todo #1082); no process supervision for agent processes (design
requirement). FROZEN except stability fixes. Plan: `PLAN-nixos-migration.md`,
`nix/CLAUDE-NIX.md`.

**todo #1049 split (2026-07-04):** `--print-url` flag on the Python `tgw get-ebay-token`
CLI was **already fully implemented** (found while checking, not built new) — live-
verified, real auth URL generated, zero eBay calls. DONE, 5 new tests. The other half
(upgrading the `tgw` fish wrapper in `nix/tgw/home.nix` to call `xdg-open` automatically)
is a flake change under the freeze — left untouched, deferred to whenever PP-NIXOS-001
thaws or Dave wants a targeted exception.

## PP-PHOTO-001 — photo pipeline (GDrive → Gemini / eBay)
Sync infra live. Phase A (GDrive→Gemini multimodal draft) #1064; Phase B
(zero-bandwidth EPS upload) #1065. FROZEN until R1 drains.

## PP-CLIP-001 — clipboard manager
Phase 1 done; crash loop fixed s41. #1086 conceptual pass (unify with PP-EVENTD-001)
BLOCKS #1055 rofi picker. FROZEN. Design: `pp/PP-CLIP-001.md`, `pp/PP-EVENTD-001.md`.

PP-CLIP-001 conceptual pass: identified duplication with PP-EVENTD-001; recommended split (tgw-clipd local-only, cross-machine sync to EVENTD). Full analysis filed as CLIPBOARD-CONCEPT-PLANNING-1086.md.
## PP-CATPICK-001 — smart category picker
**Phase 1 DONE 2026-07-04** (#1079): `category_candidates` (id/name/full ancestor
path) backfilled onto all 25 `category-groups.json` groups from the on-disk eBay
category tree cache — zero live API calls. `scripts/catpick_backfill_candidates.py`
(dry-run default, `--apply` to write); 4 unit tests. 2 stale category IDs
(`manuals: 34210`, `tools_hand: 43994`) not found in the tree cache — kept as
bare-ID fallback rather than dropped, flagged for review. Phase 2 (the actual
group-shortlist-first picker UI/logic) remains FROZEN until R1 drains. Memory:
project-smart-category-picker.

## PP-REPRICER-001 — market-data repricer (the pricing rebuild)
Read-only foundation done. **Context (s42): automated pricing is DEFUSED** — schedule
minting disabled, reducer cliff-guarded, prices are operator-only until this project
delivers trustworthy data. Two candidate data sources, not mutually exclusive:
1. **eBay sold data** via `buy.marketplace_insights` — BLOCKED external: scope request
   in the eBay application review (#79, Dave answers DS questions).
2. **PP-PRICING-001 — Google Shopping comps via SerpApi (paid)** — the designed
   interim substitute for marketplace_insights, dropped from the s42 redraw index by
   mistake and restored at Dave's flag. Full Phase 1 design (title-based Shopping
   SERP in ai_identify, `apis/lookup/shopping_search.py`, key at
   `secrets_root/serpapi-credentials.json`): `pp/PP-PRICING-001.md`. Cross-market
   active prices (Google Shopping: eBay/Amazon/Walmart) — a real floor signal, unlike
   same-marketplace Browse asking prices.
3. **Google-grounded price check** (Dave's 2026-06-09 suggestion, also dropped —
   "not accessible via API" is now stale: Gemini supports Search grounding as an API
   tool on our free-tier direct key). Zero-cost eval before paying for SerpApi.

Eval packet (#1109) — DONE 2026-07-04: ran grounded Gemini (gemini-2.5-flash +
Google Search grounding) against 10 real sold TGW items, scored vs the existing
free `BrowseCompsProvider` signal. **Result: Gemini grounding LOST** — 45.3%
mean abs error vs 30.4% for Browse comps; it kept finding plausible-but-wrong
comps for near-generic/vintage items. **Do not wire grounded Gemini as a
pricing signal.** SerpApi (Shopping SERP) still untested — blocked on #1110's
key. Full writeup: `docs/TGW-Plan-Vault/inbox/DONE-1109-repricer-eval.md`,
raw data `/opt/TGW/var/log/repricer-eval-1109.json`.

**New candidate, same day — Phase 0 comping interface** (research inbox,
`pp/PP-PRICING-001.md` Phase 0 section): the #1109 result directly validates
a Perplexity research thread's thesis — don't let a model invent prices,
build a supervised capture tool instead. Proposed: 3-pane web UI (item /
embedded eBay Product Research browser / structured comp+pricing capture),
`comp_snapshot` + `pricing_recommendation` schema, Marketplace Insights as a
later drop-in upgrade to the same schema. Design capture only, not started —
needs Dave's go/no-go. Related: PP-AGENTIC-PRICE-001 candidate-query design
composes with either.

**Same day, Phase -1 — self-powered comp engine (Dave request, todo #1134):**
the infrastructure (`OwnSalesProvider` + `velocity_stats` worker) already
exists and runs — this turned out to be a data-density problem, not a
missing feature. **Initial 71%-uncategorized figure was checking the
wrong field** (Magento `attribute_set`, not what the pricing engine
reads) — corrected via todo #1135: the real field (`ebay_category_id`)
is already populated on 52% of the catalog (28,710/55,419).

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

### Done (designs in `pp/` or archive; tracker holds history)

PP-EDITOR-001 (web UI, 31 todos) · PP-EBAY-MIRROR-001 (P1/P1.5/P2) · PP-MIGRATE-001 ✅
2026-06-20 · PP-PORTABLE-CATALOG-001 (P2 Flutter build) · PP-PLANDB-001 (plan/tracker
tooling) · PP-DEADLETTER-001 · PP-DOCFLOW-001 · PP-INTAKE-001 · PP-DATALEARN-001 ·
PP-MULTIMODEL-001 · PP-OFFER-001 · PP-OPS-001 · PP-PROMO-001 · PP-REF-002 ·
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
### Gated on R1 — named, designed later

## PP-BULKLIST-001 — bulk editing + listing surface (stub, Dave 2026-07-02)
The operator-gate design at volume: review MANY pending proposals in one sitting —
bulk-approve the ~99% that are right, pull exceptions into the single-item editor,
batch-publish approved items. **Hard gate: the single-item pipeline must be
operator-verified end-to-end first (R1.6/R1.7 pass)** — a bulk surface over a broken
pipeline bulk-applies the breakage. Design draws on the action-console principle
(state drives interface) and the 550 pending re-drafts as the first real workload.

### Frozen — parked, not cancelled (thaw only if it blocks an R1 packet)

PP-MC-001 (Midnight Commander UI) · PP-MCP-001 (MCP server — partial, tools live) ·
PP-EVENTD-001 (event server — pending #1086 concept pass) · PP-FULFILLMENT-001 ·
PP-TASKER-001 · PP-PERP-AUTO-001 · PP-EMAIL-001 · PP-CLAUDE-HELP-001 ·
PP-DERIVED-001 (design feeds Data Charter) · PP-DATA-OWN-001 (axiom absorbed into
charter; mirror work continues as R1.8 + mirror fields) · PP-UI-INTEGRITY-001 ·
PP-REVIEW-001 · PP-MACRO-001 (#15) · PP-DOCLIB-001 (#1044) ·
PP-STORAGE-001/PP-VISION-001 (GPU-gated) · PP-RESCUE-001 · PP-AGENTIC-PRICE-001 ·
PP-AIOPS-001 (see `PP-AIOPS-001-cat-herding-platform.md`) · LVM expansion (#1056) ·
PP-PRICING-001 (Google Shopping SERP comps — design `pp/PP-PRICING-001.md`, thaws with
the pricing rebuild, see PP-REPRICER-001) · PP-CANONICALIZE-001 · PP-CAPTURE-001 ·
PP-HINT-001 (revisit) · PP-IFDIR-001 · PP-REMOTE-001 · PP-REF-003 · PP-GIT-001.
Long-horizon concepts: `FUTURE-IDEAS.md` (planning sessions only).

*(Index completeness: restored 2026-07-02 after Dave caught PP-PRICING-001 missing —
the s42 redraw had dropped 27 PPs from the index; all archived designs remain
byte-complete in `archive/sections/` and promote to `pp/` on touch.)*

---

## Open discussion items (for 2pm 2026-07-04 planning session)

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

**PP-INTAKE-004 (proposed) — unified Kotlin intake app, and a much bigger platform
question behind it.** Full design: `docs/ai-plans/tgw-intake-app.md`. Supersedes
PP-INTAKE-002/003; the handheld camera/barcode/video app absorbs PP-TASKER-001's
functions and replaces the current Tasker-Scenes + AutoTools-WebScreens overlay.
Decided so far: own repo (Kotlin); early-`ai_identify` trigger = the ID call's own
batch size (`_MAX_PHOTOS_CLOUD`, 6) with a session-completion fallback for smaller
sets; custom turntable (not Foldio360) targeting ~12 photos/item, still being
sourced; dedicated turntable/collector devices get rooted deliberately (fixed-
purpose hardware), the handheld app itself should need no root at all (owns its
whole stack). **Bigger, unresolved:** Dave wants the handheld app built to real
product polish because "TGW" itself — trader-grims-warehouse, but running someone
else's inventory — may become a sellable platform. Three business models raised,
not chosen between: Dave hosts it multi-tenant; sell/license it for others to
self-host and customize; or TGW/Dave as the service provider on top of a possibly
open-sourced core. Any of these implies generalizing config/secrets/category-groups
beyond a single-operator deployment — flagged as its own future PP item, not decided
by the intake-app plan. Also flagged: `clip-route`'s ingest path needs a session/
capture-batch correlation ID independent of SKU, since capture can start before an
item record exists in ItemData.

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
