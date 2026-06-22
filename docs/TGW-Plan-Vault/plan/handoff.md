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

## 3. What Changed This Session (session 39 — 2026-06-22)

**Sessions 27–38** covered Home Manager / fish shell (PP-HM-001 Phase 1), dress rehearsal config (`tgw-test-rehearsal.nix`), schema init (`tgw-db-init`), USB vault (`TGW-VAULT` btrfs partition + `tgw-usb-stamp.sh`), `tgw-push-config.sh` validated end-to-end, `nix flake check` passing all 4 configs. See master plan session log for full detail.

**Session 39 — 2026-06-22 (this session):**

| Change | Detail |
|--------|--------|
| Storage architecture decision | LVM for OS base + PostgreSQL + future microVMs; Btrfs for `/opt/TGW` data. Replaces Btrfs+NoCoW design. |
| `nix/hosts/tgw-prod-disko.nix` | New file — LVM+XFS+Btrfs layout; device `/dev/sda` placeholder, sizes for ~1 TB drive. |
| `flake.nix` | Wired `disko.nixosModules.disko` + `tgw-prod-disko.nix` into tgw-prod config. |
| `PLAN-nixos-migration.md` step 3.3 | Updated with storage architecture decision table + status. |
| Master plan Disko row | Updated; Stage 4 Disko todo checked off. |
| Disko files | Warning comments added to both disko files — `extraArgs=["-f"]` danger if nixos-anywhere re-run post-restore. |
| `inbox/lvm.md` | Archived (Perplexity research on LVM storage architecture). |

**Dave's confirmed execution sequence (in progress 2026-06-22):**
1. Back up data; confirm storage devices safely offline
2. Disable all tgw services + Syncthing + rclone
3. uid migration (`usermod -u 900 tgw`, full-disk audit, permissions check)
4. Database dump (`pg_dump --format=custom`)
5. MX snapshot (Phase 1 ISO bake)
6. Phase 4 dress rehearsal → Phase 5 production cutover

⚠️ **One flag on this sequence:** the plan (Phase 0.6) calls for briefly restarting the pipeline after uid migration and doing a reboot test *before* the pg_dump — to confirm the uid change didn't leave anything broken. If you skip this, you'll have no validation that uid=900 works on MX before the ISO is baked. Low risk since the procedure is well-defined, but worth a quick `tgw health` + `tgw enqueue-sku <any> echo` after the uid change before proceeding to the dump.

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

**Migration in flight (2026-06-22) — Dave executing:**

1. ✅ Data backed up, storage devices safe
2. → Disable all tgw + Syncthing + rclone services
3. → uid migration (0.6) — then `tgw health` + echo round-trip + reboot before pg_dump
4. → `pg_dump --format=custom` into `/opt/TGW/var/`
5. → MX snapshot (Phase 1 ISO bake, boot-verify in QEMU)
6. → Phase 4 dress rehearsal on A1131: push `tgw-test-rehearsal`, restore from backup, run §7 battery, record timings
7. → Phase 5 production cutover: nixos-anywhere on prod SSD, restore /opt/TGW (from HDD local copy if available — much faster than rclone), unmask/start workers in order

**Code-side still open (Phase 0.4):**

- Config normalization: clean dead keys from `tgw-api-config.json`, surface `ebay_sku_migrate` through `load_config()` proper. Can be done any time before or after cutover — not a migration gate.

**After cutover stabilises:**

- PP-PORTABLE-CATALOG-001 P2 (no remaining code blocker)
- PP-BACKUP-001 Phase A timers (scripts exist in `etc/systemd/`; nothing running yet)
- A4 CI gate (invariant close; small)
- Code/venv LV in LVM (deferred — not needed for migration)

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
