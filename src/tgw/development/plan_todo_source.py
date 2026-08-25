"""Read one canonical Todo projection from the standalone Plan Taskboard.

The development database is an independent local execution store.  A selected
Todo may therefore need to be projected from the committed, generated Plan
Taskboard before it can be bound to a worktree.  This adapter is deliberately
read-only with respect to the Plan repository and never contacts tgw-prod.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


class PlanTodoSourceError(ValueError):
    """The selected Todo cannot be proven from current standalone Plan source."""


_ACTOR = re.compile(r"^## ([a-z][a-z0-9_-]*) \([0-9]+ open\)$")
_PP = re.compile(r"PP-[A-Z0-9][A-Z0-9_-]*")
_BLOCKER = re.compile(r"#([0-9]+)")


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={repository.resolve()}", *args],
        cwd=repository,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise PlanTodoSourceError(f"standalone Plan Git read failed: {result.stderr[-500:]}")
    return result.stdout


def _cells(line: str) -> list[str]:
    """Split a generated Markdown row without splitting escaped link pipes."""
    value = line.strip()
    if not value.startswith("|") or not value.endswith("|"):
        return []
    cells = re.split(r"(?<!\\)\|", value[1:-1])
    return [cell.strip().replace(r"\|", "|") for cell in cells]


def parse_taskboard(text: str, todo_id: int) -> dict[str, Any]:
    """Return one open Todo from generated Taskboard bytes."""
    if not text.startswith("# TGW Taskboard\n") or "GENERATED FILE — DO NOT EDIT" not in text[:500]:
        raise PlanTodoSourceError("standalone Plan Taskboard is not the generated Todo projection")
    actor: str | None = None
    for line in text.splitlines():
        heading = _ACTOR.fullmatch(line)
        if heading:
            actor = heading.group(1)
            continue
        cells = _cells(line)
        if len(cells) != 6 or not cells[0].isdigit() or int(cells[0]) != todo_id:
            continue
        if actor is None or not cells[1].isdigit() or not cells[3]:
            raise PlanTodoSourceError(f"Todo {todo_id} has malformed Taskboard metadata")
        pp_refs = list(dict.fromkeys(_PP.findall(cells[4])))
        blockers = [int(value) for value in _BLOCKER.findall(cells[5])]
        return {
            "id": todo_id,
            "agent": actor,
            "priority": int(cells[1]),
            "body": cells[3],
            "pp_ref": pp_refs[0] if len(pp_refs) == 1 else None,
            "depends_on": blockers,
            "plan_anchor": None,
            "reasoning": "normal",
        }
    raise PlanTodoSourceError(f"Todo {todo_id} is absent from the current standalone Plan Taskboard")


def resolve(
    todo_id: int,
    *,
    repository: Path | str,
    approved_commit: str,
    taskboard_path: str = "plan/TGW-Taskboard.md",
) -> dict[str, Any]:
    """Resolve one Todo from exact current Plan Git bytes and provenance."""
    root = Path(repository).resolve()
    if not root.is_dir() or not re.fullmatch(r"[0-9a-f]{40}", approved_commit):
        raise PlanTodoSourceError("standalone Plan repository or approved commit is invalid")
    head = _git(root, "rev-parse", "HEAD").strip()
    ancestor = subprocess.run(
        [
            "git", "-c", f"safe.directory={root}", "merge-base", "--is-ancestor",
            approved_commit, head,
        ],
        cwd=root,
        check=False,
        text=True,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise PlanTodoSourceError("current standalone Plan does not descend from the approved Plan")
    blob = _git(root, "rev-parse", f"{head}:{taskboard_path}").strip()
    taskboard = _git(root, "show", f"{head}:{taskboard_path}")
    item = parse_taskboard(taskboard, todo_id)
    item.update(
        {
            "source": f"standalone-plan-taskboard@{head}",
            "plan_repository": str(root),
            "plan_evidence_commit": head,
            "taskboard_path": taskboard_path,
            "taskboard_blob": blob,
        }
    )
    return item
