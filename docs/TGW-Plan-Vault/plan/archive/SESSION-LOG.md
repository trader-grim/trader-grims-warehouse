# Session log archive (rolled off handoff.md's 2-session window)

## Session 41 — 2026-07-02 (quota drains, data-preservation bugs, google_direct)

Committed: `a7e7439`, `f511f2d`, `d1cad9a`. Full detail:
`dev-workflow/research/SESSION41-wrapup.md` + `archive/handoff-v5-2026-07-02-preredraw.md`.

- eBay quota drains fixed (QA-telemetry call removed; 25707 fallback capped 24h;
  tree-cache auto-expiry removed; warm-up gated to pre-reset window).
- `google_direct` LLM provider live (free-tier Gemini verified); ai_identify/alt_text/
  ebay_draft/bulk_classify moved to it; OpenRouter auto-fallback.
- ebay_draft aspect-fill now vision-based (up to 10 photos).
- Data-preservation bugs: price reducer never persisted reductions (silent revert on
  re-stage) — fixed; `atomic_write_json` reverted shared-file perms to 0600 — fixed;
  vault permission drift root-caused (stale deployed script + the above).
- `tgw-clipd` crash loop (15,769 restarts) fixed; UTC-as-local timestamp display fixed
  (13 sites).

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
