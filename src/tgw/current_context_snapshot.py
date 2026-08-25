"""Atomic, read-only context snapshot for every TGW harness.

The active task and its Plan-cycle cursor are one context fact.  Keeping them
in separate files made it possible to publish a task that pointed at one leaf
while the MCP searched a different derived graph.  This module defines the
single-file representation used by the MCP and its publisher; it grants no
effect or approval authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
SCHEMA = "tgw-current-context-snapshot/v1"


class CurrentContextError(ValueError):
    """The published context is absent, malformed, or internally divergent."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def build(task: Mapping[str, Any], cursor: Mapping[str, Any]) -> dict[str, Any]:
    """Build one bounded snapshot from a task record and its cycle cursor."""
    if not isinstance(task, Mapping) or task.get("schema") != "tgw-current-task/v1":
        raise CurrentContextError("task record is invalid")
    if not isinstance(cursor, Mapping) or cursor.get("schema") != "tgw-plan-execution-cycle-cursor/v1":
        raise CurrentContextError("cycle cursor is invalid")
    plan = task.get("plan")
    implementation = task.get("implementation")
    development = implementation.get("development_source") if isinstance(implementation, Mapping) else None
    resolved = cursor.get("resolved")
    if not isinstance(plan, Mapping) or not isinstance(development, Mapping) or not isinstance(resolved, Mapping):
        raise CurrentContextError("task/cursor bindings are invalid")
    plan_commit = plan.get("approved_commit")
    source_commit = development.get("commit")
    source_tree = cursor.get("source_tree")
    capability = development.get("next_leaf")
    treatment = resolved.get("next_treatment")
    if (
        not isinstance(plan_commit, str) or _COMMIT.fullmatch(plan_commit) is None
        or not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None
        or not isinstance(source_tree, str) or _COMMIT.fullmatch(source_tree) is None
        or not isinstance(capability, str) or not capability
        or cursor.get("plan_commit") != plan_commit
        or cursor.get("source_commit") != source_commit
        or not isinstance(treatment, str) or treatment.rsplit(":", 1)[-1] != capability
    ):
        raise CurrentContextError("task and cursor select different Plan context")
    snapshot = {
        "schema": SCHEMA,
        "plan_commit": plan_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "active_capability": capability,
        "active_treatment": treatment,
        "task": dict(task),
        "cursor": dict(cursor),
    }
    snapshot["snapshot_sha256"] = _sha(snapshot)
    return snapshot


def parse(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a previously published snapshot."""
    if not isinstance(value, Mapping) or value.get("schema") != SCHEMA:
        raise CurrentContextError("current context snapshot is invalid")
    claimed = value.get("snapshot_sha256")
    body = dict(value)
    body.pop("snapshot_sha256", None)
    if not isinstance(claimed, str) or claimed != _sha(body):
        raise CurrentContextError("current context snapshot hash differs")
    rebuilt = build(body.get("task", {}), body.get("cursor", {}))
    if any(value.get(key) != rebuilt.get(key) for key in ("plan_commit", "source_commit", "source_tree", "active_capability", "active_treatment")):
        raise CurrentContextError("current context snapshot bindings differ")
    return dict(value)
