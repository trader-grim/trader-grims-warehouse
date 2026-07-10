## Implementation TODO — next priorities

### Recently completed (sessions 4–10)
- ✅ **PP-QUALITY-001** listing quality scorer (2026-06-04)
- ✅ **PP-PRICE-003** comp search improvement (2026-06-04)
- ✅ **PP-HINT-001** bulk requeue command (2026-06-04)
- ✅ **PP-SEO-001** title enhancement, all phases (2026-06-04)
- ✅ **PP-REF-001** item JSON schema doc (2026-06-04)
- ✅ **PP-CI-001** linting + GitHub Actions (2026-06-04)
- ✅ **PP-LOOKUP-001** all Tier 1 sources (2026-06-05)
- ✅ **PP-PRICE-004** velocity analytics (2026-06-05)
- ✅ **PP-LISTING-001** description footer + picklist line (2026-06-04)
- ✅ **PP-SOLD-001 Tier 2** CSV import (run 2) — 909 fuzzy + archive tombstone pass; need full all-time CSV for archive hits
- ✅ **PP-STORE-001** eBay store categories (2026-06-05)
- ✅ **PP-STRIKE-001** strikethrough pricing (2026-06-05) — disabled by default; enable once Dave verifies Seller Hub access
- ✅ **PP-CAPTURE-001** `tgw note` + `tgw btw` aliases (2026-06-05)
- ✅ **PP-REF-002** eBay error code reference (2026-06-05)
- ✅ **Data Scrub Pass 1** `#VERIFIED`→`verified` rename (2026-06-05) — 55,226 items
- ✅ **PP-IFDIR-001** interface file org (2026-06-05)
- ✅ **PP-SHELL-001 Tier 1** shell source audit (2026-06-05)
- ✅ **SKU search first-18** (2026-06-05)
- ✅ **PP-TODO-001** multi-agent TODO tracker (2026-06-05)
- ✅ **PP-WM-001 Phase 1** Qtile WM base config (2026-06-05); operator install pending
- ✅ **PP-PRICE-005** Category groups taxonomy (2026-06-06)
- ✅ **PP-TOKEN-001** token double-buffer bug fix + `tgw restart-ebay-token` (2026-06-06)
- ✅ **PP-SHELL-001 Tier 2** ARCH-VIOLATES replacements + deprecated block removal (2026-06-06) — all 6 ARCH-VIOLATES functions replaced with thin `tgw` CLI wrappers; `statusupdate` CLI added; `verifiedupdate` now writes `verified`+`#STATUS` atomically; 20+ deprecated blocks removed; file 3405→2879 lines. Remaining deprecated: csvmerge*, addphotos*, data2json*, archivenewitems, mkjob*, newitem* (minor, no arch risk — wrap if needed).
- ✅ **PP-INTAKE-001 Phase 1** `tgw set-template` command (2026-06-06) — writes category_group, ai_hint (prepended), size_class, ebay_category_id to item JSON; pushes SETTEMPLATE: to clipboard for KDE Connect relay; `--list`, `--camera`, `--dry-run`; resolves SKU from CurrentItem symlink; closes the template→pipeline loop.
- ✅ **PP-MC-001 Phase 2** `tgwitem` copyin + ebay/ + pipeline/ + actions/ (2026-06-07) — copyin for fields/ and meta.json; ebay/ read-only subdir; pipeline/ live PG jobs; actions/ pipeline triggers. Deploy: `sudo bash etc/interfaces/mc/install-system-mc.sh`.
- ✅ **tgw status** alias for `tgw health` (2026-06-07)
- ✅ **tgw mvitems** — expands `catlocmvall` with SKU list / --from / --search / --status selectors (2026-06-07); catlocmvall kept as deprecated alias
- ✅ **tgw bash completion** — `etc/completion/tgw-completion.bash`; auto-sourced via `tgw.source` (2026-06-07)
- ✅ **tgw suggest-edit** — opens SUGGESTIONS.md in $EDITOR; `--pending-only` for focused review (2026-06-07)
- ✅ **PP-GLOBALS-001 analysis** (2026-06-07) — no `globals` block needed; top-level fields already serve this role; add `weight_oz: float | null` in PP-INTAKE-001 Phase 2 (natural write path)
- ✅ **PP-HINT-001 Browse enrichment** (2026-06-07) — `_fetch_browse_aspect_hints()` in `ebay_draft.py`; ASPECT_REFINEMENTS fieldgroup + category filter; injects common values into Ollama prompt; `browse_hint_count` in draft_listing
- ✅ **PP-HINT-001 trail** (2026-06-08) — `identification_history` in item JSON; `append_history_event()` in `items.py`; events: `ai_identify` + `hint_set`; `tgw hint-trail <sku>` CLI

