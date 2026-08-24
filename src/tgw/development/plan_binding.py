"""Validation for Plan-bound coding Todo metadata."""

from __future__ import annotations

import json
import hashlib
import re
from typing import Any, Mapping


PLAN_BINDING_SCHEMA = "tgw-plan-coding-todo/v1"
EXECUTION_ROOT_SCHEMA = "tgw-execution-root/v1"
_REQUIRED_STRINGS = frozenset({
    "plan_commit", "solution_hash", "closure_hash", "capability",
    "treatment_id", "idempotency_key", "worktree",
})


class MalformedPlanBindingError(ValueError):
    """A Todo identifies itself as Plan-bound but cannot be executed safely."""


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def execution_root_hash(value: Mapping[str, Any]) -> str:
    """Return the content address of one typed execution-root identity."""
    unsigned = dict(value)
    unsigned.pop("identity_hash", None)
    return "sha256:" + hashlib.sha256(_canonical(unsigned).encode()).hexdigest()


def validate_execution_root(value: object) -> dict[str, Any]:
    """Validate the selected Plan, PP, or canonical Todo execution root."""
    if not isinstance(value, Mapping) or value.get("schema") != EXECUTION_ROOT_SCHEMA:
        raise MalformedPlanBindingError("Plan binding has malformed execution root")
    root = dict(value)
    kind = root.get("kind")
    if kind == "plan":
        allowed = {"schema", "kind", "plan_id", "profile", "plan_commit", "identity_hash"}
        valid = (
            isinstance(root.get("plan_id"), str) and bool(root["plan_id"])
            and isinstance(root.get("profile"), str) and bool(root["profile"])
            and isinstance(root.get("plan_commit"), str) and bool(root["plan_commit"])
        )
    elif kind == "pp":
        allowed = {"schema", "kind", "pp_ref", "identity_hash"}
        valid = (
            isinstance(root.get("pp_ref"), str)
            and bool(re.fullmatch(r"PP-[A-Z0-9][A-Z0-9_-]*", root["pp_ref"]))
        )
    elif kind == "todo":
        allowed = {"schema", "kind", "todo_id", "identity_hash"}
        valid = isinstance(root.get("todo_id"), int) and root["todo_id"] > 0
    else:
        allowed = set()
        valid = False
    if (
        not valid
        or set(root) != allowed
        or root.get("identity_hash") != execution_root_hash(root)
    ):
        raise MalformedPlanBindingError("Plan binding has malformed execution root")
    return root


def validate_plan_binding(value: object, *, todo_id: int | None = None) -> dict[str, Any]:
    """Return a validated Plan binding or raise a typed refusal."""
    label = f"Todo {todo_id}" if todo_id is not None else "coding job"
    if not isinstance(value, Mapping) or value.get("schema") != PLAN_BINDING_SCHEMA:
        raise MalformedPlanBindingError(f"{label} has malformed Plan binding")
    binding = dict(value)
    if (
        any(not isinstance(binding.get(field), str) or not binding[field]
            for field in _REQUIRED_STRINGS)
        or not isinstance(binding.get("worktree_identity"), dict)
        or not isinstance(binding.get("execution_card"), Mapping)
    ):
        raise MalformedPlanBindingError(f"{label} has malformed Plan binding")
    # Imported lazily to avoid making the light-weight Todo parser import the
    # full solver/bridge graph unless the Todo actually claims Plan execution.
    from tgw.plan_execution_card import PlanExecutionCardError, validate_execution_card
    try:
        card = validate_execution_card(binding["execution_card"])
    except PlanExecutionCardError as exc:
        raise MalformedPlanBindingError(f"{label} has malformed Plan execution card") from exc
    if (
        card["plan"]["commit"] != binding["plan_commit"]
        or card["solution"]["hash"] != binding["solution_hash"]
        or card["solution"]["closure_hash"] != binding["closure_hash"]
        or card["work_unit"]["capability"] != binding["capability"]
        or card["work_unit"]["treatment_id"] != binding["treatment_id"]
        or card["source"]["commit"] != binding["source_commit"]
    ):
        raise MalformedPlanBindingError(f"{label} Plan execution card does not match Plan binding")
    binding["execution_card"] = card
    try:
        root = validate_execution_root(binding.get("execution_root"))
    except MalformedPlanBindingError as exc:
        raise MalformedPlanBindingError(f"{label} has malformed Plan binding: {exc}") from exc
    if root["kind"] == "plan" and root["plan_commit"] != binding["plan_commit"]:
        raise MalformedPlanBindingError(f"{label} Plan root does not match Plan binding")
    binding["execution_root"] = root
    if "supersedes_todo_id" in binding and (
        not isinstance(binding["supersedes_todo_id"], int)
        or binding["supersedes_todo_id"] <= 0
    ):
        raise MalformedPlanBindingError(f"{label} has malformed Plan binding")
    return binding


def parse_plan_binding(status_note: object, *, todo_id: int | None = None) -> dict[str, Any] | None:
    """Parse Plan metadata while leaving unrelated Todo notes untouched."""
    if not isinstance(status_note, str) or not status_note:
        return None
    try:
        parsed = json.loads(status_note)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, Mapping) and parsed.get("schema") == PLAN_BINDING_SCHEMA:
        return validate_plan_binding(parsed, todo_id=todo_id)
    return None
