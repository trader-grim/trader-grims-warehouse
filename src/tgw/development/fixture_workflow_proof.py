"""Isolated, one-shot proof of the Plan-bound coding workflow.

This is a test fixture runner, not a service or queue consumer.  Its Todo and
queue adapters are in-memory; the only filesystem effect is a request-bound Git
worktree and its receipt beneath the caller-provided fixture root.
"""
from __future__ import annotations

import json
import hashlib
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tgw.coding_provision_worker import _prepare_request_worktree
from tgw.development.foreman import EVALUATOR_VERSION, TodoRecord, tick
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.development.plan_binding import execution_root_hash
from tgw.plan_execution_card import build_execution_card
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.plan_solver import solve
from tgw.workflow import compile_solution_runtime
from tgw.workflow_kernel.contracts import RuntimeWorkGraph, TreatmentDisposition
from tgw.workers.coding import CodingWorker


@dataclass(frozen=True)
class PlanSolution:
    document: dict[str, Any]
    solution: dict[str, Any]


@dataclass(frozen=True)
class ReadyLeaf:
    treatment_id: str
    capability: str
    closure_hash: str


@dataclass(frozen=True)
class PlanBoundTodo:
    todo_id: int
    binding: dict[str, Any]


@dataclass(frozen=True)
class CodingRequest:
    job_id: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class AllocatedWorktree:
    identity: dict[str, Any]


@dataclass(frozen=True)
class CodingExecution:
    envelope: dict[str, Any]


@dataclass(frozen=True)
class Receipt:
    path: str
    document: dict[str, Any]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], check=True, text=True, capture_output=True)
    return result.stdout.strip()


def _fixture_solution(commit: str) -> PlanSolution:
    document = {
        "schema": "tgw-plan/v2", "plan_commit": commit,
        "capabilities": ["fixture.code@1", "promptcraft.receiver-profiles@1"],
        "providers": [{"id": "fixture", "provides": ["fixture.code@1"]}, {"id": "recovered-promptcraft", "provides": ["promptcraft.receiver-profiles@1"]}],
        "observations": [],
        "target": {"id": "fixture", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["fixture.code@1", "promptcraft.receiver-profiles@1"]},
    }
    native = solve(document)
    solution = solve(document, conformance_result={"available": True, "closure_hash": native["closure_hash"]})
    return PlanSolution(document, solution)


def _fixture_card(compiled, solution: dict[str, Any], treatment_id: str, source_commit: str) -> dict[str, Any]:
    root = {"schema": "tgw-execution-root/v1", "kind": "plan", "plan_id": "fixture",
            "profile": "implementation", "plan_commit": source_commit}
    root["identity_hash"] = execution_root_hash(root)
    resources = {name: {"ref": f"fixture:{name}", "hash": "sha256:" + "0" * 64} for name in (
        "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot", "source_tree",
        "execution_environment", "authority_conditions", "candidate_evidence", "receipt_sink",
    )}
    return build_execution_card(
        compiled=compiled, solution=solution,
        execution_graph={"plan_id": "fixture", "work_units": [{"id": "fixture", "title": "Fixture proof",
            "establishes": ["fixture.code@1"], "acceptance": ["fixture receipt"]}, {"id": "promptcraft", "title": "Promptcraft",
            "establishes": ["promptcraft.receiver-profiles@1"], "acceptance": ["Promptcraft bound"]}]},
        treatment_id=treatment_id, source_commit=source_commit, source_tree=source_commit,
        resources=resources, environment={"id": "fixture"}, execution_root=root,
    )


