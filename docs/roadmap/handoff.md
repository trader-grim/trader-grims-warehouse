# TGW Build-Phase → Operating-Phase Handoff

**Status:** v2, 2026-06-11. Supersedes v1 (2026-06-10) after a full cross-doc consistency
review of `docs/architecture/*`, `docs/plans/*`, `docs/invariants.md`, `docs/runbooks/*`,
`docs/dev-workflow/*`, the master plan, ISSUES.md, and the live todo queue. v1's todo table
and test counts were already stale (sessions 21–23 landed after it was written); §1.1 below
records what changed.

**Ground rule that governs everything below:** the todo tracker (`tgw todo claude`) is the
canonical task queue; the master plan is the reference spec; this document is a snapshot —
when it disagrees with the tracker, the tracker wins.

**Numbering pitfall (caused real drift in v1):** todo-tracker IDs and master-plan Round-table
row numbers are *different sequences* (tracker #47 = plan row #46, etc.). This document uses
**plan Round-5 row numbers** for plan-table items and says "todo #N" only for live tracker IDs.

---

## 1. What has been completed

### The core platform (Phases 1–4 + Rounds 1–4)

- **Platform layer ("the fence")** — installable `tgw` package; all ItemData access through
  `items.py`/`resolver.py`; `{ok, ...}` output contract on every CLI/API call; ~100 CLI
  subcommands; `tgw health` with Postgres/catalog/thumbnail/ownership checks.
- **Queue system** — PostgreSQL `state_machine` ledger, lease-based claiming
  (`SKIP LOCKED`), dedupe keys, transient-vs-dead-letter classification, idempotent
  handlers, 18 worker queues under systemd template units (`tgw-worker@<queue>`).
- **The full listing pipeline, end to end and live:**
  photo intake → `ai_identify` (vision + 8-source barcode lookup) → `ebay_draft` →
  `ebay_upload` (EPS) → `ebay_price` (launch = 110% of max → .99, p25 target) →
  `ebay_stage` (UNPUBLISHED offer) → operator `tgw staged` review → `tgw publish` → live →
  `ebay_price_reducer` markdown (p75 day 3 → p25 day 17) → `ebay_sync`/`ebay_legacy_sync`
  sold detection → `velocity_stats` nightly feedback into pricing.
- **Hard-won eBay correctness encoded:** Content-Language header, full-replace offer PUT
  rule, condition fallback on 25021, active-listing guard, locked OAuth scopes,
  `availabilityDistributions`+`merchantLocationKey` for 25002 (ISS-001 ✅ closed 2026-06-11).
- **Surfaces:** tgw-http (FastAPI :7373) with tablet web forms and bulk edit, `GET
  /api/health`, Flutter app scaffold, MC extfs (`tgwitem`, `tgwlogs`), MCP server (10
  tools), version-controlled shell layer.
- **Operations tooling:** `tgw dead-letter` triage/requeue, `tgw restart-workers`,
  `v_dead_letters`/`v_job_history` SQL views + `tgw queue-history`, notify subsystem,
  permissions audit, bash completion.

### The documentation and safety layer (sessions 17–20)

- `docs/architecture/overview.md` + `services.md` — verified architecture reference.
- `docs/invariants.md` — all five gaps from the review (A5, B4, C3–C5) fixed 2026-06-10
  with regression tests; 7 dedicated invariant test files.
- `docs/runbooks/` — 8 incident runbooks + triage INDEX.
- `docs/dev-workflow/` — AI/operator collaboration process (tool routing, session ritual,
  Aider/Antigravity tiers). **Still untracked — needs Dave's review and a commit** (so is
  `docs/roadmap/`).
