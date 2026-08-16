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
    subprocess.run(
        [
            "git", "-C", str(root), "-c", "user.name=Context Test",
            "-c", "user.email=context@example.invalid", "commit", "-qm", message,
        ],
        check=True,
    )
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def bound_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path | str]:
    plan_repo = tmp_path / "plans"
    (plan_repo / "plan" / "execution").mkdir(parents=True)
    (plan_repo / "plan" / "pp").mkdir()
    (plan_repo / "reference").mkdir()
    (plan_repo / "plan" / "SPEC-plan-capability-graph-v2.md").write_text(
        "# Plan v2\n\nA Todo does not complete its parent Plan.\n"
    )
    (plan_repo / "plan" / "TGW-Master-Plan.md").write_text(
        "# Master Plan\n\n## PP-CONTEXT-001\nContext service.\n"
    )
    (plan_repo / "plan" / "pp" / "PP-CONTEXT-001.md").write_text(
        "# PP-CONTEXT-001\n\nProvide Plan and CodeGraph context.\n"
    )
    (plan_repo / "plan" / "execution" / "GOVERNED-EXECUTION-PLATFORM-v1.yaml").write_text(
        "schema: fixture\nwork_units:\n  - id: W11\n"
    )
    (plan_repo / "reference" / "context.md").write_text("# Context\n\nExact sources.\n")
    _git(plan_repo, "init", "-q")
    approved = _commit(plan_repo, "approved plan")
    approved_root = tmp_path / "approved"
    _git(plan_repo, "worktree", "add", "--detach", str(approved_root), approved)
    (plan_repo / "evidence.md").write_text("later evidence\n")
    evidence_head = _commit(plan_repo, "later evidence")

    source = tmp_path / "source"
    (source / "src" / "pkg").mkdir(parents=True)
    (source / "src" / "pkg" / "core.py").write_text(
        "class ContextProvider:\n    pass\n"
    )
    (source / "docs" / "runbooks").mkdir(parents=True)
    (source / "docs" / "runbooks" / "context-v1.md").write_text(
        "# Context runbook\n\nPlan Graph and CodeGraph stay distinct.\n"
    )
    _git(source, "init", "-q")
    source_commit = _commit(source, "source")

    runtime = tmp_path / "runtime"
    monkeypatch.setenv("TGW_CONTEXT_PLAN_ROOT", str(approved_root))
    monkeypatch.setenv("TGW_CONTEXT_PLAN_REPOSITORY", str(plan_repo))
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", approved)
    monkeypatch.setenv("TGW_CONTEXT_SOURCE_ROOT", str(source))
    monkeypatch.setenv("TGW_CONTEXT_RUNTIME_ROOT", str(runtime))
    context._code_snapshot.cache_clear()
    return {
        "plan_repo": plan_repo,
        "approved_root": approved_root,
        "approved": approved,
        "evidence_head": evidence_head,
        "source": source,
        "source_commit": source_commit,
        "runtime": runtime,
    }


def test_status_separates_approved_plan_evidence_head_and_master_scope(bound_context):
    result = context.context_status()
    assert result["plan"]["approved_commit"] == bound_context["approved"]
    assert result["plan"]["evidence_head"] == bound_context["evidence_head"]
    assert result["source"]["commit"] == bound_context["source_commit"]
    assert result["code_graph"]["commit"] == bound_context["source_commit"]
    assert result["scope_semantics"]["platform_w11_completion_implies_master_plan_completion"] is False
    assert result["scope_semantics"]["narrow_plan_pp_or_todo_completion_implies_parent_completion"] is False
    assert result["context_sha256"].startswith("sha256:")


def test_task_bundle_uses_approved_plan_and_committed_runbook(bound_context):
    bundle = context.context_bundle("PP-CONTEXT-001 Plan Graph CodeGraph")
    assert bundle["plan_graph"]["plan_commit"] == bound_context["approved"]
    assert bundle["code_graph"]["binding"]["commit"] == bound_context["source_commit"]
    assert bundle["runbooks"]["matches"][0]["path"] == "docs/runbooks/context-v1.md"
    assert "Never describe platform W11" in bundle["instructions"][-1]
    assert (bound_context["runtime"] / "tgw-plan-graph").is_dir()


def test_codegraph_and_runbooks_ignore_dirty_source_bytes(bound_context):
    source = bound_context["source"]
    (source / "src" / "pkg" / "core.py").write_text("broken python !!!\n")
    (source / "docs" / "runbooks" / "context-v1.md").write_text("dirty replacement\n")
    graph = context.code_graph("symbols", "ContextProvider", 10)
    assert graph["result"][0]["name"] == "ContextProvider"
    runbook = context.runbooks(path="docs/runbooks/context-v1.md")
    assert "Plan Graph and CodeGraph stay distinct" in runbook["content"]
    assert "dirty replacement" not in runbook["content"]
    assert context.context_status()["source"]["working_tree_clean"] is False


def test_plan_chunks_are_bounded_and_path_safe(bound_context):
    result = context.source_chunk("plan/TGW-Master-Plan.md", 1, 2)
    assert result["commit"] == bound_context["approved"]
    assert result["content"].startswith("# Master Plan")
    with pytest.raises(context.ContextError, match="outside"):
        context.source_chunk("docs/runbooks/context-v1.md")
    with pytest.raises(context.ContextError, match="canonical"):
        context.runbooks(path="docs/runbooks/../secrets.md")


def test_wrong_approved_materialization_fails_closed(bound_context, monkeypatch):
    monkeypatch.setenv("TGW_CONTEXT_PLAN_COMMIT", str(bound_context["evidence_head"]))
    with pytest.raises(context.ContextError, match="does not match"):
        context.context_status()


def test_mcp_surface_is_read_only_and_complete(bound_context):
    tools = set(context.mcp._tool_manager._tools)
    assert tools == {
        "tgw_context_status",
        "tgw_context_bundle",
        "tgw_context_plan_graph",
        "tgw_context_plan_source",
        "tgw_context_runbooks",
        "tgw_context_code_graph",
    }
    payload = json.loads(context.tgw_context_status())
    assert payload["ok"] is True
    assert not any("enqueue" in name or "write" in name for name in tools)
