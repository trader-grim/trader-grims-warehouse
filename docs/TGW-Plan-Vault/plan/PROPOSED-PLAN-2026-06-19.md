# TGW Proposed Plan — 2026-06-19

**Author:** Session 35 (Claude Sonnet 4.6)
**Status:** PROPOSED — awaiting Dave's review and go signal
**Context:** Full replan following the June 2026 data blindness incident, photo disaster,
and shipping pricing crisis. See `AUDIT-2026-06-19.md` for the full incident analysis.

---

## The Problem We Are Solving

For roughly one month the platform operated without reliable eBay data locally, without
any audit trail for data changes, and without automated recovery from known failure modes.
When things went wrong — 619 photos renamed, shipping policies unknown, data regressions —
we discovered them by noticing symptoms, not by observing causes.

The underlying issue is architectural: the data platform was built and operated without
the foundation it needs to be trustworthy. We now build that foundation first.

---

## The Target Architecture

```
┌─────────────────────────────────────────────────────┐
│  OS Platform Layer                                  │
│  NixOS + Btrfs + NATS JetStream + nspawn runtime   │
│  Owns: isolation, auditability, recovery            │
└────────────────────┬────────────────────────────────┘
                     │ runs on
┌────────────────────▼────────────────────────────────┐
│  TGW Data Platform                                  │
│  PostgreSQL + workers + tgw-api + ItemData          │
│  Owns: business logic, eBay integration, catalog    │
└─────────────────────────────────────────────────────┘
```

AI agent tasks (Claude sessions, Aider) run in ephemeral nspawn containers provisioned
by the OS layer. They interact with the data platform through defined API surfaces only.
The OS layer decides whether to commit or discard what came out of each session.

---

## Sequencing Rationale

We cannot safely address the current data gaps (eBay data, shipping policies)
until we can see what is happening and recover from what goes wrong. The sequence is:

> foundation → visibility → data confidence → data recovery

Fixing the data before the foundation is solid risks creating new problems we cannot trace.

---

## Stage 0 — Immediate Operational Fixes
**Status:** Ready to execute now. No new development.
**Estimated time:** 1 session.

These are broken things that have a known fix requiring no architecture work.

