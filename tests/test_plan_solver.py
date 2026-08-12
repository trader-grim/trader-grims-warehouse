from copy import deepcopy

import pytest

from tgw.plan_solver import PlanResolutionError, StalePlanCommit, solve, validate_for_dispatch

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
            "establishes": ["app@1@admitted"],
            "selected_provider": "app",
            "requires_capabilities": ["queue@1"],
            "resume_from": ["receipt:todo"],
        }
    ]


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
    validate_for_dispatch(first, current_plan_commit=COMMIT)
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
