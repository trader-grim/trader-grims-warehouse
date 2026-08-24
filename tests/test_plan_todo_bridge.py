from unittest.mock import MagicMock, patch

import pytest

from tgw.development.foreman import EVALUATOR_VERSION, TodoRecord, tick
from tgw.development.plan_binding import MalformedPlanBindingError
from tgw.development.plan_todo_bridge import PlanTodoBridgeError, bind_leaf
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.plan_solver import solve
from tgw.workers.coding import CodingWorker
from tgw.workflow import compile_solution_runtime
from tgw.workflow_kernel.contracts import RuntimeWorkGraph, TreatmentDisposition

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def _compiled(conformant=True, root_id="x"):
    document = {
        "schema": "tgw-plan/v2",
        "plan_commit": COMMIT,
        "capabilities": ["base@1", "promptcraft.receiver-profiles@1"],
        "providers": [{"id": "base", "provides": ["base@1"]}, {"id": "recovered-promptcraft", "provides": ["promptcraft.receiver-profiles@1"]}],
        "observations": [],
        "target": {"id": root_id, "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["base@1", "promptcraft.receiver-profiles@1"]},
    }
    native = solve(document)
    solution = solve(document, conformance_result={"available": conformant, "closure_hash": native["closure_hash"]}) if conformant else native
    return solution, compile_solution_runtime(solution, current_plan_commit=COMMIT)


def test_luet_leaf_creates_one_bound_todo_and_is_idempotent():
    solution, compiled = _compiled()
    rows = []

    def create(agent, body, priority, source, pp, anchor):
        row = {"id": len(rows) + 1, "agent": agent, "body": body, "status_note": ""}
        rows.append(row)
        return row

    def note(todo_id, value):
        rows[todo_id - 1]["status_note"] = value

    def allocator(todo_id, request_id, source):
        return {
            "worktree": f"/worktrees/todo-{todo_id}-{request_id}",
            "todo_id": todo_id,
        }

    first = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="request-a",
        agent="codex",
        body="implement",
        priority=10,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
    )
    second = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="request-a",
        agent="codex",
        body="implement",
        priority=10,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
    )
    assert first["created"] and not second["created"] and first["todo_id"] == second["todo_id"]
    assert first["binding"]["worktree_identity"]["todo_id"] == first["todo_id"]
    assert first["binding"]["execution_root"]["kind"] == "plan"
    assert rows[0].get("pp_ref") is None


def test_held_solution_refuses_without_todo():
    solution, compiled = _compiled(False)
    try:
        bind_leaf(
            compiled,
            solution=solution,
            treatment_id="establish:base@1",
            source_commit="a" * 40,
            worktree_identity="request-a",
            agent="codex",
            body="x",
            priority=1,
            create_todo=lambda *_: {},
            list_todos=lambda: [],
            allocate_worktree=lambda *_: {},
            set_status_note=lambda *_: None,
        )
        assert False
    except PlanTodoBridgeError:
        pass


def test_changed_worktree_identity_creates_superseding_todo():
    solution, compiled = _compiled()
    rows = []

    def create(*_):
        row = {"id": len(rows) + 1, "status_note": ""}
        rows.append(row)
        return row

    def note(todo_id, value):
        rows[todo_id - 1]["status_note"] = value

    def allocator(todo_id, request_id, source):
        return {"worktree": f"/worktrees/{request_id}", "todo_id": todo_id}

    first = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="one",
        agent="codex",
        body="x",
        priority=1,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
    )
    second = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="two",
        agent="codex",
        body="x",
        priority=1,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
    )
    assert second["todo_id"] != first["todo_id"]
    assert second["binding"]["supersedes_todo_id"] == first["todo_id"]


def test_explicit_pp_root_uses_actual_pp_reference_and_supersedes():
    solution, compiled = _compiled(root_id="PP-WORKFLOW-001")
    rows = []
    calls = []

    def create(agent, body, priority, source, pp_ref, anchor):
        calls.append((source, pp_ref, anchor))
        row = {"id": len(rows) + 1, "status_note": ""}
        rows.append(row)
        return row

    def note(todo_id, value):
        rows[todo_id - 1]["status_note"] = value

    def allocator(todo_id, request_id, source):
        return {"worktree": f"/worktrees/{request_id}", "todo_id": todo_id}

    root = {"schema": "tgw-execution-root/v1", "kind": "pp", "pp_ref": "PP-WORKFLOW-001"}
    first = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="one",
        agent="codex",
        body="x",
        priority=1,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
        execution_root=root,
    )
    again = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="one",
        agent="codex",
        body="x",
        priority=1,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
        execution_root=root,
    )
    second = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="two",
        agent="codex",
        body="x",
        priority=1,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=allocator,
        set_status_note=note,
        execution_root=root,
    )
    assert calls == [("plan-luet-bridge", "PP-WORKFLOW-001", "establish:base@1")] * 2
    assert again["todo_id"] == first["todo_id"] and not again["created"]
    assert second["binding"]["execution_root"]["kind"] == "pp"
    assert second["binding"]["supersedes_todo_id"] == first["todo_id"]


