#!/usr/bin/env python3
"""PreToolUse guard: mechanically enforce that application-code changes
route through the tgw-coder branch-per-task contract (PP-HERMES-EA-001),
the same way flake-guard.py mechanizes the nix-flake-maintainer contract
for ~/tgw-flake.

Background (2026-07-18): a multi-hour live-troubleshooting session found
and fixed a real bug (a stale inventory-page badge), then kept going --
CI-1 through CI-4 of PP-CATALOG-INCR-001, several other fixes -- all via
direct Edit/Write calls on http_server.py/items.py/sqlite_catalog.py/
state_machine.py and test files in the SHARED checkout, from the main
session, never handed off to tgw-coder's isolated worktree+branch. Dave:
"we seem to be running these fixes outside our new process. How can we
funnel these troubleshooting type sessions through a similar process to
what we did in the sprint... make both something you recognize and
something that can be specified directly like the nix maintainer?" This
hook is the "recognize" half -- the same mechanical nudge flake-guard.py
already gives for flake work, now for application source too.

Scope: only agents whose contract is to execute scoped, packet-driven
work in an isolated worktree -- as of 2026-07-18 that is tgw-coder only.
The main session (agent_type empty/"claude") and any other agent doing
live diagnosis/triage should recognize this prompt as the signal to stop
editing directly and dispatch to tgw-coder instead, exactly as flake-guard
nudges toward nix-flake-maintainer for flake paths.

Deliberately scoped to Edit/Write only (not Bash), matching
worktree-guard.py's footprint -- today's actual violation was Edit tool
calls, not Bash-driven file mutation (sed/tee/etc against src paths). A
Bash-based bypass of this guard is a known, documented gap, same
transparency convention as other hooks here that don't claim more
coverage than they actually have.
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

REPO_ROOT = "/opt/TGW/src/trader-grims-warehouse"
GUARDED_SUBDIRS = ("src/tgw/", "tests/")
EXEMPT_AGENTS = {"tgw-coder"}

INCIDENT = "docs/TGW-Plan-Vault/inbox/claude/INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md"


def ask(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


if tool_name in ("Edit", "Write") and agent_type not in EXEMPT_AGENTS and file_path:
    path = os.path.normpath(file_path)
    rel = os.path.relpath(path, REPO_ROOT) if path.startswith(REPO_ROOT) else None

    if rel and not rel.startswith("..") and any(
        rel == d.rstrip("/") or rel.startswith(d) for d in GUARDED_SUBDIRS
    ):
        ask(
            f"Direct Edit/Write into {file_path} is an application-code change in the "
            "shared checkout, outside tgw-coder's branch-per-task contract "
            "(PP-HERMES-EA-001, .claude/agents/tgw-coder.md). Dave, 2026-07-18: "
            "troubleshooting-session fixes should route through tgw-coder once "
            "root-caused, the same way flake work routes through nix-flake-maintainer "
            "-- diagnose freely, then dispatch the scoped fix as a todo/packet instead "
            "of editing here directly. If this genuinely needs to happen in the shared "
            "checkout right now (e.g. an XS one-line fix Dave has explicitly asked for "
            "inline), confirm that explicitly before proceeding rather than defaulting "
            f"to direct edits. See {INCIDENT} for the earlier triage-discipline incident "
            "this pattern is modeled on."
        )

sys.exit(0)
