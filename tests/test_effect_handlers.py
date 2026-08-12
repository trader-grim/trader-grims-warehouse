import hashlib
import json
from unittest.mock import Mock

import pytest

from tgw.effect_handlers import AmbiguousEffect, AuthorityEffectController, EffectOutcome, RetryableEffect, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect

SHA = "a" * 40
TREE = "b" * 40
DIGEST = "c" * 64


def _evaluation_parameters():
    input_closure = [
        {
            "lock_node": "nixpkgs",
            "lock_rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
            "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
            "path": "/nix/store/11111111111111111111111111111111-input",
            "nar_sha256": "sha256:" + DIGEST,
        }
    ]
    return {
        "target_host": "tgw-prod",
        "flake_repository_id": "tgw-flake",
        "artifact_ref": f"artifact:sha256:{DIGEST}",
        "source_commit": SHA,
        "source_tree": TREE,
        "source_archive_sha256": DIGEST,
        "flake_lock_sha256": DIGEST,
        "archive_root": "trader-grims-warehouse",
        "module_path": "nix/review-egress.nix",
        "module_sha256": DIGEST,
        "provider_sha256": DIGEST,
        "ssh_sha256": DIGEST,
        "known_hosts_sha256": DIGEST,
        "remote_python_sha256": DIGEST,
        "git_sha256": DIGEST,
        "nix_sha256": DIGEST,
        "nix_store_sha256": DIGEST,
        "systemd_analyze_sha256": DIGEST,
        "scratch_id": "nixos-review:operation-1",
        "system": "x86_64-linux",
        "evaluation_target": "review-egress-systemd-units",
        "unit_set": "tgw-review-egress@.service,tgw-review-egress-attest@.service,tgw-review-egress-namespace@.service",
        "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
        "nix_network_policy": "offline-no-substituters",
        "input_closure_manifest_json": json.dumps(input_closure, sort_keys=True, separators=(",", ":")),
        "input_closure_manifest_sha256": "sha256:" + hashlib.sha256(json.dumps(input_closure, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "input_closure_path_count": "1",
        "minimum_systemd_version": "257",
        "max_duration_seconds": "300",
        "max_output_bytes": "1048576",
        "max_archive_bytes": "1048576",
        "max_unpacked_bytes": "4194304",
        "max_files": "1000",
        "activate": "false",
        "profile_write": "false",
        "home_db_write": "false",
        "operation_id": "nixos-review:operation-1",
    }


def _evaluation_result(parameters):
    assert set(parameters) == {"kind", "generation", "parameters"}
    parameters = parameters["parameters"]
    result = {
        "schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
        "outcome": "verified",
        "source_commit": parameters["source_commit"],
        "source_tree": parameters["source_tree"],
        "source_archive_sha256": parameters["source_archive_sha256"],
        "flake_lock_sha256": parameters["flake_lock_sha256"],
        "module_sha256": parameters["module_sha256"],
        "provider_sha256": parameters["provider_sha256"],
        "ssh_sha256": parameters["ssh_sha256"],
        "known_hosts_sha256": parameters["known_hosts_sha256"],
        "executable_sha256": {"remote_python": DIGEST, "git": DIGEST, "nix": DIGEST, "nix_store": DIGEST, "systemd_analyze": DIGEST},
        "scratch_id": parameters["scratch_id"],
        "cleanup": "removed",
        "activate": False,
        "scratch_root": {"path": "/var/tmp/tgw-reviewed-evaluation", "created_by_attempt": True, "final_state": "removed"},
        "profile_write": False,
        "home_db_write": False,
        "system": "x86_64-linux",
        "evaluation_target": "review-egress-systemd-units",
        "input_closure_manifest": json.loads(parameters["input_closure_manifest_json"]),
        "input_closure_manifest_sha256": parameters["input_closure_manifest_sha256"],
        "input_closure_path_count": int(parameters["input_closure_path_count"]),
        "evaluated_config_drv": "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-review-units.drv",
        "closure_path_count": 1,
        "eval_log_sha256": DIGEST,
        "build_log_sha256": DIGEST,
        "systemd_verify_output_sha256": DIGEST,
        "verifier_metadata_sha256": DIGEST,
        "verifier_metadata": {
            "schema": "tgw-review-egress-systemd-units/v1",
            "system": "x86_64-linux",
            "units": [
                "tgw-review-egress@.service",
                "tgw-review-egress-attest@.service",
                "tgw-review-egress-namespace@.service",
            ],
            "activation": False,
        },
        "systemd_verify_exit": 0,
        "systemd_version": 257,
        "nix_version": "2.28.5",
        "executables": {
            "git": "/run/current-system/sw/bin/git",
            "nix": "/run/current-system/sw/bin/nix",
            "nix_store": "/run/current-system/sw/bin/nix-store",
            "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze",
        },
        "unit_sha256": {
            "tgw-review-egress@.service": DIGEST,
            "tgw-review-egress-attest@.service": DIGEST,
            "tgw-review-egress-namespace@.service": DIGEST,
        },
    }
    result["closure_manifest"] = [{"path": "/nix/store/11111111111111111111111111111111-dependency", "nar_sha256": DIGEST}]
    result["closure_manifest_sha256"] = "sha256:" + hashlib.sha256(json.dumps(result["closure_manifest"], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    result["closure_manifest_ref"] = "inline:" + result["closure_manifest_sha256"]
    result["receipt_sha256"] = "sha256:" + hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    result["evidence"] = ["nixos-evaluation:" + result["receipt_sha256"]]
    return result


def _registry(**changes):
    providers = {
        "release_install": Mock(return_value={"evidence": ["release:selected"]}),
        "release_rollback": Mock(return_value={"receipt": "rollback:1"}),
        "flake_push": Mock(return_value={"evidence": ["remote:readback"]}),
        "flake_switch_record": Mock(return_value={"evidence": ["switch:recorded"]}),
        "dependency_resubmit": Mock(return_value={"evidence": ["queue:accepted"]}),
        "bootstrap_install": Mock(return_value={"evidence": ["nixos:switched", "probes:passed"]}),
        "bootstrap_rollback": Mock(return_value={"receipt": "nixos:rollback"}),
        "nixos_reviewed_evaluation": Mock(side_effect=_evaluation_result),
    }
    providers.update(changes)
    return TypedEffectHandlerRegistry(**providers), providers


@pytest.mark.parametrize(
    ("kind", "parameters"),
    [
        (
            "coding-release",
            {
                "candidate_commit": SHA,
                "candidate_tree": TREE,
                "archive_sha256": DIGEST,
                "artifact_ref": "artifact:release-1",
                "root_id": "tgw-staging",
                "expected_current": "release-a",
                "operation_id": "install-b",
                "review_receipt": "review:1",
                "controller_receipt": "controller:1",
            },
        ),
        ("bounded-flake-push", {"repository_id": "tgw-flake", "host_role": "production", "commit": SHA, "remote_ref": "origin/master"}),
        ("flake-switch-record-only", {"host_role": "production", "commit": SHA, "execution_receipt": "manual:1"}),
        ("dependency-resubmit", {"dependency_id": "W03", "queue_id": "coding", "failed_generation": "generation-1"}),
        ("authority-canary", {"canary_id": "canary:w10-1", "purpose": "verify-plan-authority-roundtrip"}),
        (
            "approval-platform-bootstrap-deployment",
            {
                "target_host": "tgw-prod",
                "flake_repository_id": "tgw-flake",
                "flake_commit": SHA,
                "flake_tree": TREE,
                "expected_current_system": "/nix/store/aaaaaaaa-nixos-system-tgw-prod-old",
                "successor_system": "/nix/store/bbbbbbbb-nixos-system-tgw-prod-new",
                "credential_ref": "credential:tgw-review:codex",
                "credential_sha256": DIGEST,
                "broker_source_sha256": DIGEST,
                "namespace_source_sha256": DIGEST,
                "nix_module_sha256": DIGEST,
                "egress_contract_sha256": DIGEST,
                "install_contract_sha256": DIGEST,
                "review_receipt": "review:passed",
                "controller_receipt": "controller:passed",
                "network_attestation_receipt": "network:passed",
                "probe_receipt": "probes:passed",
                "operation_id": "bootstrap:review-transport-1",
            },
        ),
        ("nixos-reviewed-evaluation", _evaluation_parameters()),
    ],
)
def test_registered_effects_consume_exact_authority_then_invoke_only_their_handler(kind, parameters):
    registry, providers = _registry()
    consumed = Mock(return_value={"receipt_id": "authority:1"})
    effect = TypedEffect.parse({"kind": kind, "generation": "generation-2", "parameters": parameters})

    receipt = AuthorityEffectController(registry, consumed).execute(request_id="request:1", effect=effect)

    consumed.assert_called_once_with("request:1", effect_hash=effect.effect_hash, generation="generation-2")
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.effect_hash == effect.effect_hash
    assert receipt.receipt_hash.startswith("sha256:")
    handler_id, _, _, _ = registry.prepare(effect)
    assert receipt.handler_id == handler_id
    assert sum(provider.call_count for provider in providers.values()) == (0 if kind == "authority-canary" else 1)


@pytest.mark.parametrize(
    "effect",
    [
        {
            "kind": "coding-release",
            "generation": "g",
            "parameters": {
                "candidate_commit": SHA,
                "candidate_tree": TREE,
                "archive_sha256": DIGEST,
                "artifact_ref": "/tmp/arbitrary",
                "root_id": "x",
                "expected_current": "a",
                "operation_id": "x",
                "review_receipt": "r",
                "controller_receipt": "c",
                "command": "sh",
            },
        },
        {"kind": "bounded-flake-push", "generation": "g", "parameters": {"repository_id": "other", "host_role": "random-host", "commit": SHA, "remote_ref": "elsewhere"}},
        {"kind": "dependency-resubmit", "generation": "g", "parameters": {"dependency_id": "W", "queue_id": "arbitrary", "failed_generation": "g0"}},
    ],
)
def test_arbitrary_commands_hosts_paths_and_queues_fail_before_authority_consume(effect):
    registry, _ = _registry()
    consumed = Mock()
    with pytest.raises(ValueError):
        parsed = TypedEffect.parse(effect)
        AuthorityEffectController(registry, consumed).execute(request_id="request:1", effect=parsed)
    consumed.assert_not_called()


@pytest.mark.parametrize(
    ("exception", "outcome"),
    [(RetryableEffect("provider unavailable"), EffectOutcome.RETRY), (AmbiguousEffect("outcome unknown"), EffectOutcome.AMBIGUOUS)],
)
def test_retry_and_ambiguity_emit_distinct_receipts(exception, outcome):
    handler = Mock(side_effect=exception)
    registry, _ = _registry(dependency_resubmit=handler)
    effect = TypedEffect.parse({"kind": "dependency-resubmit", "generation": "g2", "parameters": {"dependency_id": "W", "queue_id": "coding", "failed_generation": "g1"}})

    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "a"})).execute(request_id="r", effect=effect)

    assert receipt.outcome is outcome
    assert str(exception) in receipt.detail


def test_release_failure_invokes_only_registered_rollback_and_receipts_it():
    install = Mock(side_effect=RuntimeError("selection failed"))
    rollback = Mock(return_value={"receipt": "rollback:exact"})
    registry, _ = _registry(release_install=install, release_rollback=rollback)
    effect = TypedEffect.parse(
        {
            "kind": "coding-release",
            "generation": "release-b",
            "parameters": {
                "candidate_commit": SHA,
                "candidate_tree": TREE,
                "archive_sha256": DIGEST,
                "artifact_ref": "artifact:1",
                "root_id": "tgw-staging",
                "expected_current": "release-a",
                "operation_id": "install-b",
                "review_receipt": "review:1",
                "controller_receipt": "controller:1",
            },
        }
    )

    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "authority:1"})).execute(request_id="request:1", effect=effect)

    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt == "rollback:exact"
    rollback.assert_called_once()


