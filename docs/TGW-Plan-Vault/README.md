# TGW Plan Vault

This folder is an Obsidian vault. Open Obsidian → "Open folder as vault" → point it here.

## Layout
- `plan/` — the living spec, decisions, and execution tasks
  - `TGW-Master-Plan.md` — **start here.** The markmap hub. Renders as a mind-map.
  - `DECISION-queue-architecture.md` — settled queue design + reasoning
  - `TASKS-phase1-queue.md` — bite-sized tasks to hand to Sonnet/Haiku
- `reference/` — starter code and distilled context for executor models
- `sessions/` — one note per planning session
- `inbox/` — drop a plan note here; the PM-intake worker (Phase 2) will file it
- `suggestions/` — `tgw suggest "..."` appends here for the next session

## To see the mind-map
Install the **Markmap** community plugin in Obsidian, open `TGW-Master-Plan.md`,
and use the Markmap view. The YAML frontmatter already sets the expand level.

## To brief another model
Paste `TGW-Master-Plan.md` for full context, then the relevant `TASKS-*.md`
and any `reference/*.py` the task names. Each task is one execution session.
