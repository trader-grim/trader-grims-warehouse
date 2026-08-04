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

**Archived 2026-07-11** — #1233 (a1131 config push) confirmed DONE same
session as the archival (verified: a1131 rebuilt 3x on 2026-07-11, well
past this session's fixes). #1230 also confirmed DONE (tagged PP-COHESION-001).

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

**Open into next session (archived, superseded by later sessions):** todo
#1246 (4 small deferred code-review findings). PR #8 closed, no longer a
carry-forward.


## 2026-07-16→17 (planner rubric · lost-PP recovery sweep · 6 PPs planned)

**Note: session-number sequence has a gap since Session 48 (2026-07-06) —
using date heading instead of guessing a number.**

- Master-plan reconciliation (todo #1477) resumed from a prior-session
  pause; worker-contract gaps (#1458/#1459/#1479) reviewed, left as-is per
  Dave ("in progress is good enough for today").
- Wrote `reference/PP-HERMES-EA-001-planner-rubric.md` (todo #1414) —
  closed the confirmed pipeline-maturity gap #3 from 2026-07-14.
- Multi-round planning sweep, each round prompted by Dave asking "is that
  all unplanned?": PP-STORAGE-001, PP-VISION-001, PP-INVENTORY-001,
  PP-UIUX-001 (new, absorbing an orphaned 10-day Flutter-vs-web
  discussion), PP-RUNNERCOMMS-001 (resolved as a "mailbox" design), and
  PP-INTAKE-004 Phase 1 (seeded as real todos, not started) all taken from
  bare/stub to fully planned.
- Dave triaged 7 stale/quiet PPs directly: PP-BULKLIST-001 (queued after
  pipeline restart), PP-PHOTO-001 Phase B (→ Tigwa), PP-RECOVERY-001
  (CLOSED — confirmed already resolved via later work), PP-MACRO-001 (→
  Tigwa), PP-LOOKUP-001 (→ Tigwa), PP-EBAY-SNAPSHOT-001 (status only),
  PP-MARKETING-001 (deferred).
- "Recover lost PPs" sweep: **PP-ROUTER-001 opened**, recovering an
  orphaned `docs/ai-plans/router-dlink-dir868l-ecosystem.md` (filed
  2026-07-06, never had a PP) — found a live DHCP IP-conflict finding in
  the process. Corrected a false "no design doc existed" claim about
  PP-DOCLIB-001 (doc exists, Dave confirmed no action needed — recoll was
  the faster route already taken). Sent router findings to Tigwa re: a
  possible NATS-JetStream-for-alarm-system leg she's already researched.
- **Process correction, twice:** lost-PP recovery is pull-based
  (search/reinstate on request), and the function itself belongs to
  Tigwa (the librarian), not Claude — she's already working it nightly on
  Dave's direct assignment. Encoded in memory + master plan; tonight's
  sweep was explicitly named as not-the-template-to-repeat.
- Reconfirmed the six-stage-loop doctrine at the PP level and encoded
  "parallel-track discipline" (R1 concentrated focus, background PPs keep
  nudging forward) into the master plan.
- 12 new todos filed (#1480-#1491), all planned/unstarted. `tgw plan
  check` clean throughout every round.

**Open into next session:** todo #1477 still paused, not Dave-confirmed
complete. "2 credentials issues" question (PP-LOOKUP-001) unresolved.
Dave: "we code in the morning" — next session is likely execution
(pipeline restart-in-earnest), not more planning.

## 2026-07-17 — DeepSeek/OpenRouter billing investigation + registry-log delegation

- Dave asked what was misconfigured after OpenRouter billed "DeepSeek 3"
  tokens during the 5pm UTC hour on 2026-07-15. Root-caused via git history
  + file mtimes (no code changes made): the live `.aider.conf.yml` during
  that hour still had the pre-tuning line `model: openrouter/deepseek/
  deepseek-chat-v3-0324` — the switch to DeepSeek-direct didn't land until
  commit `2d98364` that same evening (2026-07-16 03:50 UTC), several hours
  after todo #1358/#1365's worktree-wiring smoke test ran. Confirmed as the
  old default being live during a planned test, not an ongoing gap.
- Side finding, not actioned (Dave didn't ask for a todo): MCP-invoked
  `aider_run_task` calls have no provider/model/token audit trail — only
  the `/tgw-aider-step` skill path logs to `usage.csv`.
- Dave separately noted Tigwa is hashing configs (and, on her own
  initiative, worker contracts) for the library catalog — partial coverage
  so far — and asked to formalize it into a durable registry log. Filed
  todo #1493 (pp_ref PP-KNOWLEDGE-001) and sent
  `inbox/tigwa/CLAUDE-REQUEST-config-hash-registry-log-2026-07-17.md`
  relaying the ask; format/schema left to Tigwa to scope, same
  consult-then-review pattern as HR-001.

**Open into next session:** #1493 awaiting Tigwa's design response. Nothing
changed on the still-open top-priority thread (#1492 Flutter launch/connect
verification, #1477 master-plan reconciliation paused) — see handoff.md.
