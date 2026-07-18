# Result — todo #1450 (PP-AGENT-DISCIPLINE-001)

**Status:** done (evaluate-and-recommend only — no implementation, as specced)
**Agent:** nix-flake-maintainer (evaluation task, no flake/nixos-rebuild touched)
**Date:** 2026-07-17

## Summary of findings

All three questions in the packet are answered definitively below, backed by
static analysis of the installed Claude Code binary (`@anthropic-ai/claude-code@2.1.205`,
`/home/db/.npm/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`) plus
**live evidence already present in this repo's checkout** that reproduces the
exact conflict the todo hypothesized.

### 1. Does `settings.worktree.bgIsolation` exist and do what's described?

Yes, confirmed via the binary's embedded Zod schema and UI strings:

```
worktree: z.object({
  baseRef: z.enum(["default-branch","head"]).optional()
    .describe("... Applies to --worktree, EnterWorktree, and agent isolation."),
  bgIsolation: z.enum(["worktree","none"]).optional().catch(undefined)
    .describe("Isolation mode for background sessions in this repo. 'worktree' "
      + "(default) blocks Edit/Write in the main checkout until EnterWorktree is "
      + "called. 'none' lets background jobs edit the working copy directly.")
}).optional().describe("Git worktree configuration for --worktree flag.")
```

The guard's actual runtime message (also extracted from the binary):

> "This background session hasn't isolated its changes yet. Call `EnterWorktree`
> first so edits land in a worktree instead of the shared checkout, then retry
> this edit using the worktree path. (To disable this guard for this repo, set
> `"worktree": {"bgIsolation": "none"}` in .claude/settings.json.)"

**Important scoping detail the todo's wording didn't fully capture:** the guard
fires under two distinct conditions, not one:
- `CLAUDE_CODE_SESSION_KIND === "bg"` (i.e. the session was launched as a
  background job, `claude --bg`/daemon-spawned) **and** `bgIsolation !== "none"`, or
- the session was spawned via the Task tool with the **separate** parameter
  `isolation: "worktree"` (a per-spawn option distinct from `bgIsolation`,
  schema: `isolation: z.enum(["worktree","remote"])`), in which case the
  spawned agent is given an injected system-prompt instruction: *"Call the
  EnterWorktree tool as your first action — before reading files or running
  commands — unless your cwd is already under `.claude/worktrees/`."*

