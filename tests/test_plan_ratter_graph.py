"""Todo 2008 (item R): compose + solve the unified ``ratter`` capability graph.

``ratter_execution_graph()`` below reproduces -- verbatim ids/edges, no
internals invented -- the ``tgw-plan-execution/v2`` document already committed
to the Plan vault at ``plan/execution/RATTER-CAPABILITY-GRAPH-v1.yaml``
(commit ``c9d01b47212befee587bc421a46f47dcd9bbbc8c``). It unions the real
``work_units`` from the five LEAF-11 leaf execution YAMLs (11.1, 11.4, 11.5,
11.6, 11.8) plus PP-ROLES-001's ``workflow.role-identity-model@1`` capability.
This test module keeps the fixture in Python (as the sibling
``test_plan_catalog.py``/``test_plan_solver.py`` modules do for their own
fixtures) rather than depending on the Plan-vault checkout at test time.
"""

from pathlib import Path

from tgw.plan_catalog import CATALOG_SCHEMA, compose_catalog, load_provider_catalog
from tgw.plan_solver import solve, validate_solution_integrity

COMMIT = "c9d01b47212befee587bc421a46f47dcd9bbbc8c"
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "agent-services/catalogs/ratter-v1.json"

TARGET_CAPABILITIES = [
    "continual-harness.core@1",
    "plan.luet-canonical-resolution@1",
    "generation.atomic-identity@1",
    "workflow.per-job-change-handling@1",
    "workflow.model-selection-and-research@1",
    "workflow.role-identity-model@1",
]

# Capabilities the ratter-v1.json catalog actually declares a provider for
# (LEAF-11-1 core + its two direct dependencies, and LEAF-11-8's near-term
# slice). The remaining four target capabilities correspond to leaves that
# are ``defined-not-dispatched`` or (for PP-ROLES-001) still ``PROPOSAL`` --
# no provider exists for them and none is fabricated here.
CAPABILITIES_WITH_REAL_PROVIDERS = {
    "continual-harness.core@1",
    "queue.durable-claims@1",
    "evidence.immutable-receipts@1",
    "workflow.model-selection-and-research@1",
}
CAPABILITIES_WITHOUT_PROVIDERS = {
    "plan.luet-canonical-resolution@1",
    "generation.atomic-identity@1",
    "workflow.per-job-change-handling@1",
    "workflow.role-identity-model@1",
}


def ratter_execution_graph():
    return {
        "schema": "tgw-plan-execution/v2",
        "plan_id": "PLAN-RATTER-CAPABILITY-GRAPH",
        "version": 1,
        "target": {"profile": "implementation", "required_capabilities": TARGET_CAPABILITIES},
        "work_units": [
            {
                "id": "L11.1",
                "requires": [],
                "requires_capabilities": ["queue.durable-claims@1", "evidence.immutable-receipts@1"],
                "establishes": ["continual-harness.core@1"],
            },
            {
                "id": "L11.1.LEDGER-AND-GIT-DISCIPLINE",
                "requires": ["L11.1"],
                "requires_capabilities": ["queue.durable-claims@1", "evidence.immutable-receipts@1"],
                "establishes": ["continual-harness.core@1"],
            },
            {
                "id": "L11.1.DELETE-APPARATUS",
                "requires": ["L11.1", "L11.1.LEDGER-AND-GIT-DISCIPLINE"],
                "establishes": ["continual-harness.core@1"],
            },
            {
                "id": "L11.4",
                "requires": [],
                "requires_capabilities": ["queue.durable-claims@1", "evidence.immutable-receipts@1"],
                "establishes": ["plan.luet-canonical-resolution@1"],
            },
            {
                "id": "L11.5",
                "requires": [],
                "requires_capabilities": [
                    "queue.durable-claims@1",
                    "evidence.immutable-receipts@1",
                    "plan.luet-canonical-resolution@1",
                    "continual-harness.core@1",
                ],
                "establishes": ["generation.atomic-identity@1"],
            },
            {
                "id": "L11.6",
                "requires": [],
                "requires_capabilities": [
                    "queue.durable-claims@1",
                    "evidence.immutable-receipts@1",
                    "continual-harness.core@1",
                ],
                "establishes": ["workflow.per-job-change-handling@1"],
            },
            {
                "id": "L11.8",
                "requires": [],
                "requires_capabilities": [
                    "queue.durable-claims@1",
                    "evidence.immutable-receipts@1",
                    "continual-harness.core@1",
                ],
                "establishes": ["workflow.model-selection-and-research@1"],
            },
            {
                "id": "PP-ROLES-001.ROLE-IDENTITY-MODEL",
                "requires": [],
                "requires_capabilities": [
                    "queue.durable-claims@1",
                    "evidence.immutable-receipts@1",
                    "continual-harness.core@1",
                ],
                "establishes": ["workflow.role-identity-model@1"],
            },
        ],
    }


