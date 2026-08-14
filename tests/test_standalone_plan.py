from __future__ import annotations

from copy import deepcopy

import pytest

from tgw.workflow.standalone_plan import (
    PlanValidationError,
    canonical_hash,
    compile_plan,
    completion_candidate,
    parse_plan,
    persist_artifact,
    status,
    validate_evidence,
    validate_plan,
)


def _registry():
    value = {
        "schema": "tgw-plan-registry/v1",
        "treatments": {"inventory-read": {"version": "1"}},
        "verifiers": {"inventory-valid/v1": {"version": "1"}},
        "enums": {
            "kind": ["discovery"],
            "effect_class": ["read-only"],
            "authority": ["plan-approved"],
        },
    }
    return value


def _contract():
    return {
        "exclusions": ["shell execution", "automatic closure"],
        "work_units": [{
            "id": "S0-inventory", "title": "Inventory registered facts",
            "kind": "discovery", "requires": [], "owns": ["registry:test"],
            "effect_class": "read-only", "authority": "plan-approved",
            "operator_surface": None,
            "treatment_id": "inventory-read", "treatment_version": "1",
            "inputs": {}, "outputs": [{"id": "manifest", "schema": "manifest/v1"}],
            "acceptance": [{
                "id": "inventory-valid", "verifier": "inventory-valid/v1",
                "assertion": "schema_and_hash_valid",
                "evidence_schema": "tgw-plan-evidence/v1", "freshness": "same-plan-version",
            }],
            "on_conflict": "reconciliation_required", "rollback": "none-read-only",
        }],
        "operator_surfaces": [],
        "plan_acceptance": ["S0-inventory:inventory-valid"],
        "rollback": "retain immutable evidence",
    }


def _text(*, version=1, status="approved", contract=None, scope_hash=None, narrative="Never run `touch /tmp/pwned`.", registry=None):
    registry = registry or _registry()
    contract = contract or _contract()
    metadata = {
        "tracks": ["server"], "dependencies": [],
    }
    scope = canonical_hash({
        **metadata,
        "exclusions": contract["exclusions"],
        "work_units": contract["work_units"],
        "operator_surfaces": contract["operator_surfaces"],
        "plan_acceptance": contract["plan_acceptance"],
        "rollback": contract["rollback"],
    })
    return f"""---
schema: tgw-plan/v1
plan_id: PLAN-TEST-001
version: {version}
status: {status}
owner: dave
authority_class: operator-approved
created_at: 2026-08-10T00:00:00-07:00
supersedes: null
registry_revision: {canonical_hash(registry)}
scope_hash: {scope_hash or scope}
tracks: [server]
dependencies: []
---
# Test plan

{narrative}

## Workflow contract
```yaml
{__import__('yaml').safe_dump(contract, sort_keys=False).rstrip()}
```
"""


def _graph():
    plan = parse_plan(_text())
    return compile_plan(plan, _registry(), {
        "repository_id": "trader-grims-warehouse",
        "canonical_root": "/opt/TGW/src/trader-grims-warehouse",
        "source_commit": "82149b906f000000000000000000000000000000",
    })


def _receipt(graph, result="true"):
    value = {
        "schema": "tgw-plan-evidence/v1", "receipt_id": "receipt-1",
        "plan_id": graph["plan_id"], "plan_version": graph["plan_version"],
        "scope_hash": graph["scope_hash"], "registry_revision": graph["registry_revision"],
        "graph_id": graph["graph_id"], "condition_hash": graph["condition_hash"],
        "work_unit_id": "S0-inventory", "acceptance_id": "inventory-valid",
        "entity": "registry:test", "verifier": "inventory-valid/v1",
        "verifier_version": "1", "result": result,
        "observed_at": "2026-08-11T00:00:00Z",
    }
    value["evidence_hash"] = canonical_hash(value)
    return value


def test_fixture_validates_compiles_stably_and_keeps_prose_inert():
    first = parse_plan(_text(narrative="Run `touch /tmp/one`"))
    second = parse_plan(_text(narrative="Run `touch /tmp/two`"))
    validate_plan(first, _registry())
    validate_plan(second, _registry())
    assert first.scope_hash == second.scope_hash
    assert compile_plan(first, _registry(), _graph()["repository"])["required_conditions"] == ["S0-inventory:inventory-valid"]


@pytest.mark.parametrize("mutation", ["unknown-key", "cycle", "bad-treatment", "bad-scope"])
def test_schema_and_graph_rejections(mutation):
    contract = _contract()
    scope = None
    if mutation == "unknown-key":
        contract["execute"] = "echo unsafe"
    elif mutation == "cycle":
        contract["work_units"][0]["requires"] = ["S0-inventory"]
    elif mutation == "bad-treatment":
        contract["work_units"][0]["treatment_id"] = "shell"
    else:
        scope = "sha256:" + "0" * 64
    with pytest.raises(PlanValidationError):
        validate_plan(parse_plan(_text(contract=contract, scope_hash=scope)), _registry())


def test_registry_and_collection_types_fail_closed():
    plan = parse_plan(_text())
    registry = deepcopy(_registry())
    registry["schema"] = "historical-memory/v1"
    with pytest.raises(PlanValidationError, match="registry"):
        validate_plan(plan, registry)

    contract = _contract()
    contract["work_units"][0]["requires"] = "S0-other"
    with pytest.raises(PlanValidationError, match="requires"):
        validate_plan(parse_plan(_text(contract=contract)), _registry())

    bool_version = _text().replace("version: 1\nstatus:", "version: true\nstatus:")
    with pytest.raises(PlanValidationError, match="positive integer"):
        validate_plan(parse_plan(bool_version), _registry())


