# Result: 1389 worktree-convention

Status: done
Todo: #1389   PP: PP-HERMES-EA-001

## Summary

Built on #1450's evaluation (read in full first, not re-derived) and
implemented its recommendation: decided **(b) build a dedicated PreToolUse
hook** rather than adopting Claude Code's own `EnterWorktree`/harness
worktree convention, plus a narrow companion setting change so the two
conventions can no longer silently coexist.

## Files touched

- `.claude/hooks/worktree-guard.py` (new) — PreToolUse hook, sibling to
  `flake-guard.py`. Blocks Edit/Write when `agent_type == "tgw-coder"` and
  `file_path` falls outside `/opt/TGW/var/worktrees/<id>-<slug>/` or
  `/home/db/tgw-worktrees/<id>-<slug>/` (both roots seen live in #1450's
  `git worktree list` evidence). Gives a specific, more pointed message
  when the target path is under the harness's own
  `.claude/worktrees/agent-<id>/` — the exact conflict this todo exists to
  prevent recurring. Fails open (exits 0) on any payload-parsing error or
  missing `agent_type`, matching `flake-guard.py`'s style (JSON stdin
  payload, `ask()` helper printing `hookSpecificOutput.permissionDecision`).
- `.claude/settings.json` — added `"worktree": {"bgIsolation": "none"}`
  (top-level key, matches the Zod schema `bgIsolation: z.enum(["worktree",
  "none"])` extracted from the installed Claude Code binary during #1450)
  and registered `worktree-guard.py` as a second `PreToolUse` hook entry
  with matcher `Edit|Write`.
- `.claude/agents/tgw-coder.md` — Step 2 ("Worktree + branch") now states
  the isolation contract is mechanically enforced by
  `worktree-guard.py`/`bgIsolation: "none"`, not prose-only, and explains
  what to do if the guard fires unexpectedly (fix the worktree location,
  don't route around the hook).
- `.claude/agents/nix-flake-maintainer.md` — added a short note at the top
  explaining this agent is deliberately NOT in
  `WORKTREE_REQUIRED_AGENTS` (its mutation surface is `~/tgw-flake`
  directly on tgw-prod/a1131, gated by its own drift-check/commit
  procedure, not a worktree convention), with guidance for a future
  reviser if that ever changes.
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1389-worktree-convention.md`
  (new, worktree-local breadcrumb, per contract).

## Live evidence

**Hook behavior — synthetic PreToolUse payloads via stdin (all 7 cases
match expected outcome):**

1. `agent_type=tgw-coder`, `file_path` in shared checkout
   (`/opt/TGW/src/trader-grims-warehouse/src/tgw/items.py`) → **blocked**
   (`permissionDecision: "ask"`, reason names the worktree convention).
2. `agent_type=tgw-coder`, `file_path` under harness worktree
   (`.claude/worktrees/agent-xyz/...`) → **blocked** with the more specific
   "targets a Claude-Code-harness-provisioned worktree path" message,
   naming #1389/#1450 and the orphaned `agent-a271e21fa52fe73ad` precedent.
3. `agent_type=tgw-coder`, `file_path` under
   `/opt/TGW/var/worktrees/1389-worktree-convention/...` → **allowed**
   (exit 0, no output).
4. `agent_type=tgw-coder`, `file_path` under
   `/home/db/tgw-worktrees/1449-flake-guard-edit-write/...` → **allowed**
   (exit 0).
5. `agent_type=nix-flake-maintainer`, shared-checkout path → **passthrough**
   (exit 0) — confirms the agent-type gate is scoped correctly per the
   nix-flake-maintainer.md note above.
6. No `agent_type` field at all (main-session-shaped payload),
   shared-checkout path → **passthrough** (exit 0).
7. Malformed (non-JSON) stdin → **fails open**, exit 0, no exception.

**Settings schema validity:** `.claude/settings.json` parses as valid JSON
(`python3 -c "import json; json.load(...)"` succeeded) and the
`worktree.bgIsolation` key/value (`"none"`) matches the enum
`z.enum(["worktree","none"])` extracted directly from the installed
`@anthropic-ai/claude-code@2.1.205` binary's settings schema during #1450's
investigation (see #1450-RESULT.md §1) — same authoritative source used to
confirm this rather than guessing at undocumented schema shape.

**Payload field confirmation (this session, not carried over from #1450):**
reverse-engineered the binary's hook-context builder (`ff()` in the CLI
bundle) and confirmed PreToolUse hook stdin payloads include an
`agent_type` field (alongside `session_id`, `transcript_path`, `cwd`,
`permission_mode`, `agent_id`, `effort`) beyond what the CLI's own `/hooks`
help text documents (which only shows `session_id`/`tool_name`/
`tool_input`/`tool_response` as a simplified example) — this is what makes
agent-scoped gating possible at all, and is flagged as best-effort/fails-open
in the hook's own docstring in case a future Claude Code release renames
the field.

**Regression check:** full pytest suite run from inside the worktree with
`PYTHONPATH`/`LD_LIBRARY_PATH` overrides, confirmed testing the worktree's
own copy first (`import tgw; tgw.__file__` resolved under
`/opt/TGW/var/worktrees/1389-worktree-convention/src/tgw/__init__.py`, not
the shared checkout): **2514 passed, 1 skipped, 0 failed** (204s). No
`src/tgw/` files were touched by this packet, so this run is a clean
sanity check that nothing else broke, not a targeted test of the change
itself (hooks aren't pytest-unit-testable, per the packet's own acceptance
note — verified instead via the 7 scripted stdin cases above, same
methodology as #1449's own hook verification).

## Deviations from spec

- The packet named `.claude/agents/nix-flake-maintainer.md` as "if
  relevant." Investigation (reading the file in full) confirmed it is
  **not** relevant — nix-flake-maintainer has no worktree step at all; its
  contract works directly on `~/tgw-flake` on tgw-prod/a1131 via a
  drift-check + commit procedure. Rather than silently skipping the file
  as the packet named it, added an explicit short note there documenting
  the decision and why, so a future reader isn't left wondering whether
  this was an oversight. `WORKTREE_REQUIRED_AGENTS` in the hook contains
  only `tgw-coder`.
- Everything else implemented exactly as #1450 recommended and this
  packet specced: `bgIsolation: "none"`, dedicated PreToolUse hook sibling
  to `flake-guard.py`, `tgw-coder.md` updated to reference it. No other
  deviations.

## Out-of-scope findings filed

None new. This packet is explicitly the follow-through on #1450's own
filed recommendation (no separate todo needed — #1389 already covers
implementation). The orphaned `.claude/worktrees/agent-a271e21fa52fe73ad`
worktree was left exactly as found, per the packet's explicit instruction
not to touch it — that cleanup decision remains pending Dave's word,
tracked in #1450's result manifest ("Loose end surfaced" section).
