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
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import psycopg2.extras

from tgw import todo
from tgw.development import coding_lifecycle
from tgw.development.coding_lifecycle import LifecycleStore
from tgw.development.foreman import ForemanConfig, TickResult, tick
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
    LEGACY_1747,
    classify,
    migrate_todo_1747,
    preservation_manifest,
    source_tree,
    validate_implementation_lineage,
)
from tgw.development.plan_binding import parse_plan_binding, validate_plan_binding
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.development.plan_todo_source import PlanTodoSourceError
from tgw.development.plan_todo_source import resolve as resolve_plan_todo
from tgw.development.profiles import CODING_READY_FOR_ADMISSION
from tgw.development.treatments import (
    CLAUDE_REVIEW,
    CODEX_IMPLEMENT,
    CONTROLLER_VERIFY,
)
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
_LOCAL_QUEUES = ("codex-implement", "controller-verify", "claude-review")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_LEGACY_1747_JOBS = (
    ("dfdfd643-312e-46ef-a33c-1542340e9b9c", "partial"),
    ("2b1f9f04-a09f-489e-aade-f21ab1e4aaa9", "failed"),
)
_ACCIDENTAL_1747_SOURCE = "bed4a66a55173d3994f20395ee73d423a874c6d3"
_ACCIDENTAL_1747_WORKTREE = Path(
    "/opt/TGW/var/worktrees/todo-1747-plan-7b3ab5b80fe10535a8ea38c7"
)


class CodingCLIError(RuntimeError):
    """The requested local coding operation is invalid or unavailable."""


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _durable_json(value: Any) -> Any:
    """Normalize database timestamps and UUIDs before journaling a receipt."""
    return json.loads(json.dumps(value, sort_keys=True, default=_json_default))


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


def _legacy_1747_binding(item: dict[str, Any], jobs: list[dict[str, Any]]) -> tuple[dict[str, Any], str | None]:
    """Select the historical binding and exact accidental note without effects."""
    if item.get("id") != 1747 or item.get("agent") != "codex":
        raise CodingCLIError("Todo 1747 historical continuation owner mismatch")
    observed = item.get("status_note")
    plans = []
    for job in jobs:
        payload = job.get("payload")
        plan = payload.get("plan_binding") if isinstance(payload, dict) else None
        plans.append(validate_plan_binding(plan, todo_id=1747))
    if len(plans) != 2 or plans[0] != plans[1]:
        raise CodingCLIError("Todo 1747 durable jobs do not share one historical binding")
    historical = plans[0]
    if historical.get("worktree") != str(LEGACY_1747):
        raise CodingCLIError("Todo 1747 durable jobs do not bind the historical worktree")
    accidental = parse_plan_binding(observed, todo_id=1747)
    if accidental == historical:
        return historical, None
    if (
        not isinstance(observed, str)
        or accidental is None
        or accidental.get("source_commit") != _ACCIDENTAL_1747_SOURCE
        or accidental.get("worktree") != str(_ACCIDENTAL_1747_WORKTREE)
        or accidental.get("worktree_identity", {}).get("created") is not True
        or accidental.get("worktree_identity", {}).get("head") != _ACCIDENTAL_1747_SOURCE
        or accidental.get("worktree_identity", {}).get("actor") != "codex"
    ):
        raise CodingCLIError("Todo 1747 status note is not the observed accidental bed4a66a binding")
    return historical, observed