- `docs/plans/PLAN-nixos-migration.md` — full PP-NIXOS-001 plan (**DRAFT, still awaiting
  Dave's approval**; ADR at repo-root `ADR/ADR-nixos-migration.md`).

### 1.1 Landed since handoff v1 (sessions 21–23, 2026-06-10 → 06-11)

Everything v1 listed as "open todos" is done or resolved:

- ✅ size_class backfill (`tgw data-scrub --pass 2`) — plan row 36
- ✅ `GET /api/health` Bearer endpoint (Flutter unblocked) — plan row 37
- ✅ `tgw alt-text <sku>` + unified LLM dispatcher (Ollama/OpenRouter/Gemini routing) — row 38
- ✅ 25002 Item.Country — **resolved, not coded around**: ISS-001 closed; the session-23
  dead-letter lookalikes were item-specifics validation errors on an already-live item;
  all 15 stale dead-letters cleared — row 39
- ✅ Ledger SQL views + `tgw queue-history` — row 46 (tracker #47)
- ✅ PP-SHELL-001 canonical command renames + `tgw search` — row 47 (tracker #48)
- ✅ PP-CONTEXT-001 `tgw set/get/clear-context` (replaces `tgwset`/CurrentItem symlinks) —
  row 48 (tracker #49)
- Also: eBay client Content-Language centralization; Tailscale installed; MasterArchive
  drive repaired (`tgw history-index` unblocked); markmap-cli installed; Antigravity CLI
  configured + v2.0 installed.
- **Test suite: 475 passing** (v1 said 346), ruff clean.

## 2. What remains unfinished

### Claude-ready work (NOT currently in the tracker — seed first, see §6 step 3)

`tgw todo claude` is **empty**, but master-plan Round 5 rows 40–45 were never seeded as
todos and fell out of every tracking surface — the exact failure the
todo-tracker-is-canonical rule exists to prevent:

| Row | Size | Task |
|-----|------|------|
| 40 | XS | category-groups.json pricing calibration (GEMINI-005: electrical_fixtures→12.50, media_records→13.50, collectibles_pins_buttons→10.50) + `--reseed` |
| 41 | XS | category-groups.json store_category mappings (GEMINI-006: 4 groups) |
| 45 | XS | TGW-Quickstart.md pipe examples (`--skus-only`, stdin `-`, multi-SKU) |
| 44 | S | `GET /form/suggest` punctuation-safe suggestion web form |
| 43 | S | Standard Envelope ≤0.25 in constraint in `_resolve_fulfillment_id()` + CATEGORY-QUIRKS note (touches the fulfillment resolver — review-flagged) |
| 42 | S | `description_history` boilerplate-contamination scrub ("John F. Rider", GEMINI-004) — bulk ItemData mutation: dry-run first |

Newly unblocked, sized but unscheduled: **`tgw history-index`** (MasterArchive repaired
2026-06-11; design sketch in GEMINI-007) and **PP-PYIPC-001 implementation** (research
complete; Syncthing live with API key).

### Open operator todos (live tracker, 2026-06-11)

#7 IGDB credentials · #11 `tgw ebay-sweep` physical review · #12 fix 9 wrong-shipping
listings (ISS-002) · #15 macroboard keyboard · #16 webhook infra (nginx/cloudflared —
**gated on ISS-005 dev_id signature verification first**) · #17 sweep after full-history
CSV · #20 Qtile install test items. Plus, from the plan: **answer eBay Developer Support's
8 questions** (gates `buy.marketplace_insights` → PP-REPRICER-001 live).

### Long-running / in-flight

- **`ebay_sku_migrate`** — ~8,350 legacy listings at ~5–10/hr; months. Pausable via config.
  Fulfillment-policy reconciliation deliberately frozen until it completes.
- **PP-NIXOS-001** — plan written, **not approved, nothing executed**. Phase 0 items become
  todos on Dave's approval.
- **PP-SOLD-001 Tier 4 webhook** — code done; blocked on operator infra **and** ISS-005.
- **PP-REPRICER-001 live mode** — blocked on `buy.marketplace_insights` scope.
- **Aider execution tier** — designed, not adopted; gate unchanged (API key + billing cap +
  ≥3 Aider-ready todos).
- **Antigravity validation** — ⏰ **hard deadline 2026-06-18** (Gemini CLI shutoff, 7 days):
  the 5-step checklist in `dev-workflow/next-process.md` §3 includes a side-by-side brief
  comparison that is **only possible while both CLIs run**.

### Known issues and accepted gaps

- Open: ISS-002 (9–10 legacy shipping fixes, manual), ISS-003/ISS-004 (config
  normalization — folded into NixOS Phase 0.4), ISS-005 (webhook signature), ISS-008
  (legacy-listing resolution not authoritative).
- Closed since v1: ISS-001 (25002).
- Archive tombstone ceiling (~22K pre-2023 sold records) — accepted.
- Untracked tree: `docs/dev-workflow/`, `docs/roadmap/`, master-plan edits, one
  `.sync-conflict-*` file in `.obsidian/`.

## 3. What should be done next

Unchanged framing: the build phase is over — **operate, harden, migrate**, in that order of
emphasis. The concrete order is §6.

## 4. Which parts are safe to automate

(Unchanged from v1 except todo references.) Safe = idempotent, reversible,
derived-data-only, or operator-gated downstream:

- Everything already automated and proven: the 18-queue pipeline up to `ebay_stage`,
  catalog/thumbnail rebuilds, `velocity_stats`, `token_refresh`, `pm_intake`, notify.
- Derived-store maintenance — rebuild-from-ItemData is always safe by invariant.
- Read-only operations: `tgw health`, queue views/`queue-history`, `tgw reprice-suggest`,
  dead-letter listing, `tgwlogs`.
- XS/S well-specified offline-testable tasks (Round-5 rows 40/41/44/45 qualify; row 42 is
  bulk-mutation → dry-run gate; row 43 touches the fulfillment resolver → review gate).
- `tgw dead-letter --requeue-transient` — candidate for scheduled automation after a few
  weeks of clean manual use.

## 5. Which parts must remain human-reviewed

Unchanged from v1 — the standing gates in `dev-workflow/claude-cli.md` §3: every diff
before commit; publishing (`tgw publish` is the only path to Active); eBay-touching code
paths; OAuth scopes/keyset (locked); live config + secrets; `state_machine` schema;
permanent dead-letters; destructive/bulk operations (dry-run first); all NixOS migration
phases 1–6; behavior flags on live listings (`strikethrough_enabled`).

## 6. Consolidated execution order

This replaces and reconciles: v1 §6, the master-plan Round-5 residue, the NixOS plan
phases, and the dev-workflow adoption gates. Ordered by dependency, deadline, and risk.

**Now (this week):**

1. **Commit the build-phase tail** (Dave — carried over from v1, still not done):
   review + commit `docs/dev-workflow/`, `docs/roadmap/`, master-plan edits; delete the
   `.obsidian/*.sync-conflict-*` artifact. Until committed, these files have **no safety
   net at all** — not in git, and outside the backup watcher's data tree.
2. **Antigravity validation checklist** (operator, deadline **2026-06-18**): the 5 steps in
   `next-process.md` §3 — especially the side-by-side Gemini brief comparison and the
   headless-use check, which gate all future delegated-task wiring and are impossible after
   shutoff. This outranks all discretionary build work.
3. **Schedule a daily `pg_dump`** (operator one-liner — cron or systemd timer writing
   `pg_dump --format=custom` into `/opt/TGW/var/backups/`): the ledger holds the *canonical
   todo queue* (`todo_items`), which — unlike pipeline state — cannot be re-derived from
   ItemData. Putting the dump inside the data tree means the existing file snapshots carry
   a consistent copy off the live cluster. Closes the biggest data-loss hole (risk 8)
   without waiting for PP-BACKUP-001. Details: `runbooks/postgres-outage.md` § Rollback.
4. **Seed Round-5 residuals as todos** (restores the canonical-queue invariant), then drain:
   rows 40 → 41 → 45 (XS, ~one short session) → 44 → 43 → 42 (S; 43 review-flagged,
   42 dry-run-gated). Closes Round 5 completely.

**Next (unblocks the hardening track):**

5. **Dave approves or amends `PLAN-nixos-migration.md`** → Phase 0 items become todos.
   This is the single gating decision for everything below it; it has no date.
6. **NixOS Phase 0** (normal test-gated repo work, no infra risk; interleaves freely):
   0.1 Pillow promotion · 0.2 Nix module fixes (PG 17 pin, schema bootstrap, backup unit)
   · 0.3 template-unit form · 0.4 config normalization (**closes ISS-003 + ISS-004**) ·
   0.5 site-config repo · 0.7 health additions (PG version + ledger-tables + fleet +
   backup-freshness + queue-aging checks — see plan §9.1).
7. **Discretionary build lane** (parallel, anytime): `tgw history-index` (newly unblocked),
   PP-PYIPC-001 implementation, Aider trial when its gate is met.
8. **Operator parallel lane** (no dependencies): answer eBay DS 8 questions (highest
   leverage — unblocks PP-REPRICER-001); ISS-002 Seller Hub fixes (#12 — **run
   `tgw ebay-pull` after the manual edits**, see ISSUES.md); IGDB creds (#7);
   Qtile test items (#20); ebay-sweep (#11).

**Then (the migration, at Dave's pace, gates green before each phase):**

9. **Phase 1** — MX ISO bake with the **uid-900 migration folded into the same downtime
   window** (plan step 0.6).
10. **Phases 2 + 3 (parallel-safe)** — VM validation (incl. pg_restore drill) + spare-machine
    client tier with headless Syncthing (also feeds PP-PYIPC-001).
11. **Phase 4** — shadow-server dress rehearsal; **eBay workers masked (hard rule R7)**;
    record restore timings (they are the DR RTO).
12. **Phase 5 + 6** — cutover (spare-promotion path is the lower-risk option) → 2-week
    shakedown → MX ISO retirement decision.

**Post-cutover queue (unchanged from v1):** webhook go-live only after ISS-005 signature
verification; `ebay_sku_migrate` completion + fulfillment-policy reconciliation;
PP-BACKUP-001 DR suite; sync-conflict-resolution worker before any catalog write-back.

Standing cadence throughout: daily `tgw health` + `tgw dead-letter` (expect empty),
weekly queue-depth glance, runbook INDEX as the triage entry point.

## 7. If Claude is no longer available

Unchanged from v1 in substance — no AI is load-bearing at runtime; losing Claude affects
development velocity only. Fallbacks: keep operating via runbooks; the work queue survives
in `tgw todo` + master plan + `docs/architecture/` + `docs/invariants.md`; re-route dev
tiers per `dev-workflow/README.md` §2 (Aider on the API, Antigravity for bounded tasks,
Perplexity for research); human-only mode is protected by the same guardrails (475-test
suite, ruff, invariant tests, `tgw health`, review-then-commit). The three tool-agnostic
hard rules: **never alter eBay OAuth scopes, never auto-publish, never commit without
Dave.**

## 8. Unresolved risks (cross-doc review, 2026-06-11)

1. **Antigravity window expiry (dated!):** headless/scripted use and the skills/hooks
   carry-over are *unverified claims*; after 2026-06-18 the Gemini baseline comparison is
   permanently impossible. If the checklist slips, accept reduced confidence and note it.
2. **ISS-005 webhook signature gap:** accept-when-unsigned is a deliberate interim, but if
   webhook infra is ever exposed before dev_id verification, a forged notification can mark
   items sold. Gate is documented in three places; the risk is someone deploying infra
   (operator todo #16) without reading them.
3. **ISS-008:** legacy-listing resolution data is not authoritative — duplicate-listing
   protection rests entirely on the `ebay_stage` Active-listing guard.
4. **Zero-work stall class (invariants D7):** transient requeues are unbounded by design;
   batch-success verification is a noted pattern, not generic. Surfaced only by daily
   health/notify discipline.
5. **Transient-error substring coupling (D6):** classification is string matching; the
   substring list is *duplicated in three docs* (runbooks INDEX, services.md, invariants).
   Rewording a worker error silently converts wait-states into dead-letters. One cross-check
   test exists for the stage string; the token-expiry string has none.
6. **Inline ItemData path construction (invariants A4 ⚠):** several workers duplicate the
   path formula the fence owns; the proposed CI grep gate was never built. Becomes a real
   bug when PP-PORTABLE-CATALOG satellites change the layout.
7. **R12 — Ollama on NixOS cannot be rehearsed** (spare machine can't run the models);
   first real validation is at cutover step 5.6, on production hardware, mid-window.
8. **PostgreSQL backup coverage:** file snapshots don't capture live WAL; no routine
   `pg_dump` is scheduled (only the runbook's pre-snapshot dump). Same-host-only backups
   until PP-BACKUP-001. A disk loss today loses the ledger since the last manual dump —
   including **`todo_items`, the canonical task queue, which cannot be re-derived from
   ItemData** (pipeline state can; the todo queue and job-history audit cannot).
   *Mitigation queued:* §6 step 3 (daily dump timer) reduces this to ≤24 h exposure.
9. **Two-surface task tracking (process risk):** plan-table rows that never become todos
   vanish from view — it happened to Round-5 rows 40–45 within one day of handoff v1.
   Mitigation is procedural (seed rows as todos at round creation), not enforced.
10. **NixOS plan approval is undated:** the entire hardening/migration track (steps 5–12
    above) queues behind a decision with no deadline; meanwhile the MX host's DR posture
    is the weakest part of the system (risk 8).
11. **Backup freshness is unmonitored:** nothing watches the age of the newest dump or the
    last rclone sync — a silently-dead backup path stays invisible until a restore is
    needed. Check specified in NixOS plan §9.1 (backup-freshness health check); until it
    lands, verifying backup age is a manual weekly habit.
12. **Media-mutation safety is per-feature, not systemic (invariant A8 ⚠):** alt-text
    archives originals before renaming, but no fence-level guard covers photo writes
    generally. The researched GDrive→EPS photo pipelines are the next exposure; the
    interim control is the A8 review rule (any diff touching non-JSON files in a SKU
    folder must show its archive step).
13. **eBay-side human edits don't round-trip:** manual Seller Hub changes (e.g. the ISS-002
    fixes) reach the local mirror only via the 6 h sync — and only for mirrored fields.
    Reconciliation ownership now documented (ISS-002: run `tgw ebay-pull` + spot-check
    after any direct eBay edit), but it remains a habit, not a mechanism.
