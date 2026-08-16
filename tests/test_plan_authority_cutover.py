"""Regression checks for the standalone approved-Plan authority cutover."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
VAULT = ROOT / "docs" / "TGW-Plan-Vault"
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
    """Archive/inbox history is intentionally out of this scan and retained."""
    active_files = (
        ROOT / "AGENTS.md",
        ROOT / ".aider.conf.yml",
        ROOT / "scripts" / "requeue_deadletter.py",
        VAULT / "reference" / "TGW-Architecture-Overview.md",
        VAULT / ".obsidian" / "workspace.json",
        VAULT / ".obsidian" / "workspace-mobile.json",
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
    assert "plan/TGW-Master-Plan" not in (
        VAULT / ".obsidian" / "workspace.json"
    ).read_text(encoding="utf-8")
    assert "plan/TGW-Master-Plan" not in (
        VAULT / ".obsidian" / "workspace-mobile.json"
    ).read_text(encoding="utf-8")
    assert (VAULT / "reference" / "PLAN-AUTHORITY-CUTOVER.md").is_file()
    # Historical evidence stays available but cannot be selected as authority.
    assert (VAULT / "inbox").is_dir()
    assert (VAULT / "plan" / "archive").is_dir()