| Item | Fix |
|------|-----|
| Ghost `tgw-worker@http.service` crash-loop | `systemctl disable --now && mask` |
| 50 `ebay_upload` dead-letters (Jun 17 outage) | Requeue — outage cleared |
| `task/aider-20260616145314` stale branch | Diff vs main, merge or abandon |
| PP-BACKUP-001 Phase A operator todos (#61) | Install db-backup, cloud-sync, secrets-backup timers |

These unblock clear queue state before the platform build begins.

---

## Stage 1 — API Fence: Asset Management
**Depends on:** Stage 0 complete.
**PP items:** New scope addition to PP-DATA-OWN-001 or standalone.
**Estimated size:** S (1 session).

The ISS-013 photo disaster happened because workers touched `ItemData/<SKU>/<SKU>.jpg`
directly — no API gate, no validation, no log. Before the transactional logging layer
is wired in, we close the hole it would expose.

**What gets built:**

- `tgw-api.py` asset management endpoints:
  - `GET /api/item/<sku>/assets` — list all media files for a SKU
  - `POST /api/item/<sku>/asset` — write/replace a named asset (validates filename convention)
  - `DELETE /api/item/<sku>/asset/<name>` — remove an asset
  - `POST /api/item/<sku>/asset/reorder` — set photo sequence order
- Workers that touch photos (`alt_text`, `thumbnail_gen`, `ebay_repush`) are updated to
  call these endpoints instead of writing to disk directly
- Filename convention validation — legal asset names the API will accept:
  - `<SKU>.jpg` — primary photo
  - `<SKU>-alt.jpg` — alt-text companion (companion filename derived from source name, never from caller)
  - `<SKU>-thumb.jpg` — thumbnail
  - `<original>-alt.jpg` — companion keyed to original photo name (for non-SKU-named originals)
  - `<sku>-foldioNN.jpg` — Foldio multi-shot photos (NN = 2-digit zero-padded sequence, e.g. 01–28)
  - Any `<original>.jpg` in `tgwYYYYMMDD_HHMMSS`, `a11bYYYYMMDD_HHMMSS`, `IMG_YYYYMMDD_HHMMSS` formats
  - The API rejects everything else, including bare numeric names (`1.jpg`, `2.jpg`)
- **Foldio intake rule:** Photos arriving with bare numeric names (`1.jpg`, `2.jpg`, ...) are
  renamed at ingest to `<sku>-foldio01.jpg`, `<sku>-foldio02.jpg`, ... in sort order.
  232 existing items carry the old numeric names; these will be renamed in a deferred
  cleanup pass once the transactional base (Stage 2) is in place.

**What this unblocks:** Every asset write that follows is now visible, validatable, and
ready to be logged by Stage 2.

---

## Stage 2 — Automated Base Platform: PP-AIOPS-001 Phases 1–4
**Depends on:** Stage 1 complete (asset fence closes the hole before we start logging).
**PP items:** PP-AIOPS-001.
**Estimated size:** L–XL (4–6 sessions across sub-phases).
**Platform:** MX Linux today. No PP-NIXOS-001 dependency.

Full technical spec: `docs/TGW-Plan-Vault/plan/PP-AIOPS-001-cat-herding-platform.md`.

### Phase 1 — JetStream Install + ItemData Audit Stream (M, 1–2 sessions)

Install NATS JetStream (single-node, native binary). Wire `items._write_field()` to
publish every ItemData mutation to the `ITEMDATA_MUTATIONS` stream. Wire the asset
management endpoints (Stage 1) to publish there too.

Every data change from this point forward has a timestamped record: what changed, who
changed it, when. The data blindness problem is structurally solved.

**Stream subjects:**
- `itemdata.{sku}.{field}` — field-level mutations
- `itemdata.{sku}.asset.{name}` — asset writes/deletes/reorders

### Phase 2 — Queue Transition Outbox (S, 1 session)

Wire `QueueWorker` to publish every job state transition to the `QUEUE_TRANSITIONS` stream.
Add `session_id` so operator CLI commands, Claude sessions, and Aider tasks are all
individually attributable in the log.

The queue state machine is now fully observable without polling PostgreSQL.

### Phase 3 — Anomaly Detection Worker (M, 1–2 sessions)

New worker subscribes to both streams and applies a rule library. Known bad patterns
(price set to zero, primary photo renamed, dead-letter spike, status regressed) are
detected within seconds, not discovered hours later by operator inspection.

Critical anomalies fire desktop notifications immediately.

### Phase 4 — Litterbox Worker + MCP Audit Tools (L, 2–3 sessions)

Deploy the three existing undeployed workers:
- `itemdata_scrub.py` — field cleaning/normalization
- `photo_history_recovery.py` — repairs ISS-013-class photo rename damage
- New `litterbox.py` — subscribes to anomaly events, applies auto-fix library

Auto-fix library handles: 503/rate-limit requeue, photo rename repair, negative qty,
LEASE_EXPIRED requeue, stale template prefix. Known messes are fixed without operator
intervention.

Extend `tgw-mcp-server` with audit tools: `tgw_audit_trail`, `tgw_session_diff`,
`tgw_anomaly_log`, `tgw_litterbox_log`, `tgw_mutation_rate`.

One-time repair run: feed all 619 photo-rename victims through `photo_history_recovery`
using `ebay_live.inventory_item.product.imageUrls[0]` as the source.

**After Phase 4:** The platform observes itself, fixes known messes automatically,
and gives Claude/Aider complete visibility into what changed during any session.

---

## Stage 3 — Backup Automation: PP-BACKUP-001 Remaining Phases
**Depends on:** Stage 2 Phase 1 (JetStream running — backup events can be logged).
**PP items:** PP-BACKUP-001.
**Estimated size:** M (1–2 sessions).

PP-BACKUP-001 Phase A gave us hourly Btrfs snapshots of `/opt/TGW`. The remaining
phases complete the backup posture:

| Phase | What | Status |
|-------|------|--------|
| A | Hourly Btrfs snapshot (tgw-snapshot) | **DONE** |
| B | Database backup (PostgreSQL dump → encrypted archive) | Not done |
| C | Cloud sync (encrypted offsite copy of snapshots + dumps) | Not done |
| D | Secrets backup (separate encrypted channel) | Not done |
| E | Restore runbook validation (restore from scratch to verified working state) | Not done |

Phases B–D are the operator todos from Stage 0 (#61) that need the timer units installed.
Phase E is a dedicated validation session — actually running a restore — before we declare
backup posture solid.

---

## Stage 4 — NixOS Migration: PP-NIXOS-001
**Depends on:** Stages 1–3 complete (so the migration moves a known-good, observable system).
**PP items:** PP-NIXOS-001.
**Timing:** Dave indicated "very soon" as of 2026-06-19. Stages 1–3 should be short enough
to complete before or in parallel with NixOS preparation.

The NixOS migration is not blocked by this plan. If NixOS lands before Stages 1–3 are
complete, that is fine — those stages run the same on NixOS.

**What NixOS enables that MX Linux does not:**
- Declarative nspawn container definitions (reproducible, version-controlled sandbox)
- `pkgs.dockerTools` for GPU-ready Docker images from Nix (future GPU upgrade path)
- System-wide rollback via NixOS generations (Btrfs + NixOS = double safety net)
- `--ephemeral` on nspawn guaranteed to use Btrfs CoW (requires subvolume, not directory)

---

## Stage 5 — AI Session Isolation: PP-AIOPS-001 Phase 5
**Depends on:** PP-NIXOS-001 Phase 3 (NixOS running); Stage 2 Phase 4 (litterbox + audit).
**PP items:** PP-AIOPS-001 Phase 5.
**Estimated size:** L–XL (2–4 sessions + operator validation).

Each AI task (Claude session, Aider) gets a pre-task Btrfs CoW snapshot of `/opt/TGW/src`
and `/opt/TGW/data`, runs inside an ephemeral nspawn container with `--private-network`,
communicates results back to the host via FIFO pipe. The host supervisor reads the output,
validates the change scope, and either promotes the snapshot to live or discards it.

The host supervisor also monitors the container via cgroup v2 (memory.current, cpu.stat)
and kills runaway containers automatically, preserving the snapshot for forensic review.

After Phase 5: the "photo rename disaster" class of problem is structurally impossible.
A bad Aider session can be rolled back in one command. AI agent changes are staged and
validated before they touch production data.

---

## Stage 6 — Data Recovery and Ongoing Sync
**Depends on:** Stages 1–5 (especially the audit trail from Stage 2 and photo repair
from Stage 2 Phase 4; shipping policy data pull can start earlier).
**PP items:** PP-DATA-OWN-001 Phases 2–5.

With the foundation solid, address the current data gaps in order of impact:

| Priority | Item | PP item |
|----------|------|---------|
| 1 | Shipping policy pull (PP-DATA-OWN-001 Phase 4) — `sell.account` API → `data/ebay-policies.json` | PP-DATA-OWN-001 |
| 2 | Ongoing eBay sync (Phase 2) — `ebay_sync` writes `ebay_live` on a schedule | PP-DATA-OWN-001 |
| 3 | 460 Trading API legacy listings — get `ebay_live` data once `ebay_legacy_sync` rate limit resolves (ISS-015) | PP-DATA-OWN-001 |
| 4 | Transaction/sold history backfill (Phase 3) | PP-DATA-OWN-001 |
| 5 | Forward sync after listing edits (Phase 5) | PP-DATA-OWN-001 |
| 6 | PP-REPRICER-001 — blocked on `buy.marketplace_insights` scope (eBay DS 8 pending) | PP-REPRICER-001 |

The shipping policy pull (Priority 1) can actually start as soon as Stage 0 is done —
it is a read-only API pull with no write risk, and ISS-002 (wrong shipping profile) is
an active business problem. It does not need to wait for the full foundation.

---

## Parallel Data Track — Safe to Run During Stages 1–5

These three sub-tracks run concurrently with the foundation build. They are safe because
they are either additive (writing data that was absent), targeted repairs to known-broken
items, or reference data that does not touch ItemData at all. None of them require the
audit trail or sandbox to proceed safely.

### Data Track A — eBay Data Acquisition (PP-DATA-OWN-001)

Pull the full eBay dataset. Phase 1 ran 2026-06-17 and gave us 19,346 items with
`ebay_live`. The remaining phases can proceed now:

| Phase | What | Notes |
|-------|------|-------|
| 2 | Ongoing sync — schedule `ebay_sync` to write `ebay_live` on a cycle | Keeps data current after edits |
| 4 | Account policies mirror — `sell.account` API → `data/ebay-policies.json` | Fixes shipping gap; read-only pull |
| 3 | Transaction/sold history backfill | Lower urgency; no blocking dependency |
| 5 | Forward sync after listing edits | Ties into Phase 2 scheduling |

Phase 4 (policies mirror) is the most urgent — it resolves the shipping pricing crisis
(ISS-002) and unblocks knowing which fulfillment policy applies to each listing.
Start this in Stage 0 alongside the operational fixes. It is a read-only API call
writing to a new reference file with no risk to existing ItemData.

Writes to `ebay_live` are additive — they fill in absent data, they do not mutate
fields that workers or the operator have deliberately set.

### Data Track B — Photo Recovery (ISS-013) ✅ CLOSED 2026-06-19

**Root cause:** `alt_text.py` was using `rename` instead of `copy` (pre-commit `9319e5e`).
The worker renamed `<original-photo>.jpg` → `<sku>-alt.jpg` (wrong name, destructive).
Fix landed 2026-06-15; no new damage can occur.

**Recovery approach (revised):** The correct fix was a rename-only operation — the original
photos were still present in each item directory. The misnamed `<sku>-alt.jpg` was renamed
to `<original-photo>-alt.jpg` to correctly pair with the existing primary. No files were
created. `scripts/photo_repair_iss013.py` executed 2026-06-19.

**Result:** 618 items repaired (1 already fixed manually by Dave before the script ran).
Zero errors. All naming formats handled: `tgwYYYYMMDD_HHMMSS`, `a11bYYYYMMDD_HHMMSS`,
`IMG_YYYYMMDD_HHMMSS`, `cropped-*`, and numeric (`1.jpg`→`1-alt.jpg`).

**Remaining:** Originals are present alongside their `-alt.jpg` companions. The archive
sweep (moving originals to history once companions are confirmed) is deferred until the
transactional base (Stage 2) is in place so each archive move is logged.

### Data Track C — Reference and Relationship Data

Build the reference data that powers the decision engine and processes without
touching any ItemData directly. This data lives in config files, reference tables,
and the catalog — not in per-item JSON.

Much of what Dave has learned about eBay quirks, category constraints, and listing
behavior has come from investigating metadata. This track captures that knowledge
systematically so it is not locked in operator memory.

#### C1 — Shipping policies
Pull all fulfillment/payment/return policies from `sell.account` API.
Store in `data/ebay-policies.json` with full policy detail.
Fixes ISS-002 (wrong shipping profile); powers shipping cost calculation and repricer accuracy.

#### C2 — eBay category data (full pull for all categories in use)

This is the highest-value reference dataset. Pull once, store as structured JSON,
keep queryable. Covers four sub-areas:

**C2a — Category hierarchy (main + secondary + store categories)**
- Pull the full category tree for all primary categories appearing across our listings
- Pull secondary categories where assigned (sparse but present; need to be prepared)
- Pull eBay Store category structure — main store categories + all secondaries
  (secondaries are mostly empty today, but the structure must be captured so the
  decision engine knows what slots exist and can assign them as inventory grows)
- Source: Taxonomy API `getCategoryTree` + Trading API `GetCategories`;
  store categories via `sell.stores` or Trading API `GetStore`
- Store as `data/ebay-categories.json`

**C2b — Item aspects: full attribute set per category**
- For every eBay category ID in use, pull the complete aspect/attribute list:
  required aspects, recommended aspects, optional aspects, value constraints
  (free-text vs enum, max length, allowed values where applicable)
- Required aspects are a hard gate — eBay rejects listings missing them; knowing
  them in advance powers pre-flight validation in `ebay_draft`
- Source: Taxonomy API `getItemAspectsForCategory` per category ID;
  or Trading API `GetCategorySpecifics` (available under existing scopes)
- Store as `data/ebay-aspects-by-category.json`, keyed by category ID
- Cross-reference against `category-groups.json` so each TGW category group
  maps to its full eBay aspect requirement list

**C2c — EPS image URLs: map eBay-hosted images to local photos**
- Every listing that has gone live has images hosted by eBay Picture Services (EPS)
- The EPS URLs live in `ebay_live.product.imageUrls[]` from the Track A data pull
- Goal: correlate each EPS URL to the local photo it came from (by filename/order)
  so we know definitively which local asset is displayed for each listing
- This closes the photo→listing provenance gap and validates the ISS-013 repair:
  if an item's EPS primary image doesn't match our repaired local primary, that's
  a re-upload candidate
- Store correlation in `data/ebay-image-map.json` (SKU → [{eps_url, local_file, position}])
- No new API call needed — data already in `ebay_live`; this is a derivation pass
  over Track A data once Track A Phase 2 (ongoing sync) is running

**C2d — Full metadata capture (category data AND per-item eBay data)**
- The investigation-driven metadata principle applies everywhere, but *especially*
  to the per-item eBay data pulled in Track A. Every field eBay returns should be
  stored, not just the fields we currently use. Unknown fields are where the
  operational tidbits live — discovered by looking at what eBay actually sent back,
  not by reading the API docs.
- Track A item data: store the raw API response in full (or as a complete
  `ebay_raw` sub-object in `ebay_live`) — condition description text, item specifics
  as-listed, seller custom label, subtitle, secondary category, store category
  assignment, promoted listing status, compatibility data, regional availability,
  any fields present that we haven't mapped yet
- Category-level metadata: same principle — store the full Taxonomy/Trading API
  response per category, not just the fields we currently consume
- Correlation pass: for each item, diff `ebay_live` (what eBay has) against
  our `ItemData/<SKU>.json` (what we sent/know locally) — divergences are candidates
  for `CATEGORY-QUIRKS.md` entries and decision-engine rules
- Document surprises in `CATEGORY-QUIRKS.md` as they surface; each quirk discovered
  this way is operational knowledge that was previously locked in the operator's head
  or buried in a one-off investigation

#### C3 — Category group enrichment
Review `category-groups.json` (25 groups); fill in any missing `size_class`, floor
prices, `typical` prices, eBay category ID mappings. Drive from C2a/C2b findings —
the aspect requirements and category hierarchy inform which group assignments are right.
Powers template intake, pricing floors, the decision engine.

#### C4 — Location types
Define storage location types and their properties (size class capacity, access method).
Powers semi-chaotic storage assignment and pick-path optimization.

#### C5 — eBay error code index
Extend `reference/eBay-Error-Codes.md` with gaps found during dead-letter triage.
Powers litterbox classification rules.

---

None of this touches `ItemData/<SKU>/<SKU>.json`. It is pure reference data that
makes the decision engine smarter without risking existing item records.

---

## Updated Dependency Graph

```
Stage 0 (ops fixes) ──────────────────────────────────────────────────────────────►
  │                                                                                 │
  ├─► Data Track A Phase 4 (policies pull) ──► Data Track A Phase 2 (ongoing sync) │
  │                                                                                 │
  ├─► Data Track C (reference data) ───────────────────────────────────────────────►
  │
  └─► Stage 1 (asset fence)
        └─► Stage 2 (PP-AIOPS-001 Phases 1–4)
              │   ▲
              │   └── Data Track B (photo repair) ── can run after Track A coverage check
              │
              ├─► Stage 3 (PP-BACKUP-001 B–E)
              │
              └─► Stage 4 (PP-NIXOS-001)
                    └─► Stage 5 (Phase 5 sandbox)
                          └─► Stage 6 (remaining PP-DATA-OWN-001 Phases 3+5)
```

The data tracks and the foundation stages run side by side. The data tracks are
safe because they are either additive, targeted repairs, or reference-only.
The foundation stages are what make future data work safe by default.

---

## What This Plan Does Not Include

| Item | Why not now |
|------|-------------|
| PP-REPRICER-001 | Blocked on eBay scope approval; not in our control |
| PP-VISION-001 / PP-DERIVED-001 | Depend on clean data; address in Stage 6+ |
| PP-PORTABLE-CATALOG-001 | Unblocked but lower priority than foundation |
| PP-CLIP-001, PP-WM-001 | Convenience; not blocking |
| PP-PROMO-001 | Depends on stable listing data |
| GPU upgrade / PP-HARDWARE-001 | Phase 5 Docker path ready; hardware timing is operator call |

---

## Dependency Graph

```
Stage 0 (ops fixes)
  └─► Stage 1 (asset fence)
        └─► Stage 2 (PP-AIOPS-001 Phases 1–4)
              ├─► Stage 3 (PP-BACKUP-001 B–E)
              │     └─► Stage 4 (PP-NIXOS-001) ─► Stage 5 (Phase 5 sandbox)
              └─► Stage 6 (PP-DATA-OWN-001 Phases 2–5)

Shipping policy pull (PP-DATA-OWN-001 Phase 4) ──► can start after Stage 0
```

---

## What Success Looks Like

After Stages 1–5 are complete:

- Every data change (field write, asset write, queue transition) has a timestamped
  record with source attribution. Nothing is invisible.
- Known failure patterns (photo rename, negative qty, 503 dead-letters) are detected
  automatically and repaired without operator intervention.
- AI agent sessions are isolated: bad sessions are rolled back, not discovered later.
- Backups are automated, tested, and offsite. Restore has been validated.
- NixOS is running. The platform is declarative and reproducible.
- eBay data is current and syncing on a schedule.
- The 619 photo-rename victims are repaired.
- Shipping policies are known for every active listing.

At that point the data platform is trustworthy and the repricer, vision matching,
and catalog enrichment work can proceed on solid ground.
