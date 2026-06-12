# Handoff and the next process

**Status:** v1, 2026-06-10. Two things: (1) how work hands off between a Claude Code session
and Dave today, and (2) what the Aider/Cline execution tier looks like when introduced.

---

## 1. Session handoff (today)

Every Claude Code session ends with a **handoff package** so the next actor (Dave reviewing,
or the next session resuming) starts cold without archaeology:

1. **Todo state** — `tgw todo` updated: finished items closed, discovered work added as new
   todos with priority, blocked items annotated with the blocker.
2. **Diff summary** — what changed, per file, why; total test count before → after; ruff clean
   or not.
3. **Deploy notes** — which `tgw-worker@<queue>` units need restarting, whether `tgw health`
   was run, any config the operator must apply by hand.
4. **Risk flags** — explicit callouts of anything in the manual-review list
   ([claude-cli.md](claude-cli.md) § 3).
5. **Plan/inbox updates** — only if something strategic changed (a PP item completed, a
   decision made). Routine task progress lives in the todo tracker, not the plan.

### Review → commit → deploy (Dave)

```
git diff                          # review everything
pytest -q && ruff check .         # or trust the session's reported run
# feedback → drop a note in vault inbox/ or tell the next session directly
# approve → ask Claude to commit (or commit yourself)
sudo systemctl restart tgw-worker@<queue>.service   # per deploy notes
tgw health
```

Rejected work stays on the branch; the feedback becomes a todo annotation so the next session
picks it up with context.

## 2. Future: the Aider execution tier

### Why and when

Claude Code subscription tokens are the scarce resource. The Round-2 pattern proved that TGW
generates a steady stream of **XS/S, well-specified, offline-testable tasks** — exactly what a
cheaper executor can handle. Aider + Claude API (with prompt caching and a hard billing cap,
~$40/mo) is that executor. Aider has already been trialed in this repo (`.aider.chat.history.md`).

**Adoption gate:** introduce Aider when (a) the API key + billing caps are set up, and (b) the
todo queue has ≥3 tasks that meet the "Aider-ready" bar below. Trial it on 2–3 tasks and
compare review burden against a Claude Code session before making it routine.