### Session 35 — 2026-06-29 — Migration complete; permanent-failure signals; photo push; workers restarted

**PP-SKU-MIGRATE-001 COMPLETE** — `ebay_sku_migrate` reached "no live non-canonical items remain"
at 02:39 UTC. All migratable items are on canonical SKUs. Permanent-failure signal set expanded
in `_PERMANENT_ERROR_SIGNALS` to include errorId 25021 (condition invalid even after USED_EXCELLENT
retry), 25002 (invalid/missing item specific), 25004 (qty=0 / sold), 25005 (invalid category),
25604 (availability not found), duplicate listing, and ended listings. Without this fix the worker
was looping forever on ~29 items that had `ebay_done=True` but no permanent-failure match.

**Permanent failures (ebay_done=True)** — ~29 items blocked with `sku_migrate_skip=True`:
categories that reject all used conditions (25021), missing required item specifics (25002),
zero quantity (25004), stale category IDs (25005). Use `tgw migrate-unblock <sku>` after fixing
the underlying data to retry each one through normal pipeline (ebay_draft → ebay_publish).

**Photo push complete** — `scripts/ebay_photo_push.py --include-no-eps` ran live: 539 items
pushed successfully, 66 remain (same items with eBay 400 errors — blocked migration items whose
Inventory API records are in a bad state). Photos on 539 live listings now match local files.

**All workers restarted** — 14 workers active: catalog_rebuild, thumbnail_gen, pm_intake,
plan_render, ai_identify, ebay_draft, ebay_upload, ebay_price, ebay_stage, ebay_publish,
ebay_sync, ebay_legacy_sync (365-day lookback running), ebay_sku_migrate (idle/polling),
token_refresh. Worker fleet was stopped since session 31; now fully live.

See `docs/TGW-Plan-Vault/dev-workflow/research/RESEARCH-sku-migration-complete-2026-06-29.md` for full outcome, root cause, and remaining open items.
### Session 34 — 2026-06-28 — Migration fixes, photo reconciliation, sold-item detection

**PP-EBAY-MIRROR-001 P1 DONE** — `scripts/ebay_normalize.py` ran: 19,394 items updated
(photo_urls from ebay_live, listing_url constructed, image_urls→imageUrls rename).
total=55,419, errors=0. `ebay_sku_migrate` immediately resumed and began succeeding.

**PP-EBAY-MIRROR-001 P1.5 DONE** — `scripts/ebay_photo_push.py` written and
dry-run verified; ready to run after migration completes (~1,135 items with photo gaps +
116 with no EPS photos). todo #1073.

**PP-EBAY-MIRROR-001 P2 DONE** — `ebay_sync.py` extended: (1) propagate
`ebay_live.inventory_item.product.imageUrls` → `ebay_offer.photo_urls` when missing;
(2) `_check_photo_integrity()` refreshes ebay_live + ebay_offer.photo_urls when confirmed
URLs differ from stored. Worker restarted.

