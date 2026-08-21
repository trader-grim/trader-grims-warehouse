from copy import deepcopy

import pytest

from tgw.plan_solver import ExecutionGraphAdapter, PlanResolutionError, StalePlanCommit, solve, validate_for_dispatch

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def graph(*, capabilities, providers, required, observations=(), minimum_state="admitted"):
    return {
        "schema": "tgw-plan/v2",
        "plan_commit": COMMIT,
        "capabilities": capabilities,
        "providers": providers,
        "observations": observations,
        "target": {"id": "fixture", "profile": "implementation", "minimum_state": minimum_state, "required_capabilities": required},
    }


def test_global_solution_does_not_greedily_choose_highest_provider():
    document = graph(
        capabilities=["app@1", "db@1", "exclusive@1"],
        required=["app@1", "exclusive@1"],
        providers=[
            {"id": "app-high", "provides": ["app@1"], "requires": ["db@1"], "preference": 100},
            {"id": "app-low", "provides": ["app@1"], "preference": 10},
            {"id": "db", "provides": ["db@1"], "conflicts": ["exclusive@1"]},
            {"id": "exclusive", "provides": ["exclusive@1"]},
        ],
    )

    result = solve(document, expected_plan_commit=COMMIT)

    assert result["complete"] is True
    assert result["selected_providers"] == ["app-low", "exclusive"]


@pytest.mark.parametrize(
    ("capabilities", "providers", "expected"),
    [
        ([], [], "UNKNOWN_CAPABILITY"),
        (["wanted@1"], [], "UNSATISFIED"),
        (["wanted@1"], [{"id": "later", "provides": ["wanted@1"], "status": "blocked", "blocked_reason": "maintenance"}], "BLOCKED"),
    ],
)
def test_unresolved_outcomes_remain_distinct(capabilities, providers, expected):
    result = solve(graph(capabilities=capabilities, providers=providers, required=["wanted@1"]))

    assert result["complete"] is False
    assert result["dispatchable"] is False
    assert result["unresolved"][0]["code"] == expected


def test_installed_state_is_reused_and_partial_state_becomes_work():
    document = graph(
        capabilities=["queue@1", "app@1"],
        required=["app@1"],
        providers=[
            {"id": "app", "provides": ["app@1"], "requires": ["queue@1"]},
            {"id": "queue", "provides": ["queue@1"]},
        ],
        observations=[
            {"capability": "queue@1", "provider": "queue", "state": "admitted", "evidence": ["receipt:q"]},
            {"capability": "app@1", "provider": "app", "state": "partial", "evidence": ["receipt:todo"]},
        ],
    )

    result = solve(document)

    assert result["satisfied_installed"] == [{"capability": "queue@1", "provider": "queue", "state": "admitted", "evidence": ["receipt:q"]}]
    assert result["work_units"] == [
        {
            "id": "establish:app@1",
            "capability": "app@1",
            "establishes": ["app@1@admitted"],
            "selected_provider": "app",
            "requires_capabilities": ["queue@1"],
            "resume_from": ["receipt:todo"],
        }
    ]


def test_superseded_observation_invalidates_old_receipts() -> None:
    document = graph(
        capabilities=["app@1"],
        required=["app@1"],
        providers=[
            {"id": "app-old", "provides": ["app@1"], "status": "superseded"},
            {"id": "app-current", "provides": ["app@1"]},
        ],
        observations=[
            {
                "capability": "app@1",
                "provider": "app-old",
                "state": "superseded",
                "evidence": ["receipt:old-observation"],
                "invalidated_by": ["receipt:current-observation"],
            },
            {
                "capability": "app@1",
                "provider": "app-current",
                "state": "admitted",
                "evidence": ["receipt:current-observation"],
            },
        ],
    )

    result = solve(document)

    assert result["selected_providers"] == ["app-current"]
    assert result["reusable_receipts"] == ["receipt:current-observation"]
    assert result["invalidated_receipts"] == ["receipt:old-observation"]


def test_invalidation_edge_requires_bound_replacement_evidence() -> None:
    document = graph(
        capabilities=["app@1"],
        required=["app@1"],
        providers=[{"id": "app", "provides": ["app@1"]}],
        observations=[{
            "capability": "app@1",
            "provider": "app",
            "state": "superseded",
            "evidence": ["receipt:old"],
            "invalidated_by": ["receipt:missing"],
        }],
    )

    with pytest.raises(PlanResolutionError, match="unknown replacement evidence"):
        solve(document)


def test_invalidation_edge_cannot_replace_evidence_with_itself() -> None:
    document = graph(
        capabilities=["app@1"],
        required=["app@1"],
        providers=[{"id": "app", "provides": ["app@1"]}],
        observations=[{
            "capability": "app@1",
            "provider": "app",
            "state": "superseded",
            "evidence": ["receipt:same"],
            "invalidated_by": ["receipt:same"],
        }],
    )

    with pytest.raises(PlanResolutionError, match="own evidence"):
        solve(document)


