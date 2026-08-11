from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tgw.environment_registry import load_registry
from tgw.task_context import TaskContextError, load_task, resolve_task_context, validate_task

ROOT = Path(__file__).parents[1]
REGISTRY = ROOT / "config/environment/registry.yaml"
TASK = ROOT / "config/environment/tasks/environment-recovery.json"
RESOLVED = ROOT / "config/environment/resolved/environment-recovery.codex.json"


def _inputs():
    return load_task(TASK), load_registry(REGISTRY)


def test_recovery_task_resolves_deterministically_and_is_actor_scoped():
    task, registry = _inputs()
    first = resolve_task_context(task, registry)
    second = resolve_task_context(task, registry)
    assert first == second
    assert first["actor"] == "codex"
    assert first["repository"]["path"] == "/opt/TGW/tgw-lib/src/trader-grims-warehouse"
    assert first["historical_context_grants_authority"] is False
    assert first["authority_files"] == ["AGENTS.md"]
    assert "CLAUDE.md" in first["excluded_authority_files"]
    assert __import__("json").loads(RESOLVED.read_text()) == first


@pytest.mark.parametrize("path", ["/tmp", "../escape", ".git/config", "src/../etc"])
def test_allowed_paths_fail_closed(path):
    task, registry = _inputs()
    task["allowed_paths"] = [path]
    with pytest.raises(TaskContextError, match="unsafe"):
        validate_task(task, registry)


def test_task_rejects_stale_registry_unknown_actor_and_external_effects():
    task, registry = _inputs()
    for key, value in (
        ("registry_revision", "sha256:" + "0" * 64),
        ("actor", "hermes-from-memory"),
        ("effect_class", "production-write"),
    ):
        bad = deepcopy(task)
        bad[key] = value
        with pytest.raises(TaskContextError):
            validate_task(bad, registry)


def test_expiry_is_enforced_without_affecting_context_identity():
    task, registry = _inputs()
    before = datetime(2026, 8, 12, tzinfo=timezone.utc)
    assert resolve_task_context(task, registry, as_of=before)["task_id"] == task["task_id"]
    after = datetime(2026, 8, 19, tzinfo=timezone.utc)
    with pytest.raises(TaskContextError, match="expired"):
        resolve_task_context(task, registry, as_of=after)


def test_manifest_cannot_grant_history_authority_or_name_unregistered_repo():
    task, registry = _inputs()
    task["historical_context_grants_authority"] = True
    with pytest.raises(TaskContextError, match="Historical|historical"):
        validate_task(task, registry)
    task, registry = _inputs()
    task["repository_id"] = "first-path-found"
    with pytest.raises(TaskContextError, match="repository"):
        validate_task(task, registry)