def test_authority_rejection_prevents_handler_invocation():
    registry, providers = _registry()
    effect = TypedEffect.parse({"kind": "dependency-resubmit", "generation": "g2", "parameters": {"dependency_id": "W", "queue_id": "coding", "failed_generation": "g1"}})

    with pytest.raises(ValueError, match="already consumed"):
        AuthorityEffectController(registry, Mock(side_effect=ValueError("already consumed"))).execute(request_id="r", effect=effect)
    assert all(provider.call_count == 0 for provider in providers.values())


def test_authority_canary_is_internal_receipt_only_and_cannot_broaden_purpose():
    registry, providers = _registry()
    consume = Mock(return_value={"receipt_id": "authority:canary"})
    effect = TypedEffect.parse(
        {
            "kind": "authority-canary",
            "generation": "w10-canary-1",
            "parameters": {"canary_id": "canary:w10-1", "purpose": "verify-plan-authority-roundtrip"},
        }
    )
    receipt = AuthorityEffectController(registry, consume).execute(request_id="request:canary", effect=effect)
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.handler_id == "authority-canary-receipt-only@1"
    assert receipt.evidence[0].startswith("authority-canary:sha256:")
    assert all(provider.call_count == 0 for provider in providers.values())

    broadened = TypedEffect.parse(
        {
            "kind": "authority-canary",
            "generation": "w10-canary-2",
            "parameters": {"canary_id": "canary:w10-2", "purpose": "deploy-platform"},
        }
    )
    with pytest.raises(ValueError, match="harmless registered bound"):
        AuthorityEffectController(registry, consume).execute(request_id="request:bad", effect=broadened)
    assert consume.call_count == 1


