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

## Session 2026-07-12 continued (Hermes setup + a1131 toolkit + #1322 durable fix)

- **Hermes-lite** installed+configured on tgw-prod (userspace, `nix profile
  install`), recovered old state restored, all 4 API keys wired
  (deepseek-v4-flash). Not yet a systemd service.
- **a1131 toolkit** (all under the `claude` account): Codex CLI, Aider,
  Claude Code CLI, AGY, notebooklm-py, xdg-utils — all installed, latest
  stable. Claude Code CLI OAuth-authenticated (Pro sub,
  claude@mappo.eu.org) after a long fight — see memory
  `feedback-a1131-claude-account-oauth` for the gotchas (home-dir
  permissions, DISPLAY-inheritance hang, code-relay-through-agent
  fragility, clipboard failures). Codex CLI + AGY logins still pending.
  Hermes's own openai-codex OAuth (tgw-prod) still OpenAI-429-throttled.
- **Todo #1322 FIXED**: durable worker-stop mechanism. `services.tgw.
  workers` was defaulting to ALL queues — root cause of this morning's
  reboot resurrecting pm_intake etc. Explicit exclusion list added to
  `nix/hosts/tgw-prod.nix`, verified, staged via `nixos-rebuild boot`
  (not `switch` — live system untouched, takes effect on Dave's next
  reboot). Flake change uncommitted, pending Dave's review.
- **Design fully written up**: `pp/PP-HERMES-EA-001.md` — office split
  (Hermes-lite always-on tgw-prod / full Tigwa woken on a1131 via WoL),
  wake-trigger structure (reuse tgw health/ops-digest, shadow mode first),
  deferred-investigation queue (new Tigwa function, NotebookLM as first
  use case), Nix-safety rules (test>switch, build-vm off-host).
- **Dave rebooting both machines now** to test whether it resolves a1131
  clipboard-paste failures that blocked OAuth code entry all session.

**Open into next session:** verify #1322's fix actually took effect
post-reboot; finish Codex CLI + AGY auth on a1131; retry Hermes's
openai-codex OAuth after real cooldown; a1131 browser-launch still not
working even with xdg-utils (Dave: "wasn't pulling up the browser") — not
yet root-caused, troubleshoot if it recurs; create the dedicated tigwa/tgw
Google account (Dave) to unblock notebooklm-py auth.

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

## Session 2026-07-12, later (post-reboot checkup → Tigwa's a1131 office fully provisioned)

Picked up right after the reboot from the previous session (testing whether it
fixed a1131 clipboard paste). Turned into completing PP-HERMES-EA-001's a1131
leg end-to-end. Dave, on seeing it work: "Seems we have a scaffolding to build
this tool. It is a keystone in our strategy, time for me to onboard tigwa and
let her interview me."

- **Post-reboot checkup**: #1322 durable-worker-stop fix confirmed live
  (pm_intake/thumbnail_gen/velocity_stats/ebay_price_reducer/ebay_sku_migrate
  correctly absent). `tgw health` clean (as `tgw` user — running as the wrong
  user gives misleading permission-denied noise, not real failures).
- **Codex CLI OAuth, a1131**: got working after real friction — device-auth
  needs the local process to observe its own completion, finishing the
  ChatGPT-webUI side alone isn't enough (3 failed attempts before this was
  understood). See [[feedback-a1131-claude-account-oauth]].
- **Hermes model config, tgw-prod**: main model set to `openai-codex`/
  `gpt-5.6-sol` via the interactive `hermes model` picker (not hand-edited —
  the picker also manages provider-specific api_mode/base_url correctly).
  Hermes's own `openai-codex` OAuth also completed (earlier 429 throttle had
  cleared). OpenRouter confirmed healthy, $5/day cap is intentional sizing
  for its fallback-only role, not a problem.
- **a1131 account renamed `claude` -> `tigwa`**, Dave's direction ("moving
  tigwa into her new office... under my authority"). Done via the nix flake
  (uid 1001 pinned, uncommitted pending Dave's review — same pattern as the
  #1322 fix), not raw `usermod`. Real regressions found+fixed post-rename:
  stale absolute paths in `~/.nix-profile` symlinks and `~/.npmrc`, pipx
  venvs (`aider-chat`, `notebooklm-py`) needed `pipx reinstall`. Full detail
  and reusable gotchas in [[project-tigwa-office-a1131]].
- **Full toolkit verified live on `tigwa@a1131`**: Codex (OAuth), Claude Code
  CLI (auth intact), Aider, notebooklm-py, AGY (binary runs, auth deferred),
  Hermes (installed, not yet configured). Added a missing `~/.profile` so all
  six resolve on PATH with a normal login — the account had none before.
- **`bubblewrap` added to a1131's system packages** — Codex's `--sandbox`
  mode needs `bwrap`, wasn't present anywhere on the host. Build-then-switch,
  verified live.
- **Deliberately deferred, not gaps**: AGY and notebooklm-py auth both wait
  on Dave's own timeline for deciding whether/how to migrate off his personal
  Google account for Tigwa's identity — explicitly not a rush.
- **Still open**: Hermes-lite's a1131 model/credential config (Dave doing
  himself), Hermes-lite gateway service (still stopped), wake-rules config,
  office-side dispatch mechanism (not yet designed). See todo #1340 (updated
  this session) and `docs/TGW-Plan-Vault/inbox/INPROGRESS-1340-hermes-setup.md`.
- Both flake changes **committed** (Dave: "commit the flake"), as two
  separate commits: `1a3285c` (#1322 durable-worker-stop, tgw-prod.nix) and
  `8592ae2` (#1340 tigwa rename + bubblewrap, a1131.nix). Neither pushed to
  origin — not asked for.
- Dave's closing note: onboarding Tigwa doesn't change Claude's role —
  "You are still lead engineering architect. Tigwa is here as both of our
  assistant to reduce our workloads." **Next session's stated priority:
  "tackling the audit results"** — check CLAUDE.md's Current Phase section
  and open PP-COHESION-001 items first to confirm which audit before
  assuming.

## Session 2026-07-13 (task-execution contract designed + piloted — 14 real fixes)

Dave's stated goal: "find out if our results improve by strictly following
the processes we have and also to save me time" — an experiment, not a
settled process yet. Framed explicitly as "uberscripting" of his own
review/git process, not autonomous execution (see
[[feedback-uberscripting-not-autonomy]]).

- **Built the task-execution contract** in `pp/PP-HERMES-EA-001.md`:
  branch-per-task + result manifest, Tigwa's bounded check/fix
  enforcement loop (escalation-only, deliberate pre-crypto-lock
  exception), `.claude/agents/tgw-coder.md` (executor) +
  `.claude/skills/tgw-runner-review/SKILL.md` (reviewer, persona-agnostic
  by design).
- **Piloted it for real**: two full stitch cycles against
  `PP-COHESION-001` findings — 5 mechanical bugs, then 5 SECURITY findings
  (including a 4-item shared-root path-traversal cluster:
  `config.py`'s `sku_dir()`/`location_dir()` had zero containment
  validation). **14 real bugs fixed and merged**, full suite green
  throughout (2111 passed, 1 skipped as of last run).
- **Two durable process rules emerged from real friction**, both encoded
  in the plan doc: (1) cadence rule — stitch after each clean task except
  the first of a new sequence, which needs 2-in-a-row clean before
  stitching + graduating to concurrent execution; (2) shared-root cluster
  rule — fix the root alone, then verify each dependent rather than
  assuming branch count (proven with a real 50/50 split: one dependent
  needed zero code, two needed independent fixes).
- **Mandatory git-worktree-per-task isolation** added mid-pilot
  (`/opt/TGW/var/worktrees/`) after two tasks shared one working directory.
  A `PYTHONPATH` hazard was found and closed (the venv's editable install
  points at the shared checkout, so untested-worktree code could silently
  pass). Claude's own prompts wrongly said "branch off `main`" for several
  tasks — this repo's actual branch is `catio-nix-0.0.1-alpha` (see
  [[reference-catio-nix-branch]]); caught before real harm, `tgw-coder.md`
  now requires live verification. New standing rule: any operational
  friction (not just code deviations) gets a todo, always.
- **Tigwa**: set to auto-review the plan every 4 hours (read-only). Filed
  and got two requests reconciled through the inbox seam on her own
  initiative — a Hermes-native checkpoint adapter (#1356) and a plan-review
  publishing folder (#1359, `docs/TGW-Plan-Vault/tigwa-reviews/`) — both
  approved as proposed, no changes needed. A third, a narrow
  `tgw-inbox-intake` skill so she can discover Claude's responses without
  Dave relaying them, reconciled same session (#1362). She asked how to
  proceed via the seam even holding Dave's own explicit permission already
  given — see [[project-tigwa-inbox-request-validated-2026-07-13]].
- **Still open**: `#1359`'s controlled baseline publication (Tigwa's side,
  not blocked on Claude). Remaining untouched SECURITY findings: `#1276`,
  `#1277`, `#1278`, `#1279`, `#1281`, `#1283`. `#1358` (wire Aider into the
  worktree contract) and `#1361` (minor `.pytest_cache` ownership friction)
  filed, not started. `#1360` is a reminder for Dave (Antigravity
  UI-generation idea), assigned to Tigwa.
