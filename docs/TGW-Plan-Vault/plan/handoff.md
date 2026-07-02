# TGW Handoff Packet — Next Process

**Status:** v5, 2026-06-12. All docs consolidated into vault (single tree). Supersedes v4.
Tracker beats plan when they disagree.
Branch `round4-vision-export-todos` merged to main (PR #2, 2026-06-12).

---

## 1. Source of Truth (ranked)

| Source | What it owns |
|--------|-------------|
| `tgw todo claude` / `tgw todo admin` | **Canonical task queue** — if it's not here, it doesn't exist as work |
| `plan/TGW-Master-Plan.md` | Reference spec, architecture decisions, PP-* design |
| `reference/invariants.md` | 29 invariants (A1–A8, B1–B5, C1–C8, D1–D7, E1–E4) + resolution log; 7 companion test files |
| `reference/TGW-Architecture-Services.md` + `TGW-Architecture-Overview.md` | Service-by-service responsibility, deps, failure modes, critical invariants |
| `plan/next-process.md` | Session handoff protocol + Aider + Antigravity tool routing |
| `reference/runbooks/INDEX.md` + 8 runbooks | Incident response (dead-letter triage, pipeline stall, token failure, etc.) |
| `plan/PLAN-nixos-migration.md` + `PLAN-backup-dr.md` | Approved migration/DR plans; phases become todos on Dave's go |
| Test suite (563 passing) | Correctness contract — `pytest` must stay green |
| `tgw health` | System liveness gate — run before and after any significant change |

**Numbering pitfall:** tracker IDs and plan Round-table row numbers are different sequences.
Use plan row numbers for plan-table items; "todo #N" only for live tracker IDs.

---

## 2. Planned but Not Implemented

### Claude-ready — not yet in tracker (seed first)

| Plan row | Size | Task |
|----------|------|------|
| 40 | XS | `category-groups.json` pricing calibration (GEMINI-005): `electrical_fixtures`→12.50, `media_records`→13.50, `collectibles_pins_buttons`→10.50; run `tgw category-groups --reseed` |
| 41 | XS | `category-groups.json` store_category mappings (GEMINI-006): `tools_hand`, `electronics_adapters_chargers`, `electronics_remotes`, `kitchen_utensils` |

### Operator-gated (in tracker as admin todos)

- **#61** PP-BACKUP-001 Phase A operator items: scripts + timers exist in `etc/systemd/`; **nothing is running yet.** Remaining: gpg passphrase custody → install 3 timers → first manual cloud sync → restore drill (RTO timing).
- **#7** IGDB credentials (Twitch dev portal)
- **#11** `tgw ebay-sweep` physical inventory review
- **#12** Fix 9 wrong-shipping Seller Hub listings (ISS-002)
- **#16** eBay webhook infra (nginx/cloudflared) — **gate: ISS-005 signature verification first**
- **#20** Qtile WM install

### Larger planned work (no todos yet)

| Item | Status | Blocker |
|------|--------|---------|
| PP-NIXOS-001 execution | Plan written + approved | Dave signals go → Phase 0 becomes todos |
| PP-REPRICER-001 live | Read-only foundation done | `buy.marketplace_insights` scope (eBay DS 8 questions unanswered) |
| PP-PORTABLE-CATALOG-001 P2 | Design complete (PERPLEXITY-006) | PP-PYIPC-001 done ✅; no remaining blocker |
| PP-SOLD-001 Tier 4 webhook | Code done | Infra (#16) + ISS-005 |
| PP-VISION-001 P2+ | Deferred | GPU upgrade required |
| PP-VERIFY-001 | Scaffolded (Gemini Track 2) | Integration + tests |
| PP-STORE-001 / PP-REF-002 / PP-CAPTURE-001 | Designed | Track 1 queue |
| A4 grep gate | Documented in invariants.md | CI integration (no ticket yet) |
| `ebay_sku_migrate` | Running | ~8,350 live listings; months to complete |

---

## 3. What Changed This Session (session 40 — 2026-07-01/02)

**Session 40 — 2026-07-01/02 (action console design+build, revision apply, hermes/flake ops):**

- **PP-ACTIONCONSOLE-001 designed AND built in one session.** Design principle settled
  with Dave: state drives the interface; every control is also an indicator; compaction
  without losing anything; platform-wide house style (fulfillment/warehousing next).
  Built: state-driven action line (intake/draft/working/live-in-sync/live-edited/error/
  sold), Editor + Live/Sold Listing tabs (live tab = graduated "eBay Live Data" content),
  pipeline breadcrumb + eBay Status dropdown + Pipeline Tools section removed, pricing
  history merged to one left-column display (comps deduped to the price-field panel),
  dead-letter job lines get contextual Retry (zero-clutter guarantee) with
  operator_retry ledger annotation (improvement loop). Late fix per Dave: Inventory
  Record separated on top; "eBay Listing" bordered block below. All state pages
  verified 200. Fixed latent `(dl or {{}})` f-string crash (no-draft items 500'd).
- **PP-LISTEDITOR-001 Phase 2 revision apply CODE COMPLETE** — `_APPLY_ENABLED=True`
  (Dave confirmed design), fresh-GET→delta→PUT live path with supported-field table,
  revision_history audit, endpoint enqueues ebay_sync after apply; 10 new tests (74
  pass). **Live-fire NOT yet done** — first test: one live item, price-only delta.
- **Clipboard gated:** new todo #1086 (conceptual planning pass unifying PP-CLIP-001 +
  PP-EVENTD-001 + inbox research) BLOCKS #1055 rofi picker, per Dave.
- **Ops (tgw-flake, committed + pushed):** hermes model switched to
  openrouter/xiaomi/mimo-v2.5-pro (experimentation; Claude remains documented default);
  disk/storage toolkit added to base.nix (smartmontools ncdu duf fclones rmlint
  git-annex recoll); a1131 rebuilt via --target-host (no GitHub access there).
- **Reference:** eBay daily quotas reset 00:00 PST — recorded in eBay-API-Landscape.md.

**Open / risks:**
- Dave tests the new console next session → iterate (ship-and-adjust).
- Revision-apply live-fire pending; Update Item button still routes to ebay_update
  (stage-force) not revision apply — wire after live-fire.
- Troubleshooting buttons (Re-identify/Re-upload/Re-price/Sync) removed from item page
  with NO new home yet — ops surface to design.
- `tgw health` failing: backups, nats, ebay_sync_fallback (pre-existing? unverified).
- Master plan sync-conflict files from tonight's edit/Syncthing race — expected to
  self-clear; check next session that they did.

---

## 3b. Previous Session (session 37 — 2026-06-30)

**Session 37 — 2026-06-30 (Sway graphical-session.target fix + reboot test):**

| Change | Detail |
|--------|--------|
| Root cause found | `~/.config/sway/config` had no `include ~/.config/sway/conf.d/*.conf` — flake-managed session init was silently ignored on every login |
| Wrong target fixed | Flake `db.nix` was calling `systemctl --user start graphical-session.target` which has `RefuseManualStart=yes`; fixed to `sway-session.target` (NixOS's `BindsTo` pulls in graphical-session transitively) |
| kdeconnectd added to flake | Now a proper HM `systemd.user.services` entry; D-Bus activation alone never broadcast to LAN |
| `nixos-rebuild switch` clean | All three services (lan-mouse, kdeconnectd, tgw-clipd) auto-start on login |
| Reboot test passed | lan-mouse active; tgw-prod visible in KDE Connect on a1131 |

**Open from session 37:** Nothing new — no TGW workers or eBay API touched.

---

## 3. What Changed This Session (session 36 — 2026-06-29)

**Session 36 — 2026-06-29 (PP-WM-001: Sway TGW-ify + Flutter startup fix):**

| Change | Detail |
|--------|--------|
| `flutter_secure_storage` removed | Replaced with `lib/config/tgw_config.dart` (plain files in `~/.config/tgw/`). Root cause: libsecret → tinysparql caused 1-2 min startup delay. |
| Flutter app rebuilt | nix-shell: cmake ninja clang libepoxy fontconfig; `CC=clang`; explicit `-lepoxy -lfontconfig` linker flags. Recipe in `DONE-sway-flutter-startup.md`. |
| `/opt/TGW/bin/tgw-app` wrapper | LD_LIBRARY_PATH caching + `NO_AT_BRIDGE=1 GTK_USE_PORTAL=0 GSETTINGS_BACKEND=memory GIO_USE_VFS=local GTK_MODULES=""` to kill all GTK D-Bus startup delays. |
| xdg-desktop-portal-gtk fixed | Was failing "cannot open display: :0" — `WAYLAND_DISPLAY` not in systemd user env. Added `exec systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP DISPLAY` to sway config. |
| Permissions script updated | Flutter SDK bin/ and *.sh get 0750; bundle .so* symlinks no longer excluded by -type f. |
| Sway TGW chord working | Super+T: h/q/s/t/v/c/o/F all working. F launches Flutter app. |

**Open from session 36:** a1131 setup (sway + lan-mouse) — in progress next.

---

## 3b. What Changed Session 35 — 2026-06-29

**Sessions 33–34** covered PP-EBAY-MIRROR-001 P1/P1.5/P2, ebay_sku_migrate condition fixes, mark_item_sold qty-decrement, Sway+lan-mouse Nix modules, Aider MCP fix. See master plan session log.

**Session 35 — 2026-06-29 (this session):**

| Change | Detail |
|--------|--------|
| `_PERMANENT_ERROR_SIGNALS` expanded | Added 25021, 25002, 25004, 25005, 25604, duplicate listing, ended listings. Without this, items with `ebay_done=True` but unrecognised error codes looped forever. |
| `ebay_sku_migrate` COMPLETE | Reached "no live non-canonical items remain" at 02:39 UTC. ~29 items permanently blocked with `sku_migrate_skip=True`; use `tgw migrate-unblock <sku>` after data fix. |
| Photo push live | 539 items pushed successfully; 66 remain (eBay 400 — same blocked migration items). |
| Worker fleet restarted | All 14 workers now active after being stopped since session 31. `ebay_legacy_sync` running 365-day lookback (no prior state file). |

---

## 3c. What Changed This Session (session 38 — 2026-06-30)

**Session 38 — 2026-06-30 (dead-letter triage, ebay_sync 25707 fix, SEO title filler demotion):**

| Change | Detail |
|--------|--------|
| SEO title filler demotion | `_demote_leading_filler()` in `seo/title.py`: Case A (pure filler lead → append to end), Case B (year+filler cluster → insert at positions 4-5) |
| `ebay_offer.status` stale fix | `ebay_stage.py`: force-update of live listing now preserves `PUBLISHED` status instead of unconditionally writing `UNPUBLISHED` |
| `ebay_offer.price` accuracy fix | `ebay_publish.py`: uses `staged_price` (what eBay was told) not reprice schedule's launch_price |
| `ai_identify` dead-letter fix | `_USER_PROMPT_HINTED` built by string concat with JSON `{}`; `.format()` treated braces as placeholders. Switched to `.replace()`. |
| `ebay_sync` 25707 workaround | `fetch_all_offers()` falls back to per-SKU individual lookups when bulk GET /offer fails with 25707. Root cause: orphaned eBay offer with book-title SKU (see below). |
| Orphaned eBay offer identified | `tgw201607172015419` had `sku_old = "Murder on the Middle Fork by Don Ian Smith and Naida West"` — this SKU exists on eBay as a draft offer (no backing inventory_item, not visible in Seller Hub). eBay Inventory API refuses DELETE/GET with 25707. **todo #1077: contact eBay support to purge.** |

### Open from session 38

- **todo #1077** — Contact eBay support to delete orphaned draft offer with book-title SKU. Until removed, `ebay_sync` uses per-SKU fallback (slower but correct).
- **`ebay_stage`/`ebay_upload` KeyError('api_key') for tgw202605051933258** — separate config issue, not yet investigated.
- **`ebay_stage` "ImageLinks cannot exceed"** for same item — eBay rejecting image URLs.
- **15 stale `catalog_rebuild` dead-letters** from 2026-06-28/29 — likely old path errors; may need cleanup.

## 3d. What Changed This Session (session 39 — 2026-07-01)

**Session 39 — category picker rebuild, eBay API quota audit, condition-policy fix, action-console design (todo #1078, done; #1079 + PP-ACTIONCONSOLE-001 open):**

| Change | Detail |
|--------|--------|
| Category field rebuilt | Was broken (429 from per-keystroke live Taxonomy calls). New multi-mode picker: local-cached-tree search / type-ID / Browse. `apis/ebay/taxonomy.py`: `search_categories_local`, `get_category_node`, `get_category_children`; tree cached to `ebay-category-tree.json` (30-day TTL). New `/api/ebay/category-{search,node,children}` endpoints. |
| Aspects cached | `get_aspects()` was live-per-page-view, zero caching — the real Taxonomy quota killer. Now cached per category_id, `ebay-aspects-cache.json`, 14-day TTL. |
| `ebay_sync.py` quota fixes | (1) Unconditional `inventory_item` GET on every offer every sync pass (~8k calls/day) now gated by `ebay_verify_interval_days`, reuses photo-integrity check's fetch instead of double-calling. (2) Per-SKU 25707 fallback now tracked in `ebay-sync-fallback-state.json`; new `tgw health` check `ebay_sync_fallback` goes red after 2+ consecutive runs. |
| Condition policy fabrication fixed | `http_server.py` had a hand-rolled `_CONDITION_ID_MAP` that invented 3 grades (Used-Excellent/Good/Acceptable) under eBay's single real "Used" conditionId 3000. Removed; condition dropdown now sourced from the real cached per-category Metadata API policy (`apis/ebay/conditions.py`, already correct, just wasn't wired up). |
| Prop 65 un-hidden | Removed `'California Prop 65 Warning'` from `specifics.py`'s aspect skip-list — it's a real, near-universal aspect, was wrongly treated as boilerplate. |
| `get_category_tree_id` resilience | Was in-memory-only with no fallback — stacked a 2nd live-call failure atop every aspects/search call during quota exhaustion. Now disk-cached (effectively permanent) with documented EBAY_US default `'0'` as last resort. |
| `aspects_error` field added | Empty aspects list used to render as "no specifics for this category" (false — no real category has zero). API now distinguishes lookup-failed from genuinely-empty; UI shows "lookup failed, retry" instead. |
| Condition remap wired up | `best_condition_for_enum()` (never-upgrade condition remap) existed but was never called anywhere. Now wired into `/api/ebay/category-context?current_condition=` + JS auto-selects the remapped value with a visible note + auto-PATCH — fixes "switching category jumped condition to Like New" (confirmed via live data: 3000/"Used" and the 4000/5000/6000/"Very Good/Good/Acceptable" set are ~mutually exclusive per category; books/media use 5000 not 3000, as Dave suspected). |
| Pipeline status bar restyled | Flat text breadcrumb instead of button-like chips; dropped "Staged" from operator view (not actionable, implementation detail). |
| ~130 new tests | Across ~10 new/extended test files, all passing. |

**PP-ACTIONCONSOLE-001 opened (design only, nothing else built):** item detail page has ~12 action buttons + a status bar; Dave wants day-to-day listing focused with no clutter. Settled so far: Archive/Delete/End Listing stay as first-class actions; troubleshooting buttons (Re-identify, Re-upload photos, Sync from eBay, manual Stage) should relocate to a *separate ops/admin surface* entirely, not stay on this page even collapsed; stateful/smart buttons should extend the already-existing Publish-Now→Update-Listing/End-Listing pattern to every action slot, replacing separate status indicators; troubleshooting collapses conceptually to "this AI result sucks, try again." Still undesigned: draft-vs-live view toggle, operator notes field, exact button-slot transition logic. See plan section for full discussion — do not build the 3-button consolidation until the contextual-log-action design is settled.

### Open from session 39

- **PP-ACTIONCONSOLE-001** — design conversation, continue next session before building anything beyond what's already done (pipeline bar restyle).
- **todo #1079** — PP-CATPICK-001 Phase 1 (backfill category_candidates names/paths from tree cache, zero API calls) — planned, not started.
- **Taxonomy API per-category aspects endpoint** may still be quota-exhausted for categories never previously viewed (separate from tree-ID resolution, which is now fixed) — expected to self-clear; no action needed unless it persists unusually long.
- **todo #1077** carries forward from session 38 — still open, still the root cause of the `ebay_sync` fallback path.

---

## 4. What Remains Risky

Ordered by urgency:

1. **No backup running (deadline risk):** PP-BACKUP-001 Phase A scripts exist but timers are not installed. `todo_items` (canonical task queue) and `queue_job_history` **cannot be re-derived from ItemData** — a disk loss today loses them since the last manual dump. *Mitigation: operator todo #61.*

2. **Antigravity validation window (hard deadline 2026-06-18 — 7 days):** Headless/scripted use and skills/hooks carry-over are unverified. Side-by-side Gemini CLI comparison is only possible while both CLIs run. Checklist in `plan/next-process.md` §3. After shutoff, reduced confidence is permanent.

3. **eBay DS 8 questions unanswered:** Blocks `buy.marketplace_insights` → PP-REPRICER-001 live. Dave must respond to eBay Developer Support.

4. **ISS-005 webhook signature gap:** `accept_when_unsigned` is a deliberate interim. Deploy webhook infra (todo #16) only after dev_id signature verification is implemented — forged notifications can mark items sold otherwise. Gate documented in invariants (C8), ISSUES.md, and services.md.

5. **Branch not merged to main:** 27 commits are on `round4-vision-export-todos`, pushed to origin but no PR merged. Losing the production machine before merge = audit/rollback difficulty. Open and merge the PR.

6. **`pm_intake` needs OpenRouter key:** PP-DOCFLOW-001 Phase 1 routes `pm_intake` to `openrouter/google/gemini-2.5-flash`. If `openrouter-credentials.json` is absent, `pm_intake` will dead-letter every job. Verify before restarting the worker.

7. **Inline ItemData path construction (invariant A4):** Several workers duplicate `itemdata_root / sku / f'{sku}.json'` inline instead of calling `config.sku_json()`. No CI gate. Becomes a bug when PP-PORTABLE-CATALOG changes layout. See `reference/invariants.md` A4.

8. **Two-surface task drift:** Plan rows not seeded as todos vanish (rows 40–41 still unseeded). Procedural — not enforced.

---

## 5. Recommended Next Sequence

**Worker fleet live as of session 35 (2026-06-29) — pipeline running normally.**

1. Monitor ebay_legacy_sync — 365-day lookback running; check for sold items being detected.
2. Review ~29 permanently-blocked migration items (sku_migrate_skip=True). Items with 25002
   (missing item specifics) re-queue after ebay_draft regenerates data. Items with 25021
   (category rejects used condition) need category change in item JSON.
3. PP-BACKUP-001 Phase A — scripts + timers exist in etc/systemd/; nothing running yet.
   todo_items and queue_job_history cannot be re-derived from ItemData.
4. PP-FENCE-001 Session D — 6 unfenced write sites remain (deferred). Run when stable.
5. nixos-rebuild switch — Sway + lan-mouse Nix modules written session 34; not yet applied.

**Deferred:**


---

## 6. Tool Routing

See `plan/next-process.md` for the full decision tree + Aider config + Antigravity constraints.

| Task type | Tool | Notes |
|-----------|------|-------|
| Bounded PP-* slices, new workers, test coverage | **Claude CLI (Sonnet)** | One session per item; `tgw health` + tests after |
| Architecture decisions, high-stakes design | **Claude CLI (Opus)** | Planning sessions, invariant design, risk assessment |
| Mechanical refactors, adding tests to existing code | **Aider** | Gate: API key + billing cap + ≥3 Aider-ready todos queued |
| Large-context analysis, alt-text batch, corpus work | **Antigravity/OpenRouter** | `agy` configured; free vision via `openrouter/free`; compute-cap refresh ~5h |
| Research inbox docs, self-contained structured tasks | **Gemini CLI** | Until 2026-06-18 cutover; keep tasks small |
| Live web research, cited sources | **Perplexity** | 4 briefs unrun (PERPLEXITY-001–004) |
| eBay OAuth, Seller Hub edits, infra deploy, hardware | **Human only (Dave)** | Never automate publish, scopes, live config, bulk-destructive ops |

**Standing human-only gates:** never alter eBay OAuth scopes; never auto-publish; never commit without Dave's review; dry-run before any bulk ItemData mutation.

---

## 7. First 5 Commands for the Next Process

```bash
# 1. Baseline health check
sudo -u tgw tgw health

# 2. Current task queue
sudo -u tgw tgw todo

# 3. Any new dead-letters since last session
sudo -u tgw tgw dead-letter

# 4. Inbox: pending items for pm_intake
ls -la /opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault/inbox/

# 5. Branch status
git log --oneline main..HEAD
```

Then: read the master plan and check §2 above against `tgw todo` to confirm alignment.

---

## Session 32 — 2026-06-28

### What changed

- **PP-FENCE-001 Session C DONE** — all 18 inline atomic_write_json call sites in http_server.py consolidated into _apply_patch(), _apply_ebay_write(), _enqueue_catalog_rebuild(). atomic_write_json now called only inside these three functions. Smoke tested live.
- **Plan Session C renamed to Session D** — OS-level NixOS tgw-worker lockout; still deferred until workers stable.
- **sqlite_catalog.py status resolution fix** — _resolve_status() with terminal-state-wins logic; fixes archive invisibility bug; 353 conflicted items resolved.
- **Master plan updated** — Session 32 recorded; PP-FENCE-001 section current.
- **Inbox cleaned** — two stale INPROGRESS notes archived; clean DONE note written.
- **Todos closed** — #1068 (Session C), #1057 (exit skill).
- **Exit skill (/tgw-exit) confirmed working.**

### Still open

- **Workers not yet restarted** — unblocked; restart one at a time, verify each. Sequence in master plan and project-status memory.
- **ebay_sync broken** — fetch_all_offers() returns 400/25707 silently; fix needed.
- **#STATUS normalization** — 5,103 items have only #STATUS; todo #1053.
- **PP-BACKUP-001 warnings** — db dump stale, rclone sync absent.
- **End+relist gate test** — verify ebay_end_listing through UI once workers running.

### New risks

- Workers stopped since session 30. Catalog drift vs eBay accumulates. Restart promptly.
- ebay_sync silent failure means no reconciliation loop. Fix is next priority after restart.

## Session 34 — 2026-06-28

### What changed

| Change | Detail |
|--------|--------|
| PP-EBAY-MIRROR-001 P1 | `scripts/ebay_normalize.py` ran: 19,394 items updated, 0 errors. Unblocked `ebay_sku_migrate`. |
| PP-EBAY-MIRROR-001 P1.5 | `scripts/ebay_photo_push.py` written + dry-run OK. Ready to run after migration. todo #1073. |
| PP-EBAY-MIRROR-001 P2 | `ebay_sync.py` propagation live — photo_urls backfill + integrity refresh. Worker restarted. |
| `ebay_sku_migrate` condition fix | `_CONDITION_MAP['3000'] = 'USED_EXCELLENT'`; 25021 retry in `_migrate_inventory()` + `_recover_partial()`. 13 briefly-offline items recovered (12 live again; GATX sold — cleaned up). |
| Sold-item guard | `migrate_one()` checks live offer status before migrating — skips if COMPLETED/ENDED, updates local status. Prevents re-listing sold items. |
| `mark_item_sold()` rewrite | Decrements `draft_listing.quantity`; marks sold only when qty reaches 0. Multi-qty partial sales stay active. 365-day lookback triggered. |
| Sway + lan-mouse Nix | `nix/os/sway.nix` + `nix/os/lan-mouse.nix` written; tgw-prod + a1131 host files updated. `nixos-rebuild switch` not yet run. |
| Aider MCP fix | `/home/tgw/.local/bin/aider` symlink created → Nix store aider. Restart Claude Code to activate. |

### Still open

- **`ebay_sku_migrate` still running** — ~2,000+ items remaining; check progress with `journalctl -u tgw-worker@ebay_sku_migrate.service | grep "no live"` for completion signal.
- **Run `scripts/ebay_photo_push.py` after migration completes** (todo #1073) — 1,019 + 116 items with photo gaps.
- **`nixos-rebuild switch`** — Sway + lan-mouse modules not yet applied; pending reboot test.
- **Other workers still stopped** — restart sequence after migration: catalog_rebuild → thumbnail_gen → pm_intake → plan_render → ai_identify → ebay pipeline.
- **83 skip-flagged migration items** — permanent failures (Best Offer / qty=0); need operator review.
- **PP-BACKUP-001** — timers still not installed; db dump stale.
- **#STATUS normalization** — todo #1053.

### New risks

- `mark_item_sold()` idempotency for multi-qty: only checks last `ebay_sale.order_id`; multiple sales per item could over-decrement if same order retried differently. Acceptable for now (single-qty is the common case).
- Sway config (`~/.config/sway/config`) not yet written — Nix module is ready but no actual sway config file exists yet for either host.

## Session 33 — 2026-06-28

### What changed

- **Suggestions processed** — 4 unprocessed items filed: PP-RESCUE-001 (tgw-rescue live ISO), PP-AGENTIC-PRICE-001 (agentic comp search, 4-phase design), PP-LISTEDITOR-001 stub added to master plan. All checked off in SUGGESTIONS.md.
- **Plan check warnings resolved** — PP-PHOTO-001 heading fixed (removed ✅ to stop false done-mismatch); PP-LISTEDITOR-001 section added. `tgw plan check` now all clear.
- **eBay backfill confirmed complete** — 19,785 items have listing_id, matching eBay's ~19,736 active+sold count.
- **ebay_sku_migrate blasting** — config cranked to batch_size=100, interval_hours=0.05. Worker started ~13:56. 3,203 non-canonical SKUs to process at ~8s/item via inventory_live path. ETA ~7-8 hours. todo #1069.

### Still open

- **ebay_sku_migrate in flight** — DO NOT restart other workers until it logs `no live non-canonical items remain — done`. Check: `journalctl -u tgw-worker@ebay_sku_migrate.service | grep "no live"`
- **83 skip-flagged items** — permanent failures (Best Offer / qty=0). Need operator review post-migration.
- **Worker restart sequence** (after migration): catalog_rebuild → thumbnail_gen → pm_intake → plan_render → ai_identify → ebay pipeline.
- **ebay_sync returns 0** — legacy listings not in Inventory API; needs fix after workers stable.
- **#STATUS normalization** — todo #1053.
- **PP-BACKUP-001** — timers still not installed.

### New risks

- **Don't restart workers while migration runs** — ebay_sku_migrate writes directly to ItemData (6 unfenced sites); concurrent writes from other workers risk collision.
- ebay_sku_migrate config is batch=100/3min — worker self-stops when done, interval is then moot.

---

## Session 32 — 2026-06-30

### What changed

- **CatioNIX dual-desktop fully wired** — a1131 + tgw-prod committed as known-good (flake `4c5b014`). Rebooted and confirmed stable.
- **lan-mouse bidirectional** — root cause of DTLS failure found in source: `authorized_fingerprints` TOML had key/value reversed. Fingerprint must be the TOML key. Both directions now working.
- **Clipboard fixed on a1131** — `dom.events.clipboardevents.enabled=false` in Firefox about:config + `firefox-wayland` package. CopyQ replaces klipper.
- **Syncthing dual-instance correct** — db=8384/22000/21027, tgw=8385/22001/21028. Both GUIs on 0.0.0.0. NixOS `guiAddress` option (not `settings.gui.address`) controls the CLI flag.
- **KDE Connect** — kdeconnectd running as systemd user service on tgw-prod (Sway); tgw-prod visible on a1131.
- **Wayland-only toolset** — ydotool, wl-clipboard, firefox-wayland committed; X11 tools removed.

### Still open

- **KDE Connect device pairing** — accept request on both sides (manual step in KDE Connect GUI).
- **KDE Connect clipboard** — will work after pairing; untested.
- **Syncthing tgw instances need pairing** — http://192.168.60.100:8385 ↔ http://192.168.60.101:8385 (add as devices in each other's GUI).
- **a1131 Plasma on tty7** — cosmetic; both Sway + Plasma sessions registered by SDDM. Works fine.
- All previous open items (worker restart sequence, ebay_sku_migrate, PP-BACKUP-001, etc.) unchanged.

### New risks

- None from this session. No TGW workers or eBay API touched.
