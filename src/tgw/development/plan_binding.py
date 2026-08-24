"""Validation for Plan-bound coding Todo metadata."""

from __future__ import annotations

import json
from typing import Any, Mapping


PLAN_BINDING_SCHEMA = "tgw-plan-coding-todo/v1"
_REQUIRED_STRINGS = frozenset({
    "plan_commit", "solution_hash", "closure_hash", "capability",
    "treatment_id", "idempotency_key", "worktree",
})


class MalformedPlanBindingError(ValueError):
    """A Todo identifies itself as Plan-bound but cannot be executed safely."""


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
    ):
        raise MalformedPlanBindingError(f"{label} has malformed Plan binding")
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
