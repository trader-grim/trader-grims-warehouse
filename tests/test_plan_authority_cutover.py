"""Regression checks for the standalone approved-Plan authority cutover."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
_DIRECT_MASTER_CONFIG_READ = re.compile(
    r"(?:cfg|self\.config)\s*(?:\[['\"]plan_master_path['\"]\]|"
    r"\.get\(['\"]plan_master_path['\"]\))"
)


def test_no_operational_plan_consumer_reads_configured_master_path():
    """A path from config can never replace approved_plan_binding()."""
    offenders = []
    for source in sorted((ROOT / "src" / "tgw").rglob("*.py")):
        if _DIRECT_MASTER_CONFIG_READ.search(source.read_text(encoding="utf-8")):
            offenders.append(str(source.relative_to(ROOT)))
    assert offenders == []


def test_active_navigation_and_operator_docs_do_not_name_legacy_plan_authority():
    """The application source tree must not recreate an embedded Plan Vault."""
    active_files = (
        ROOT / "AGENTS.md",
        ROOT / ".aider.conf.yml",
        ROOT / ".claude" / "settings.local.json",
        ROOT / ".claude" / "agents" / "tgw-coder.md",
        ROOT / "etc" / "interfaces" / "claude" / "project-settings.local.json",
        ROOT / "scripts" / "requeue_deadletter.py",
        ROOT / "config" / "environment" / "registry.yaml",
        ROOT / "config" / "environment" / "tasks" / "environment-recovery.json",
        ROOT / "config" / "environment" / "resolved" / "environment-recovery.codex.json",
    )
    legacy = "docs/TGW-Plan-Vault/plan/"
    offenders = [
        str(path.relative_to(ROOT)) for path in active_files
        if legacy in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
    assert not (ROOT / "docs" / "TGW-Plan-Vault").exists()


def test_claude_project_surfaces_cannot_shadow_signed_fleet_instructions():
    redirect = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert redirect.count("\n") <= 4
    assert "/home/claude/.claude/CLAUDE.md" in redirect
    assert "docs/TGW-Plan-Vault" not in redirect
    assert "SessionStart" not in (ROOT / ".claude/settings.json").read_text(
        encoding="utf-8"
    )
    for skill in (
        "tgw-plan",
        "tgw-plan-maintain",
        "tgw-packet",
        "tgw-pr-review",
        "tgw-runner-review",
        "tgw-exit",
        "tgw-mailbox-send",
    ):
        assert not (ROOT / ".claude/skills" / skill / "SKILL.md").exists()
