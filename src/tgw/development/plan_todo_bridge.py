"""Bind one dispatchable Plan leaf to a canonical coding Todo.

This adapter deliberately creates no queue job.  ``tgw.development.foreman``
remains the only dispatcher of coding treatments.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any, Mapping

from tgw.development.plan_binding import (
    EXECUTION_ROOT_SCHEMA,
    MalformedPlanBindingError,
    execution_root_hash,
    validate_execution_root,
)
from tgw.plan_solver import PlanResolutionError, validate_solution_integrity
from tgw.plan_execution_resources import PlanExecutionResourceError, validate_execution_envelope
from tgw.workflow.plan_bridge import CompiledPlanRuntime


class PlanTodoBridgeError(ValueError):
    pass


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _key(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _selected_execution_root(
    compiled: CompiledPlanRuntime, solution: Mapping[str, Any], supplied: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Bind one explicit W-series execution root without making Todo authoritative."""
    raw_solution_root = solution.get("root")
    if not isinstance(raw_solution_root, Mapping):
        raise PlanTodoBridgeError("Plan solution lacks a typed root")
    if supplied is None:
        root: dict[str, Any] = {
            "schema": EXECUTION_ROOT_SCHEMA,
            "kind": "plan",
            "plan_id": raw_solution_root.get("id"),
            "profile": raw_solution_root.get("profile"),
            "plan_commit": compiled.plan_commit,
        }
    else:
        root = dict(supplied)
    if "identity_hash" not in root:
        root["identity_hash"] = execution_root_hash(root)
    try:
        root = validate_execution_root(root)
    except MalformedPlanBindingError as exc:
        raise PlanTodoBridgeError(str(exc)) from exc
    if root["kind"] == "plan":
        if (
            root["plan_id"] != raw_solution_root.get("id")
            or root["profile"] != raw_solution_root.get("profile")
            or root["plan_commit"] != compiled.plan_commit
        ):
            raise PlanTodoBridgeError("selected Plan root does not match resolved solution")
    elif root["kind"] == "pp":
        # A PP-rooted resolution must name the PP as its solved root; do not
        # substitute a convenient Plan identifier or historical pseudo-PP.
        if raw_solution_root.get("id") != root["pp_ref"]:
            raise PlanTodoBridgeError("selected PP root does not match resolved solution")
    return root


def bind_leaf(
    compiled: CompiledPlanRuntime, *, solution: Mapping[str, Any], treatment_id: str,
    source_commit: str, worktree_identity: str, agent: str, execution_envelope: Mapping[str, Any],
    create_todo: Callable[[str, str, int, str, str | None, str | None], Mapping[str, Any]],
    list_todos: Callable[[], list[Mapping[str, Any]]],
    allocate_worktree: Callable[[int, str, str], Mapping[str, Any]],
    set_status_note: Callable[[int, str], Any],
    fixture_run_id: str | None = None,
    execution_root: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create/retrieve a Todo and its existing request-bound worktree.

    Callers inject the tracker and the existing coding-worker allocator so this
    module cannot dispatch or choose a queue itself.
    """
    if not compiled.dispatchable or compiled.holds:
        raise PlanTodoBridgeError("Plan solution is held or non-conformant")
    try:
        envelope = validate_execution_envelope(execution_envelope, compiled=compiled)
    except PlanExecutionResourceError as exc:
        raise PlanTodoBridgeError("Plan execution envelope is invalid") from exc
    card = envelope["card"]
    if card["work_unit"]["treatment_id"] != treatment_id or card["source"]["commit"] != source_commit:
        raise PlanTodoBridgeError("Plan execution card does not match Todo binding")
    try:
        validate_solution_integrity(solution, current_plan_commit=compiled.plan_commit)
    except PlanResolutionError as exc:
        raise PlanTodoBridgeError("Plan solution integrity check failed") from exc
    if solution.get("plan_commit") != compiled.plan_commit or solution.get("solution_hash") != compiled.solution_hash:
        raise PlanTodoBridgeError("Plan solution does not match compiled runtime")
    treatment = next((item for item in compiled.treatments if item.identity == treatment_id), None)
    if treatment is None or treatment_id not in {item.treatment_id for item in compiled.runtime_graph.eligible_treatments}:
        raise PlanTodoBridgeError("Plan leaf is not eligible")
    capability = treatment.ownership[0]
    root = _selected_execution_root(compiled, solution, execution_root)
    if card["plan"].get("root") != root:
        raise PlanTodoBridgeError("Plan execution card root does not match Todo binding")
    binding = {
        "schema": "tgw-plan-coding-todo/v1", "plan_commit": compiled.plan_commit,
        "solution_hash": compiled.solution_hash, "closure_hash": compiled.closure_hash,
        "capability": capability, "treatment_id": treatment_id, "source_commit": source_commit,
        "requested_worktree_identity": worktree_identity,
        "execution_card": card,
        "execution_envelope": envelope,
        "execution_root": root,
    }
    if fixture_run_id is not None:
        binding["fixture_run_id"] = fixture_run_id
    binding["idempotency_key"] = _key(binding)
    supersedes_todo_id = None
    rows = list_todos()
    selected_todo: Mapping[str, Any] | None = None
    if root["kind"] == "todo":
        selected_todo = next((row for row in rows if row.get("id") == root["todo_id"]), None)
        if selected_todo is None:
            raise PlanTodoBridgeError("selected Todo root does not exist in the canonical Todo adapter")
    for row in rows:
        note = row.get("status_note")
        if isinstance(note, str):
            try:
                prior = json.loads(note)
            except ValueError:
                continue
            if prior.get("idempotency_key") == binding["idempotency_key"]:
                return {"todo_id": row["id"], "binding": prior, "created": False}
            prior_root = prior.get("execution_root")
            if (
                isinstance(prior_root, Mapping)
                and prior_root.get("identity_hash") == root["identity_hash"]
                and all(prior.get(field) == binding[field] for field in ("plan_commit", "solution_hash", "capability", "treatment_id", "source_commit"))
            ):
                if root["kind"] == "todo" and row.get("id") == root["todo_id"]:
                    raise PlanTodoBridgeError(
                        "a changed Todo-root binding requires a new selected canonical Todo"
                    )
                supersedes_todo_id = row.get("id")
    if selected_todo is None:
        # A Plan root creates a derived implementation Todo with no invented
        # PP reference.  A PP root preserves its actual selected identifier.
        created = create_todo(
            agent, card["task"]["body"], card["scheduling"]["transport_priority"], "plan-luet-bridge",
            root["pp_ref"] if root["kind"] == "pp" else None,
            treatment_id,
        )
        todo_id = created.get("id")
        created_new = True
    else:
        todo_id = selected_todo.get("id")
        created_new = False
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
    return {"todo_id": todo_id, "binding": binding, "created": created_new}
