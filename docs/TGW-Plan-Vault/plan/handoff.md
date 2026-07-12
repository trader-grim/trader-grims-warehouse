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

*(Session 48 — 2026-07-06 rolled to `archive/SESSION-LOG.md` 2026-07-12.)*

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

## Session 2026-07-11 (master plan retarget: catio structural kickoff · full tracker triage)

**The biggest single session in this project's plan history — read
`project-catio-sequencing` + `project-plan-retarget-2026-07` memory before
anything else, don't re-derive from this summary alone.**

- **Six-concept structural retarget**, framed by Dave as PP-CATIONIX-001
  Phase 1 ("a catio, dev team, and Dave upgrade"): Hermes personas Tigwa/
  Leotha (PP-HERMES-EA-001, apprenticeship model), 5-layer knowledge hub
  (PP-KNOWLEDGE-001 extended, PP-ANNEX-001 promoted), event server/"Radar"
  (PP-EVENTD-001 unfrozen — the long-dormant #1086 gate finally ratified),
  justshoutit voice-operated listing, plan/invariant-as-correctness
  doctrine (new CLAUDE.md section), camera app (PP-INTAKE-004 promoted +
  expanded scope). PP-AIOPS-001 turned out to already be a thorough
  6-phase cat-herder/litterbox design — cross-referenced, not re-derived.
- **Full `tgw todo --by-pp` tracker triage**, new tool built same session:
  ~114 untagged open todos → 1 (Dave's own call, #1253 left alone). 5 brand
  new PPs opened (PP-SELLERHUB-001, PP-DATAINTEGRITY-001, PP-INVENTORY-001,
  PP-HARDWARE-001, PP-COHESION-001), several promoted from bare mentions to
  real headings (PP-AIOPS-001, PP-EDITOR-001, PP-DATALEARN-001,
  PP-LOOKUP-001, PP-MACRO-001, PP-MULTIMODEL-001, PP-VISION-001,
  PP-MARKETING-001 new).
- **Real gaps found, not just reorganized**: PP-PORTABLE-CATALOG-001 was
  marked done but never live-verified/installed (a1131) — got its first
  real design doc with an honest architecture assessment; PP-EVENTD-001's
  design doc had a FALSE "already implemented" backchannel claim, corrected;
  PP-QUOTA-001's ✅ removed (no dollar-balance monitoring exists, only
  call-count proxies — todo #1337, "fine now only because pipeline is
  quiet"); #1251 was silently gated on #1250 via a config comment, now an
  explicit `depends_on`.
- **Process note**: multiple real synthesis errors were caught mid-session
  by re-verifying against primary sources instead of trusting earlier-
  session assumptions (NATS-vs-Postgres conflation — two DIFFERENT NATS
  questions got merged into one wrong answer at first; Web-UI-vs-Flutter-
  app conflation — the new Kotlin camera app got wrongly identified as the
  existing Flutter app). Both corrected before landing in the real vault.
  Worth remembering: verify, don't just synthesize forward.
- Two real commits, both pushed: `3080e3c` (the main retarget) and
  `acaf930` (the triage round) on `catio-nix-0.0.1-alpha`, covered by
  existing open PR #10. Flake repo also has an unrelated earlier-session
  commit `6292326` pushed (SSH key rotation, hermes.nix removal,
  backup-drive mount durability fix — all live-verified before commit).

**Open into next session:**
- PP-SELLERHUB-001's Gemini audit (unlimited scope per Dave) — not yet
  run, todo #1336 is just the scoping pass.
- #150-152 (PP-PORTABLE-CATALOG-001) — status left as "done" in the
  tracker even though the honest assessment says otherwise; Dave hasn't
  decided whether to formally reopen them.
- Full master-plan diet pass to ≤500 lines — still owed (todo #1331,
  deferred twice now for the same "don't rush a mechanical rewrite of
  dense history" reason; #1338's findings folded in as specifics).
- Dave will handle #1253 (secrets facility → interactive shell) himself in
  Hermes config planning — not TGW's tracker, don't pick this up.

## Session 2026-07-12 (Fable independent review #1338 · live worker-resurrection incident)

- **Ran todo #1338**, the Fable independent review deferred from the
  2026-07-11 retarget session. 13 confirmed findings against commits
  `3080e3c`/`acaf930` — full report + corrections filed:
  `dev-workflow/research/DONE-1338-plan-retarget-followup-triage.md`.
- **LIVE INCIDENT caught and fixed same turn (Prime Directive 2):** the
  2026-07-11 11:11 reboot had resurrected `pm_intake` + 4 other
  deliberately-stopped/dead workers (systemd-disable never made durable —
  the exact risk CLAUDE.md had already flagged as unfixed). `pm_intake`
  ran ~9h unnoticed and autonomously filed+archived one plan document via
  LLM decision before being caught. **Stopped `pm_intake` live**, restoring
  Dave's 2026-07-09 direction; verified nothing else was touched. The other
  4 (`thumbnail_gen`, `velocity_stats`, `ebay_price_reducer`,
  `ebay_sku_migrate`) were left running — no individual standing
  instruction found for each — **Dave's call whether to stop them too.**
  Durable-stop fix (todo #1322/PP-NIXOS-001) bumped to p5 given the live
  incident behind it now.
- **12 doc/plan corrections applied same session** (mechanical, not design
  judgment — see the DONE doc above for full detail): PP-BACKUP-001 status
  corrected (durable fix was actually applied + reboot-verified, plan still
  said otherwise); PP-DATAINTEGRITY-001.md's wrong pp_ref for #152 and
  missing #1271 absorption; PP-EVENTD-001.md stub's stale frozen status;
  PP-PRICING-001.md's banned per-provider-JSON secrets pattern (both
  master-plan and pp/ copies); PP-KNOWLEDGE-001 "5-LAYER" → "6-LAYER";
  PP-CATIONIX-001.md's 3-week-stale Qtile module-structure section;
  `ebay_dole` three-way contradiction reconciled across CLAUDE.md + master
  plan + PP-BULKLIST-001; Done-rollup/Frozen-list stale entries removed
  (PP-EDITOR-001/PP-DATALEARN-001/PP-MULTIMODEL-001 had open work;
  #1056 LVM item was closed/superseded); PP-CLIP-001.md header/body Phase 2
  contradiction; PP-WHISPER-001/PP-STORAGE-001 added to the plan index
  (recurrence of the s42 "27 PPs dropped from index" failure mode); a
  severed mid-sentence dependency clause and packet-template code-fence
  debris repaired.
- **Not attempted this session, deliberately** — the larger structural fix
  (master plan is 1,381 lines vs its own ≤500-line rule; ~30 floating
  unfiled status notes; several PPs still living inline instead of in
  `plan/pp/`). Folded into existing todo #1331 rather than duplicating.
- `tgw plan check` clean after all edits. Nothing committed yet — same as
  every prior session this week, awaiting Dave's go.