def test_solution_hash_is_deterministic_across_input_order_and_detects_mutation():
    original = graph(
        capabilities=["b@1", "a@1"],
        required=["a@1", "b@1"],
        providers=[{"id": "b", "provides": ["b@1"]}, {"id": "a", "provides": ["a@1"]}],
    )
    reordered = deepcopy(original)
    reordered["capabilities"].reverse()
    reordered["providers"].reverse()
    reordered["target"]["required_capabilities"].reverse()

    first = solve(original)
    second = solve(reordered)

    assert first == second
    assert first["complete"] is True
    assert first["conformance_verified"] is False
    assert first["dispatchable"] is False
    with pytest.raises(PlanResolutionError, match="cannot dispatch"):
        validate_for_dispatch(first, current_plan_commit=COMMIT)

    agreed = solve(original, conformance_result={"available": True, "closure_hash": first["closure_hash"]})
    assert agreed["conformance_verified"] is True
    assert agreed["dispatchable"] is True
    validate_for_dispatch(agreed, current_plan_commit=COMMIT)
    first = agreed
    first["selected_providers"].append("fabricated")
    with pytest.raises(PlanResolutionError, match="hash mismatch"):
        validate_for_dispatch(first, current_plan_commit=COMMIT)


def test_stale_plan_commit_is_rejected_at_ingest_and_dispatch():
    document = graph(capabilities=["a@1"], providers=[{"id": "a", "provides": ["a@1"]}], required=["a@1"])

    with pytest.raises(StalePlanCommit):
        solve(document, expected_plan_commit="new-commit")

    solution = solve(document)
    with pytest.raises(StalePlanCommit):
        validate_for_dispatch(solution, current_plan_commit="new-commit")


def test_conformance_disagreement_holds_dispatch_as_contradictory():
    document = graph(capabilities=["a@1"], providers=[{"id": "a", "provides": ["a@1"]}], required=["a@1"])

    result = solve(document, conformance_result={"provider_id": "luet-pinned@1", "available": True, "closure_hash": "sha256:different"})

    assert result["complete"] is True
    assert result["conformance_verified"] is False
    assert result["dispatchable"] is False
    contradiction = next(item for item in result["unresolved"] if item["code"] == "CONTRADICTORY_RESOLUTION")
    assert contradiction["native_closure_hash"] == result["closure_hash"]
    assert contradiction["provider"] == "luet-pinned@1"
    with pytest.raises(PlanResolutionError, match="cannot dispatch"):
        validate_for_dispatch(result, current_plan_commit=COMMIT)


def test_execution_graph_adapter_exposes_bounded_catalog_gaps_without_inventing_providers():
    execution = {
        "schema": "tgw-plan-execution/v2",
        "plan_id": "PLAN-GOVERNED-EXECUTION-PLATFORM",
        "version": 1,
        "target": {"profile": "production", "required_capabilities": ["plan.capability-resolution@2", "queue.durable-claims@1"]},
        "work_units": [
            {"id": "W02", "requires": ["W01"], "establishes": ["queue.durable-claims@1"]},
            {"id": "W06", "requires": ["W02"], "establishes": ["plan.capability-resolution@2"]},
        ],
    }

    catalog = ExecutionGraphAdapter().adapt(execution, plan_commit=COMMIT)
    result = solve(catalog, expected_plan_commit=COMMIT)

    assert catalog["providers"] == []
    assert catalog["observations"] == []
    assert catalog["catalog_gaps"] == [
        {"code": "MISSING_PROVIDER_DECLARATION", "capability": "plan.capability-resolution@2", "required_by": "PLAN-GOVERNED-EXECUTION-PLATFORM"},
        {"code": "MISSING_PROVIDER_DECLARATION", "capability": "queue.durable-claims@1", "required_by": "PLAN-GOVERNED-EXECUTION-PLATFORM"},
    ]
    assert result["complete"] is False
    assert result["dispatchable"] is False
    assert {(item["code"], item["capability"]) for item in result["unresolved"]} == {
        ("UNSATISFIED", "plan.capability-resolution@2"),
        ("UNSATISFIED", "queue.durable-claims@1"),
    }


def test_execution_graph_work_unit_ids_are_not_converted_to_capabilities_or_providers():
    execution = {
        "schema": "tgw-plan-execution/v2",
        "plan_id": "P",
        "target": {"profile": "implementation", "required_capabilities": ["app@1"]},
        "work_units": [{"id": "W1", "requires": ["W0"], "establishes": ["app@1"]}],
    }

    catalog = ExecutionGraphAdapter().adapt(execution, plan_commit=COMMIT)

    assert catalog["capabilities"] == [{"id": "app@1"}]
    assert catalog["providers"] == []
    assert "W0" not in str(catalog)


@pytest.mark.parametrize("bad_commit", ["", None])
def test_execution_graph_adapter_requires_exact_commit_binding(bad_commit):
    execution = {"schema": "tgw-plan-execution/v2", "target": {"required_capabilities": ["app@1"]}}

    with pytest.raises(PlanResolutionError, match="exact Plan commit"):
        ExecutionGraphAdapter().adapt(execution, plan_commit=bad_commit)
