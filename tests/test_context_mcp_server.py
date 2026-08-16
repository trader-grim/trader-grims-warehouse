from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tgw import context_mcp_server as context


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Context Test", "-c", "user.email=context@example.invalid", "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def bound_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    plan_repository = tmp_path / "plans"
    for path, content in {
        "plan/SPEC-plan-capability-graph-v2.md": "# Plan v2\n",
        "plan/TGW-Master-Plan.md": "# Master Plan\n",
        "plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml": "schema: fixture\n",
        "plan/pp/PP-CONTEXT-001.md": "# PP\n",
        "reference/context.md": "# Context\n",
    }.items():
        target = plan_repository / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    _git(plan_repository, "init", "-q")
    approved = _commit(plan_repository, "approved")
    materialization = tmp_path / "approved"
    _git(plan_repository, "worktree", "add", "--detach", str(materialization), approved)
    (plan_repository / "evidence.md").write_text("later evidence\n")
    evidence_head = _commit(plan_repository, "evidence")

    source = tmp_path / "source"
    (source / "src" / "fixture").mkdir(parents=True)
    (source / "src" / "fixture" / "provider.py").write_text("class ContextProvider:\n    pass\n")
    (source / "docs" / "runbooks").mkdir(parents=True)
    (source / "docs" / "runbooks" / "context.md").write_text("# Context\n\nCommitted runbook.\n")
    _git(source, "init", "-q")
    source_commit = _commit(source, "source")

    monkeypatch.setenv("TGW_CONTEXT_PLAN_ROOT", str(materialization))
    monkeypatch.setenv("TGW_CONTEXT_PLAN_REPOSITORY", str(plan_repository))
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", approved)
    monkeypatch.setenv("TGW_CONTEXT_PLAN_SOLUTION", "sha256:" + "a" * 64)
    monkeypatch.setenv("TGW_CONTEXT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("TGW_CONTEXT_RUNTIME_ROOT", str(tmp_path / "runtime"))
    context._code_snapshot.cache_clear()
    return {"approved": approved, "evidence_head": evidence_head, "source": source, "source_commit": source_commit}


def test_status_binds_approved_plan_evidence_and_committed_source(bound_context):
    status = context.context_status()
    assert status["plan"]["approved_commit"] == bound_context["approved"]
    assert status["plan"]["approved_solution_hash"] == "sha256:" + "a" * 64
    assert status["plan"]["evidence_head"] == bound_context["evidence_head"]
    assert status["source"]["commit"] == bound_context["source_commit"]
    assert status["code_graph"]["commit"] == bound_context["source_commit"]
    assert status["scope_semantics"]["platform_w11_completion_implies_master_plan_completion"] is False


def test_committed_plan_runbook_and_codegraph_ignore_dirty_bytes(bound_context):
    source = bound_context["source"]
    assert isinstance(source, Path)
    (source / "src" / "fixture" / "provider.py").write_text("broken python !!!\n")
    (source / "docs" / "runbooks" / "context.md").write_text("dirty bytes\n")
    assert context.code_graph("symbols", "ContextProvider")["result"][0]["name"] == "ContextProvider"
    assert "Committed runbook" in context.runbooks(path="docs/runbooks/context.md")["content"]
    assert "dirty bytes" not in context.runbooks(path="docs/runbooks/context.md")["content"]
    assert context.context_status()["source"]["working_tree_clean"] is False


def test_fail_closed_path_and_materialization_checks_and_read_only_surface(bound_context, monkeypatch):
    with pytest.raises(context.ContextError, match="outside"):
        context.source_chunk("docs/runbooks/context.md")
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", str(bound_context["evidence_head"]))
    with pytest.raises(context.ContextError, match="does not match"):
        context.context_status()
    tools = set(context.mcp._tool_manager._tools)
    assert tools == {
        "tgw_context_status", "tgw_context_bundle", "tgw_context_plan_graph",
        "tgw_context_plan_source", "tgw_context_runbooks", "tgw_context_code_graph",
    }
    payload = json.loads(context.tgw_context_status())
    assert payload["ok"] is False