- **Housekeeping note**: this file is well over its stated ~150-line cap
  (325+ lines) — due for a prune/archive pass per its own rule; not done
  this session, flagging rather than silently ignoring.

## Session 2026-07-13, later (resumed after rate-limit — Eligible filter → status/#STATUS incident → PP-POSTGRES-001)

Resumed a rate-limited prior session: closed out the interrupted
PP-COHESION-001 follow-up batch (#1371/#1372/#1373 — merged, one real
conflict resolved in `alt_text.py`, tested, closed) and delegated the
priority-emergency-channel work (#1346) to Tigwa (her infrastructure,
Claude's a1131 key doesn't authenticate there).

- **#1377 DONE**: web UI Eligible filter (`http_server.py` `__eligible__`)
  silently excluded any item with blank `status` — fixed, tested,
  live-verified (1541→2351 eligible items), deployed.
- **Root-cause chain, #1376 (logged, not fixed)**: diffing
  `ItemArchive/<sku>.zip` snapshot pairs + a stray
  `data-scrub-1053-report.json` proved `scripts/data_scrub_legacy_ebay_fields.py
  --apply` stripped the legacy `#STATUS` key from 20,415 items on
  2026-07-03 22:21 with no promotion-first guard. Dave then corrected the
  read: `status` (lowercase) is the real canonical field; `#STATUS` was
  his own manual convenience alias, "sometimes not updated." Real bug:
  `items.statusupdate()`/`verifiedupdate()`/`bulk_edit` all write to
  `#STATUS`, never `status` — see [[reference-status-vs-hashtag-status]].
  Dave: "this is a big fix" — logged under PP-DATAINTEGRITY-001, explicitly
  not executed pending his scoping. See
  [[feedback-dont-stop-at-first-plausible-fix]] for how this was found
  (Dave pushed back twice on a "done" surface fix before the real incident
  surfaced).
