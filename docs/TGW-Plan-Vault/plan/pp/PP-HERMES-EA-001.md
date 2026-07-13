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
