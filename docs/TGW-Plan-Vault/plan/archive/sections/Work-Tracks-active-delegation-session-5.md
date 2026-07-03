## Work Tracks — active delegation (session 5)

**Strategy test** (2026-06-05): The 4-track structure is an experiment in AI delegation —
routing tasks to the right model/tool at design time rather than defaulting everything to
Sonnet. PP-TODO-001 (multi-agent TODO tracker) is partly motivated by making this delegation
trackable: each track's queue becomes an agent-tagged TODO list that `tgw todo [agent]` can
surface. This session is the first real run of the pattern; assess after a few sessions whether
the routing overhead pays off in throughput.

PP-MULTIMODEL-001 is now the working model. Each new task is routed to the right tool at design time.

### Track 1 — Claude Sonnet (minimal intervention)
One bounded session per item. Ordered by value.

| # | PP | Task | Size |
|---|----|------|------|
| ✅ | PP-STORE-001 | eBay store category support — done (session 6) | S |
| ✅ | PP-STRIKE-001 | Strikethrough pricing — done (session 6); enable via config once verified | S |
| ✅ | PP-CAPTURE-001 | `tgw note`/`tgw btw` aliases — done (session 6) | XS |
| ✅ | PP-REF-002 | eBay error code reference — done (session 6); `reference/eBay-Error-Codes.md` | S |
| ✅ | PP-SHELL-001 T1 | Shell audit + targeted fixes — done (session 6); `reference/SHELL-AUDIT.md` | M |
| ✅ | PP-IFDIR-001 | Interface file org — done (session 6); MC + keyd in `etc/interfaces/`; symlink live | S |
| ✅ | Data scrub P1 | `#VERIFIED`→`verified` rename — done (session 6); 55,226 items; `tgw data-scrub`; bash + Python verifiedupdate updated | M |
| ✅ | PP-SHELL-001 T2 | (round-1 #1) ARCH-VIOLATES + deprecated removal — done session 8 (also listed below) | M |
| ✅ | PP-IFDIR-001 | (round-1 #3) Interface configs in `etc/interfaces/` — done session 6 (also listed above) | S |
| ✅ | SKU search | (round-1 #5) Catalog/search match on first 18 chars — done session 6 | XS |
| ✅ | PP-TODO-001 | PostgreSQL `todo_items` + `tgw todo [agent]` CLI — done (session 6) | M |
| ✅ | PP-WM-001 P1 | Qtile base config + TGW widgets — done (session 7); operator install pending | M |
| ✅ | PP-SHELL-001 T2 | ARCH-VIOLATES + deprecated block removal — done (session 8); SHELL-AUDIT.md updated | M |
| ✅ | PP-INTAKE-001 P1 | `tgw set-template` command — done (session 8); closes template→pipeline loop | M |
| ✅ | PP-MC-001 P2 | `tgwitem` copyin + `ebay/` + `pipeline/` + `actions/` subdirs — done (session 10) | M |
| ✅ | tgw synonyms | `tgw status` alias for health; `tgw mvitems` expands catlocmvall — done (session 10) | XS |
| ✅ | bash completion | `tgw` bash/zsh tab completion — `etc/completion/tgw-completion.bash`; sourced via tgw.source — done (session 10) | S |
| ✅ | suggestion editor | `tgw suggest-edit [--pending-only]` — done (session 10) | XS |
| ✅ | PP-GLOBALS-001 | Analysis done (session 10) — no globals block; add `weight_oz` in PP-INTAKE-001 P2 | S |
| ✅ | PP-HINT-001 Browse | Browse ASPECT_REFINEMENTS enrichment in `ebay_draft` — done (session 10) | S |
| ✅ | PP-HINT-001 trail | `identification_history` in item JSON; `tgw hint-trail <sku>` — done (session 11) | M |
| ✅ | PP-INTAKE-001 P2 | Intake web form `/form/intake/<sku>`; template chips, weight_oz, barcode, condition, ai_hint — done (session 11) | M |
| ✅ | PP-DEADLETTER-001 | `classify_dead_letter()` + `requeue_with_backoff()`; auto-reschedule transient failures — done (session 13) | S |
| ✅ | PP-VERIFY-001 P1 | `tgw catalog-verify`; 9 rules; markdown checklist — done (session 13) | M |
| ✅ | PP-MCP-001 | `tgw/mcp_server.py`; 9 tools; `tgw-mcp-server` console script; MCP registration = operator task — done (session 13) | M |
| ✅ | GEMINI-001/002 | Category group review processed; `electrical_industrial` split; 3 new verify rules; ai_hints improved — done (session 14) | S |
| ✅ | PP-VERIFY-001 P2 | `catalog_verified` hall pass; `--mark-verified`/`--force`/`--skip-verified`; clear-on-write — done (session 14) | S |
| ✅ | PP-DEADLETTER-001 health | `dead_letter_breakdown()` per-queue; health detail + MCP tool; `notify()` on requeue — done (session 14) | XS |
| ✅ | tgw todo CRUD | `--update`/`--delegate`/`--set-priority`; 9 tests — done (session 14) | XS |
| ✅ | bash completion values | `--severity` → critical/warning/info; `todo --update/--delegate/--set-priority` — done (session 14) | XS |

**Track 1 round 1 is COMPLETE** (sessions 6–14). Every numbered item above is done. The
round-2 backlog below was produced by a full code-verified audit of all open PP-* items and
issues (session 15, 2026-06-07) — see `### Track 1 — Round 2` immediately below.

### Track 1 — Round 2 — code-verified backlog (session 15, 2026-06-07)

Produced by auditing every open PP-* item + every open issue against the **actual code**, not
plan labels. Each item below was classified ready / blocked and sized; the audit also caught
substantial stale-done drift (the plan was crediting several shipped features as "to build" and
vice-versa — see the reconciliation subsection). **Nothing here is executed yet — this is the
plan.** Ordering is value-per-risk, best-first. All "ready" slices are buildable + testable
offline (pure functions, mocked tests, or local-only data); none require the dead eBay token.

**Cross-cutting rules for every round-2 slice:**
- Do **not** commit until Dave asks (he controls git history).
- Run `tgw health` after any change touching config or `health.py` (PP-GLOBALS-001,
  PP-DEPLOY-001, PP-WM-001 notify block).
- Restart affected `tgw-worker@<queue>` units after editing a worker (e.g. `ai_identify` for
  the PP-LOOKUP-001 routing change).
- **ISS-009 (eBay token)** — ⬇ DOWNGRADED (session 16): production keyset is active; token
  refresh likely resolves it. Does **not** block live eBay work — run `tgw restart-ebay-token`
  if token jobs are dead-lettered. No longer a hard blocker for Round 3 work.

**Execution status (session 15, 2026-06-07): Tier A + Tier B COMPLETE (ranks 1–6).**
Suite 77 → **184 passing**, ruff clean. Uncommitted, pending Dave's review:
- ✅ R1 PP-GLOBALS-001 — `weight_oz` → `packageWeightAndSize` in `sync.py` + 7 tests
  (`test_ebay_sync.py`). ⚠️ restart `ebay_stage`/`ebay_publish` to pick up on deploy.
- ✅ R2 PP-SOLD-001 — 20 tests (`test_sold_recon.py`); accept-when-unsigned encoded as deliberate.
- ✅ R3 PP-MCP-001 — 19 tests (`test_mcp_server.py`) incl. 10-tool drift guard.
- ✅ R4 PP-INTAKE-001 — 16 tests (`test_set_template.py`) + `fulfillment_policy_id` claim struck above.
- ✅ R5 PP-EDITOR-001 — 30 tests (`test_http_server.py`, FastAPI TestClient; PATCH/merge/auth/rebuild).
- ✅ R6 PP-STRIKE-001 — 18 tests (`test_strikethrough.py`); MSRP gate + offer-body gate. Config flag stays off.

**Tier C COMPLETE (ranks 7–14)** — suite **184 → 234 passing**, ruff clean, all CLI parses + bash completion added. Uncommitted, pending review:
- ✅ R7 PP-FULFILLMENT-001 — real `tgw picklist` (location-sorted) + `_item_ebay_id` + 4 tests (`test_picklist.py`). Plan landmine retired.
- ✅ R8 PP-HINT-001 — per-item `shipping_profile`: `tgw setshipping` + `_resolve_fulfillment_id` precedence (item > category > size_class > global).
- ✅ R9 PP-STORAGE-001 — `fulfillment_policy_by_size_class` wired into the same resolver; 11 tests (`test_listing_policies.py`). ⚠️ `ebay_sku_migrate` has its own policy copy — left untouched (actively migrating ~8,350 listings); parity is a follow-up.
- ✅ R10 PP-LOOKUP-001 — `apis/lookup/pricecharting.py` + dispatcher routing (strictly additive, fires only when Tier-1 missed) + first lookup tests, 9 (`test_lookup.py`). ⚠️ routing is on the `ai_identify` hot path → restart that worker on deploy.
- ✅ R11 PP-CAPTURE-001 — `tgw quiet-check` + `state_machine.active_depths()`; 5 tests (`test_quiet_check.py`).
- ✅ R12 PP-PERP-AUTO-001 — `tgw perp-run <BRIEF-ID> [--list]` + `## Prompt` parser; 9 tests (`test_perp_run.py`).
- ✅ R13 PP-WHISPER-001 — `tgw whispertosuggest <wav>` (ffmpeg→whisper-cli→cmd_suggest), subprocess-mocked; 6 tests. Model file still operator-supplied.
- ✅ R14 PP-CLAUDE-HELP-001 — `CLAUDE-TROUBLESHOOT.md` + `tgw claude-help [issue] [--worker] [--launch]`; 6 tests.
**Tier D COMPLETE (ranks 15–18)** — suite **234 → 263 passing**, ruff clean. Committed 9fa38ee covers 1–14; Tier D uncommitted pending review:
- ✅ R15 PP-WM-001 — (a) qtile chord bug fixed: chords now call the new `tgw enqueue-sku <sku> <queue>` (CLI sibling of MCP tgw_enqueue) + 4 tests (`test_enqueue_sku.py`); (b) notify activated — `notifications` block added to live config (backends `log,file` — desktop opt-in, behavior-neutral) + `worker_base` calls `notify.configure()` at startup (wrapped so it can't block a worker). ⚠️ restart all workers to pick up the worker_base change.
- ✅ R16 PP-DEPLOY-001 — read-only `check_ownership()` in `health.py`, wired into `check_all`; 8 tests. **Live finding:** flags `discogs-credentials.json` at mode 0o664 (group/other-readable secret — should be 0o600). UID-below-1000 is informational (doesn't fail). This makes `tgw health` report red on `ownership` until the file is fixed — operator decision (chmod 600).
- ✅ R17 PP-EMAIL-001 — `smtp`/`email` backend in `notify.py` (stdlib, fail-soft, out of default backends); 7 tests (`test_notify_smtp.py`).
- ✅ R18 PP-CLIP-001 — `src/tgw/clip.py` SQLite store + `tgw clip {list,last-sku,search,wipe}`; 10 tests (`test_clip.py`). Xlib daemon deferred (desktop-session-blocked).
Config backup: `/opt/TGW/config/tgw-api-config.json.bak-session15`. eBay scopes untouched.
Next available: Tier E (ranks 19–25) — bulk-mutation / larger / lower value-per-risk.

#### Tier A — XS, highest value-per-risk (do first)
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 1 | PP-GLOBALS-001 | Wire `weight_oz` into eBay inventory body (`packageWeightAndSize`) in `_build_offer_bodies()` with a 0-guard mirroring `ebay_sku_migrate.py:299`; unit-test. Operator already captures `weight_oz` at intake but it's dropped — staged offers ship with no calculated-shipping weight. Plan's "wait for PP-INTAKE-001 P2" dependency (≈line 1106) is **satisfied/stale**. | XS |

#### Tier B — test-only / doc-reconcile for already-shipped hot-path code (near-zero regression risk; front-load)
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 2 | PP-SOLD-001 | `tests/test_sold_recon.py` for the token-free path: `pull.find_title_match` (Jaccard/threshold/tie-reject), `mark_item_sold` idempotency, `build_listing_index`, `notifications.parse_sold_notification`/`verify_notification_signature` (encode current accept-when-unsigned as deliberate), `cmd_ebay_sweep` A/B/C. **Also reconcile: Tier 3 ebay-sweep + Tier 4 webhook are DONE, not "pending/future".** | S |
| 3 | PP-MCP-001 | `tests/test_mcp_server.py` for all **10** tools (mock `_get_cfg`/state_machine; `tgw_health` runs `include_ebay=False`). Fix drift: plan table lists only 9 (omits `tgw_dead_letter`); docstring says `~/.claude/mcp_servers.json` vs plan's `settings.json` block. | S |
| 4 | PP-INTAKE-001 | Tests for set-template: `_build_template_fields`, `cmd_set_template` (--list/--dry-run/--camera/unknown-key/CurrentItem), `POST /api/items/{sku}/set-template`. **Reconcile: P1 & P2 DONE; STRIKE the §1612 claim that the template writes `fulfillment_policy_id` — the code never does** (PP-HINT-001 shipping_profile is the cleaner per-item mechanism). | S |
| 5 | PP-EDITOR-001 | `tests/test_http_server.py` via FastAPI `TestClient` against the **untested 28 KB backend** every Flutter phase + MC console depends on: GET/PATCH `/api/items/{sku}` (sku-immutable, empty-field, merge, `catalog_verified` auto-pop, location-tree sync, coalesced rebuild), `/api/items`, `/api/locations`, `/api/category-groups`, bearer-auth 401. Mock PG + `enqueue_job`. Flutter app stays GUI-blocked. | M |
| 6 | PP-STRIKE-001 | Tests for the existing (untested) strikethrough gating: `ebay_price.py:104` MSRP>launch, `ebay/sync.py:285` `originalRetailPrice` gated on `strikethrough_enabled`. Optional: add MSRP line to `ebay_draft.py` description footer. **Reconcile: core code is DONE (plan says "Planned"); do NOT flip `strikethrough_enabled` — needs account approval + live token.** | S |

#### Tier C — new operator-facing capability, pure/additive code
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 7 | PP-FULFILLMENT-001 | Real `tgw picklist` CLI: location-sorted plain-text list (location/SKU/title/eBay id) over the token-free `list_items()`. **LANDMINE: plan line ≈2208 falsely says this already exists — it does NOT** (only `picklist_line()` in `ebay/description.py`). Hardware sub-features (scale/printer/PDF/QR) stay blocked. | S |
| 8 | PP-HINT-001 | Per-item `shipping_profile` override: `tgw setshipping <sku> <profile>` writes item JSON; `_get_listing_policies()` honors `item['shipping_profile']` with precedence item > category > global > API. **Reconcile: requeue / Browse enrichment / hint-trail / `hint --force` already DONE; only this remains.** Must not auto-repush published listings. | S |
| 9 | PP-STORAGE-001 | Wire the (currently write-only) `size_class` field into fulfillment-policy resolution: add `fulfillment_policy_by_size_class` map + extend `_get_listing_policies(..., size_class=None)` with precedence below per-category. Pairs with #8 — same resolver; sequence them together. Defer intake-UI prompt + weight-derivation. | S |
| 10 | PP-LOOKUP-001 | `apis/lookup/pricecharting.py` (games/cards/collectibles market value; graceful-skip if no key, like `igdb.py`) + dispatcher routing for is_game/is_tcg + **first-ever `tests/test_lookup.py`**. Routing edit is on every `ai_identify` run — keep strictly additive (fires only when result is None and key present); restart `ai_identify`. | S |
| 11 | PP-CAPTURE-001 | `tgw quiet-check`: read-only over `queue_depths()` + SUGGESTIONS.md/TODOs; surface pending count when queues idle (also confirm no running jobs; stdout default, notify opt-in). **Reconcile: `suggest`/`note`/`btw` + `suggest-edit` already DONE.** Add first tests for the capture commands. | S |
| 12 | PP-PERP-AUTO-001 | `tgw perp-run <BRIEF-ID> [--list]`: resolve a brief under `perplexity/`, parse the `## Prompt` body, push to clipboard via `_push_clipboard()` + stdout fallback. One parser unit test. GUI automation (ydotool/watcher/Qtile layout) deferred. | S |
| 13 | PP-WHISPER-001 | `tgw whispertosuggest <wav>`: ffmpeg → `whisper-cli` → parse → existing `cmd_suggest()`. Mock-test the parse + dispatch. whisper-cli + ffmpeg installed; **`ggml-base.en.bin` model absent** → live transcription needs operator download (plumbing is mock-testable now). | S |
| 14 | PP-CLAUDE-HELP-001 | Author `CLAUDE-TROUBLESHOOT.md` (worker→queue→DB flow, condensed ISSUES.md, diagnostic decision tree) + a `tgw claude-help [issue] [--worker]` launcher calling `claude --append-system-prompt-file`. `claude` CLI + flags confirmed present. | S |

#### Tier D — infra activation / diagnostics
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 15 | PP-WM-001 | Two headless fixes: (a) `qtile/config.py:178,184` chord keys call non-existent `tgw requeue-sku $SKU <queue>` — replace with a real per-SKU enqueue path; (b) the `tgw.notify` desktop backend is fully coded but inert — `configure_from_api_config()` has zero call sites and the `notifications` block is absent from config; add the block + call it at worker startup (keep desktop opt-in, default `['log','file']`). Phase-1 GUI verify stays desktop-blocked. | S |
| 16 | PP-DEPLOY-001 | Read-only `check_ownership(cfg)` in `health.py` wired into `check_all`: resolve tgw UID via `pwd.getpwnam`, flag UID ≥ 1000 (migration boundary), spot-check key roots + secrets (600/700) for owner/mode drift. Diagnoses only — the actual UID migration/image bake stays operator-gated. Don't walk all of ItemData. | S |
| 17 | PP-EMAIL-001 | Add an `smtp` backend to `notify.py` `_BACKENDS` (stdlib `smtplib`/`EmailMessage`, fail-soft, keep out of default backends), read from the `notifications` config block; mock-tested. Credential-free foundation; operator drops an app-password later. Inbound eBay-message half stays token/scope-blocked. | S |
| 18 | PP-CLIP-001 | Non-GUI core only: `src/tgw/clip.py` — SQLite store at `~/.local/share/tgw-clip/history.db`, `record_clip()` SKU classifier, query fns, `tgw clip {list,last-sku,search,wipe}` + tests. **Defer the Xlib daemon / socket / Qtile widget / systemd unit (desktop-session-blocked).** `python3-xlib` is already installed. | S |

#### Tier E — bulk-mutation / larger / lower value-per-risk
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 19 | PP-VERIFY-001 | Phase 3 `--fix`: auto-correct **only** safe mechanical rules (start with stale `TEMPLATE:` title-prefix strip), route through the single-item write path so the hall pass clears, coalesce a rebuild, per-SKU fix log, tests. **Only ranked item that mutates real ItemData in bulk** — explicit flag, default dry-run, conservative. **Reconcile: Phase 2 is DONE (27 tests, ~13 rules), not "Next".** | S |
| 20 | PP-MC-001 | Phase 4 `tgwlogs` extfs VFS (read-only `journalctl`-per-worker; guard unit-name injection, cap output) + **first headless extfs CLI-contract test**. **Reconcile: Phase 2 is DONE** (448-line `tgwitem` committed) — the §1144 subsection still shows it open. | S |
| 21 | PP-REPRICER-001 | Read-only foundation only: `market_data` provider interface (`OwnSalesProvider` from velocity-stats.json, `BrowseCompsProvider` from item `ebay_offer.price_comps`, `MarketplaceInsightsProvider` stub) + `recommend_price()` floored by the reprice move price + `tgw reprice-suggest [SKU\|--all]` dry-run + tests. **No eBay write** (live push + insights scope stay blocked). | M |
| 22 | PP-SHELL-001 | Bring `tgw.source`/`tgw-dev.source` under version control (copy into `etc/interfaces/shell/`) + apply the WRAP tier: replace the 6 ARCH-VIOLATES fns with `tgw <subcmd>` one-liners (all CLI equivalents now exist) + fix the `ic_test()` artifact. **Deliver as reviewable in-repo copy; do NOT mutate the live `/opt/TGW/bin` file** — cutover is operator-controlled. | M |
| 23 | PP-VISION-001 | Offline visual-fingerprint index over the 54K existing thumbnails (Pillow+numpy phash/histogram, dependency-free) + `tgw locate <image> [--size-class]` ranked-SKU output; index build is a **batch job** (catalog-rebuild-is-a-job rule). Baseline precision — frame as a workflow proof, not a final CLIP matcher. | M |
| 24 | PP-MC-002 | Satellite-capable extfs refactor: env-driven DSN/paths (`TGW_NODE_ROLE`/`TGW_HTTP_BASE`) + a `role=satellite` branch routing writes to tgw-http instead of psycopg2. Default `role=master` (preserve current behavior), gate behind env vars, test the data-source helper. Real LTSP/hardware rollout stays operator-gated. | M |
| 25 | PP-NIXOS-001 | Author `flake.nix` + `nix/tgw.nix` from existing `pyproject.toml`/`install.sh`/systemd units. ⬆ **NixOS now COMMITTED TARGET** (session 16) — active migration prep, not just evaluation. PP-DEPLOY-001 MX image = final safety-net image before cutover. Cannot build/test here (no nix toolchain) — produce files, Dave validates in VM. | M |

### Track 1 — Round 3 (session 16)

**Guiding principle:** Build time-saving interfaces usable now, especially on tablet. Maintain stability. Build better later. NixOS is the committed destination — prepare the path, don't block on it.

**Decisions confirmed (session 16):**
- NixOS = committed target. PP-NIXOS-001 promoted from evaluation to active prep.
- PP-DEPLOY-001 MX image = one final restore image as safety net before migrating.
- PP-BULKEDIT-001 = #1 priority. Tablet-usable: web UI via browser + Termux SSH fallback.
- ISS-009 downgraded — production keyset active; `tgw restart-ebay-token` if token dead-lettered.

**Active task list:** ✅ **ALL 8 DONE** (todo IDs 21–28 closed, session 17 — see `### Execution — 2026-06-07 (session 17 …)` above for the per-item summary). The todo tracker is the canonical queue. Next: a `/code-review` pass once the session limit resets, then pick the next batch (blocked items below remain operator/research-gated).

### Track 1 — Round 4 (session 18)

**Guiding principle:** Mix of pipeline hygiene, new operator-facing capability, and NixOS prep. Keep building usable things now; prep the spare machine path.

**Input to this round:**
- Round 3 all 8 DONE; 321 tests passing; git clean.
- 27 dead_letter jobs (all from 2026-06-02/03 pre-fix era) — need triage.
- `tgw todo claude` empty — seeded below.
- PP-PLASMA-001 + PP-PORTABLE-CATALOG-001 never got formal plan sections (added below).

| # | PP | Task | Size |
|---|----|------|------|
| 29 | — | Dead_letter triage: cancel 27 stale pre-fix jobs; add `tgw dead-letter --requeue-transient` flag to batch-requeue all transient-classified entries in one shot; re-enqueue 6 `no ebay_category_id` items through ai_identify | XS |
| 30 | PP-REF-003 | Author `reference/TGW-Quickstart.md`: all `tgw` CLI subcommands + workers + web forms + MC VFS + Qtile chords organised by workflow (health→intake→pipeline→eBay→admin); stubs for physical processes; replaces hunting through plan for command syntax | M |
| 31 | PP-VISION-001 P1 | Offline phash/histogram fingerprint index over 54K thumbnails (Pillow+numpy, no external deps); batch build job (`catalog-rebuild` rule); `tgw locate <image> [--size-class S]` CLI returns ranked SKU matches; index stored in SQLite catalog | M |
| 32 | PP-PORTABLE-CATALOG-001 P1 | Add plan section (design below); `tgw export-catalog <dest>` command: copies `tgwcatalog.db` + thumbnails subset to destination path; no Syncthing API needed for Phase 1 — Syncthing handles transport; lays groundwork for spare machine client setup | S |
| 33 | PP-PLASMA-001 | Add formal plan section (missing since session 16 suggestion); design notes for Plasma 6 + Qtile dual-desktop; no code this round — design/tracking only | XS |
| 34 | PP-TODO-001 | `GET /form/todos` in tgw-http: tablet-friendly HTML table of open todos grouped by agent; auth-gated (Bearer or network-trust like `/form/intake`); low-friction daily queue review from tablet/phone | S |
| 35 | PP-NIXOS-001 | Update `flake.nix` + `nix/README.md`: configure `NVM_DIR=/opt/TGW/.nvm`, `NPM_CONFIG_PREFIX=/opt/TGW/.npm` so nvm/npm install under `/opt/TGW/` when operator runs nvm install; ensures `/opt/TGW` is a fully self-contained imageable entity with no tgw home-dir dependencies | XS |

#### Execution — 2026-06-08 (session 18, Track 1 Round 4 — ALL 7 items DONE)
Built largely via a parallel build workflow (5 file-isolated agents) + main-loop wiring for the
shared `api.py`/`config.py`/completion surface. Suite **321 → 346** (+25), ruff clean, `tgw health`
green. ⚠ Sub-agent **session limit** + transient socket errors killed the adversarial-review workflow
(same constraint as session 17) — review was done **in the main loop** instead (Opus 4.8), with live
end-to-end probes. Per item:
- **#29 `dead-letter --requeue-transient`** — batch re-enqueues every `[transient]`-classified
  dead_letter job (honours `--queue`), via the existing `requeue_dead_letter_job` + `classify_dead_letter`.
  5 tests (`test_dead_letter.py`). **Live triage run:** 2 transient requeued; 5 now-fixable
  "no ebay_category_id" items (categories since populated) re-driven through `ebay_draft` (NOT
  ai_identify — would overwrite good categories); 2 stale `pm_intake` lease-expired orphans cancelled.
  Board **27 → 23**. The remaining 23 are **real eBay rejections** (25709×8, 25002 Item.Country×3
  [tracked known issue], 25738×2, 25021×1) + superseded `ebay_draft` records — left for operator
  review, deliberately **not** mass-cancelled (would hide real signal). Once the re-driven jobs
  clear, `tgw dead-letter --cancel ebay_draft` clears the superseded records.
- **#30 PP-REF-003** — `reference/TGW-Quickstart.md` (9 sections, every `tgw` subcommand cross-checked
  against `api.py` add_parser names; MC/Qtile/macroboard key maps; worker table; physical-process stubs).
- **#31 PP-VISION-001 Phase 1** — `src/tgw/fingerprint.py` (Pillow-only dHash + joint-RGB histogram;
  index in `fingerprints.db`; 64-bit dhash stored as TEXT to dodge SQLite signed-int overflow).
  `tgw build-fingerprints` (batch build, `build-thumbnails` precedent) + `tgw locate <image>
  [--size-class --top --json]`. 8 tests. **Full index built: 54,314 rows in 87s**; self-match
  verified distance 0.0000. ⚠ **`--size-class` filter is a no-op until items carry `size_class`** —
  0 of 83,520 catalog rows have it (set-template hasn't populated it at scale; enrichment + SKU
  match verified working). New config key `fingerprint_index_path`.
- **#32 PP-PORTABLE-CATALOG-001 Phase 1** — `src/tgw/catalog_export.py` `export_catalog()` +
  `tgw export-catalog <dest> [--no-thumbnails --limit --check-only]`; copies `tgwcatalog.db` +
  thumbnail subset for Syncthing relay. 8 tests. Live verified (179 MB / 83,520-row db copies clean).
- **#33 PP-PLASMA-001** — formal plan section added (dual-desktop Qtile+Plasma 6; NixOS declares both).
- **#34 PP-TODO-001** — `GET /form/todos` in tgw-http: tablet-first HTML todo dashboard grouped by
  agent, no Bearer (network trust, like `/form/intake`), `html.escape` on all fields, graceful
  200-on-DB-error. 4 tests (`test_http_server.py`).
- **#35 PP-NIXOS-001** — `nix/tgw.nix` `commonService.environment` now sets `HOME`/`NVM_DIR`/
  `NPM_CONFIG_PREFIX` under `/opt/TGW` + tmpfiles for `.nvm`/`.npm`/`.venvironments` (propagates to
  every worker + tgw-http + backup via the verified `recursiveUpdate` merge); `nix/README.md`
  home-dir-independent section; `flake.nix` devShell note. No nix toolchain on host → review-only.

⚠ **COMMIT REMINDER** (untracked, will break features if not `git add`ed): `src/tgw/fingerprint.py`,
`src/tgw/catalog_export.py`, `tests/test_fingerprint.py`, `tests/test_catalog_export.py`,
`tests/test_dead_letter.py`, `docs/TGW-Plan-Vault/reference/TGW-Quickstart.md` (+ modified
`src/tgw/api.py`, `src/tgw/config.py`, `src/tgw/http_server.py`, `tests/test_http_server.py`,
`etc/completion/tgw-completion.bash`, `nix/tgw.nix`, `flake.nix`, `nix/README.md`).

**Follow-ups surfaced this session:**
- `size_class` is virtually unpopulated (0/83,520) → `tgw locate --size-class` + PP-STORAGE-001
  resolver are inert until set-template adoption grows or a backfill runs. Candidate: a
  `size_class` backfill from `category_group` defaults (category-groups.json has per-group size_class).
- 23 real eBay-rejection dead-letters (25709/25002/25738/25021) need item-data/code fixes —
  25002 Item.Country is the tracked open issue at `## Current state` line ~83.

### Inbox processing — 2026-06-10 (session 19/20)

8 queued inbox files processed + 20 SUGGESTIONS.md items marked done. Key findings:

**GEMINI-003** — Flutter app scaffold (Phases B+C+D) delivered by Gemini. Full app at
`apps/tgw_app/`: Riverpod state, Dio HTTP, sqflite DB, all screens (SKU list, scan, detail,
edit stubs). `flutter analyze` clean. Build environment needs `libsecret-1-dev` (Linux) and
Android SDK licences; expected. Phase D edit flows disabled in UI (stubs visible, not wired).
⚠ **BACKEND-NEEDED**: app polls `/api/queue/status` for connectivity; a proper `/api/health`
JSON endpoint is the right contract → added as todo #37.

**GEMINI-004** — Multimodal photo QA on 20 items. ⚠ **Critical finding**: boilerplate
contamination in `description_history` — text from "John F. Rider Perpetual
Troubleshooter's Manuals" (electronics service manual series) injected into items across
diverse categories (confirmed on SKUs `tgw201501021970398`, `tgw201501021970498`,
`tgw201501021970953`; likely more). Probable cause: batch description import or AI prompt
bleed-through. Data scrub needed → added as Round 5 item. Alt-text pilot: Ollama/Gemini
vision can generate useful captions and SEO fields from item photos → added as todo #38.
Alt-text sidecar naming convention: `<SKU>-alt.jpg` (confirm with Dave whether this is a
renamed secondary image or an annotated derivative before implementing).

**GEMINI-005** — Pricing calibration. 3 concrete `category-groups.json` edits recommended:
- `electrical_fixtures`: `typical_used` 15.43 → **12.50** (align with market p25)
- `media_records`: `typical_used` 12.03 → **13.50** (increase to capture value)
- `collectibles_pins_buttons`: `typical_used` 9.72 → **10.50** (increase to capture value)
Run `tgw category-groups --reseed` after. Added as Round 5 items #40–41.

**GEMINI-006** — Marketing/category insights. Top zero-inventory high-velocity categories
(ST=1.00, 0 active): Sewing Buttons, Network Cards, Heavy Equipment Manuals, Lapel Pins,
Locomotives, Collectible Magazines. Store category mappings: `tools_hand`→"Tools & Workshop
Equipment", `electronics_adapters_chargers`→"Power Adapters & Chargers",
`electronics_remotes`→"TV, Video & Home Audio Accessories", `kitchen_utensils`→"Kitchen
Tools & Gadgets". Priority quality improvements: Headphones, Flashlights, Wrenches.

**PERPLEXITY-005** — Full 36KB result processed (`PERPLEXITY-005-result.md`). PP-PYIPC-001
is now research-complete. Key decisions: `pyncthing` + custom `httpx` `/rest/events/disk`
consumer for Syncthing; `pydbus` + `kdeconnect-cli` for KDE Connect; psycopg3 migration
path clear; `aiosqlite` for FastAPI catalog reads; `discogs_client` deprecated → wrap in
adapter; EasyPost for shipping rates (PirateShip has no public API); `python-evdev` for
barcode scanners; `hidapi`/`hid` for USB scales; Go-UPC/Apify for enrichment upgrades.
See PP-PYIPC-001 section for full findings.

**PERPLEXITY-006** — Flutter offline-first sync pattern (full research). Key design
decisions for PP-PORTABLE-CATALOG-001 P2:
- Syncthing + SQLite: safe only with one writer; clients must treat catalog as a closed artifact
- **Pattern**: snapshot + copy to app-private storage; open the copy, never the synced file directly
- **Stack**: `sqflite` + `sqflite_common_ffi` (Linux); `sqlite3` package (sqlite3_flutter_libs deprecated)
- **HTTP**: `dio` + `dio_smart_retry` (successor to abandoned dio_retry)
- **Offline queue**: roll own outbox SQLite table (states: pending/sent/ack); avoid black-box plugins
- **Connectivity**: `connectivity_plus` + health-ping check; `workmanager` for Android background flush
- **Flutter secure storage**: requires `libsecret-1-dev` on Linux
- **Server-side snapshot**: `sqlite3.Connection.backup()` for atomic export
See PP-PORTABLE-CATALOG-001 Phase 2 design below.

**syncthing-nixos-nginx-research.md** — Complete NixOS Syncthing deployment design:
- Isolated headless `tgw` user instance on port 8385/22001; regular users use 8384/22000
- NixOS declarative `services.syncthing`; per-hostname config via config dir symlink (LTSP fat clients)
- Nginx reverse proxy with `insecureSkipHostCheck` + WebSocket headers
- Auto-TLS: systemd oneshot generating self-signed cert before nginx starts
- GUI access from dev machine: `ssh -L 9000:127.0.0.1:8385 user@server`
See PP-NIXOS-001 → Syncthing deployment section.

**system-app-config-and-nixos-flake-design.md** — NixOS multi-tier flake architecture:
`flake-parts` framework; modules: `bases/master.nix` + `bases/portable.nix`, `interfaces/cli.nix`,
`graphical/tiled.nix` + `plasma.nix` + `thin-client-rdp.nix`, `ai/compute-node.nix`.
LTSP fat clients share NFS `/nix/store`; Ollama model weights on NFS mount (not in initrd).
Separate dev flake: `nix develop ./dev-env`. See PP-NIXOS-001 → Flake architecture section.

**Dead-letter triage 2026-06-09 (between sessions 18/19)** — 744 `ai_identify` dead-letters
from Ollama HTTP 500 crash (~2026-06-08) reset via `tgw dead-letter --requeue-transient`.
Root cause: `batch_size` config key missing from `TgwConfig` in `config.py` → `ValidationError`
on load after config update. Fixed: `batch_size: int = 1` added to `TgwConfig`. Worker
restarted; recovery confirmed.

**eBay Developer Support (Track 4)** — eBay responded to the `buy.marketplace_insights`
scope request with 8 questions Dave must answer. See Track 4 Priority 1 for the response
action item.

**Blocked — not Round 4 (held for later rounds):**
Same blocker groups as Round 3 plus:
- PP-PLANDB-001 — design discussion needed before code; currently design-open
- PP-PORTABLE-CATALOG-001 P2+ — PP-PYIPC-001 ✅ done; Syncthing API + PERPLEXITY-006 result both available; **unblocked as of 2026-06-11**
- PP-VISION-001 P2+ — CLIP/embedding model requires GPU upgrade

#### Blocked — not Claude-ready (grouped by blocker)
- **Operator / host-level ops** — `PP-REMOTE-001` (Tailscale + tmux + SSH hardening + sudoers + claude-user decision); `PP-DEPLOY-001` full epic (usermod UID<1000, recursive chown, image bake, fresh-restore reboot — only the read-only audit check #16 is ready). *Unblock:* operator does the host work, then Claude can add reviewable config (tmux launcher, OSC52 helper).
- **Hardware / physical device** — `PP-MACRO-001` install+prove needs a 2nd keyboard, live desktop, and `keyd list-devices` hash (a static drift-validation test is the only code-only slice, and it doesn't advance the goal). *Unblock:* operator wires the dedicated keyboard + captures the `[ids]` hash.
- **Android device + push creds** — `PP-TASKER-001` (Tasker/Join apps, barcode-intent audit; even a server-side push backend needs an operator ntfy topic / Join key to validate). *Unblock:* operator audits phone intents + supplies a push URL/key.
- ~~**External research + creds/services** — PP-PYIPC-001~~ **✅ UNBLOCKED**: PERPLEXITY-005 research complete; Syncthing is live at `127.0.0.1:8384`; API key in `/opt/TGW/.local/syncthing/config.xml` (in-project). Libraries decided: `pyncthing` + custom `httpx` events consumer; `pydbus` for KDE Connect. No operator action needed — PP-PYIPC-001 is Claude-ready.
- **Design-open / architecture decision** — `PP-REVISION-001` live-listing revision. `ReviseFixedPriceItem` exists (`trading.py:266`) but only for SKU-label changes; `ebay_stage.py:6` calls itself "the stopgap until the full revision system is built." Open question (sparse-delta vs full-replacement) + depends on PP-SYNC-001 being authoritative + live token for any push. *Unblock:* Dave settles the delta-vs-replacement design; then a dry-run delta computer is a buildable first slice.

#### Stale-done reconciliation (doc-only corrections — several bundled into the slices above)
Audit caught the plan crediting shipped work as open and missing shipped tools. Corrections:
- **PP-MCP-001** — 10 MCP tools shipped (`tgw_dead_letter` added); plan table (≈L2139) lists 9. Registration-path docstring drift. *(bundled into rank 3)*
- **PP-SOLD-001** — Tier 3 `ebay-sweep` (`api.py:1517`) + Tier 4 webhook (`notifications.py` + `http_server.py:714` + `tgw setup-ebay-hooks`) are **DONE**; plan calls them "pending/future" (≈L831–842). *(bundled into rank 2)*
- **PP-VERIFY-001** — Phase 2 **DONE** with 27 passing tests + ~13 rules; plan marks it "Next" and claims 10 tests / 9 rules. "catalog-rebuild resets the hall pass" is moot — `catalog.py` never references `catalog_verified`. *(bundled into rank 19)*
- **PP-MC-001** — Phase 2 **DONE** (448-line `tgwitem` committed); §1144 subsection still shows it open. *(bundled into rank 20)*
- **PP-INTAKE-001** — P1 & P2 **DONE** (committed); §1601 says "to build". §1612 claims the template writes `fulfillment_policy_id` — **it never does; strike it.** *(bundled into rank 4)*
- **PP-CAPTURE-001** — `suggest`/`note`/`btw` + `suggest-edit` **DONE**; plan calls them "planned". *(bundled into rank 11)*
- **PP-HINT-001** — requeue / Browse enrichment / hint-trail / `hint --force` **DONE**; only `shipping_profile` remains. *(bundled into rank 8)*
- **PP-FULFILLMENT-001** — plan line ≈2208 falsely states `tgw picklist` exists. **Active landmine — corrected inline** + rank 7 builds the real command.
- **PP-GLOBALS-001** — "wait for PP-INTAKE-001 P2" (≈L1106) is satisfied/stale; the intake form already captures `weight_oz`. *(bundled into rank 1)*
- **PP-STRIKE-001** — core code **DONE**; Track-1 table + planning text said "Planned". *(bundled into rank 6)*
- **ISS-006** — `_USER_PROMPT_ENRICHED` is fully wired (`ai_identify.py:171–204`); issue is stale → **closed in ISSUES.md**.

### Track 1 — Round 5 (session 19/20)

**Guiding principle:** Process Gemini inbox findings into code. Address data quality issues.
Pipeline hygiene + Flutter backend gap.

**Input:** Round 4 all 7 DONE; 346 tests passing; 4 open todos (#36–39); 8 inbox files processed.

**Session 21 progress:** #36 DONE (433 tests passing; 121 items backfilled via `ebay_category_id`; catalog_rebuild enqueued). Todos remaining: #37–39 + #47–49.

**Session 22 progress:** #37 DONE (439 tests passing). Todos remaining: #38–39 + #47–49.

**Session 23 progress:** #38 DONE (455 tests passing). Todos remaining: #39 + #47–49.

| # | PP | Task | Size |
|---|----|------|------|
| ✅ 36 | PP-STORAGE-001 | `size_class` backfill — `tgw data-scrub --pass 2 [--write]`; 121 items populated via `ebay_category_id` reverse map; catalog_rebuild enqueued; 13 new tests (`test_scrub.py`); suite 433 — **DONE session 21** | S |
| ✅ 37 | PP-EDITOR-001 | `GET /api/health` — Bearer-auth; mirrors `check_all()` JSON + `dead_letter_count`; HTTP 503 on failure; 6 new tests; suite 439 — **DONE session 22** | S |
| ✅ 38 | — | `tgw alt-text <sku> [--model MODEL] [--dry-run]`: Ollama vision → `alt_text` + `seo_caption` in `draft_listing`; original photo archived to `data/history/ItemData/<sku>/` if not there; production photo renamed to `<sku>-alt.jpg`; 16 new tests (`test_alt_text.py`); suite 455 — **DONE session 23** | M |
| ✅ 39 | — | ~~Fix 25002 `Item.Country` dead-letter rejections~~ — **RESOLVED 2026-06-11 (ISS-001 closed)**: `availabilityDistributions` + `merchantLocationKey` fix (session 9) confirmed working; originally-affected items live via Inventory API. Session-23 25002-lookalikes were item-specifics validation errors on an already-live Trading-API item; all 15 stale dead-letters cleared | S |
| 40 | — | `category-groups.json` pricing calibration (GEMINI-005): update `electrical_fixtures`→12.50, `media_records`→13.50, `collectibles_pins_buttons`→10.50; run `tgw category-groups --reseed` | XS |
| 41 | — | `category-groups.json` store categories (GEMINI-006): populate `store_category` for `tools_hand`, `electronics_adapters_chargers`, `electronics_remotes`, `kitchen_utensils` | XS |
| 42 | — | Data scrub: scan `description_history` for "John F. Rider" and generic boilerplate contamination (GEMINI-004); report affected SKUs; strip contamination strings | S |
| 43 | PP-FULFILLMENT-001 | Standard Envelope constraint (≤0.25 in thick, uniform): wire into `_resolve_fulfillment_id()` as a size/category gate; add note to CATEGORY-QUIRKS.md | S |
| ✅ 44 | PP-CAPTURE-001 | `GET/POST /form/suggest` — punctuation-safe suggestion web form; plain HTML (no JS), network-trust like `/form/intake`; reuses `cmd_suggest()`; whitespace collapsed to keep one checklist line per entry; 5 tests; suite 480 — **DONE session 24 (uncommitted, pending review)** | S |
| 45 | — | `TGW-Quickstart.md` pipe examples: add `--skus-only` / stdin `-` / multi-SKU patterns; note `tgw enqueue-sku` queue-first path | XS |
| 46 | — | Ledger ops-query ergonomics (from runbook work 2026-06-10): `queue_job_history` has no `queue_name` (per-queue history needs `JOIN queue_jobs USING (job_id)`) and uses `created_at`; job columns are `payload_json`/`error_code`/`error_detail` (not `payload`/`last_error`). Fix: add SQL views to `queue/schema.sql` (e.g. `v_dead_letters`, `v_job_history` with queue_name) and/or a `tgw queue history` subcommand so operators stop hand-writing joins; `reference/runbooks/` already uses the correct join form | S |
| ✅ 47 | PP-SHELL-001 | **DONE 2026-06-11 (session 23).** Canonical hyphenated names adopted; deprecated aliases kept. `tgw search TEXT` added. Quickstart updated. Key findings: (1) `statusupdate VALUE SKUS...` — value-first is intentional for multi-SKU; kept as-is, documented. (2) `enqueue-sku QUEUE SKUS...` — queue-first is correct (you target a queue, not an item); quickstart was wrong and is now fixed. (3) `ebay-pull` has no scoping — deferred (needs design). (4) Nested-field CLI writes → HTTP PATCH / MC extfs path (PP-CONTEXT-001, not CLI). (5) `requeue` is ai_identify-only but generically named — leave for PP-SHELL-001 Tier 3. Canonical rename table: `titleupdate`→`update-title`, `locationupdate`→`update-location`, `verifiedupdate`→`update-verified`, `statusupdate`→`update-status`, `setshipping`→`set-shipping`, `whispertosuggest`→`whisper-suggest`. | M |
| ✅ 48 | PP-CONTEXT-001 | **DONE 2026-06-11 (session 23).** `tgw set-context <sku>` / `tgw get-context [--sku-only]` / `tgw clear-context`. Primary store: `runtime/state/current-item.json` `{sku, set_at, set_by}`. Compat symlinks (`/opt/TGW/CurrentItem`, `CurrentItem.json`) maintained atomically via temp+os.replace. Legacy symlink fallback preserved in `get-context`. `tgw_sku` → `tgw get-context --sku-only`. `tgwset` → `tgw set-context`. `set-template` updated to use `context.current_sku(cfg)`. 20 tests in `test_context.py`. `CurrentLocation` dropped (derive location from SKU via `tgw resolve`). | M |

### Track 1 — Round 6 (session 24)

**Input:** Round 5 fully drained except rows 40–43/45 (seeded as todos session 24). Suite 480.
Lint-policy incident (session 24): bare `ruff check` mutated 8 files because pyproject set
`fix = true` — root cause removed; see #49.

| # | PP | Task | Size |
|---|----|------|------|
| ✅ 49 | — | Lint policy hardening — **DONE session 24**: `fix = true` removed from pyproject (a bare `ruff check` must never mutate the tree; fixes are explicit via `ruff check --fix`); `systemd/history/` excluded (archived dead scripts, not lint-gated); the 8 pending isort autofixes kept and committed separately from feature work | XS |
| ✅ 50 | — | `tools/migrate_batch.py` **DONE session 26** — archived to `tools/archive/migrate_batch.py`; superseded by `ebay_sku_migrate` worker; added `tools/archive` to ruff exclude | S |
| ✅ 51 | — | `tools/repair_itemdata_json.py` **DONE session 26** — fixed Python 3.11 f-string backslash (lambda rewrite); removed unused `nxt`; ruff clean | XS |
| ✅ 52 | PP-DOCFLOW-001 | **Design session HELD 2026-06-11 (session 24)** — all four open questions settled by Dave; design recorded below; Phase 1 build seeded as todo | M |
| ✅ 53 | PP-DOCFLOW-001 | **Phase 1 build DONE 2026-06-11 (session 25)**: pm_intake ported to `call_model()` + `tgw-models.json` → `openrouter/google/gemini-2.5-flash`; actions: `no_change \| append_to_section \| file_document \| flag_for_review`; `new_section` demoted to review-flag; 4h submission-delay gate + `tgw admin-file [--now]`; `reference/FILING-LOG.md` audit trail; `inbox/review/` + `dev-workflow/research/` dirs; `pm_intake_delay_hours` config key; 19 offline tests | M |
| ✅ 56 | PP-DOCFLOW-001 | **Phase 2 DONE 2026-06-11 (session 26)**: `tgw.suggestions` module — `parse_pending()`, `classify_batch()` (1 LLM call via `suggestions_classify` → openrouter/gemini-2.5-flash), `apply_classifications()`, `format_report()`; `tgw classify-suggestions [--apply] [--limit N]`; dry-run default; `already_done` entries marked `[x]` on `--apply`; `todo` entries create DB todos; `plan_append`/`review_flag` report-only. 16 offline tests. | M |
| ✅ 57 | PP-PYIPC-001 | **DONE 2026-06-11 (session 26)**: `tgw.apis.syncthing` — `_parse_api_key()` from config.xml, `folder_status()`, `folder_is_idle()`, `list_folders()`, `scan_folder()`, `disk_events()` long-polling generator; `tgw.apis.kdeconnect` — `list_devices()`, `get_device_id()`, `ping()`, `send_text()`, `send_file()`, `push_clipboard()` via kdeconnect-cli; `syncthing_config_path`/`syncthing_url` config keys; `pyncthing>=0.1` in pyproject.toml; 25 tests | M |
| ✅ 58 | — | **`tgw history-index` DONE 2026-06-11 (session 26)**: `tgw.history_index` module; `index_archive_unindexed()` scans ~32K legacy Magento zips not in `archive-ebay-index.json` → `var/history-itemdata-index.jsonl` (sku/title/location/status/price/condition); `index_loose_csvs()` parses eBay-OrdersReport-*.csv → `var/history-loose-csv-index.jsonl`; `tgw history-index [--target ItemArchive\|loose-csv\|all] [--dry-run] [--limit N]`; smoke-tested production (54,683 zips); 13 tests. Run `tgw history-index --target all` in a screen session to populate | M |
| ✅ 54 | PP-BACKUP-001 | **Phase A build DONE session 25**: `tgw-db-backup` + `tgw-cloud-sync` + `tgw-secrets-backup` scripts + systemd units/timers in `etc/systemd/`; `check_backups()` in health.py + tests — **scripts exist, operator must install** | M |
| 55 | PP-BACKUP-001 | **Phase A operator items** (todo #61): approve plan ✅ done; remaining: gpg passphrase custody decision (off-machine!); install+enable the three timers; first manual cloud sync in an off-hours window; `rclone about dbukove:` quota check; A5 restore drill + record RTO times | M |
| 56 | PP-PRICING-001 | **Phase 1 (title-based)**: after `ai_identify` vision step, fire SerpApi `engine=google_shopping` with AI title → write `price_comps.shopping_search` (prices, p25, p50, count) to item JSON; feeds `suggest_price()` Stage 1.5. **Phase 2 (image-based)**: Bing Visual Search API multipart upload (no public URL needed) → visual product matches + prices → `price_comps.visual_search` + optional `ai_identify_result.lens_title` confidence override. Both phases run async after Ollama, graceful-skip if keys absent. Keys: `serpapi-credentials.json` + `bing-search-credentials.json`. Interim substitute while `buy.marketplace_insights` blocked. | M |
| 57 | PP-WEBAUTH-001 | `tgw set-web-password` CLI: prompt for new password (or `--password` flag), write `web_password` to `secrets_root/tgw-api-key.json` (preserve chmod 600), restart `tgw-http.service`. Add `tgw web-password-status` (shows whether custom password is set or falling back to API key, without revealing value). Document in `TGW-HTTP-API.md`. **Context:** web UI session auth added this session — password currently hardcoded via one-liner; this makes it self-service. | XS |
| 58 | PP-CANONICALIZE-001 | **Canonical inventory record promotion.** Two trust flows feed top-level canonical fields (`title`, `description`, `item_attributes`, `category`): (1) AI-confident path — `ai_identify` auto-promotes when confidence ≥ threshold; (2) operator-approval path — listing editor prompts "Update canonical inventory record?" on save; first approval defaults Yes (no approved record yet), subsequent saves default No (eBay tweaks assumed eBay-specific). Gate tracked by `content_approved_at` timestamp. After promotion, canonical fields are source of truth and editable directly; workers update their own blocks (`draft_listing`, `ebay_offer`) but do not overwrite locked canonical fields. | M |
| ✅ 59 | PP-PHOTO-001 | **Continuous sync infrastructure DONE 2026-06-26 (session 28)**: `bin/tgw-itemdata-sync` loop service (120 s, `--fast-list`, shared flock); `/opt/TGW/config/rclone.conf` TGW-owned remote `tgw-gdrive`; `nix/tgw/backup.nix` declares service + tmpfiles lock; `bin/tgw-cloud-sync` updated to use TGW config; `src/tgw/apis/gdrive_sync.py` sync status helper (`sync_status`, `files_synced_by`); status JSON written atomically each cycle | M |

### Track 1 — Round 7 (session 28, 2026-06-12)

**Produced by a full docs-tree gap analysis — see `plan/PLAN-round7-platform-gaps.md`**
(the reference spec for this round: what was designed-but-unbuilt, noted-but-never-planned,
and newly proposed). 14 Claude tasks + 2 Gemini/Antigravity tasks + 6 operator items seeded
into the tracker 2026-06-12 with `--source round7`. Highlights: sync-conflict resolution
worker (zero-data-loss), Ready state + rate-limited dole-out, AI usage ledger (cost per
item), alt-text batch via OpenRouter, computer-side intake, picklist/label PDFs, Taxonomy
category validation, `tgw report sales`, PP-PROMO-001 (new — markdown sale events on the
held `sell.marketing` scope, design-first). Three tasks are Aider-eligible.

**All four reserved discussions held + decided with Dave 2026-06-12 (session 28):**
PP-REVISION-001 = **sparse delta + pinned baseline** (drift gate at apply; dry-run delta
computer first); PP-PLANDB-001 = **Option C generated taskboard** (companion file
`plan/TGW-Taskboard.md`; DOCFLOW admin is the single write-gateway — Dave submits via
inbox/suggest only); PP-CLIP-001 = **dual-backend watcher** (X11 stable now, Wayland
first-class via `wl-paste --watch`; build after Qtile install); Aider = **committed** (amended
2026-06-12: used even with Antigravity as primary agent/agent manager; Antigravity-first trial
week stands as routing calibration). Decisions recorded in the respective PP sections +
next-process.md; follow-on todos #109–#118 seeded.

### Track 1 — Round 8 (session 32, 2026-06-15) — Web UI Rework

**Input:** 14 suggestions from Dave's live web interface review session 2026-06-15. Claude todo queue empty (AGY drained 3n/3o). Suite state ~637 tests (session 29 baseline; 3n/3o items completed by AGY).

**Guiding principle:** First real use of the web UI surfaces real gaps. Fix what's broken first (photo naming, ISS-013/014/015), then make each page operationally useful.

**Themes:** A = Photo fixes, B = Item detail overhaul, C = Inventory browse, D = Pipeline drill-down, E = Review Queue, F = Data/API fixes, G = Page clarification

| # | Theme | Task | Size |
|---|-------|------|------|
| 874 | A | **ISS-013 alt-text photo naming fix** — `tgw alt-text` must NOT rename the original photo; `<sku>-alt.jpg` = new companion/derivative only; scan ItemData for already-renamed originals and restore original filename; gallery sort: mtime-based, SKU-named files first | S |
| 875 | A | **Photo gallery UX** — lightbox on click (modal enlarged view); move-to-front / manual reorder button per photo; video items visually separated (different border + "VIDEO" label) in detail gallery | S |
| 876 | B | **Item detail page restructure** — cleaner section layout; all Inventory fields visible + inline-editable via PATCH; eBay section shows price/shipping policy/categories/store categories; clarify eBay Offer vs buyer-offer vs revision draft with better labels and a purpose banner for each section | M |
| 877 | B | **Pricing History link** — replace inline "Price source: ..." with expandable "Pricing History" link showing: price comps used (links if available), category-group floor/typical, operator override record, suggested vs accepted price history; price is the canonical inventory field that drives eBay | M |
| 878 | B | **eBay draft section fixes** — (1) show draft price (from `ebay_offer.price` if present); (2) fix `no_brand` false-positive — scan title against brand field before flagging; (3) add "Does Not Apply" / "Unknown" to `no_model` display | S |
| 879 | B | **Worker pipeline tooltips** — hover tooltip on each worker name in the pipeline jobs section of item detail; text from `TGW-Pipeline-Flow.md` worker descriptions | XS |
| 880 | C | **Inventory browse — status at a glance** — per-card additions: price (not just on detail), eBay status badge (Listed + eBayID as clickable link / Staged / Ready / Needs Review / Not Listed), missing-photo indicator | S |
| 881 | C | **Inventory browse — bulk selection** — checkbox per card; select-all button; sticky bulk-action toolbar: Re-identify / Reprice / Mark Ready / Mark Sold / Delete from eBay / Apply Draft; search+filter then select-all is the primary bulk workflow | M |
| 882 | D | **Pipeline page drill-down** — click on failed/dead_letter count → slide-out detail panel with job list (queue, error text, classify verdict); per-job Re-queue + Report buttons; also surface stuck active jobs (elapsed > 2× expected) | M |
| 883 | E | **Intake form purpose + UX** — add instructions banner ("Review and confirm this item before sending through the pipeline"); show pre-populated fields with current values; Pipeline Trigger section (re-identify / re-draft / stage buttons + confirm); clarify this is pre-pipeline review not raw data-entry | S |
| 884 | E | **Review Queue upgrade** — add shipping policy name, category, condition, condition description to each review card; filter + search bar; checkbox selection; bulk-approve + bulk-list-now + bulk-mark-ready actions | M |
| 885 | F | **ISS-014 qty validation** — guard in `items._write_field()` refusing qty < 0; `catalog-verify` rule `negative_qty` (critical); `tgw data-scrub --pass 3` (or `--fix`) repair: set qty=1 for any item with qty < 0 | S |
| 886 | F | **ISS-015 Best Offers rate limit** — call `GetAPIAccessRules` to get call budget; add rate limiting + exponential backoff to `get_best_offers()`; display friendly error + call-budget status in the UI | S |
| 887 | G | **Revisions page clarification** — add purpose banner: "Proposed changes from AI or operator review appear here before being pushed to eBay"; show example workflow on empty state; link to PP-REVISION-001 docs | XS |

**Block ordering (recommended):**
1. #874 (photo rename bug fix — ISS-013, breaks display for all alt-text items)
2. #885 + #886 (data/API fixes — quick wins)
3. #878 + #879 + #887 (XS/S polish — low risk)
4. #875 + #880 (gallery + browse status — M/S but high daily-use value)
5. #876 + #877 (item detail restructure — M, most impactful page)
6. #881 (bulk selection — M, completes the browse upgrade)
7. #882 + #883 + #884 (pipeline/review/intake pages — M each)

---

### PP-DOCFLOW-001 — PAM (Project Administration Manager) — LLM document + suggestion intake

**Naming decision (Dave, 2026-06-14, session 31):** The LLM project admin is officially named **PAM** — Project Administration Manager. Update all UI references (web PM chat, labels) to use "PAM" instead of "PM" or "pm_intake". Code module names unchanged (pm_intake, /api/pm/chat) for backward compat — surface name only.

**Status: PHASE 1 + PHASE 2 COMPLETE 2026-06-11 (sessions 25–26). Phase 3+ (admin skills expansion) is future scope.**

**Mental model (Dave, session 24):** model this tool as a **real-life project admin** — the
best ones always have the plan ready to be worked on: all docs filed and readily available,
**cross-indexed to the appropriate tasks**. Ours will just be better. When we move to
planning, everything — thoughts, notes, files, binaries — is collected and easily
accessible. It is an admin function, but a *knowledgeable* admin: it knows where or what a
doc is.

**Decisions (Dave, 2026-06-11):**
1. **Evolve pm_intake in place** — same worker/queue/unit, ported to the session-23
   dispatcher (`tgw.apis.llm.call_model`), action vocabulary extended. Compute is no
   longer a constraint: route to fast capable Gemini, "go overboard" — well under $1/mo
   at classification-prompt sizes. (Note: pm_intake is currently **enabled + active** on
   local Ollama — verified session 24; the remembered disable-to-reserve-compute is not
   in effect.)
2. **Review surface = the admin pattern**: filed docs land in the right vault location;
   anything uncertain goes to `inbox/review/` + a todo pointing at it. Any fall-through
   is cleaned up in the normal session-start ritual (the existing safety net).
3. **Trigger model — batched, not continuous:**
   - **Auto-run as planning prep** (before a planning/Claude session) and
   - **manually triggerable** (`tgw admin-file`) — e.g. after dumping a stack of research.
   - **Submission-delay window**: items must age N hours before absorption — gives the
     human submitter a chance to correct a hasty submission *before group resources are
     spent on it* (manual trigger can override with `--now`).
4. **Suggestions: batched at session start** (Phase 2) — the admin pre-classifies;
   Claude reviews dispositions instead of raw entries.
5. **Plan writes: append-only.** The cloud model may `append_to_section`; `new_section`
   and anything structural becomes a review flag. (This *tightens* current pm_intake,
   which can create sections today.)

**Scope notes from the session:** intake accepts anything — "a one word comment or a
folder full of docs and binaries." Binaries (photos, PDFs, zips) are in scope: filed by
type/context (the dispatcher already supports vision for image classification when
needed — later phase). Cross-indexing means filed docs get index entries linking them to
the relevant PP-* items / tasks, so planning sessions start with material attached.

**Phase 1 (MVP — build next; seeded as todo):**
- Port `pm_intake` from direct `ollama.chat()` to `call_model('pm_intake', ...)`;
  set `tgw-models.json`: `pm_intake → openrouter / google/gemini-2.5-flash`
  (Ollama stays the automatic fallback — frees CPU for vision/pipeline).
- Extend actions: `no_change | append_to_section | flag_for_review | file_document`
  (`new_section` demoted to a review flag per decision 5).
- `file_document`: move the file **verbatim** (never reflow) to
  `reference/` / `perplexity/` / `dev-workflow/research/`; append an entry to a filing
  log/index (`reference/FILING-LOG.md`: date, source, destination, related PP-*, model,
  confidence); optional one-line plan pointer (append-only).
- `flag_for_review`: move to `inbox/review/` + create a todo (agent claude or dave).
- Submission-delay gate (mtime-based, configurable, e.g. 4 h) + `tgw admin-file [--now]`
  manual trigger.
- Audit trail on every action (the `identification_history` pattern).

**Phase 2:** suggestions join the path — session-start batch pass pre-classifies
unprocessed SUGGESTIONS.md entries into todo / plan-append / review-flag dispositions for
Claude's review. Cross-index todo ↔ filed-doc links.

**Phase 3 (later):** binaries with vision classification; whole-folder submissions as one
unit; Antigravity batch jobs for large backlogs; **URL/URI submissions** (Dave 2026-06-11
18:25 — pm_intake accepts a link, fetches the content, files/classifies it like a doc).

**Admin skills expansion (Dave, 2026-06-11 18:23 — Phase 3+/4 scope):** like a real-life
admin, the project admin should also handle **presentation and aggregation**: spreadsheets,
charts, SKU groupings too complicated for the generic tgw filters, topic summaries,
research consolidations, basic project documentation work — on request ("even you could
request topic summaries"). Builds on the same dispatcher; routes to large-context
providers (Gemini/Antigravity) per PP-MULTIMODEL-001. Constraint: outputs are *artifacts
filed in the vault* (reports, sheets), never direct writes to curated data
(PP-REVISION-001 governing principle).

**Invariants:** writes only inside the plan vault (pm_intake's existing rule); originals
never destroyed (move, never rewrite-in-place; `processed/` archive retained);
flag-don't-guess on low confidence; plan writes append-only.

### Track 2 — Gemini CLI (large-context data + self-contained tasks)
**Status 2026-06-10 update**: Google One → **Google AI Plus** with compute-based limits (5-hour
refresh window). Keep individual Gemini tasks small and self-contained to avoid hitting the
compute cap. Also available: **Antigravity/Flow** ✅ CLI configured + v2.0 installed (2026-06-11).

**Antigravity CLI + OpenRouter config (inbox research 2026-06-11):** `agy` reads `~/.gemini/antigravity-cli/settings.json`. Add OpenRouter as a custom provider:
```json
{ "llm_providers": { "openrouter": { "base_url": "https://openrouter.ai/api/v1", "api_key": "YOUR_KEY", "default_model": "openrouter/free" } } }
```
Google Drive access via: (a) Google Workspace MCP (add to `~/.gemini/config/mcp_config.json`), or (b) rclone mount at a local directory (`cd ~/mnt/gdrive && agy`). The rclone approach is simpler since ItemData is already synced to GDrive. Both methods confirmed working in `agy` CLI.

**How to delegate to Gemini**: Write a self-contained task file with all needed context
baked in (no live system access). Drop data excerpts, schemas, and the task description.
Save result to `inbox/` for PM-intake.

| Task | Give Gemini | Expect |
|------|-----------|--------|
| ✅ PP-VERIFY-001 scaffold | done (session 13) — scaffold superseded by full Phase 1 implementation |
| ✅ Data scrub analysis | done (GEMINI-002, session 14) — completeness matrix, stall patterns, legacy scrub rules; 3 new verify rules implemented |
| ✅ Category-group quality review | done (GEMINI-001, session 14) — `electrical_industrial` split; ai_hints improved; `tools_hand` coherence noted |
| ✅ Flutter app scaffold | done (GEMINI-003, session 19) — Phases B+C+D at `apps/tgw_app/`; analyze clean; libsecret-1 build dep; BACKEND-NEEDED /api/health endpoint (todo #37) |
| ✅ Photo QA + alt-text pilot | done (GEMINI-004, session 19) — boilerplate contamination finding; alt-text viable via Ollama vision; sidecar naming confirmed |
| ✅ Pricing data analysis | done (GEMINI-005, session 19) — 3 calibration edits (see Round 5 #40); reseed reminder; tier pattern notes |
| ✅ Marketing/category insights | done (GEMINI-006, session 19) — store category mappings; zero-inventory high-velocity list; SEO keyword opportunities |
| ✅ TGW camera app design | done (gemini todo #115, session 29) — full Flutter scaffold proposal at `reference/PP-INTAKE-002-camera-app-design.md`; mobile_scanner + flutter_tts + Riverpod + Foldio360 root bypass + BLE direct control; Dave reviews before build |
| ✅ xmouse replacement design | done (gemini todo #116, session 29) — Flutter architecture survey at `reference/PP-INTAKE-003-xmouse-replacement-design.md`; flutter_rfb (Apache-2.0 VNC) + dartssh2 + flutter_inappwebview; 3-phase roadmap; Dave reviews before build |
| ebay_draft aspect fill audit | Grep of aspect fill rates per category | Which categories have worst specifics coverage; tuning recommendations |
| **AI conversation history consolidation** | Dave's conversation history with AI assistants (Claude, Perplexity sessions) | Organize + consolidate into structured reference; **plan scope with Dave before executing** (session 10 note) |
| ✅ **Data/archive history consolidation** | GEMINI-007 (2026-06-10) — **CRITICAL: MasterArchive I/O errors detected** (`/dev/sdc5`). `ls`/`du` work (cached dir entries), but `cat`/`touch` fail with EIO. **Operator must run `dmesg`, `umount /media/tgw/MasterArchive`, `fsck /dev/sdc5` before any indexing**. Folder inventory: `ItemData/` 584G (1.1M files, KEEP-INDEX), `job_archive/` 371G (KEEP-COLD), `ItemArchive/` 163G 54K zips (KEEP-INDEX, only 40% in archive-ebay-index.json), `magento/` 129G (KEEP-COLD), `eBay/` 60G (KEEP-COLD), `GarageSale/` 33G (KEEP-COLD), `ItemCreation/` 8.8G drafts (MIGRATE). Cleanup order: (1) fix mount, (2) consolidate zips, (3) index loose CSVs, (4) complete ItemArchive index to 100%, (5) offload cold data. `tgw history-index --target <folder>` design sketch included. See GEMINI-007-result.md. | |

### PP-DATALEARN-001 — Gemini Data Mining + AI-Calculated Fields

**Architecture principle (session 18):** All Gemini data reads/writes must go through the tgw-api fence. Gemini gets a task file with context baked in; it calls tgw-http endpoints or produces structured output for PM-intake. No direct filesystem or DB access from external AI tools. This extends the "tgw-api is the fence" settled architecture to the external AI layer.

**Task queue (Track 2):** See table above — pricing analysis, marketing insights, history consolidation, category quality review.

**Alt-text pipeline research (session 18):** Alt-text for item photos is a future enrichment opportunity (accessibility + SEO in external surfaces). Research links:
- https://medium.com/@petter.eckerbom/building-an-ai-multilingual-alt-text-pipeline-thats-fast-and-open-source-032982a5170c
- https://github.com/lukeslp/alt-text-local-llm (local LLM variant — compatible with our Ollama stack)
- https://surfai.app/blog/best-ai-image-description-generator-tools (survey)

Alt-text can feed `draft_listing.description` enrichment and future accessibility features. Track for Track 2 / PP-SEO-001 Phase 5+ when compute allows.

**Alt-text file-naming convention (session 19 — Dave 17:47):** When alt-text generation lands,
adopt a `<SKU>-alt.jpg` naming convention for the associated/derivative photo (sidecar to the
primary `<SKU>....jpg`). Wire the convention into the alt-text writer (Claude todo #38 `tgw
alt-text`) and reflect it in GEMINI-TASK-004's output spec. ⚠ Intent slightly ambiguous — confirm
with Dave whether `-alt.jpg` is (a) a renamed/secondary image file, or (b) the naming for an
alt-text-annotated derivative — before implementing the writer.

**Alt-text provider strategy (session 21–22):** Use Antigravity/OpenRouter LLMs for
alt-text in batches to offload Ollama (CPU-only, slow). Google Drive rclone sync in place
for ItemData — available for cloud provider access. Best free vision models on OpenRouter
(inbox research 2026-06-11): `google/gemma-4-31b-it:free` (top-rated, spatial awareness),
`google/gemma-4-26b-a4b-it:free` (MoE, fast), `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`
(scene description). Ultra-cheap paid: `google/gemini-1.5-flash` (~$0.075/M tokens),
`meta-llama/llama-3.2-11b-vision-instruct` (~$0.05–0.10/M). Use `openrouter/free` to
auto-route to the shortest-queue free vision model. Rate limit: ~20 req/min on free tier.
Prompt template: "Act as an expert in web accessibility and SEO. Analyze this image and
provide a concise, descriptive alt-text (max 150 characters). Describe the main subject,
setting, and context accurately without using fluff words like 'image of'..."

**Zero-bandwidth GDrive→EPS upload strategy (inbox research 2026-06-11):** ItemData is rclone-synced
to Google Drive. Photos can flow directly from Drive to eBay Picture Services without local download:
eBay's `UploadSiteHostedPictures` (Trading API) accepts image data via API upload; Antigravity CLI
(`agy`) can be scripted to fetch image bytes from Drive API and POST to EPS in one pass. Requirements:
eBay Trading API access (currently have `sell.inventory` + Trading credentials), Google Drive API
scope in OAuth client. Relevant for PP-PHOTO-001 (bulk photo re-upload / migration) — evaluate when
Planning that phase. Source: `inbox/queued/20260611T093715-antigravity-remote-execution-direct-gdrive-to-eps.md`.

### PP-PHOTO-001 — GDrive Zero-Bandwidth Photo Pipeline (Phase 0 complete 2026-06-26; Phases A+B open)

**Goal:** Pass all item photos to Gemini in `ebay_draft` via GDrive URLs (no base64 upload) for
major listing quality jump; use same pattern to replace deprecated EPS upload with zero-bandwidth
GDrive→eBay flow.

**Infrastructure completed (2026-06-26):**
- `bin/tgw-itemdata-sync` — continuous rclone service; syncs `ItemData/` + all item JSONs to `tgw-gdrive:TGW/data/ItemData` every 120 s; `--fast-list` for 55k-folder efficiency; shared flock with daily cloud-sync to prevent parallel Drive access
- `/opt/TGW/config/rclone.conf` — TGW-owned rclone config (tgw:tgw 600); remote `tgw-gdrive` (dbukove account); token auto-refreshes in place
- `nix/tgw/backup.nix` — `tgw-itemdata-sync.service` declared as persistent systemd service (`Restart=always`); `tmpfiles.d` rule recreates lock file on boot
- `bin/tgw-cloud-sync` — updated to use TGW config + shared flock
- `src/tgw/apis/gdrive_sync.py` — `sync_status()`, `last_completed_at()`, `files_synced_by(mtime)` helpers; workers read `/opt/TGW/var/log/rclone-itemdata-sync-status.json` to know when Drive is fresh enough to use GDrive URLs safely
- Status file schema: `{state, cycle, started_at, completed_at, pid}` — written atomically at each cycle start/end

**Remaining (todos #1064, #1065):**
- Phase A (#1064, p25): `ebay_draft` multimodal — pass photos as GDrive URLs to `gemini-2.5-flash`; temp-public grant/revoke per photo; needs Google Cloud project + Drive API OAuth creds in `secrets_root/gdrive-token.json`
- Phase B (#1065, p35): zero-bandwidth EPS upload — GDrive direct URL → `Inventory API imageUrls[]` → eBay CDN fetches; replaces deprecated Trading API upload

**LLM routing principle (session 21–22):** OpenRouter provides built-in meta-model endpoints:
`openrouter/auto` (NotDiamond-powered, routes by task complexity; session-sticky for multi-turn),
`openrouter/free` (rotating free models; vision-aware), `openrouter/fusion` (multi-model
consensus panel). Open-source self-hosted options: **LiteLLM** (Python proxy, 100+ models,
fallback/load-balance; drop-in OpenAI-compatible), **RouteLLM** (LMSYS classifier, escalates
to expensive models only when needed), **Bifrost** (Go, near-zero latency). Recommended path:
start with `openrouter/free` for alt-text (zero cost, auto-routed); add LiteLLM when mixing
local Ollama + cloud providers in one pipeline.

### Track 3 — Perplexity (live web research, cited sources)
Research briefs in `docs/TGW-Plan-Vault/perplexity/`. Paste brief into Perplexity → save result as `.md` to `inbox/` for PM-intake.
**⚠ Perplexity subscription expires ~2026-12 — run all remaining briefs before then.**

| Brief                          | File                                | Priority | What it unblocks                                 |
| ------------------------------ | ----------------------------------- | -------- | ------------------------------------------------ |
| eBay API scope expansion       | `PERPLEXITY-001-ebay-scopes.md`     | HIGH     | PP-REPRICER-001, PP-SEO-001 Phase 3+6            |
| eBay Cassini 2025–2026         | `PERPLEXITY-002-cassini-seo.md`     | HIGH     | PP-SEO-001 tuning, listing quality strategy      |
| Sold price data alternatives   | `PERPLEXITY-003-sold-price-data.md` | HIGH     | PP-REPRICER-001 unblock if MI scope stays closed |
| Third-party integration status | `PERPLEXITY-004-integrations.md`    | MEDIUM   | IGDB, Whisper.cpp, Discogs, Go-UPC               |
| ✅ Library & API audit         | `PERPLEXITY-005-result.md`          | DONE         | Full result processed (session 19/20); see PP-PYIPC-001 + PP-LOOKUP-001 + PP-FULFILLMENT-001 updates |
| ✅ Flutter offline sync        | `PERPLEXITY-006-flutter-offline-sync Result.md` | DONE | PP-PORTABLE-CATALOG-001 P2 — snapshot+copy pattern; sqflite stack; Dio+dio_smart_retry; outbox table design |

### Track 4 — Operator (Dave must act to unblock)

#### ✅ Hardware alert resolved — MasterArchive drive (2026-06-11)
`/dev/sdc5` (`/media/tgw/MasterArchive`) had I/O errors (EIO on reads, GEMINI-007).
**Repaired by Dave 2026-06-11.** `tgw history-index` built and smoke-tested (session 26). Run `sudo -u tgw tgw history-index --target all` in a screen session to populate the index (~hours for 32K zips).

---

#### ✅ Done
- [x] `velocity_stats` worker enabled (2026-06-05)
- [x] 2-year eBay sold CSV confirmed as maximum available — archive tombstone ceiling accepted
- [x] **ISS-009 downgraded (session 16)** — production keyset active; `tgw restart-ebay-token` if dead-lettered; no longer a hard blocker

---

#### Priority 0 — NixOS migration prep (session 16 decision; updated session 18)

NixOS is the **committed target OS**. Migration is not immediate — do when ready. Recommended path:

**Step 1 — Spare machine first (session 18):**
- [ ] Identify the spare intake support machine
- [ ] Install NixOS on it using the `flake.nix` already produced; configure as client (portable catalog, services not started)
- [ ] Use it to build familiarity, discover any tool gaps, and validate the flake without risk to the main production machine
- [ ] When client setup is solid → promote to tgwOS 2.0 server or full replacement for main machine

**Step 2 — Final MX safety net:**
- [ ] Use MX Snapshot to bake a bootable ISO of the current working system before any migration touches the main machine. This is the permanent safety net.

**Step 3 — VM validation:**
- [ ] Validate `flake.nix` + `nix/tgw.nix` in a NixOS VM (watch item: `python3Packages.mcp` in nixos-24.11)

**Step 4 — Main machine cutover (when ready):**
- [ ] Run `nixos-install` on new partition; keep MX as fallback until `tgw health` fully green on NixOS

---

#### Priority 0b — Qtile WM install

- [x] **Install Qtile window manager** (PP-WM-001):
  ```bash
  bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/qtile/install.sh
  ```
  Installs: `qtile`, `xclip`, `dmenu` (via apt); symlinks `~/.config/qtile/{config.py,tgw_widgets.py}`
  from repo; copies tgw-http API key to `~/.config/tgw/api-key` for bar widgets.
- [x] Log out → select **Qtile** at SDDM/LightDM session list → log back in
- [x] Verify bar shows: workspaces, W:N/N health, Q:✓ queue indicator, clock
- [ ] Test Super+T → TGW mode (bar shows `[ TGW ]`); press `h` for health, Escape to exit
- [ ] Test F12 scratchpad terminal toggle
- [ ] Edit `~/.config/qtile/autostart.sh` if compositor (picom) or notifier (dunst) is desired

---

#### Priority 1 — eBay Developer Account (new keyset + scope requests)

**Strategy:** Request a fresh keyset (new App ID / Cert ID / Dev ID) with all desired scopes
applied at once. Avoids piecemeal scope expansion later. See complete desired scope list below.

**Status 2026-06-05 ✅:** New keyset requested. All desired scopes requested including
`buy.marketplace_insights`. Awaiting eBay approval. Portal request flow has changed from
what's documented below — steps below are reference only; follow current portal UI when
updating credentials after approval.

**Status 2026-06-10 update:** eBay Developer Support responded to the `buy.marketplace_insights`
scope request with **8 questions** Dave must answer before the scope can be approved.

**Status 2026-06-14 update (todo #142):** Follow-up check confirmed no response yet recorded in the repo. Direct DS portal access is restricted to the authenticated operator (Dave). Research indicates `buy.marketplace_insights` remains highly restricted for independent developers.

**Operator Action (todo #79):** Check the eBay DS portal inbox. If unanswered, provide these details (typical eBay questionnaire):
1.  **App ID:** `DaveBuko-DaveBuko-P-66170566` (Production keyset)
2.  **Business Overview:** Trader Grim's Warehouse (TGW) — independent resale inventory automation.
3.  **Business Model:** Internal inventory management and automated repricing (no data redistribution).
4.  **Target Regions:** eBay US (primary), UK, DE.
5.  **Website URLs:** Internal-only (Tailscale/VPN); private tool.
6.  **User Experience:** Automated price adjustments based on sold-price p25/p75; no public data display.
7.  **Sales Volume:** ~55K active items; aiming for 20% volume growth through market-aware pricing.
8.  **Integration Details:** Currently using Trading API (photos/orders) and Sell Inventory/Account APIs.

If rejected, pivot to `Browse API` (item_summary/search) as the pricing floor/p25 proxy (noted as PP-PRICE-003).

- [ ] **Review and respond to eBay Developer Support message** — answer the 8 questions about the use case for `buy.marketplace_insights` (automated pricing engine, resale platform, no redistribution of sold-price data to third parties). Be specific: automated repricing, TGW internal use only, ~55K items, eBay seller account DaveBuko-Webkulap.


⚠ When new keyset arrives: update `secrets_root/ebay-credentials.json`, update
`tgw-api-config.json` scopes to match approved scopes only, then re-run OAuth.

- [x] New keyset requested via developer.ebay.com (2026-06-05)
- [x] All desired scopes requested including `buy.marketplace_insights` (2026-06-05)
- [ ] Receive approval + credentials from eBay
- [ ] Update `secrets_root/ebay-credentials.json` with new App ID / Cert ID / Dev ID / RU name
- [ ] Re-run OAuth: `sudo -u tgw BROWSER=/usr/bin/firefox python3 .../get_access_token.py`
- [ ] Restart all eBay workers after new token is live

**Old instructions (portal UI has changed — reference only):**
- Go to https://developer.ebay.com → My Account → Application Keys → **Create new keyset**
  - App name suggestion: `TGW-Automation-v2` or similar
  - Note new App ID, Cert ID, Dev ID — replace in `secrets_root/ebay-credentials.json`
- On the new keyset, request **all scopes in the desired list** (see below) via the "Get a Token" / OAuth consent flow and the scope editor
- For `buy.marketplace_insights` — **this requires a separate contact** (limited release):
  - Go to https://developer.ebay.com/support → contact Developer Support
  - Frame: "We are a private resale automation platform (eBay seller: DaveBuko-Webkulap) automating inventory pricing and listing management. We need `buy.marketplace_insights` to power our automated pricing engine using actual sold-item data rather than active-listing prices."
  - Reference: Marketplace Insights API docs at developer.ebay.com/api-docs/buy/marketplace-insights
- [ ] Update `secrets_root/ebay-credentials.json` with new keyset values after approval:
  ```json
  {
    "app_id": "...",
    "cert_id": "...",
    "dev_id": "...",
    "ru_name": "..."
  }
  ```
- [ ] Re-run OAuth flow to get a new user token against the new keyset:
  `sudo -u tgw tgw health` — confirm token active
- [ ] Restart all eBay workers after new token is live:
  ```
  sudo systemctl restart tgw-worker@ebay_legacy_sync.service
  sudo systemctl restart tgw-worker@ebay_sync.service
  sudo systemctl restart tgw-worker@ebay_price_reducer.service
  sudo systemctl restart tgw-worker@ebay_sku_migrate.service
  ```

##### Complete desired scope list for new keyset

| Scope                                | Have | Priority | What it enables                                                    |
| ------------------------------------ | ---- | -------- | ------------------------------------------------------------------ |
| `sell.inventory`                     | ✅    | core     | Create/update/delete inventory items and offers                    |
| `sell.account`                       | ✅    | core     | Fulfillment policies, merchant location, payment policies          |
| `sell.marketing`                     | ✅    | core     | Promotions, campaigns                                              |
| `buy.marketplace_insights`           | ❌    | **HIGH** | Sold price data → PP-REPRICER-001                                  |
| `commerce.catalog.readonly`          | ❌    | **HIGH** | EPID lookup by UPC/EAN → PP-SEO-001 Phase 3                        |
| `sell.analytics.readonly`            | ❌    | **HIGH** | Per-listing impressions/clicks → PP-SEO-001 Phase 6                |
| `sell.fulfillment.readonly`          | ❌    | medium   | Read orders via REST (supplements Trading API GetOrders)           |
| `sell.finances.readonly`             | ❌    | medium   | Payout/financial data for accounting and reconciliation            |
| `sell.stores.readonly`               | ❌    | medium   | Read eBay store category tree → PP-STORE-001                       |
| `sell.reputation.readonly`           | ❌    | low      | Feedback score tracking and monitoring                             |
| `commerce.notification.subscription` | ❌    | low      | REST-based webhook event subscriptions (future alt to Trading API) |

---

#### Priority 1b — TGW MCP Server registration (2 min)

PP-MCP-001 code is **done** (`src/tgw/mcp_server.py`, 9 tools). Needs one manual step to activate
in Claude Code because Claude cannot self-modify its own settings:

1. Open `~/.claude/settings.json` in your editor
2. Add the `"mcpServers"` block (merge with existing content):
   ```json
   {
     "model": "opusplan",
     "theme": "dark",
     "mcpServers": {
       "tgw": {
         "command": "sudo",
         "args": ["-u", "tgw", "/opt/TGW/.venvironments/tgw/bin/python", "-m", "tgw.mcp_server"],
         "env": {}
       }
     }
   }
   ```
3. Restart Claude Code — the `tgw_*` tools will appear in future sessions.

**What this unlocks:** Claude can query live queue state, item data, health, and TODO items
mid-session without shell escapes. Makes future debugging sessions significantly faster.

---

#### Priority 2 — API credentials (15–20 min each, each unlocks a lookup source)

- [ ] **IGDB** (video game lookups) — ⏳ App registered 2026-06-05; key not appearing in portal yet:
  1. Go to https://dev.twitch.tv → Log in with Twitch account (create if needed)
  2. Register new application: Name=`TGW`, OAuth Redirect=`http://localhost`, Category=`Other`
  3. Copy Client ID + generate Client Secret
  4. Write: `sudo -u tgw nano /opt/TGW/secrets/igdb-credentials.json`
     ```json
     {"client_id": "...", "client_secret": "..."}
     ```
  5. `sudo chmod 600 /opt/TGW/secrets/igdb-credentials.json`

- [x] **Discogs** (music/vinyl lookups) — ✅ Done 2026-06-05:
  1. Go to https://www.discogs.com/settings/developers
  2. Click "Generate new token"
  3. Write: `sudo -u tgw nano /opt/TGW/secrets/discogs-credentials.json`
     ```json
     {"personal_access_token": "..."}
     ```
  4. `sudo chmod 600 /opt/TGW/secrets/discogs-credentials.json`

- [x] **Go-UPC** — ❌ No free tier available (2026-06-05); skip for now; upcitemdb + go-upc paid plan if needed later:
  1. Go to https://go-upc.com/api → sign up for free tier
  2. Copy API key
  3. Write: `sudo -u tgw nano /opt/TGW/secrets/go-upc-credentials.json`
     ```json
     {"api_key": "Bearer <your-token>"}
     ```
  4. `sudo chmod 600 /opt/TGW/secrets/go-upc-credentials.json`

- [x] **upcitemdb** — ✅ Free tier (100/day) works keyless; no credential needed; code already handles this:
  1. Go to https://www.upcitemdb.com/api → sign up
  2. Write: `sudo -u tgw nano /opt/TGW/secrets/upcitemdb-credentials.json`
     ```json
     {"api_key": "..."}
     ```
  3. `sudo chmod 600 /opt/TGW/secrets/upcitemdb-credentials.json`

- [ ] After any credential added: `sudo -u tgw tgw health` — confirm no errors

---

#### Priority 3 — Perplexity research (copy-paste, save result to inbox)

Briefs are in `docs/TGW-Plan-Vault/perplexity/`. Open each in Obsidian, paste the prompt into
https://perplexity.ai, save the result as `PERPLEXITY-001-result.md` etc. into
`docs/TGW-Plan-Vault/inbox/` — PM-intake will file it automatically.

- [ ] **PERPLEXITY-001** — eBay scope expansion (do this first; informs Priority 1 above)
- [ ] **PERPLEXITY-002** — Cassini SEO 2025–2026
- [ ] **PERPLEXITY-003** — Sold price data alternatives
- [ ] **PERPLEXITY-004** — Third-party integration status (Whisper.cpp, Discogs, IGDB, Go-UPC)
- [ ] **PERPLEXITY-005** — Library audit (Syncthing Python client, KDE Connect DBus, USB scale HID)

**PP-PERP-AUTO-001**: when briefs pile up, use ydotool semi-automation to reduce copy-paste overhead.
See PP-PERP-AUTO-001 section for design.

---

#### Priority 3b — Tasker / Join evaluation (15–30 min)
- [ ] Compare Join vs KDE Connect for clipboard relay and push notifications; document findings in inbox
- [ ] Identify 3–5 highest-value Tasker automation opportunities from PP-TASKER-001 — start with barcode scan → tgw-http intake

---

#### Priority 4 — Physical inventory and Seller Hub

- [ ] **eBay sweep** — generate ambiguous-status checklist for physical review:
  ```
  sudo -u tgw tgw ebay-sweep --output /opt/TGW/var/ebay-sweep.md
  ```
  Then open `/opt/TGW/var/ebay-sweep.md` in Obsidian; work through Group A (active eBay / unclear local) first
  
- [ ] **Wrong shipping profiles** — 9 listings with FRE instead of FC4.
  Seller Hub: Listings → search by Item ID → Edit → Shipping → select FC4 (199931446015)
  - [ ] 327195083346  - [ ] 327195083374  - [ ] 327195083408  - [ ] 327195083423
  - [ ] 327195083451  - [ ] 227372145582  - [ ] 327195085940  - [ ] 227372145665
  - [ ] 227372145712

---

#### Priority 5 — Infrastructure

- [ ] **Second keyboard** → connect → install macroboard (PP-MACRO-001):
  ```
  keyd.rvaiya list-devices   # find the unique ID for the macroboard keyboard
  sudo nano /opt/TGW/src/trader-grims-warehouse/etc/keyd/tgw-macroboard.conf
  # replace "413c:2105" in [ids] with the full unique ID
  sudo cp .../etc/keyd/tgw-macroboard.conf /etc/keyd/
  sudo systemctl reload keyd
  # Test: Caps Lock on macroboard → LED changes
  ```

- [x] **Tailscale** ✅ installed 2026-06-11 (PP-REMOTE-001):
  Tailscale installed and configured. Verify `tgw-http` reachable over Tailscale from remote
  devices; verify `tgw-macro` works over SSH. SSH hardening (key-only, sudoers) still open.

- [ ] **eBay webhook endpoint** (PP-SOLD-001 Tier 4 — reduces sold-detection latency from daily → seconds):
  First check if you have a static public IP:
  ```
  curl -s https://ifconfig.me && ip route get 1.1.1.1 | awk '{print $7; exit}'
  ```
  Same → Path A (nginx + certbot). Different → Path B (Cloudflare Tunnel, works behind NAT).
  - **Path A** (static public IP):
    ```
    apt install nginx certbot python3-certbot-nginx
    cp /opt/TGW/config/nginx/ebay-webhook.conf /etc/nginx/sites-available/tgw-webhook
    # edit server_name to your actual subdomain (e.g. hooks.yourdomain.com)
    ln -s /etc/nginx/sites-available/tgw-webhook /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
    certbot --nginx -d hooks.yourdomain.com
    ```
  - **Path B** (behind NAT / dynamic IP):
    ```
    sudo bash /opt/TGW/config/nginx/cloudflared-setup.sh
    # edit /etc/cloudflared/config.yml — replace REPLACE_WITH_YOUR_SUBDOMAIN
    # add CNAME in ZoneEdit: hooks.yourdomain.com -> <tunnel-id>.cfargotunnel.com
    systemctl start cloudflared && systemctl enable cloudflared
    ```
  - Add `dev_id` to `/opt/TGW/secrets/ebay-credentials.json` (from developer.ebay.com → My Account → Application Keys → DevID field):
    `"dev_id": "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"`
  - Register URL: `tgw setup-ebay-hooks --url https://hooks.yourdomain.com/webhooks/ebay/notification`
  - Verify: `tgw setup-ebay-hooks --check`
  - Restart: `systemctl restart tgw-worker@ebay_legacy_sync.service` (and tgw-http)

---

#### Priority 6 — External AI tooling (PP-MULTIMODEL-001)

- [x] **markmap-cli** ✅ INSTALLED (2026-06-11) — `markmap-cli` now available. Test: `markmap docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md --no-open -o /tmp/plan.html`
- [ ] **nvm + npm** (needed for other JS tooling if required later):
  ```
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  # restart shell or: source ~/.bashrc
  nvm install --lts
  ```

- [x] **Gemini CLI** ✅ INSTALLED (2026-06-06) — `gemini` available in PATH; excellent for
  large-context data tasks; free with Google Drive subscription. Elevated to Track 2 primary
  for data analysis and self-contained scaffold tasks. See `## Work Tracks § Track 2`.

- [ ] **⚠ Perplexity expiry ~2026-12** — subscription expires in ~6 months. Run all remaining
  research briefs before expiry:
  - [x] PERPLEXITY-001 — eBay sold price API
  - [x] PERPLEXITY-002 — pricing strategy
  - [x] PERPLEXITY-003 — barcode lookup
  - [x] PERPLEXITY-004 — IGDB/Discogs APIs
  - [ ] **PERPLEXITY-005** — TGW library audit (brief ready at `perplexity/PERPLEXITY-005-library-audit.md`)
  Note from Dave (2026-06-06): Perplexity is also a capable Python programmer — it designed the
  state machine architecture. Use it for architecture research too, not just web lookups.

---

