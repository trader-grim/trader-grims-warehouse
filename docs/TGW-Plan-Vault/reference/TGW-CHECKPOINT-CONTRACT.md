# TGW Checkpoint Contract — agent-neutral spec

Canonical, agent-neutral. `.claude/skills/tgw-exit/SKILL.md` (Claude) and
Tigwa's Hermes-native adapter are both implementations of THIS contract —
if either drifts from what's listed here, fix the adapter, not this doc.
Same pattern as the `tgw-coder` / `tgw-runner-review` split: one contract,
interchangeable executors.

## What a checkpoint must preserve

1. **Current todo state** — what's in_progress, what's newly filed this
   session, with `pp_ref` set.
2. **Inbox breadcrumb or continuation note** — enough for the next session
   (any agent) to reconstruct where things stand without re-deriving it.
3. **Durable decisions/memories** — anything that should survive past this
   session's context (standing rules, resolved questions, project facts).
4. **Handoff/open risks** — what's unresolved, what could bite the next
   session if not flagged.
5. **Exact next action** — not "continue the work," the actual next step.
6. **Concise close-out summary** — what changed, in a form a human can
   read in seconds.

## What a checkpoint is NOT

- Not a commit or merge.
- Not a live/production data mutation.
- Not full session history — that's a separate concern (Hermes distinguishes
  checkpoint / `/compress` / `/new <name>`; Claude's equivalent is the
  session transcript itself, untouched by `/tgw-exit`).

## Adapter responsibilities

Each adapter (Claude's `/tgw-exit`, Tigwa's Hermes-native equivalent, any
future agent's) must:

- Implement all six preserved elements above, in whatever storage
  mechanism is native to that agent (Claude: `~/.claude/projects/.../memory/`;
  Hermes: its own memory/session store).
- Write the same canonical artifacts into the shared TGW plan vault where
  the contract calls for it (inbox breadcrumb, todo state) — these are
  shared, not agent-private, regardless of which adapter wrote them.
- Make no commit/merge, no live/production write, as part of the
  checkpoint action itself.
- Support a dry-run mode (report intended writes, change nothing) before
  any adapter is trusted to run live.

## Provenance

Tigwa proposed this canonical/adapter split and enumerated the six
preserved elements in her 2026-07-13 inbox request
(`TIGWA-REQUEST-20260713-hermes-checkpoint-skill.md`, reconciled same day
— see `pp/PP-HERMES-EA-001.md`). This doc formalizes what she proposed,
unchanged in substance.
