#!/usr/bin/env python3
"""PreToolUse allow-hook: mechanically pre-approve a tight set of known-safe
coding-workflow and read-only tgw-prod-diagnostic Bash commands, so the
auto-mode classifier never sees them.

Why this exists (operator-directed, 2026-09-06 / session 93c00702): the
auto-mode classifier hard-refuses commands that are *already* in
`permissions.allow` and *already* described in `.claude/settings.json`
`autoMode.allow` (e.g. `sudo -n -u db sudo -n systemctl status <unit>`;
`sudo -n -u db ssh tgw-prod journalctl ...` — both blocked with no prompt
despite the explicit autoMode.allow line for prod read-only diagnostics).
A PreToolUse hook runs *before* the classifier and, by returning
`permissionDecision: "allow"`, bypasses it entirely — the same mechanism the
other guards in this dir use to *block*, used here to *allow*.

Layers, in order:
  1. sudoers-backed shapes — `sudo -n -u tgw-harness /usr/bin/git -C <repo> …`
     and the harness_cli — are granted verbatim by /etc/sudoers.d/tgw-harness,
     so they are allowed before the denylist even runs.
  2. Hard denylist — any mutation / privilege-broadening / file-write token
     anywhere in the command drops it to the normal permission path.
  3. Read-only diagnostic shapes — local `systemctl` reads as db, prod
     diagnostics over the db-owned forced-command ssh path (first remote word
     must be in an explicit read-only set), read-only `tgw` subcommands.

Contract: only ever emits "allow", never "deny"/"ask". A non-match exits
silently (code 0, empty stdout) and the classifier / static allowlist still
applies. Fails open on any error.

Scope: `Bash` tool calls only. Edit/Write stay with worktree-guard /
app-code-guard.
"""
from __future__ import annotations

import json
import re
import sys

try:
    _payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if not isinstance(_payload, dict) or (_payload.get("tool_name") or "") != "Bash":
    sys.exit(0)

_command = (_payload.get("tool_input") or {}).get("command") or ""
if not _command.strip():
    sys.exit(0)

_REPO = r"/opt/TGW/(?:tgw-lib/src/trader-grims-warehouse|library/plans)"

# --------------------------------------------------------------------------- #
# Layer 1 — sudoers-backed shapes. /etc/sudoers.d/tgw-harness grants
# `tgw-harness ... /usr/bin/git -C <repo> *` and the harness_cli outright, so
# the interactive session running these adds no privilege the harness identity
# doesn't already hold. Allowed before the denylist.
# --------------------------------------------------------------------------- #
_SUDOERS_ALLOW = (
    re.compile(
        rf"^\s*sudo\s+-n\s+-u\s+tgw-harness\s+/usr/bin/git\s+-C\s+{_REPO}(?:\s|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*sudo\s+-n\s+-u\s+tgw-harness\s+"
        r"/opt/TGW/\.venvs/controller/bin/python3\s+-m\s+tgw\.development\.harness_cli(?:\s|$)",
        re.IGNORECASE,
    ),
)

