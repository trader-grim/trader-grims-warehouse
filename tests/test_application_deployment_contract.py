import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tgw.application_deployment_contract import (
    CLOSURE_HASH,
    MIGRATION_PATHS,
    OPERATIONAL_CONFIG_SCHEMA,
    PLAN_COMMIT,
    PROJECTION_PATH,
    SCHEMA,
    SUCCESSOR_SCHEMA,
    SOLUTION_HASH,
    STAGES,
    ApplicationDeploymentContractError,
    VerifiedApplicationDeploymentContract,
    _validate_projection,
    _validate_runtime_config,
    validate_application_deployment_contract,
)
from tgw.effect_handlers import (
    AuthorityEffectController,
    EffectOutcome,
    RetryableEffect,
    TypedEffectHandlerRegistry,
)
from tgw.plan_authority import TypedEffect
from tgw.release_installer import runtime_manifest_identity


def _hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _contract():
    def h(digit):
        return "sha256:" + digit * 64

    value = {
        "schema": SCHEMA,
        "authorization": {
            "operator_instruction": {"ref": "instruction:w09", "content_sha256": h("1")},
            "observed_at": "2026-08-16T10:00:00Z",
            "expires_at": "2026-08-16T12:00:00Z",
            "capabilities": ["coding.governed-execution@1"],
            "phases": ["W09"],
            "repositories": {"source": "repo:source", "plan": "repo:plan", "flake": "repo:flake"},
            "effect_set": ["approval-platform-bootstrap-deployment"],
            "exclusions": ["generic-shell", "nix-system-switch", "unrelated-host-effects"],
            "retirement_condition": "W10:canonical-gate-operational",
            "deployment_uses": 1,
        },
        "candidate": {
            "commit": "a" * 40,
            "tree": "b" * 40,
            "archive_sha256": h("2"),
            "candidate_evidence_bundle_hash": h("3"),
            "manifest_hash": h("4"),
            "release_manifest_hash": h("5"),
            "rollback_manifest_hash": h("6"),
            "admission_gate_hash": h("7"),
            "predecessor_commit": "c" * 40,
            "predecessor_tree": "d" * 40,
            "predecessor_archive_sha256": h("8"),
            "predecessor_release_manifest_hash": h("9"),
            "predecessor_content_manifest_sha256": h("a"),
        },
        "archive": {
            "artifact_ref": "artifact:candidate",
            "content_sha256": h("2"),
            "size_bytes": 123,
            "embedded_commit": "a" * 40,
            "release_manifest_hash": h("5"),
            "content_manifest_sha256": h("b"),
            "file_count": 10,
        },
        "plan": {
            "commit": PLAN_COMMIT,
            "tree": "e" * 40,
            "solution_hash": SOLUTION_HASH,
            "closure_hash": CLOSURE_HASH,
            "graph_hash": h("c"),
            "work_unit": "W09",
            "authorization_ref": "instruction:w09",
            "projection": {"release_path": PROJECTION_PATH, "content_sha256": h("d")},
        },
        "runtime_config": {
            "artifact_ref": "config:candidate",
            "generation_path": "config/tgw-api-config.json",
            "content_sha256": h("e"),
            "config_schema": OPERATIONAL_CONFIG_SCHEMA,
            "executor_principal": "executor:release",
            "operator_principals": ["operator:api", "operator:session"],
            "executor_credential_env": "TGW_AUTHORITY_TOKEN",
            "credential_reference": "credential:tgw-release",
            "trusted_root": "/opt/TGW/releases/release-b",
            "trusted_uid": 0,
            "forbidden_paths": ["/opt/TGW/src", "/run/tgw/no-local-plan"],
        },
        "migrations": [{"path": path, "source_sha256": h(str(index + 1)), "receipt_hash": h(str(index + 3))} for index, path in enumerate(MIGRATION_PATHS)],
        "deployment": {
            "target_host": "tgw-prod",
            "root_id": "production-releases",
            "release_root": "/opt/TGW",
            "artifact_ref": "artifact:candidate",
            "prior_generation": "release-a",
            "next_generation": "release-b",
            "immutable_generation_path": "/opt/TGW/releases/release-b",
            "current_selector": "/opt/TGW/current",
            "nix_system_path": "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-nixos-system-tgw-prod-26.05",
            "predecessor_observation_ref": "observation:release-a",
            "predecessor_observation_hash": h("f"),
            "provider_observation_ref": "observation:w09-provider",
            "provider_observation_hash": h("0"),
            "prior_projection_sha256": None,
            "prior_runtime_config_sha256": h("2"),
            "prior_database_identity_sha256": h("3"),
            "prior_runtime_config_uid": 0,
            "prior_runtime_config_gid": 995,
            "prior_runtime_config_mode": 0o640,
            "prior_runtime_config_size": 3336,
        },
        "services": ["tgw-api.service", "tgw-worker.target"],
        "health_probes": ["api", "authority", "queue"],
        "stage_order": list(STAGES),
        "rollback": {
            "generation": "release-a",
            "manifest_hash": h("6"),
            "database_backup_required": True,
            "selector_cas_required": True,
            "config_reconciliation_required": True,
            "service_reconciliation_required": True,
            "predecessor_health_required": True,
        },
        "operation_sink": {"sink_id": "w09-terminal", "descriptor_hash": h("0")},
    }
    overlay = runtime_manifest_identity(
        value["deployment"]["next_generation"],
        {value["runtime_config"]["generation_path"]: value["runtime_config"]["content_sha256"].removeprefix("sha256:")},
    )
    value["runtime_config"]["overlay_manifest_sha256"] = "sha256:" + overlay["manifest_sha256"]
    value["contract_hash"] = _hash(value)
    return value