def test_bootstrap_effect_rejects_host_path_command_digest_and_cas_broadening_before_consumption():
    registry, _ = _registry()
    consume = Mock()
    base = next(
        parameters
        for kind, parameters in [
            (
                "approval-platform-bootstrap-deployment",
                {
                    "target_host": "tgw-prod",
                    "flake_repository_id": "tgw-flake",
                    "flake_commit": SHA,
                    "flake_tree": TREE,
                    "expected_current_system": "/nix/store/aaaaaaaa-nixos-system-tgw-prod-old",
                    "successor_system": "/nix/store/bbbbbbbb-nixos-system-tgw-prod-new",
                    "credential_ref": "credential:tgw-review:codex",
                    "credential_sha256": DIGEST,
                    "broker_source_sha256": DIGEST,
                    "namespace_source_sha256": DIGEST,
                    "nix_module_sha256": DIGEST,
                    "egress_contract_sha256": DIGEST,
                    "install_contract_sha256": DIGEST,
                    "review_receipt": "review:passed",
                    "controller_receipt": "controller:passed",
                    "network_attestation_receipt": "network:passed",
                    "probe_receipt": "probes:passed",
                    "operation_id": "bootstrap:review-transport-1",
                },
            )
        ]
        if kind
    )
    changes = (
        {"target_host": "other"},
        {"credential_ref": "/home/codex/.codex/auth.json"},
        {"credential_sha256": "unbound"},
        {"successor_system": base["expected_current_system"]},
        {"command": "nixos-rebuild switch"},
    )
    for change in changes:
        effect = {"kind": "approval-platform-bootstrap-deployment", "generation": "nixos-review-transport-1", "parameters": {**base, **change}}
        with pytest.raises(ValueError):
            AuthorityEffectController(registry, consume).execute(request_id="bootstrap", effect=TypedEffect.parse(effect))
    consume.assert_not_called()


