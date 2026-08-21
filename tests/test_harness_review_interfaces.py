from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "agent-services/skills/tgw-review/SKILL.md"
INSTALLER = ROOT / "scripts/install_shared_harness_skills.py"


def test_review_skill_is_provider_neutral_and_current():
    text = SKILL.read_text(encoding="utf-8")
    assert "tgw_context_bundle" in text
    assert "NON_ADMITTING_DIAGNOSTIC" in text
    assert "tgw-code-review/v1" in text
    assert "Skill availability does not qualify a harness" in text
    assert "Never merge, publish, install, deploy" in text
    for stale in ("docs/TGW-Plan-Vault", "main...HEAD", "2 fix attempts", "human/Claude stitch"):
        assert stale not in text


def test_recovery_reference_binds_both_historical_skills_without_static_current_plan():
    text = (SKILL.parent / "references/recovered-contracts.md").read_text()
    assert "tgw-pr-review" in text
    assert "tgw-runner-review" in text
    assert "9a267214fd4efec051af38173ac9b3cfc50a4fd8" in text
    assert "f59f653b2922b66ea0523c3abc28a68e0bf0156d" in text
    assert "hard-coded historical" in text
    assert "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99" not in text


def test_shared_skill_installer_is_idempotent_and_refuses_copies(tmp_path: Path):
    source = tmp_path / "source"
    for skill in ("tgw-plan", "tgw-review"):
        (source / skill).mkdir(parents=True)
        (source / skill / "SKILL.md").write_text(f"---\nname: {skill}\n---\n")
    home = tmp_path / "home"
    home.mkdir()
    command = [
        sys.executable, str(INSTALLER), "--harness", "claude",
        "--home", str(home), "--source-root", str(source),
    ]
    first = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    second = json.loads(subprocess.run(command, check=True, capture_output=True, text=True).stdout)
    assert [item["status"] for item in first["skills"]] == ["installed", "installed"]
    assert [item["status"] for item in second["skills"]] == ["current", "current"]
    assert (home / ".claude/skills/tgw-review").resolve() == source / "tgw-review"
    (home / ".claude/skills/tgw-review").unlink()
    (home / ".claude/skills/tgw-review").write_text("preserve me")
    failed = subprocess.run(command, capture_output=True, text=True)
    assert failed.returncode != 0
    assert "refusing to replace non-link" in failed.stderr


def test_claude_mcp_config_uses_current_context_and_omits_legacy_aider():
    value = json.loads((ROOT / "etc/interfaces/claude/mcp-servers.json").read_text())
    assert set(value["mcpServers"]) == {"tgw", "tgw-context"}
    assert value["mcpServers"]["tgw"] == {
        "type": "sse", "url": "http://100.107.99.66:8765/sse"
    }
    context = value["mcpServers"]["tgw-context"]
    assert context["type"] == "stdio"
    assert context["args"] == ["-m", "tgw.context_mcp_server"]
    assert context["command"] == "<context-mcp-command>"
    assert context["env"]["TGW_CONTEXT_PLAN_COMMIT"] == "<approved-plan-commit>"
    assert context["env"]["TGW_CONTEXT_PLAN_SOLUTION"] == (
        "sha256:<approved-solution-hash>"
    )
    assert context["env"]["TGW_CONTEXT_SOURCE_ROOT"] == "<exact-source-root>"
    assert context["env"]["TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH"] == (
        "sha256:<verified-catalog-hash>"
    )


def test_installation_catalog_does_not_confuse_skill_and_provider_status():
    value = json.loads(
        (ROOT / "etc/interfaces/harness-review-installations.json").read_text()
    )
    assert value["evidence_state"] == "historical-observation-not-live-proof"
    harnesses = {item["id"]: item for item in value["harnesses"]}
    assert harnesses["codex"]["automated_provider"] == "codex-isolated-review-runner"
    assert harnesses["claude"]["interactive_review"] == "available"
    assert harnesses["claude"]["model_authentication"] == "configured"
    assert harnesses["claude"]["automated_provider"] == (
        "governed-review-selected-hold-not-installed"
    )
    assert harnesses["claude"]["governed_execution_adapter"] == (
        "implemented-not-installed"
    )
    assert harnesses["claude"]["protected_review_projection"] == "not-installed"
    assert harnesses["claude"]["context_bundle_service"] == "not-installed"
    assert harnesses["claude"]["review_evidence_sink"] == "not-installed"
    assert harnesses["claude"]["review_network_mode"] == "shared-host-network"
    assert harnesses["claude"]["governed_runtime_identity"] == {
        "uid": 1006, "gid": 1006,
    }
    assert harnesses["claude"]["live_readiness"] == "HOLD_NOT_INSTALLED"
    assert harnesses["hermes"]["automated_provider"] == "unregistered"
    assert harnesses["hermes"]["context_mcp"] == "configured"
    assert harnesses["hermes"]["production_inventory_mcp"] == (
        "hold-legacy-sse-incompatible"
    )
    assert harnesses["tigwadev-claude-recovery"]["context_mcp"] == (
        "configured-for-codex-and-claude"
    )
    assert harnesses["aider"]["interactive_review"] == "unavailable-not-installed"


def test_runbook_does_not_claim_hermes_legacy_sse_compatibility():
    text = (ROOT / "docs/runbooks/harness-review-and-context-v1-20260815.md").read_text()
    assert "legacy SSE endpoint" in text
    assert "405" in text
    assert "treat production inventory access as HOLD" in text
    assert "unless a current catalog-" in text
    assert "bound probe proves" in text
    assert "ad hoc proxy" in text
