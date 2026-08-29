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
    HISTORY,
    LEGACY_1747,
    PRESERVATION,
    classify,
    migrate_todo_1747,
    preservation_manifest,
    source_fingerprint,
    source_tree,
    validate_implementation_lineage,
)
from tgw.development.plan_binding import parse_plan_binding, validate_plan_binding
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.development.plan_todo_source import PlanTodoSourceError
from tgw.development.plan_todo_source import resolve as resolve_plan_todo
from tgw.development.profiles import CODING_DIAGNOSTIC_REVIEW
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
                       finished_at, lease_owner, lease_token::text,
                       error_code, error_detail, payload_json
                  FROM queue_jobs
                 WHERE {where}
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                params,
            )
            return [dict(row) for row in cursor.fetchall()]


def _job_state_counts(todo_id: int | None = None) -> dict[str, int]:
    """Count local coding jobs without loading their durable payloads."""

    where = "queue_name = ANY(%s)"
    params: list[Any] = [list(_LOCAL_QUEUES)]
    if todo_id is not None:
        where += " AND payload_json->>'todo_id' = %s"
        params.append(str(todo_id))
    with state_machine._conn() as connection:
        with connection.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cursor:
            cursor.execute(
                f"""
                SELECT state::text AS state, COUNT(*)::bigint AS job_count
                  FROM queue_jobs
                 WHERE {where}
                 GROUP BY state
                 ORDER BY state
                """,
                params,
            )
            return {
                str(row["state"]): int(row["job_count"])
                for row in cursor.fetchall()
            }


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
    lifecycle_remediation: Mapping[str, Any] | None = None,
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
    elif lifecycle_job_binding is not None:
        # A managed lifecycle already owns an exact, operator-created
        # Plan/Todo/worktree binding.  The supervisor runs under its service
        # account and must reuse that binding; rebinding here would replace
        # the operator's Unix identity with the service identity.
        _bound_item, exact_binding = _plan_binding_for_todo(todo_id)
        if (
            source_commit is not None
            and exact_binding["source_commit"] != source_commit
        ):
            raise CodingCLIError(
                "lifecycle source differs from the exact Todo binding"
            )
        binding = {"binding": exact_binding}
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
        elif (
            resume_only
            and resume_state["state"] == "CLOSED_CANDIDATE"
            and (legacy_jobs is not None or lifecycle_job_binding is not None)
        ):
            # A lifecycle resume can crash after its worker closes the exact
            # candidate but before the journal observes the new fenced job.
            # Dispatch one lifecycle-rebound implementation job so the worker's
            # existing CLOSED_CANDIDATE recovery path emits the missing receipt
            # without rerunning Codex.  Preserve the legacy 1747 no-op contract.
            resume_noop = legacy_jobs is not None
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
            remediation_bindings=(
                {todo_id: dict(lifecycle_remediation)}
                if lifecycle_remediation is not None
                else {}
            ),
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
    active_generation = (
        record.get("active_implementation_generation")
        if record is not None
        else None
    )
    active_resume = (
        active_generation
        if isinstance(active_generation, Mapping)
        and active_generation.get("kind") == "resume"
        else None
    )
    live_resume = (
        record.get("resume_intent")
        if record is not None
        and isinstance(record.get("resume_intent"), Mapping)
        else None
    )
    if record is not None and record.get("state") != "RESUMABLE_PARTIAL" and (
        live_resume is not None or active_resume is not None
    ):
        intent_hash = (
            (live_resume.get("resume_intent_hash") if live_resume else None)
            or active_resume.get("intent_hash")
        )
        return {
            "schema": "tgw-local-coding-resume/v2",
            "ok": True,
            "todo_id": identifier,
            "lifecycle_root_id": record["root_id"],
            "lifecycle_state": record["state"],
            "resume_intent_hash": intent_hash,
            "resume_reused": True,
            "supervisor": "tgw-coding-lifecycle-supervisor.service",
        }
    if record is not None:
        item = todo.todo_get(identifier)
        if item is None:
            raise CodingCLIError(f"Todo {identifier} does not exist")
        worktree = Path(record["binding"]["worktree"])
        plan = record["binding"]["plan_todo_binding"]
        expected = {
            "todo_id": identifier,
            "plan_commit": plan["plan_commit"],
            "solution_hash": plan["solution_hash"],
            "source_commit": plan["source_commit"],
            "source_tree": record["binding"]["source_tree"],
            "actor": item.get("agent") or "codex",
            "worktree": str(worktree),
            "treatment_id": "codex-implement",
            "treatment_version": "1",
        }
        with exclusive_worktree_lease(worktree):
            coding_state = classify(worktree, expected)
        resume_evidence = coding_state
        if (
            coding_state.get("state") == "CLOSED_CANDIDATE"
            and record.get("state") == "RESUMABLE_PARTIAL"
        ):
            failed_stage = record.get("failure", {}).get("stage")
            prior = record.get("stages", {}).get(failed_stage, {})
            prior_receipt = prior.get("receipt") if isinstance(prior, Mapping) else None
            if (
                not isinstance(prior_receipt, Mapping)
                or prior_receipt.get("state") != "RESUMABLE_PARTIAL"
                or not isinstance(prior_receipt.get("resume_of"), str)
                or not isinstance(prior_receipt.get("fingerprint"), str)
            ):
                raise CodingCLIError(
                    "closed resume candidate lacks its exact pre-dispatch partial fence"
                )
            resume_evidence = prior_receipt
        elif coding_state.get("state") != "RESUMABLE_PARTIAL":
            raise CodingCLIError(
                f"Todo {identifier} is {coding_state.get('state')}; "
                "coding resume requires RESUMABLE_PARTIAL"
            )
        store = LifecycleStore(lifecycle_root)
        reopened = coding_lifecycle.request_resume(
            store,
            record["root_id"],
            receipt={
                "schema": "tgw-local-coding-lifecycle-resume-intent/v1",
                "root_id": record["root_id"],
                "binding_hash": record["binding"]["binding_hash"],
                "todo_id": identifier,
                "resume_of": resume_evidence["resume_of"],
                "resume_fingerprint": resume_evidence["fingerprint"],
                "worktree": str(worktree),
                "source_commit": record["binding"]["source_commit"],
                "source_tree": record["binding"]["source_tree"],
            },
        )
        return {
            "schema": "tgw-local-coding-resume/v2",
            "ok": True,
            "todo_id": identifier,
            "worktree": str(worktree),
            "coding_state": coding_state,
            "jobs": [],
            "lifecycle_root_id": reopened["root_id"],
            "lifecycle_state": reopened["state"],
            "resume_intent_hash": reopened["resume_intent"][
                "resume_intent_hash"
            ],
            "supervisor": "tgw-coding-lifecycle-supervisor.service",
            "dependencies": reopened["boundaries"],
        }
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
        dispatch_jobs=record is None,
    )
    return result