def test_bootstrap_provider_failure_rolls_back_only_registered_prior_closure():
    install = Mock(side_effect=RuntimeError("health probe failed"))
    rollback = Mock(return_value={"receipt": "nixos:prior-closure-restored"})
    registry, _ = _registry(bootstrap_install=install, bootstrap_rollback=rollback)
    parameters = {
        "target_host": "tgw-prod",
        "flake_repository_id": "tgw-flake",
        "flake_commit": SHA,
        "flake_tree": TREE,
        "expected_current_system": "/nix/store/aaaaaaaa-nixos-system-tgw-prod-old",
        "successor_system": "/nix/store/bbbbbbbb-nixos-system-tgw-prod-new",
        "credential_ref": "credential:tgw-review:codex",
        "credential_sha256": DIGEST,
        "broker_source_sha256": DIGEST,
        "namespace_source_sha256": DIGEST,
        "nix_module_sha256": DIGEST,
        "egress_contract_sha256": DIGEST,
        "install_contract_sha256": DIGEST,
        "review_receipt": "review:passed",
        "controller_receipt": "controller:passed",
        "network_attestation_receipt": "network:passed",
        "probe_receipt": "probes:passed",
        "operation_id": "bootstrap:review-transport-1",
    }
    effect = TypedEffect.parse({"kind": "approval-platform-bootstrap-deployment", "generation": "nixos-review-transport-1", "parameters": parameters})
    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "bootstrap:consumed"})).execute(request_id="bootstrap", effect=effect)
    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt == "nixos:prior-closure-restored"
    rollback.assert_called_once()


