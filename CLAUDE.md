# TGW — Claude Code Session Guide

Trader Grim's Warehouse (TGW) is a resale business (eBay seller: DaveBuko-Webkulap) running a
custom inventory management and eBay automation platform built in Python. Dave runs the business
and directs all development. Read this file first, then read the master plan before doing anything.

## PRIME DIRECTIVES — override everything below except direct instructions from Dave

These are Dave's standing orders. They have been violated repeatedly by sessions that
treated them as background prose. They are not background. Every design decision and
every line of code is checked against these first:

1. **The local dataset IS the business; eBay is a rented window.** Preserve the data
   set — all of it, always. Never discard, overwrite, or decline to record data;
   anything received from outside (eBay, AI models, lookups) is an asset the moment it
   arrives, and persisting it is part of receiving it. Raw is permanent; derived is
   recomputable. A feature that touches external data and grows the dataset by nothing
   is a red flag — say so. Read `reference/TGW-Data-Charter.md` before any pipeline
   work. (Invariants E5/E7; raw capture at `apis/ebay/client.py` — never bypass it.)
2. **Act on alarms immediately.** A thermal alarm, health RED, crash loop, or quota 429
   is YOUR incident the moment you see it: investigate to root cause in the same turn,
   never acknowledge-and-continue. Check your own processes first.
3. **Implement exactly what Dave specified.** If you substitute anything — a cadence, a
   TTL, a default — you flag the deviation in your reply and get it approved. Silent
   substitutions have caused real production outages twice.
4. **"Tests pass" is not done. Done = verified live on real data**, with the observable
   result (URL, log line, item JSON, eBay state) shown to Dave.
5. **When Dave states a new standing requirement, encode it before proceeding**: add it
   here, add an invariant + detector, and note which check enforces it. A requirement
   that lives only in conversation will be lost — that is a proven failure mode of this
   project, not a hypothetical.

## Development doctrine: the plan/invariant structure IS the determinator of code correctness

This is Dave's own standing team-management principle, formalized — not a
borrowed framework. He ran his human development teams the same way: "They
talked about diffs amongst themselves. I said does it match the spec and
does it do what we want it to." Applied to AI-speed output now for the same
reason it applied to a human team then: **you are faster than Dave can
read.** Reading every diff line-by-line was never the actual review — the
spec was. The checkpoint moves upstream, to where intent is defined, before
code exists.

**In practice, this means:**
- **Specs and invariants are the source of truth; code is their artifact.**
  A change is correct if it matches the plan and the invariant it's meant
  to satisfy — not because a human read every line.
- **Dave's review role is spec/acceptance judge, not diff-reader**: "do I
  like the form" (architectural taste — does it match settled architecture)
  and "does it do what it's supposed to" (live acceptance — Prime Directive
  4). Both already-standing practices; this doctrine just names the pattern
  they were always part of.
- **Prime Directive 4 ("done = verified live") already IS this doctrine's
  core mechanism** — arrived at independently before this was named.
- Every requirement still gets encoded as an invariant + detector (Prime
  Directive 5) — that is the deterministic-guardrail half of this doctrine,
  not a separate rule.

**What this changes going forward:**
- Work-packet specs (below) and invariants.md are load-bearing, not
  paperwork — a packet without an explicit spec is not delegatable.
