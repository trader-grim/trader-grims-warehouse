# Claude Code (CLI) — current usage

**Status:** v1, 2026-06-10. How Claude Code sessions on the TGW repo work today.
Claude Code is currently the only tool that writes code in this repo.

---

## 1. Session lifecycle

### Start (every session, in order)

1. Process `docs/TGW-Plan-Vault/inbox/*.md` into the master plan, then delete/move each file.
2. Process new items in `docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md`.
3. Read `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` (at minimum: Current state,
   Implementation TODO, Work Tracks).
4. Run `tgw todo claude` — this is the work queue.
5. Read the relevant `docs/TGW-Plan-Vault/reference/*.md` before touching that area
   (the table in `CLAUDE.md` maps areas to docs).

This ritual is already encoded in `CLAUDE.md`; it is repeated here because it is the workflow.

### During

- Work the todo items in priority order, or the task Dave gives directly.
- Bounded scope: finish a task to a reviewable state rather than starting three.
- Run the test suite (`pytest`) and `ruff check` before declaring a task done.
- Run `tgw health` after any change touching config, secrets, workers, or paths.
- All commands as the `tgw` user.

### End

Produce the handoff package (see [next-process.md](next-process.md) § Session handoff):
updated todos, a diff summary, deploy notes (worker restarts), and plan/inbox updates if
anything strategic changed.

## 2. What Claude Code should produce

| Artifact | Expectation |
|----------|-------------|
| **Code** | Matches settled architecture (workers thin, tgw-api is the fence, `{ok, ...}` contract, secrets from `secrets_root`). Style-matched to surrounding code. |
| **Tests** | Every code task ships tests. The Round-2 pattern is the bar: pure-function/mocked tests that run offline without a live eBay token. |
| **Doc updates** | Reference docs (`vault/reference/`) updated when behavior they describe changes; ISSUES.md updated when bugs are found/fixed. |
| **Todo updates** | `tgw todo` entries updated/closed as work completes; new discovered work captured as new todos, not prose. |
| **Diff summary** | End-of-session: what changed, why, which files, what to restart, what to verify. |
| **Uncommitted changes** | Everything stays uncommitted until Dave reviews and asks for a commit. |

## 3. What must be reviewed manually (Dave)

Never auto-applied; always flagged in the session summary:

- **Every diff before commit** — no exceptions.
- **eBay-touching code paths** — anything in `ebay_*` workers, `ebay/sync.py`, pricing/
  lifecycle invariants. A wrong PUT body affects live listings.
- **OAuth scopes and the eBay keyset** — locked. Claude proposes, never edits.
- **Live config (`/opt/TGW/config/tgw-api-config.json`) and secrets** — changes are proposed
  as a diff for Dave to apply, unless the task explicitly authorizes the edit (then: backup
  first, `tgw health` after).
- **Database schema changes** — migrations on `state_machine` need explicit sign-off.
- **Hot-path worker edits** — `ai_identify` routing, `worker_base.py` — flag the restart
  requirement and blast radius explicitly.
- **Anything destructive or bulk** — bulk item mutations, deletions, requeue storms. Dry-run
  first, show the plan, wait.
- **Anything that flips a behavior flag on live listings** (e.g. `strikethrough_enabled`).

## 4. Modes and features worth using

- **Plan mode** for M+ tasks or anything ambiguous — design first, get approval, then build.
  Skip it for XS/S well-specified todos; just do them.
- **`/code-review`** before handing off a large diff.
- **Subagents/workflows** only when Dave asks — default sessions are single-agent.
- **Memory** persists across sessions (`~/.claude/.../memory/`) — corrections and preferences
  land there so they don't repeat.

## 5. Prompt templates

### Start-of-session (default)

```text
Start the session: process the inbox and suggestions, then read the master plan.
Then work through `tgw todo claude` in priority order. Stop after ~2 tasks or when
you hit anything needing my review (config, eBay-touching, schema). Tests + ruff
for everything. Don't commit. End with a diff summary and deploy notes.
```

### Single bounded task

```text
Do todo #38 (tgw alt-text <sku>). Read reference/TGW-Ollama-Prompts.md and
HARDWARE-AI-INFERENCE.md first. Constraints: CPU-only Ollama, keep the prompt lean,
graceful-skip if the model is missing. Ship with offline tests. Update the todo
when done. Don't commit.
```

### Bug fix / dead-letter diagnosis

```text
Diagnose the 25002 Item.Country dead-letters (todo #39). Check reference/ISSUES.md
and eBay-Error-Codes.md first, then the dead-letter rows and item JSON for the
affected SKUs. Report root cause and a proposed fix before changing any code.
```

### Exploratory / design (no code)

```text
Exploratory — suggest, don't implement. Question: <question>. Read the relevant
reference docs and code, give me options with a recommendation. If we pick one,
it becomes a todo, not work in this session.
```

### Review prep / handoff

```text
Wrap up: run the full test suite and ruff, then give me the review package —
per-file diff summary, risk notes (eBay-touching? worker restart? config?),
verification steps for me, and updated todo state.
```
