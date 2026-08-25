"""Operator CLI for TGW's ordinary local Unix-user coding workflow.

This is deliberately a tgw-lib-local control surface. It binds an existing
Todo through the pinned Plan/Luet solution, creates or reuses its group-owned
Git worktree, and asks the existing Foreman to evaluate exactly that Todo.
It has no production, SSH, sudo, remote-provision, or approval dependency.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import psycopg2.extras

from tgw import todo
from tgw.development.foreman import ForemanConfig, tick
from tgw.development.local_workflow import (
    DEFAULT_CONFIG,
    LocalCodingWorkflowError,
    allocate_worktree,
    bind_command,
    load_config,
    require_coder_account,
    status_command,
)
from tgw.development.partial_resume import (
    classify,
    migrate_todo_1747,
    preservation_manifest,
    source_tree,
)
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.development.plan_todo_source import PlanTodoSourceError
from tgw.development.plan_todo_source import resolve as resolve_plan_todo
from tgw.development.treatments import CODEX_IMPLEMENT, CONTROLLER_VERIFY
from tgw.development.worktree_lease import exclusive_worktree_lease
from tgw.pp_workflow_reconcile import PP_REF
from tgw.pp_workflow_reconcile import reconcile as reconcile_pp_workflow
from tgw.queue import state_machine
from tgw.workflow import compile_solution_runtime

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOLUTION = (
    ROOT
    / "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-058e2f98.json"
)
DEFAULT_TREATMENT = "establish:workflow.condition-derived-convergence@1"
DEFAULT_PLAN_REPOSITORY = Path("/opt/TGW/library/plans")
_LOCAL_QUEUES = ("codex-implement", "controller-verify")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_LEGACY_1747_JOBS = (
    ("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial"),
    ("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed"),
)


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


def _legacy_1747_jobs() -> list[dict[str, Any]]:
    """Read the two immutable queue facts used by the one-time migration."""
    result = []
    for job_id, outcome in _LEGACY_1747_JOBS:
        row = state_machine.get_job(job_id)
        if row is None:
            raise CodingCLIError(f"Todo 1747 durable job {job_id} is missing")
        result.append(
            {
                "job_id": job_id,
                "outcome": outcome,
                "attempt_count": row.get("attempt_count"),
                "state": row.get("state"),
                "error_code": row.get("error_code"),
                "error_detail": row.get("error_detail"),
                "payload": row.get("payload_json") or row.get("payload"),
            }
        )
    return result


def _pp_runtime_binding(config: dict[str, Any], source_commit: str | None = None) -> dict[str, Any]:
    """Resolve the one external repository/runtime binding used by CLI and MCP."""
    local = __import__("tgw.development.local_workflow", fromlist=["_git"])
    repository = Path(config["coding"]["repository_root"])
    try:
        top = Path(local._git(ROOT, "rev-parse", "--show-toplevel")).resolve()
    except (OSError, ValueError, LocalCodingWorkflowError):
        top = None
    if top == ROOT.resolve():
        mode = "source-worktree"
        commit = source_commit or local._git(ROOT, "rev-parse", "HEAD")
    else:
        mode = "immutable-release"
        release_commit = ROOT.name
        if _COMMIT.fullmatch(release_commit) is None:
            raise CodingCLIError("installed coding runtime is not an immutable commit release")
        if source_commit is not None and source_commit != release_commit:
            raise CodingCLIError("requested source differs from installed immutable runtime")
        commit = release_commit
    tree = local._git(repository, "rev-parse", f"{commit}^{{tree}}")
    return {"repository": repository, "source_root": ROOT, "selected_commit": commit,
            "selected_tree": tree, "runtime_mode": mode}


def start(
    todo_id: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    solution_path: Path | str = DEFAULT_SOLUTION,
    source_commit: str | None = None,
    resume_only: bool = False,
) -> dict[str, Any]:
    """Bind, allocate, evaluate, and dispatch one existing Todo locally."""
    if isinstance(todo_id, str) and todo_id == PP_REF:
        if resume_only:
            raise CodingCLIError("resume-only operation requires one Todo ID")
        config = _initialize(config_path)
        actor = require_coder_account()
        coding = config["coding"]
        runtime = _pp_runtime_binding(config, source_commit)
        repository = runtime["repository"]
        commit = runtime["selected_commit"]
        result = reconcile_pp_workflow(
            todo_rows=todo.todo_list(show_all=True), **runtime,
        )
        materialized = []
        if result["unmet_capabilities"]:
            solution = result["solution"]
            compiled = compile_solution_runtime(solution, current_plan_commit=solution["plan_commit"])
            worktree_root = Path(coding["worktree_root"])
            root = {"schema": "tgw-execution-root/v1", "kind": "pp", "pp_ref": PP_REF}
            lock_path = worktree_root / ".pp-workflow-001-materialize.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with lock_path.open("a") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                for unit in solution["work_units"]:
                    capability = unit["capability"]
                    materialized.append(bind_leaf(
                    compiled, solution=solution, treatment_id=unit["id"],
                    source_commit=commit, worktree_identity=f"unix:{actor}",
                    agent=actor, body=f"{PP_REF}: establish genuinely unmet {capability}",
                    priority=50,
                    create_todo=lambda agent, body, priority, source, pp, anchor: todo.todo_add(
                        agent, body, priority, source, pp_ref=pp, plan_anchor=anchor,
                        suppress_plan_render=True),
                    list_todos=lambda: todo.todo_list(show_all=True),
                    allocate_worktree=lambda item, request, source: allocate_worktree(
                        repository, worktree_root, actor, item, request, source),
                    set_status_note=lambda item, note: todo.todo_set_status_note(
                        item, note, suppress_plan_render=True),
                    execution_root=root,
                    ))
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            # Resumed bindings need the same deduplicating Foreman tick as new
            # rows; the Foreman alone decides whether a job already exists.
            bound_ids = {item["todo_id"] for item in materialized}
            foreman = tick(
                ForemanConfig(
                    coding_config=dict(coding),
                    treatments=(CODEX_IMPLEMENT, CONTROLLER_VERIFY),
                    receipt_backed_conditions=frozenset({"tested", "linted"}),
                ),
                todo_ids=bound_ids,
            )
            operation_success = foreman.errors == 0 and foreman.refused_plan_binding == 0
            result["dimensions"] = {"reconciliation_complete": False,
                                    "operation_success": operation_success,
                                    "materialization_attempted": True}
            result["effects"] = {
                "todo_created": any(item["created"] for item in materialized),
                "worktree_created": any(
                    item.get("binding", {}).get("worktree_identity", {}).get("created") is True
                    for item in materialized
                ),
                "job_created": getattr(foreman, "dispatched", 0) > 0,
                "plan_publication": False,
            }
            result["foreman"] = dataclasses.asdict(foreman)
        operation_success = result.get("dimensions", {}).get("operation_success", True)
        return {
            "schema": "tgw-local-coding-start-pp/v1", "ok": operation_success,
            "reconciliation_complete": result["ok"],
            "target": PP_REF, "actor": actor, "group": "tgw-coders",
            "reconciliation": result,
            "materialized": materialized,
            "dependencies": {"tgw_prod": False, "ssh": False, "sudo": False,
                             "remote_provision_api": False, "approval_card": False},
            "note": ("All bounded PP capabilities are satisfied; no Todo, worktree, or job was created."
                     if not materialized else "Genuinely unmet PP work was passed through the explicit PP-root bridge; Foreman owns dispatch."),
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
    worktree = Path(binding["binding"]["worktree"])
    expected_attempt = {
        "todo_id": todo_id, "plan_commit": binding["binding"]["plan_commit"],
        "solution_hash": binding["binding"]["solution_hash"],
        "source_commit": binding["binding"]["source_commit"],
        "source_tree": source_tree(worktree, binding["binding"]["source_commit"]),
        "actor": item.get("agent") or "codex", "worktree": str(worktree),
        "treatment_id": "codex-implement", "treatment_version": "1",
    }
    migration: str | None = None
    with exclusive_worktree_lease(worktree):
        if resume_only and todo_id == 1747:
            migration = str(
                migrate_todo_1747(worktree, expected_attempt, _legacy_1747_jobs())
            )
        resume_state = classify(worktree, expected_attempt)
        resume_bindings: dict[int, dict[str, str]] = {}
        if resume_state["state"] == "RESUMABLE_PARTIAL":
            resume_bindings[todo_id] = {
                "resume_of": resume_state["resume_of"],
                "resume_fingerprint": resume_state["fingerprint"],
            }
        elif resume_only:
            manifest = None
            if resume_state["state"] in {"UNSAFE_DIRTY", "STALE_RECEIPT"}:
                manifest = preservation_manifest(worktree, resume_state, expected_attempt)
            suffix = f"; preserved at {manifest}" if manifest is not None else ""
            raise CodingCLIError(
                f"Todo {todo_id} is {resume_state['state']}; "
                f"coding resume requires RESUMABLE_PARTIAL{suffix}"
            )
        elif resume_state["state"] not in {"ABANDONED_CLEAN", "CLOSED_CANDIDATE"}:
            manifest = preservation_manifest(worktree, resume_state, expected_attempt)
            raise CodingCLIError(
                f"Todo {todo_id} is {resume_state['state']}; preserved at {manifest} and not dispatched"
            )
    result = tick(
        ForemanConfig(
            coding_config=dict(coding),
            treatments=(CODEX_IMPLEMENT, CONTROLLER_VERIFY),
            receipt_backed_conditions=frozenset({"tested", "linted"}),
            resume_bindings=resume_bindings,
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
        "coding_state": classify(Path(worktree), expected_attempt),
        "resume_only": resume_only,
        "migration": migration,
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


def resume(
    todo_id: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    solution_path: Path | str = DEFAULT_SOLUTION,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Resume exactly one already-proven partial implementation."""
    return start(
        todo_id,
        config_path=config_path,
        solution_path=solution_path,
        source_commit=source_commit,
        resume_only=True,
    )


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
        item = todo.todo_get(todo_id)
        if item and isinstance(item.get("status_note"), str):
            from tgw.development.plan_binding import parse_plan_binding
            binding = parse_plan_binding(item["status_note"], todo_id=todo_id)
            if binding:
                local["coding_state"] = classify(Path(binding["worktree"]), {
                    "todo_id": todo_id, "plan_commit": binding["plan_commit"],
                    "solution_hash": binding["solution_hash"], "source_commit": binding["source_commit"],
                    "source_tree": source_tree(Path(binding["worktree"]), binding["source_commit"]),
                    "actor": item.get("agent") or "codex", "worktree": binding["worktree"],
                    "treatment_id": "codex-implement", "treatment_version": "1",
                })
    local["database"] = config["postgres_dsn"]
    return local


