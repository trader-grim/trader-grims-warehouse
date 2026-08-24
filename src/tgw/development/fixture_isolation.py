"""Exact-ID fixture adapters for one local Plan-bound coding proof.

This module is deliberately not a worker registration or a general fixture
framework.  It reserves one Todo source, one queue name, and one subdirectory
below the configured coding worktree root for a caller-supplied fixture run.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from tgw.development.foreman import TodoRecord
from tgw.development.plan_binding import validate_plan_binding
from tgw.errors import HardFailure
from tgw.queue import state_machine
from tgw.workers.coding import CodingWorker

_RUN_ID = re.compile(r"^fixture-[a-z0-9](?:[a-z0-9-]{6,62})$")
_QUEUE_PREFIX = "tgw-fixture-codex-implement:"
_SOURCE_PREFIX = "tgw-fixture:"


class FixtureIsolationError(ValueError):
    """The requested fixture action is outside its exact isolated boundary."""


def validate_fixture_run_id(run_id: object) -> str:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise FixtureIsolationError("fixture run id must match fixture-[a-z0-9-]{7,63}")
    return run_id


def fixture_queue_name(run_id: object) -> str:
    return _QUEUE_PREFIX + validate_fixture_run_id(run_id)


def fixture_todo_source(run_id: object) -> str:
    return _SOURCE_PREFIX + validate_fixture_run_id(run_id)


def fixture_worktree_root(canonical_root: str | Path, run_id: object) -> Path:
    root = Path(canonical_root).resolve()
    if not root.is_absolute() or root == Path("/"):
        raise FixtureIsolationError("configured coding worktree root is unsafe")
    return root / "fixture-runs" / validate_fixture_run_id(run_id)


def create_fixture_todo(
    run_id: object, *, agent: str, body: str, priority: int,
    pp_ref: str | None = None, plan_anchor: str | None = None,
) -> Mapping[str, Any]:
    """Create one durable fixture Todo without scheduling normal plan render."""
    from tgw.todo import todo_add
    return todo_add(
        agent, body, priority, source=fixture_todo_source(run_id), pp_ref=pp_ref,
        plan_anchor=plan_anchor, suppress_plan_render=True,
    )


def list_fixture_todos(run_id: object) -> list[Mapping[str, Any]]:
    """Read only Todo records owned by this exact fixture run."""
    from tgw.todo import _conn
    with _conn() as con, con.cursor() as cur:
        cur.execute(
            "SELECT id, agent, priority, body, status_note FROM todo_items "
            "WHERE source = %s AND done_at IS NULL ORDER BY id",
            (fixture_todo_source(run_id),),
        )
        return [
            {"id": row[0], "agent": row[1], "priority": row[2], "body": row[3], "status_note": row[4]}
            for row in cur.fetchall()
        ]


def fixture_todo_record(run_id: object, todo_id: int) -> TodoRecord:
    if not isinstance(todo_id, int) or todo_id <= 0:
        raise FixtureIsolationError("fixture Todo id is invalid")
    rows = [row for row in list_fixture_todos(run_id) if row["id"] == todo_id]
    if len(rows) != 1:
        raise FixtureIsolationError("fixture Todo is missing or outside this run")
    row = rows[0]
    try:
        binding = validate_plan_binding(json.loads(row["status_note"]), todo_id=todo_id)
    except (TypeError, ValueError) as exc:
        raise FixtureIsolationError("fixture Todo has malformed Plan binding") from exc
    if binding.get("fixture_run_id") != validate_fixture_run_id(run_id):
        raise FixtureIsolationError("fixture Todo Plan binding crosses fixture namespace")
    return TodoRecord(todo_id, row["agent"] or "", row["priority"], row["body"] or "", binding["worktree"], binding)


def fixture_enqueue(run_id: object, enqueue_fn: Callable[..., str] | None = None) -> Callable[..., str]:
    """Adapt the foreman's ordinary treatment dispatch into this fixture queue."""
    run_id = validate_fixture_run_id(run_id)
    real_enqueue = enqueue_fn or state_machine.enqueue_job
    queue_name = fixture_queue_name(run_id)

    def enqueue(requested_queue: str, payload: dict[str, Any], **kwargs: Any) -> str:
        if requested_queue != "codex-implement":
            raise FixtureIsolationError("fixture foreman may dispatch only codex-implement")
        binding = validate_plan_binding(payload.get("plan_binding"), todo_id=payload.get("todo_id"))
        if binding.get("fixture_run_id") != run_id:
            raise FixtureIsolationError("fixture job Plan binding crosses fixture namespace")
        if payload.get("treatment_id") != "codex-implement":
            raise FixtureIsolationError("fixture job has wrong treatment")
        durable_payload = dict(payload, fixture_run_id=run_id)
        durable_key = f"fixture:{run_id}:{kwargs.get('dedupe_key', '')}"
        if not kwargs.get("dedupe_key"):
            raise FixtureIsolationError("fixture job has no dedupe key")
        return real_enqueue(
            queue_name, durable_payload, entity_type="coding_task",
            entity_id=str(payload.get("object_id") or payload.get("worktree") or ""),
            handler_family="fixture-codex-implement", dedupe_key=durable_key,
        )
    return enqueue


