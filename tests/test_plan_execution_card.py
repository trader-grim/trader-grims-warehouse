import copy

import pytest

from tgw.development.plan_binding import execution_root_hash
from tgw.development.plan_todo_bridge import bind_leaf
from tgw.plan_execution_card import PlanExecutionCardError, build_execution_card, card_hash, select_next_execution_card, validate_execution_card
from tgw.plan_solver import solve
from tgw.workflow import compile_solution_runtime


COMMIT = "a" * 40
SOURCE = "b" * 40
TREE = "c" * 40


def _resolved():
    document = {
        "schema": "tgw-plan/v2", "plan_commit": COMMIT, "capabilities": ["one@1", "two@1", "promptcraft.receiver-profiles@1"],
        "providers": [{"id": "one", "provides": ["one@1"]}, {"id": "two", "provides": ["two@1"], "requires": ["one@1"]}, {"id": "recovered-promptcraft", "provides": ["promptcraft.receiver-profiles@1"]}],
        "observations": [],
        "target": {"id": "plan", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["one@1", "two@1", "promptcraft.receiver-profiles@1"]},
    }
    native = solve(document)
    solution = solve(document, conformance_result={"available": True, "closure_hash": native["closure_hash"]})
    return solution, compile_solution_runtime(solution, current_plan_commit=COMMIT)


def _root():
    value = {"schema": "tgw-execution-root/v1", "kind": "plan", "plan_id": "plan", "profile": "implementation", "plan_commit": COMMIT}
    return {**value, "identity_hash": execution_root_hash(value)}


def _resources():
    names = ("plan_input", "plan_commit", "plan_graph", "codegraph_snapshot", "source_tree", "execution_environment", "authority_conditions", "candidate_evidence", "receipt_sink")
    return {name: {"ref": f"test:{name}", "hash": "sha256:" + name.encode().hex().ljust(64, "0")[:64]} for name in names}


def _graph():
    return {"plan_id": "plan", "work_units": [
        {"id": "W1", "title": "Build one", "establishes": ["one@1"], "acceptance": ["one is tested"]},
        {"id": "W2", "title": "Build two", "establishes": ["two@1"], "acceptance": ["two is tested"]},
        {"id": "W0", "title": "Recover Promptcraft", "establishes": ["promptcraft.receiver-profiles@1"], "acceptance": ["Promptcraft is bound"]},
    ]}


def _card(treatment="establish:one@1"):
    solution, compiled = _resolved()
    return build_execution_card(compiled=compiled, solution=solution, execution_graph=_graph(), treatment_id=treatment,
                                source_commit=SOURCE, source_tree=TREE, resources=_resources(), environment={"id": "dev"}, execution_root=_root()), compiled, solution


def test_card_is_deterministic_and_mechanically_renders_task_and_priority():
    first, _, _ = _card()
    second, _, _ = _card()
    assert first == second
    assert first["task"]["body"].startswith("Plan work unit W1: Build one")
    assert first["scheduling"]["transport_priority"] > 0
    assert validate_execution_card(first) == first


def test_card_requires_all_registered_resource_bindings_and_exact_solution():
    solution, compiled = _resolved()
    resources = _resources(); resources.pop("codegraph_snapshot")
    with pytest.raises(PlanExecutionCardError, match="resources"):
        build_execution_card(compiled=compiled, solution=solution, execution_graph=_graph(), treatment_id="establish:one@1",
                             source_commit=SOURCE, source_tree=TREE, resources=resources, environment={"id": "dev"}, execution_root=_root())


def test_provider_only_solved_leaf_has_a_mechanical_not_manual_card():
    card, compiled, solution = _card()
    graph = {"plan_id": "plan", "work_units": [item for item in _graph()["work_units"] if item["id"] != "W1"]}
    derived = build_execution_card(compiled=compiled, solution=solution, execution_graph=graph, treatment_id="establish:one@1",
                                   source_commit=SOURCE, source_tree=TREE, resources=_resources(), environment={"id": "dev"}, execution_root=_root())
    assert derived["work_unit"]["id"] == "establish:one@1"
    assert "Establish one@1" in derived["task"]["body"]


def test_execution_card_is_not_a_plan_todo_transport_input():
    _card_value, compiled, solution = _card()
    rows = []
    def create(agent, body, priority, *_):
        row = {"id": 1, "status_note": "", "body": body, "priority": priority}; rows.append(row); return row
    def note(_id, value): rows[0]["status_note"] = value
    result = bind_leaf(compiled, solution=solution, treatment_id="establish:one@1", source_commit=SOURCE,
                       worktree_identity="request", agent="codex", body="implement one", priority=7, create_todo=create,
                       list_todos=lambda: rows, allocate_worktree=lambda *_: {"worktree": "/worktrees/test"}, set_status_note=note,
                       execution_root=_root())
    assert rows[0]["body"] == "implement one"
    assert rows[0]["priority"] == 7
    assert "execution_card" not in result["binding"]
    with pytest.raises(TypeError, match="execution_envelope"):
        bind_leaf(compiled, solution=solution, treatment_id="establish:one@1", source_commit=SOURCE,
                  worktree_identity="second", agent="codex", body="implement one", priority=7,
                  execution_envelope={"legacy": True}, create_todo=create,
                  list_todos=lambda: rows, allocate_worktree=lambda *_: {"worktree": "/worktrees/test2"}, set_status_note=note,
                  execution_root=_root())


def test_stale_or_mismatched_card_refuses_closed():
    card, compiled, _solution = _card()
    stale = copy.deepcopy(card); stale["plan"]["commit"] = "d" * 40
    with pytest.raises(PlanExecutionCardError, match="hash"):
        validate_execution_card(stale, compiled=compiled)
    mismatched = copy.deepcopy(card); mismatched["work_unit"]["treatment_id"] = "establish:two@1"; mismatched["card_hash"] = "sha256:" + "0" * 64
    with pytest.raises(PlanExecutionCardError):
        validate_execution_card(mismatched, compiled=compiled)


def test_next_eligible_card_selection_is_phase_then_provider_deterministic():
    _one, compiled, solution = _card()
    selected = select_next_execution_card(compiled=compiled, solution=solution, execution_graph=_graph(),
                                          source_commit=SOURCE, source_tree=TREE, resources=_resources(),
                                          environment={"id": "dev"}, execution_root=_root())
    again = select_next_execution_card(compiled=compiled, solution=solution, execution_graph=_graph(),
                                       source_commit=SOURCE, source_tree=TREE, resources=_resources(),
                                       environment={"id": "dev"}, execution_root=_root())
    assert selected == again
