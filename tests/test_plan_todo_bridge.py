from tgw.development.plan_todo_bridge import PlanTodoBridgeError, bind_leaf
from tgw.plan_solver import solve
from tgw.workflow import compile_solution_runtime
from unittest.mock import MagicMock, patch
from tgw.development.foreman import TodoRecord, tick
from tgw.development.plan_binding import MalformedPlanBindingError
from tgw.workers.coding import CodingWorker
from tgw.workflow_kernel.contracts import RuntimeWorkGraph, TreatmentDisposition
from tgw.development.foreman import EVALUATOR_VERSION
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw import coding_provision

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def _compiled(conformant=True):
    document = {"schema":"tgw-plan/v2","plan_commit":COMMIT,"capabilities":["base@1"],"providers":[{"id":"base","provides":["base@1"]}],"observations":[],"target":{"id":"x","profile":"implementation","minimum_state":"admitted","required_capabilities":["base@1"]}}
    native = solve(document)
    solution = solve(document, conformance_result={"available": conformant, "closure_hash": native["closure_hash"]}) if conformant else native
    return solution, compile_solution_runtime(solution, current_plan_commit=COMMIT)


def test_luet_leaf_creates_one_bound_todo_and_is_idempotent():
    solution, compiled = _compiled(); rows = []
    def create(agent, body, priority, source, pp, anchor):
        row = {"id": len(rows)+1, "agent":agent, "body":body, "status_note":""}; rows.append(row); return row
    def note(todo_id, value): rows[todo_id-1]["status_note"] = value
    allocator = lambda todo_id, request_id, source: {"worktree": f"/worktrees/todo-{todo_id}-{request_id}", "todo_id": todo_id}
    first = bind_leaf(compiled, solution=solution, treatment_id="establish:base@1", source_commit="a"*40, worktree_identity="request-a", agent="codex", body="implement", priority=10, create_todo=create, list_todos=lambda: rows, allocate_worktree=allocator, set_status_note=note)
    second = bind_leaf(compiled, solution=solution, treatment_id="establish:base@1", source_commit="a"*40, worktree_identity="request-a", agent="codex", body="implement", priority=10, create_todo=create, list_todos=lambda: rows, allocate_worktree=allocator, set_status_note=note)
    assert first["created"] and not second["created"] and first["todo_id"] == second["todo_id"]
    assert first["binding"]["worktree_identity"]["todo_id"] == first["todo_id"]


def test_held_solution_refuses_without_todo():
    solution, compiled = _compiled(False)
    try:
        bind_leaf(compiled, solution=solution, treatment_id="establish:base@1", source_commit="a"*40, worktree_identity="request-a", agent="codex", body="x", priority=1, create_todo=lambda *_: {}, list_todos=lambda: [], allocate_worktree=lambda *_: {}, set_status_note=lambda *_: None)
        assert False
    except PlanTodoBridgeError:
        pass


def test_changed_worktree_identity_creates_superseding_todo():
    solution, compiled = _compiled(); rows = []
    def create(*_):
        row = {"id": len(rows)+1, "status_note":""}; rows.append(row); return row
    def note(todo_id, value): rows[todo_id-1]["status_note"] = value
    allocator = lambda todo_id, request_id, source: {"worktree": f"/worktrees/{request_id}", "todo_id": todo_id}
    first = bind_leaf(compiled, solution=solution, treatment_id="establish:base@1", source_commit="a"*40, worktree_identity="one", agent="codex", body="x", priority=1, create_todo=create, list_todos=lambda: rows, allocate_worktree=allocator, set_status_note=note)
    second = bind_leaf(compiled, solution=solution, treatment_id="establish:base@1", source_commit="a"*40, worktree_identity="two", agent="codex", body="x", priority=1, create_todo=create, list_todos=lambda: rows, allocate_worktree=allocator, set_status_note=note)
    assert second["todo_id"] != first["todo_id"]
    assert second["binding"]["supersedes_todo_id"] == first["todo_id"]


def test_bridge_todo_foreman_payload_and_execution_envelope_retain_plan_binding(tmp_path):
    solution, compiled = _compiled(); rows = []
    def create(*_):
        row = {"id": 1, "agent":"codex", "body":"implement", "status_note":""}; rows.append(row); return row
    def note(_, value): rows[0]["status_note"] = value
    worktree = tmp_path / "allocated"; worktree.mkdir()
    location = {"worktree":str(worktree), "todo_id":1, "branch":"coding/test", "head":"a"*40}
    result = bind_leaf(compiled, solution=solution, treatment_id="establish:base@1", source_commit="a"*40, worktree_identity="allocated", agent="codex", body="implement", priority=1, create_todo=create, list_todos=lambda: rows, allocate_worktree=lambda *_: location, set_status_note=note)
    graph = RuntimeWorkGraph("runtime-work-graph/v1", "graph", location["worktree"], "gen", CODING_READY_FOR_IMPLEMENTATION.identity, "1", EVALUATOR_VERSION, "evidence", "condition", "registry", (), (), (), (), (TreatmentDisposition("codex-implement", "1", ("ready",)),), (), (), (), ())
    enqueue = MagicMock(return_value="job-1")
    todo = TodoRecord(1, "codex", 1, "implement", location["worktree"], result["binding"])
    with patch("tgw.development.foreman.validated_coding_worktree", return_value=__import__("pathlib").Path(location["worktree"])), patch("tgw.development.foreman.build_coding_snapshot", return_value=object()), patch("tgw.development.foreman.evaluate", return_value=graph):
        assert tick(fetch_todos=lambda:[todo], check_active_fn=lambda _:False, enqueue_fn=enqueue).dispatched == 1
    payload = enqueue.call_args.kwargs["payload"]
    assert payload["todo_id"] == 1 and payload["worktree"] == location["worktree"] and payload["plan_binding"] == result["binding"]
    worker = CodingWorker("codex-implement", {"coding": {}}, launcher=lambda *_: {"outcome":"satisfied", "established_conditions":["implemented"], "artifacts":[]})
    with patch.object(worker, "_validated_worktree", return_value=__import__("pathlib").Path(location["worktree"])):
        receipt = worker.handle({"payload_json": payload})
    assert receipt["execution_envelope"]["plan_binding"] == result["binding"]


def test_malformed_plan_bound_todo_refuses_tick_instead_of_skipping():
    malformed = '{"schema":"tgw-plan-coding-todo/v1","plan_commit":"missing-fields"}'
    with patch("tgw.development.foreman._default_fetch_open_todos", side_effect=MalformedPlanBindingError("Todo 7 has malformed Plan binding")):
        result = tick()
    assert result.errors == 1
    assert result.refused_plan_binding == 1