def run_fixture_job_once(
    run_id: object, *, job_id: str, config: dict[str, Any], launcher: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Claim one named fixture job and execute it through ``CodingWorker``.

    The named claim is deliberately stricter than a queue polling loop: it can
    never take an adjacent fixture run's job, and ordinary workers cannot claim
    this queue because it is not a registered coding treatment queue.
    """
    run_id = validate_fixture_run_id(run_id)
    queued = state_machine.get_job(job_id)
    if queued is None or queued.get("queue_name") != fixture_queue_name(run_id):
        raise FixtureIsolationError("fixture worker refuses a job outside its exact queue")
    payload = queued.get("payload_json") or {}
    if payload.get("fixture_run_id") != run_id:
        raise FixtureIsolationError("fixture worker refuses a cross-namespace job")
    binding = validate_plan_binding(payload.get("plan_binding"), todo_id=payload.get("todo_id"))
    if binding.get("fixture_run_id") != run_id:
        raise FixtureIsolationError("fixture worker refuses a cross-namespace Plan binding")
    owner = f"fixture-worker:{run_id}"
    claimed = state_machine.claim_job(job_id, owner, lease_seconds=300)
    if claimed is None:
        raise FixtureIsolationError("fixture job is not claimable")
    token = str(claimed["lease_token"])
    state_machine.mark_running(job_id, owner, token)
    try:
        receipt = CodingWorker("codex-implement", config, launcher=launcher).handle(claimed)
    except Exception as exc:
        state_machine.mark_failed(job_id, owner, token, repr(exc))
        raise
    state_machine.mark_succeeded(job_id, owner, token, receipt)
    return receipt


def cleanup_fixture_run(
    run_id: object, *, canonical_worktree_root: str | Path, repository_root: str | Path,
) -> dict[str, int]:
    """Remove only the exact run's queue rows, Todo rows, and worktree.

    No glob, prefix delete, or recursive filesystem deletion is used.
    """
    run_id = validate_fixture_run_id(run_id)
    queue_name, source = fixture_queue_name(run_id), fixture_todo_source(run_id)
    canonical_root = Path(canonical_worktree_root).resolve()
    repository = Path(repository_root).resolve()
    root = fixture_worktree_root(canonical_root, run_id)
    removed = 0
    jobs = todos = 0
    database_error: Exception | None = None
    try:
        with state_machine._conn() as con, con.cursor() as cur:
            cur.execute("DELETE FROM queue_jobs WHERE queue_name = %s AND payload_json->>'fixture_run_id' = %s", (queue_name, run_id))
            jobs = cur.rowcount
        from tgw.todo import _conn
        with _conn() as con, con.cursor() as cur:
            cur.execute("DELETE FROM todo_items WHERE source = %s", (source,))
            todos = cur.rowcount
    except Exception as exc:
        database_error = exc
    finally:
        if root.exists():
            for worktree in sorted(root.iterdir()):
                if not worktree.is_dir() or worktree.parent != root:
                    raise FixtureIsolationError("fixture cleanup found an unsafe worktree target")
                subprocess.run(["git", "-C", str(repository), "worktree", "remove", "--force", str(worktree)], check=True)
                removed += 1
            root.rmdir()
            try:
                root.parent.rmdir()
            except OSError:
                pass
    if database_error is not None:
        raise FixtureIsolationError("fixture database cleanup could not be verified") from database_error
    return {"jobs": jobs, "todos": todos, "worktrees": removed}
