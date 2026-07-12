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

## Cross-links
- `plan/PP-AIOPS-001-cat-herding-platform.md` — execution/isolation
  substrate (audit stream, anomaly detection, litterbox, session isolation).
- `pp/PP-CATIONIX-001.md` — umbrella framing, sequencing, crypto-lock
  endgame note.
- `pp/PP-INTAKE-004.md` — Tigwa's first concrete apprenticeship task
  (justshoutit voice-operated listing + concurrent identification).
- `reference-hermes` (memory) — Hermes infra (NixOS service, model, secrets).
- [[project-catio-sequencing]] — "stabilize TGW first, cage comes last."