def reconcile(target: str = PP_REF, *, config_path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Read-only reconciliation status for an explicit PP root."""
    if target != PP_REF:
        raise CodingCLIError(f"unsupported PP root: {target}")
    config = _initialize(config_path)
    return reconcile_pp_workflow(todo_rows=todo.todo_list(show_all=True),
                                 **_pp_runtime_binding(config))


def job_log(
    job_id: str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    _initialize(config_path)
    job = state_machine.get_job(job_id)
    if job is None or job.get("queue_name") not in _LOCAL_QUEUES:
        raise CodingCLIError(f"local coding job {job_id} does not exist")
    payload = job.get("payload_json") or job.get("payload")
    if isinstance(payload, dict) and payload.get("queue_name", job.get("queue_name")) == "codex-implement":
        binding = payload.get("plan_binding")
        if isinstance(binding, dict) and isinstance(payload.get("worktree"), str):
            job["coding_state"] = classify(Path(payload["worktree"]), {
                "todo_id": payload.get("todo_id"), "plan_commit": binding.get("plan_commit"),
                "solution_hash": binding.get("solution_hash"), "source_commit": binding.get("source_commit"),
                "source_tree": source_tree(Path(payload["worktree"]), binding.get("source_commit")),
                "actor": payload.get("todo_agent"), "worktree": payload["worktree"],
                "treatment_id": "codex-implement", "treatment_version": str(payload.get("treatment_version", "1")),
            })
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
        elif args.coding_op == "resume":
            result = resume(
                _todo_id(target),
                config_path=config_path,
                source_commit=getattr(args, "source_commit", None),
            )
        elif args.coding_op in {"status", "access-status"}:
            result = (reconcile(target, config_path=config_path) if target == PP_REF else status(
                _todo_id(target) if target is not None else None, config_path=config_path))
        elif args.coding_op == "reconcile":
            result = reconcile(target or PP_REF, config_path=config_path)
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
    resume_parser = commands.add_parser(
        "resume", help="resume one exact RESUMABLE_PARTIAL Todo"
    )
    resume_parser.add_argument("coding_target", metavar="TODO_ID")
    resume_parser.add_argument("--source-commit")
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
