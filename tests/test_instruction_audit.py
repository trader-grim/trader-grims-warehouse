from __future__ import annotations

from pathlib import Path

import pytest

from tgw.environment_registry import load_registry
from tgw.instruction_audit import InstructionAuditError, audit_instructions, persist_audit

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "config/environment/registry.yaml"


def test_real_instruction_inventory_is_hashed_classified_and_inert():
    result = audit_instructions(ROOT, load_registry(REGISTRY), observed_at="2026-08-11T09:05:00-07:00")
    paths = {item["path"] for item in result["sources"]}
    assert {"AGENTS.md", "CLAUDE.md", ".claude/agents/nix-flake-maintainer.md"} <= paths
    assert "docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md" in paths
    assert all(item["sha256"].startswith("sha256:") for item in result["sources"])
    assert result["commands_executed_from_sources"] is False
    assert result["source_files_modified"] is False
    assert any(item["code"] == "retired-host-reference" for item in result["findings"])
    assert not any(item["code"] == "obsolete-maintainer-profile-present" for item in result["findings"])


def test_retired_reference_and_deploy_command_are_line_bound(tmp_path):
    registry = load_registry(REGISTRY)
    (tmp_path / ".claude/agents").mkdir(parents=True)
    (tmp_path / "config/environment/actors").mkdir(parents=True)
    (tmp_path / "docs/TGW-Plan-Vault/plan/pp").mkdir(parents=True)
    (tmp_path / "docs/TGW-Plan-Vault/reference/runbooks").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("shared\n", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("Use a1131\n", encoding="utf-8")
    (tmp_path / ".claude/agents/nix-flake-maintainer.md").write_text("old\n", encoding="utf-8")
    (tmp_path / "config/environment/actors/tgw-steward.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md").write_text("history is not authority\n", encoding="utf-8")
    (tmp_path / "docs/TGW-Plan-Vault/reference/runbooks/deploy.md").write_text("nixos-rebuild switch\n", encoding="utf-8")
    result = audit_instructions(tmp_path, registry, observed_at="2026-08-11T09:05:00-07:00")
    codes = {item["code"] for item in result["findings"]}
    assert {"retired-host-reference", "direct-mutable-deploy-command"} <= codes


def test_missing_or_symlinked_registered_source_fails_closed(tmp_path):
    registry = load_registry(REGISTRY)
    with pytest.raises(InstructionAuditError):
        audit_instructions(tmp_path, registry, observed_at="2026-08-11T09:05:00-07:00")
    (tmp_path / "outside").write_text("x", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(tmp_path / "outside")
    with pytest.raises(InstructionAuditError):
        audit_instructions(tmp_path, registry, observed_at="2026-08-11T09:05:00-07:00")


def test_audit_artifact_is_immutable(tmp_path):
    artifact = {"schema": "tgw-instruction-audit/v1", "finding_count": 0}
    path = persist_audit(tmp_path / "audit.json", artifact)
    assert __import__("json").loads(path.read_text()) == artifact
    with pytest.raises(FileExistsError):
        persist_audit(path, artifact)
