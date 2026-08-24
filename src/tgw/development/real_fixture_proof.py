"""One bounded real-adapter proof for the Plan-bound coding spine."""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tgw.coding_provision_worker import _prepare_request_worktree
from tgw.development.fixture_isolation import (
    cleanup_fixture_run, create_fixture_todo, fixture_enqueue, fixture_todo_record,
    fixture_worktree_root, list_fixture_todos, run_fixture_job_once, validate_fixture_run_id,
)
from tgw.development.foreman import ForemanConfig, tick
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.plan_solver import solve
from tgw.workflow import compile_solution_runtime


@dataclass(frozen=True)
class PlanSolution:
    document: dict[str, Any]
    solution: dict[str, Any]


@dataclass(frozen=True)
class ReadyLeaf:
    treatment_id: str
    capability: str
    closure_hash: str


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(root), *args], check=True, text=True, capture_output=True)
    return completed.stdout.strip()


def _fixture_solution(commit: str) -> PlanSolution:
    document = {
        "schema": "tgw-plan/v2", "plan_commit": commit,
        "capabilities": ["fixture.code@1"],
        "providers": [{"id": "fixture", "provides": ["fixture.code@1"]}],
        "observations": [],
        "target": {"id": "fixture", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["fixture.code@1"]},
    }
    native = solve(document)
    return PlanSolution(document, solve(document, conformance_result={"available": True, "closure_hash": native["closure_hash"]}))


def run_real_fixture_proof(*, run_id: str, source_root: Path, coding: dict[str, Any], candidate_commit: str) -> dict[str, Any]:
    """Run one retained real Todo/queue/worktree proof, then return its evidence.

    The caller must call ``cleanup_fixture_run`` only after retaining the returned
    evidence.  This function never looks at ordinary Todo rows or queue names.
    """
    run_id = validate_fixture_run_id(run_id)
    source_root = source_root.resolve()
    if _git(source_root, "rev-parse", "HEAD") != candidate_commit:
        raise ValueError("source does not resolve to the requested candidate")
    canonical_root = Path(str(coding["worktree_root"])).resolve()
    fixture_root = fixture_worktree_root(canonical_root, run_id)
    if fixture_root.exists() and any(fixture_root.iterdir()):
        raise ValueError("fixture run root must be empty")
    fixture_root.mkdir(parents=True, exist_ok=True)
    imported = [
        __import__("tgw.development.plan_todo_bridge", fromlist=["x"]).__file__,
        __import__("tgw.development.foreman", fromlist=["x"]).__file__,
        __import__("tgw.workers.coding", fromlist=["x"]).__file__,
    ]
    if any(not str(path).startswith(str(source_root / "src")) or any(part in str(path) for part in ("actor-runtime", "site-packages", "ppworkflow-")) for path in imported):
        raise ValueError("imports are not bound to candidate source")
    proof_coding = dict(coding, worktree_root=str(fixture_root), repository_root=str(source_root))
    plan = _fixture_solution(candidate_commit)
    compiled = compile_solution_runtime(plan.solution, current_plan_commit=candidate_commit)
    ready = ReadyLeaf("establish:fixture.code@1", "fixture.code@1", compiled.closure_hash)

    def create(agent: str, body: str, priority: int, _source: str, pp_ref: str | None, plan_anchor: str | None) -> dict[str, Any]:
        return dict(create_fixture_todo(run_id, agent=agent, body=body, priority=priority, pp_ref=pp_ref, plan_anchor=plan_anchor))

    def allocate(todo_id: int, request_id: str, source_commit: str) -> dict[str, Any]:
        return _prepare_request_worktree({"todo_id": todo_id, "request_id": request_id, "source_commit": source_commit}, proof_coding, f"fixture-worker:{run_id}")

    from tgw.todo import todo_set_status_note
    bound = bind_leaf(
        compiled, solution=plan.solution, treatment_id=ready.treatment_id,
        source_commit=candidate_commit, worktree_identity=run_id, agent="codex",
        body="fixture-only Plan-bound coding proof", priority=1, create_todo=create,
        list_todos=lambda: list_fixture_todos(run_id),
        allocate_worktree=allocate,
        set_status_note=lambda todo_id, note: todo_set_status_note(todo_id, note, suppress_plan_render=True),
        fixture_run_id=run_id,
    )
    record = fixture_todo_record(run_id, bound["todo_id"])
    outcome = tick(
        config=ForemanConfig(coding_config=proof_coding), todo_ids={record.todo_id},
        fetch_todos=lambda: [record], enqueue_fn=fixture_enqueue(run_id),
    )
    if outcome.dispatched != 1:
        raise RuntimeError("fixture foreman did not dispatch exactly one job")
    from tgw.queue.state_machine import _conn
    with _conn() as con, con.cursor() as cur:
        cur.execute("SELECT job_id::text FROM queue_jobs WHERE queue_name = %s AND payload_json->>'fixture_run_id' = %s", (f"tgw-fixture-codex-implement:{run_id}", run_id))
        rows = cur.fetchall()
    if len(rows) != 1:
        raise RuntimeError("fixture queue did not contain exactly one job")
    job_id = rows[0][0]
    receipt = run_fixture_job_once(run_id, job_id=job_id, config={"coding": proof_coding}, launcher=lambda *_: {"outcome": "satisfied", "established_conditions": ["implemented"], "artifacts": ["fixture"]})
    binding = bound["binding"]
    if receipt.get("execution_envelope", {}).get("plan_binding") != binding:
        raise RuntimeError("fixture receipt lost Plan binding")
    return {"plan_solution": asdict(plan), "ready_leaf": asdict(ready), "plan_bound_todo": bound,
            "coding_request": {"job_id": job_id}, "allocated_worktree": binding["worktree_identity"],
            "coding_execution": receipt["execution_envelope"], "receipt": receipt,
            "cleanup": lambda: cleanup_fixture_run(run_id, canonical_worktree_root=canonical_root, repository_root=source_root)}