def _plan_binding_for_todo(identifier: int) -> tuple[dict[str, Any], dict[str, Any]]:
    item = todo.todo_get(identifier)
    if item is None:
        raise CodingCLIError(f"Todo {identifier} does not exist")
    binding = parse_plan_binding(item.get("status_note"), todo_id=identifier)
    if binding is None:
        raise CodingCLIError(f"Todo {identifier} has no exact Plan/Todo binding")
    return item, validate_plan_binding(binding, todo_id=identifier)


def _resolve_pp_lifecycle_alias(
    config: Mapping[str, Any],
    solution: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Resolve PP-WORKFLOW-001 to its sole active smallest bound Todo leaf."""

    rows = todo.todo_list(show_all=True)
    projection = reconcile_pp_workflow(todo_rows=rows, **runtime)
    if (
        projection.get("pp_ref") != PP_REF
        or projection.get("resolver_binding", {}).get("agreement") != "verified"
        or projection.get("solution", {}).get("conformance_verified") is not True
    ):
        raise CodingCLIError("PP lifecycle reconciliation is not exact")
    unit = next(
        (
            item
            for item in solution.get("work_units", [])
            if item.get("id") == DEFAULT_TREATMENT
        ),
        None,
    )
    if not isinstance(unit, Mapping):
        raise CodingCLIError("PP lifecycle Plan solution lacks its bounded leaf")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        identifier = row.get("id")
        if (
            not isinstance(identifier, int)
            or identifier <= 0
            or row.get("pp_ref") != PP_REF
            or row.get("done_at") is not None
        ):
            continue
        raw = parse_plan_binding(row.get("status_note"), todo_id=identifier)
        if raw is None:
            continue
        binding = validate_plan_binding(raw, todo_id=identifier)
        root = binding.get("execution_root", {})
        if (
            binding.get("plan_commit") == solution.get("plan_commit")
            and binding.get("solution_hash") == solution.get("solution_hash")
            and binding.get("closure_hash") == solution.get("closure_hash")
            and binding.get("source_commit") == runtime.get("selected_commit")
            and binding.get("treatment_id") == unit.get("id")
            and binding.get("capability") == unit.get("capability")
            and root.get("kind") == "todo"
            and root.get("todo_id") == identifier
        ):
            candidates.append((identifier, binding))
    if len(candidates) != 1:
        raise CodingCLIError(
            "PP lifecycle alias is absent or ambiguous; exact active Todo root required"
        )
    identifier, binding = candidates[0]
    alias = {
        "schema": "tgw-local-coding-pp-alias/v1",
        "pp_ref": PP_REF,
        "todo_id": identifier,
        "plan_commit": binding["plan_commit"],
        "solution_hash": binding["solution_hash"],
        "closure_hash": binding["closure_hash"],
        "treatment_id": binding["treatment_id"],
        "execution_root_identity": binding["execution_root"]["identity_hash"],
        "reconciliation_solution_hash": projection["solution"]["solution_hash"],
    }
    alias["alias_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(alias, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return identifier, alias


def lifecycle_start(
    target: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    solution_path: Path | str = DEFAULT_SOLUTION,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Bind one Todo, create/reuse its durable root, and return immediately."""
    config = _initialize(config_path)
    solution = __import__(
        "tgw.development.local_workflow", fromlist=["load_solution"]
    ).load_solution(solution_path)
    runtime = _pp_runtime_binding(config, source_commit)
    alias = None
    if target == PP_REF:
        identifier, alias = _resolve_pp_lifecycle_alias(
            config, solution, runtime
        )
    else:
        identifier = _todo_id(target)
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
        return {
            "schema": "tgw-local-coding-lifecycle-start/v1",
            "ok": prior["state"] != "REMEDIATION_REQUIRED",
            "root_id": prior["root_id"],
            "target": str(identifier),
            "state": prior["state"],
            "binding_hash": prior["binding"]["binding_hash"],
            "supervisor": "tgw-coding-lifecycle-supervisor.service",
            "returns_immediately": True,
            "dependencies": prior["boundaries"],
            "session": {
                "cwd": prior["binding"]["worktree"],
                "argv": ["codex", "-C", prior["binding"]["worktree"]],
                "observer": [
                    "tgw", "coding", "status", str(identifier),
                ],
            },
            **({"pp_alias": alias} if alias is not None else {}),
            **({"failure": prior["failure"]} if prior.get("failure") else {}),
        }
    # The ordinary operator command is the one place that may create the exact
    # Unix-user Plan/Todo/worktree binding.  The managed supervisor owns every
    # later queue dispatch and is never allowed to silently rebind it.
    prepared: dict[str, Any] | None = None
    try:
        _item, plan = _plan_binding_for_todo(identifier)
    except CodingCLIError as exc:
        if "has no exact Plan/Todo binding" not in str(exc):
            raise
        prepared = start(
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
    return {
        "schema": "tgw-local-coding-lifecycle-start/v1", "ok": True,
        "root_id": record["root_id"], "target": str(identifier), "state": record["state"],
        "binding_hash": record["binding"]["binding_hash"],
        "supervisor": "tgw-coding-lifecycle-supervisor.service",
        "returns_immediately": True,
        "dependencies": record["boundaries"],
        "session": {
            "cwd": record["binding"]["worktree"],
            "argv": ["codex", "-C", record["binding"]["worktree"]],
            "observer": ["tgw", "coding", "status", str(identifier)],
        },
        "binding_created": prepared is not None,
        **({"pp_alias": alias} if alias is not None else {}),
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
            # A journaled resume intent deliberately changes the job fence.
            # Earlier jobs remain history but cannot become evidence for the
            # resumed generation and must not poison its exact lookup.
            if dict(observed) != expected_job_binding:
                continue
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
        worktree = Path(record["binding"]["worktree"])
        payload = row.get("payload_json") or row.get("payload")
        try:
            failed_receipt, failed_sha256 = _receipt_file(
                worktree / receipt_name
            )
            coding_lifecycle.validate_job_binding(
                record, failed_receipt.get("coding_lifecycle")
            )
            resumable_partial = (
                stage == "implementation"
                and queue_name == "codex-implement"
                and failed_receipt.get("outcome") == "partial"
            )
            if (
                failed_receipt.get("status") != "FAIL"
                or failed_receipt.get("outcome")
                != ("partial" if resumable_partial else "failed")
                or failed_receipt.get("established_conditions") != []
                or failed_receipt.get("treatment_id") != queue_name
                or failed_receipt.get("plan_binding")
                != record["binding"]["plan_todo_binding"]
            ):
                raise CodingCLIError(
                    f"{queue_name} negative receipt is stale or contradictory"
                )
        except (CodingCLIError, coding_lifecycle.LifecycleError) as exc:
            return _stage(
                record,
                stage,
                "failed",
                reason=(
                    f"{queue_name} terminal job has no exact negative receipt: {exc}"
                ),
                job_ids=ids,
            )
        findings: list[dict[str, Any]] = []
        artifacts = failed_receipt.get("artifacts")
        if not isinstance(artifacts, list):
            return _stage(
                record,
                stage,
                "failed",
                reason=f"{queue_name} negative receipt artifacts are invalid",
                job_ids=ids,
            )
        if resumable_partial:
            if not isinstance(payload, Mapping):
                return _stage(
                    record,
                    stage,
                    "failed",
                    reason="partial implementation queue payload is unavailable",
                    job_ids=ids,
                )
            durable_result = payload.get("result")
            if (
                row.get("state") != "dead_letter"
                or row.get("error_code") != "HARD_FAILURE"
                or not isinstance(row.get("error_detail"), str)
                or row.get("error_detail")
                != "TreatmentFailure('coding treatment reported partial')"
                or row.get("finished_at") is None
                or row.get("lease_owner") is not None
                or row.get("lease_token") is not None
                or durable_result != failed_receipt
            ):
                return _stage(
                    record,
                    stage,
                    "failed",
                    reason=(
                        "partial implementation lacks exact durable terminal "
                        "queue provenance"
                    ),
                    job_ids=ids,
                )
            plan = record["binding"]["plan_todo_binding"]
            expected = {
                "todo_id": int(record["target"]),
                "plan_commit": plan["plan_commit"],
                "solution_hash": plan["solution_hash"],
                "source_commit": record["binding"]["source_commit"],
                "source_tree": record["binding"]["source_tree"],
                "actor": payload.get("todo_agent"),
                "worktree": str(worktree),
                "treatment_id": queue_name,
                "treatment_version": str(payload.get("treatment_version", "1")),
            }
            observed = classify(worktree, expected)
            attempt_history = observed.get("history")
            latest_attempt = (
                attempt_history[-1]
                if isinstance(attempt_history, list) and attempt_history
                else None
            )
            if (
                observed.get("state") != "RESUMABLE_PARTIAL"
                or not isinstance(latest_attempt, Mapping)
                or latest_attempt.get("job_id") != str(row["job_id"])
                or latest_attempt.get("attempt_count") != row.get("attempt_count")
                or latest_attempt.get("outcome") != "partial"
                or latest_attempt.get("artifacts") != artifacts
                or failed_receipt.get("implementation_attempt_hash")
                != latest_attempt.get("attempt_hash")
            ):
                return _stage(
                    record,
                    stage,
                    "failed",
                    reason=(
                        "partial implementation receipt has no exact resumable "
                        f"lineage: {observed.get('state')}"
                    ),
                    job_ids=ids,
                )
            return _stage(
                record,
                stage,
                "resumable_partial",
                receipt=observed,
                reason="codex-implement preserved one exact resumable partial attempt",
                job_ids=ids,
            )
        detail = "; ".join(
            str(item.get("detail"))
            for item in artifacts
            if isinstance(item, Mapping) and item.get("detail")
        )
        if stage == "review":
            if not isinstance(payload, Mapping):
                return _stage(
                    record,
                    stage,
                    "failed",
                    reason="failed review queue payload is unavailable",
                    job_ids=ids,
                )
            from tgw.development.coding_review import (
                validate_failed_review_artifact,
            )
            from tgw.review_contract import ReviewRunnerError

            try:
                if any(
                    failed_receipt.get(key) != payload.get(key)
                    for key in (
                        "plan_binding",
                        "coding_lifecycle",
                        "coding_candidate",
                        "task_spec",
                    )
                ):
                    raise ReviewRunnerError(
                        "failed review receipt differs from its queue bindings"
                    )
                artifact = validate_failed_review_artifact(
                    failed_receipt,
                    payload=payload,
                    worktree=worktree,
                    expected_job_id=str(row["job_id"]),
                )
            except ReviewRunnerError as exc:
                return _stage(
                    record,
                    stage,
                    "failed",
                    reason=f"failed independent review evidence is invalid: {exc}",
                    job_ids=ids,
                )
            findings = [dict(item) for item in artifact["report"]["findings"]]
            detail = "; ".join(
                f"{item.get('severity', 'unknown')} "
                f"{item.get('path', '<unknown>')}:{item.get('line', 1)} "
                f"{item.get('message', '')}"
                for item in findings
            )
            candidate = {
                "head": artifact["candidate_commit"],
                "tree": artifact["candidate_tree"],
            }
        else:
            candidate = source_fingerprint(worktree)
        evidence = {
            "schema": "tgw-local-coding-negative-queue-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": record["binding"]["binding_hash"],
            "job_id": str(row["job_id"]),
            "queue_name": queue_name,
            "job_state": row.get("state"),
            "receipt_file": str(worktree / receipt_name),
            "receipt_file_sha256": failed_sha256,
            "result": failed_receipt,
            "candidate": candidate,
            **({"findings": findings} if findings else {}),
        }
        return _stage(
            record, stage, "remediation",
            receipt=evidence,
            reason=(
                f"{queue_name} exact job ended {row.get('state')}"
                + (f": {detail}" if detail else "")
            ),
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
        from tgw.development.coding_review import validate_review_artifact
        from tgw.review_contract import ReviewRunnerError

        try:
            validate_review_artifact(
                file_receipt,
                payload=payload,
                worktree=worktree,
                expected_job_id=str(row["job_id"]),
            )
        except ReviewRunnerError as exc:
            return _stage(
                record,
                stage,
                "remediation",
                reason=f"independent review receipt is invalid: {exc}",
                job_ids=ids,
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


def supervise(identity: str, *, config_path: Path | str = DEFAULT_CONFIG) -> dict[str, Any]:
    """Resume the existing local dispatcher under the durable root journal."""
    config = _initialize(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])

    def implementation(record: dict[str, Any]) -> dict[str, Any]:
        resume_intent = record.get("resume_intent")
        remediation_intent = record.get("remediation_intent")
        active_generation = record.get("active_implementation_generation")
        if isinstance(active_generation, Mapping):
            active_intent = active_generation.get("intent")
            if not isinstance(active_intent, Mapping):
                raise CodingCLIError(
                    "active coding implementation generation has no exact intent"
                )
            if active_generation.get("kind") == "resume" and resume_intent is None:
                resume_intent = active_intent
            elif (
                active_generation.get("kind") == "remediation"
                and remediation_intent is None
            ):
                remediation_intent = active_intent
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
                lifecycle_remediation=(
                    remediation_intent
                    if isinstance(remediation_intent, Mapping)
                    else None
                ),
                lifecycle_stage="implementation",
                resume_only=isinstance(resume_intent, Mapping),
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
                reason="independent diagnostic reviewer is not installed",
            )

        def dispatch() -> TickResult:
            return tick(
                ForemanConfig(
                    coding_config=dict(config["coding"]),
                    goal_profile=CODING_DIAGNOSTIC_REVIEW,
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
                    "diagnostic_review_receipt_hash": record["effects"]["review"]["receipt_hash"],
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
        from tgw.development.coding_root_effect import (
            RootEffectPaths,
            ensure_request,
            read_response,
        )

        coding = config["coding"]
        paths = RootEffectPaths(
            request_root=Path(coding["root_effect_root"]),
            lifecycle_root=Path(coding["lifecycle_root"]),
            repository=Path(coding["repository_root"]),
            runtime_root=Path(coding["runtime_root"]),
            coding_config=Path(config_path),
        )
        request = ensure_request(paths, record)
        response = read_response(paths, request)
        if response is None:
            return _stage(
                record,
                "materialization",
                "waiting",
                reason=(
                    "awaiting the ordinary db:tgw-coders materialization response"
                ),
            )
        candidate = record["effects"]["candidate"]["receipt"]
        evidence = {
            "schema": "tgw-local-coding-materialization-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": record["binding"]["binding_hash"],
            "request_hash": request["request_hash"],
            "response_hash": response["response_hash"],
            "candidate_commit": candidate["commit"],
            "candidate_tree": candidate["tree"],
            "diagnostic_review_schedule_hash": record["effects"]["review"]["receipt_hash"],
            "integration_receipt_hash": record["effects"]["integration"]["receipt_hash"],
            "materialization_receipt_hash": response[
                "materialization_receipt_hash"
            ],
            "selection_receipt_hash": response["selection_receipt_hash"],
            "workers_receipt_hash": response["workers_receipt_hash"],
        }
        return _stage(record, "materialization", "satisfied", receipt=evidence)

    def live_verification(record: dict[str, Any]) -> dict[str, Any]:
        from tgw.development.coding_root_effect import (
            RootEffectPaths,
            build_request,
            read_response,
        )

        coding = config["coding"]
        paths = RootEffectPaths(
            request_root=Path(coding["root_effect_root"]),
            lifecycle_root=Path(coding["lifecycle_root"]),
            repository=Path(coding["repository_root"]),
            runtime_root=Path(coding["runtime_root"]),
            coding_config=Path(config_path),
        )
        request = build_request(record)
        response = read_response(paths, request)
        if response is None:
            return _stage(
                record,
                "live_verification",
                "waiting",
                reason="awaiting exact lifecycle-bound live verification response",
            )
        candidate = record["effects"]["candidate"]["receipt"]
        evidence = {
            "schema": "tgw-local-coding-live-verification-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": record["binding"]["binding_hash"],
            "request_hash": request["request_hash"],
            "response_hash": response["response_hash"],
            "candidate_commit": candidate["commit"],
            "candidate_tree": candidate["tree"],
            "materialization_stage_receipt_hash": record["effects"][
                "materialization"
            ]["receipt_hash"],
            "live_verification_receipt_hash": response[
                "live_verification_receipt_hash"
            ],
            "technical_result_hash": response["technical_result_hash"],
            "canary": response["receipts"]["live_verification"],
        }
        return _stage(record, "live_verification", "satisfied", receipt=evidence)

    def terminal_publication(record: dict[str, Any]) -> dict[str, Any]:
        from tgw.development.coding_root_effect import (
            RootEffectPaths,
            ensure_projection_request,
            read_projection_response,
        )

        coding = config["coding"]
        paths = RootEffectPaths(
            request_root=Path(coding["root_effect_root"]),
            lifecycle_root=Path(coding["lifecycle_root"]),
            repository=Path(coding["repository_root"]),
            runtime_root=Path(coding["runtime_root"]),
            coding_config=Path(config_path),
        )
        request = ensure_projection_request(paths, record)
        response = read_projection_response(paths, request)
        if response is None:
            return _stage(
                record,
                "terminal_publication",
                "publication_unavailable",
                reason=(
                    "Context terminal projection is pending; technical completion "
                    "does not depend on this optional orientation evidence"
                ),
            )
        evidence = {
            "schema": "tgw-local-coding-context-projection-evidence/v1",
            "root_id": record["root_id"],
            "binding_hash": record["binding"]["binding_hash"],
            "projection_hash": request["projection_hash"],
            "result_hash": request["result_hash"],
            "candidate_commit": request["candidate_commit"],
            "candidate_tree": request["candidate_tree"],
            "diagnostic_review_schedule_hash": record["effects"]["review"]["receipt_hash"],
            "integration_receipt_hash": request["integration_receipt_hash"],
            "materialization_receipt_hash": request[
                "materialization_receipt_hash"
            ],
            "live_verification_receipt_hash": request[
                "live_verification_receipt_hash"
            ],
            "technical_result_hash": request["technical_result_hash"],
            "projection_response_hash": response["response_hash"],
            "context_receipt_file_sha256": response[
                "context_receipt_file_sha256"
            ],
            "context_task_file_sha256": response[
                "context_task_file_sha256"
            ],
        }
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
                "operator_acceptance": "PENDING",
            },
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
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Record readback without claiming operator acceptance."""

    config = _initialize(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    actor = require_coder_account()
    record = coding_lifecycle.record_operator_readback(
        store,
        identity,
        actor=actor,
    )
    return {
        "schema": "tgw-local-coding-operator-action/v1",
        "ok": True,
        "root_id": identity,
        "actor": actor,
        "readback": record["operator"]["readback"],
        "acceptance": "PENDING",
        "state": record["state"],
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


def access_status(
    todo_id: int | None = None,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
    full_jobs: bool = False,
) -> dict[str, Any]:
    """Return a compact Unix/group proof; expose job payloads only on request."""

    _initialize(config_path)
    result = status_command(argparse.Namespace(config=Path(config_path)))
    state_counts = _job_state_counts(todo_id)
    job_count = sum(state_counts.values())
    result.update(
        {
            "schema": "tgw-local-coding-access-status/v1",
            "todo_id": todo_id,
            "job_state_counts": dict(sorted(state_counts.items())),
            "job_count": job_count,
            "jobs_included": full_jobs,
        }
    )
    if full_jobs:
        result["jobs"] = _jobs(todo_id, limit=max(1, job_count))
    return result


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


_RECEIPT_FILES = frozenset({
    "implementation-receipt.json",
    "controller-harness-receipt.json",
    "review-receipt.json",
    "deployment-receipt.json",
    "stitch-receipt.json",
    "operator-admit-pending.json",
})
_HISTORY_ROOT = HISTORY.split("/", 1)[0]
_PRESERVATION_ROOT = PRESERVATION.split("/", 1)[0]


def _worktree_source_status(worktree: str) -> str:
    """Worktree status excluding workflow-evidence files (receipts/history).

    The rebind cleanliness check must ignore the untracked receipt and
    partial-resume trees that legitimately live in a closed candidate worktree,
    or every closed candidate is misread as dirty (the same defect the review
    worker had before the 1923 fix).
    """
    status = subprocess.run(
        ["git", "-c", f"safe.directory={worktree}", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=worktree,
        check=False,
        text=True,
        capture_output=True,
    )
    if status.returncode:
        raise CodingCLIError("worktree status probe failed during rebind")
    kept = []
    for line in status.stdout.splitlines():
        if len(line) < 4:
            kept.append(line)
            continue
        path = line[3:]
        if path in _RECEIPT_FILES:
            continue
        if path == _HISTORY_ROOT or path.startswith(_HISTORY_ROOT + "/"):
            continue
        if path == _PRESERVATION_ROOT or path.startswith(_PRESERVATION_ROOT + "/"):
            continue
        kept.append(line)
    return "\n".join(kept)


def rebind(
    todo_id: int | str,
    *,
    config_path: Path | str = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Reset a FAILED/REMEDIATION_REQUIRED lifecycle for a fresh operator bind.

    Preserves the failed journal (renamed, never deleted) and clears the Todo
    status-note binding; a subsequent ``tgw coding start`` creates a brand-new
    binding and worktree under the caller's Unix identity. Refuses a dirty
    worktree so no implementation bytes are stranded.
    """
    config = _initialize(config_path)
    actor = require_coder_account()
    identifier = _todo_id(todo_id)
    item = todo.todo_get(identifier)
    if item is None or item.get("done_at") is not None:
        raise CodingCLIError(f"Todo {identifier} is not an open Todo")
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    prior = store.find(identifier)
    if prior is None:
        raise CodingCLIError(f"Todo {identifier} has no coding lifecycle to rebind")
    if prior.get("state") not in {"FAILED", "REMEDIATION_REQUIRED"}:
        raise CodingCLIError(
            f"Todo {identifier} lifecycle state {prior.get('state')!r} is not rebindable "
            "(only FAILED or REMEDIATION_REQUIRED)"
        )
    worktree = prior.get("binding", {}).get("worktree")
    if isinstance(worktree, str) and worktree:
        if _worktree_source_status(worktree):
            raise CodingCLIError(
                f"Todo {identifier} worktree is not clean; rebind refuses a dirty worktree"
            )
    identity = prior.get("root_id") or f"coding:{identifier}"
    journal = store.path(identity)
    stamp = datetime.now().astimezone().strftime("%Y%m%dT%H%M%S%z")
    archived = journal.with_name(journal.name + f".rebind-{stamp}")
    journal.rename(archived)
    todo.todo_set_status_note(identifier, "", suppress_plan_render=True)
    return {
        "schema": "tgw-local-coding-rebind/v1",
        "ok": True,
        "todo_id": identifier,
        "actor": actor,
        "archived_lifecycle": archived.name,
        "worktree": worktree,
        "note": "Run 'tgw coding start <id>' to create a fresh binding and worktree.",
    }



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
        elif args.coding_op == "status":
            result = consolidated_status(target, config_path=config_path)
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
                raise CodingCLIError("log requires a coding job ID")
            result = job_log(target, config_path=config_path)
        elif args.coding_op == "stop":
            if not target:
                raise CodingCLIError("stop requires a coding job ID")
            result = stop(target, config_path=config_path)
        elif args.coding_op == "rebind":
            result = rebind(_todo_id(target), config_path=config_path)
        elif args.coding_op == "readback":
            if not target or not target.startswith("coding:"):
                raise CodingCLIError(
                    f"{args.coding_op} requires an exact coding lifecycle root"
                )
            result = operator_action(
                target,
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
    start_parser = commands.add_parser(
        "start", help="create or reuse one durable coding lifecycle root"
    )
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
    rebind_parser = commands.add_parser(
        "rebind", help="reset a FAILED/REMEDIATION_REQUIRED lifecycle for a fresh bind"
    )
    rebind_parser.add_argument("coding_target", metavar="TODO_ID")
    for operation in ("readback",):
        action = commands.add_parser(
            operation,
            help=f"record explicit operator {operation} for one lifecycle",
        )
        action.add_argument("coding_target", metavar="ROOT")
    access = commands.add_parser("access-status", help="prove the local Unix/group binding")
    access.add_argument("coding_target", metavar="TODO_ID", nargs="?")
    access.add_argument(
        "--full-jobs",
        action="store_true",
        help="include complete durable job payloads instead of counts only",
    )
    return root


def main() -> int:
    return run(parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
