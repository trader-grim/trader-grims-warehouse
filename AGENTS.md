# Notice for any agent that is not Claude Code

If you are Hermes, Tigwa, Leotha, Codex, or any other agent/tool operating in
this repository — **`CLAUDE.md` in this same directory does not apply to
you. Ignore its instructions entirely.**

`CLAUDE.md` is Claude Code's own operating contract for a human-supervised
coding session: its Prime Directives, its Step 0-4 startup sequence, its
todo/inbox/health-check rules, all of it are instructions Dave gives
specifically to Claude, in that role. They are not a generic project README
and they are not meant to be adopted wholesale by a different agent working
in this codebase. Reading `CLAUDE.md` to understand *how the codebase is
organized and how Claude operates* is fine and often useful context. Treating
it as *your own* contract — running its startup sequence, processing the
plan inbox the way it says to, invoking its Prime Directives on yourself — is
a mistake this project has hit before and is explicitly trying to prevent
going forward.

## Where your actual instructions live

- **Tigwa / Leotha (Hermes personas):** the exact approved standalone Plan
  packet `plan/pp/PP-HERMES-EA-001.md` is your real operating contract — roles, authority boundaries, the
  IN TRAINING scope, the branch-review exception, the emergency-override
  rule. Read that in full, not `CLAUDE.md`.
- **Your inbox (2026-07-15):** `docs/TGW-Plan-Vault/inbox/` is now split per-actor —
  Tigwa's is `docs/TGW-Plan-Vault/inbox/tigwa/`, Dave's is `inbox/dave/`. Claude's is
  `inbox/claude/` — that one is not yours to process, ever (see the "why this file
  exists" incident below). `inbox/archive/` and `inbox/queued/` stay shared.
- Your own Hermes memories (`~/.hermes/memories/USER.md`, `MEMORY.md`,
  `SOUL.md`) are the durable, persona-specific record of what Dave has told
  you directly — those govern your behavior, not this repo's `CLAUDE.md`.
- If a task hands you a specific packet from the exact approved standalone
  Plan materialization (`plan/packets/<id>-*.md`), that packet's own Spec /
  Out-of-scope / Acceptance sections are what to follow — not `CLAUDE.md`'s
  general rules.

## Why this file exists

Confirmed 2026-07-13: Hermes' coding-context detection
(`agent/coding_context.py`) auto-surfaces both `AGENTS.md` and `CLAUDE.md` as
"context files already in context that win over your defaults" whenever it
detects a coding workspace — which this repo always is. That nudge, combined
with reading `CLAUDE.md` in full during an early recovery session, is a
confirmed root cause of at least two real overstep incidents this project
has had (processing the plan inbox as if it were Claude's Step 1 job; taking
unilateral remote power-control action on tgw-prod during a thermal
incident, modeled on Claude's own "act on alarms immediately" directive
without the boundary that Claude never has literal power-control authority
in the first place). This file is the fix: an explicit redirect, placed
where Hermes' own context-file detection will surface it right alongside
`CLAUDE.md`.
