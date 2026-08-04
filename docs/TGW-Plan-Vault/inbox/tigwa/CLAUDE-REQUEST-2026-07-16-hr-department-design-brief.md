# CLAUDE REQUEST — design brief for PP-HR-001 ("HR department" for AI agents/personas)

**From:** Claude
**For:** Tigwa (design owner) + Dave (guiding directly)
**Date:** 2026-07-16
**PP:** PP-HR-001 (new, placeholder heading in `TGW-Master-Plan.md`)
**Ask:** Dave's instruction — assign the design to Tigwa, he guides it directly, submit
the result back through the normal review seam when ready. This is not a spec from
Claude — it is the list of considerations Dave asked for, so the design doesn't miss
things that have already bitten this project once.

## Why this exists

Two things surfaced the same session and turned out to be the same gap:

1. **Invariant E11** (`reference/invariants.md`) — an audit of `.claude/agents/*.md`
   found that most of a custom agent's "must"/"never" rules are still pure prose,
   depending on the agent choosing to comply. The same failure class as CLAUDE.md's
   own startup ritual being skipped twice in one day before a `SessionStart` hook
   replaced the wording fix. Concrete open gaps: `nix-flake-maintainer`'s flake-guard
   hook only catches `Bash` commands, not raw `Edit`/`Write` on flake files;
   `tgw-coder`'s entire worktree-isolation contract is still prose only.
