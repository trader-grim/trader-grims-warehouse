# TGW Handoff — rolling (last 2 sessions + current risks)

**Rules for this file (R3.2, session 42):** hard cap ~150 lines. Holds ONLY: current
risks, the last two sessions' summaries, and the recommended next sequence. When a new
session is added, the oldest moves to `archive/SESSION-LOG.md`. Pre-redraw handoff
(v5 + all session logs): `archive/handoff-v5-2026-07-02-preredraw.md`.

Source-of-truth ranking: `tgw todo` (canonical tasks) → `TGW-Master-Plan.md` (spec/
status) → `reference/` docs. Tracker beats plan when they disagree.

---

## Current risks (ranked)

0a. **OPEN (todo #1115) — ebay_upload silently masks partial photo-upload failure,
    and a leftover redraft-loop backlog re-exhausted ebay_eps quota 3 days running
    (07-01/02/03)**. `ebay_upload.py`'s completion guard only fails if ZERO photos
    exist, so quota-blocked photos get silently dropped and logged as "success ―
    0 new". Backlog (2,715 stale retry_wait jobs, ~2,514 legacy SKUs, left behind by
    the #1107 loop) auto-requeued every ~6h and raced the worker at every midnight-PST
    quota reset, burning the full daily EPS budget before real work ran. Backlog
    CANCELLED 2026-07-03 (Dave authorized); code fix still open. `tgw202606021133367`
    still short 17/26 photos — needs a decision on how to finish it (see
    `dev-workflow/research/` session-43 note). Full detail:
    `inbox/DONE-ebay-photo-desync.md` (or `INPROGRESS-` if not yet closed).
0. **RESOLVED s42 evening (todo #1107, closed)** — the R1.3 requeue test exposed a
   chain that was diagnosed to root cause: the http PATCH endpoint's
   auto-redraft-on-draft_listing-change fired on WORKER fence patches too, creating an
   infinite draft→patch→redraft loop (one SKU: 287 draft jobs; 2 live listings PUT to
   eBay every ~90s for hours; the all-day 4-jobs/min queue drip). **Fixed**: fence
   clients send `X-TGW-Caller`; auto-redraft is operator-edits-only. The feared price
   reverts NEVER reached eBay (capture ground truth: only the 2 loop SKUs were PUT;
   the 5 flagged items are legacy-Item# and stage always skipped them). Local damage
   fixed: **784 items** carried stale pre-s41 draft prices above their live markdown —
   backfilled from the live mirror (before/after in `var/backups/s42-price-backfill/`)
   + a never-raise clamp added to ebay_stage (C5-extended, `allow_price_raise` to
   override, 4 tests). All workers running again.

1. **No backup running** — Postgres work ledger (todos + job history) is NOT
   re-derivable from ItemData. PP-BACKUP-001 operator todos #61/#146/#147. Weeks old.
2. **RESOLVED (todo #1102, closed)** — test suite repaired; full-suite state as of
   session 48 (2026-07-06 evening): 1874 pass, 10 fail (all pre-existing/unrelated:
   google_direct→openrouter rollback + pricing-invariant tests), 1 skipped. Full
   `pytest -q` is meaningful again, not just targeted runs.
3. **RESOLVED s45 (2026-07-04/05 night): ebay_draft 402 pile fully drained.**
   Final pass 2,656/2,658 succeeded (99.92%); day total ~6,500 jobs, ~$1.08
   OpenRouter spend. Only failures: 4 corrupt-photo SKUs (Feb-2022 migration
   truncation — recovery roster in #1145 note; fleet integrity sweep running
   on a1131, todo #1154). dead_letter table rows are historical (D4 clones).
4. **Live-fire gates unexecuted** — listeditor revision apply (R1.1) and action
   console operator test (R1.2) are the current critical path; everything else waits.
5. **todo #1077** — orphaned bad-SKU offer keeps ebay_sync on per-SKU fallback
   (health red). Dave must contact eBay support.
6. **15 Syncthing conflict files** in the vault (master-plan edit races 07-01/02).
   NOTE: the plan was redrawn s42 — resolve conflicts in favor of the new plan; the
   pre-redraw content is archived.
7. **Thermal hook not installed** — agent shell commands are not yet blocked at
   THROTTLE/SHUTDOWN; harness denied agent self-modification; needs Dave's explicit
   authorization or manual file drop (script in s42 transcript/inbox note).

---

## Session 47 — 2026-07-06 (flake decouple · audit#1143 nix mitigation · todo consolidation · router research)

Standing rule encoded: **iterated-on tools stay out of the flake** (Dave —
rebuild risk + usage-cost tax not worth it while a tool is still moving).
Memory: `feedback-flake-minimal-surface`.

- **Hermes/Aider decoupled from `~/tgw-flake`** (todo #1227 DONE): Hermes'
  `settings.model` and Aider's nixpkgs pin pulled out; `android-tools`/`pipx`
  added (settled tools). `nixos-rebuild switch` succeeded; Aider now
  pipx-managed (0.86.2, already newer than the removed 0.83.1 pin). Plan:
  `ai-plans/decouple-hermes-aider-flake.md`. Hermes model live-edited to
  `deepseek-v4-flash` (Dave bought DeepSeek+Google credits) — **service NOT
  restarted yet**, `DEEPSEEK_API_KEY` doesn't exist until Dave generates it.
- **audit#1143 nix-flake batch, all 10 findings fixed** (todos #1216,
  #1220-#1225, #1231 — DONE): SSH password auth disabled (new ed25519 key
  verified working first); `services.tgw.enablePostgres` option added
  (portable tier genuinely skips Postgres — this fix regressed
  `tgw/users.nix`'s unconditional postgres-user line, caught by `nix flake
  check` before reaching a1131, fixed same session); a1131's stray
  `keyd.nix` import removed; duplicate `kdeconnectd` unit removed; backup
  timer relabeled (30min cadence confirmed intentional by Dave, not a bug);
  stale disko comment fixed; dead `tgw/desktop.nix` stub deleted; a1131
  power-management rewritten suspend-free (the naive fix would have
  reintroduced the iMac12,1 "never suspend" bug — caught before applying).
  **New todo #1229, also fixed:** keyd-macroboard's `wayland-0` hardcode vs
  live `wayland-1` session — dynamic socket discovery now. **Deliberately
  NOT fixed:** #1219/#1228 NFS export (no static IP for the intake device
  yet) and #1217/#1218 Syncthing GUI auth (Dave mid-configuring peers).
  **New todo #1233:** a1131 itself still needs its own config push/rebuild
  to pick up these fixes (only tgw-prod rebuilt so far).
- **19 audit#1143 findings consolidated into 6 todos** (#1234-#1239) by
  shared root cause, not just shared file — e.g. #1165+#1166 (identical
  self-rescheduling flaw in two workers) → one todo. Originals marked
  `SUPERSEDED` + closed, not deleted. Memory: `feedback-todo-consolidation`.
- **Router ecosystem research** (todo #1232, PROPOSAL only): D-Link
  DIR-868L → DD-WRT recommended over OpenWrt (chipset support finding, not a
  guess). Plan: `ai-plans/router-dlink-dir868l-ecosystem.md`. DHCP
  reservations mostly done (tgw-prod/a1131/others) but intake
  cameras/devices NOT yet reserved — why #1219/#1228 stay blocked.

**Open into next session:** restart `hermes-agent` once DeepSeek key exists ·
push flake fixes to a1131 (#1233, may be asleep — `wakeonlan c8:2a:14:2a:a1:85`)
· get intake device's reserved IP to unblock #1219/#1228 · #1234-#1239 as
normal execution-track packets · #1230 governance/policy review (not started).

## Session 48 — 2026-07-06 (todo #1200 — recover_expired_jobs dead-letter zombie fix)

- **Fixed via `/tgw-packet 1200`** (todo #1200 DONE): `recover_expired_jobs()`
  was demoting exhausted lease-expired jobs to `'failed'` and leaving them
  there forever — invisible to `dead_letter_count`, the dead-letter CLI/MCP
  tools, and the stall watchdog (Prime Directive 2 violation). Pre-flight
  live query found 62 real zombie jobs already stuck this way
  (ebay_sync/ebay_legacy_sync/ebay_sku_migrate, oldest since 2026-06-24).
  Fixed `src/tgw/queue/schema.sql` to set `dead_letter` directly, plus closed
  a declared-transition-matrix gap in `state_machine.py`
  (`leased`/`running` → `dead_letter` now allowed, matching existing
  `mark_dead_letter()` behavior). New test added. Offline suite: 1837
  passed, same 9 pre-existing/unrelated failures as main.
- **Live apply, Dave approved ("yes, apply")**: deployed via
  `sudo -u postgres psql -d state_machine -f schema.sql` — `tgw` role is
  not the schema owner, `postgres` is (new reference memory:
  `reference-schema-sql-apply-role`). All 62 zombie jobs self-healed to
  `dead_letter` within the next worker's normal 60s recovery cycle, no
  manual backfill needed. `tgw health` confirmed the count now folds in
  correctly.
- **`/code-review` follow-up (commit 7ec2a23, separate per Dave's request):**
  the first-pass fix used a second cascade UPDATE (`WHERE state='failed'`)
  that was an unindexed full-table scan run on every worker's 60s recovery
  cycle forever, and undercounted the recovered-jobs total. Folded the
  `dead_letter` assignment directly into the existing CASE expression
  instead — same live-verified outcome, no extra scan, accurate count.
  Re-applied live, re-verified.
- Two commits: `3ab832b` (original fix) and `7ec2a23` (review follow-up).
  Inbox note: `docs/TGW-Plan-Vault/inbox/DONE-1200-dead-letter-zombie.md`.

**Same session, continued — audit#1143 dead-letter/atomic-write/multi_intake
(todos #1234, #1235, #1242-#1246):**

- **#1234 DONE**: self-rescheduling workers (token_refresh, velocity_stats,
  +6 more found by review) only re-enqueued their next job on success — a
  dead-lettered job silently ended the chain forever. `state_machine.
  mark_failed()` now returns `'retry_wait'`/`'dead_letter'` instead of
  `None`. **#1235 DONE**: 6 non-atomic/no-archive-before-write sites fixed
  (new `items.atomic_write_text()`, `_token_io.py` for eBay token saves,
  `data_scrub_magento.py` rewritten onto `items.strip_fields()`).
- `/code-review` found #1234's fix only covered 2 of 8 self-rescheduling
  workers — fixed the other 6 (**#1242**), then a second review pass
  generalized the whole mechanism (**#1245**): `worker_base.QueueWorker.
  _on_terminal_failure()` now auto-detects a no-arg `self._reschedule` via
  signature introspection instead of 8 hand-copied overrides
  (`ebay_sku_migrate` keeps its own — needs `interval_hours`).
- Dave questioned `multi_intake.py`'s fence-bypassing SKU-collision patch
  live in conversation; investigated with a live `ebay-pull` + exhaustive
  photo-size search across all ItemData/NewItems, confirmed it was
  unverified and redundant with `bundle_intake`'s existing safe idempotent
  handling, removed it (**#1244**).
- **#1246** files 4 remaining PLAUSIBLE review findings Dave explicitly
  deferred (usage/reset timing) — small, no urgency: multi_intake notify
  spam risk, `mark_failed` rowcount race, `ebay_sku_migrate` interval_h
  duplication, notify text missing the ebay_stage next-step.
- Committed as `3efdaed`, pushed — updates existing open **PR #8**
  (`catio-nix-0.0.1-alpha` → `main`), no duplicate PR created.
- Restarted all 19 active `tgw-worker@*` services twice (once per fix
  round); `tgw health` clean except 3 pre-existing tracked warnings
  (backups, nats, ebay_sync_fallback/#1077) — unrelated.
- Full offline suite at each step: 1874 passed at the end, same 10
  pre-existing unrelated failures (google_direct/openrouter rollback +
  pricing-invariant tests) throughout.

**Same session, continued — PR #8 investigation + `~/tgw-flake` git cleanup
(todo #1247):**

- Dave asked what PR #8 was. Traced it: `trader-grims-warehouse`'s
  `flake.nix`/`flake.lock`/`nix/` are convenience symlinks to a fully
  separate repo, `~/tgw-flake` (own GitHub remote) — confirmed intentional.
  PR #8 predates that symlink move, so its diff had drifted to show 595
  files of stale pre-move nix content this repo doesn't use anymore.
- While checking "which flake is applied, is one older than the other,"
  found `~/tgw-flake` had **14 modified + 1 deleted file uncommitted since
  2026-07-04** — turned out to be the earlier Session 47/48 audit#1143
  nix-flake mitigation (SSH key-only, Postgres gating, dead-file removal,
  a1131 suspend-block, Hermes/Aider decouple). Live on tgw-prod (dry-activate
  produced the identical store path to running gen 80) but never committed
  — a fix being "live" and "safe" (committed/pushed) turned out to be two
  different claims.
- Ran the `~/tgw-flake` `commit-nix-flake` skill workflow: cleaned a stray
  `hermes.nix.save`, `nix flake check` clean, committed `a58d86a`,
  dry-activate confirmed no live change needed, pushed to `origin/master`
  (only after separate explicit approval — the permission classifier
  correctly blocked bundling push into an earlier "commit then rebuild"
  approval).
- Dave's call: **PR #8 closed, not merged** — intent already achieved
  elsewhere, "a speedbump." Branch `catio-nix-0.0.1-alpha` itself untouched.

**Open into next session:** todo #1246 (4 small deferred code-review
findings, process whenever Dave asks — not urgent). PR #8 is closed, no
longer a carry-forward. Same carry-forwards as Session 47 above (Hermes
restart, a1131 push, #1219/#1228, #1230 review) are still open.

## Session 2026-07-10 (workflow code review · full-codebase cohesion audit ·
live title-length + Save-Draft UI incident)

- **Workflow-tool code review** (89cf6d7..5c6223e): 1 confirmed finding
  (`sold-order-history-gaps.jsonl` written but never surfaced anywhere) —
  todo #1271.
- **Full-codebase cohesion audit** ("the big workflow review like #1143"):
  6 subsystems x 3 dimensions, adversarial-verified. 54 candidates, 49
  confirmed, deduped to 45 todos (**#1273-#1317**). First run hit the
  session's rate limit mid-flight (80/126 agents failed); resumed cleanly
  post-reset via cached-agent replay — no work lost. Biggest pattern: the
  tgw-api fence's own write helpers (`archive_root`, atomic-write, path
  construction) are bypassed far more widely than known (`api.py`,
  `revision.py`, `scrub.py`, `photo_history_recovery.py`, `http_server.py`,
  `mcp_server.py`) — filed as **PP-FENCE-002** proposal in the inbox
  ("don't climb the fence, use the gate"), proposing new invariants A9
  (path-input validation) and F1 (untrusted content never reaches a live
  external write unescaped). Not yet incorporated into the master plan —
  queued for the next planning session.
- **Proposed planning-session agenda drafted**: `inbox/AGENDA-planning-session-2026-07-10.md`,
  7 sections (alarms, cohesion-audit triage, autosave/pre-flight-validation
  discussion, open PP items, carried-over 07-04 discussion items, future
  ideas, housekeeping).
- **Live incident, found and fixed same session** (todos #1318/#1319/#1320):
  investigating "what does Retry do" on two dead-lettered items surfaced a
  real UX bug — the standalone "Save Draft" button had been removed by
  `a7e7439`/PP-ACTIONCONSOLE-001, leaving NO way to save a draft edit while
  an item shows a pipeline error and isn't live (only "Retry" renders there,
  and it's scroll-only — does nothing). Restored the button. Root-caused a
  related bug while there: `seo/title.py::enhance_title()` only flagged
  oversized titles instead of enforcing eBay's 80-char cap, so 3 real items
  dead-lettered after burning an eBay API call for something knowable
  locally. Fixed with a pre-flight guard in `ebay_stage.py` (same shape as
  the existing `no_price_set` guard) — but does NOT auto-truncate: Dave
  redirected mid-fix to match eBay's own bulk-CSV-editor UX (preserve the
  full oversized title, let the operator trim by double-click-deleting
  words). Added a "Trim Title" action-line affordance + live red-border
  highlighting on the problem field. All verified live (266 tests passed,
  both known-affected items' actual rendered pages confirmed post-restart).
  See memory `project-title-length-guard-2026-07-10.md`.
- **Alarm found and flagged (not fixed):** `tgw-cloud-sync.service` failed
  6x since 07-05 with Google Drive "Queries per minute" quota 403s; the
  eBay-mirror sync workers (`ebay_sync`, `ebay_legacy_sync`, `catalog_rebuild`)
  have been inactive since 07-08, so local eBay-mirror state is ~2 days
  stale with a real backlog (117 queued jobs). Decision needed at planning
  session. **Note:** a stray, unrecognized `<task-notification>` referenced
  a "Start the cloud-sync service" background command near session end that
  I never issued — flagged in the inbox note, re-verify actual service state
  from scratch next session rather than trusting either that notification
  or this summary.
- **No active pipeline incidents**: verified live — 0 jobs leased/running,
  0 dead-letter transitions in the last 24h; the large dead_letter total
  (2942, mostly `ebay_draft:2771`) is historical debt from the already-
  resolved session-45 402 pile-drain, not current failures.

**Open into next session:** run the planning session against
`AGENDA-planning-session-2026-07-10.md`; decide the eBay-mirror-sync
reactivation and cloud-sync quota fix; triage the 45 cohesion-audit todos;
re-verify `tgw-cloud-sync.service`'s real state before trusting the flagged
notification either way; `handoff.md` itself is over its stated 150-line cap
and due for a Session-47 archival rotation (flagged, not done this session).

---

Older sessions: `archive/SESSION-LOG.md`.

---

## First commands for the next session

```bash
cat /opt/TGW/var/run/thermal.status
sudo -u tgw tgw ops-digest          # replaces ad-hoc health/dead-letter checks
sudo -u tgw tgw todo claude
sudo -u tgw tgw plan check
git status --short | head           # s42 work is UNCOMMITTED until Dave says commit
```
