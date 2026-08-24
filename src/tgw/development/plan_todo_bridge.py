"""Bind one dispatchable Plan leaf to a canonical coding Todo.

This adapter deliberately creates no queue job.  ``tgw.development.foreman``
remains the only dispatcher of coding treatments.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Mapping

from tgw.development.foreman import TodoRecord
from tgw.workflow.plan_bridge import CompiledPlanRuntime


class PlanTodoBridgeError(ValueError):
    pass


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _key(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def bind_leaf(
    compiled: CompiledPlanRuntime, *, solution: Mapping[str, Any], treatment_id: str,
    source_commit: str, worktree_identity: str, agent: str, body: str, priority: int,
    create_todo: Callable[[str, str, int, str, str | None, str | None], Mapping[str, Any]],
    list_todos: Callable[[], list[Mapping[str, Any]]],
    allocate_worktree: Callable[[int, str, str], Mapping[str, Any]],
    set_status_note: Callable[[int, str], Any],
) -> dict[str, Any]:
    """Create/retrieve a Todo and its existing request-bound worktree.

    Callers inject the tracker and the existing coding-worker allocator so this
    module cannot dispatch or choose a queue itself.
    """
    if not compiled.dispatchable or compiled.holds:
        raise PlanTodoBridgeError("Plan solution is held or non-conformant")
    if solution.get("plan_commit") != compiled.plan_commit or solution.get("solution_hash") != compiled.solution_hash:
        raise PlanTodoBridgeError("Plan solution does not match compiled runtime")
    treatment = next((item for item in compiled.treatments if item.identity == treatment_id), None)
    if treatment is None or treatment_id not in {item.treatment_id for item in compiled.runtime_graph.eligible_treatments}:
        raise PlanTodoBridgeError("Plan leaf is not eligible")
    capability = treatment.ownership[0]
    binding = {
        "schema": "tgw-plan-coding-todo/v1", "plan_commit": compiled.plan_commit,
        "solution_hash": compiled.solution_hash, "closure_hash": compiled.closure_hash,
        "capability": capability, "treatment_id": treatment_id, "source_commit": source_commit,
        "requested_worktree_identity": worktree_identity,
    }
    binding["idempotency_key"] = _key(binding)
    supersedes_todo_id = None
    for row in list_todos():
        note = row.get("status_note")
        if isinstance(note, str):
            try:
                prior = json.loads(note)
            except ValueError:
                continue
            if prior.get("idempotency_key") == binding["idempotency_key"]:
                return {"todo_id": row["id"], "binding": prior, "created": False}
            if all(prior.get(field) == binding[field] for field in ("plan_commit", "solution_hash", "capability", "treatment_id", "source_commit")):
                supersedes_todo_id = row.get("id")
    created = create_todo(agent, body, priority, "plan-luet-bridge", "PLAN-GOVERNED-EXECUTION-PLATFORM", treatment_id)
    todo_id = created.get("id")
    if not isinstance(todo_id, int) or todo_id <= 0:
        raise PlanTodoBridgeError("Todo tracker did not return an id")
    request_id = "plan-" + binding["idempotency_key"][7:31]
    location = dict(allocate_worktree(todo_id, request_id, source_commit))
    worktree = location.get("worktree")
    if not isinstance(worktree, str) or not worktree:
        raise PlanTodoBridgeError("existing allocator did not return a worktree")
    binding["worktree"] = worktree
    binding["worktree_identity"] = location
    if isinstance(supersedes_todo_id, int):
        binding["supersedes_todo_id"] = supersedes_todo_id
    set_status_note(todo_id, _canonical(binding))
    return {"todo_id": todo_id, "binding": binding, "created": True}
