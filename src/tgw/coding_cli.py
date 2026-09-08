"""Operator CLI for TGW's ordinary local Unix-user coding workflow.

This is a thin tgw-lib-local control surface over the continual-harness
orchestrator (LEAF-11-1 / AMENDMENT-20260905). ``tgw coding start <todo>``
hands the Todo body to ``harness_cli.dispatch`` — a fresh disposable coder
session runs implement -> test -> review and, on a clean mechanical pass, the
change squashes to one commit and fast-forwards ``main`` (``main_ref_guard``
is the one gate). The durable ``harness_ledger`` holds every attempt,
remediation round, and finding; git holds only accepted results.

There is no lifecycle store, no Foreman, no queue job, no candidate manifest,
no release materialization, and no Context generation cutover in the
completion path. A tgw-lib coding task is DONE at a clean commit on main with a
passing suite. It has no production, SSH, sudo, remote-provision, or approval
dependency.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from tgw import todo
from tgw.development import harness_cli, harness_ledger, harness_orchestrator
from tgw.development.local_workflow import (
    DEFAULT_CONFIG,
    LocalCodingWorkflowError,
    load_config,
    require_coder_account,
)
from tgw.development.plan_todo_source import PlanTodoSourceError
from tgw.pp_workflow_reconcile import PP_REF
from tgw.pp_workflow_reconcile import reconcile as reconcile_pp_workflow

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PLAN_REPOSITORY = Path("/opt/TGW/library/plans")
_COMMIT_SUBJECT_MAX = 72


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
    harness_ledger.init(config["postgres_dsn"])
    return config


def _todo_id(value: Any) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise CodingCLIError("a positive Todo ID is required") from exc
    if result <= 0:
        raise CodingCLIError("a positive Todo ID is required")
    return result


def _task_id(todo_id: int) -> str:
    return f"todo-{todo_id}"


def _commit_subject(todo_id: int, body: str) -> str:
    """A default commit subject from the Todo — the operator overrides with
    ``--message``. First non-empty line, trimmed, prefixed with the Todo id."""
    first = next((line.strip() for line in body.splitlines() if line.strip()), "")
    first = first[:_COMMIT_SUBJECT_MAX].rstrip()
    return f"Todo {todo_id}: {first}" if first else f"Todo {todo_id}"


def _lease_live(task: dict[str, Any]) -> bool:
    expires = task.get("lease_expires_at")
    if expires is None:
        return False
    now = datetime.now(expires.tzinfo) if getattr(expires, "tzinfo", None) else datetime.now()
    return expires > now


def _ledger_view(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if task is None:
        return None
    return {
        "task_id": task["task_id"],
        "status": task["status"],
        "cursor": task.get("cursor") or {},
        "generation": task.get("generation"),
        "owner": task.get("owner"),
        "lease_live": _lease_live(task),
        "updated_at": task.get("updated_at"),
    }


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
        import re

        if re.fullmatch(r"[0-9a-f]{40}", release_commit) is None:
            raise CodingCLIError("installed coding runtime is not an immutable commit release")
        if source_commit is not None and source_commit != release_commit:
            raise CodingCLIError("requested source differs from installed immutable runtime")
        commit = release_commit
    tree = local._git(repository, "rev-parse", f"{commit}^{{tree}}")
    return {"repository": repository, "source_root": ROOT, "selected_commit": commit,
            "selected_tree": tree, "runtime_mode": mode}


# --------------------------------------------------------------------------- #
# operations
# --------------------------------------------------------------------------- #

def start(
    todo_id: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    source_commit: str | None = None,
    message: str | None = None,
    max_rounds: int = harness_orchestrator.DEFAULT_MAX_ROUNDS,
    executor: str | None = None,
) -> dict[str, Any]:
    """Dispatch one existing Todo through the continual-harness orchestrator."""
    if isinstance(todo_id, str) and todo_id == PP_REF:
        return reconcile(PP_REF, config_path=config_path)

    identifier = _todo_id(todo_id)
    config = _initialize(config_path)
    item = todo.todo_get(identifier)
    if item is None:
        raise CodingCLIError(f"Todo {identifier} does not exist locally")
    if item.get("done_at") is not None:
        raise CodingCLIError(f"Todo {identifier} is already complete")
    body = str(item.get("body") or "").strip()
    if not body:
        raise CodingCLIError(f"Todo {identifier} has no body to implement")

    coding = config["coding"]
    result = harness_cli.dispatch(
        _task_id(identifier),
        message=message or _commit_subject(identifier, body),
        body=body,
        repository=coding["repository_root"],
        worktree_root=coding["worktree_root"],
        max_rounds=max_rounds,
        # the supervised operator CLI runs implement/review as the invoking
        # coder, not the confined tgw-coder service identity
        coder_user=None,
        publisher_user="tgw-harness",
        postgres_dsn=config["postgres_dsn"],
        executor_preference=(tuple(e.strip() for e in executor.split(",") if e.strip())
                             if executor else ()),
    )
    outcome = result.get("outcome")
    return {
        "schema": "tgw-local-coding-start/v2",
        "ok": outcome in {"landed", "already_satisfied", "already_done"},
        "todo_id": identifier,
        "task_id": _task_id(identifier),
        "actor": require_coder_account(),
        "group": "tgw-coders",
        "source_commit_ignored": source_commit,
        "dependencies": {"tgw_prod": False, "ssh": False, "sudo": False,
                         "remote_provision_api": False, "approval_card": False},
        **result,
    }


def resume(
    todo_id: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Resume one Todo. The orchestrator + ledger resume by construction — a
    fresh invocation acquires the (expired) cursor lease and continues from the
    recorded round — so this is ``start`` with the same task id."""
    return start(todo_id, config_path=config_path, source_commit=source_commit)


