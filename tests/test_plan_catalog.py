import json
from copy import deepcopy
from pathlib import Path

import pytest

from tgw.plan_catalog import CATALOG_SCHEMA, compose_catalog, load_provider_catalog
from tgw.plan_solver import PlanResolutionError, solve

COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "agent-services/catalogs/governed-execution-platform-v1.json"
TARGET_CAPABILITIES = [
    "plan.capability-resolution@2",
    "workflow.condition-derived-convergence@1",
    "coding.governed-execution@1",
    "authority.operator-decisions@1",
    "operator.surface-projection@1",
    "coding.harness-agnostic-role-lanes@1",
    "promptcraft.receiver-profiles@1",
]


def execution_graph():
    return {
        "schema": "tgw-plan-execution/v2",
        "plan_id": "PLAN-GOVERNED-EXECUTION-PLATFORM",
        "version": 1,
        "target": {"profile": "production", "required_capabilities": TARGET_CAPABILITIES},
        "work_units": [
            {"id": "W07", "establishes": ["coding.harness-agnostic-role-lanes@1"]},
        ],
    }


def test_canonical_catalog_composes_complete_closure_as_work_not_installed_state():
    explicit = load_provider_catalog(CATALOG)
    composed = compose_catalog(execution_graph(), explicit, plan_commit=COMMIT)
    solution = solve(composed, expected_plan_commit=COMMIT)

    assert explicit["schema"] == CATALOG_SCHEMA
    assert composed["catalog_gaps"] == []
    assert solution["complete"] is True
    assert solution["satisfied_installed"] == []
    assert {unit["selected_provider"] for unit in solution["work_units"]} == {
        "native-exact-resolver",
        "canonical-plan-graph",
        "shared-tgw-plan-skill",
        "recovered-promptcraft",
        "build-workflow-convergence",
        "build-operator-authority",
        "build-operator-surface",
        "build-harness-role-lanes",
        "build-governed-execution",
    }
    promptcraft = next(
        unit for unit in solution["work_units"] if unit["selected_provider"] == "recovered-promptcraft"
    )
    assert promptcraft["resume_from"] == [
        "lineage:agent-services/providers/promptcraft/LINEAGE.sha256",
        "source-commit:3260710",
        "test:tests/test_promptcraft_agent_service.py:13-passed",
    ]
    assert all(unit["establishes"][0].endswith("@operationally_verified") for unit in solution["work_units"])


def test_missing_explicit_provider_stays_unsatisfied_despite_work_unit_establishes():
    explicit = load_provider_catalog(CATALOG)
    explicit["providers"] = [
        provider
        for provider in explicit["providers"]
        if "coding.harness-agnostic-role-lanes@1" not in provider["provides"]
    ]
    composed = compose_catalog(execution_graph(), explicit, plan_commit=COMMIT)
    solution = solve(composed)

    assert solution["complete"] is False
    assert solution["dispatchable"] is False
    assert solution["unresolved"] == [
        {
            "code": "UNSATISFIED",
            "capability": "coding.harness-agnostic-role-lanes@1",
            "reason": "MISSING_PROVIDER_DECLARATION",
            "required_by": "PLAN-GOVERNED-EXECUTION-PLATFORM",
        }
    ]


def test_catalog_rejects_observation_not_bound_to_provider_object():
    explicit = load_provider_catalog(CATALOG)
    explicit["observations"].append(
        {
            "capability": "promptcraft.receiver-profiles@1",
            "provider": "work-unit-W07",
            "state": "operationally_verified",
            "evidence": ["work-unit:W07"],
        }
    )

    with pytest.raises(PlanResolutionError, match="unknown provider"):
        compose_catalog(execution_graph(), explicit, plan_commit=COMMIT)


def test_catalog_plan_binding_is_strict():
    explicit = json.loads(CATALOG.read_text())
    stale = deepcopy(explicit)
    stale["plan_commit"] = "stale"

    with pytest.raises(PlanResolutionError, match="exact Plan commit"):
        compose_catalog(execution_graph(), stale, plan_commit=COMMIT)