**ebay_sku_migrate condition-ID fixes** — `_CONDITION_MAP` in `ebay/sync.py` now maps
`'3000' → 'USED_EXCELLENT'` (root cause: 2017-era items stored Trading API conditionId '3000';
`_map_condition('3000')` was falling through to default USED_GOOD, rejected by some categories).
25021 condition retry added to both `_migrate_inventory()` and `_recover_partial()` — PUT
inventory_item with USED_EXCELLENT and re-publish if first publish rejected.
13 items were briefly offline (old offer deleted, new offer stuck); 12 recovered automatically
(one — GATX train car — was sold last week and has been cleaned up).

**Sold-item guard in migrate_one()** — before migrating, if `_find_offer()` returns an
offer with status not in (PUBLISHED, UNPUBLISHED, ACTIVE), update local `ebay_listing.status`
and skip — prevents re-listing sold items when `ebay_legacy_sync` is behind. Uses
`fence_ebay_write` (import added).

**`mark_item_sold()` rewritten** — now decrements `draft_listing.quantity` by sold qty;
marks `status=sold` / `ebay_listing.status=Sold` ONLY when remaining qty reaches 0.
Multi-qty items (partial sales) update qty + record ebay_sale but stay active. Idempotent
on same `order_id`. `ebay_legacy_sync` restarted with 365-day lookback (no prior state file).

**Sway + lan-mouse Nix modules** — `nix/os/sway.nix` and `nix/os/lan-mouse.nix` written;
`nix/hosts/tgw-prod.nix` updated (sway + lan-mouse, input-leap-server retired by removal);
`nix/hosts/a1131.nix` updated (lan-mouse, input-leap-client retired by removal).
`nixos-rebuild switch` not yet run — pending verify.

**Aider MCP fixed** — `/home/tgw/.local/bin/aider` created as symlink →
`/etc/profiles/per-user/db/bin/aider` (Nix store, world-accessible). MCP bridge now
finds binary; restart Claude Code to reload. MCP server runs as `tgw` so it can write
`tgw`-owned source files.

### Session 33 — 2026-06-28 — eBay mirror gap audit + WM/KVM architecture decisions

**PP-EBAY-MIRROR-001 opened** — full audit of eBay data gaps. Root cause: `ebay_live` is
populated but nothing propagates values to `ebay_offer` canonical fields. `photo_urls` missing
for 2,137/2,138 listed items despite data sitting in `ebay_live.inventory_item.product.imageUrls`.
`draft_listing` has `image_urls`/`imageUrls` key mismatch. Phase 1 normalization script unblocks
`ebay_sku_migrate` (stopped after 319/2,138 successes — remaining items all fail "no photo URLs").
Marketing data (promotions, markdown, watchers) added to scope: `sell.marketing` scope held.
Supersedes `scripts/ebay_backfill_offers.py`, `scripts/ebay_audit.py`, PP-EBAY-SNAPSHOT-001 todo #894.

**WM + KVM decisions** — Qtile retired; **Sway** selected (Wayland-native, stable, waybar for
TGW dashboard). Input Leap retired; **lan-mouse** selected (wlroots peer-to-peer, proper Wayland
clipboard via `zwlr-data-control-v1`, in nixpkgs 0.10.0). **Wayland primary** decision recorded —
X11/XWayland compatibility only where it comes for free (reversed from prior X11-primary stance
after 9 hours clipboard debugging). Hyprland left as reconsideration candidate once mature.

**Flutter/web surface hierarchy** — web UI = universal fallback; Flutter = near-universal primary
(Linux desktop + Android + iOS + web). Separation between event server and WM means surface swap
is config not rewrite.

### Session 32 — 2026-06-28 — PP-FENCE-001 Session C + status resolution fix

**PP-FENCE-001 Session C DONE** — http_server write consolidation (see PP-FENCE-001 section above).