def test_compile_rejects_proposed_plan_and_noncanonical_json():
    with pytest.raises(PlanValidationError, match="approved or active"):
        compile_plan(parse_plan(_text(status="proposed")), _registry(), _graph()["repository"])
    with pytest.raises(PlanValidationError, match="canonical JSON"):
        canonical_hash({"not_finite": float("nan")})


def test_operator_gated_work_requires_deployed_discoverable_current_surface():
    contract = _contract()
    unit = contract["work_units"][0]
    unit["authority"] = "operator-explicit"
    registry = deepcopy(_registry())
    registry["enums"]["authority"].append("operator-explicit")
    with pytest.raises(PlanValidationError, match="verified operator surface"):
        validate_plan(parse_plan(_text(contract=contract, registry=registry)), registry)

    unit["operator_surface"] = "approvals"
    condition = "S0-inventory:inventory-valid"
    contract["operator_surfaces"] = [{
        "id": "approvals", "route": "/form/approvals", "audience": "operator",
        "actions": ["approve", "hold"], "status_source": "canonical-receipts",
        "required_for": ["S0-inventory"], "deployment_condition": condition,
        "discoverability_condition": condition, "freshness_condition": condition,
    }]
    validate_plan(parse_plan(_text(contract=contract, registry=registry)), registry)

    contract["operator_surfaces"][0]["freshness_condition"] = "missing:condition"
    with pytest.raises(PlanValidationError, match="must reference plan acceptance"):
        validate_plan(parse_plan(_text(contract=contract, registry=registry)), registry)


def test_evidence_exact_bindings_staleness_and_completion_candidate():
    graph = _graph()
    receipt = _receipt(graph)
    assert validate_evidence(receipt, graph)["result"] == "true"
    assert status(graph, [receipt])["ready_for_candidate"] is True
    candidate = completion_candidate(
        graph,
        [receipt],
        created_at="2026-08-11T00:00:00Z",
        expires_at="2026-08-12T00:00:00Z",
    )
    assert candidate["schema"] == "plan-completion-candidate/v1"
    assert candidate["acceptance_receipts"] == {"S0-inventory:inventory-valid": "receipt-1"}

    stale = deepcopy(receipt)
    stale["plan_version"] = 2
    stale["evidence_hash"] = canonical_hash({key: value for key, value in stale.items() if key != "evidence_hash"})
    with pytest.raises(PlanValidationError, match="stale evidence"):
        validate_evidence(stale, graph)

    wrong_entity = deepcopy(receipt)
    wrong_entity["entity"] = "registry:other"
    wrong_entity["evidence_hash"] = canonical_hash({
        key: value for key, value in wrong_entity.items() if key != "evidence_hash"
    })
    with pytest.raises(PlanValidationError, match="ownership"):
        validate_evidence(wrong_entity, graph)


def test_contradiction_holds_candidate_and_artifacts_are_immutable(tmp_path):
    graph = _graph()
    yes, no = _receipt(graph), _receipt(graph, "false")
    no["receipt_id"] = "receipt-2"
    no["evidence_hash"] = canonical_hash({key: value for key, value in no.items() if key != "evidence_hash"})
    projection = status(graph, [yes, no])
    assert projection["conditions"][0]["result"] == "contradictory"
    with pytest.raises(PlanValidationError):
        completion_candidate(
            graph,
            [yes, no],
            created_at="2026-08-11T00:00:00Z",
            expires_at="2026-08-12T00:00:00Z",
        )
    path = persist_artifact(tmp_path, graph)
    assert path == persist_artifact(tmp_path, graph)
    assert path.read_text().endswith("\n")


def test_artifact_store_rejects_symlink_target(tmp_path):
    graph = _graph()
    digest = canonical_hash(graph).split(":", 1)[1]
    outside = tmp_path / "outside"
    outside.write_text("do not read", encoding="utf-8")
    (tmp_path / f"{digest}.json").symlink_to(outside)
    with pytest.raises(PlanValidationError, match="symlink"):
        persist_artifact(tmp_path, graph)


def test_candidate_rejects_bad_time_gates_and_duplicate_receipts():
    graph = _graph()
    receipt = _receipt(graph)
    with pytest.raises(PlanValidationError, match="timezone-aware"):
        completion_candidate(graph, [receipt], created_at="now", expires_at="later")
    with pytest.raises(PlanValidationError, match="unresolved"):
        completion_candidate(
            graph,
            [receipt],
            created_at="2026-08-11T00:00:00Z",
            expires_at="2026-08-12T00:00:00Z",
            reconciliation_gates=["repair:one"],
        )
    with pytest.raises(PlanValidationError, match="duplicate evidence"):
        status(graph, [receipt, receipt])


def test_parent_program_is_not_misrepresented_as_bootstrap_completion():
    root = __import__("pathlib").Path(__file__).parents[1]
    parent = (root / "docs/TGW-Plan-Vault/plan/PLAN-environment-cleanup-program.md").read_text()
    assert not parent.startswith("---\nschema: tgw-plan/v1")
    assert "Servers:" in parent and "Satellite laptops:" in parent


def test_environment_cleanup_bootstrap_fixture_is_workflow_completable():
    from pathlib import Path

    root = Path(__file__).parents[1]
    registry = __import__("json").loads(
        (root / "tests/fixtures/workflow_completable_plan/registry.json").read_text()
    )
    plan = parse_plan(
        (root / "docs/TGW-Plan-Vault/plan/PLAN-environment-cleanup-workflow-bootstrap.md").read_text()
    )
    validate_plan(plan, registry)
    repository = __import__("json").loads(
        (root / "tests/fixtures/workflow_completable_plan/repository-binding.json").read_text()
    )
    graph = compile_plan(plan, registry, repository)
    assert len(graph["work_units"]) == 3
    assert status(graph, [])["ready_for_candidate"] is False