def status(
    todo_id: int | str | None = None,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    config = _initialize(config_path)
    coding = config["coding"]
    out: dict[str, Any] = {
        "schema": "tgw-local-coding-status/v2",
        "ok": True,
        "actor": require_coder_account(),
        "group": "tgw-coders",
        "database": config["postgres_dsn"],
        "repository_root": coding["repository_root"],
        "worktree_root": coding["worktree_root"],
        "dependencies": {"tgw_prod": False, "ssh": False, "remote_provision_api": False},
    }
    if todo_id is not None and not (isinstance(todo_id, str) and todo_id == PP_REF):
        identifier = _todo_id(todo_id)
        task = harness_ledger.read_task(_task_id(identifier))
        out["todo_id"] = identifier
        out["task"] = _ledger_view(task)
        out["history"] = harness_ledger.history(_task_id(identifier)) if task else []
    elif isinstance(todo_id, str) and todo_id == PP_REF:
        out["reconciliation"] = reconcile(PP_REF, config_path=config_path)
    else:
        out["tasks"] = [_ledger_view(t) for t in harness_ledger.list_tasks()]
    return out


def access_status(
    todo_id: int | None = None,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    full_jobs: bool = False,
) -> dict[str, Any]:
    """Prove the invoking Unix account's tgw-coders binding; expose the ledger
    history only on request."""
    config = _initialize(config_path)
    coding = config["coding"]
    out: dict[str, Any] = {
        "schema": "tgw-local-coding-access-status/v2",
        "ok": True,
        "actor": require_coder_account(),
        "group": "tgw-coders",
        "database": config["postgres_dsn"],
        "repository_root": coding["repository_root"],
        "worktree_root": coding["worktree_root"],
        "jobs_included": full_jobs,
        "dependencies": {"tgw_prod": False, "ssh": False, "sudo": False,
                         "remote_provision_api": False, "approval_card": False},
    }
    if todo_id is not None:
        identifier = _todo_id(todo_id)
        task = harness_ledger.read_task(_task_id(identifier))
        out["todo_id"] = identifier
        out["task"] = _ledger_view(task)
        if full_jobs and task is not None:
            out["history"] = harness_ledger.history(_task_id(identifier))
    return out


def reconcile(target: str = PP_REF, *, config_path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Read-only reconciliation status for an explicit PP root."""
    if target != PP_REF:
        raise CodingCLIError(f"unsupported PP root: {target}")
    config = _initialize(config_path)
    return reconcile_pp_workflow(
        todo_rows=todo.todo_list(show_all=True), **_pp_runtime_binding(config),
    )


def job_log(
    job_id: str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Return one coding task's durable ledger row and its full append-only
    history — the whole attempt / remediation / finding record, no git
    archaeology (L11.1.LEDGER-AND-GIT-DISCIPLINE)."""
    _initialize(config_path)
    task_id = job_id if not str(job_id).isdigit() else _task_id(int(job_id))
    task = harness_ledger.read_task(task_id)
    if task is None:
        raise CodingCLIError(f"no coding task {task_id!r} in the harness ledger")
    return {
        "schema": "tgw-local-coding-log/v2",
        "ok": True,
        "task_id": task_id,
        "task": _ledger_view(task),
        "history": harness_ledger.history(task_id),
    }


def stop(
    job_id: str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Record an operator stop request against one coding task.

    A running orchestrator holds the task's cursor lease; this appends an
    ``operator_correction`` entry to the durable history rather than racing the
    lease. A live owner finishes its current round and hands off; with no live
    owner the task is simply left blocked for inspection."""
    _initialize(config_path)
    task_id = job_id if not str(job_id).isdigit() else _task_id(int(job_id))
    task = harness_ledger.read_task(task_id)
    if task is None:
        raise CodingCLIError(f"no coding task {task_id!r} in the harness ledger")
    if task["status"] in {"done", "abandoned"}:
        raise CodingCLIError(f"coding task {task_id} is already {task['status']}")
    harness_ledger.append(
        task_id, "operator_correction",
        {"stop_requested": "stopped by the local operator CLI"},
    )
    live = _lease_live(task)
    return {
        "schema": "tgw-local-coding-stop/v2",
        "ok": True,
        "task_id": task_id,
        "stop_state": (
            "recorded_running_owner_completes_round" if live else "recorded_no_live_owner"
        ),
        "task": _ledger_view(task),
    }


# --------------------------------------------------------------------------- #
# argv dispatch
# --------------------------------------------------------------------------- #

def _target(args: argparse.Namespace) -> str | None:
    return getattr(args, "coding_target", None) or getattr(args, "request_id", None)


def run(args: argparse.Namespace) -> int:
    try:
        config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG)
        target = _target(args)
        if args.coding_op == "start":
            value = target if target == PP_REF else _todo_id(target or getattr(args, "todo_id", None))
            result = start(
                value,
                config_path=config_path,
                source_commit=getattr(args, "source_commit", None),
                message=getattr(args, "message", None),
                executor=getattr(args, "executor", None),
            )
        elif args.coding_op == "resume":
            result = resume(
                _todo_id(target),
                config_path=config_path,
                source_commit=getattr(args, "source_commit", None),
            )
        elif args.coding_op == "status":
            result = status(
                target if target == PP_REF else (_todo_id(target) if target is not None else None),
                config_path=config_path,
            )
        elif args.coding_op == "access-status":
            result = access_status(
                _todo_id(target) if target is not None else None,
                config_path=config_path,
                full_jobs=bool(getattr(args, "full_jobs", False)),
            )
        elif args.coding_op == "reconcile":
            result = reconcile(target or PP_REF, config_path=config_path)
        elif args.coding_op == "log":
            if not target:
                raise CodingCLIError("log requires a coding task ID")
            result = job_log(target, config_path=config_path)
        elif args.coding_op == "stop":
            if not target:
                raise CodingCLIError("stop requires a coding task ID")
            result = stop(target, config_path=config_path)
        else:
            raise CodingCLIError(f"unknown coding operation: {args.coding_op}")
        print(json.dumps(result, sort_keys=True, default=_json_default))
        return 0 if result.get("ok", True) else 1
    except (
        CodingCLIError, LocalCodingWorkflowError, PlanTodoSourceError, OSError, ValueError,
    ) as exc:
        print(json.dumps({
            "schema": "tgw-local-coding-error/v1",
            "ok": False,
            "operation": getattr(args, "coding_op", None),
            "target": _target(args),
            "error": str(exc),
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="tgw coding")
    root.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    commands = root.add_subparsers(dest="coding_op", required=True)

    start_parser = commands.add_parser("start", help="dispatch one Todo through the harness orchestrator")
    start_parser.add_argument("coding_target", metavar="TODO_ID|PP_REF")
    start_parser.add_argument("--source-commit")
    start_parser.add_argument("--message", help="commit subject for the accepted task (default: from the Todo)")
    start_parser.add_argument("--executor", help="ordered executor preference (e.g. claude,codex or stub); "
                              "empty = model selector / default chain")

    resume_parser = commands.add_parser("resume", help="resume one Todo from its ledger cursor")
    resume_parser.add_argument("coding_target", metavar="TODO_ID")
    resume_parser.add_argument("--source-commit")

    status_parser = commands.add_parser("status", help="show coding tasks from the harness ledger")
    status_parser.add_argument("coding_target", metavar="TODO_ID|PP_REF", nargs="?")

    reconcile_parser = commands.add_parser("reconcile", help="read-only PP reconciliation")
    reconcile_parser.add_argument("coding_target", metavar="PP_REF", nargs="?", default=PP_REF)

    log_parser = commands.add_parser("log", help="show one coding task's full ledger history")
    log_parser.add_argument("coding_target", metavar="TASK_ID|TODO_ID")

    stop_parser = commands.add_parser("stop", help="record an operator stop request for one coding task")
    stop_parser.add_argument("coding_target", metavar="TASK_ID|TODO_ID")

    access = commands.add_parser("access-status", help="prove the local Unix/group binding")
    access.add_argument("coding_target", metavar="TODO_ID", nargs="?")
    access.add_argument("--full-jobs", action="store_true",
                        help="include the full ledger history instead of the task summary only")
    return root


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
