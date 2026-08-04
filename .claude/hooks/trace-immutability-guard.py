#!/usr/bin/env python3
"""PreToolUse guard: mechanically block any modification or deletion of
agent-trace evidence (PP-AGENTTRACE-001 invariant E14). Dave, 2026-07-20,
after opening the authenticity-review thread (todo #1585/#1586): "this
needs to be baked into everyone's contract. No touching the traces or you
self-destruct." Read literally about severity, not literally about
mechanism -- this hook is the actual enforcement (a hard DENY, not a
self-destruct routine, which would itself be a destructive action this
project's own doctrine wouldn't sanction building). Same "prose depends on
the model choosing to comply, a hook doesn't ask" doctrine as E9/E11/E12.

Scope: EVERY agent, no exemptions -- unlike flake-guard.py/app-code-guard.py
(which exempt the one agent whose contract IS to make that class of change),
there is no legitimate agent identity that should ever directly Edit/Write/
rm/mv/sed the archived trace files, or UPDATE/DELETE/DROP/TRUNCATE the
agent_runs / hash-commitment tables via a raw SQL command. The only
sanctioned write path is the `tgw trace start`/`tgw trace end` CLI and
`tgw.logging.archive_transcript()` (Phase 1, insert-only by design; Phase
1b/packet #1586 adds an explicit insert-once hash-commitment lock) --
neither of those goes through this hook's guarded verbs/patterns below, so
legitimate trace-recording is unaffected.

Deliberately best-effort on the Bash side (pattern-matched against the
command string), same transparency convention as worktree-guard.py/
app-code-guard.py's own admitted Bash gaps -- a sufficiently obfuscated
command or out-of-band DB access this hook can't see is a known,
documented limitation, not claimed coverage this hook doesn't have.

Matching is deliberately scoped PER LINE (a destructive verb/redirect and
the traces path must appear together on the same line) -- caught live in
testing (2026-07-20): a command writing an unrelated file whose heredoc
BODY happened to mention "/opt/TGW/var/agent-traces/" as prose (this very
docstring's own commit, in fact) false-positived under a whole-command
substring check. Per-line scoping fixes that specific case without
weakening real single-line destructive commands, which is the actual
threat model here -- but is still not a full shell parser, so treat this
as a strong practical filter, not a formal guarantee.
"""
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_name = payload.get("tool_name") or ""
tool_input = payload.get("tool_input") or {}

TRACES_ROOT = "/opt/TGW/var/agent-traces"

# Bash verbs/patterns that mutate or destroy a file, checked only when the
# traces path ALSO appears on the same line (see per-line scoping note above).
_DESTRUCTIVE_FS_RE = re.compile(
    r"\b(rm|mv|cp\s+-f|dd|shred|truncate|chmod|chown)\b|"
    r"(>>?|\btee\b)|"
    r"\bsed\b.*-i\b"
)

# SQL that mutates or destroys the trace tables, wherever it appears
# (psql -c "...", a heredoc, etc.) -- table names from Phase 1/packet #1586.
_TRACE_TABLES = r"(agent_runs|agent_run_transcript_hashes)"
_DESTRUCTIVE_SQL_RE = re.compile(
    r"\b(UPDATE|DELETE\s+FROM|DROP\s+TABLE|TRUNCATE)\b[^;]*" + _TRACE_TABLES,
    re.IGNORECASE,
)


def _fs_command_hits_traces(command: str) -> bool:
    """True if a destructive verb/redirect and TRACES_ROOT co-occur on the
    same line -- not just anywhere in a (possibly multi-line, heredoc-
    bearing) command string. See module docstring for why this matters."""
    for line in command.splitlines():
        if TRACES_ROOT in line and _DESTRUCTIVE_FS_RE.search(line):
            return True
    return False


def deny(reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


REASON = (
    "Blocked by invariant E14 (PP-AGENTTRACE-001) -- agent-trace evidence "
    "(archived transcripts under /opt/TGW/var/agent-traces/, the agent_runs "
    "table, and the hash-commitment table) is write-once/append-only for "
    "every agent, with no exemptions, per Dave's explicit standing order "
    "(2026-07-20): \"no touching the traces.\" The only sanctioned write "
    "path is `tgw trace start`/`tgw trace end` and archive_transcript()'s "
    "own insert-only logic -- if you need to record a run, use that CLI; "
    "there is no legitimate reason for any agent to directly Edit/Write/rm/"
    "mv/sed a trace file or UPDATE/DELETE/DROP/TRUNCATE a trace table. If "
    "this really is a legitimate need (e.g. Dave has explicitly directed a "
    "specific correction), stop and get that confirmed explicitly in-session "
    "-- do not route around this guard."
)

if tool_name in ("Edit", "Write"):
    file_path = tool_input.get("file_path") or ""
    if file_path.startswith(TRACES_ROOT):
        deny(REASON)

if tool_name == "Bash":
    command = tool_input.get("command") or ""
    if _fs_command_hits_traces(command):
        deny(REASON)
    if _DESTRUCTIVE_SQL_RE.search(command):
        deny(REASON)

sys.exit(0)
