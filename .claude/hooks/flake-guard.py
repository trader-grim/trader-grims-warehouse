#!/usr/bin/env python3
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = payload.get("tool_name") or ""
tool_input = payload.get("tool_input") or {}
cmd = tool_input.get("command") or ""
# Edit/Write tool_input carries file_path; MultiEdit-style payloads aren't
# handled here since the matcher below only registers Bash/Edit/Write.
file_path = tool_input.get("file_path") or ""

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

if re.search(r"\bnixos-rebuild\s+(switch|test)\b", cmd):
    ask(
        "nixos-rebuild switch/test is a gated system mutation on tgw-prod/a1131 -- "
        "use the nix-flake-maintainer agent (.claude/agents/nix-flake-maintainer.md) "
        "or the commit-nix-flake skill procedure (dry-activate, per-host session-safety "
        f"check, durability verify) before proceeding. See {INCIDENT}."
    )

if "tgw-flake" in cmd and re.search(r"\bgit\s+(commit|push)\b", cmd):
    ask(
        "git commit/push on ~/tgw-flake is a shared-infra history change across "
        "tgw-prod and a1131 -- use the nix-flake-maintainer agent (checks drift on "
        f"both hosts first) or confirm explicitly with Dave. See {INCIDENT}."
    )

# invariant E11 follow-up (todo #1449): raw Edit/Write tool calls landing on
# files inside ~/tgw-flake (either host -- this only inspects the path
# string, so it applies the same way whether the session is on tgw-prod or
# a1131) are just as much a shared-infra mutation as a Bash-driven edit
# would be. Match on "tgw-flake" appearing as a path segment, same
# substring convention already used for the Bash git-commit/push check
# above, so a path like ~/tgw-flake/hosts/a1131/default.nix is caught
# without needing to know the caller's home directory.
if tool_name in ("Edit", "Write") and re.search(r"(^|/)tgw-flake(/|$)", file_path):
    ask(
        "Edit/Write directly on a ~/tgw-flake file is a gated system-config "
        "mutation on tgw-prod/a1131 -- use the nix-flake-maintainer agent "
        "(.claude/agents/nix-flake-maintainer.md) or the commit-nix-flake skill "
        f"procedure before proceeding. See {INCIDENT}."
    )

sys.exit(0)
