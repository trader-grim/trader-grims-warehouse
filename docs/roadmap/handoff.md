# TGW Handoff Packet — Next Process

**Status:** v4, 2026-06-11. Supersedes v3. Incorporates new docs/ tree (invariants,
services, runbooks, plans, dev-workflow) + session 26 completions (PP-DOCFLOW-001 P2,
PP-PYIPC-001, tgw history-index). Tracker beats plan when they disagree.
Branch `round4-vision-export-todos` is **pushed** to origin, ~27 commits ahead of main.
PR + merge to main still pending.

---

## 1. Source of Truth (ranked)

| Source | What it owns |
|--------|-------------|
| `tgw todo claude` / `tgw todo admin` | **Canonical task queue** — if it's not here, it doesn't exist as work |
| `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` | Reference spec, architecture decisions, PP-* design |
| `docs/invariants.md` | 29 invariants (A1–A8, B1–B5, C1–C8, D1–D7, E1–E4) + resolution log; 7 companion test files |
| `docs/architecture/services.md` + `overview.md` | Service-by-service responsibility, deps, failure modes, critical invariants |
| `docs/dev-workflow/next-process.md` | Session handoff protocol + Aider + Antigravity tool routing |
| `docs/runbooks/INDEX.md` + 8 runbooks | Incident response (dead-letter triage, pipeline stall, token failure, etc.) |
| `docs/plans/PLAN-nixos-migration.md` + `PLAN-backup-dr.md` | Approved migration/DR plans; phases become todos on Dave's go |
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

## 3. What Changed This Session (session 26 — 2026-06-11)

Five commits on top of the earlier 22:

| Commit | What changed | Deploy note |
|--------|-------------|-------------|
| `ee2f65f` PP-DOCFLOW-001 P2 | `tgw.suggestions` module + `tgw classify-suggestions [--apply] [--limit N]`; batch-classifies SUGGESTIONS.md entries via gemini-2.5-flash; dry-run default; 16 tests | None (no worker; CLI only) |
| `db188a6` test fix | `test_apply_marks_already_done` missing `todo_add` mock was writing real todos; deleted 3 spurious DB entries; fixed | None |
| `f57d862` PP-PYIPC-001 | `tgw.apis.syncthing` (pyncthing + httpx disk events); `tgw.apis.kdeconnect` (kdeconnect-cli subprocess); config keys `syncthing_config_path`/`syncthing_url`; `pyncthing` added to pyproject.toml; 25 tests | None (library only; no worker) |
| `4bfa3c9` history-index | `tgw.history_index` module + `tgw history-index [--target ItemArchive\|loose-csv\|all] [--dry-run] [--limit N]`; indexes ~32K legacy Magento archive zips not in archive-ebay-index.json → `var/history-itemdata-index.jsonl`; indexes loose eBay order CSVs → `var/history-loose-csv-index.jsonl`; smoke-tested on production (54,683 zips); 13 tests | None (run manually when ready) |

**Sessions 19–25 new docs** (written by Dave / earlier Claude sessions, not captured in v3):

| Doc | Content |
|-----|---------|
| `docs/invariants.md` | 29 system invariants with enforcement status + 7 test files; gaps B4, C3–C6, A5 fixed 2026-06-10 |
| `docs/architecture/services.md` + `overview.md` | Full service map: responsibility, deps, failure modes per subsystem |
| `docs/dev-workflow/next-process.md` | Session handoff SOP; Aider config + task template; Antigravity 2.0 notes + validation checklist |
| `docs/runbooks/INDEX.md` + 8 runbooks | Dead-letter triage, pipeline stall, token failure, eBay rejections, sold-sync gaps, Ollama stall, catalog stale, Postgres outage |
| `docs/plans/PLAN-backup-dr.md` | PP-BACKUP-001 full plan (APPROVED 2026-06-11); Phase A scripts in `etc/systemd/` |
| `docs/plans/PLAN-nixos-migration.md` | PP-NIXOS-001 migration plan; verified against live host |

---

## 4. What Remains Risky

Ordered by urgency:

1. **No backup running (deadline risk):** PP-BACKUP-001 Phase A scripts exist but timers are not installed. `todo_items` (canonical task queue) and `queue_job_history` **cannot be re-derived from ItemData** — a disk loss today loses them since the last manual dump. *Mitigation: operator todo #61.*

2. **Antigravity validation window (hard deadline 2026-06-18 — 7 days):** Headless/scripted use and skills/hooks carry-over are unverified. Side-by-side Gemini CLI comparison is only possible while both CLIs run. Checklist in `docs/dev-workflow/next-process.md` §3. After shutoff, reduced confidence is permanent.

3. **eBay DS 8 questions unanswered:** Blocks `buy.marketplace_insights` → PP-REPRICER-001 live. Dave must respond to eBay Developer Support.

4. **ISS-005 webhook signature gap:** `accept_when_unsigned` is a deliberate interim. Deploy webhook infra (todo #16) only after dev_id signature verification is implemented — forged notifications can mark items sold otherwise. Gate documented in invariants (C8), ISSUES.md, and services.md.

5. **Branch not merged to main:** 27 commits are on `round4-vision-export-todos`, pushed to origin but no PR merged. Losing the production machine before merge = audit/rollback difficulty. Open and merge the PR.

6. **`pm_intake` needs OpenRouter key:** PP-DOCFLOW-001 Phase 1 routes `pm_intake` to `openrouter/google/gemini-2.5-flash`. If `openrouter-credentials.json` is absent, `pm_intake` will dead-letter every job. Verify before restarting the worker.

7. **Inline ItemData path construction (invariant A4):** Several workers duplicate `itemdata_root / sku / f'{sku}.json'` inline instead of calling `config.sku_json()`. No CI gate. Becomes a bug when PP-PORTABLE-CATALOG changes layout. See `docs/invariants.md` A4.

8. **Two-surface task drift:** Plan rows not seeded as todos vanish (rows 40–41 still unseeded). Procedural — not enforced.

---

## 5. Recommended Next Sequence

**Immediate (this week, deadline-driven):**

1. **Merge the branch** — open PR for `round4-vision-export-todos` → main; review diff; merge.
2. **Antigravity validation checklist** (deadline 2026-06-18): run the 5-step checklist in `docs/dev-workflow/next-process.md` §3 while both CLIs still run.
3. **Operator: PP-BACKUP-001 Phase A** (todo #61): install timers, first cloud sync, restore drill. Closes the biggest data-loss risk. Scripts are ready — ~30 min operator work.
4. **Verify `pm_intake` OpenRouter key**: confirm `secrets_root/openrouter-credentials.json` exists + is 600; restart `pm_intake`; confirm no dead-letters.

**Next (code work, pick-up order):**

5. **Seed rows 40–41 as todos** (XS, ~10 min) — restores canonical-queue invariant.
6. **Answer eBay DS 8 questions** (operator) — highest-leverage unblocked action for live pipeline value.
7. **PP-PORTABLE-CATALOG-001 P2** — Syncthing API client now done (PP-PYIPC-001); no remaining code blocker.
8. **Dave approves/amends `PLAN-nixos-migration.md`** → Phase 0 items become todos.
9. **Run `tgw history-index --target all`** (no code needed; command exists) — will take ~hours for 32K zips; run as `tgw` user in a screen session.
10. **A4 CI gate** — grep test that `itemdata_root.*\.json` outside `config.py`/`items.py` fails the suite; small, high-value invariant close.

---

## 6. Tool Routing

See `docs/dev-workflow/next-process.md` for the full decision tree + Aider config + Antigravity constraints.

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
