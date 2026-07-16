#!/usr/bin/env python3
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

cmd = (payload.get("tool_input") or {}).get("command") or ""

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

sys.exit(0)