def ratter_catalog():
    return load_provider_catalog(CATALOG)


def composed_ratter_graph():
    return compose_catalog(ratter_execution_graph(), ratter_catalog(), plan_commit=COMMIT)


def test_ratter_catalog_is_bound_to_the_composed_graph_commit():
    catalog = ratter_catalog()
    assert catalog["schema"] == CATALOG_SCHEMA
    assert catalog["plan_commit"] == COMMIT
    assert catalog["plan_id"] == "PLAN-RATTER-CAPABILITY-GRAPH"


def test_capabilities_with_real_providers_produce_zero_catalog_gaps():
    composed = composed_ratter_graph()
    gapped_capabilities = {gap["capability"] for gap in composed["catalog_gaps"]}
    assert gapped_capabilities.isdisjoint(CAPABILITIES_WITH_REAL_PROVIDERS)
    provider_ids = {provider["id"] for provider in composed["providers"]}
    provided_capabilities = {
        capability for provider in composed["providers"] for capability in provider["provides"]
    }
    assert CAPABILITIES_WITH_REAL_PROVIDERS <= provided_capabilities
    assert provider_ids  # sanity: the catalog is not empty


def test_undeclared_leaves_surface_as_honest_missing_provider_gaps():
    """Leaves that are ``defined-not-dispatched`` or still ``PROPOSAL`` (11.4's
    Luet-as-producer, 11.5's atomic generation identity, 11.6's per-job change
    handling, and PP-ROLES-001's role-identity-model) have no provider
    declared in ``ratter-v1.json``. Each must show up as a correctly-labeled,
    non-fabricated ``catalog_gaps`` entry rather than being papered over --
    the same honesty rule that keeps the fences/``action.*`` registry gap
    (identified in the 2026-09-12 vocabulary reconciliation, and not
    referenced anywhere in this graph) from ever being silently marked solved.
    """
    composed = composed_ratter_graph()
    gaps_by_capability = {gap["capability"]: gap for gap in composed["catalog_gaps"]}
    assert CAPABILITIES_WITHOUT_PROVIDERS <= set(gaps_by_capability)
    for capability in CAPABILITIES_WITHOUT_PROVIDERS:
        gap = gaps_by_capability[capability]
        assert gap["code"] == "MISSING_PROVIDER_DECLARATION"
        assert gap["required_by"] == "PLAN-RATTER-CAPABILITY-GRAPH"
    provided_capabilities = {
        capability for provider in composed["providers"] for capability in provider["provides"]
    }
    assert provided_capabilities.isdisjoint(CAPABILITIES_WITHOUT_PROVIDERS)


def test_solve_is_an_honest_partial_result_not_a_fabricated_success():
    composed = composed_ratter_graph()
    solution = solve(composed, expected_plan_commit=COMMIT)
    validate_solution_integrity(solution, current_plan_commit=COMMIT)

    assert solution["schema"] == "tgw-plan-solution/v1"
    assert solution["plan_commit"] == COMMIT
    # Real, non-fabricated result: 4 of 6 target capabilities have no
    # provider yet, so the closure is genuinely incomplete.
    assert solution["complete"] is False
    assert solution["dispatchable"] is False
    unresolved_capabilities = {item["capability"] for item in solution["unresolved"]}
    assert unresolved_capabilities == CAPABILITIES_WITHOUT_PROVIDERS
    for item in solution["unresolved"]:
        assert item["code"] == "UNSATISFIED"
        assert item["reason"] == "MISSING_PROVIDER_DECLARATION"


def test_solution_hash_round_trips_through_json():
    import json

    composed = composed_ratter_graph()
    solution = solve(composed, expected_plan_commit=COMMIT)
    reloaded = json.loads(json.dumps(solution))
    validate_solution_integrity(reloaded, current_plan_commit=COMMIT)
