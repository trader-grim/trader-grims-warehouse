"""Deterministic, non-effecting task and agent-context resolution."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from tgw.environment_registry import (
    resolved_agent_context,
    validate_registry,
)


class TaskContextError(ValueError):
    """A task manifest cannot safely resolve against the environment registry."""


_TASK_KEYS = {
    "schema", "task_id", "repository_id", "base_commit", "branch", "actor",
    "allowed_paths", "effect_class", "plan_binding", "acceptance_ids",
    "registry_revision", "created_at", "expires_at",
    "historical_context_grants_authority", "worktree_policy",
}
_PLAN_KEYS = {"plan_id", "plan_version", "scope_hash"}
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{1,127}$")
_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        raise TaskContextError("task context is not canonical JSON data") from exc


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TaskContextError(f"{label} must be a string-keyed mapping")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TaskContextError(f"{label} must be a canonical non-empty string")
    return value


def _timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise TaskContextError(f"{label} must be a canonical timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TaskContextError(f"{label} must be a canonical timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TaskContextError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise TaskContextError(f"{label} must be a non-empty string list")
    if not all(isinstance(item, str) and item.strip() and item == item.strip() for item in value):
        raise TaskContextError(f"{label} must be a string list")
    if len(value) != len(set(value)):
        raise TaskContextError(f"{label} contains duplicates")
    return value


def validate_task(
    raw: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    task = _mapping(dict(raw), "task")
    if set(task) != _TASK_KEYS or task.get("schema") != "tgw-task/v1":
        raise TaskContextError("task fields or schema are not exact")
    validated_registry = validate_registry(registry)
    if task["registry_revision"] != validated_registry["revision"]:
        raise TaskContextError("task registry revision is stale")
    if not isinstance(task["task_id"], str) or not _ID.fullmatch(task["task_id"]):
        raise TaskContextError("task_id is invalid")
    repositories = validated_registry["content"]["repositories"]
    if task["repository_id"] not in repositories:
        raise TaskContextError("task repository is not registered")
    if not isinstance(task["base_commit"], str) or not _COMMIT.fullmatch(task["base_commit"]):
        raise TaskContextError("task base commit is not exact")
    _string(task["branch"], "task branch")
    agents = validated_registry["content"]["agents"]
    if task["actor"] not in agents:
        raise TaskContextError("task actor is not registered")
    paths = _strings(task["allowed_paths"], "task allowed_paths")
    for raw_path in paths:
        path = PurePosixPath(raw_path)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts or str(path) != raw_path:
            raise TaskContextError(f"unsafe task allowed path: {raw_path}")
    if task["effect_class"] not in {"read-only", "local-reversible"}:
        raise TaskContextError("task effect class is not permitted by this contract")
    plan = _mapping(task["plan_binding"], "task plan_binding")
    if set(plan) != _PLAN_KEYS:
        raise TaskContextError("task plan binding fields are not exact")
    if not isinstance(plan["plan_id"], str) or not _ID.fullmatch(plan["plan_id"]):
        raise TaskContextError("task plan id is invalid")
    if isinstance(plan["plan_version"], bool) or not isinstance(plan["plan_version"], int) or plan["plan_version"] < 1:
        raise TaskContextError("task plan version is invalid")
    if not isinstance(plan["scope_hash"], str) or not _HASH.fullmatch(plan["scope_hash"]):
        raise TaskContextError("task scope hash is invalid")
    _strings(task["acceptance_ids"], "task acceptance_ids")
    created = _timestamp(task["created_at"], "task created_at")
    expires = _timestamp(task["expires_at"], "task expires_at")
    if expires <= created:
        raise TaskContextError("task expiry must follow creation")
    if as_of is not None:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise TaskContextError("as_of must be timezone-aware")
        if as_of.astimezone(timezone.utc) >= expires:
            raise TaskContextError("task manifest is expired")
    if task["historical_context_grants_authority"] is not False:
        raise TaskContextError("historical context cannot grant task authority")
    if task["worktree_policy"] not in {"existing-actor-worktree", "new-ephemeral-worktree"}:
        raise TaskContextError("task worktree policy is invalid")
    return task


def resolve_task_context(
    task_raw: Mapping[str, Any],
    registry_raw: Mapping[str, Any],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    registry = validate_registry(registry_raw)
    task = validate_task(task_raw, registry, as_of=as_of)
    environment = resolved_agent_context(registry, task["actor"])
    repository = environment["repositories"][task["repository_id"]]
    body = {
        "schema": "tgw-resolved-context/v1",
        "registry_revision": registry["revision"],
        "task_hash": _hash(task),
        "task_id": task["task_id"],
        "actor": task["actor"],
        "repository": {
            "repository_id": task["repository_id"],
            "host_role": repository["host_role"],
            "path": repository["path"],
            "branch": task["branch"],
            "base_commit": task["base_commit"],
            "dirty_policy": repository["dirty_policy"],
        },
        "allowed_paths": task["allowed_paths"],
        "effect_class": task["effect_class"],
        "plan_binding": task["plan_binding"],
        "acceptance_ids": task["acceptance_ids"],
        "worktree_policy": task["worktree_policy"],
        "instruction_precedence": environment["instructions"]["precedence"],
        "authority_files": environment["actor_instructions"]["authority_files"],
        "excluded_authority_files": environment["actor_instructions"]["excluded_authority_files"],
        "historical_context_grants_authority": False,
        "unknowns": environment["unknowns"],
    }
    return {**body, "context_id": _hash(body)}


def load_task(path: Path) -> dict[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), "task")
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskContextError(f"cannot load task manifest: {exc}") from exc
