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
2. **Test suite rot** — true state 1,399 pass / 11 fail / 236 errors (most:
   test_http_server.py broken since cookie-auth refactor). "Suite green" claims from
   earlier sessions were stale. Repair: todo #1102. Until fixed, only targeted test
   runs are meaningful.
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

## Session 46 — 2026-07-05 (todo #1143 audit — workers/ subsystem, first slice)

Dave asked for "the one that fixes the commit backlog," expecting it first on his
admin todo list — it was **#1039** (PP-RECOVERY-001), a stale 2026-06-17 web-UI
regression audit whose only real recovery action (merge `task/aider-20260616145314`
→ main) turned out already done; correctly redirected to **#1143**, the
full-codebase cohesion+correctness audit named in CLAUDE.md's code-review cadence
rule (confirmed by line 124 above — "missed again" three sessions running).

- **#1143 workers/ slice DONE** (first of 6 staged subsystems): Workflow tool, 5
  file-groups, 60 agents, ~2.3M tokens, 25 files/6,817 lines, 2-of-3 adversarial
  verify per finding. 17 confirmed, 1 dropped. Report:
  `dev-workflow/research/RESEARCH-1143-workers-audit.md`.
- **9 correctness bugs filed as individual todos #1162-#1170** — most notable:
  `token_refresh.py`/`velocity_stats.py` self-scheduling flaw (an unexpected error
  silently ends the loop forever — token_refresh dying eventually breaks every
  eBay-facing worker with no alert); `ebay_publish.py:250` condition-fallback never
  syncs back to the local record after a 25021 rejection, so local permanently
  disagrees with live eBay and re-staging repeats the same reject+fallback cycle;
  `ebay_sku_migrate.py:252` partial migration (eBay ok, local rename fails) never
  flags the item blocked, so it's silently reprocessed every cycle.
- **8 lower-severity invariant/cohesion findings batched into #1171** (fence-bypass
  path construction in 5 files, one ad-hoc queue, one duplicated helper function).
- **#1143 remaining subsystems (queued, run opportunistically):** apis/ebay/,
  http_server.py, queue/state-machine, scripts/, nix flake.
- No files in `src/` changed this session — audit only; nothing to commit besides
  plan-vault docs/todos.

**Open into next session:** work #1162-#1170 correctness bugs directly, or continue
#1143 with the next subsystem (apis/ebay/ recommended next — same lifecycle code
`ebay_publish.py`/`ebay_sku_migrate.py` just flagged touches it heavily).

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
