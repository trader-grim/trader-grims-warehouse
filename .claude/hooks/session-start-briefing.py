#!/usr/bin/env python3
"""SessionStart hook: surface CLAUDE.md's mandatory startup-sequence inputs
(inbox/claude backlog, unprocessed suggestions, plan check/status) directly
into context, before any reply is composed. Read-only -- never mutates
files, todos, or plan state. Built 2026-07-16 (PP-AGENT-DISCIPLINE-001)
after the startup ritual was skipped twice in one day relying on the
model's own judgment call; see
docs/TGW-Plan-Vault/inbox/queued/INCIDENT-2026-07-16-kdeconnect-clipboard-triage-failure.md.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path("/opt/TGW/src/trader-grims-warehouse")
INBOX = REPO_ROOT / "docs/TGW-Plan-Vault/inbox/claude"
SUGGESTIONS = REPO_ROOT / "docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md"

# Drain stdin (hook payload) without depending on its contents.
try:
    json.load(sys.stdin)
except Exception:
    pass

lines = []
lines.append("## Startup-ritual auto-briefing (SessionStart hook, PP-AGENT-DISCIPLINE-001)")
lines.append(
    "CLAUDE.md's Step 1/3 inputs, collected automatically -- act on Step 1 "
    "(process any pending inbox/suggestions items) and Step 3 (read the "
    "master plan, then react to plan check/status) before anything else, "
    "regardless of how the user's first message reads."
)

# 1. inbox/claude backlog
try:
    if INBOX.is_dir():
        files = sorted(p.name for p in INBOX.glob("*.md"))
        if files:
            lines.append(f"\n### inbox/claude/ -- {len(files)} file(s) pending")
            for name in files:
                lines.append(f"- {name}")
        else:
            lines.append("\n### inbox/claude/ -- empty, nothing pending")
    else:
        lines.append(f"\n### inbox/claude/ -- directory not found at {INBOX}")
except Exception as exc:
    lines.append(f"\n### inbox/claude/ -- error listing: {exc}")

# 2. unprocessed suggestions
try:
    if SUGGESTIONS.is_file():
        text = SUGGESTIONS.read_text(errors="replace")
        unchecked = sum(1 for line in text.splitlines() if line.lstrip().startswith("- [ ]"))
        if unchecked:
            lines.append(f"\n### SUGGESTIONS.md -- {unchecked} unprocessed item(s) (unchecked '- [ ]' lines)")
        else:
            lines.append("\n### SUGGESTIONS.md -- fully processed, 0 unchecked items")
    else:
        lines.append(f"\n### SUGGESTIONS.md -- not found at {SUGGESTIONS}")
except Exception as exc:
    lines.append(f"\n### SUGGESTIONS.md -- error reading: {exc}")


def run_tgw(args, max_lines=None):
    try:
        proc = subprocess.run(
            ["sudo", "-u", "tgw", "tgw"] + args,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            lines.append(f"\n### `tgw {' '.join(args)}` -- exit {proc.returncode}, non-fatal")
            if out:
                lines.append(out)
            if err:
                lines.append(err)
            return
        lines.append(f"\n### `tgw {' '.join(args)}`")
        if not out:
            lines.append("(no output)")
            return
        if max_lines:
            out_lines = out.splitlines()
            if len(out_lines) > max_lines:
                shown = "\n".join(out_lines[:max_lines])
                lines.append(shown)
                lines.append(f"... {len(out_lines) - max_lines} more line(s), run `tgw {' '.join(args)}` for the full list")
                return
        lines.append(out)
    except FileNotFoundError:
        lines.append(f"\n### `tgw {' '.join(args)}` -- `sudo`/`tgw` not found on PATH, skipped")
    except subprocess.TimeoutExpired:
        lines.append(f"\n### `tgw {' '.join(args)}` -- timed out after 20s, skipped")
    except Exception as exc:
        lines.append(f"\n### `tgw {' '.join(args)}` -- error: {exc}, skipped")


run_tgw(["plan", "check"])
# plan status can run to 50+ PP lines -- cap it, full detail is one command away
run_tgw(["plan", "status"], max_lines=8)

briefing = "\n".join(lines)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": briefing,
    }
}))