@pytest.mark.parametrize(
    "change",
    [
        {"target_host": "other"},
        {"artifact_ref": "/home/db/tgw-flake"},
        {"evaluation_target": "nixosConfigurations.tgw-prod.config.system.build.toplevel"},
        {"nix_network_policy": "online"},
        {"activate": "true"},
        {"profile_write": "true"},
        {"home_db_write": "true"},
        {"module_path": "../../etc/passwd"},
        {"max_duration_seconds": "901"},
        {"max_output_bytes": "16777217"},
        {"command": "nixos-rebuild switch"},
        {"scratch_id": "other:run"},
        {"operation_id": "bad identity with spaces"},
    ],
)
def test_reviewed_nixos_evaluation_rejects_broadening_before_authority(change):
    registry, providers = _registry()
    consume = Mock()
    value = {**_evaluation_parameters(), **change}
    with pytest.raises(ValueError):
        effect = TypedEffect.parse({"kind": "nixos-reviewed-evaluation", "generation": "eval-1", "parameters": value})
        AuthorityEffectController(registry, consume).execute(request_id="eval", effect=effect)
    consume.assert_not_called()
    providers["nixos_reviewed_evaluation"].assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"cleanup": "present"},
        {"activate": True},
        {"profile_write": True},
        {"home_db_write": True},
        {"systemd_verify_exit": 1},
        {"unit_sha256": {}},
        {"source_tree": "d" * 40},
        {"evaluated_config_drv": "/tmp/fake.drv"},
        {"closure_path_count": 0},
    ],
)
def test_reviewed_nixos_evaluation_fails_closed_on_unsafe_or_unbound_receipt(change):
    provider = Mock(side_effect=lambda parameters: {**_evaluation_result(parameters), **change})
    registry, _ = _registry(nixos_reviewed_evaluation=provider)
    consume = Mock(return_value={"receipt_id": "authority:eval"})
    effect = TypedEffect.parse({"kind": "nixos-reviewed-evaluation", "generation": "eval-1", "parameters": _evaluation_parameters()})

    receipt = AuthorityEffectController(registry, consume).execute(request_id="eval", effect=effect)

    assert receipt.outcome is EffectOutcome.FAILED
    assert not receipt.evidence


def test_reviewed_nixos_evaluation_emits_only_validated_immutable_evidence():
    registry, providers = _registry()
    effect = TypedEffect.parse({"kind": "nixos-reviewed-evaluation", "generation": "eval-1", "parameters": _evaluation_parameters()})

    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "authority:eval"})).execute(request_id="eval", effect=effect)

    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.handler_id == "nixos-reviewed-evaluation@1"
    assert receipt.evidence[0].startswith("nixos-evaluation:sha256:")
    passed = providers["nixos_reviewed_evaluation"].call_args.args[0]
    assert passed["generation"] == "eval-1"
    assert passed["kind"] == "nixos-reviewed-evaluation"
    assert "generation" not in passed["parameters"]
    assert "command" not in passed["parameters"] and "path" not in passed["parameters"]