def _verified():
    contract = _contract()
    receipts = tuple(
        {
            "migration_path": item["path"],
            "migration_sha256": item["source_sha256"],
            "receipt_hash": item["receipt_hash"],
        }
        for item in contract["migrations"]
    )
    return VerifiedApplicationDeploymentContract(
        "candidate:" + "a" * 40 + ":application-bootstrap:v1",
        contract["contract_hash"],
        contract,
        receipts,
    )


def test_contract_binds_application_not_nix_masquerade_and_exact_stage_order():
    assert validate_application_deployment_contract(_contract())["stage_order"] == list(STAGES)
    for mutate in (
        lambda value: value.update(schema="tgw-bootstrap-deployment-contract/v2"),
        lambda value: value["migrations"].reverse(),
        lambda value: value["plan"]["projection"].update(release_path="/opt/TGW/current/projection.json"),
        lambda value: value["runtime_config"].update(trusted_root="/opt/TGW/current"),
    ):
        bad = copy.deepcopy(_contract())
        mutate(bad)
        with pytest.raises(ApplicationDeploymentContractError):
            validate_application_deployment_contract(bad)


def test_successor_contract_uses_approved_w13_w18_plan_without_f0_constants():
    value = _contract()
    value["schema"] = SUCCESSOR_SCHEMA
    value["authorization"].update({
        "phases": ["W13", "W14", "W15", "W16", "W17", "W18"],
        "retirement_condition": "W18:verified-and-resumed",
    })
    value["authorization"]["operator_instruction"]["ref"] = "instruction:w13-w18"
    value["plan"].update({
        "commit": "1" * 40, "solution_hash": "sha256:" + "2" * 64,
        "closure_hash": "sha256:" + "3" * 64, "work_unit": "W13-W18",
        "authorization_ref": "instruction:w13-w18",
        "projection": {
            "release_path": "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-111111111111.json",
            "content_sha256": "sha256:" + "4" * 64,
        },
    })
    value["contract_hash"] = _hash({key: item for key, item in value.items() if key != "contract_hash"})
    validated = validate_application_deployment_contract(value)
    assert validated["plan"]["commit"] == "1" * 40
    config = _runtime_config(value)
    config.update({
        "plan_approved_commit": value["plan"]["commit"],
        "plan_approved_solution_hash": value["plan"]["solution_hash"],
        "plan_projection_path": value["deployment"]["immutable_generation_path"] + "/" + value["plan"]["projection"]["release_path"],
    })
    _validate_runtime_config(json.dumps(config).encode(), value)


def test_projection_binds_semantic_solution_separately_from_canonical_content_digest():
    projection = Path("agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-f0a8cf22.json").read_bytes()
    value = json.loads(projection)
    assert value["solution"]["solution_hash"] == SOLUTION_HASH
    assert value["solution_sha256"] != SOLUTION_HASH
    _validate_projection(projection)

    value["solution_sha256"] = SOLUTION_HASH
    with pytest.raises(ApplicationDeploymentContractError, match="canonical binding"):
        _validate_projection(json.dumps(value).encode())


def _runtime_config(contract):
    runtime = contract["runtime_config"]
    deployment = contract["deployment"]
    return {
        "schema": runtime["config_schema"],
        "plan_approved_commit": PLAN_COMMIT,
        "plan_approved_solution_hash": SOLUTION_HASH,
        "plan_projection_path": deployment["immutable_generation_path"] + "/" + PROJECTION_PATH,
        "plan_projection_root": deployment["immutable_generation_path"],
        "plan_authority_executor_principal": runtime["executor_principal"],
        "plan_authority_executor_credential_env": runtime["executor_credential_env"],
        "plan_authority_executor_credential_ref": runtime["credential_reference"],
        "plan_projection_trusted_uid": runtime["trusted_uid"],
        "plan_authority_operator_api_principal": runtime["operator_principals"][0],
        "plan_authority_operator_session_principal": runtime["operator_principals"][1],
    }


@pytest.mark.parametrize(
    "addition",
    [
        {"ebay_access_token": "plaintext"},
        {"nested": {"client_secret": "plaintext"}},
        {"nested": {"retired_source": "/opt/TGW/src/neighbor"}},
    ],
)
def test_runtime_config_rejects_recursive_secret_and_retired_path_substitution(addition):
    contract = _contract()
    value = _runtime_config(contract)
    value.update(addition)
    with pytest.raises(ApplicationDeploymentContractError, match="secret material"):
        _validate_runtime_config(json.dumps(value).encode(), contract)