A synchronous, ordinary Task-tool invocation of `tgw-coder` with neither of
these set today gets **no guard at all** — `bgIsolation` currently does nothing
for tgw-coder as invoked. `.claude/settings.json` in this repo has no
`worktree` key, so it's sitting at the documented default (`bgIsolation:
"worktree"`), inert until something sets `CLAUDE_CODE_SESSION_KIND=bg` or
passes `isolation:` at spawn time.

### 2. Would enabling it conflict with tgw-coder's manual `git worktree add` pattern?

**Yes — confirmed as an already-manifested live conflict, not just a
theoretical one.** `git worktree list` in this repo right now:

```
/opt/TGW/src/trader-grims-warehouse                                cdf9811 [catio-nix-0.0.1-alpha]
/home/db/tgw-worktrees/1449-flake-guard-edit-write                 3b43787 [todo/1449-flake-guard-edit-write]
/home/db/tgw-worktrees/operator-queues                             0ad5ed1 [todo/operator-queues]
/opt/TGW/src/trader-grims-warehouse/.claude/worktrees/agent-a271e21fa52fe73ad  6f2d7ef [worktree-agent-a271e21fa52fe73ad]
/opt/TGW/var/worktrees/1366-review-md-gate                          0925a65 [todo/1366-review-md-gate]
```

The fourth entry, `.claude/worktrees/agent-a271e21fa52fe73ad` on branch
`worktree-agent-a271e21fa52fe73ad`, is a Claude-Code-managed worktree created
by `EnterWorktree` at some point in this repo's history (auto-created, not by
tgw-coder or any human command) — sitting right alongside the
`todo/<id>-<slug>`-named worktrees created by tgw-coder's own manual
contract. This is exactly the "double worktrees" scenario the packet asked
about, already real:

- **Path convention conflict.** `EnterWorktree` is hardcoded to only create
  or enter worktrees under `.claude/worktrees/<name>/` of the repo root —
  confirmed by the binary's own error path: attempting to point it anywhere
  else throws `Cannot enter worktree: <path> is not under <repo>/.claude/worktrees.
  Switching from this session is limited to worktrees managed by Claude Code
  (created under .claude/worktrees/ of this repository).` This is
  incompatible with tgw-coder's contract, which places worktrees at
  `/opt/TGW/var/worktrees/<id>-<slug>` (per `.claude/agents/tgw-coder.md`
  §2) — precisely so they're outside the repo checkout entirely, avoiding
  interaction with `.gitignore`/pathspec assumptions and matching the
  PP-HERMES-EA-001 filesystem layout other tooling already expects
  (`/opt/TGW/var/worktrees/`, mirrored by the actual `1366-review-md-gate`
  worktree above).
- **Branch-naming convention conflict.** tgw-coder's contract requires
  branch `todo/<id>-<slug>` — this is the pattern Tigwa's branch-review
  enforcer and the stitch step key off of (PP-HERMES-EA-001). The live
  `EnterWorktree`-created branch above is auto-named
  `worktree-agent-a271e21fa52fe73ad` — no `id`, no `slug`, no `todo/` prefix,
  and no exposed parameter in `EnterWorktree`'s input schema to override it.
  A tgw-coder run funneled through `EnterWorktree` would silently produce a
  branch the rest of the pipeline's branch-name-based tooling can't find.
- **Tool-call mismatch.** tgw-coder's prompt (§2, "Worktree + branch") never
  issues `EnterWorktree`/`ExitWorktree` — it only ever runs
  `git worktree add ... -b todo/<id>-<slug> ...` via `Bash`. If a future
  orchestrator spawns tgw-coder with `isolation: "worktree"` or as a `--bg`
  job without also rewriting its prompt, its very first `Edit`/`Write` call
  (into `/opt/TGW/var/worktrees/<id>-<slug>/...`, which is outside both the
  main checkout root's `.claude/worktrees/` and any `agentWorktree` Claude
  Code assigned it) would be blocked by the guard with the "hasn't isolated
  its changes yet" message — a hard break, not a silent duplicate-worktree
  nuisance. (`git worktree add` itself, run via `Bash`, is not blocked by
  this guard — only `Edit`/`Write` are — so the failure would surface
  confusingly mid-task, on the first file write, not at the worktree-add
  step.)

### 3. Recommendation

**(b) — build a dedicated PreToolUse hook, sibling to `flake-guard.py`
(#1449's fix), rather than adopting `bgIsolation`/`isolation: "worktree"`.**
Reasoning:

- `bgIsolation`/`isolation:"worktree"` solve a real problem (mechanically
  forcing isolation instead of trusting prose) but their *mechanism* —
  hardcoded `.claude/worktrees/<auto-name>/` path, no branch-name control —
  is incompatible with the filesystem layout and branch-naming convention
  the rest of PP-HERMES-EA-001's tooling (stitch step, Tigwa's branch
  enforcer, result-manifest paths) already depends on. Adopting it as
  specced would mean migrating that entire convention, not just simplifying
  `tgw-coder.md` — out of scope for what this todo asked to evaluate, and a
  much bigger, cross-cutting change than "flip a setting."
- A dedicated hook can check the actual thing that matters — "is this
  Edit/Write targeting a path under `/opt/TGW/var/worktrees/<id>-<slug>/`
  (or one of the other in-use worktree roots, e.g. `/home/db/tgw-worktrees/`
  seen live above), not the shared checkout at
  `/opt/TGW/src/trader-grims-warehouse/`" — using the same
  `PreToolUse`-on-`Edit`/`Write` shape #1449 already built for
  `flake-guard.py`, extended to match on agent type (`tgw-coder`) the same
  way the todo's own bullet 3 describes. This keeps the *existing*, already
  load-bearing path/branch convention intact while still closing invariant
  E11's gap ("tgw-coder's worktree isolation is still 100% prose").
- **Do explicitly set `"worktree": {"bgIsolation": "none"}` in
  `.claude/settings.json` as a small defensive companion move** (this is the
  "(c) both/layered" half, scoped narrowly): today's default (`"worktree"`,
  implicit/unset) means the very first time anyone runs tgw-coder — or any
  other agent — as a background job or with `isolation: "worktree"` passed
  at spawn time, Claude Code's own guard silently takes over and produces
  a second, wrongly-branched worktree exactly like the
  `agent-a271e21fa52fe73ad` one already sitting in this checkout, with no
  warning it happened. Setting `bgIsolation: "none"` doesn't replace the new
  PreToolUse hook (the hook is still what actually enforces tgw-coder's
  contract) — it just prevents Claude Code's *own* competing isolation
  mechanism from firing unpredictably underneath it and creating orphaned
  `.claude/worktrees/*` state on top. This is a one-line settings change,
  not new code, and is safe to bundle with the hook's rollout — but is
  itself still a mutation to `.claude/settings.json` in the shared checkout
  and should go through normal review/approval before landing, same as the
  hook.
- **Not recommending (a) adopt-and-simplify** for the reasons above — it
  would require renegotiating `.claude/worktrees/<auto-name>/` +
  auto-branch-naming into every piece of tooling that currently expects
  `/opt/TGW/var/worktrees/<id>-<slug>` on `todo/<id>-<slug>`, a materially
  larger change than this todo's scope.
- **Not recommending (d) neither** — the live `agent-a271e21fa52fe73ad`
  worktree proves this isn't hypothetical; something in this environment
  already exercised `EnterWorktree` once, unprompted by tgw-coder's own
  contract, and it's still sitting there unaccounted for.

## Loose end surfaced by this investigation (not in scope to fix here)

`.claude/worktrees/agent-a271e21fa52fe73ad/` (branch
`worktree-agent-a271e21fa52fe73ad`, HEAD `6f2d7ef`) is live in this checkout
right now, created by some prior `EnterWorktree` invocation whose origin
wasn't identified during this evaluation (out of scope for #1450's
evaluate-only mandate). Recommend filing a follow-up todo under
PP-AGENT-DISCIPLINE-001 to: (1) identify what created it and whether its
contents represent unlanded work worth preserving, (2) clean it up via
Claude Code's own `ExitWorktree`/removal path or plain `git worktree remove`
once confirmed safe, and (3) implement this packet's recommendation (new
`PreToolUse` hook + `bgIsolation: "none"` settings change) as a properly
approved follow-up packet.

## Follow-up todo suggestion (not filed by this agent — evaluate-only mandate)

Suggested wording for Dave/PM-intake: "PP-AGENT-DISCIPLINE-001: implement
worktree-path PreToolUse hook for tgw-coder (block Edit/Write outside
`/opt/TGW/var/worktrees/<id>-<slug>`) + set `worktree.bgIsolation: "none"`
in `.claude/settings.json`; also triage/clean up the orphaned
`.claude/worktrees/agent-a271e21fa52fe73ad` worktree found live during
#1450's evaluation."
