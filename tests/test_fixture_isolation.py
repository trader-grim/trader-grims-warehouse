from contextlib import contextmanager

import pytest

from tgw.development.fixture_isolation import (
    FixtureIsolationError, create_fixture_todo, fixture_enqueue, fixture_queue_name,
    fixture_worktree_root, run_fixture_job_once, validate_fixture_run_id, cleanup_fixture_run,
)
from tgw.workers.coding import CodingWorker
from tgw.development.plan_binding import execution_root_hash


RUN_ID = "fixture-local-spine-001"


def _binding():
    root = {
        "schema": "tgw-execution-root/v1", "kind": "plan",
        "plan_id": "fixture", "profile": "implementation", "plan_commit": "a" * 40,
    }
    root["identity_hash"] = execution_root_hash(root)
    return {
        "schema": "tgw-plan-coding-todo/v1", "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "1" * 64, "closure_hash": "sha256:" + "2" * 64,
        "capability": "fixture.code@1", "treatment_id": "codex-implement",
        "source_commit": "a" * 40, "requested_worktree_identity": RUN_ID,
        "idempotency_key": "sha256:key", "worktree": "/configured/fixture",
        "worktree_identity": {"worktree": "/configured/fixture"}, "fixture_run_id": RUN_ID,
        "execution_root": root,
    }


def test_fixture_id_and_worktree_namespace_are_exact():
    assert validate_fixture_run_id(RUN_ID) == RUN_ID
    assert fixture_queue_name(RUN_ID) == "tgw-fixture-codex-implement:" + RUN_ID
    assert str(fixture_worktree_root("/opt/TGW/var/worktrees", RUN_ID)).endswith("fixture-runs/" + RUN_ID)
    for invalid in ("fixture-x", "fixture-../../bad", "codex-implement", "fixture-Uppercase"):
        with pytest.raises(FixtureIsolationError):
            validate_fixture_run_id(invalid)


def test_fixture_todo_uses_real_adapter_without_plan_render(monkeypatch):
    observed = {}
    monkeypatch.setattr("tgw.todo.todo_add", lambda *args, **kwargs: observed.update(args=args, kwargs=kwargs) or {"id": 1})
    assert create_fixture_todo(RUN_ID, agent="codex", body="fixture", priority=1)["id"] == 1
    assert observed["kwargs"]["source"] == "tgw-fixture:" + RUN_ID
    assert observed["kwargs"]["suppress_plan_render"] is True


def test_fixture_dispatch_uses_namespaced_queue_and_preserves_binding():
    calls = []
    enqueue = fixture_enqueue(RUN_ID, lambda *args, **kwargs: calls.append((args, kwargs)) or "fixture-job")
    payload = {
        "todo_id": 7, "treatment_id": "codex-implement", "object_id": "/configured/fixture",
        "plan_binding": _binding(),
        "task_spec": {"schema": "coding-task/v1", "todo_id": 7, "agent": "codex", "body": "fixture"},
    }
    assert enqueue(queue_name="codex-implement", payload=payload, dedupe_key="graph-id") == "fixture-job"
    args, kwargs = calls[0]
    assert args[0] == fixture_queue_name(RUN_ID)
    assert args[1]["plan_binding"] == _binding()
    assert args[1]["fixture_run_id"] == RUN_ID
    assert kwargs["dedupe_key"] == "fixture:" + RUN_ID + ":graph-id"


def test_ordinary_coding_worker_rejects_fixture_queue():
    with pytest.raises(ValueError, match="unsupported coding queue"):
        CodingWorker(fixture_queue_name(RUN_ID), {"coding": {}})


def test_fixture_worker_refuses_cross_namespace_job(monkeypatch):
    monkeypatch.setattr("tgw.development.fixture_isolation.state_machine.get_job", lambda _job: {"queue_name": "codex-implement", "payload_json": {}})
    with pytest.raises(FixtureIsolationError, match="outside its exact queue"):
        run_fixture_job_once(RUN_ID, job_id="any", config={"coding": {}}, launcher=lambda *_: {})


def test_cleanup_removes_only_exact_empty_namespace_when_database_is_unavailable(monkeypatch, tmp_path):
    root = tmp_path / "fixture-runs" / RUN_ID
    root.mkdir(parents=True)

    @contextmanager
    def unavailable():
        raise RuntimeError("database unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr("tgw.development.fixture_isolation.fixture_worktree_root", lambda *_: root)
    monkeypatch.setattr("tgw.development.fixture_isolation.state_machine._conn", unavailable)
    with pytest.raises(FixtureIsolationError, match="cleanup could not be verified"):
        cleanup_fixture_run(RUN_ID, canonical_worktree_root="/configured", repository_root="/repository")
    assert not root.exists()
