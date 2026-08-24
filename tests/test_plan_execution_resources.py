import copy

import pytest

from tgw.development.plan_binding import execution_root_hash
from tgw.plan_execution_resources import (
    ExecutionResourceContext, PlanExecutionResourceError, bind_execution_envelope, context_from_registered_status,
    validate_execution_envelope,
)
from tgw.plan_solver import solve
from tgw.workflow import compile_solution_runtime


PLAN = "a" * 40
PLAN_TREE = "b" * 40
SOURCE = "c" * 40
SOURCE_TREE = "d" * 40
HASH = "sha256:" + "0" * 64


def binding(name):
    return {"ref": f"mcp:tgw-context/{name}", "hash": HASH}


def context():
    return {
        "schema": "tgw-plan-execution-resource-context/v1",
        "context": {"service": "tgw-context", "status_hash": HASH},
        "plan": {"commit": PLAN, "tree": PLAN_TREE, "input": binding("approved-plan"), "graph": binding("plan-graph")},
        "codegraph": {"commit": SOURCE, "tree": SOURCE_TREE, "snapshot": binding("codegraph")},
        "source": {"commit": SOURCE, "tree": SOURCE_TREE, "tree_ref": binding("source-tree")},
        "environment": {"profile": "development", "catalog": binding("environment")},
        "authority": {"solution_hash": HASH, "closure_hash": HASH, "conditions": binding("authority")},
        "candidate_evidence": {"clean": True, "binding": binding("candidate")},
        "receipt_sink": binding("receipt-sink"),
        "receiver": {"capability": "promptcraft.receiver-profiles@1", "provider": "recovered-promptcraft", "handoff": {"adapter": "promptcraft-card-handoff", "schema": "tgw-launcher-handoff/v1"}},
    }


def resolved():
    document = {
        "schema": "tgw-plan/v2", "plan_commit": PLAN,
        "capabilities": ["plan.graph-query@1", "promptcraft.receiver-profiles@1"],
        "providers": [{"id": "canonical-plan-graph", "provides": ["plan.graph-query@1"]}, {"id": "recovered-promptcraft", "provides": ["promptcraft.receiver-profiles@1"]}],
        "observations": [],
        "target": {"id": "PLAN", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["plan.graph-query@1", "promptcraft.receiver-profiles@1"]},
    }
    native = solve(document)
    solution = solve(document, conformance_result={"available": True, "closure_hash": native["closure_hash"]})
    # Use the exact dynamic values in a registered context fixture.
    bound = context(); bound["authority"] = {"solution_hash": solution["solution_hash"], "closure_hash": solution["closure_hash"], "conditions": binding("authority")}
    root = {"schema": "tgw-execution-root/v1", "kind": "plan", "plan_id": "PLAN", "profile": "implementation", "plan_commit": PLAN}
    return solution, compile_solution_runtime(solution, current_plan_commit=PLAN), bound, {**root, "identity_hash": execution_root_hash(root)}


def envelope():
    solution, compiled, raw_context, root = resolved()
    return bind_execution_envelope(
        compiled=compiled, solution=solution,
        execution_graph={"plan_id": "PLAN", "work_units": [{"id": "W-graph", "title": "Provide Plan graph", "establishes": ["plan.graph-query@1"], "acceptance": ["typed graph query is available"]}, {"id": "W-promptcraft", "title": "Promptcraft", "establishes": ["promptcraft.receiver-profiles@1"], "acceptance": ["receiver bound"]}]},
        treatment_id="establish:plan.graph-query@1", source_commit=SOURCE, source_tree=SOURCE_TREE,
        context=ExecutionResourceContext.parse(raw_context), execution_root=root,
    ), compiled


def test_binds_current_plan_graph_query_card_from_registered_context_only():
    bound, compiled = envelope()
    assert validate_execution_envelope(bound, compiled=compiled) == bound
    assert bound["card"]["work_unit"]["capability"] == "plan.graph-query@1"
    assert bound["card"]["receiver"]["capability_provider"] == "recovered-promptcraft"
    assert set(bound["resources"]) == {
        "plan_input", "plan_commit", "plan_graph", "codegraph_snapshot", "source_tree",
        "execution_environment", "authority_conditions", "candidate_evidence", "receipt_sink",
    }


def test_registered_status_is_the_only_real_context_constructor():
    solution, _compiled, _raw, _root = resolved()
    status = {
        "schema": "tgw-context-service/v1", "ok": True, "context_sha256": HASH,
        "plan": {"approved_commit": PLAN, "approved_tree": PLAN_TREE, "sources": {"plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml": {"sha256": HASH}}},
        "source": {"commit": SOURCE, "tree": SOURCE_TREE, "working_tree_clean": True, "status_sha256": HASH},
        "code_graph": {"commit": SOURCE, "tree": SOURCE_TREE, "freshness_hash": HASH},
        "environment": {"catalog_hash": HASH},
    }
    derived = context_from_registered_status(status, solution=solution, plan_graph_hash=HASH, receipt_sink=binding("receipt-sink"))
    assert derived.value["plan"]["input"]["ref"].startswith("mcp:tgw-context/")
    assert derived.value["candidate_evidence"]["binding"]["hash"] == HASH


@pytest.mark.parametrize("mutate", [
    lambda value: value["plan"].pop("graph"),
    lambda value: value["source"].update({"commit": "e" * 40}),
    lambda value: value["plan"]["input"].update({"ref": "file:///opt/TGW/w/historical/plan"}),
    lambda value: value["environment"].update({"profile": "production"}),
    lambda value: value["candidate_evidence"].update({"clean": False}),
])
def test_missing_stale_historical_or_unregistered_context_refuses_closed(mutate):
    value = context(); mutate(value)
    with pytest.raises(PlanExecutionResourceError):
        ExecutionResourceContext.parse(value)


def test_envelope_tampering_or_resource_substitution_refuses_closed():
    bound, compiled = envelope()
    tampered = copy.deepcopy(bound)
    tampered["resources"]["receipt_sink"] = binding("different-receipt-sink")
    with pytest.raises(PlanExecutionResourceError):
        validate_execution_envelope(tampered, compiled=compiled)
