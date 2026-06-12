# TGW Handoff Packet — Next Process

**Status:** v3, 2026-06-11. Supersedes v2. Written after sessions 24–25 completed Round 5
residuals + PP-DOCFLOW-001 Phase 1 + PP-BACKUP-001 Phase A. Tracker beats plan when they
disagree. Branch `round4-vision-export-todos` is 22 commits ahead of origin — unpushed.

---

## 1. Source of Truth (ranked)

| Source | What it owns |
|--------|-------------|
| `tgw todo claude` / `tgw todo admin` | **Canonical task queue** — if it's not here, it doesn't exist as work |
| `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` | Reference spec, architecture decisions, PP-* design |
| `docs/architecture/` + `docs/invariants.md` | Verified architecture + 5 invariant contracts (7 test files) |
| `docs/runbooks/` | 8 incident runbooks + triage INDEX |
| `docs/plans/PLAN-nixos-migration.md` + `PLAN-backup-dr.md` | Migration/DR plans (approved; phases become todos on Dave's go) |
| Test suite (480+ passing) | Correctness contract — `pytest` must stay green |
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

### In tracker, Claude-assigned (todo #56, #57)

- **#56** Round 6 #50: `tools/migrate_batch.py` — 8 F821s (missing imports, undefined `BULK_MIGRATE_URL`, no `main`). Decide: repair or archive. If `ebay_sku_migrate` supersedes it, archive out of lint path.
- **#57** Round 6 #51: `tools/repair_itemdata_json.py` — Python 3.12-only f-string backslash syntax (host runs 3.11; script cannot parse today) + unused `nxt`. Fix or archive.

### Operator-gated (in tracker as admin todos)

- **#61** PP-BACKUP-001 Phase A operator items: approve plan (done) → gpg passphrase custody → install 3 timers → first manual cloud sync → restore drill (RTO timing). **Scripts exist in `etc/systemd/` — nothing is running yet.**
- **#7** IGDB credentials (Twitch dev portal)
- **#11** `tgw ebay-sweep` physical inventory review
- **#12** Fix 9 wrong-shipping Seller Hub listings (ISS-002)
- **#16** eBay webhook infra (nginx/cloudflared) — **gate: ISS-005 signature verification first**
- **#20** Qtile WM install

### Larger planned work (no todos yet)

| Item | Status | Blocker |
|------|--------|---------|
| PP-DOCFLOW-001 Phase 2 | Designed | Phase 1 done; seed as next todo |
| PP-NIXOS-001 execution | Plan written, approved | Dave signals go → Phase 0 becomes todos |
| PP-REPRICER-001 live | Read-only foundation done | `buy.marketplace_insights` scope (eBay DS 8 questions unanswered) |
| PP-PYIPC-001 | Research complete | No todos seeded yet |
| `tgw history-index` | Design sketch (GEMINI-007) | MasterArchive repaired 2026-06-11; unblocked |
| PP-PORTABLE-CATALOG-001 P2 | Design complete (PERPLEXITY-006) | PP-PYIPC-001 first |
| PP-SOLD-001 Tier 4 webhook | Code done | Infra + ISS-005 |
| PP-VISION-001 P2+ | Deferred | GPU upgrade required |
| `ebay_sku_migrate` | Running | ~8,350 live listings; months |

---

## 3. What Fable Changed (post-handoff-v2, commits after `0ba1a9a`)

Seven commits landed in sessions 24–25 after v2 was written:

| Commit | What changed | Deploy note |
|--------|-------------|-------------|
| `577c356` Round 5 #44 | `GET/POST /form/suggest` in tgw-http: punctuation-safe suggestion web form; network-trust (no Bearer); reuses `cmd_suggest()`; 5 tests | Restart `tgw-http` |
| `e1bf1bc` lint | `fix = true` removed from `pyproject.toml`; `systemd/history/` excluded; bare `ruff check` no longer mutates | None |
| `d28172f` PP-DOCFLOW-001 P1 | `pm_intake` ported to `call_model()` + `tgw-models.json` → `openrouter/google/gemini-2.5-flash`; new actions: `no_change\|append_to_section\|file_document\|flag_for_review`; `new_section` demoted to review-flag; 4h submission-delay gate + `tgw admin-file [--now]`; `FILING-LOG.md` audit trail; `inbox/review/` dir; 19 offline tests | Restart `pm_intake` worker; confirm `openrouter` API key in secrets |
| `282b484` PP-BACKUP-001 Phase A | `tgw-db-backup` + `tgw-cloud-sync` + `tgw-secrets-backup` scripts + systemd units/timers in `etc/systemd/`; `check_backups()` in `health.py`; tests — **scripts exist, operator must install** | Operator todo #61 |
| `fa6cfe8` Round 5 #45 | `TGW-Quickstart.md` pipe examples: `--skus-only`, stdin `-`, multi-SKU, `enqueue-sku` queue-first path | None |
| `9d8b8f1` Round 5 #43 | Standard Envelope ≤0.25 in gate in `_resolve_fulfillment_id()`; CATEGORY-QUIRKS note | Restart `ebay_stage`/`ebay_publish` |
| `35cef9e` Round 5 #42 | `description_history` boilerplate contamination scrub ("John F. Rider" + generic contamination strings); dry-run default; reports affected SKUs + strips on `--write` | Run `tgw data-scrub --pass 3 [--write]` after review |

**All 22 branch commits are uncommitted to origin** — `git push` + PR needed before any deploy.

---

## 4. What Remains Risky

Ordered by urgency:

1. **No backup running (deadline risk):** PP-BACKUP-001 Phase A scripts exist but timers are not installed. `todo_items` (the canonical task queue) and `queue_job_history` **cannot be re-derived from ItemData** — a disk loss today loses them since the last manual dump. *Mitigation: operator todo #61.*

2. **Antigravity validation window (hard deadline 2026-06-18 — 7 days):** Headless/scripted use and skills/hooks carry-over are unverified. The side-by-side Gemini CLI comparison is only possible while both CLIs run. After shutoff, accept reduced confidence permanently.

3. **eBay DS 8 questions unanswered:** Blocks `buy.marketplace_insights` → PP-REPRICER-001 live. No code workaround — Dave must respond to eBay Developer Support.

4. **ISS-005 webhook signature gap:** `accept_when_unsigned` is a deliberate interim. If webhook infra (operator todo #16) is deployed before dev_id signature verification is implemented, forged notifications can mark items sold. Gate is documented in three places; don't deploy infra first.

5. **22 commits unpushed:** The entire post-v2 work (DOCFLOW, BACKUP, scrubs, fulfillment gate) has no remote safety net and no PR. Disk loss = lost work.

6. **`pm_intake` needs OpenRouter key:** PP-DOCFLOW-001 Phase 1 routes `pm_intake` to `openrouter/google/gemini-2.5-flash`. If `openrouter-credentials.json` is absent, `pm_intake` worker will dead-letter on every job. Verify key exists before restarting worker.

7. **Inline ItemData path construction (invariant A4):** Several workers duplicate the path formula the fence owns. Becomes a bug when PP-PORTABLE-CATALOG satellites change layout. CI grep gate was designed but not built.

8. **Two-surface task drift:** Plan rows not seeded as todos vanish (Round 5 rows 40–41 still not seeded). Procedural control only — not enforced.

---

## 5. Recommended Next Sequence

**Immediate (this week, deadline-driven):**

1. **Push the branch + open PR** — 22 commits of work with no remote copy. Do this first.
2. **Antigravity validation checklist** (deadline 2026-06-18): run the 5-step checklist in `docs/dev-workflow/next-process.md` §3; the side-by-side Gemini comparison is time-limited.
3. **Operator: PP-BACKUP-001 Phase A** (todo #61): install timers, first cloud sync, restore drill. Closes the biggest data-loss risk. Scripts are ready — this is operator work, ~30 min.
4. **Verify `pm_intake` OpenRouter key**: check `secrets_root/openrouter-credentials.json` exists + is 600; restart `pm_intake`; confirm no dead-letters after first inbox job.

**Next (code work, pick-up order):**

5. **Seed rows 40–41 as todos** (XS, ~10 min) — restores canonical-queue invariant.
6. **Drain todos #56, #57** — Round 6 cleanup; both are XS/S offline-testable.
7. **Answer eBay DS 8 questions** (operator) — highest-leverage unblocked action for live pipeline value.
8. **PP-DOCFLOW-001 Phase 2** — suggestions batch-classify; natural next slice after Phase 1.
9. **Dave approves/amends `PLAN-nixos-migration.md`** → Phase 0 items become todos.
10. **PP-PYIPC-001 implementation** (research complete; Syncthing live at 8384 with API key).
11. **`tgw history-index`** (MasterArchive repaired 2026-06-11; design sketch in GEMINI-007-result.md).

---

## 6. Tool Routing

| Task type | Tool | Notes |
|-----------|------|-------|
| Bounded PP-* slices, new workers, test coverage | **Claude CLI (Sonnet)** | One session per item; round-table format; run `tgw health` + tests after |
| Architecture decisions, high-stakes design | **Claude CLI (Opus)** | Use for planning sessions, invariant design, risk assessment |
| Mechanical refactors, adding tests to existing code | **Aider** | Gate not met yet: needs API key + billing cap + ≥3 Aider-ready todos queued |
| Large-context data analysis, alt-text batch, corpus cross-reference | **Antigravity/OpenRouter** | `agy` configured; OpenRouter key in settings; free vision via `openrouter/free` |
| Research inbox docs, self-contained structured tasks | **Gemini CLI** | Keep tasks small (compute-based limits, 5h refresh window) |
| Live web research, cited sources | **Perplexity** | 4 briefs unrun (PERPLEXITY-001–004); subscription ~2026-12 |
| eBay OAuth, Seller Hub edits, infra deploy, hardware | **Human only (Dave)** | Never automate publish, scopes, live config, or destructive bulk ops |

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

# 5. Confirm what's unpushed
git log --oneline origin/round4-vision-export-todos..HEAD 2>/dev/null || git log --oneline main..HEAD
```

Then: read the master plan (`cat docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md`) and check this file's §2 against `tgw todo` to confirm alignment before picking up work.
