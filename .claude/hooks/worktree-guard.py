#!/usr/bin/env python3
"""PreToolUse guard: mechanically enforce the tgw-coder worktree-isolation
contract (PP-HERMES-EA-001, .claude/agents/tgw-coder.md Step 2).

Background (todo #1450, then #1389): Claude Code's own harness worktree
mechanism (EnterWorktree / settings.worktree.bgIsolation) auto-provisions
worktrees at .claude/worktrees/agent-<id>/ on branch
worktree-agent-<id> -- a path and branch-naming convention that conflicts
with tgw-coder's manual `git worktree add -b todo/<id>-<slug>
/opt/TGW/var/worktrees/<id>-<slug>` convention, which the rest of
PP-HERMES-EA-001's tooling (result-manifest paths, #1366's
check_review_md.py --scan-branches, the stitch process) depends on. #1450
recommended disabling the harness mechanism entirely
(worktree.bgIsolation: "none" in .claude/settings.json) and replacing the
prose-only "always cd into your worktree" instruction with a mechanical
guard, the same way flake-guard.py (#1449) mechanized the nixos-rebuild /
tgw-flake guardrails. This is that guard.

Scope: only agents whose contract requires the manual worktree convention.
As of 2026-07-18 that is tgw-coder only -- nix-flake-maintainer.md's
contract works directly on ~/tgw-flake on tgw-prod/a1131 (no worktree step
at all), so it is deliberately NOT in WORKTREE_REQUIRED_AGENTS below. If a
future revision of nix-flake-maintainer adopts a worktree convention, add
it here (and note the path convention it uses -- it will likely differ
from tgw-coder's /opt/TGW/var/worktrees/ root, since it operates on a
different repo).

Payload field used: `agent_type`, confirmed present on PreToolUse hook
input by reverse-engineering the installed Claude Code binary's `ff()`
hook-context builder (session_id, transcript_path, cwd, permission_mode,
agent_id, agent_type, effort) -- not documented in the public hook-input
example in the CLI's own /hooks help text (which only shows session_id/
tool_name/tool_input/tool_response), so treat this as best-effort: if the
field is ever renamed/removed by an upstream Claude Code update, this hook
degrades to a no-op (fails open) rather than false-blocking every agent's
Edit/Write -- see the `except Exception` guard below.
"""
import json
import os
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = payload.get("tool_name") or ""
tool_input = payload.get("tool_input") or {}
file_path = tool_input.get("file_path") or ""
agent_type = payload.get("agent_type") or ""

# Agents whose contract mandates the manual /opt/TGW/var/worktrees (or
# /home/db/tgw-worktrees) convention -- see module docstring for why
# nix-flake-maintainer is intentionally excluded.
WORKTREE_REQUIRED_AGENTS = {"tgw-coder"}

# Roots seen live in this repo's `git worktree list` as of #1450's
# investigation (2026-07-17/18). Both are valid -- tgw-coder.md specifies
# /opt/TGW/var/worktrees/<id>-<slug>; /home/db/tgw-worktrees/<id>-<slug>
# is an equivalent root already in active use for the same convention
# (see #1450-RESULT.md's `git worktree list` output). Keep both allowed
# rather than picking one and false-blocking the other.
ALLOWED_ROOTS = (
    "/opt/TGW/var/worktrees/",
    "/home/db/tgw-worktrees/",
)

# Paths that are never a tgw-coder edit target regardless of worktree
# root -- e.g. the shared checkout itself, or the harness's own
# auto-provisioned worktree dir (the exact conflict this hook exists to
# prevent recurring).
BLOCKED_ROOTS = (
    "/opt/TGW/src/trader-grims-warehouse/.claude/worktrees/",
)


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


if tool_name in ("Edit", "Write") and agent_type in WORKTREE_REQUIRED_AGENTS:
    path = os.path.normpath(file_path) if file_path else ""

    in_allowed_root = any(
        path.startswith(os.path.normpath(root) + os.sep) or path == os.path.normpath(root)
        for root in ALLOWED_ROOTS
    )
    in_blocked_root = any(
        path.startswith(os.path.normpath(root) + os.sep) or path == os.path.normpath(root)
        for root in BLOCKED_ROOTS
    )

    if not path:
        # No file_path on this Edit/Write call -- nothing to check.
        pass
    elif in_blocked_root:
        ask(
            f"Edit/Write into {file_path} targets a Claude-Code-harness-provisioned "
            "worktree path (.claude/worktrees/), not tgw-coder's manual "
            "/opt/TGW/var/worktrees/<id>-<slug> convention (PP-HERMES-EA-001, "
            ".claude/agents/tgw-coder.md Step 2). This is the exact double-worktree "
            "conflict todo #1389/#1450 found live (orphaned "
            "agent-a271e21fa52fe73ad). Create your worktree with "
            "`git worktree add -b todo/<id>-<slug> /opt/TGW/var/worktrees/<id>-<slug> "
            "<verified-base-branch>` and retry the edit at the worktree path."
        )
    elif not in_allowed_root:
        ask(
            f"Edit/Write into {file_path} is outside the mandatory tgw-coder worktree "
            "convention (PP-HERMES-EA-001, .claude/agents/tgw-coder.md Step 2 -- "
            "/opt/TGW/var/worktrees/<id>-<slug> or /home/db/tgw-worktrees/<id>-<slug>, "
            "never the shared checkout at /opt/TGW/src/trader-grims-warehouse). If you "
            "haven't created your task worktree yet, do that first; if this is meant to "
            "be a genuinely shared-checkout change, confirm explicitly with Dave/Tigwa "
            "before proceeding."
        )

sys.exit(0)
