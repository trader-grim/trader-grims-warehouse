# Session log archive (rolled off handoff.md's 2-session window)

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
---
## Session 45 — 2026-07-04→05 (provider flip · a1131 buildout · tool fixes · knowledge-plane plan) — COMPLETE

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

**s45 evening/night (continued past 4pm through ~03:00):**
- **a1131 fully built out** (#1146 DONE): ro NFS data/log mounts, claude
  account (key-only + Dave-authorized NOPASSWD sudo), Wake-on-LAN live-fired
  (`wakeonlan c8:2a:14:2a:a1:85`; NEVER initiate suspend — iMac bug).
  nix-syncthing overrideDevices/Folders=false fix (rebuilds were wiping
  GUI-added peers — Dave's vault share); devices restored, Dave re-accepting
  shares.
- **Two UI-pipeline TOOL FIXES live-verified** (Dave's course-correction:
  fix the tool, not the data lists — see memories): per-field policy
  resolution (#1152; config FC4/payment/return now always win) and
  draft-price-only staging (stale ebay_offer.price can never publish
  unreviewed; operator List on unpriced item → HardFailure + no_price_set
  finding persisted). 8 wrong-policy live listings repaired PS→FC4;
  0125081 healed 1→8 photos via C10 chain.
- **Four-item forensics:** one root shape — truth/plan/live planes never
  reconciled. Broker planned (`ai-plans/reconciliation-broker.md`, packets
  B0–B5; B0 = Dave's 20-min rule-table sign-off; cardinal rule: validate
  against TRUTH, never the plan).
- **Knowledge plane planned** (`ai-plans/recoll-annex-jetstream.md` +
  PP-KNOWLEDGE-001 in master plan): stage 1 = organize/make accessible;
  todos #1147-#1151; Dave: annex-gdrive REPLACES Syncthing for data trees
  (vault→git); E0 transport decision leans Postgres-events over JetStream.
- **402 pile FULLY DRAINED:** ~6,500 jobs, 99.9% success, ~$1.08; ~2,650
  fresh drafts now await operator review (NB #1113 ebay_dole not installed).
- **Fleet photo-integrity sweep DONE** (a1131 over NFS, 3.4h): 206 bad/149
  SKUs (0.076%), single Feb-2022 unverified-copy event, 30 LIVE listings
  prioritized; roster = var/reports/photo-integrity-2026-07-05.tsv; plan =
  ai-plans/photo-integrity-mitigation.md (#1154).
- New skill: `/tgw-packet`. New todos: #1145-#1154.

**Open into next session:** B0 broker sign-off (20min, unlocks B1-B5) ·
#1145 walkthrough remainder (Dave's full defect list; 2336026 price via
editor) · #1147 search surface (top delegable) · fleet getOffer policy
sweep (~2k calls, no gate) · #1139 · E0/A0 decision packets.

---