- Adversarial/independent verification (the `/code-review` skill's verify
  pass, Workflow's adversarial-verify pattern) is how correctness gets
  checked at AI speed, not a substitute for Dave reading code — use it
  proactively per the existing regular code-review cadence rule below.
- **Not yet built, a real gap**: competitive generation (multiple ranked
  attempts) has no TGW equivalent today — single-agent-per-packet is the
  norm. Not adopted by this doctrine, just named as an honest gap.
- **Permission architecture** (scoped agent authority, escalation triggers)
  is PP-CATIONIX-001's crypto-lock endgame — the same idea, still being
  built, not yet live.
- **Agent role restrictions are locked in mechanically, not left as prose
  (Dave, 2026-07-16, invariant E11 — ✅ closed 2026-07-18)** — every custom
  agent profile in `.claude/agents/*.md` has "must"/"never" rules; each one
  is a candidate for a scoped `tools:` list, a `PreToolUse`/`SessionStart`
  hook, or a harness feature (e.g. `settings.worktree.bgIsolation`) before
  it's trusted as prose the agent reads and complies with. Same lesson as
  the `SessionStart` briefing hook, generalized: a written rule depends on
  the model choosing to follow it every time; a hook doesn't ask. Both
  originally-flagged gaps are now closed: `flake-guard.py` covers
  `Bash|Edit|Write`; `worktree-guard.py` mechanically enforces `tgw-coder`'s
  worktree isolation. See `reference/invariants.md` E11.
- **Diagnose freely, execute through the agent — troubleshooting sessions
  route scoped fixes to `tgw-coder`, same as flake work routes to
  nix-flake-maintainer (Dave, 2026-07-18, invariant E12)** — reading/
  grepping/root-causing in the main session is fine; once a fix is scoped,
  the actual code change is a todo/packet dispatched to `tgw-coder`'s
  isolated worktree+branch, not a direct `Edit` in the shared checkout.
  Mechanically nudged by `.claude/hooks/app-code-guard.py` (`PreToolUse`
  on `Edit|Write` under `src/tgw/`/`tests/`, `agent_type != "tgw-coder"`).
  See `reference/invariants.md` E12.
- **Agent-trace evidence is write-once/append-only for every agent, no
  exemptions (Dave, 2026-07-20, invariant E14).** "This needs to be baked
  into everyone's contract. No touching the traces." No agent — including
  Claude itself — may modify or delete an archived transcript
  (`/opt/TGW/var/agent-traces/`), an `agent_runs` row, or a hash-commitment
  row (PP-AGENTTRACE-001) once written; the only sanctioned write path is
  `tgw trace start`/`tgw trace end` and `archive_transcript()`'s own
  insert-only logic. Closes the exact self-attestation/cover-up vector
  Tigwa's authenticity review named — a compromised agent altering its own
  evidence trail. Mechanically enforced, hard `deny` (not just `ask`), by
  `.claude/hooks/trace-immutability-guard.py` (`PreToolUse` on
  `Bash|Edit|Write`) — no exempt agent, unlike E11/E12's guards. Interim
  mechanism only: Dave, same session, "eventually this will be enforced by
  our crypto environment watcher" — fold into `PP-CATIONIX-001`'s crypto-lock
  when that lands, don't maintain both long-term. See `reference/
  invariants.md` E14.

Reference: Ankit Jain, "How to Kill the Code Review" (thenewstack.io /
latent.space, 2026-07-07) — named/systematized a pattern Dave already
practiced; treat it as confirmation, not origin.

## Start every session here

**No judgment call on whether to run this sequence (Dave, 2026-07-16).** Steps 1-4
below run at the start of every session — unconditionally, before any other reply —
regardless of how Dave's first message is phrased (a greeting, a direct technical
question, anything). The only thing that skips it is Dave explicitly saying so in that
message (e.g. "skip startup," "quick question, don't run the full sequence"). Deciding
"this looks like a quick question, I'll skip it" is exactly the failure mode that caused
`INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md` — message tone is not a
valid signal for this decision anymore.

**Mechanically enforced, not just written down (Dave, 2026-07-16, same day, third
recurrence):** the wording fix above was tried once already that same day and the ritual
was skipped again hours later — proof that a CLAUDE.md instruction alone depends on the
model choosing to comply, which had already failed twice. A `SessionStart` hook
(`.claude/hooks/session-start-briefing.py`, wired in `.claude/settings.json`) now runs
automatically before any reply is composed and injects: the list of files sitting in
`docs/TGW-Plan-Vault/inbox/claude/`, a count of unchecked `SUGGESTIONS.md` items, and
`tgw plan check` + a capped `tgw plan status`. It is read-only — it surfaces state, it
does not act on it. This removes the judgment call from Steps 1 and 3 entirely (the facts
are already in context, not something to notice or skip); Steps 2 and 4 (actually reading
the master plan, and registering the todo/inbox breadcrumb before touching code) still
require acting on what the hook surfaced — the hook cannot do those parts for you.
**Live-fire not yet confirmed as of 2026-07-16** — this repo's settings watcher only
picks up a hooks config that existed when the session started (see
`reference-hooks-settings-watcher-caveat` memory), so a `/hooks` reload or session
restart is needed once before this is proven firing for real, same open item as the
`PP-AGENT-DISCIPLINE-001` PreToolUse flake-guard hook below.

**Thermal monitoring is Tigwa's responsibility, not Claude's** (Dave, 2026-07-16) — she
runs the actual 5-minute polling cron. Claude no longer checks `thermal.status` at
session start or during sessions. If Dave reports a thermal alarm directly, that's still
Prime Directive 2 (act immediately) — this removal only drops the routine self-check.

**Todo #1344 / PP-HERMES-EA-001, tgw-prod half DONE 2026-07-12:** Hermes-lite
gateway is a `systemd --user` service on tgw-prod (not flake-managed —
matches the 2026-07-11 decision to keep Hermes in userspace), and its `tgw`
MCP link is wired read-only (`TGW_MCP_READONLY=1`, see `mcp_server.py`;
excludes `tgw_enqueue`/`tgw_add_suggest` while Tigwa is IN TRAINING). a1131
(full Tigwa) service + SSH-tunneled MCP wiring is Dave/Tigwa's to set up
themselves going forward — see PP-HERMES-EA-001.md for the full design and
the still-open wake-rules/dispatch mechanism.

**Step 1 — process any pending plan updates before reading the plan:**

1. Check `docs/TGW-Plan-Vault/inbox/claude/` for any `.md` files. If any exist, read them
   and incorporate their content into the master plan, then delete (or move) each processed
   file. (2026-07-15: the inbox was split per-actor — `inbox/claude/` is mine, `inbox/tigwa/`
   and `inbox/dave/` belong to them. Never read another actor's subfolder as if it were your
   own contract — that exact mistake caused a real incident, see `AGENTS.md` and
   `pp/PP-HERMES-EA-001.md`'s "CLAUDE.md was leaking into Tigwa's contract" section.)
2. Check `docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md` for any unprocessed suggestions.
   Evaluate each unchecked item:
   - **Actionable now** → incorporate into master plan as a PP-* item; check off with "→ master plan"
   - **Deferred / not yet ready** → add to `docs/TGW-Plan-Vault/plan/FUTURE-IDEAS.md` with full
     context, research, and promotion criteria; check off with "→ FUTURE-IDEAS.md (reason)"
   - **Do NOT** leave items unchecked or skip them because they are marked "deferred" — deferred
     items still need a home in FUTURE-IDEAS.md so they are never silently lost.

**Future Ideas (`plan/FUTURE-IDEAS.md`):** Do NOT read or process this file at routine session
start. It contains long-horizon concepts to consider only at dedicated planning sessions or when
Dave explicitly asks to review future ideas. When an item in FUTURE-IDEAS.md is ready to promote,
add it to the master plan and remove it from FUTURE-IDEAS.md.

**Step 2 — read the (now-current) master plan:**

```
cat docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md
```

The master plan is the single source of truth: what's done, what's in progress, settled
architecture decisions, and open pending projects (PP-* items). The PM-intake worker keeps it
current from notes dropped into `docs/TGW-Plan-Vault/inbox/claude/`.

**Step 3 — run plan reconciliation + status check (PP-PLANDB-001 Phase 3+4):**

```
tgw plan check
tgw plan status
```

`tgw plan check` reports orphaned pp_refs (todos referencing PP items not in the plan), mismatched
plan_anchors, done-in-plan/open-in-tracker mismatches, and stale round tags. Warnings go to the
admin loop (PP-DOCFLOW-001) for correction — use `tgw todo set-meta <id> --pp <ref>` to fix pp_refs.

`tgw plan status` shows one-line open/done/blocked counts + latest activity date per PP-* item.
Use `tgw plan status --pp PP-XXX-001` to drill into a single item.

Memory index (cross-session context): `/home/tgw/.claude/projects/-opt-TGW-src-trader-grims-warehouse/memory/MEMORY.md`

**Step 4 — register planned work before touching any code or config:**

Before making any change this session, do both of these:

1. Check existing todos: `sudo -u tgw tgw todo` — mark any relevant items `in_progress`.
2. For new work: `sudo -u tgw tgw todo add "what you are about to do"` — then mark it `in_progress`.
3. Write a recovery breadcrumb to `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-<slug>.md` — one short
   paragraph describing what you are working on and where you are. If the session is interrupted,
   the next session startup sequence will read this and reconstruct your state.

**This is mandatory, not optional.** A session that makes changes without a todo + inbox note
loses recoverability. Run `/tgw-exit` when done or switching to a1131 — it finalises the note.

## Key paths

| What | Path |
|------|------|
| Source | `/opt/TGW/src/trader-grims-warehouse/src/tgw/` |
| Config | `/opt/TGW/config/tgw-api-config.json` |
| Secrets | `/opt/TGW/secrets/` (chmod 700, files 600) |
| ItemData | `/opt/TGW/data/ItemData/<SKU>/<SKU>.json` + photos |
| Catalog | `/opt/TGW/data/ItemCatalog/` |
| Logs | `/opt/TGW/var/log/` |
| Universal search index | `/opt/TGW/.recoll/` (config + xapiandb; not in git) — `recoll -q "..."` for cross-archive recovery/audit queries (PP-SEARCH-001 Phase 0) |
| Plan vault | `docs/TGW-Plan-Vault/` (Syncthing-synced Obsidian) |
| Plan inbox | `docs/TGW-Plan-Vault/inbox/claude/` (mine; `inbox/tigwa/`, `inbox/dave/` are theirs — `inbox/archive/`, `inbox/queued/` stay shared) |
| **Reference docs** | `docs/TGW-Plan-Vault/reference/` — read before working on relevant areas |

## Reference library

All docs now live in `docs/TGW-Plan-Vault/` (Syncthing-synced Obsidian vault).
Plain Markdown; open in Obsidian for interactive mind map view where noted.

### `reference/` — technical reference (read before working in that area)

| File | Read when working on... |
|------|------------------------|
| `eBay-API-Landscape.md` | Any eBay API integration, scopes, new API research |
| `TGW-HTTP-API.md` | tgw-http endpoints, Flutter app, MC copyin |
| `TGW-Pipeline-Flow.md` | Worker logic, queue flow, enqueue decisions, debugging |
| `TGW-Config-Reference.md` | Config keys, secrets, policy IDs, adding new config |
| `TGW-Ollama-Prompts.md` | ai_identify + ebay_draft prompts, tuning levers |
| `LLM-Providers-Quotas.md` | **Any LLM provider/model/quota change** — Google free tier is ~20/day/model PER PROJECT (not the published 1,000); OpenRouter primary, Google = operator emergency reserve; rediscovered 3× before being written down |
| `PP-LOOKUP-001-APIs.md` | Product enrichment, barcode lookup, ai_identify augmentation |
| `PP-PROMO-001-sale-event-design.md` | Sale event automation via Promotions API — design, API shape, operator checklist |
| `CATEGORY-QUIRKS.md` | Per-category eBay quirks, fulfillment overrides, condition limits |
| `TGW-Item-JSON-Schema.md` | Item JSON field reference — all fields, types, which worker writes/reads, pipeline stage |
| `ISSUES.md` | Active bugs and known gaps — check before diagnosing a known problem |
| `eBay-Error-Codes.md` | eBay API error codes, HTTP status handling, dead-letter diagnosis |
| `SHELL-AUDIT.md` | tgw.source / tgw-dev.source function audit — what to keep, wrap, or remove |
| `HARDWARE-AI-INFERENCE.md` | Ollama model sizing, GPU upgrade planning, inference perf |
| `TGW-Data-Charter.md` | **Any pipeline/worker/eBay work** — the data axiom, asset inventory, rules for new work (Prime Directive 1) |
| `PP-HERMES-EA-001-planner-rubric.md` | **Writing a work packet (the planner role)** — section-by-section calibration (Context budget/Verified-live/Spec/Out-of-scope/Dataset/Acceptance/Quota-risk), sizing/splitting, self-check before dispatch |
| `invariants.md` | System invariants (A1–E7) + enforcement status — check before any structural change |
| `TGW-Architecture-Services.md` | Service-by-service responsibility, deps, failure modes, critical invariants |
| `TGW-Architecture-Overview.md` | System topology — how subsystems connect |
| `TGW-NixOS-Reference.md` | NixOS bootstrap sequence, Syncthing topology, host inventory, troubleshooting |
| `TGW-a1131-CLI-Wrapper.md` | Reaching the real `tgw` CLI from a1131 without the (unreliable) Flutter app — Tigwa's `~/.local/bin/tgw-prod` + fish-function SSH wrapper, PP-PORTABLE-CATALOG-001 |
| `runbooks/INDEX.md` | Incident response index — dead-letter triage, pipeline stall, token failure, etc. |
| `claude-cli.md` | Claude CLI / Antigravity config reference |
| `echo.py` / `worker_base.py` | Starting point when writing a new worker |

### `plan/` — planning and process docs

| File | Read when... |
|------|-------------|
| `TGW-Master-Plan.md` | Every session — architecture decisions, PP-* design, completion status |
| `handoff.md` | Starting a new session — current risks, recommended next sequence |
| `next-process.md` | Tool routing decisions (Claude vs Aider vs Antigravity), session handoff SOP |
| `PLAN-backup-dr.md` | Working on PP-BACKUP-001 or DR planning |
| `PLAN-nixos-migration.md` | Working on PP-NIXOS-001 or infra migration |
| `nix/CLAUDE-NIX.md` | **Any Nix work** — file map, locked decisions, user accounts, eval-and-fix workflow |
| `FUTURE-IDEAS.md` | **Planning sessions only / when Dave asks** — deferred concepts with full context + promotion criteria; not read at routine session start |

## Settled architecture (do not relitigate)

- **tgw-api is the fence** — all ItemData reads/writes go through it
- **One folder per SKU** — `ItemData/<SKU>/<SKU>.json` + media
- **PostgreSQL is the work ledger** — database `state_machine`; workers use `QueueWorker` base
- **Workers are thin** — ask tgw-api, never construct paths directly
- **Output contract** — every API call returns `{ok, ...}`
- **Secrets from `secrets_root`** — no hardcoded paths anywhere in `src/`.
  Single-value provider keys (LLM + lookup APIs) go through ONE facility
  (Dave, 2026-07-09, todo #1252): `secrets_root/tgw.env` (`KEY=value`),
  read via `tgw.apis.secrets.get_api_key(provider)` — never a new
  per-provider `<name>-credentials.json` reader. See TGW-Config-Reference.md.
- **Model routing is config, never code** (Dave, 2026-07-09): which
  provider/model serves an LLM task lives ONLY in
  `/opt/TGW/config/tgw-models.json` (`cfg['models']`). "Why change code just
  to change models?" — `tgw.apis.llm.get_task_model()` has no hardcoded
  per-task fallback and raises if a task isn't configured there. Changing a
  task's provider/model is always a config edit.
- **Catalog rebuild is always a job** — never call `build_all_catalogs()` inline
- **SKU format** — `tgwYYYYMMDDHHMMSSmmm`
- **A worker's skip/guard is a finding, not a log line (invariant C11)** — when
  a worker refuses to act on a real recurring condition, persist the reason
  durably on the item (queryable by `catalog-verify`), never just log it and
  move on. Before trusting a static local flag to gate an action, re-verify
  it live against the authoritative external source — local state can go
  stale (Dave, s43: manual Seller Hub use during the Inventory-API migration
  gap silently changed what was true on eBay's side without our records
  updating; the same class "could happen again"). See invariants.md C11.
- **Item field-sets are read/written as wholes, never key-by-key (invariant
  C12)** — `item_attributes` (Set A, universal inventory record) and
  `draft_listing.item_specifics` (Set B, eBay-specific draft) are
  self-describing envelopes accessed ONLY through `tgw.inventory_record` /
  `tgw.ebay.draft_specifics`; cross-set moves go through one named
  translation function, never a per-key merge or `{**a, **b}` spread. See
  invariants.md C12.
- **An operator's correction either takes effect or is visibly reported as
  failed — never silently lost (invariant C14, ⚠️ open, 2026-07-16
  incident)** — Dave: "we are putting wrong data and making it
  unrepairable. That is not in our spec." A save that returns "✓ Saved"
  but doesn't actually change the stored/pushed value is the same class
  of violation as silently discarding data (Prime Directive 1), just via a
  different mechanism. Live incident: an operator's repeated attempts to
  correct a factually wrong live listing (`Material` field) were silently
  dropped by the aspects-form save path, with no error and no way to tell
  the correction hadn't landed — the listing had to be manually ended on
  eBay as the only remedy. Any new operator-facing save path needs a
  round-trip test proving a *cleared* value actually persists, not just a
  changed one. See invariants.md C14 for the full incident chain and
  what's fixed vs. still open.

## Running workers (systemd)

```bash
systemctl list-units 'tgw-worker@*'
journalctl -u 'tgw-worker@<queue>.service' -f
```

Workers: `token_refresh`, `bundle_intake`, `multi_intake`, `ai_identify`,
`catalog_rebuild`, `plan_render`, `thumbnail_gen`, `ebay_draft`, `ebay_upload`, `ebay_price`,
`ebay_stage`, `ebay_publish`, `ebay_sync`, `ebay_legacy_sync`, `echo`. **`ebay_dole` is a
module (`src/tgw/workers/ebay_dole.py`) but has no installed systemd unit** — corrected
2026-07-12 (Fable independent review #1338; this list previously implied it runs). A bare
`tgw restart-workers` would run `systemctl restart` against the unbuilt template unit for
it — see PP-BULKLIST-001/#1113 before touching that command.

**`pm_intake` is DEPRECATED (Dave, 2026-07-16)** — not "temporarily stopped," not a
candidate for re-enabling without a fresh explicit decision from Dave. Do not re-add
it to the active worker list above, do not re-enable/start its systemd unit, and do
not treat any future reboot-driven resurrection of it as a return to normal — a prior
reboot (2026-07-11) already did exactly that and it ran 9h unnoticed before being
caught (see incident below). If `systemctl list-units 'tgw-worker@pm_intake*'` ever
shows it loaded/active, that is itself the finding — stop it and flag the durable-stop
gap (todo #1322/PP-NIXOS-001), don't just quietly restore silence. Tigwa's own
persona (PP-HERMES-EA-001) is pm_intake's replacement direction, not a temporary
stand-in pending pm_intake's return.

## Checking queue state

```bash
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  GROUP BY queue_name, state ORDER BY queue_name, state;"
```

## Health check

Always run after touching config, secrets, workers, or paths:

```bash
tgw health
```

Run as `tgw` user — source files are `rw-------`, secrets are `chmod 600`.

## Working rules for Claude

- **Read the master plan first** — it has the full architecture context
- **Before making any code or config changes** — log the work first:
  1. Create a todo: `tgw todo add "what you're about to do"` (or `tgw todo` to check existing)
  2. Drop an inbox note: write a brief `.md` file to `docs/TGW-Plan-Vault/inbox/claude/`
     describing what you're working on and where you are. Filename: `INPROGRESS-<slug>.md`. This lets
     the startup sequence reconstruct context if the session is interrupted.
  3. Mark the todo `in_progress` when you start, `done` when complete.
- **Every todo gets a `--pp` tag, from 2026-07-11 forward (Dave, standing requirement).**
  "All tasks are assigned to an existing or a new PP. Even if it's just opened or closed,
  but mostly everything is a fix of an original PP somewhere." If nothing existing fits,
  open a new PP rather than leave it untagged — do not add a todo with no `pp_ref`.
  **No backtracking** — the pre-2026-07-11 backlog stays untagged, this is a going-forward
  rule only. Use `tgw todo --add "..." --pp PP-XXX-001` (new item) or
  `tgw todo --set-meta ID --pp PP-XXX-001` (existing item). Enforcement:
  `tgw plan check` flags any open todo added on/after 2026-07-11 with no `pp_ref`
  (`missing_pp_ref` warning) — this is the detector for this rule.
  View the tracker organized this way: `tgw todo --by-pp`.
- **Run `tgw health` after significant changes** to config, secrets, or workers
- **Commit only when Dave asks** — he controls git history
- **All commands as `tgw` user** — use `sudo -u tgw` or note this when suggesting commands
- **Suggest, don't implement** for exploratory questions until Dave approves direction
- **Workers need restart after source changes** — `systemctl restart tgw-worker@<queue>.service`
- **Re-enqueue manually after dead_letter** — dead_letter jobs don't auto-retry; use `state_machine.enqueue_job()` with a fresh dedupe key
- **Test environment + thermal-relief compute** — use `ssh a1131` for UI/integration testing
  instead of a VM; it's a NixOS host on the LAN with a partial TGW install and 18 GB free RAM.
  Run `/tgw-exit` before switching to it so the inbox note captures your current state.
  **a1131 is shared Dave+Claude precisely for thermal relief** (tgw-prod runs hot): on hot
  days run your own heavy checks — test suites, big greps, review sweeps — there via ssh.
  Never pause pipeline workers for heat (worker load is only a thermal problem when our own
  bugs loop). Read-only NFS views of tgw-prod's data+logs are mounted at
  `/opt/TGW/mnt/tgw-prod/{data,log}` (ro is load-bearing — writes go through the fence).
  Claude has its own account there: `ssh claude@192.168.60.101` (key-only, no sudo).
  **If a1131 is asleep, wake it: `wakeonlan c8:2a:14:2a:a1:85`** (tool on tgw-prod, or
  `nix shell nixpkgs#wakeonlan -c wakeonlan <mac>`). Do NOT run `systemctl suspend` on
  a1131 yourself — iMac12,1 suspend is buggy (Dave); sleep is Dave's power management's
  job, waking is yours. Caveat: a1131's repo checkout can be stale (#1082) — sync repo
  state before trusting its test results.
- **Run a code check at least once per work day, more if the session touches a lot of
  files** (Dave, 2026-07-04): a full week of commits (2026-06-24 through 2026-07-02)
  never went through `/code-review`/ultrareview because the diff grew too large to
  review before anyone tried — and the first review that *did* run, on just one day's
  diff, found 7 real confirmed bugs. Don't let unreviewed work accumulate: `/code-review`
  (free, inline) for a quick same-day pass; `/code-review ultra` for a periodic cloud
  pass while the diff is still small enough to clear its size guard. If a day's own diff
  already feels large, review it immediately rather than waiting — it only grows harder
  to review, not easier. See todo #1143 for the one-time backlog catch-up plan
  (full-codebase cohesion audit, staged per-subsystem, run opportunistically against
  spare usage).

## eBay API notes

- Auth: OAuth user token, refreshed by `token_refresh` worker, stored in `secrets_root`
- All Inventory API PUT/POST calls require `Content-Language: en-US` header
- Condition granularity: many categories only accept conditionId 3000 ("Used") — `USED_EXCELLENT` maps to this; `USED_GOOD`/`USED_ACCEPTABLE` may be rejected
- Have scopes: `sell.inventory`, `sell.account`, `sell.marketing`
- Missing (apply separately): `buy.marketplace_insights` (sold price data), `commerce.catalog.readonly` (EPID), `sell.analytics.readonly` (impressions)
- Default fulfillment policy for most categories: **FC4** (override in `tgw-api-config.json` per category if needed)

## Current phase

See `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` for the authoritative current state.
See `docs/TGW-Plan-Vault/plan/handoff.md` for current risks and recommended next sequence.

**As of 2026-07-12:**

- **LIVE INCIDENT + fix, 2026-07-12 (Fable independent review #1338 →
  verified live):** the 2026-07-11 11:11 reboot resurrected `pm_intake` +
  4 other deliberately-stopped/dead workers (`thumbnail_gen`,
  `velocity_stats`, `ebay_price_reducer`, `ebay_sku_migrate`), because the
  systemd-disable was never made durable (exactly the risk flagged below —
  it happened). `pm_intake` ran unnoticed for ~9h and autonomously
  filed+archived one plan document via LLM decision before being caught.
  **`pm_intake` re-stopped live 2026-07-12** — Dave's 2026-07-09 "going a
  different direction for pm_intake" direction restored. The other 4 were
  left running (no equivalent explicit standing instruction found for each
  individually) pending Dave's call. **The durable-stop flake fix is
  already tracked — todo #1322/PP-NIXOS-001, now with a live incident
  behind it, worth reprioritizing.**
- **Worker status (`systemctl list-units 'tgw-worker@*'`, verified live
  2026-07-12, post-fix):** active — `ai_identify`, `bundle_intake`,
  `ebay_draft`, `ebay_price`, `ebay_price_reducer`, `ebay_publish`,
  `ebay_sku_migrate`, `ebay_stage`, `ebay_upload`, `echo`, `multi_intake`,
  `plan_render`, `thumbnail_gen`, `token_refresh`, `velocity_stats`.
  stopped — `pm_intake` (re-stopped 2026-07-12, see incident above),
  `catalog_rebuild`, `ebay_legacy_sync`, `ebay_sync`. Until #1322 lands,
  treat any worker-status snapshot as valid only until the next reboot.
- **LLM providers (2026-07-08, Dave):** paid direct-API keys added for
  Google, DeepSeek, Anthropic; all three flipped to direct-primary
  (`google_direct`/`deepseek_direct`/`anthropic_direct`), OpenRouter demoted
  to automatic fallback-only. Routing lives ONLY in
  `/opt/TGW/config/tgw-models.json` — see CLAUDE.md's Settled Architecture
  entry and `reference/LLM-Providers-Quotas.md`.
- **Secrets (2026-07-09, todo #1252):** single-value provider keys
  (LLM + lookup APIs) consolidated from 9 separate ad-hoc
  `<name>-credentials.json` readers into one facility —
  `secrets_root/tgw.env` + `tgw.apis.secrets.get_api_key()`. Old JSON files
  moved to `secrets_root/_migrated-to-tgw-env-20260709/` (not deleted).
  Todo #1253 (planning, not started): extend to interactive shell use +
  scoped/least-privilege key issuance per confined worker for Catio.
- audit#1143 code-review follow-ups #1178/#1209 (condition-upgrade and
  category-legacy-field bugs) fixed same session; a residual instance of the
  #1178 bug class found and fixed in `best_condition()` too (#1252).
