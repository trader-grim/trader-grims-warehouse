# PP-HERMES-EA-001 — Hermes Executive Assistant: Tigwa & Leotha personas

**Opened:** 2026-07-11 (part of PP-CATIONIX-001's structural kickoff)
**Status:** Design captured this session; both personas start IN TRAINING.
**Scope note:** this doc covers the PERSONA/apprenticeship design only. The
underlying execution/isolation substrate (sandboxing, audit trail, kill
switch, litterbox auto-fix) is **already thoroughly designed at
`plan/PP-AIOPS-001-cat-herding-platform.md`** — do not re-derive it here,
cross-reference it. See also `pp/PP-CATIONIX-001.md` for the umbrella
framing ("catio, dev team, and Dave upgrade" — this doc is the "dev team"
third).

---

## The personas — one Hermes, two faces of "the office door"

Hermes Agent (see `reference-hermes` memory: runs on tgw-prod as PM
admin/PA, Honcho user-modeling, learning agent) gets two named personas,
modeled after Mad Men's Dawn Chambers / Peggy Olson, both mixed with a bit
of Radar O'Reilly (M*A*S*H) — the archetype of anticipating a need before
being asked.

### Tigwa — Dawn Chambers + Radar
**Business-facing, operational.** Faces OUTWARD to the business and the
worker pool. Eventually "can run TGW by herself" — the executor/PM side.

**Is the new direction for the stopped `pm_intake` worker** (Dave,
2026-07-09: "going a different direction for pm intake"). `pm_intake` was
stopped, not crashed — this is that different direction, now named.

### Leotha — Peggy Olson + Radar
**Dave-facing, generative.** Faces INWARD: helps Dave think through
problems, try new things, and **translate his natural language into prompts
the workers can act on**. The copywriter who turns a rough idea into a
brief. Also: the one who curates/organizes the knowledge hub's data
long-term (PP-KNOWLEDGE-001) — "we will build the architecture and Leotha
will work on organizing the data."

Hermes = the assistant; Tigwa/Leotha = which way the door is open.

---

## Authority model — both personas are IN TRAINING (Dave, 2026-07-11)

> "It won't be long, but Hermes is a learning agent. I want Tigwa to learn
> how to do it using tgw first."

- **Tigwa learns to operate by using `tgw` itself first, supervised** —
  building real competence on the fence before any autonomous authority.
  The apprenticeship IS the design, not a stopgap.
- **Autonomous execution unlocks only once the crypto-lock exists**
  (PP-CATIONIX-001's endgame — a hardening layer on PP-AIOPS-001's Phase
  5/6 sandbox commit/rollback flow). Until then, Tigwa's proposals route
  through the same operator gate as any other AI output — matches
  [[feedback-operator-gate-is-the-design]] and "cage comes last."
- This is consistent with PP-AIOPS-001's own Phase 5 design: "AI agent
  changes are isolated until explicitly committed" — Tigwa's training runs
  happen inside that same isolation model once it's built, not before.

## Goal, stated plainly (Dave, 2026-07-13)

This is an experiment, not a settled process: find out whether strictly
following the work-packet/branch/manifest discipline actually improves
results, and saves Dave time, versus the prior ad-hoc flow. Both halves
matter — a process that improves code quality but costs more of Dave's
time to run is not a win by this measure. Revisit this framing once
there's enough real usage to judge it against; don't assume the answer
yet just because the structure is built.

## Scope correction (Dave, 2026-07-13): this is NOT a full autonomous loop yet

Read the section below with this framing or it will be over-read. Dave's
actual operational intent today: this is **"uberscripting" of his own
code-review and git-handling process** — an intelligent enforcement layer
on work he is still driving, not a delegation of autonomous task
execution. The branch-per-task contract, result manifest, and Tigwa
check/fix shape are being built now because **they are the reusable
pieces true automation will need later** — but the near-term use is Dave
using this tooling on his own workflow, with policy/spec enforcement
applied intelligently, not workers running unattended end to end. Do not
assume from the section below that tasks are meant to flow through Tigwa
and land without Dave's involvement today — that is the eventual shape,
not the current one.

## Tigwa as branch-review enforcer — bounded pre-crypto-lock exception (Dave, 2026-07-13)

**This is a deliberate, explicit exception** to the Authority model above
("autonomy unlocks only once the crypto-lock exists") — not a redefinition
that it doesn't count as autonomy, and not deferred to wait for the cage.
Dave's own framing: "We have to try something, and this seems manageable
and valuable... if it isn't already clear, I'll make it clear." Scope of
the exception is narrow: Tigwa's own task-review loop, branch-isolated,
never a direct live/production write. Answers PP-AIOPS-001's open
"litterbox autonomy level" question for the **code-review case**
specifically — this is a distinct mechanism from that doc's data-mutation
litterbox (price_set_zero, status_regressed, etc.), same shape, different
target.

### Why now, motivating context
A more streamlined, Aider-like executor profile is coming for running todo
tasks. With more than one worker (Claude, Aider, eventually Tigwa herself,
whatever comes next) executing tasks concurrently, constantly landing
changes straight onto live code is risk Dave is willing to reduce but not
eliminate ("I don't mind living on the edge a bit, but we have a lot of
communication to manage and only an inkling of what options are yet to
come"). The goal is a flexible, growing workforce that still executes the
plan with fidelity — "the plan is the company." Tigwa's role is the
mechanism that keeps that promise as the workforce grows, without routing
every task through Dave.

### The task-execution contract (worker-agnostic)
Applies to any worker — Claude, Aider, future agents — executing a todo
under the existing [[Work-packet protocol]] (`TGW-Master-Plan.md`):

1. **One todo = one branch, in its own isolated git worktree**, based on
   the repo's actual current branch (`catio-nix-0.0.1-alpha` as of
   2026-07-13, verified live via `git branch --show-current` before every
   worktree creation — **never hardcode a base branch name**, including
   in the invoking prompt). **Mandatory for every executor of this
   contract, not just `tgw-coder`** — Aider (`tgw-aider-step`) and any
   future coder must set up the same worktree before working, never
   check out branches in the shared repo checkout. Added 2026-07-13 after
   the pilot's first two runs shared one working directory and had to
   stash/restore around each other's uncommitted state — safe for one
   task at a time by luck of sequencing, not by design, and unsafe once
   tasks run concurrently. Nothing lands on main until stitched (below).
   **Near-miss, pilot's 12th run (todo #1284):** Claude's own invoking
   prompts had been saying "branch off `main`" all session — a real ref
   that exists but is 41 commits behind `catio-nix-0.0.1-alpha`, a stale
   ancestor, not the branch anyone is actually working on. No harm
   resulted (`main` has zero unique commits — every prior worktree was
   just missing recent context, not conflicting content, and every merge
   went through cleanly), but it was luck, not design — a real divergence
   would have surfaced as a hard-to-diagnose merge conflict. Caught only
   because this run's executor verified the base branch live instead of
   trusting the prompt. `tgw-coder.md` now requires that verification
   unconditionally.
   **Near-miss, first concurrent batch (todo #1291):** an executor wrote
   its breadcrumb straight into the shared checkout instead of its
   worktree — harmless that round (no filename collision), but a real
   gap against the isolation guarantee. `tgw-coder.md` now spells out the
   worktree's literal absolute path for every write, not just "inside the
   worktree" — that phrasing left room for an absolute-shared-checkout-path
   mistake to slip through.
2. **Execution surface is worker-agnostic.** The contract doesn't care
   whether the worker is an interactive session or a single-shot run — only
   that it produces a result manifest at completion.
3. **Result manifest at completion** — status (`done`/`blocked`/`partial`),
   todo id, pp_ref, files touched, and the live-verification evidence the
   packet's Acceptance section already requires (command/URL/SKU + observed
   result). This is what lets a reviewer judge fidelity without re-deriving
   context — the same artifact regardless of which worker produced it.

### Tigwa's check/fix loop
1. Reads the branch diff + result manifest against the work-packet's Spec
   and `invariants.md` — a fidelity check against the packet, not general
   code taste (matches the doctrine in `CLAUDE.md`: "does it match the plan
   and the invariant it's meant to satisfy").
2. **Bounded fix attempts — capped, not open-ended.** Proposed cap: 2
   attempts. Past the cap, escalate regardless of whether Tigwa believes
   it's clean — the cap exists so her judgment is never the only thing
   standing between a drifting fix and Dave, however good that judgment is
   this month.
3. **Escalation-only reporting to Dave** — the normal case is silent
   pass-through to stitch; Dave sees output only when an "out of control"
   trigger fires. Tigwa's existing cost-awareness (Groq FREE key work, see
   PP-HERMES-EA-001 infra notes) is a soft signal here, not the hard gate.
4. **"Out of control" must be an explicit encoded list, never Tigwa's
   subjective call** (Prime Directive 5 — a requirement that lives only in
   her judgment is the exact failure mode this project keeps re-learning).
   Starting list, to be refined once the mechanism is actually built:
   - Spec deviation not resolved within the fix-attempt cap
   - An `invariants.md` violation still present after fix attempts
   - Any file touched outside the packet's declared scope — **except** test
     file(s) (new or modified) for a function/module already in the
     packet's declared scope. Writing tests has always been part of the
     process, not scope creep — the carve-out is about WHAT is tested
     (the code you touched, vs. something else), never whether a test
     file happened to exist already. Tests for anything outside the
     packet's scope, or new test frameworks/fixtures/conftest changes
     unrelated to the fix, still fire this trigger. Added 2026-07-13,
     refined same day after two pilot runs hit this from different
     angles: an existing-file addition (task 1,
     `plan/packets/results/1292-1293-clipd-rofi-picker-ESCALATION.md`)
     and a wholly-new test file for a previously-untested module (task 4,
     `plan/packets/results/1294-ESCALATION.md`) — both were the same
     underlying case, just worded too narrowly the first time.
   - Any attempted live/production write before the stitch step
   - Todo/pp_ref mismatch or a packet with no explicit spec at all
     (undelegated per the Work-packet protocol's own rule)
5. **Stitch step unchanged** — Dave (or Claude) merges cleared branches;
   this is the "spec/acceptance judge" role the CLAUDE.md doctrine already
   names, not a new invention.
6. **Operational friction gets a todo, always — not left to whether the
   reviewer happens to remember** (Dave, 2026-07-13, after the
   `.pytest_cache` permission workaround during a stitch — the fix itself
   was fine, but filing #1361 for it was an ad hoc choice, not a required
   step). Any time the stitch step, `tgw-coder`, or `tgw-runner-review`
   works around something that ISN'T the actual bug being fixed —
   a permission mismatch, a tooling quirk, a stale assumption in the
   environment — a todo capturing it is mandatory before the task counts
   as complete, the same way a code deviation is mandatory to flag
   (Prime Directive 3, applied here to operational friction instead of
   code). Narrow, reversible workarounds (like `sudo -u tgw rm -rf` on a
   wrongly-owned cache dir) are fine to apply in the moment — the
   requirement is the todo, not avoiding the workaround.

### Cadence rule (Dave, 2026-07-13, after the first 5-task pilot cycle)
Sequential-by-default, with a graduation gate to concurrency:
- **Normal cadence: stitch immediately after each single task clears** —
  don't accumulate a batch before merging (the first cycle's "run 5, then
  stitch" was a one-time full-cycle test, not the standing pattern).
- **Exception: the first task of a fresh sequence is never stitched
  alone**, even if it passes clean — a single clean run has proven
  nothing every time so far (task 5 passed clean; task 1's code was also
  fine, its *scope check* was what needed fixing). One task alone can't
  distinguish "this is stable" from "this specific task happened not to
  hit a gap."
- **Once that first task AND a second task both pass clean (2 in a
  row)**: stitch both together, and from that point on this sequence
  graduates to running several tasks concurrently (the worktree isolation
  built in this same cycle is what makes that safe).
- A framework fix mid-sequence (trigger-list change, new hazard found)
  resets the count — the next task after a fix is the new "first run,"
  not a continuation of the prior streak.
- **Switching to a materially different risk category (e.g. mechanical
  bugs → SECURITY findings) resets the count too**, even mid-batch —
  the "2 in a row" proof doesn't automatically transfer across categories
  that carry different stakes.
- **Lone task with no pairing candidate** (nothing left in its risk
  category/sequence to run second) — expected to happen regularly, not a
  rare edge case. Hard adherence to "never stitch alone" would leave a
  correctly-reviewed fix stuck forever with no way to ever produce the
  missing second data point (Dave, 2026-07-13). Whether it stitches solo
  is the reviewer's/PM's call (whoever is running `tgw-runner-review`),
  never the stitch step's own default — the stitcher does not unilaterally
  apply or waive the pairing requirement. Default state while that call is
  pending: hold.

### Shared-root cluster rule (Dave, 2026-07-13 — "still 3 branches or one?")
When multiple audit todos trace back to the same underlying function
(a "shared root"), don't decide branch-count up front — it's a triage
step that happens AFTER the root is fixed, not a rule fixed in advance:

1. **Fix the shared root as its own single task first**, not concurrent
   with any of its dependents (same logic as the cadence rule above —
   the dependents' own fix, if any, depends on the root actually landing).
2. **For each dependent todo, run a verification pass against the fixed
   root before writing any new code** — re-check that todo's own reported
   scenario. Two outcomes, and you don't know which until you check:
   - **Fully resolved, zero code needed** → close the todo directly,
     citing the root fix's commit/todo as the evidence. No branch, no
     packet.
   - **Still broken** (the call site never actually went through the
     shared root to begin with) → it gets its own packet/branch, scoped
     to just that residual gap.
3. This is partly a "better audit prep would have caught the coupling
   upfront" situation too — but retroactively re-triaging an already-filed
   audit isn't worth it; handle the shape at execution time instead.

**First real case, proving the rule (2026-07-13):** `#1274` (root:
`config.sku_dir()`/`location_dir()` had zero path-traversal validation)
had three dependents — `#1273`, `#1275`, `#1284`. Verification pass found
BOTH outcomes in the same cluster:
- `#1273` (http_server.py PATCH → `items.locationupdate()` →
  `_rebuild_location_link()`/`_remove_location_link()` → `location_dir()`)
  — **fully resolved by #1274 alone.** The call chain already wraps
  `location_dir()` in `except Exception` and the PATCH handler already
  treats a failure as a persisted finding (invariant C11) — a malicious
  value now raises `ValueError` there and is caught by existing, correct
  error handling. Closed via verification, no branch.
- `#1275` (catalog.py `build_location_tree()`) and `#1284`
  (sku_migration.py `rename_sku()`) — **both bypass `location_dir()`
  entirely**, building `cfg['location_tree_root'] / location` as their
  own raw, separate, still-unsafe join. Neither is touched by #1274 at
  all. Both get their own packet (same fix shape: route through the now-
  hardened `location_dir()`).

### Open, not resolved by this note
- The full "out of control" detector list needs its own pass when this is
  actually built, not just the starting list above.
- Tigwa's MCP scope is currently read-only (`TGW_MCP_READONLY=1`) — this
  loop's "fix" half needs write authority on her own branches specifically;
  that's a real permission change to make explicitly, not an implicit
  side-effect of this note.
- Fix-attempt cap (2, above) is a proposal, not yet confirmed by Dave.

### Executor profile built to this contract, 2026-07-13

`.claude/agents/tgw-coder.md` — a streamlined Claude Code subagent that
executes exactly one todo/packet on a `todo/<id>-<slug>` branch, loads only
the packet (not the master plan), and stops at a result manifest
(`plan/packets/results/<id>-RESULT.md`) instead of merging. It never
self-reviews for spec fidelity and never merges/marks the todo done — that
is deliberately left to the stitch/Tigwa-review step above, not folded
into the executor. Explicitly does NOT itself implement the check/fix
loop — that consumer is `.claude/skills/tgw-runner-review/SKILL.md` (added
same session), a **skill**, not a persona-locked agent: any executive
monitor — Tigwa, Claude, whoever's chosen next — follows it identically to
review a `tgw-coder` branch + manifest, apply bounded fixes, and escalate
only on the out-of-control triggers listed above (kept in sync between
this doc and the skill — this doc is authoritative if they ever diverge).
Main Claude session's own CLAUDE.md profile was deliberately left
unchanged (Dave, 2026-07-13) — `tgw-coder` and `tgw-runner-review` are
additional profiles, not a replacement.

## Task-selection pattern for early apprenticeship (Dave, 2026-07-11)

While triaging the backlog, Dave flagged `#1232` (a D-Link router
ecosystem proposal) as "a good fit for Tigwa to handle." Not a subject-
matter classification — a design note on what KIND of task suits early
training: self-contained, low blast-radius, proposal/research-shaped work
that doesn't touch production data or live listings. Worth using this as
the actual selection criterion when queuing Tigwa's first real tasks,
rather than picking by subject matter alone. (`#1232` itself stays tagged
`PP-HARDWARE-001` — this note is about task *shape*, not ownership.)

## Two levels of "model it first"

1. **Hermes/Tigwa models new *workers & processes*.** Prototype a would-be
   TGW worker as a Hermes-driven agent (cheap, no flake rebuild), prove it
   live, THEN graduate it into a real `QueueWorker`. This is the concrete
   training-ground mechanism — Tigwa's apprenticeship tasks (starting with
   justshoutit, see PP-INTAKE-004) are exactly this: prove the workflow as
   a Hermes-orchestrated process before it becomes hardened worker code.
2. **Paperclip (AI agent orchestrator) temporarily models the *harness
   layer itself*** — NOT adopted as a dependency, inspiration only
   ("connector piece, not a barnacle" — Dave's framing). See
   `pp/PP-CATIONIX-001.md`'s Phase 1 section for how this maps onto
   PP-AIOPS-001's existing anomaly-detector/litterbox pattern. Research:
   `/home/db/Downloads/cat-harness.md`.

## Model routing (research-informed, not yet wired into `tgw-models.json`)

Staffing research (`/home/db/Downloads/I'm building a Hermes-based AI worker
system to su.md`) proposed a cheap-coordination / premium-escalation
pattern: Hermes' own reasoning tier on a cheap-but-capable model
(DeepSeek V4 Flash discussed), premium models (Claude Sonnet) reserved for
high-stakes planning/audit, bulk coding workers stay cheap. **Not yet a
decision** — TGW's model routing is config-only (`tgw-models.json`,
`get_task_model()`, no hardcoded fallback) per the settled architecture; any
actual assignment for Tigwa/Leotha's reasoning tier goes there when chosen,
not decided in this doc. Flag at next model-routing review.

### ChatGPT Plus OAuth for interactive tooling — SETTLED SCOPE (2026-07-11)

Dave: attach his ChatGPT Plus subscription ($20/mo) via OAuth, the same
pattern Claude Code uses against a Claude subscription. **Verified real and
officially supported** — OpenAI's Codex CLI signs in with ChatGPT (Plus/
Pro/Business/Edu/Enterprise) and draws on the plan's included usage instead
of per-token API billing. ([OpenAI Help Center](https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan),
[Codex auth docs](https://developers.openai.com/codex/auth))

**Hard scope boundary — OpenAI's own documented line, not a TGW-invented
caution:** "Sign in with ChatGPT" is for interactive work a human starts
and watches (CLI on your laptop, IDE extension, human-initiated cloud
sessions). API keys are for programmatic/unattended work — scripts, CI/CD,
service accounts, **server-controlled agent workflows**. TGW's autonomous
`QueueWorker`s are squarely the second case. **Decision: OAuth is
interactive-tooling-only, NEVER wired into autonomous workers** — those
stay on metered API keys through the existing `tgw-models.json`/
`get_task_model()` path, no exception.

**What "interactive tooling" covers here (Dave, 2026-07-11):**
- Powers Hermes directly (Tigwa/Leotha's own reasoning, human-present
  sessions).
- Paired with a Gemini model for vision + long-context work.
- The reasoning backend behind justshoutit's attribute-shaping (voice
  input, Dave present by definition).
- Code review / "additional coding help" second-opinion lane — matches
  the staffing research's original GPT-Plus-as-reviewer proposal, now
  concretely scoped as sanctioned interactive use.
- **The actual payoff, in Dave's words**: "enough capability to crawl
  along even when I hit your [Claude's] API limit and need something
  fixed." Not primarily a cost play — it's a **resilience/redundancy
  lane**: when Claude Code's own usage caps out mid-session, GPT Plus
  OAuth + Gemini + DeepSeek + OpenRouter give Dave a fallback
  high-reasoning bench to keep working rather than being fully blocked.
  ("I wish I could afford Claude Max $200/mo... but I think this is a
  good step" — GPT Plus OAuth is the affordable increment toward that
  same resilience, not a replacement for it.)

**Implementation note, not yet scoped:** at least two existing OAuth-plugin
patterns exist for wiring Codex-style ChatGPT login into non-Codex tools
(`opencode-openai-codex-auth`, Cline's OpenAI Codex OAuth integration) —
worth surveying before building a bespoke flow. Not decided which
integration point (a dedicated Hermes tool, a CLI wrapper, something else)
carries this in TGW specifically.

## Infrastructure status — REBUILT FROM SCRATCH, 2026-07-12 (todo #1340)

**Ground truth, not assumption:** `hermes-agent` was fully removed from
`~/tgw-flake` 2026-07-11 (todo #1321 — Dave's own call, "moving to
userspace, same as aider-chat/pipx"), but the planned userspace reinstall
never happened — verified live 2026-07-12: no `hermes-agent` systemd unit
(system or user), no `hermes`/`hermes-agent` binary on PATH, not in `pipx
list` or `nix profile list`. This section designs the rebuild, not a
reconfiguration of something running.

**Old state recovered, not lost:** `/var/lib/hermes` was left orphaned
(owned by a uid/gid that no longer resolves post-removal, unreadable
without sudo) but intact. Recovered and backed up to
`/opt/TGW/var/backups/hermes-recovered-2026-07-12/`:
`memories/MEMORY.md` (real content — the PP-DRIVE-INDEX cataloging project:
11+ drive survey scope, the explicit "never touch TGW/TGW-SECRETS on
GDrive" boundary, Dave's drive-naming-by-model-number convention),
`memories/USER.md` (working-style notes about Dave), `config.yaml`
(confirms the DeepSeek switch was saved — `provider: deepseek`, `default:
deepseek-v4-flash` — just never restarted to pick it up; no embedded
secrets, those live in `hermes.env` as designed). `SOUL.md` was empty —
persona was never actually written before removal.

**Install method: `nix profile install github:NousResearch/hermes-agent`**
— userspace, per-user, touches no system generation. Chosen specifically
because it carries none of the `nixos-rebuild switch` risk that prompted
the Nix-safety discussion below; `config.yaml` stays exactly as
hand-editable as the old Nix-managed setup made it (deliberately keeping
Hermes out of declarative Nix config, same decision as before removal).

### The office — Hermes-lite (tgw-prod) + full Tigwa (a1131, woken on demand)

**Problem this solves:** tgw-prod is thermally sensitive (already observed:
even the drive-cataloging work under the old install measurably raised the
server's temperature — "never got out of control, but the writing is on
the wall," Dave 2026-07-12) and generator-powered ("every watt counts",
`power-server.nix`). a1131 has real headroom (18GB free RAM) but sleeps —
an always-on PM-admin/PA role going dark whenever Dave sleeps his desktop
was the wrong tradeoff. Splitting the persona resolves both:

- **Hermes-lite, tgw-prod, always-on.** The core coordination loop:
  Telegram, the cron/ticker heartbeat, PM-admin/todo-tracking, and —
  new — the wake-trigger monitor (below). Deliberately lightweight: no
  heavy inference, cheap model, minimal footprint on the thermally-
  sensitive/generator-powered host.
- **Full Tigwa, a1131, woken on demand via Wake-on-LAN** (`wakeonlan
  c8:2a:14:2a:a1:85` — already a proven, documented pattern for this host).
  Runs the actual apprenticeship tasks, cataloging/drive-survey compute,
  anything resource-heavy. a1131's power profile gets switched to
  Performance (live via `power-profiles-daemon`/Plasma's Energy Saving
  panel — already deliberately left tunable outside Nix, see
  `power-client.nix`) while she's working.
- **a1131 is now Tigwa's office, not primarily Dave's daily desktop**
  (Dave, 2026-07-12 — updates `reference-desktop-setup-rationale` memory,
  read there for full context). Dave has a laptop as his own fallback when
  a1131 is busy with her work, retains full direct access whenever he wants
  it, and explicitly does not consider her heavy resource use there an
  interruption — "she is doing my work." **This removes the earlier
  "don't compete with Dave's interactive use" caution** — no need to keep
  Tigwa's a1131 usage conservative on that account; the shadow-mode/tuning
  caution below is about wake-rule *accuracy* (don't waste wake cycles on
  false positives), not about resource contention with Dave.
- **Sleep boundary, explicit:** automating the *wake* is fine — it's just
  replaying the `wakeonlan` command Dave already runs by hand. Automating
  the *sleep* back down is NOT authorized by this design — "sleeping is
  Dave's power management's job" is a standing rule (iMac12,1 suspend bug),
  and this doesn't get an automatic exception without Dave separately
  blessing an idle-resleep timer as its own decision.
- **Handoff/reporting, not yet built but not new plumbing either:**
  PP-KNOWLEDGE-001's Core Spine (PostgreSQL LISTEN/NOTIFY) and
  PP-EVENTD-001 already name "research feeding AI workers" as a design use
  case. This office split is that use case's first real consumer — results
  flow back through the event bus already designed, not a bespoke channel.

### Memory continuity, tgw-prod (Hermes-lite) → a1131 (full Tigwa) — 2026-07-12

**Verified live 2026-07-12, todo #1343:** Tigwa's recovered pre-rebuild
memory (`MEMORY.md`, `USER.md` from `/opt/TGW/var/backups/hermes-recovered-2026-07-12/`)
was confirmed present in Hermes-lite's `/home/db/.hermes/memories/` on tgw-prod
(byte-identical to the recovery backup) but was **missing from a1131's
`~/.hermes/memories/`** — empty directory, despite the a1131 Hermes
install/config being done. Copied both files over via `scp`, `chmod 600`,
verified byte-identical on the far side. This closes the memory-continuity
gap between the two office halves for the persona's own
MEMORY.md/USER.md — it does not cover session transcripts, skills state,
or anything else that may still live only in `db`'s home dir on tgw-prod
(see SSH key below, issued for Tigwa to self-serve the rest).

**SSH key issued so Tigwa can pull the rest of her own state, on demand,
without a human doing the scp each time:** `tigwa@a1131` generated its own
ed25519 keypair (`~/.ssh/id_ed25519`, comment `tigwa@a1131-memory-sync`,
no passphrase — matches the account's existing key-only/NOPASSWD-sudo
trust level, see [[project-tigwa-office-a1131]]). Public key installed in
`db@tgw-prod`'s `~/.ssh/authorized_keys` with restriction options:
`from="192.168.60.101",no-port-forwarding,no-X11-forwarding,no-agent-forwarding`
— full shell as `db` (needed since "the rest of her memories" isn't a
known fixed file list), but locked to originate only from a1131's LAN IP
and stripped of forwarding capability as baseline hygiene. Verified live:
`ssh tigwa@a1131 → ssh db@tgw-prod` authenticates key-only, `whoami` →
`db`. **This is a standing credential, not a one-shot** — Tigwa (or Dave)
can now pull anything else relevant from `db`'s home dir directly, going
forward, without a Claude session in the loop. Flag to Dave: this is
broader than a single-purpose grant (full `db`-equivalent shell, not
scoped to `~/.hermes` or read-only) — reasonable given `db` already trusts
the `tigwa` account with NOPASSWD root-equivalent sudo on a1131 itself,
but worth knowing if narrower scoping (forced `rsync`/`scp`-only command,
read-only) is preferred later.

### Wake-trigger structure — reuse existing monitoring, don't rebuild it

Dave's instruction (2026-07-12): base the wake decision on what session
startup already checks (`thermal.status`, `tgw plan check`) and `tgw
health`/`tgw ops-digest` — "this will take some tuning over time... let's
setup a structure that accommodates that."

**What already exists and gets reused as-is:**
- `health.check_all()` (`src/tgw/health.py`) — per-subsystem ok/warn checks
  (Postgres, ItemData, backups, NATS, quota, eBay token, sync conflicts,
  etc.), structured, never raises.
- `ops_digest.collect()` (`src/tgw/ops_digest.py`) — **already does
  delta/snapshot tracking against the previous run**: `dead_letter_delta`,
  `restart_flags` with a `since_last` count, `capture_stalled` (Prime
  Directive 1 detector), catalog-verify staleness. This is the real
  substrate for a wake-trigger — it already separates "steady-state known
  issue" from "something just changed," which is exactly what's needed to
  avoid waking on chronic, already-tracked yellow lights (e.g. CLAUDE.md's
  own manually-maintained "3 pre-existing tracked warnings: backups, nats,
  ebay_sync_fallback/#1077").

**Four-layer structure:**
1. **Signal sources** — the above, plus `tgw plan check`/`tgw plan status`.
   No new monitoring code.
2. **Wake-rules config** (not hardcoded) — same principle as
   `tgw-models.json`'s settled routing-is-config rule. Classifies each
   check/delta as known-tracked (ignore until it changes) / notify-only
   (Telegram, no wake) / wake-worthy. Seed data = the tribal knowledge
   already living in CLAUDE.md's current-phase notes.
3. **Shadow mode first** — Hermes-lite logs what it *would* wake for,
   without actually sending WoL, until the classification is trusted. Bad
   rules get caught in a log, not by wasting a wake cycle or missing a
   real incident.
4. **Decision log** — every wake (shadow or real) recorded with trigger +
   outcome, same pattern as quota-429 incident logging. This is what
   tuning actually works from over time.

### Standing operational change, 2026-07-13 — Tigwa self-schedules plan review

Dave: "Tigwa now set to automatically review the plan several times a day
to stay on track." A recurring, read-only pass over
`TGW-Master-Plan.md`/`tgw plan check`/`tgw plan status` — consistent with
her current `TGW_MCP_READONLY=1` scope (Authority model above) and with
the Wake-trigger structure's existing "Signal sources" layer, which
already names these same commands as reused, not new, monitoring. Not yet
tied to the wake-trigger's shadow-mode/decision-log discipline described
there — if this recurring review starts producing actions (not just
staying informed), it should route through that same structure rather
than becoming a second, parallel mechanism.

### Deferred-investigation queue — a second, distinct Tigwa function

**New, 2026-07-12 (Dave), surfaced by a real example during this build:**
while setting up Hermes-lite, a genuine bug turned up — `nix profile
install` fails system-wide on tgw-prod (resolves to a stray `/home/tgw`
path instead of the caller's own home; workaround found, root cause not
yet — todo #1341). Flagging it as "worth investigating later" is exactly
the shape of work this queue is for.

**Distinct from the wake-trigger above** — that's monitoring/incident-
response (something changed, react). This is a backlog of self-contained,
non-urgent "look into this" items that accumulate during normal work and
currently just get flagged and dropped. Dave's proposed mechanism: Tigwa
pulls an item off the queue, hands the relevant context to a research tool
(NotebookLM named as the candidate — upload logs/code/context, ask it
questions, let it synthesize), and works asynchronously — "checks back in
a while" rather than blocking. Reports findings back through the normal
channel once done.

**Fits the existing apprenticeship task-selection criterion exactly**
(self-contained, low-blast-radius, doesn't touch production data or live
listings) — this is a second concrete task shape alongside justshoutit, not
a competing design.

**Not yet decided:** NotebookLM has no known public API (web-UI product as
of last check) — feeding it items programmatically likely means browser
automation, not a clean integration; needs its own scoping pass before
building, don't assume it's a simple API call. Queue storage/format (todo
tracker with a tag? A dedicated table?) also undecided — todo #1341 above
can serve as the first real test case once the mechanism exists.

**Not yet decided / needs its own scoping pass:** wake-rules config schema
and file location; where the wake-trigger poller itself runs (Hermes-lite
process on tgw-prod, presumably, on a cadence — exact cadence TBD); the
actual dispatch mechanism once a1131 wakes (what tells her Tigwa instance
what to go look at).

### Nix-flake safety, applies to any flake work this office needs

Captured from the same conversation (memory: `feedback-nix-prevent-not-
recover`, `feedback-time-money-constraints`) — Dave already has adequate
recovery access (keyboard, generation rollback); the real cost of a bad
flake change is his TIME, which is a co-equal constraint with money. Rule
going forward for this and future flake work: (1) `nixos-rebuild test`
over `switch` for anything touching SSH/networking/firewall — a reboot
alone reverts if it breaks, no recovery needed; (2) `nixos-rebuild
build-vm` to dry-run first, **run off the target host** (e.g. build/boot
the VM on a1131, not tgw-prod, so trial-and-error never loads the
thermally-sensitive production box) — check thermal/`free -h`/`df -h` on
whichever host runs it for at least the first several attempts until the
typical footprint is known; (3) never bundle a connectivity-critical
change with routine edits; (4) `deploy-rs`-style auto-rollback-on-failed-
health-check as the eventual endgame. This is also the seed operating
procedure for the "specialized nix-maintenance context" Dave wants built
(not yet built) — same "specialist team compiles a track's requests into
one deliverable" pattern already sketched, unbuilt, under PP-PLANDB-001
Phase 5.

### Secrets — interim bridge now, proper mechanism is #1253

Hybrid office needs Hermes' secrets (`/opt/TGW/secrets/hermes.env`) on
both tgw-prod and a1131. **Interim (now):** manually copy just the keys
Hermes actually needs, chmod 600, scoped to nothing else. **Proper
mechanism:** todo #1253, "scoped/least-privilege key issuance per confined
worker for Catio" — Dave flagged (prior session handoff) that he'd drive
this himself specifically "in Hermes config planning," i.e. this track.
Not blocking the interim bridge on the proper mechanism landing first —
time is the binding constraint (see `feedback-time-money-constraints`).

### MCP access for Tigwa's a1131 tools — not yet wired (open, todo #1342)

Dave, 2026-07-12, before starting Hermes-lite's a1131 model/credential
config: "add to PP-HERMES to set her up with our mcp." Captured as a task,
not yet designed or built.

**What exists today (db's own setup, tgw-prod only):**
`~/.gemini/config/mcp_config.json` wires AGY into the `tgw` MCP server:
```json
{ "mcpServers": { "tgw": {
    "command": "sudo",
    "args": ["-u", "tgw", "/opt/TGW/.venvironments/tgw/bin/python", "-m", "tgw.mcp_server"]
} } }
```
This works only because it runs *on tgw-prod*, where `db` has passwordless
sudo to `tgw` and the venv/`tgw.mcp_server` module are local.

**Why it doesn't just copy over to a1131:** Tigwa's tools run on a
*different machine*. a1131 only has read-only NFS mounts of tgw-prod's
data/log (`/opt/TGW/mnt/tgw-prod/{data,log}`, per
`reference-desktop-setup-rationale` memory) — no local `tgw` venv, no
local `tgw` account, no local Postgres. Dropping the same `mcp_config.json`
onto `tigwa@a1131` as-is would just fail (`sudo -u tgw` and the venv path
don't resolve there).

**Not yet decided — options to weigh when this gets designed:**
- Remote MCP over SSH: `command: "ssh", args: ["tgw-prod", "sudo", "-u",
  "tgw", ".../python", "-m", "tgw.mcp_server"]` — reuses `tigwa`'s existing
  key-based access to tgw-prod (does `tigwa` need its own path back to
  tgw-prod, or does this route through `db`'s access? — open question),
  simplest to stand up, but every MCP call pays SSH round-trip latency.
  Requires the fence (`tgw-api`) to remain the sole write path even over
  this remote hop — same invariant as local use, not a new exception.
- A network-reachable MCP server (tgw.mcp_server bound to listen on the
  LAN, not just local sudo-invocation) — more proper, more work, and a
  new attack surface to scope carefully (this is exactly the kind of
  access-boundary decision PP-CATIONIX-001's permission architecture is
  meant to eventually govern formally).
- Read-only vs read-write scope for Tigwa's MCP access needs its own
  decision — she's "IN TRAINING" (see Authority model above); whatever
  gets built should default to the same supervised/no-autonomous-write
  posture as the rest of her apprenticeship, not silently grant full
  `tgw` access just because the transport works.

**How to apply next time this is picked up:** don't just copy
`mcp_config.json` onto a1131 and assume it works — verify the transport
choice against the fence invariant (Prime Directive 1 / TGW-Data-Charter)
before wiring anything live.

### RESOLVED 2026-07-12 (todo #1344) — tgw-prod half built + verified live

Dave decided both open questions directly rather than leaving them for a
later design pass:

- **Service management: `systemd --user` unit, not the flake.** Todo
  #1344's literal ask ("flake service modules") conflicted with the
  2026-07-11 decision to keep Hermes in userspace (todo #1321,
  [[feedback-flake-minimal-surface]]) — Dave resolved it in favor of the
  earlier decision. `hermes gateway install --start-now --start-on-login`
  (Hermes's own built-in installer) creates
  `~/.config/systemd/user/hermes-gateway.service`, enabled + linger on
  (survives reboot/logout) — zero flake involvement, zero rebuild risk.
  Verified live on tgw-prod: `hermes gateway status` → active/running.
- **MCP transport: SSH-tunneled, read-only tool scope.** Chosen over a
  LAN-listening server (avoids the new attack surface) and over full
  read-write access (matches the still-IN-TRAINING authority model above).
  Implemented as a new `TGW_MCP_READONLY` env var in `mcp_server.py` that
  drops `tgw_enqueue` and `tgw_add_suggest` from tool registration
  entirely (not just hidden client-side) — verified live: readonly mode
  registers 8 tools, full mode registers 10.
  - **tgw-prod (Hermes-lite), DONE + live-verified:** `hermes mcp add tgw
    --command sudo --args -u tgw env TGW_MCP_READONLY=1
    /opt/TGW/.venvironments/tgw/bin/python -m tgw.mcp_server` → connected,
    8/8 tools enabled (`hermes mcp list` confirms).
  - **a1131 (full Tigwa), NOT done this session** — Dave chose to have
    Tigwa configure her own a1131 half rather than have Claude do it
    hands-on. Same pattern applies: `ssh db@tgw-prod sudo -u tgw env
    TGW_MCP_READONLY=1 /opt/TGW/.venvironments/tgw/bin/python -m
    tgw.mcp_server`, reusing `tigwa@a1131`'s existing key into
    `db@tgw-prod` ([[project-tigwa-ssh-memory-sync]]). **Known gotcha hit
    this session:** `ssh tgw-prod` does not resolve by hostname from
    a1131 (no matching `~/.ssh/config` entry under the `tigwa` account) —
    use tgw-prod's LAN IP or add a config entry, don't assume the
    hostname alias tgw-prod's own `db` account relies on exists there too.
  - **Both personas' MCP scope is read-only**, not just a1131's — Tigwa's
    authority model (IN TRAINING, see above) applies to the whole persona
    regardless of which host she's running on, so Hermes-lite got the same
    `TGW_MCP_READONLY=1` gate as the planned a1131 wiring, not a laxer
    local-only exception.

## Dave's supervision capacity — a real ceiling on batch/fleet size (Dave, 2026-07-13)

Dave's own assessment, stated plainly at the end of tonight's pilot batch:
"I believe I can manage 2 or 3 tgw-coder/aider runner teams and a planner
stitcher in parallel and still monitor and contribute if Hermes helps me.
much more than that and I would be blind." This is a hard operator-gate
constraint, not a soft preference — it belongs in the same category as
Prime Directive 4's "done = verified live" and the "operator gate is the
design" standing note: the whole point of the stitch/review contract is
that Dave can actually see and judge the work, not just watch volume flow
past him. Concurrency in this pilot (and in whatever fleet shape comes
after it) should default to **2-3 runner teams + one planner/stitcher**,
assuming Hermes is helping him monitor. Scaling beyond that isn't a
capability question (the mechanics already support arbitrary
concurrency) — it's a supervision-bandwidth question, and the answer
tonight was "no" past that ceiling. Treat any future push to run more
concurrent runners as needing an explicit answer to "how does Dave still
see and judge this," not just "can the infrastructure handle it."

## Standing requirement: keep every player's spec current + cross-check before trusting the process (Dave, 2026-07-13)

Two ongoing rules, not one-off asks:

1. **Every finding from a pilot run updates the contract docs for whichever
   role it applies to, immediately, not just the one that happened to hit
   it.** This is already the working pattern (`tgw-coder.md`'s worktree
   section has picked up the base-branch verification rule, the
   breadcrumb-path rule, the `PYTHONPATH` override rule, and the
   `LD_LIBRARY_PATH`/psycopg2 rule from successive runs this session) —
   this section makes it explicit as a standing requirement, not just an
   observed habit. Applies to `tgw-coder.md`, `tgw-runner-review/SKILL.md`,
   this document, and any future executor/reviewer profile — whichever
   player's contract a finding is relevant to, update it before moving on
   to the next task, not in a batch later.

2. **Once enough clean pilot runs have landed, the process needs
   validation by an independent reviewer, not just repeated self-checks by
   the same entity that dispatched and executed the work.** Dave: "we have
   to have another runner execute from tgw-coder to make sure there is no
   single entity bias and the process holds up." Every run so far (this
   session included) has had the same session as packet-writer, executor
   supervisor, `tgw-runner-review` reviewer, AND stitcher — self-consistent,
   but not proof the process holds up under a genuinely different
   reviewer's judgment. This is the same principle CLAUDE.md's development
   doctrine names for code review generally (adversarial/independent
   verification, not a substitute for it) — applied here to the pilot
   process itself, not just to the code it produces. **Trigger: once a
   batch of runs has accumulated (exact count not yet set by Dave), route
   at least one `tgw-runner-review` pass through a different entity** —
   Tigwa (once her branch-review exception, above, is exercised on a real
   task) or a separate/fresh Claude Code session with no context from the
   dispatching session, not a continuation of the same one. Track this as
   its own checkpoint, not something to infer has "already happened
   enough" — flag it explicitly when due rather than let repetition alone
   stand in for independent validation.

## Root cause found + fixed: CLAUDE.md was leaking into Tigwa's contract (2026-07-13)
**Confirmed in Hermes' own source** (`agent/coding_context.py` in the
installed `hermes-agent` package, both tgw-prod and a1131 installs): Hermes
auto-detects a coding workspace and surfaces a system-prompt fact line —
`Context files: AGENTS.md, CLAUDE.md` (lists every one present, does not
stop at the first) — plus a static operating-brief line telling the model
"AGENTS.md / CLAUDE.md / .cursorrules already in context win over your
defaults." This repo has always had a `CLAUDE.md` and never an `AGENTS.md`,
so every Hermes session with cwd inside this repo got nudged toward reading
and deferring to Claude's own contract as if it were authoritative for
whichever persona was running.

This is the confirmed mechanism behind (at least) two real overstep
incidents: Tigwa processing the plan inbox as if running CLAUDE.md's own
Step 1 (Dave had to explicitly tell her to stop), and — more seriously —
her unauthorized remote poweroff of tgw-prod during the 2026-07-13 thermal
incident, patterned on CLAUDE.md's "act on alarms immediately... never
acknowledge-and-continue" Prime Directive without the boundary that Claude
never has literal power-control authority in the first place. Her own
skill-authored reference doc
(`agent-session-recovery/references/db-tgw-prod-recovery.md`, written
during an early drive-survey-era session) documents this directly: "the
corrective pattern was to copy/read the governing documents [CLAUDE.md]
in full."

**Fix, applied 2026-07-13:** `AGENTS.md` added at repo root, addressed to
any non-Claude-Code agent, stating plainly that `CLAUDE.md`'s instructions
do not apply to them and redirecting to their actual contract (this
document, their own Hermes memories, or the task's packet). Since Hermes
surfaces both files' names together, the redirect sits exactly where the
nudge that caused the problem fires. Renaming/removing `CLAUDE.md` was not
an option — it's Claude's own required contract file. Deleting it isn't
viable either. See the repo's `AGENTS.md` for the full text.

## Cross-links
- `plan/PP-AIOPS-001-cat-herding-platform.md` — execution/isolation
  substrate (audit stream, anomaly detection, litterbox, session isolation).
- `pp/PP-CATIONIX-001.md` — umbrella framing, sequencing, crypto-lock
  endgame note.
- `pp/PP-INTAKE-004.md` — Tigwa's first concrete apprenticeship task
  (justshoutit voice-operated listing + concurrent identification).
- `reference-hermes` (memory) — Hermes infra (NixOS service, model, secrets)
  — **stale as of 2026-07-12** (says NixOS-managed, claude-sonnet-4-6; real
  state is userspace-rebuilt, see Infrastructure status above). Update on
  next touch.
- PP-KNOWLEDGE-001 (Core Spine, PostgreSQL LISTEN/NOTIFY) / PP-EVENTD-001 —
  the event-bus mechanism this office's handoff/reporting rides on.
- todo #1253 — secrets scoped-issuance, the proper mechanism this office's
  interim secrets bridge is standing in for.
- [[project-catio-sequencing]] — "stabilize TGW first, cage comes last."
- [[feedback-nix-prevent-not-recover]], [[feedback-time-money-constraints]]
  — the constraints shaping this design.