def _application_controller(*, install, rollback, recorder):
    verified = _verified()
    resolver = Mock()
    resolver.resolve.return_value = verified
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        application_bootstrap_contract_resolver=resolver,
        application_bootstrap_install=install,
        application_bootstrap_rollback=rollback,
        application_bootstrap_validate=Mock(),
    )
    authority = Mock(return_value={"receipt_id": "bootstrap:consumed"})
    effect = TypedEffect.parse(
        {
            "kind": "approval-platform-bootstrap-deployment",
            "generation": "release-b",
            "parameters": {"schema": "tgw-approval-application-bootstrap/v1", "application_contract_ref": verified.reference, "application_contract_hash": verified.contract_hash},
        }
    )
    return AuthorityEffectController(registry, authority, terminal_recorder=recorder), authority, effect


def test_terminal_persistence_failure_after_success_forces_rollback_then_records_terminal():
    calls = []
    install = Mock(return_value={"evidence": ["installed:release-b"]})
    rollback = Mock(return_value={"receipt": "rollback:release-a"})

    def recorder(receipt):
        calls.append(receipt["outcome"])
        if len(calls) == 1:
            raise OSError("sink fsync failed")
        return {"receipt": "terminal:rollback", "receipt_hash": receipt["receipt_hash"]}

    controller, authority, effect = _application_controller(install=install, rollback=rollback, recorder=recorder)
    result = controller.execute(request_id="w09", effect=effect)
    assert result.outcome is EffectOutcome.ROLLED_BACK
    assert calls == ["succeeded", "rolled_back"]
    assert result.rollback_receipt == "rollback:release-a"
    authority.assert_called_once()


def test_terminal_persistence_failure_after_rollback_returns_typed_ambiguity():
    install = Mock(side_effect=RuntimeError("preselection failure"))
    rollback = Mock(return_value={"receipt": "rollback:release-a"})
    recorder = Mock(side_effect=OSError("sink unavailable"))
    controller, _, effect = _application_controller(install=install, rollback=rollback, recorder=recorder)
    result = controller.execute(request_id="w09", effect=effect)
    assert result.outcome is EffectOutcome.AMBIGUOUS
    assert result.evidence[0].startswith("effect-terminal-persistence:sha256:")
    rollback.assert_called_once()


def test_consumed_w09_grant_never_returns_retry():
    install = Mock(side_effect=RetryableEffect("temporary", evidence=("provider:temporary",)))
    rollback = Mock(return_value={"receipt": "rollback:release-a"})
    recorder = Mock(
        side_effect=lambda receipt: {
            "receipt": "terminal:rollback",
            "receipt_hash": receipt["receipt_hash"],
        }
    )
    controller, _, effect = _application_controller(install=install, rollback=rollback, recorder=recorder)
    result = controller.execute(request_id="w09", effect=effect)
    assert result.outcome is EffectOutcome.ROLLED_BACK
    assert result.outcome is not EffectOutcome.RETRY


def test_application_provider_readiness_fails_before_one_use_authority_consumption():
    verified = _verified()
    resolver = Mock()
    resolver.resolve.return_value = verified
    readiness = Mock(side_effect=ValueError("provider observation expired"))
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        application_bootstrap_contract_resolver=resolver,
        application_bootstrap_install=Mock(),
        application_bootstrap_rollback=Mock(),
        application_bootstrap_validate=readiness,
    )
    authority = Mock()
    effect = TypedEffect.parse(
        {
            "kind": "approval-platform-bootstrap-deployment",
            "generation": "release-b",
            "parameters": {
                "schema": "tgw-approval-application-bootstrap/v1",
                "application_contract_ref": verified.reference,
                "application_contract_hash": verified.contract_hash,
            },
        }
    )
    with pytest.raises(ValueError, match="expired"):
        AuthorityEffectController(registry, authority).execute(request_id="w09", effect=effect)
    authority.assert_not_called()


def test_authenticated_remote_restore_is_terminal_without_second_rollback_dispatch():
    install = Mock(
        return_value={
            "terminal_outcome": "rolled_back",
            "rollback_receipt": "remote-rollback:release-a",
            "evidence": ["database:restored", "health:predecessor"],
        }
    )
    rollback = Mock()
    recorder = Mock(
        side_effect=lambda receipt: {
            "receipt": "terminal:rollback",
            "receipt_hash": receipt["receipt_hash"],
        }
    )
    controller, _, effect = _application_controller(
        install=install,
        rollback=rollback,
        recorder=recorder,
    )
    result = controller.execute(request_id="remote-restored", effect=effect)
    assert result.outcome is EffectOutcome.ROLLED_BACK
    assert result.rollback_receipt == "remote-rollback:release-a"
    rollback.assert_not_called()
