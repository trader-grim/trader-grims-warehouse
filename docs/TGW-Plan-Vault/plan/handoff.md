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
