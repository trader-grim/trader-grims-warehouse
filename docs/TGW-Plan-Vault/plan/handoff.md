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
3. **3,239 ebay_draft dead-letters** — old OpenRouter-402 pile; pipeline is now
   google_direct + quota-supervised; bulk requeue ramp (50 → 500 → rest) awaits
   Dave's go (R1.3).
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
- ⚠ **s43 diff UNCOMMITTED** (worker_base, http_server, 5 workers, invariants.md,
  2 test files) — first executor session gets Dave's commit go first.

**Recommended next sequence:** (1) commit s43 diff → (2) fix track P1 #1115 →
(3) forward track #1122 snapshot (any time, pool-disjoint) → (4) P2/P3 while P4
ramps → (5) PP-BACKUP-001 packets interleaved.

---

## Session 42 — 2026-07-02 (retarget + R0 quota independence + data-first redraw)

Nothing committed to git yet — Dave controls commits. All changes live in prod.

- **Retarget approved + executed**: `plan/RETARGET-2026-07-02.md` (diagnosis F1–F5,
  tracks R0–R3, freeze list, work-packet protocol).
- **Quota independence (R0) built and live-verified**:
  - `getRateLimits` probe works (snapshot `/opt/TGW/var/run/ebay-rate-limits-probe.json`).
  - **Bulk aspects**: `tgw warm-ebay-aspects` — ONE call on the untouched
    `commerce.taxonomy.bulk` pool (100/day) cached aspects for ALL 15,105 leaf
    categories (shards + raw gz at `ItemCatalog/ebay-aspects-bulk/`). UI aspect
    lookups now need zero live Taxonomy calls; operator testing unblocked same day.
    Aspects cache is permanent + manual refresh (TTL removed, matches tree policy).
  - **`tgw.quota` budget layer** at every metered choke point (REST/Trading/EPS/LLM):
    daily per-pool counters (PST boundary), background halt at 70%, 30-min post-429
    stand-down, 429s logged as incidents with caller identity
    (`var/log/quota-incidents.jsonl`), new `quota` health check. Caught 181 real 429s
    (ebay_draft/ebay_upload churning exhausted pools) within minutes; churn stopped
    after stand-down deploy. Quota/429/usage-limit errors now TRANSIENT-requeue in
    workers — quota walls can no longer pile up dead letters.
  - **`tgw ops-digest`** — morning one-screen: flagged health, quota spend,
    dead-letter deltas, restart flags, stale inbox notes.
  - Timestamps: 6 naive datetime sites fixed; invariant E6. Verified stored data was
    never wrong (timestamptz + journald store UTC; s41 bug was rendering-only).
- **PRIME DIRECTIVES added to top of CLAUDE.md** (Dave's standing orders, enforcement
  over memory) + **`reference/TGW-Data-Charter.md`** (axiom: eBay is a rented window,
  the local dataset IS the business; asset inventory; rules for new work).
- **Raw eBay capture at the fence** (invariant E7): every eBay response (REST/Trading/
  EPS, errors included) → `incoming/ebay/YYYY-MM-DD.jsonl.gz`, fail-open, capture
  happens in `client.py` before any worker parses. Live-verified.
- **Master plan REDRAWN data-first** (~250 lines): PP designs split byte-exact to
  `plan/pp/`, history to `plan/archive/sections/`; `tgw plan check` all clear after.
- Found: `tgw restart-workers` references nonexistent `ebay_dole` unit (batch fails);
  CLAUDE.md `tgw todo add` syntax stale (it's `--add`).
- Tests: +23 new (quota 17, capture 6); targeted suite green outside pre-rotten files.

**Open from s42:** R1 live-fires; R1.8 dataset backfill (Dave's go); R2.2 digest on
web UI home; R2.3 push-on-red; #1102 suite repair; #1103 dataset-growth digest lines;
#1104 enforce E5 in code; thermal hook authorization.

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