- **#1378 DONE**: while checking "do known-solds have operational status"
  (answer: yes, zero real mismatches, one false positive), found the eBay
  sold-webhook handler has 500'd on every real call since 2026-06-04 (two
  imports of functions that don't exist under those names in
  `tgw.workers.ebay_legacy_sync` — real names live in `tgw.ebay.pull`).
  Fixed both imports, added an end-to-end regression test, deployed.
- **#1375 logged**: Android/Tasker emergency annunciator proposal
  (Dave+Tigwa's own prior design) filed into PP-HARDWARE-001, cross-linked
  to #1346 (same producer script, needs coordination).
- **PP-POSTGRES-001 opened (design doc only, #1379)**: Dave, prompted by
  the incident chain plus a recurring SSD thermal problem: "we have been
  futzing around with json for too long. Time to grow up." Confirms a
  hybrid design from Dave's own separate Perplexity research: Postgres
  becomes item source-of-truth (identity/status/location/workflow,
  jsonb for evolving content), photos stay on disk untouched, JSON becomes
  a generated export artifact. Explicit role split (Dave's words): "once
  you have a message bus like that use it. It just isn't our state
  master" — NATS JetStream (`ITEMDATA_MUTATIONS`, already partially built
  under PP-AIOPS-001 Phase 1 but wired to the wrong door) carries the
  durable change log; Postgres holds current truth. Flagged an unresolved
  premise conflict with `PP-CATALOG-INCR-001` (assumes JSON stays truth)
  for Dave to reconcile. See [[project-status-postgres-migration]]. Full
  design: `pp/PP-POSTGRES-001.md`. Nothing built — needs a dedicated
  planning pass before any code.
- **Still open**: #1370 (flaky quota-state test isolation — worktree
  exists, no code written, own breadcrumb still active). Full test suite
  green throughout (2176 passed) except this known pre-existing failure.
- **Risk worth flagging**: the `#STATUS`→`status` write-path bug (#1376)
  means every `tgw update-verified` / bulk-status-edit call since this
  pattern started has been silently landing on the wrong field. Any
  operator workflow relying on "I set status via the CLI, it should show
  up" has not been working as expected — worth a heads-up before anyone
  leans on that path again.