**Decision 2026-06-12 (session 28, amended same day): Aider is COMMITTED.** Dave: Aider will be
used even if Antigravity becomes the primary agent / agent manager. Sequencing stays
Antigravity-first for the validation week (#78, deadline 2026-06-18; code-task trial = admin
#114 — now a routing-calibration exercise, not a go/no-go gate), but Aider setup (API key +
hard billing cap) is unconditional (admin #117; Claude onboarding files = todo #118).
Division of labor going forward: **Antigravity** = primary agent/agent-manager lane (bite-sized
self-contained tasks, browser-verified work, large-context analysis); **Aider** = mechanical
code-edit execution tier (XS/S spec'd tasks, auto-test, task branches); **Claude Code** =
architecture, cross-cutting, eBay-invariant work, planning + spec writing. `tgw todo brief <id>`
(todo #109) generates the Aider message files.

### Division of labor

| Task shape | Tool |
|------------|------|
| Ambiguous, cross-cutting, architectural, or eBay-invariant-touching | **Claude Code** |
| XS/S, named files, clear acceptance test, offline-testable | **Aider** |
| Planning, task specification, review prep | **Claude Code** (it writes the Aider task specs) |
| Research / analysis | Perplexity / Gemini (unchanged) |

A task is **Aider-ready** when its todo entry (or a small spec block) names: target files,
the change, the acceptance command (`pytest -q tests/test_x.py`), and constraints. If writing
that spec takes longer than doing the task, just do it in Claude Code.

### Setup (when adopted)

`.aider.conf.yml` at repo root — distilled from `research/high performance coding aider config.md`:

```yaml
model: anthropic/claude-sonnet-4-6        # current Sonnet; native Anthropic first
weak-model: anthropic/claude-haiku-4-5-20251001
architect: true
auto-accept-architect: false              # review the plan before edits
cache-prompts: true
map-tokens: 8000
show-diffs: true
git: true
auto-commits: true                        # commits land on the TASK BRANCH only
auto-lint: true
auto-test: true
lint-cmd:
  - python: ruff check .
test-cmd:
  - pytest -q
```

Notes:
- **Native Anthropic** is the primary route; OpenRouter is fallback/experimentation only
  (`research/openrouter vs native anthropic...md`).
- Set the **hard billing cap** in the Anthropic console before first run.
- A `CONVENTIONS.md` (one page: settled architecture bullets + `{ok,...}` contract + "never
  touch config/secrets/scopes") gets added to every Aider session alongside `CLAUDE.md`.

### Aider task flow

```
Claude Code writes spec  ──►  branch task/<todo#>-<slug> (worktree optional)
                                      │
                              aider --message-file spec.md <files>
                              auto-commits to the task branch, runs tests
                                      │
                              Dave (or Claude Code) reviews the branch diff
                                      │ approve            │ reject
                              merge to main         feedback → re-run or
                                                    escalate to Claude Code
```

`auto-commits: true` is safe because commits only ever land on the task branch — the
"Dave controls git history" rule applies at merge time. Agents never merge.

### Aider prompt template (the message file)

```text
You are working in the Trader Grim's Warehouse (TGW) repo. Read CLAUDE.md and
CONVENTIONS.md constraints; do not deviate from them.

Task: todo #41 — add `tgw quiet-check` (read-only queue-idle summary).

Files you may modify:
- src/tgw/api.py          (add cmd_quiet_check + CLI wiring)
- tests/test_quiet_check.py  (new)

Requirements:
1. Read-only: use state_machine.queue_depths(); no writes anywhere.
2. Output one JSON object with an "ok" key, matching every other tgw command.
3. Tests mock the DB; the suite must pass offline: pytest -q tests/test_quiet_check.py

Do NOT touch: config files, secrets, anything under src/tgw/ebay/, other commands.
If a requirement is impossible as specified, stop and explain instead of improvising.
```

## 3. Future: Google Antigravity (replaces the Cline slot)

Antigravity (Google's agent-first platform — IDE, desktop, CLI, SDK; see
`research/claude-aider-antigravity.md`) takes the third-tool slot that was originally
penciled in for Cline. Same niche — agentic IDE, browser-in-the-loop verification,
watch-every-step workflows — but at **$0 marginal cost** under the existing Google AI Plus
subscription, instead of burning metered Claude API tokens. **Cline is now "not planned."**

- **Status: CLI configured + Antigravity 2.0 installed 2026-06-11** (installed 2026-06-10,
  configured the next day). Gemini CLI stops serving AI-plan users on **2026-06-18**;
  Antigravity CLI is its successor (skills/hooks/subagents carry over). Both run until
  then — the overlap window is now the only time the side-by-side baseline comparison
  (step 3 below) is possible. **Validate before the forced switch** (all operator-runnable,
  no production risk):
  1. Confirm skills/hooks/subagents actually carried over (the carry-over claim is from
     research, not verified here).
  2. Verify **headless/scripted use** — flagged below as unconfirmed; this gates any
     future automated wiring (`tgw perp-run`-style brief running).
  3. Re-run one existing Gemini brief (`docs/TGW-Plan-Vault/gemini/`) through Antigravity
     while Gemini still works, and diff the output quality — the only week this baseline
     comparison is possible.
  4. Export/note anything that lives only in Gemini CLI config (settings, custom
     commands, history worth keeping) before shutoff.
  5. Observe the compute-cap refresh (~5 hr) behavior on a real bite-sized task.
- **Role:** the free, bounded, browser-capable delegation lane. Bite-sized self-contained
  code tasks (Round-5 "any agent" items), browser-verified UI work (eBay Seller Hub checks,
  Flutter web debugging), and the existing Gemini analysis briefs.
- **Constraints:** compute caps with ~5 hr refresh — keep jobs bite-sized; never route
  eBay-invariant, hot-path, or config/secrets work to it; same branch-per-task +
  human-merge rule as every agent. Headless/scripted use is unconfirmed — verify before
  wiring it into anything automated.
- **Adoption gate:** after the CLI migration, trial 1–2 bite-sized items and compare review
  burden vs Claude Code (mirror of the Aider trial).

## 4. Eventual automation (sketch, not commitment)

The research (`research/I am setting up an inexpensive...md`) sketches a thin orchestrator:
todo entry → task envelope → worktree → Aider run → review queue. TGW already has the
primitives (PostgreSQL todo tracker, queue workers, worktree-friendly repo). **Do not build
this until the manual Aider flow has run for a few weeks** and the bottleneck is demonstrably
the by-hand branch/spec/launch ritual — premature orchestration is how plans rot.
