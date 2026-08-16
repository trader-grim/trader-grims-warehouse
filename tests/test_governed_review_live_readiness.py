"""Read-only regression for the currently selected live provider projection."""

import subprocess

import pytest


def _sudo(*argv):
    result = subprocess.run(
        ["sudo", "-n", *argv], text=True, capture_output=True, check=False,
    )
    if result.returncode:
        pytest.skip("live provider metadata is unavailable to this harness")
    return result.stdout.strip()


def test_current_live_provider_is_hold_until_protected_skill_and_mcp_projection_exists():
    command = "/home/claude/.local/bin/claude"
    target = _sudo("readlink", "-f", command)
    if not target:
        pytest.skip("current live provider is not installed")
    link = _sudo("stat", "-c", "%d %i %u %g %a %h %s %Y", command).split()
    resolved = _sudo("stat", "-Lc", "%d %i %u %g %a %h %s %Y", command).split()
    target_hash = _sudo("sha256sum", target).split()[0]
    skill_link = _sudo(
        "stat", "-c", "%u %g %a", "/home/claude/.claude/skills/tgw-review",
    ).split()
    skill_file = _sudo(
        "stat", "-Lc", "%u %g %a", "/home/claude/.claude/skills/tgw-review/SKILL.md",
    ).split()
    credential = _sudo(
        "stat", "-c", "%u %g %a %h %s", "/home/claude/.claude/.credentials.json",
    ).split()

    assert target == "/home/claude/.local/share/claude/versions/2.1.223"
    assert link[2:7] == ["1006", "1006", "777", "1", "49"]
    assert resolved[2:7] == ["1006", "1006", "755", "1", "290728968"]
    assert target_hash == "98226474f802e3094d6a86c5ade8883c16206d0fcb5c400b7401c800063e99d7"
    assert skill_link == ["1006", "1006", "777"]
    assert skill_file == ["1004", "983", "664"]
    assert credential[:4] == ["1006", "1006", "600", "1"]

    # The executable and sealed credential have distinct, admissible owner
    # policies. The currently discovered skill does not: it resolves to a
    # group-writable development checkout. Therefore the real provider is a
    # deterministic HOLD until a protected tgw-review/MCP/runtime projection is
    # provisioned; installed-skill visibility alone is never readiness.
    violations = []
    if int(skill_file[2], 8) & 0o022:
        violations.append("skill-contract-resolved-mode")
    if skill_file[:2] != ["0", "0"]:
        violations.append("skill-contract-resolved-owner")
    assert violations == [
        "skill-contract-resolved-mode", "skill-contract-resolved-owner",
    ]