def run_fixture_proof(*, source_root: Path, fixture_root: Path, candidate_commit: str) -> dict[str, Any]:
    """Run one PlanSolution → receipt proof without touching TGW runtime state."""
    source_root, fixture_root = source_root.resolve(), fixture_root.resolve()
    if _git(source_root, "rev-parse", "HEAD") != candidate_commit:
        raise ValueError("fixture source does not resolve to the requested candidate")
    if fixture_root.exists() and any(fixture_root.iterdir()):
        raise ValueError("fixture root must be empty")
    fixture_root.mkdir(parents=True, exist_ok=True)
    imported = [
        __import__("tgw.development.plan_todo_bridge", fromlist=["x"]).__file__,
        __import__("tgw.development.foreman", fromlist=["x"]).__file__,
        __import__("tgw.workers.coding", fromlist=["x"]).__file__,
    ]
    if any(not str(path).startswith(str(source_root / "src")) or any(part in str(path) for part in ("actor-runtime", "site-packages", "ppworkflow-")) for path in imported):
        raise ValueError("fixture imports are not bound to candidate source")
    source_before = _git(source_root, "status", "--porcelain=v1")
    plan = _fixture_solution(candidate_commit)
    compiled = compile_solution_runtime(plan.solution, current_plan_commit=candidate_commit)
    ready = ReadyLeaf("establish:fixture.code@1", "fixture.code@1", compiled.closure_hash)
    rows: list[dict[str, Any]] = []
    worktree_root = fixture_root / "worktrees"
    worktree_root.mkdir()
    coding = {"worktree_root": str(worktree_root), "repository_root": str(source_root)}

    def create(agent: str, body: str, priority: int, *_unused: object) -> dict[str, Any]:
        row = {"id": len(rows) + 1, "agent": agent, "body": body, "priority": priority, "status_note": ""}
        rows.append(row)
        return row

    def note(todo_id: int, value: str) -> None:
        rows[todo_id - 1]["status_note"] = value

    def allocate(todo_id: int, request_id: str, source_commit: str) -> dict[str, Any]:
        return _prepare_request_worktree(
            {"todo_id": todo_id, "request_id": request_id, "source_commit": source_commit},
            coding, "fixture-coding-worker",
        )

    fixture_identity = "fixture-" + hashlib.sha256(str(fixture_root).encode()).hexdigest()[:16]
    bound = bind_leaf(compiled, solution=plan.solution, treatment_id=ready.treatment_id,
        source_commit=candidate_commit, worktree_identity=fixture_identity, agent="codex",
        execution_card=_fixture_card(compiled, plan.solution, ready.treatment_id, candidate_commit), create_todo=create,
        list_todos=lambda: rows, allocate_worktree=allocate, set_status_note=note)
    todo = PlanBoundTodo(bound["todo_id"], bound["binding"])
    allocation = AllocatedWorktree(todo.binding["worktree_identity"])
    graph = RuntimeWorkGraph("runtime-work-graph/v1", "fixture-graph", allocation.identity["worktree"],
        "fixture-generation", CODING_READY_FOR_IMPLEMENTATION.identity, "1", EVALUATOR_VERSION,
        "fixture-evidence", "fixture-condition", "fixture-registry", (), (), (), (),
        (TreatmentDisposition("codex-implement", "1", ("fixture-ready",)),), (), (), (), ())
    jobs: list[dict[str, Any]] = []

    def enqueue(queue_name: str, payload: dict[str, Any], **kwargs: Any) -> str:
        if queue_name != "codex-implement" or jobs:
            raise ValueError("fixture accepts exactly one codex-implement job")
        jobs.append({"job_id": "fixture-job-1", "payload": payload, **kwargs})
        return "fixture-job-1"

    record = TodoRecord(todo.todo_id, "codex", 1, "fixture-only implementation proof", allocation.identity["worktree"], todo.binding)
    with patch("tgw.development.foreman.build_coding_snapshot", return_value=object()), patch("tgw.development.foreman.evaluate", return_value=graph):
        result = tick(fetch_todos=lambda: [record], check_active_fn=lambda _graph_id: False,
            check_terminal_fn=lambda _graph_id: False, enqueue_fn=enqueue,
            config=__import__("tgw.development.foreman", fromlist=["ForemanConfig"]).ForemanConfig(coding_config=coding))
    if result.dispatched != 1 or len(jobs) != 1:
        raise RuntimeError("fixture foreman did not dispatch exactly one job")
    request = CodingRequest("fixture-job-1", jobs[0]["payload"])
    worker = CodingWorker("codex-implement", {"coding": coding}, launcher=lambda *_: {"outcome": "satisfied", "established_conditions": ["implemented"], "artifacts": ["fixture"]})
    receipt_document = worker.handle({"payload_json": request.payload})
    receipt = Receipt(str(Path(allocation.identity["worktree"]) / "implementation-receipt.json"), receipt_document)
    if receipt.document.get("execution_envelope", {}).get("plan_binding") != todo.binding:
        raise RuntimeError("fixture receipt lost Plan binding")
    if _git(source_root, "status", "--porcelain=v1") != source_before:
        raise RuntimeError("fixture changed source files")
    return {"plan_solution": asdict(plan), "ready_leaf": asdict(ready), "plan_bound_todo": asdict(todo),
        "coding_request": asdict(request), "allocated_worktree": asdict(allocation),
        "coding_execution": asdict(CodingExecution(receipt.document["execution_envelope"])),
        "receipt": asdict(receipt), "updated_leaf_evidence": {"receipt": receipt.path, "binding": todo.binding},
        "ordinary_runtime_effects": []}
