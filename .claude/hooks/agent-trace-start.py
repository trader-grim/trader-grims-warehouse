#!/usr/bin/env python3
"""SessionStart hook: record the start of an agent-trace run (PP-AGENTTRACE-001
Phase 4). Calls `tgw trace start` and persists the returned run_id keyed by
this session's session_id, so the matching Stop hook (agent-trace-stop.py) can
close the run out later without any other shared state between the two
processes.

Best-effort, never blocking: any failure here (DB down, tgw CLI missing,
etc.) is swallowed -- trace logging must never be able to break a session
starting. No visible additionalContext is emitted; this is silent
bookkeeping, unlike session-start-briefing.py which surfaces real content
the model needs to act on.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(os.environ.get("TGW_HOOK_REPO_ROOT", "/opt/TGW/src/trader-grims-warehouse"))
RUNS_STATE_DIR = Path("/opt/TGW/var/agent-traces/.session-runs")

# Which kind of agent this hook is tracing -- overridable per-caller the same
# way session-start-briefing.py's TGW_HOOK_ACTOR works, so a future non-main
# Claude Code entry point can reuse this file without a fork.
AGENT_TYPE = os.environ.get("TGW_TRACE_AGENT_TYPE", "claude-session").strip() or "claude-session"

try:
    payload = json.load(sys.stdin)
except Exception:
    payload = {}

session_id = str(payload.get("session_id") or "").strip()
if not session_id:
    # Nothing to key the run on later -- skip silently rather than guess.
    sys.exit(0)


def run_tgw(args, timeout=15):
    return subprocess.run(
        ["sudo", "-u", "tgw", "tgw"] + args,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


try:
    proc = run_tgw(["trace", "start", "--agent-type", AGENT_TYPE])
    if proc.returncode != 0:
        sys.exit(0)
    run_id = (proc.stdout or "").strip()
    if not run_id:
        sys.exit(0)

    # Ensure the state dir exists, owned by tgw (matches agent-traces/'s own
    # ownership) -- mkdir -p is idempotent, safe to run every session start.
    subprocess.run(
        ["sudo", "-u", "tgw", "mkdir", "-p", str(RUNS_STATE_DIR)],
        capture_output=True, text=True, timeout=10,
    )
    subprocess.run(
        ["sudo", "-u", "tgw", "chmod", "0770", str(RUNS_STATE_DIR)],
        capture_output=True, text=True, timeout=10,
    )

    state_file = RUNS_STATE_DIR / f"{session_id}.run_id"
    # Write via `tee` (no shell interpolation of run_id/paths -- avoids any
    # injection risk even though run_id is a trusted uuid4 hex string).
    write_proc = subprocess.run(
        ["sudo", "-u", "tgw", "tee", str(state_file)],
        input=run_id, capture_output=True, text=True, timeout=10,
    )
    if write_proc.returncode == 0:
        subprocess.run(
            ["sudo", "-u", "tgw", "chmod", "0660", str(state_file)],
            capture_output=True, text=True, timeout=10,
        )
except Exception:
    pass

sys.exit(0)