**`sqlite_catalog.py` status resolution fix** — catalog builder was reading `#STATUS` first, hiding
new `status` field values (e.g. `status=archived` invisible when `#STATUS=new` also present).
Fix: `_resolve_status()` with terminal-state-wins logic — terminal values (`sold`, `archived`,
`disposed`, `recalled`, `merged`, `discard`, `vero`) win regardless of which field contains them;
otherwise `status` takes precedence over `#STATUS`. Fixes the long-standing archive invisibility bug.
353 items had conflicting `In Stock`/`sold` states — now correctly resolved.
5,103 items have only `#STATUS` (no `status`) — normalization migration pending (todo #1053).

**ebay_sync bug identified (not yet fixed)** — `fetch_all_offers()` in `ebay/sync.py` calls
`GET /sell/inventory/v1/offer` which returns HTTP 400 / error 25707 on every request. Silent
failure: catches 400 and returns `[]` with only a `log.debug()`. Worker has been reporting
"received 0 offers" every 6h cycle. Fix: replace with inventory_item paging + per-SKU offer
lookup. Tracked separately; does not block worker restart.

**Workers now unblocked** — restart sequence: catalog_rebuild (running) → thumbnail_gen →
pm_intake → plan_render → ai_identify (verify one job) → ebay_draft → ebay_upload →
ebay_price → ebay_stage → ebay_publish.

### Session 30 — 2026-06-28 — PostgreSQL recovery + architectural audit

**Incident:** Accidental `nixos-rebuild switch --flake ...#a1131` run on tgw-prod (session 29,
2026-06-27) re-initialised the PostgreSQL cluster mid-day. WAL corruption confirmed by LSN
regression (morning checkpoint `0/2D4xxxx`, shutdown record `0/1B46420`). Recovery: `rm -rf
/var/lib/postgresql/17/`, fresh initdb via pre-start script, `pg_restore` from Jun 27 03:30
custom-format dump (`/opt/TGW/var/backups/.../state_machine-20260627.dump`). All services
restored. Btrfs snapshots healthy (no scrub errors; 49 hourly externals on sda7, latest 08:00).

**Workers stopped:** All `tgw-worker@*` services deliberately stopped pending data integrity
fix. Do not restart until PP-FENCE-001 Sessions A+B complete and eBay backfill done.

**eBay data audit findings (session 30):**
- 19,366 items confirmed in Inventory API (`/sell/inventory/v1/inventory_item`)
- **Zero items** have `ebay_offer.offer_id` or `ebay_listing.listing_id` in local JSON — eBay
  IDs were never written back after migration
- ~196 listings still in Trading API only (GetMyeBaySelling minus Inventory API SKU set) —
  not yet identified individually
- **Acceptance target:** 19,653 live listings visible in local catalog post-recovery (Seller Hub
  count 2026-06-28), minus items sold during recovery window. Note: 19,366 + ~196 = ~19,562 —
  gap of ~91 vs Seller Hub total; identification script will resolve. Any remaining gap after
  full migration = data integrity issue, investigate before restarting workers.
- Root cause: workers call `atomic_write_json` directly, bypassing the fence; Stage 1 (API
  fence) was never built; Invariant A4 has been ⚠️ partial since the start

**Immediate sequence (blocks worker restart):**
1. **eBay audit script** (`scripts/ebay_audit.py`) — produces JSON report with three buckets:
   - *inventory-only*: in Inventory API, not in Trading API (expected majority)
   - *trading-only*: in Trading API, not in Inventory API → needs migration
   - *duplicates*: same SKU active in BOTH APIs simultaneously → end Trading API listing only
   Also flags orphans (no local item JSON) and SKUs with no offer (Inventory item but not listed).
   Target: accounts for all 19,653 Seller Hub listings. Any gap = investigate before proceeding.
2. **Dave reviews audit report** — approves action per bucket before any writes
3. **Backfill + dedup** — one-off script writes offer_id/listing_id/price/EPS URLs to item JSONs
   for all inventory-only items; ends duplicate Trading API listings (no relist needed)
4. **Trading-only migration** — for each trading-only item: submit via `ebay_stage`, publish,
   end old listing. Dave approves batch policy first — watchers lost in relist gap, cannot undo.
5. **PP-FENCE-001 Session A** — new fence endpoints + `src/tgw/apis/fence.py` client
6. **PP-FENCE-001 Session B** — migrate all workers off `atomic_write_json`
7. **Restart workers**

PP-FENCE-001 OS lockdown (Session C: `tgw-worker` NixOS user) can follow after workers are back.

### Active / next build priorities — staged foundation plan (adopted 2026-06-19)

**Sequence:** Stage 0 → **PP-FENCE-001** → Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 5 → Stage 6.
Data Tracks A/B/C run in parallel with Stages 1–5. Full rationale: `PROPOSED-PLAN-2026-06-19.md`.
**See `tgw todo claude` for the live task queue.** Plan = reference spec; `tgw todo` = active duties.

#### PP-FENCE-001: ItemData write fence — Sessions A+B complete (session 31)

Full design: `docs/ai-plans/PP-FENCE-001.md`.

**Session A ✅ (session 30):** New HTTP endpoints + `src/tgw/apis/fence.py` client. 18 tests pass.

**Session B ✅ (session 31):** All 30 `atomic_write_json` call sites in workers + `ebay/` replaced with fence calls. 27 tests pass; grep audit CI test added.
- Documented gaps (3 files, 6 sites): `multi_intake.py` (newitems_dir + key-delete), `ebay_sku_migrate.py` (dir-rename context), `ebay/pull.py` (restore_archive_tombstone needs upsert). Marked with PP-FENCE-001 comments.

**Next: restart workers** — Sessions A+B+C complete; fence is live for all standard workers. Restart one at a time, verify each before proceeding.

**Session C ✅ (session 32):** http_server write consolidation. All 18 inline `atomic_write_json` call sites in action handlers and photo management replaced with three internal helpers:
- `_apply_patch(json_path, fields)` — core patch; deep-merges dict fields; `None` value = delete key
- `_apply_ebay_write(json_path, sku, *, ...)` — eBay block merge with field protection
- `_enqueue_catalog_rebuild(reason)` — coalesced 30s-delayed rebuild (replaces 18 duplicate try/except enqueue blocks)
Canonical endpoints (`PATCH`, `append`, `ebay-write`, `create`) delegate internally to same helpers.
`atomic_write_json` now called only inside these three functions. Smoke tested (archive action live). Workers unblocked.

**Session D (deferred):** NixOS `tgw-worker` user (uid 901) in `~/tgw-flake`; `tgw-worker@.service` runs as new user; physical write lockout enforced by OS permissions. Aligns with PP-AIOPS-001 Stage 5 sandbox model.

**eBay backfill ✅ (session 30):** 2,089 published listings backfilled with offer_id/listing_id/price. Remaining items are draft/unpublished with no offer (expected).

#### Stage 0 — Immediate Operational Fixes (1 session, no new dev)
| Item | Fix |
|------|-----|
| Ghost `tgw-worker@http.service` crash-loop | `systemctl disable --now && mask` |
| 50 `ebay_upload` dead-letters (Jun 17 outage) | Requeue — outage cleared |
| `task/aider-20260616145314` stale branch | Diff vs main, merge or abandon |
| PP-BACKUP-001 Phase A operator todos (#61) | Install db-backup, cloud-sync, secrets-backup timers |

Data Track A Phase 4 (policies pull) can start alongside Stage 0 — read-only, no risk.

#### Stage 1 — API Fence: Asset Management (S, 1 session)
New endpoints in `tgw-api.py`: `GET/POST/DELETE /api/item/<sku>/asset*` + `POST reorder`.
Workers that touch photos route through these. Filename convention enforced at the API:
`<sku>.jpg`, `<sku>-alt.jpg`, `<sku>-thumb.jpg`, `<original>-alt.jpg`, `<sku>-foldioNN.jpg`,
legacy timestamp formats. Bare numeric names (`1.jpg`) rejected; renamed at ingest to foldioNN.
Companion name derived from source photo name at the API, never from the caller.
Full spec in `## PP-DATA-OWN-001 Track C → Asset Management` or standalone PP section below.

#### Stage 2 — PP-AIOPS-001 Phases 1–4 (L–XL, 4–6 sessions)
JetStream + ItemData audit stream + queue transition outbox + anomaly detection + litterbox.
Full spec: `plan/PP-AIOPS-001-cat-herding-platform.md`. Summary in `## Phase 5` below.

#### Stage 3 — PP-BACKUP-001 Phases B–E (M, 1–2 sessions)
DB dump (B) → cloud sync (C) → secrets backup (D) → restore validation (E).

#### Stage 4 — PP-NIXOS-001 (L)
NixOS migration. Not blocked by Stages 1–3 — those stages run the same on NixOS.
USB boot media ready: `TGW-BOOT-01/02` (2 × 16 GB Ventoy, 400 MB tgw-kit). Prep: Phase 2.5.

#### Stage 5 — PP-AIOPS-001 Phase 5: AI Session Isolation (L–XL, after PP-NIXOS-001)
Btrfs CoW snapshot per session + ephemeral nspawn + FIFO pipe + cgroup supervisor.
Bad agent sessions roll back in one command. Full spec in `plan/PP-AIOPS-001-cat-herding-platform.md`.

#### Stage 6 — PP-DATA-OWN-001 Phases 2–5
Ongoing eBay sync, sold history backfill, forward sync, repricer (blocked on scope).

#### Data Track A — eBay Data Acquisition (parallel, safe to start)
- Phase 4 (policies pull): `sell.account` → `data/ebay-policies.json`; fixes ISS-002
- Phase 2 (ongoing sync): schedule `ebay_sync` to write `ebay_live` on a cycle
- Phase 3 (sold/transaction history): verify all 976 sold items; fix status filter
- Phase 5 (forward sync): on push, refresh `ebay_live` from response

#### Data Track B — Photo Recovery ✅ CLOSED 2026-06-19
618 items repaired by `scripts/photo_repair_iss013.py`. ISS-013 closed.
Archive sweep (originals → history) deferred until Stage 2 CDC in place.

#### Data Track C — Reference and Relationship Data (parallel, safe to start)
See `## PP-DATA-OWN-001` section below for full C1–C5 detail.
- **C1** Shipping policies → `data/ebay-policies.json`
- **C2a** Category hierarchy (main/secondary/store) → `data/ebay-categories.json`
- **C2b** Full aspects per category (required/recommended/optional) → `data/ebay-aspects-by-category.json`
- **C2c** EPS URL → local photo correlation → `data/ebay-image-map.json`
- **C2d** Full raw metadata capture: store complete eBay API responses; diff vs ItemData; quirks → CATEGORY-QUIRKS.md
- **C3** Category group enrichment (25 groups); **C4** Location types; **C5** Error code index

#### Operator-gated / blocked
| PP | Status | Notes |
|----|--------|-------|
| **PP-SOLD-001 Tier 3** sweep | operator gated | Run `tgw ebay-sweep` after full-history CSV import |
| **PP-PYIPC-001** | ✅ **DONE 2026-06-11** | `tgw.apis.syncthing` + `tgw.apis.kdeconnect`; 25 tests |
| **PP-REPRICER-001** live | blocked | Blocked on `buy.marketplace_insights` scope (eBay DS 8 pending) |

### Running in background
- `ebay_sku_migrate` — ~8,350 live listings remaining; ~5/hr; ~70 days to complete
- PP-SOLD-001 Tier 4 webhook — code done; awaiting operator infra (nginx/cloudflared)
- `velocity_stats` worker — ✅ **ENABLED 2026-06-05**; running nightly

### Archive tombstone — ceiling confirmed
eBay Seller Hub sold history export maxes at **2 years** (confirmed 2026-06-05). Archive IDs
(223–326xxx, ~2018–2023) are permanently outside this window. The archive tombstone pass is
built and correct but will not fire from CSV import alone. Options for future archive hits:
- Terapeak in Seller Hub (UI only, no API) — manual spot-checks on high-value archive items
- Wait: if `ebay_sku_migrate` eventually maps an archived eBay ID to a current SKU, that path
  may surface additional matches
- Accept the gap: ~22K archive entries; the business impact of unmarked-sold legacy items is low

