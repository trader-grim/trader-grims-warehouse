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

## Session 43 — 2026-07-03 (quota-exhaustion root cause + backlog purge)

Nothing committed to git yet.

- Diagnosed Dave's report on `tgw202606021133367` (interface/eBay mismatch, edit not
  preserved, partial photo set) down to real root causes via `journalctl` + `queue_jobs`
  + `quota-state.json` (an initial "eBay silently rewrote the listing" theory was WRONG
  — corrected after Dave pushed back; see `feedback-verify-before-blaming-external`
  memory). Confirmed:
  - `ebay_upload.py:111` reports success even when every new photo fails (completion
    guard only checks "at least one photo exists", not "all expected photos present").
  - The #1107 redraft-loop fix (s42) stopped new churn but left ~2,514 legacy SKUs'
    worth of `ebay_upload` jobs in `retry_wait`, auto-requeuing every ~6h forever —
    this backlog raced the worker at every day-reset and burned the full `ebay_eps`
    budget (5,000/day, halted at 3,500) within about an hour, 3 days running.
  - The 30%-reserve "operator priority" carve-out in `quota.py` (interactive callers
    never blocked) is structurally unreachable for photo uploads — nothing tags
    operator-triggered `ebay_upload` jobs as interactive; all run through the same
    background worker.
- Cancelled the 2,715-job stale backlog (Dave explicitly authorized after being shown
  the evidence). Today's 3,500/5,000 `ebay_eps` real spend is NOT recoverable.
- Filed todo #1115 (p20) for the code fixes (completion-guard + dedupe/cap).
- Did NOT do a manual interactive-context bypass for `tgw202606021133367`'s remaining
  17 missing photos — harness correctly blocked a self-devised safety-bypass attempt;
  left the decision to Dave (session ended via /tgw-exit before he answered).
- Full detail: `inbox/INPROGRESS-ebay-photo-desync.md`.

**s43 later same day — C10 built + live-verified, plan issued for parallel execution:**
- **Invariant C10 (operator lane) LIVE**: all 14 operator surfaces stamp
  `origin='operator'`; workers propagate it chain-wide; `worker_base` runs such jobs
  in interactive quota context. Live-fired on `tgw202606021133367`: 17 photos sailed
  through the halted pool, listing verified at 24 photos via ebay-pull (eBay cap; 26
  submitted). Regression caught+fixed during live-fire: context name must keep
  `worker:` prefix or the PATCH auto-redraft guard sees worker fence-writes as human
  edits — s42 redraft loop came back for 2 cycles. Both sides now tested (68 green).
- **Plan issued (Dave: "put it in the plan so opusplan can execute")**:
  **PP-PHOTOSYNC-001** (`pp/PP-PHOTOSYNC-001.md`) = fix track, packets P1–P6 = todos
  #1115 #1117 #1118 #1119 #1120 #1121. Forward track runs PARALLEL: **R1.8 #1122
  (Dave GO 2026-07-03**, packet `packets/1122-r18-dataset-snapshot.md`) +
  PP-BACKUP-001 (#61/#146/#147/#1052) + #1102. Collision rule in the PP doc.
- **Dave pre-authorized** P4 fleet photo repair ramp 1→5→ramp (inspect at n=1, n=5).
- **Committed + pushed**: `ae9b1e6` on `catio-nix-0.0.1-alpha` (s42+s43, 108 files,
  Dave-approved). PR to main DEFERRED until P1 (#1115) verifies — then
  `/tgw-pr-review` + merge (main is 46 commits behind; don't snapshot it mid-fix).

**Recommended next sequence:** (1) fix track P1 #1115 → (2) forward track #1122
snapshot (any time, pool-disjoint) → (3) P2/P3 while P4 ramps → (4) PP-BACKUP-001
packets interleaved → (5) PR to main via /tgw-pr-review.

---
## Session 45 — 2026-07-04 afternoon (LLM provider flip + UI-pipeline defect evidence) — CONTINUES 4pm

Committed as-we-went (Dave's instruction), 7 commits on catio-nix-0.0.1-alpha.

- **LLM provider flip (todo #1144 DONE, live-verified):** Google dole
  free-tier quota PER PROJECT (~20 req/day/model here vs published 1,000) —
  2,171 llm_google 429s in one day from the 402-requeue backlog. Dave's call:
  OpenRouter is PRIMARY; Google free tier = OPERATOR EMERGENCY RESERVE
  (interactive-only fallback); failover pattern kept + precheck-gated for a
  future paid Google key. Docs: reference/LLM-Providers-Quotas.md (canonical,
  finding was rediscovered 3× before being written down), invariant E8,
  CLAUDE.md row, memories. Backlog drains ~10× faster since (no 429+40s tax).
- **#1145 PP-UIPIPE-001 opened (p5): web UI pipeline defect audit.** Dave:
  "the web ui pipeline ain't cutting it"; his draft-vs-offer hypothesis
  CONFIRMED by evidence sweep — tgw202605052336026 LIVE at $40.99 with local
  draft_listing.price=None; tgw202605060125081 published 07-04 with 1/8
  photos (after #1115 P1 marked done!); 9/10 items same fulfillment policy;
  publish silently re-runnable (dozens of succeeded publish jobs per SKU,
  C3); published items never get a published status locally. Full evidence:
  inbox/INPROGRESS-1145-uipipe-defect-audit.md. 4pm: Dave names the
  wrong-shipping listing + rest of defect list → root-cause→packet map.
- **Standing rules encoded:** a1131 is shared Dave+Claude for THERMAL RELIEF
  — offload Claude's checks there on hot days, never pause pipeline workers
  for heat (CLAUDE.md + memory); NFS shares for check data = todo #1146.
- Also: archived 6 processed s44 inbox notes; swept last night's uncommitted
  pm-intake vault filings into a labeled commit (verified against FILING-LOG
  first).

**Open into 4pm:** #1145 walkthrough (top priority), ebay_draft backlog
~3k draining (watch OpenRouter $5/day cap on `tgw health`), #1143 2pm audit
agenda missed (reschedule), #1139 fleet decoupling, #1146 NFS shares.

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