def _resume_attempt_binding(todo_id: int, item: dict[str, Any]) -> dict[str, Any]:
    """Discover and validate one exact historical attempt without effects."""
    observed: list[dict[str, Any]] = []
    for job in _jobs(todo_id, limit=100):
        payload = job.get("payload_json") or job.get("payload")
        if not isinstance(payload, dict) or "plan_binding" not in payload:
            continue
        try:
            observed.append(validate_plan_binding(payload["plan_binding"], todo_id=todo_id))
        except (TypeError, ValueError) as exc:
            raise CodingCLIError(
                f"Todo {todo_id} historical job has a malformed Plan binding"
            ) from exc
    note = parse_plan_binding(item.get("status_note"), todo_id=todo_id)
    if note is not None:
        observed.append(validate_plan_binding(note, todo_id=todo_id))
    identities = {
        json.dumps(value, sort_keys=True, separators=(",", ":")): value
        for value in observed
    }
    if len(identities) != 1:
        reason = "absent" if not identities else "ambiguous"
        raise CodingCLIError(
            f"Todo {todo_id} historical coding attempt is {reason}; zero effects"
        )
    binding = next(iter(identities.values()))
    worktree = Path(binding["worktree"])
    expected = {
        "todo_id": todo_id,
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "source_commit": binding["source_commit"],
        "source_tree": source_tree(worktree, binding["source_commit"]),
        "actor": item.get("agent") or "codex",
        "worktree": str(worktree),
        "treatment_id": "codex-implement",
        "treatment_version": "1",
    }
    state = classify(worktree, expected)
    if state.get("state") != "RESUMABLE_PARTIAL":
        raise CodingCLIError(
            f"Todo {todo_id} historical coding attempt is {state.get('state')}; "
            "coding resume requires RESUMABLE_PARTIAL; zero effects"
        )
    return binding


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
    lifecycle_job_binding: Mapping[str, Any] | None = None,
    lifecycle_stage: str | None = None,
    dispatch_jobs: bool = True,
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
            foreman = TickResult(dispatched=0) if not dispatch_jobs else tick(
                ForemanConfig(
                    coding_config=dict(coding),
                    treatments=(CODEX_IMPLEMENT, CONTROLLER_VERIFY),
                    receipt_backed_conditions=frozenset({"tested", "linted"}),
                    lifecycle_bindings={},
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

    legacy_jobs: list[dict[str, Any]] | None = None
    accidental_note: str | None = None
    if resume_only and todo_id == 1747 and source_commit is None:
        if source_commit is not None:
            raise CodingCLIError("Todo 1747 historical resume does not accept a current-source override")
        legacy_jobs = _legacy_1747_jobs()
        historical, accidental_note = _legacy_1747_binding(item, legacy_jobs)
        binding = {"binding": historical}
    elif resume_only and source_commit is None:
        binding = {"binding": _resume_attempt_binding(todo_id, item)}
    else:
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
    resume_noop = False
    with exclusive_worktree_lease(worktree):
        if legacy_jobs is not None:
            migration = str(
                migrate_todo_1747(worktree, expected_attempt, legacy_jobs)
            )
            if accidental_note is not None:
                restored = json.dumps(binding["binding"], sort_keys=True, separators=(",", ":"))
                changed = todo.todo_compare_and_set_status_note(
                    1747, accidental_note, restored, suppress_plan_render=True,
                )
                if not changed.get("ok"):
                    raise CodingCLIError("Todo 1747 status-note compare-and-set refused")
        resume_state = classify(worktree, expected_attempt)
        resume_bindings: dict[int, dict[str, str]] = {}
        if resume_state["state"] == "RESUMABLE_PARTIAL":
            resume_bindings[todo_id] = {
                "resume_of": resume_state["resume_of"],
                "resume_fingerprint": resume_state["fingerprint"],
            }
        elif resume_only and legacy_jobs is not None and resume_state["state"] == "CLOSED_CANDIDATE":
            resume_noop = True
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
    result = TickResult(dispatched=0) if resume_noop or not dispatch_jobs else tick(
        ForemanConfig(
            coding_config=dict(coding),
            treatments=(CODEX_IMPLEMENT, CONTROLLER_VERIFY),
            receipt_backed_conditions=frozenset({"tested", "linted"}),
            resume_bindings=resume_bindings,
            lifecycle_bindings=(
                {todo_id: dict(lifecycle_job_binding)}
                if lifecycle_job_binding is not None
                else {}
            ),
            lifecycle_rebind=(
                {
                    todo_id: {
                        "implementation": "codex-implement",
                        "controller": "controller-verify",
                        "review": "claude-review",
                    }[lifecycle_stage]
                }
                if lifecycle_job_binding is not None
                and lifecycle_stage in {"implementation", "controller", "review"}
                else {}
            ),
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
    identifier = _todo_id(todo_id)
    config = _initialize(config_path)
    lifecycle_root = config["coding"].get("lifecycle_root")
    record = (
        LifecycleStore(lifecycle_root).find(identifier)
        if lifecycle_root is not None
        else None
    )
    result = start(
        identifier,
        config_path=config_path,
        solution_path=solution_path,
        source_commit=source_commit,
        resume_only=True,
        lifecycle_job_binding=(
            coding_lifecycle.job_binding(record) if record is not None else None
        ),
        lifecycle_stage="implementation" if record is not None else None,
    )
    if record is None:
        return result
    store = LifecycleStore(lifecycle_root)
    reopened = coding_lifecycle.request_resume(
        store,
        record["root_id"],
        receipt={
            "schema": "tgw-local-coding-lifecycle-resume/v1",
            "root_id": record["root_id"],
            "binding_hash": record["binding"]["binding_hash"],
            "todo_id": identifier,
            "job_ids": sorted(str(row["job_id"]) for row in result.get("jobs", [])),
            "coding_state": result.get("coding_state"),
        },
    )
    pid = coding_lifecycle.spawn(reopened["root_id"], config_path=config_path)
    return {
        **result,
        "lifecycle_root_id": reopened["root_id"],
        "lifecycle_state": reopened["state"],
        "supervisor_pid": pid,
    }


def _plan_binding_for_todo(identifier: int) -> tuple[dict[str, Any], dict[str, Any]]:
    item = todo.todo_get(identifier)
    if item is None:
        raise CodingCLIError(f"Todo {identifier} does not exist")
    binding = parse_plan_binding(item.get("status_note"), todo_id=identifier)
    if binding is None:
        raise CodingCLIError(f"Todo {identifier} has no exact Plan/Todo binding")
    return item, validate_plan_binding(binding, todo_id=identifier)


def lifecycle_start(
    target: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    solution_path: Path | str = DEFAULT_SOLUTION,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Create/reuse one durable root and return before coding work begins."""
    if target == PP_REF:
        raise CodingCLIError(
            "PP lifecycle requires a composite multi-leaf execution-card binding; "
            "the current Plan/Todo card schema exposes only leaf bindings"
        )
    identifier = _todo_id(target)
    config = _initialize(config_path)
    solution = __import__(
        "tgw.development.local_workflow", fromlist=["load_solution"]
    ).load_solution(solution_path)
    runtime = _pp_runtime_binding(config, source_commit)
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    prior = store.find(identifier)
    if prior is not None:
        if (
            prior["binding"]["source_commit"] != runtime["selected_commit"]
            or prior["binding"]["source_tree"] != runtime["selected_tree"]
        ):
            prior = coding_lifecycle.report_stale_source(
                store,
                prior["root_id"],
                source_commit=runtime["selected_commit"],
                source_tree=runtime["selected_tree"],
            )
        pid = None
        if prior["state"] not in coding_lifecycle.TERMINAL:
            pid = coding_lifecycle.spawn(prior["root_id"], config_path=config_path)
        return {
            "schema": "tgw-local-coding-lifecycle-start/v1",
            "ok": prior["state"] != "REMEDIATION_REQUIRED",
            "root_id": prior["root_id"],
            "target": str(identifier),
            "state": prior["state"],
            "binding_hash": prior["binding"]["binding_hash"],
            "supervisor_pid": pid,
            "returns_immediately": True,
            "dependencies": prior["boundaries"],
            **({"failure": prior["failure"]} if prior.get("failure") else {}),
        }
    if todo.todo_get(identifier) is None:
        projection = resolve_plan_todo(
            identifier,
            repository=config.get("plan_repository_root", DEFAULT_PLAN_REPOSITORY),
            approved_commit=solution["plan_commit"],
        )
        todo.todo_import_projection(projection)
    # Bind/allocate before journaling because the complete approved card
    # includes the exact worktree identity.  Job dispatch remains disabled;
    # only the detached supervisor receives the lifecycle fence.
    start(
        identifier,
        config_path=config_path,
        solution_path=solution_path,
        source_commit=runtime["selected_commit"],
        dispatch_jobs=False,
    )
    _item, plan = _plan_binding_for_todo(identifier)
    if (
        plan["plan_commit"] != solution["plan_commit"]
        or plan["solution_hash"] != solution["solution_hash"]
        or plan["source_commit"] != runtime["selected_commit"]
    ):
        raise CodingCLIError("Todo lifecycle Plan/solution/source binding is stale")
    binding = coding_lifecycle.build_binding(
        target=identifier,
        plan_binding=plan,
        source_tree=runtime["selected_tree"],
    )
    record = coding_lifecycle.create(
        store,
        target=identifier,
        binding=binding,
    )
    pid = None
    if record["state"] not in coding_lifecycle.TERMINAL:
        pid = coding_lifecycle.spawn(record["root_id"], config_path=config_path)
    return {
        "schema": "tgw-local-coding-lifecycle-start/v1", "ok": True,
        "root_id": record["root_id"], "target": str(identifier), "state": record["state"],
        "binding_hash": record["binding"]["binding_hash"],
        "supervisor_pid": pid, "returns_immediately": True,
        "dependencies": record["boundaries"],
    }


def _stage(
    record: Mapping[str, Any], stage: str, outcome: str, *,
    receipt: Mapping[str, Any] | None = None,
    reason: str | None = None,
    job_ids: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    return coding_lifecycle.stage_result(
        record,
        stage,
        outcome,
        receipt=receipt,
        reason=reason,
        job_ids=job_ids,
    )


def _receipt_file(path: Path) -> tuple[dict[str, Any], str]:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise CodingCLIError(f"coding receipt {path.name} is not an immutable regular file")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodingCLIError(f"coding receipt {path.name} is unavailable or malformed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not isinstance(value, dict):
        raise CodingCLIError(f"coding receipt {path.name} is not an object")
    return value, "sha256:" + hashlib.sha256(raw).hexdigest()


def _bound_jobs(record: Mapping[str, Any], queue_name: str) -> list[dict[str, Any]]:
    identifier = int(record["target"])
    expected_job_binding = coding_lifecycle.job_binding(record)
    expected_plan = record["binding"]["plan_todo_binding"]
    result = []
    for row in _jobs(identifier, limit=250):
        if row.get("queue_name") != queue_name:
            continue
        payload = row.get("payload_json") or row.get("payload")
        if not isinstance(payload, Mapping):
            continue
        observed = payload.get("coding_lifecycle")
        if isinstance(observed, Mapping) and observed.get("root_id") == record["root_id"]:
            coding_lifecycle.validate_job_binding(record, observed)
            if payload.get("plan_binding") != expected_plan:
                raise CodingCLIError(
                    f"{queue_name} job {row['job_id']} has stale Plan/Todo binding"
                )
            result.append(row)
        elif observed == expected_job_binding:
            # Kept for clarity: exact values necessarily enter the branch above.
            result.append(row)
    return result


def _queue_evidence(
    record: Mapping[str, Any], *, stage: str, queue_name: str,
    receipt_name: str, dispatch: Any,
) -> dict[str, Any]:
    rows = _bound_jobs(record, queue_name)
    dispatch_result = None
    if not rows:
        dispatch_result = dispatch()
        rows = _bound_jobs(record, queue_name)
    ids = [str(row["job_id"]) for row in rows]
    active = [
        row for row in rows
        if row.get("state") in {"queued", "leased", "running", "retry_wait"}
    ]
    terminal = [row for row in rows if row.get("state") in {
        "succeeded", "failed", "dead_letter", "cancelled"
    }]
    refused = (
        isinstance(dispatch_result, Mapping)
        and dispatch_result.get("ok") is False
    ) or (
        isinstance(dispatch_result, TickResult)
        and (
            dispatch_result.errors > 0
            or dispatch_result.refused_plan_binding > 0
        )
    )
    if not rows and refused:
        return _stage(
            record,
            stage,
            "remediation",
            reason=f"{queue_name} exact typed dispatch was refused",
        )
    if len(rows) > 1:
        return _stage(
            record, stage, "remediation",
            reason=f"duplicate {queue_name} jobs exist for the exact lifecycle root",
            job_ids=ids,
        )
    if active or not terminal:
        return _stage(
            record, stage, "waiting",
            reason=f"{queue_name} exact job is pending",
            job_ids=ids,
        )
    row = terminal[0]
    if row.get("state") != "succeeded":
        return _stage(
            record, stage, "failed",
            reason=f"{queue_name} exact job ended {row.get('state')}",
            job_ids=ids,
        )
    payload = row.get("payload_json") or row.get("payload")
    durable_result = payload.get("result") if isinstance(payload, Mapping) else None
    if not isinstance(durable_result, Mapping):
        return _stage(
            record, stage, "remediation",
            reason=f"{queue_name} succeeded without a durable result",
            job_ids=ids,
        )
    worktree = Path(record["binding"]["worktree"])
    file_receipt, file_sha256 = _receipt_file(worktree / receipt_name)
    if dict(durable_result) != file_receipt:
        return _stage(
            record, stage, "remediation",
            reason=f"{queue_name} queue result and immutable receipt differ",
            job_ids=ids,
        )
    coding_lifecycle.validate_job_binding(
        record, file_receipt.get("coding_lifecycle")
    )
    if (
        file_receipt.get("status") != "PASS"
        or file_receipt.get("outcome") != "satisfied"
        or file_receipt.get("treatment_id") != queue_name
        or file_receipt.get("plan_binding")
        != record["binding"]["plan_todo_binding"]
    ):
        return _stage(
            record, stage, "remediation",
            reason=f"{queue_name} receipt is stale or contradictory",
            job_ids=ids,
        )
    if stage == "review":
        candidate_receipt = record["effects"]["candidate"]["receipt"]
        coding_lifecycle.validate_candidate_job_binding(
            file_receipt.get("coding_candidate"),
            lifecycle_binding=coding_lifecycle.job_binding(record),
            commit=candidate_receipt["commit"],
            tree=candidate_receipt["tree"],
        )
    evidence = {
        "schema": "tgw-local-coding-queue-evidence/v1",
        "root_id": record["root_id"],
        "binding_hash": record["binding"]["binding_hash"],
        "job_id": str(row["job_id"]),
        "queue_name": queue_name,
        "job_state": "succeeded",
        "attempt_count": row.get("attempt_count"),
        "receipt_file": str(worktree / receipt_name),
        "receipt_file_sha256": file_sha256,
        "result": file_receipt,
    }
    return _stage(record, stage, "satisfied", receipt=evidence, job_ids=ids)


def _doctor_receipt(
    root: Path, *, operation: str, predicate: Any,
) -> dict[str, Any] | None:
    if not root.is_dir() or root.is_symlink():
        return None
    for path in sorted(root.glob(f"*-{operation}.json"), reverse=True):
        try:
            value, file_sha256 = _receipt_file(path)
        except CodingCLIError:
            continue
        unsigned = dict(value)
        claimed = unsigned.pop("receipt_sha256", None)
        canonical_hash = "sha256:" + hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if (
            value.get("schema") == "tgw-local-doctor-repair-receipt/v1"
            and value.get("operation") == operation
            and value.get("error") is None
            and claimed == canonical_hash
            and predicate(value)
        ):
            return {
                "path": str(path),
                "file_sha256": file_sha256,
                "receipt_sha256": claimed,
                "receipt": value,
            }
    return None


def supervise(identity: str, *, config_path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Resume the existing local dispatcher under the durable root journal."""
    config = _initialize(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])

    def implementation(record: dict[str, Any]) -> dict[str, Any]:
        return _queue_evidence(
            record,
            stage="implementation",
            queue_name="codex-implement",
            receipt_name="implementation-receipt.json",
            dispatch=lambda: start(
                int(record["target"]),
                config_path=config_path,
                source_commit=record["binding"]["source_commit"],
                lifecycle_job_binding=coding_lifecycle.job_binding(record),
                lifecycle_stage="implementation",
            ),
        )

    def controller(record: dict[str, Any]) -> dict[str, Any]:
        result = _queue_evidence(
            record,
            stage="controller",
            queue_name="controller-verify",
            receipt_name="controller-harness-receipt.json",
            dispatch=lambda: start(
                int(record["target"]),
                config_path=config_path,
                source_commit=record["binding"]["source_commit"],
                lifecycle_job_binding=coding_lifecycle.job_binding(record),
                lifecycle_stage="controller",
            ),
        )
        if result.get("outcome") == "satisfied":
            implementation = record.get("effects", {}).get("implementation", {})
            recovered = [
                item
                for item in (
                    implementation.get("receipt", {})
                    .get("result", {})
                    .get("artifacts", [])
                )
                if isinstance(item, Mapping)
                and item.get("kind") == "recovered_attempt"
            ]
            expected_attempt = (
                recovered[0].get("attempt_hash") if len(recovered) == 1 else None
            )
            observed_attempt = result["receipt"]["result"].get(
                "implementation_attempt_hash"
            )
            # The worker already validates lineage; the lifecycle also binds
            # the controller to the implementation stage's exact attempt when
            # that identity is present in the implementation receipt.
            if expected_attempt is not None and observed_attempt != expected_attempt:
                return _stage(
                    record,
                    "controller",
                    "remediation",
                    reason="controller receipt binds another implementation attempt",
                    job_ids=result.get("job_ids", []),
                )
        return result

    def candidate(record: dict[str, Any]) -> dict[str, Any]:
        target = int(record["target"])
        item, binding = _plan_binding_for_todo(target)
        if binding != record["binding"]["plan_todo_binding"]:
            return _stage(
                record, "candidate", "remediation",
                reason="candidate Plan/source/card binding is stale",
            )
        worktree = Path(record["binding"]["worktree"])
        implementation_receipt, implementation_sha256 = _receipt_file(
            worktree / "implementation-receipt.json"
        )
        expected = {
            "todo_id": target, "plan_commit": binding["plan_commit"],
            "solution_hash": binding["solution_hash"], "source_commit": binding["source_commit"],
            "source_tree": record["binding"]["source_tree"],
            "actor": item.get("agent") or "codex", "worktree": str(worktree),
            "treatment_id": "codex-implement", "treatment_version": "1",
        }
        observed = classify(worktree, expected)
        if observed["state"] != "CLOSED_CANDIDATE":
            outcome = "resumable_partial" if observed["state"] == "RESUMABLE_PARTIAL" else "remediation"
            return _stage(
                record,
                "candidate",
                outcome,
                reason=f"candidate is {observed['state']}",
                receipt=observed,
            )
        source = observed["source"]
        try:
            latest = validate_implementation_lineage(
                worktree,
                base_commit=binding["source_commit"],
                candidate_commit=source["head"],
                candidate_tree=source["tree"],
                receipt=implementation_receipt,
                expected=expected,
            )
        except ValueError as exc:
            return _stage(
                record, "candidate", "remediation",
                reason=f"candidate implementation lineage is stale: {exc}",
            )
        return _stage(
            record,
            "candidate",
            "satisfied",
            receipt={
                "schema": "tgw-local-coding-candidate-evidence/v1",
                "root_id": record["root_id"],
                "binding_hash": record["binding"]["binding_hash"],
                "worktree": str(worktree),
                "commit": source["head"],
                "tree": source["tree"],
                "classification": observed["state"],
                "implementation_attempt_hash": latest["attempt_hash"],
                "implementation_receipt_sha256": implementation_sha256,
            },
        )

    def review(record: dict[str, Any]) -> dict[str, Any]:
        command = config["coding"].get("commands", {}).get("claude-review")
        allowed = config["coding"].get("allowed_runners", [])
        if (
            not isinstance(command, list)
            or not command
            or command[0] not in allowed
        ):
            return _stage(
                record,
                "review",
                "remediation",
                reason=(
                    "independent review queue exists but no allowed typed review "
                    "runner is installed; zero review claim"
                ),
            )

        def dispatch() -> TickResult:
            return tick(
                ForemanConfig(
                    coding_config=dict(config["coding"]),
                    goal_profile=CODING_READY_FOR_ADMISSION,
                    treatments=(CLAUDE_REVIEW,),
                    receipt_backed_conditions=frozenset(
                        {"tested", "linted", "controller_verified"}
                    ),
                    lifecycle_bindings={
                        int(record["target"]): coding_lifecycle.job_binding(record)
                    },
                    lifecycle_rebind={
                        int(record["target"]): "claude-review"
                    },
                ),
                todo_ids={int(record["target"])},
            )

        return _queue_evidence(
            record,
            stage="review",
            queue_name="claude-review",
            receipt_name="review-receipt.json",
            dispatch=dispatch,
        )

    def integration(record: dict[str, Any]) -> dict[str, Any]:
        candidate_receipt = record["effects"]["candidate"]["receipt"]
        candidate_commit = candidate_receipt["commit"]
        candidate_tree = candidate_receipt["tree"]
        repository = Path(config["coding"]["repository_root"])
        local = __import__("tgw.development.local_workflow", fromlist=["_git"])
        lock = store._open_lock(record["root_id"], ".integration.lock")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            current = local._git(repository, "rev-parse", "HEAD")
            current_tree = local._git(repository, "rev-parse", "HEAD^{tree}")
            if current == candidate_commit and current_tree == candidate_tree:
                mode = "already-integrated"
            else:
                if current != record["binding"]["source_commit"]:
                    return _stage(
                        record, "integration", "remediation",
                        reason="canonical source advanced outside the exact lifecycle root",
                    )
                if local._git(repository, "status", "--porcelain=v1"):
                    return _stage(
                        record, "integration", "remediation",
                        reason="canonical source is dirty; exact fast-forward refused",
                    )
                ancestry = subprocess.run(
                    [
                        "git", "-c", f"safe.directory={repository.resolve()}",
                        "merge-base", "--is-ancestor", current, candidate_commit,
                    ],
                    cwd=repository,
                    check=False,
                    capture_output=True,
                )
                if ancestry.returncode != 0:
                    return _stage(
                        record, "integration", "remediation",
                        reason="reviewed candidate is not a canonical fast-forward",
                    )
                completed = subprocess.run(
                    [
                        "git", "-c", f"safe.directory={repository.resolve()}",
                        "merge", "--ff-only", candidate_commit,
                    ],
                    cwd=repository,
                    check=False,
                    text=True,
                    capture_output=True,
                )
                if completed.returncode != 0:
                    return _stage(
                        record, "integration", "remediation",
                        reason=f"exact fast-forward failed: {completed.stderr[-300:]}",
                    )
                mode = "fast-forwarded"
            observed_commit = local._git(repository, "rev-parse", "HEAD")
            observed_tree = local._git(repository, "rev-parse", "HEAD^{tree}")
            if (observed_commit, observed_tree) != (candidate_commit, candidate_tree):
                return _stage(
                    record, "integration", "remediation",
                    reason="canonical source differs after integration",
                )
            return _stage(
                record,
                "integration",
                "satisfied",
                receipt={
                    "schema": "tgw-local-coding-integration/v1",
                    "root_id": record["root_id"],
                    "binding_hash": record["binding"]["binding_hash"],
                    "review_receipt_hash": record["effects"]["review"]["receipt_hash"],
                    "controller_receipt_hash": record["effects"]["controller"]["receipt_hash"],
                    "base_commit": record["binding"]["source_commit"],
                    "candidate_commit": candidate_commit,
                    "candidate_tree": candidate_tree,
                    "mode": mode,
                },
            )
        finally:
            lock.close()

    def materialization(record: dict[str, Any]) -> dict[str, Any]:
        candidate_receipt = record["effects"]["candidate"]["receipt"]
        evidence = _doctor_receipt(
            Path(config["coding"]["doctor_receipt_root"]),
            operation="runtime-materialization",
            predicate=lambda value: value.get("actor") == "root"
            and value.get("after", {}).get("verified") is True
            and value.get("after", {}).get("commit") == candidate_receipt["commit"]
            and value.get("after", {}).get("tree") == candidate_receipt["tree"],
        )
        if evidence is None:
            return _stage(
                record,
                "materialization",
                "waiting",
                reason=(
                    "awaiting root-owned Doctor runtime-materialization receipt; "
                    "ordinary tgw-coders supervision has no authority to create it"
                ),
            )
        return _stage(record, "materialization", "satisfied", receipt=evidence)

    def live_verification(record: dict[str, Any]) -> dict[str, Any]:
        candidate_receipt = record["effects"]["candidate"]["receipt"]
        evidence = _doctor_receipt(
            Path(config["coding"]["doctor_receipt_root"]),
            operation="runtime",
            predicate=lambda value: value.get("actor") == "root"
            and value.get("after", {}).get("release_tree", {}).get("verified") is True
            and value.get("after", {}).get("release_tree", {}).get("commit")
            == candidate_receipt["commit"]
            and value.get("after", {}).get("release_tree", {}).get("tree")
            == candidate_receipt["tree"],
        )
        if evidence is None:
            return _stage(
                record,
                "live_verification",
                "waiting",
                reason="awaiting exact root-owned Doctor runtime selection/readback receipt",
            )
        return _stage(record, "live_verification", "satisfied", receipt=evidence)

    def terminal_publication(record: dict[str, Any]) -> dict[str, Any]:
        candidate_receipt = record["effects"]["candidate"]["receipt"]
        evidence = _doctor_receipt(
            Path(config["coding"]["doctor_receipt_root"]),
            operation="context",
            predicate=lambda value: value.get("actor") == "root"
            and value.get("after", {}).get("snapshot", {}).get("plan_commit")
            == record["binding"]["plan_commit"]
            and value.get("after", {}).get("snapshot", {}).get("source_commit")
            == candidate_receipt["commit"]
            and value.get("after", {}).get("snapshot", {}).get("source_tree")
            == candidate_receipt["tree"],
        )
        if evidence is None:
            return _stage(
                record,
                "terminal_publication",
                "publication_unavailable",
                reason=(
                    "Context publication unavailable; exact terminal Doctor receipt "
                    "will be retried without a limit"
                ),
            )
        return _stage(record, "terminal_publication", "satisfied", receipt=evidence)

    def operator_notification(record: dict[str, Any]) -> dict[str, Any]:
        return _stage(
            record,
            "operator_notification",
            "satisfied",
            receipt={
                "schema": "tgw-local-coding-operator-notification/v1",
                "root_id": record["root_id"],
                "binding_hash": record["binding"]["binding_hash"],
                "candidate_receipt_hash": record["effects"]["candidate"]["receipt_hash"],
                "live_verification_receipt_hash": record["effects"]["live_verification"]["receipt_hash"],
                "context_publication_pending": record["publication"]["pending"],
                "readback_command": f"tgw coding readback {record['root_id']}",
                "accept_command": f"tgw coding accept {record['root_id']}",
                "reject_command": f"tgw coding reject {record['root_id']}",
            },
        )

    def operator_readback(record: dict[str, Any]) -> dict[str, Any]:
        readback = record.get("operator", {}).get("readback")
        if not isinstance(readback, Mapping):
            return _stage(
                record,
                "operator_readback",
                "waiting",
                reason="operator notification is durable; explicit readback is pending",
            )
        return _stage(
            record, "operator_readback", "satisfied", receipt=dict(readback)
        )

    return coding_lifecycle.advance(store, identity, {
        "implementation": implementation,
        "controller": controller,
        "candidate": candidate,
        "review": review,
        "integration": integration,
        "materialization": materialization,
        "live_verification": live_verification,
        "terminal_publication": terminal_publication,
        "operator_notification": operator_notification,
        "operator_readback": operator_readback,
    })


def lifecycle_status(identity: int | str, *, config_path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    config = _initialize(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    record = store.get(identity) if isinstance(identity, str) and identity.startswith("coding:") else store.find(identity)
    if record is None:
        raise CodingCLIError(f"coding lifecycle {identity} does not exist")
    target = record["binding"]["target"]
    jobs = [] if target == PP_REF else _jobs(int(target), limit=100)
    effects = record.get("effects", {})

    def receipt(stage: str) -> Any:
        evidence = effects.get(stage)
        return evidence.get("receipt") if isinstance(evidence, Mapping) else None

    candidate_evidence = receipt("candidate") or {}
    return {
        **record,
        "schema": "tgw-local-coding-lifecycle-status/v1",
        "todo_or_pp": target,
        "jobs": jobs,
        "worktree": candidate_evidence.get("worktree") or record["binding"]["worktree"],
        "candidate_commit": candidate_evidence.get("commit"),
        "candidate_tree": candidate_evidence.get("tree"),
        "implementation": receipt("implementation"),
        "tests": (receipt("implementation") or {}).get("result"),
        "controller_receipt": receipt("controller"),
        "independent_review": receipt("review"),
        "integration": receipt("integration"),
        "materialization": receipt("materialization"),
        "live_verification": receipt("live_verification"),
        "context_publication": record.get("publication"),
        "operator_notification": receipt("operator_notification"),
        "operator_readback": record.get("operator", {}).get("readback"),
        "operator_acceptance": record.get("operator", {}).get(
            "acceptance", "PENDING"
        ),
    }


def operator_action(
    identity: str,
    *,
    decision: str | None,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Record the narrow explicit readback/accept/reject lifecycle transition."""

    config = _initialize(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    actor = require_coder_account()
    record = coding_lifecycle.record_operator_readback(
        store,
        identity,
        actor=actor,
        decision=decision,
    )
    pid = None
    if record["state"] not in coding_lifecycle.TERMINAL:
        pid = coding_lifecycle.spawn(identity, config_path=config_path)
    return {
        "schema": "tgw-local-coding-operator-action/v1",
        "ok": True,
        "root_id": identity,
        "actor": actor,
        "decision": decision,
        "readback": record["operator"]["readback"],
        "acceptance": record["operator"]["acceptance"],
        "state": record["state"],
        "supervisor_pid": pid,
    }


def consolidated_status(target: int | str | None = None, *,
                        config_path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Prefer the durable lifecycle projection, retaining legacy status fallback."""
    if target is None:
        return status(None, config_path=config_path)
    if isinstance(target, str) and target.startswith("coding:"):
        return lifecycle_status(target, config_path=config_path)
    config = load_config(config_path)
    lifecycle_root = config["coding"].get("lifecycle_root")
    record = LifecycleStore(lifecycle_root).find(target) if lifecycle_root else None
    if record is not None:
        return lifecycle_status(record["root_id"], config_path=config_path)
    if target == PP_REF:
        return reconcile(PP_REF, config_path=config_path)
    return status(_todo_id(target), config_path=config_path)


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
    state = job.get("state")
    if state == "cancelled":
        payload = job.get("payload_json") or job.get("payload") or {}
        prior = payload.get("result") if isinstance(payload, dict) else None
        proof = prior.get("stop_control") if isinstance(prior, dict) else None
        if isinstance(proof, dict) and proof.get("kind") == "queued_cancel":
            return {**job, "ok": True, "stop_state": "already_cancelled_queued"}
        acknowledgement = proof.get("acknowledgement") if isinstance(proof, dict) else None
        if isinstance(acknowledgement, dict):
            try:
                state_machine.validate_cancellation_acknowledgement(
                    str(job.get("job_id") or ""), acknowledgement,
                    proof.get("request_identity"),
                )
            except (TypeError, ValueError):
                acknowledgement = None
        if acknowledgement is not None:
            reason = acknowledgement.get("reason")
            suffix = reason if reason in {"stopped", "timeout", "reaped", "no_runner"} else "invalid"
            return {**job, "ok": True, "stop_state": f"worker_confirmed_{suffix}"}
        return {**job, "ok": True, "stop_state": "cancellation_requested"}
    if state in {"succeeded", "failed", "dead_letter"}:
        raise CodingCLIError(f"local coding job {job_id} is already {state}")
    if state == "queued":
        stopped = state_machine.cancel_job(
            job_id, "stopped by the local operator CLI",
            {"stop_control": {"schema": "tgw-coding-stop/v1", "kind": "queued_cancel"}},
        )
        if stopped.get("state") == "cancelled":
            return {**stopped, "ok": True, "stop_state": "cancelled_queued"}
        if stopped.get("state") in {"succeeded", "failed", "dead_letter"}:
            return {**stopped, "ok": False, "stop_state": f"completion_won_{stopped['state']}"}
        return {**stopped, "ok": False, "stop_state": "cancellation_not_applied"}
    if state not in {"leased", "running"}:
        raise CodingCLIError(f"local coding job {job_id} has unsupported active state {state!r}")

    stopped = state_machine.cancel_job(
        job_id, "stopped by the local operator CLI",
        {"stop_control": {"schema": "tgw-coding-stop/v1",
                          "kind": "runner_cancel_requested"}},
    )
    if stopped.get("state") == "cancelled":
        return {**stopped, "ok": True, "stop_state": "cancellation_requested"}
    if stopped.get("state") in {"succeeded", "failed", "dead_letter"}:
        return {**stopped, "ok": False, "stop_state": f"completion_won_{stopped['state']}"}
    return {**stopped, "ok": False, "stop_state": "cancellation_not_applied"}


def _target(args: argparse.Namespace) -> str | None:
    return getattr(args, "coding_target", None) or getattr(args, "request_id", None)


def run(args: argparse.Namespace) -> int:
    try:
        config_path = Path(getattr(args, "config", None) or DEFAULT_CONFIG)
        target = _target(args)
        if args.coding_op == "start":
            value = target or getattr(args, "todo_id", None)
            result = lifecycle_start(
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
            result = consolidated_status(target, config_path=config_path)
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
        elif args.coding_op in {"readback", "accept", "reject"}:
            if not target or not target.startswith("coding:"):
                raise CodingCLIError(
                    f"{args.coding_op} requires an exact coding lifecycle root"
                )
            result = operator_action(
                target,
                decision={
                    "readback": None,
                    "accept": "accept",
                    "reject": "reject",
                }[args.coding_op],
                config_path=config_path,
            )
        else:
            raise CodingCLIError(f"unknown coding operation: {args.coding_op}")
        print(json.dumps(result, sort_keys=True, default=_json_default))
        return 0 if result.get("ok", True) else 1
    except (
        CodingCLIError, LocalCodingWorkflowError, PlanTodoSourceError,
        coding_lifecycle.LifecycleError, OSError, ValueError,
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
    start_parser = commands.add_parser("start", help="bind and dispatch one Todo locally")
    start_parser.add_argument("coding_target", metavar="TODO_ID|PP_REF")
    start_parser.add_argument("--source-commit")
    resume_parser = commands.add_parser(
        "resume", help="resume one exact RESUMABLE_PARTIAL Todo"
    )
    resume_parser.add_argument("coding_target", metavar="TODO_ID")
    resume_parser.add_argument("--source-commit")
    status_parser = commands.add_parser("status", help="show local coding jobs")
    status_parser.add_argument("coding_target", metavar="ROOT|TODO_ID|PP_REF", nargs="?")
    reconcile_parser = commands.add_parser("reconcile", help="read-only PP reconciliation")
    reconcile_parser.add_argument("coding_target", metavar="PP_REF", nargs="?", default=PP_REF)
    log_parser = commands.add_parser("log", help="show one durable coding job")
    log_parser.add_argument("coding_target", metavar="JOB_ID")
    stop_parser = commands.add_parser("stop", help="cancel one active coding job")
    stop_parser.add_argument("coding_target", metavar="JOB_ID")
    for operation in ("readback", "accept", "reject"):
        action = commands.add_parser(
            operation,
            help=f"record explicit operator {operation} for one lifecycle",
        )
        action.add_argument("coding_target", metavar="ROOT")
    access = commands.add_parser("access-status", help="prove the local Unix/group binding")
    access.add_argument("coding_target", metavar="TODO_ID", nargs="?")
    return root


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