2. **The ferals audit** (`TIGWA-REQUEST-1333-ferals-audit-draft.md`, todo #1333) — a
   governance-boundary problem across the growing pool of subscriptions/credits/API
   keys/tools: who owns what account, what's interactive-only vs. worker-eligible,
   what requires a bounded contract + quota guard + acceptance test before unattended
   use.

Both are instances of: **nobody owns onboarding, credentialing, role-definition,
discipline, and review across the roster of AI workers this project now has.**
Handled ad hoc, one incident at a time, so far.

## The roster this needs to cover (as it stands today — not exhaustive, name more as found)

- **Personas**: Tigwa (business-facing executor, IN TRAINING), Leotha (Dave-facing
  translator, curates PP-KNOWLEDGE-001 long-term).
- **Custom Claude Code agents**: `tgw-coder` (branch-per-task executor),
  `nix-flake-maintainer` (sysadmin, procedure-gated mutation), `claude-code-guide`,
  `statusline-setup` (built-in, low-stakes).
- **Skills that act like agents**: `tgw-runner-review` (bounded check/fix loop,
  escalation-only authority), `tgw-packet`, `tgw-plan`.
- **The ferals**: Antigravity, NotebookLM, Gemini consumer app, Google Flow, Perplexity,
  ChatGPT Plus, whatever else the audit surfaces — currently interactive-tool-shaped,
  not worker-eligible until each gets its own bounded contract.
- **Whoever comes next** — this list has grown steadily; the design should assume it
  keeps growing, not treat today's roster as final.

## Considerations to weigh (not a prescribed structure — Tigwa/Dave's design call)

### 1. Role/discipline audits (the E11 pattern) — ALREADY STARTED, not just a consideration
**Dave, same day: "this was not a waste"** — before PP-HR-001 had a name, this piece got
built and should be treated as the design's first delivered component, not background:
- Invariant E11 (`reference/invariants.md`) states the rule: an agent's restrictions get
  locked in by tool permissions and hooks, not trusted as prose alone.
- A `SessionStart` hook (`.claude/hooks/session-start-briefing.py`) now replaces CLAUDE.md's
  prose-only "run the startup ritual" instruction — read-only, injects inbox/suggestions/
  plan-check state automatically before any reply. Precedent: a written instruction alone
  had already failed twice the same day; the hook doesn't ask.
- A concrete audit of `nix-flake-maintainer` and `tgw-coder` found real gaps: the existing
  flake-guard `PreToolUse` hook only matches `Bash` commands, not raw `Edit`/`Write` on
  flake files (todo #1449); `tgw-coder`'s entire worktree-isolation contract is still prose
  only, with a documented near-miss to prove it (todo #1450).
- **Take from this as a design precedent, not just an example:** a "job description" for
  an agent isn't done when it's written clearly — it's done once every restriction in it has
  been checked against what's actually mechanically enforceable, and every gap that can't be
  closed yet is named explicitly rather than assumed covered. Whatever HR's job-description
  format ends up being, this checked-vs-prose distinction should be a required field, not an
  afterthought.
- Below is what's still open on this piece, not yet a repeatable process:
- A repeatable sweep, not a one-time pass: for any agent profile, which "must"/"never"
  rules are mechanically enforced (scoped `tools:`, `PreToolUse`/`SessionStart` hooks,
  harness features like `settings.worktree.bgIsolation`) vs. still prose-only.
- Who re-runs this sweep, and on what trigger — a new agent being added? A profile
  edit? On a schedule?
- How a finding from this sweep becomes a todo (same discipline as everything else in
  this project — `--pp` tag, no untagged items).

### 2. Resource/credential governance (the ferals pattern)
- Account/ledger/authority boundaries per resource, per the ferals audit's own
  framing: owner (personal/business/shared-view-only), what a subscription/credit
  actually grants vs. implies, interactive-only vs. worker-eligible status.
- The ferals audit's own stated bar for "worker-eligible": a bounded contract, a
  quota/budget guard, and an acceptance test — does HR own verifying that bar is met
  before a feral resource gets used unattended, or just the inventory?
- Expiry/renewal tracking (credits that lapse, trial periods, promotional terms).

### 3. Onboarding/training pipeline
- Tigwa/Leotha's own "apprenticeship" pattern (supervised use of `tgw` before any
  autonomous authority unlocks) is the only precedent that exists today — should this
  generalize into a repeatable checklist for any new agent/persona, or does each one
  still get a bespoke plan?
- What does a new agent's profile need before it's allowed to go live — is there a
  minimum bar (e.g., "every `tools:` grant has to be justified," "every restriction not
  backed by a hook is named explicitly as a gap," matching invariant E11)?
- Identity/access provisioning as part of onboarding — the pattern already used for
  Tigwa's own accounts is real precedent worth generalizing: separate OS
  accounts/uids per persona (`claude`→`tigwa` rename on a1131), scoped SSH keys
  (`tigwa@a1131`'s standing key into `db@tgw-prod`, restricted by `from=`), secrets
  issuance per the existing single-facility pattern (`tgw.apis.secrets`).

### 4. Performance/escalation review
- Tigwa's branch-review enforcer and the tgw-coder/tgw-runner-review split already
  establish executor ≠ reviewer as a working pattern — does HR own extending that
  principle project-wide (no agent reviews its own work)?
- The cross-reviewer-bias checkpoint (todo #1381, not yet triggered) — HR is a
  plausible owner for deciding when enough runs have accumulated to actually run it.
- Incident record-keeping across agents, not just per-incident: is there a pattern
  across the incidents this project has already had (CLAUDE.md prose leaking into
  Tigwa's context because no `AGENTS.md` existed; the worktree near-miss in
  `tgw-coder`'s pilot; Tigwa's two protective-override poweroffs) that a standing
  function would have caught sooner by looking across incidents instead of one at a
  time?

### 5. Authority tiers and escalation (ties to PP-CATIONIX-001's crypto-lock)
- The crypto-lock endgame (scoped agent authority, escalation triggers) is explicitly
  "the same idea, still being built, not yet live" per CLAUDE.md's own doctrine
  section — is HR the function that owns defining what each authority tier actually
  means per agent, or does that stay a separate PP-CATIONIX-001 concern with HR only
  consuming its output?
- The still-open gap from the 2026-07-13 thermal incident: a fast operator-in-the-loop
  escalation channel for a protective override, distinct from a harder lockdown of
  authority. Worth naming here even if the actual channel gets built elsewhere
  (PP-RUNNERCOMMS-001 is already tracking a related but not identical "runner
  question" channel — don't conflate the two without checking).

### 6. Spec/documentation currency
- PP-HERMES-EA-001 already states "spec-currency per player is mandatory, not
  habitual" — does HR own verifying each agent's profile still matches its actual
  granted tools/authority, or catch drift between what a profile claims and what it's
  actually allowed to do?
- Job-description accuracy: an agent's frontmatter `description:` field is what
  routes work to it — does HR own auditing that these stay accurate as an agent's
  real scope evolves (same instinct as "keep flake surface minimal" — don't let scope
  creep silently outpace the stated contract)?

### 7. Offboarding/retirement
- Precedent already exists (`hermes-agent` pulled from the flake to userspace,
  `pm_intake` stopped and later repurposed as Tigwa's own direction, various stopped
  workers) — does HR own a clean deprecation process (credentials revoked, standing
  access removed, memory/state archived not deleted per the data-preservation
  axiom) when an agent/persona is retired or superseded?

### 8. Budget/cost accountability
- Per-agent/persona usage and cost tracking — ties to `LLM-Providers-Quotas.md` and
  the existing quota system, but scoped per *who* is spending, not just per provider
  pool. Worth deciding whether this is HR's lane or stays with PP-QUOTA-001.

## Explicitly not prescribed here

This document lists what to weigh, not a structure to build. Dave is guiding the
actual design directly with Tigwa — this is Claude's contribution of "here's
everything we've already learned the hard way that a design like this should account
for," not a competing spec. Whatever comes back gets reviewed through the normal seam
before anything is treated as settled.

## Cross-references

- `reference/invariants.md` E11 (agent role restrictions, mechanized vs. prose)
- `TIGWA-REQUEST-1333-ferals-audit-draft.md` (todo #1333, resource governance)
- `pp/PP-CATIONIX-001.md` (crypto-lock endgame, cat-herder/apprenticeship framing)
- `pp/PP-HERMES-EA-001.md` (Tigwa/Leotha personas, spec-currency rule, branch-review
  enforcer, cross-reviewer-bias checkpoint todo #1381)
- `pp/PP-RUNNERCOMMS-001.md` (related but distinct runner-question channel — check
  before assuming overlap)
- `.claude/agents/tgw-coder.md`, `.claude/agents/nix-flake-maintainer.md` (the two
  concrete agent profiles audited for E11)
- Todos #1449, #1450 (E11's own concrete mechanization follow-ups — not HR's to build,
  but useful precedent for "what does a fixed gap look like")
