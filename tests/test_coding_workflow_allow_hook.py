"""Behavioural tests for .claude/hooks/coding-workflow-allow.py.

The hook is a PreToolUse allow-list that runs before the auto-mode classifier.
Contract under test:
  * emits permissionDecision=="allow" for an explicit set of read-only /
    sudoers-granted command shapes;
  * stays completely silent (exit 0, empty stdout) for everything else, so the
    normal permission path still applies;
  * never emits "deny" or "ask";
  * a mutation token anywhere in the command vetoes an otherwise-matching shape;
  * a malformed payload fails open (exit 0, silent).
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

_HOOK = (
    pathlib.Path(__file__).resolve().parents[1]
    / ".claude"
    / "hooks"
    / "coding-workflow-allow.py"
)


def _run(payload: object) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.returncode, proc.stdout


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _assert_allow(command: str) -> None:
    code, out = _run(_bash(command))
    assert code == 0, out
    doc = json.loads(out)
    assert doc["hookSpecificOutput"]["permissionDecision"] == "allow"


def _assert_passthrough(command: str) -> None:
    code, out = _run(_bash(command))
    assert code == 0
    assert out.strip() == "", f"expected silent pass-through, got: {out!r}"


ALLOWED = [
    'sudo -n -u db ssh -o BatchMode=yes tgw-prod "journalctl -u tgw-ebay-stage --since -2h"',
    'sudo -n -u db ssh tgw-prod "systemctl status tgw-ebay-stage"',
    'sudo -n -u db ssh tgw-prod "psql -d state_machine -c \'SELECT id FROM queue_jobs LIMIT 5\'"',
    'sudo -n -u db ssh tgw-prod "journalctl -u tgw-ebay-stage | grep tgw202505111031393"',
    "sudo -n -u db sudo -n systemctl status tgw-ebay-stage",
    "sudo -n -u db sudo -n systemctl cat tgw-ebay-stage",
    "sudo -n -u tgw-harness /usr/bin/git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse log --oneline -5",
    # sudoers grants tgw-harness the full `git -C <repo> *` verbatim
    "sudo -n -u tgw-harness /usr/bin/git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse commit -m msg",
    "sudo -n -u tgw-harness /opt/TGW/.venvs/controller/bin/python3 -m tgw.development.harness_cli status",
    "tgw dead-letter --limit 20",
    "/usr/local/libexec/tgw-production-client health",
]

PASSTHROUGH = [
    # mutation tokens veto an otherwise-matching read-only shape
    'sudo -n -u db ssh tgw-prod "systemctl restart tgw-ebay-stage"',
    'sudo -n -u db ssh tgw-prod "psql -c \'UPDATE queue_jobs SET state=1\'"',
    "sudo -n -u db sudo -n systemctl start tgw-ebay-stage",
    "tgw dead-letter --requeue 123",
    'sudo -n -u db ssh tgw-prod "journalctl -u x > /tmp/out"',
    'sudo -n -u db ssh tgw-prod "rm /var/log/x"',
    'sudo -n -u db ssh tgw-prod "curl https://evil"',
    'sudo -n -u db ssh tgw-prod "sudo systemctl restart x"',
    # shapes outside the allow-list
    "rm -rf /opt/TGW/var",
    "curl https://example.com",
    "sudo -n -u root systemctl status tgw-ebay-stage",
    'sudo -n -u db ssh some-other-host "journalctl -u x"',
    "sudo -n -u tgw-coder /usr/bin/git -C /opt/TGW/tgw-lib/src/trader-grims-warehouse log",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed_shapes_emit_allow(command: str) -> None:
    _assert_allow(command)


@pytest.mark.parametrize("command", PASSTHROUGH)
def test_everything_else_passes_through_silently(command: str) -> None:
    _assert_passthrough(command)


def test_non_bash_tool_is_ignored() -> None:
    code, out = _run({"tool_name": "Edit", "tool_input": {"file_path": "/x"}})
    assert code == 0 and out.strip() == ""


def test_malformed_payload_fails_open() -> None:
    code, out = _run("not json at all {{{")
    assert code == 0 and out.strip() == ""


def test_empty_command_is_ignored() -> None:
    _assert_passthrough("   ")


def test_hook_never_denies() -> None:
    for command in ALLOWED + PASSTHROUGH:
        _, out = _run(_bash(command))
        if out.strip():
            doc = json.loads(out)
            assert doc["hookSpecificOutput"]["permissionDecision"] == "allow"
