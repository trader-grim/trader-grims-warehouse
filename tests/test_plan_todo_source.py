from pathlib import Path

import pytest

from tgw.development.plan_todo_source import PlanTodoSourceError, parse_taskboard, resolve

TASKBOARD = """# TGW Taskboard

> **GENERATED FILE — DO NOT EDIT.** Rebuilt from the `todo_items` table.

## claude (1 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 1732 | 3 |  | Build the CLI. | [[TGW-Master-Plan#PP-WORKFLOW-001 — workflow\\|PP-WORKFLOW-001]] | ⛔ #1729 |
"""


def test_parse_generated_taskboard_preserves_todo_identity():
    item = parse_taskboard(TASKBOARD, 1732)

    assert item == {
        "id": 1732,
        "agent": "claude",
        "priority": 3,
        "body": "Build the CLI.",
        "pp_ref": "PP-WORKFLOW-001",
        "depends_on": [1729],
        "plan_anchor": None,
        "reasoning": "normal",
    }


def test_parse_taskboard_refuses_non_generated_or_missing_todo():
    with pytest.raises(PlanTodoSourceError, match="generated Todo projection"):
        parse_taskboard("# notes\n", 1732)
    with pytest.raises(PlanTodoSourceError, match="absent"):
        parse_taskboard(TASKBOARD, 99)


def test_resolve_reads_exact_descendant_commit(tmp_path: Path):
    import subprocess

    repository = tmp_path / "plan"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repository, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repository, check=True)
    taskboard = repository / "plan/TGW-Taskboard.md"
    taskboard.parent.mkdir()
    taskboard.write_text(TASKBOARD, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "plan"], cwd=repository, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
        text=True, capture_output=True,
    ).stdout.strip()

    item = resolve(1732, repository=repository, approved_commit=head)

    assert item["plan_evidence_commit"] == head
    assert item["source"] == f"standalone-plan-taskboard@{head}"
    assert len(item["taskboard_blob"]) == 40
