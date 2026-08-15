from __future__ import annotations

import json
from pathlib import Path

from tgw.environment_registry import load_registry
from tgw.instruction_audit import audit_instructions

ROOT = Path(__file__).parents[1]


def test_obsolete_profile_is_a_toolless_fail_closed_tombstone():
    text = (ROOT / ".claude/agents/nix-flake-maintainer.md").read_text()
    assert "tgw-instruction-tombstone/v1" in text
    assert 'tools: ""' in text
    assert "RETIRED_PROFILE" in text
    assert "tools: Bash" not in text


def test_current_claude_routes_do_not_operate_retired_host_or_profile():
    claude = (ROOT / "CLAUDE.md").read_text()
    settings = json.loads((ROOT / ".claude/settings.json").read_text())
    rendered_settings = json.dumps(settings, sort_keys=True)
    for forbidden in ("ssh a1131", "wakeonlan", "nix-flake-maintainer"):
        assert forbidden not in claude
    assert "a1131" not in rendered_settings
    assert "nix-flake-maintainer" not in rendered_settings
    assert "Historical only" in claude


def test_fresh_audit_no_longer_reports_live_obsolete_profile():
    result = audit_instructions(
        ROOT,
        load_registry(ROOT / "config/environment/registry.yaml"),
        observed_at="2026-08-11T09:15:00-07:00",
    )
    assert not any(item["code"] == "obsolete-maintainer-profile-present" for item in result["findings"])
    hermes = next(
        item for item in result["sources"]
        if item["path"] == "config/environment/actors/tgw-steward.json"
    )
    assert hermes["scopes"] == ["authority:hermes-tigwa"]