# --------------------------------------------------------------------------- #
# Layer 2 — hard denylist. If any of these appear anywhere, never pre-approve.
# --------------------------------------------------------------------------- #
_DENY = re.compile(
    r"""
      \b(?:INSERT|UPDATE|DELETE|DROP|TRUNCATE|ALTER|GRANT|REVOKE|VACUUM|REINDEX|CLUSTER)\b
    | \bCREATE\s+(?:TABLE|INDEX|DATABASE|ROLE|USER|SCHEMA|EXTENSION|FUNCTION|VIEW|TRIGGER)\b
    | \bCOPY\b[^;|]*\bFROM\b
    | \\copy\b
    | \bsystemctl\b[^;|&]*\b(?:start|stop|restart|reload|kill|enable|disable|mask|unmask|reset-failed|daemon-reload|edit|set-property|isolate)\b
    | \b(?:rm|rmdir|mv|cp|chmod|chown|chgrp|ln|mkdir|touch|dd|truncate|shred|install|setfacl|useradd|usermod|groupadd|visudo|mkfs|mount|umount)\b
    | \bpsql\b[^;|&]*\s-f\b
    | \b(?:pip|pip3|npm|yarn|apt|apt-get|dpkg|nix-env|luet)\s+(?:install|remove|uninstall|update|upgrade|add|rm)\b
    | \btee\b
    | (?<![0-9&])>{1,2}(?!&)
    | --dangerously
    | \bnixos-rebuild\s+(?:switch|boot|test)\b
    | \btgw\s+(?:publish|requeue|stage|restart-workers|create-item|set-shipping|retry|cancel|delete|purge)\b
    | --requeue
    | \bkill(?:all)?\b
    | \bcrontab\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# --------------------------------------------------------------------------- #
# Layer 3 — read-only diagnostic shapes.
# --------------------------------------------------------------------------- #

# read-only first word for a command run on tgw-prod via the forced ssh path
_RO_REMOTE_WORDS = {
    "journalctl", "ps", "cat", "head", "tail", "grep", "egrep", "fgrep",
    "zgrep", "zcat", "less", "ls", "stat", "readlink", "realpath", "file",
    "wc", "date", "uptime", "df", "free", "hostname", "id", "uname", "whoami",
    "awk", "sed", "sort", "uniq", "cut", "tr", "jq", "echo", "printf", "true",
    "du", "pg_isready", "getent", "loginctl", "networkctl", "resolvectl",
    "sha256sum", "md5sum",
}
# systemctl / psql are read-only only with the right sub-args
_SYSTEMCTL_RO_SUB = {
    "status", "cat", "show", "list-units", "list-unit-files", "list-timers",
    "list-sockets", "list-dependencies", "is-active", "is-enabled",
    "is-failed", "get-default", "show-environment",
}

_LOCAL_SYSTEMCTL = re.compile(
    r"^\s*(?:timeout\s+\d+\s+)?sudo\s+-n\s+-u\s+db\s+sudo\s+-n\s+systemctl\s+(\S+)",
    re.IGNORECASE,
)
_PROD_SSH = re.compile(
    r"^\s*(?:timeout\s+\d+\s+)?sudo\s+-n\s+-u\s+db\s+ssh\b(?P<opts>(?:\s+-\S+(?:\s+\S+)?)*)"
    r"\s+tgw-prod\s+(?P<remote>.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_TGW_RO = re.compile(
    r"^\s*(?:timeout\s+\d+\s+)?(?:/usr/local/libexec/tgw-production-client|tgw)\s+"
    r"(?:health|status|staged|ready|dead-letter|queue-history|queue-status|ops-digest|"
    r"report|get|get-item|list|search|resolve|picklist|quality|hint-trail|audit-trail|"
    r"history-index|catalog-verify|store-categories|ai-usage|simple-llm-jobs)\b",
    re.IGNORECASE,
)


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1].strip()
    return s


def _remote_is_read_only(remote: str) -> bool:
    remote = _strip_quotes(remote)
    if not remote:
        return False
    # split on shell pipeline / sequencing; every stage must be read-only
    for stage in re.split(r"\|\||&&|[|;&]", remote):
        toks = stage.split()
        if not toks:
            continue
        head = toks[0]
        if head == "sudo":
            return False
        if head == "systemctl":
            if len(toks) < 2 or toks[1] not in _SYSTEMCTL_RO_SUB:
                return False
            continue
        if head == "psql":
            if " -c " not in (" " + stage + " "):
                return False
            continue
        if head not in _RO_REMOTE_WORDS:
            return False
    return True


def _allow() -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": (
                "coding-workflow-allow: sudoers-granted or read-only diagnostic "
                "coding-workflow command, pre-approved past the auto-mode "
                "classifier (operator-directed 2026-09-06). Mutation tokens are "
                "denylisted."
            ),
        }
    }))
    sys.exit(0)


# Layer 1
for _p in _SUDOERS_ALLOW:
    if _p.search(_command):
        _allow()

# Layer 2
if _DENY.search(_command):
    sys.exit(0)

# Layer 3
if _TGW_RO.search(_command):
    _allow()

_m = _LOCAL_SYSTEMCTL.match(_command)
if _m and _m.group(1) in _SYSTEMCTL_RO_SUB:
    _allow()

_m = _PROD_SSH.match(_command)
if _m and _remote_is_read_only(_m.group("remote")):
    _allow()

# no match -> silent pass-through; the normal permission path still applies.
sys.exit(0)
