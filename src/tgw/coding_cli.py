"""Operator CLI for TGW's ordinary local Unix-user coding workflow.

This is deliberately a tgw-lib-local control surface. It binds an existing
Todo through the pinned Plan/Luet solution, creates or reuses its group-owned
Git worktree, and asks the existing Foreman to evaluate exactly that Todo.
It has no production, SSH, sudo, remote-provision, or approval dependency.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2.extras

from tgw import todo
from tgw.development.foreman import ForemanConfig, tick
from tgw.development.local_workflow import (
    DEFAULT_CONFIG,
    LocalCodingWorkflowError,
    bind_command,
    load_config,
    require_coder_account,
    status_command,
)
from tgw.development.plan_todo_source import PlanTodoSourceError
from tgw.development.plan_todo_source import resolve as resolve_plan_todo
from tgw.development.treatments import CODEX_IMPLEMENT, CONTROLLER_VERIFY
from tgw.pp_workflow_reconcile import PP_REF
from tgw.pp_workflow_reconcile import reconcile as reconcile_pp_workflow
from tgw.queue import state_machine

DEFAULT_SOLUTION = (
    Path(__file__).resolve().parents[2]
    / "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-058e2f98.json"
)
DEFAULT_TREATMENT = "establish:workflow.condition-derived-convergence@1"
DEFAULT_PLAN_REPOSITORY = Path("/opt/TGW/library/plans")
_LOCAL_QUEUES = ("codex-implement", "controller-verify")


class CodingCLIError(RuntimeError):
    """The requested local coding operation is invalid or unavailable."""


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _initialize(config_path: Path | str) -> dict[str, Any]:
    config = load_config(config_path)
    require_coder_account()
    todo.init(config["postgres_dsn"])
    state_machine.init(config["postgres_dsn"])
    return config


def _jobs(todo_id: int | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    where = "queue_name = ANY(%s)"
    params: list[Any] = [list(_LOCAL_QUEUES)]
    if todo_id is not None:
        where += " AND payload_json->>'todo_id' = %s"
        params.append(str(todo_id))
    params.append(limit)
    with state_machine._conn() as connection:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(
                f"""
                SELECT job_id::text, queue_name, state, entity_id, operation,
                       attempt_count, max_attempts, created_at, updated_at,
                       lease_owner, payload_json
                  FROM queue_jobs
                 WHERE {where}
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def _todo_id(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CodingCLIError("a positive Todo ID is required") from exc
    if result <= 0:
        raise CodingCLIError("a positive Todo ID is required")
    return result


def start(
    todo_id: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    solution_path: Path | str = DEFAULT_SOLUTION,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Bind, allocate, evaluate, and dispatch one existing Todo locally."""
    if isinstance(todo_id, str) and todo_id == PP_REF:
        actor = require_coder_account()
        result = reconcile_pp_workflow()
        return {
            "schema": "tgw-local-coding-start-pp/v1", "ok": result["ok"],
            "target": PP_REF, "actor": actor, "group": "tgw-coders",
            "reconciliation": result,
            "materialized": False,
            "note": "All bounded PP capabilities are satisfied; no Todo, worktree, or job was created.",
        }
    todo_id = _todo_id(todo_id)
    config = _initialize(config_path)
    item = todo.todo_get(todo_id)
    projection = None
    if item is None:
        solution = __import__(
            "tgw.development.local_workflow", fromlist=["load_solution"]
        ).load_solution(solution_path)
        projection = resolve_plan_todo(
            todo_id,
            repository=config.get("plan_repository_root", DEFAULT_PLAN_REPOSITORY),
            approved_commit=solution["plan_commit"],
        )
        todo.todo_import_projection(projection)
        item = todo.todo_get(todo_id)
        if item is None:
            raise CodingCLIError(f"Todo {todo_id} projection was not materialized")
    if item.get("done_at") is not None:
        raise CodingCLIError(f"Todo {todo_id} is already complete")

    binding = bind_command(
        argparse.Namespace(
            config=Path(config_path),
            solution=Path(solution_path),
            treatment_id=DEFAULT_TREATMENT,
            source_commit=source_commit,
            agent="codex",
            body=item["body"],
            priority=item["priority"],
            pp_ref=None,
            todo_id=todo_id,
        )
    )
    coding = config["coding"]
    result = tick(
        ForemanConfig(
            coding_config=dict(coding),
            treatments=(CODEX_IMPLEMENT, CONTROLLER_VERIFY),
            receipt_backed_conditions=frozenset({"tested", "linted"}),
        ),
        todo_ids={todo_id},
    )
    jobs = _jobs(todo_id, limit=10)
    worktree = binding["binding"]["worktree"]
    return {
        "schema": "tgw-local-coding-start/v1",
        "ok": result.errors == 0 and result.refused_plan_binding == 0,
        "todo_id": todo_id,
        "actor": require_coder_account(),
        "worktree": worktree,
        "branch": binding["binding"]["worktree_identity"]["branch"],
        "source_commit": binding["binding"]["source_commit"],
        "plan_commit": binding["binding"]["plan_commit"],
        "solution_hash": binding["binding"]["solution_hash"],
        "todo_projection": None if projection is None else {
            "source": projection["source"],
            "plan_repository": projection["plan_repository"],
            "plan_evidence_commit": projection["plan_evidence_commit"],
            "taskboard_path": projection["taskboard_path"],
            "taskboard_blob": projection["taskboard_blob"],
        },
        "foreman": dataclasses.asdict(result),
        "jobs": jobs,
        "session": {
            "cwd": worktree,
            "codex": ["codex", "-C", worktree],
            "note": "The automated Codex treatment is already dispatched when eligible; this command is only for an optional interactive session in the same exact worktree.",
        },
        "dependencies": {
            "tgw_prod": False,
            "ssh": False,
            "sudo": False,
            "remote_provision_api": False,
            "approval_card": False,
        },
    }


def status(
    todo_id: int | None = None,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    config = _initialize(config_path)
    local = status_command(argparse.Namespace(config=Path(config_path)))
    local["jobs"] = _jobs(todo_id)
    if todo_id is not None:
        local["todo_id"] = todo_id
    local["database"] = config["postgres_dsn"]
    return local


def reconcile(target: str = PP_REF) -> dict[str, Any]:
    """Read-only reconciliation status for an explicit PP root."""
    if target != PP_REF:
        raise CodingCLIError(f"unsupported PP root: {target}")
    return reconcile_pp_workflow()


def job_log(
    job_id: str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    _initialize(config_path)
    job = state_machine.get_job(job_id)
    if job is None or job.get("queue_name") not in _LOCAL_QUEUES:
        raise CodingCLIError(f"local coding job {job_id} does not exist")
    return job


def stop(
    job_id: str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    _initialize(config_path)
    job = state_machine.get_job(job_id)
    if job is None or job.get("queue_name") not in _LOCAL_QUEUES:
        raise CodingCLIError(f"local coding job {job_id} does not exist")
    if job.get("state") in {"succeeded", "failed", "dead_letter", "cancelled"}:
        raise CodingCLIError(f"local coding job {job_id} is already {job['state']}")
    return state_machine.cancel_job(job_id, "stopped by the local operator CLI")


def _target(args: argparse.Namespace) -> str | None:
    return getattr(args, "coding_target", None) or getattr(args, "request_id", None)


def run(args: argparse.Namespace) -> int:
    try:
        config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG)
        target = _target(args)
        if args.coding_op == "start":
            value = target or getattr(args, "todo_id", None)
            result = start(
                value if value == PP_REF else _todo_id(value),
                config_path=config_path,
                source_commit=getattr(args, "source_commit", None),
            )
        elif args.coding_op in {"status", "access-status"}:
            result = (reconcile(target) if target == PP_REF else status(
                _todo_id(target) if target is not None else None, config_path=config_path))
        elif args.coding_op == "reconcile":
            result = reconcile(target or PP_REF)
        elif args.coding_op == "log":
            if not target:
                raise CodingCLIError("log requires a coding job ID")
            result = job_log(target, config_path=config_path)
        elif args.coding_op == "stop":
            if not target:
                raise CodingCLIError("stop requires a coding job ID")
            result = stop(target, config_path=config_path)
        else:
            raise CodingCLIError(f"unknown coding operation: {args.coding_op}")
        print(json.dumps(result, sort_keys=True, default=_json_default))
        return 0 if result.get("ok", True) else 1
    except (
        CodingCLIError, LocalCodingWorkflowError, PlanTodoSourceError,
        OSError, ValueError,
    ) as exc:
        print(f"tgw coding: {exc}", file=__import__("sys").stderr)
        return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tgw coding")
    root.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = root.add_subparsers(dest="coding_op", required=True)
    start_parser = commands.add_parser("start", help="bind and dispatch one Todo locally")
    start_parser.add_argument("coding_target", metavar="TODO_ID|PP_REF")
    start_parser.add_argument("--source-commit")
    status_parser = commands.add_parser("status", help="show local coding jobs")
    status_parser.add_argument("coding_target", metavar="TODO_ID|PP_REF", nargs="?")
    reconcile_parser = commands.add_parser("reconcile", help="read-only PP reconciliation")
    reconcile_parser.add_argument("coding_target", metavar="PP_REF", nargs="?", default=PP_REF)
    log_parser = commands.add_parser("log", help="show one durable coding job")
    log_parser.add_argument("coding_target", metavar="JOB_ID")
    stop_parser = commands.add_parser("stop", help="cancel one active coding job")
    stop_parser.add_argument("coding_target", metavar="JOB_ID")
    access = commands.add_parser("access-status", help="prove the local Unix/group binding")
    access.add_argument("coding_target", metavar="TODO_ID", nargs="?")
    return root


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
