"""Read-only regression for the currently selected live provider projection."""

import grp
import re
import subprocess

import pytest


def _sudo(*argv):
    result = subprocess.run(
        ["sudo", "-n", *argv], text=True, capture_output=True, check=False,
    )
    if result.returncode:
        pytest.skip("live provider metadata is unavailable to this harness")
    return result.stdout.strip()


def test_current_live_actor_skill_and_mcp_projections_are_protected():
    command = "/home/claude/.local/bin/claude"
    target = _sudo("readlink", "-f", command)
    if not target:
        pytest.skip("current live provider is not installed")
    link = _sudo("stat", "-c", "%d %i %u %g %a %h %s %Y", command).split()
    resolved = _sudo("stat", "-Lc", "%d %i %u %g %a %h %s %Y", command).split()
    skill_link = _sudo(
        "stat", "-c", "%u %g %a", "/home/claude/.claude/skills/tgw-review",
    ).split()
    skill_file = _sudo(
        "stat", "-Lc", "%u %g %a", "/home/claude/.claude/skills/tgw-review/SKILL.md",
    ).split()
    mcp_link = _sudo(
        "stat", "-c", "%u %g %a", "/home/claude/.claude/.mcp.json",
    ).split()
    mcp_file = _sudo(
        "stat", "-Lc", "%u %g %a", "/home/claude/.claude/.mcp.json",
    ).split()
    credential = _sudo(
        "stat", "-c", "%u %g %a %h %s", "/home/claude/.claude/.credentials.json",
    ).split()

    assert re.fullmatch(r"/home/claude/\.local/share/claude/versions/\d+\.\d+\.\d+", target)
    assert link[2:6] == ["1006", "1006", "777", "1"]
    assert resolved[2:6] == ["1006", "1006", "755", "1"]
    actor_group = str(grp.getgrnam("tgw-coders").gr_gid)
    assert skill_link == ["0", actor_group, "777"]
    assert skill_file == ["0", "0", "444"]
    assert mcp_link == ["0", actor_group, "777"]
    assert mcp_file == ["0", "0", "444"]
    assert credential[:4] == ["1006", "1006", "600", "1"]

    # These are actor-local discovery projections only. They prove that every
    # harness can find the root-materialized skill and Context MCP inputs; they
    # are not the separate, per-review protected projection or execution
    # receipt required for governed admission.
    violations = []
    if int(skill_file[2], 8) & 0o022:
        violations.append("skill-contract-resolved-mode")
    if skill_file[:2] != ["0", "0"]:
        violations.append("skill-contract-resolved-owner")
    if int(mcp_file[2], 8) & 0o022:
        violations.append("mcp-config-resolved-mode")
    if mcp_file[:2] != ["0", "0"]:
        violations.append("mcp-config-resolved-owner")
    assert violations == []
