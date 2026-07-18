# DONE: DeepSeek/OpenRouter billing-hour investigation + registry-log delegation

**Date:** 2026-07-17
**Todo:** #1493 (pp_ref PP-KNOWLEDGE-001) — open, delegated to Tigwa, not Claude's to close

## What I was doing

Dave noticed OpenRouter billed some "DeepSeek 3" tokens during the 5pm UTC
hour on 2026-07-15 and asked what was misconfigured. Pure investigation, no
code/config changes made by me this session.

## Where I got to

Root-caused via git history + file mtimes, no ambiguity left:

- The live `.aider.conf.yml` during 17:31–17:51 UTC on 07-15 (when todo
  #1358/#1365's worktree-wiring smoke test ran) still had the **old** model
  line: `model: openrouter/deepseek/deepseek-chat-v3-0324` (OpenRouter,
  DeepSeek V3) — i.e. literally "DeepSeek 3" via OpenRouter.
- The switch to DeepSeek-direct (`model: deepseek/deepseek-v4-flash`,
  bypassing OpenRouter) didn't land until commit `2d98364`,
  `2026-07-15T20:50:45-07:00` = 2026-07-16 03:50 UTC — several hours
  **after** the smoke test.
- So the OpenRouter DeepSeek-3 usage was the old default, live during a
  planned test, replaced later the same evening. Confirmed, not a live gap.
- Side finding (not acted on, no todo needed per Dave): there's no
  provider/model/token audit log for MCP-invoked `aider_run_task` calls —
  `.aider.chat.history.md` and `~/.local/share/aider-audit/usage.csv` both
  have zero entries for 07-15 despite the completed test. Only the
  `/tgw-aider-step` skill path gets audited, not the MCP path. Flagged
  verbally to Dave; he did not ask for a todo, so none was opened.

Separately, Dave mentioned Tigwa is already hashing configs (and, on her own
initiative, worker contracts) as part of her library catalog — partial
coverage so far. He asked to formalize this into a durable registry log.

## What I did about it

- Added todo **#1493** (pp_ref `PP-KNOWLEDGE-001`): formalize into a registry
  log, extend to worker contracts, partial coverage is fine.
- Wrote `inbox/tigwa/CLAUDE-REQUEST-config-hash-registry-log-2026-07-17.md`
  relaying the ask — format/schema left to Tigwa per the standing
  scope-her-own-contract pattern (she designs, Claude reviews after, same as
  HR-001).
- Saved memory `project-tigwa-config-hash-tracking.md` (indexed in
  MEMORY.md).

## Next step

Nothing pending on Claude's side. #1493 stays open until Tigwa responds with
a design (expect a `RESPONSE-*` or `TIGWA-*` note back in `inbox/claude/`
covering the registry-log format for configs + worker contracts). Review it
when it lands — don't implement it preemptively.