def test_explicit_todo_root_reuses_canonical_todo_without_duplicate():
    solution, compiled = _compiled()
    rows = [{"id": 41, "status_note": ""}]
    creates = MagicMock()

    def note(todo_id, value):
        rows[0]["status_note"] = value

    result = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="selected",
        agent="codex",
        body="x",
        priority=1,
        create_todo=creates,
        list_todos=lambda: rows,
        allocate_worktree=lambda todo_id, request_id, source: {"worktree": f"/worktrees/{request_id}", "todo_id": todo_id},
        set_status_note=note,
        execution_root={"schema": "tgw-execution-root/v1", "kind": "todo", "todo_id": 41},
    )
    again = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="selected",
        agent="codex",
        body="x",
        priority=1,
        create_todo=creates,
        list_todos=lambda: rows,
        allocate_worktree=lambda *_: {},
        set_status_note=note,
        execution_root={"schema": "tgw-execution-root/v1", "kind": "todo", "todo_id": 41},
    )
    assert result["todo_id"] == 41 and not result["created"]
    assert again["todo_id"] == 41 and not again["created"]
    assert result["binding"]["execution_root"]["todo_id"] == 41
    creates.assert_not_called()


def test_mismatched_or_fake_execution_root_refuses_closed():
    solution, compiled = _compiled()
    common = dict(
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="x",
        agent="codex",
        body="x",
        priority=1,
        create_todo=lambda *_: {},
        list_todos=lambda: [],
        allocate_worktree=lambda *_: {},
        set_status_note=lambda *_: None,
    )
    for root in (
        {"schema": "tgw-execution-root/v1", "kind": "plan", "plan_id": "wrong", "profile": "implementation", "plan_commit": COMMIT},
        {"schema": "tgw-execution-root/v1", "kind": "pp", "pp_ref": "PLAN-GOVERNED-EXECUTION-PLATFORM"},
        {"schema": "tgw-execution-root/v1", "kind": "plan", "plan_id": "x", "profile": "implementation", "plan_commit": COMMIT, "identity_hash": "sha256:wrong"},
        {"schema": "tgw-execution-root/v1", "kind": "todo", "todo_id": 1, "unexpected": "field"},
    ):
        try:
            bind_leaf(compiled, execution_root=root, **common)
            assert False
        except PlanTodoBridgeError:
            pass
    tampered = dict(solution)
    tampered["root"] = {"id": "PP-WORKFLOW-001", "profile": "implementation"}
    with pytest.raises(PlanTodoBridgeError, match="integrity"):
        bind_leaf(
            compiled,
            execution_root={"schema": "tgw-execution-root/v1", "kind": "pp", "pp_ref": "PP-WORKFLOW-001"},
            **{**common, "solution": tampered},
        )


@pytest.mark.parametrize(
    ("root_id", "execution_root", "existing_id"),
    (
        ("x", None, None),
        ("PP-WORKFLOW-001", {"schema": "tgw-execution-root/v1", "kind": "pp", "pp_ref": "PP-WORKFLOW-001"}, None),
        ("x", {"schema": "tgw-execution-root/v1", "kind": "todo", "todo_id": 41}, 41),
    ),
)
def test_bridge_todo_foreman_payload_and_receipt_retain_plan_binding(tmp_path, root_id, execution_root, existing_id):
    solution, compiled = _compiled(root_id=root_id)
    rows = [] if existing_id is None else [{"id": existing_id, "agent": "codex", "body": "implement", "status_note": ""}]

    def create(*_):
        row = {"id": len(rows) + 1, "agent": "codex", "body": "implement", "status_note": ""}
        rows.append(row)
        return row

    def note(todo_id, value):
        next(row for row in rows if row["id"] == todo_id)["status_note"] = value

    worktree = tmp_path / "allocated"
    worktree.mkdir()
    location = {"worktree": str(worktree), "todo_id": 1, "branch": "coding/test", "head": "a" * 40}
    result = bind_leaf(
        compiled,
        solution=solution,
        treatment_id="establish:base@1",
        source_commit="a" * 40,
        worktree_identity="allocated",
        agent="codex",
        body="implement",
        priority=1,
        create_todo=create,
        list_todos=lambda: rows,
        allocate_worktree=lambda *_: {**location, "todo_id": _[0]},
        set_status_note=note,
        execution_root=execution_root,
    )
    graph = RuntimeWorkGraph(
        "runtime-work-graph/v1",
        "graph",
        location["worktree"],
        "gen",
        CODING_READY_FOR_IMPLEMENTATION.identity,
        "1",
        EVALUATOR_VERSION,
        "evidence",
        "condition",
        "registry",
        (),
        (),
        (),
        (),
        (TreatmentDisposition("codex-implement", "1", ("ready",)),),
        (),
        (),
        (),
        (),
    )
    enqueue = MagicMock(return_value="job-1")
    todo = TodoRecord(result["todo_id"], "codex", 1, "implement", location["worktree"], result["binding"])
    with (
        patch("tgw.development.foreman.validated_coding_worktree", return_value=__import__("pathlib").Path(location["worktree"])),
        patch("tgw.development.foreman.build_coding_snapshot", return_value=object()),
        patch("tgw.development.foreman.evaluate", return_value=graph),
    ):
        assert tick(fetch_todos=lambda: [todo], check_active_fn=lambda _: False, enqueue_fn=enqueue).dispatched == 1
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["todo_id"] == result["todo_id"] and payload["worktree"] == location["worktree"] and payload["plan_binding"] == result["binding"]
    worker = CodingWorker("codex-implement", {"coding": {}}, launcher=lambda *_: {"outcome": "satisfied", "established_conditions": ["implemented"], "artifacts": []})
    with patch.object(worker, "_validated_worktree", return_value=__import__("pathlib").Path(location["worktree"])):
        receipt = worker.handle({"payload_json": payload})
    assert receipt["plan_binding"] == result["binding"]
    assert "execution_envelope" not in receipt


def test_malformed_plan_bound_todo_refuses_tick_instead_of_skipping():
    with patch("tgw.development.foreman._default_fetch_open_todos", side_effect=MalformedPlanBindingError("Todo 7 has malformed Plan binding")):
        result = tick()
    assert result.errors == 1
    assert result.refused_plan_binding == 1
