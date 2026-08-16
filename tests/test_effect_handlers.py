from unittest.mock import Mock

import pytest

from tgw.bootstrap_deployment_contract import VerifiedBootstrapDeploymentContract
from tgw.effect_handlers import AmbiguousEffect, AuthorityEffectController, EffectOutcome, RetryableEffect, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect

SHA = "a" * 40
TREE = "b" * 40
DIGEST = "c" * 64
BOOTSTRAP_REF = f"candidate:{SHA}:bootstrap-deployment:v1"
BOOTSTRAP_HASH = "sha256:" + "d" * 64
BOOTSTRAP_GENERATION = "candidate-release"
EXECUTOR = "executor:fixture-runner"


class _AuthorityStore:
    """Durable authority seam double; records both lifecycle writes."""

    def __init__(self, *, receipt_id="authority:1", begin_error=None, complete_error=None):
        self.begin_execution = Mock(
            return_value={"receipt_id": receipt_id}, side_effect=begin_error,
        )
        self.complete_execution = Mock(return_value={}, side_effect=complete_error)


class _BootstrapContractResolver:
    """Inert test seam standing in for the separately configured X resolver."""

    def __init__(self):
        self.resolve = Mock(return_value=VerifiedBootstrapDeploymentContract(
            reference=BOOTSTRAP_REF,
            contract_hash=BOOTSTRAP_HASH,
            intended_next_generation=BOOTSTRAP_GENERATION,
        ))


def _registry(**changes):
    resolver = changes.pop("bootstrap_contract_resolver", _BootstrapContractResolver())
    providers = {
        "release_install": Mock(return_value={"evidence": ["release:selected"]}),
        "release_rollback": Mock(return_value={"receipt": "rollback:1"}),
        "flake_push": Mock(return_value={"evidence": ["remote:readback"]}),
        "flake_switch_record": Mock(return_value={"evidence": ["switch:recorded"]}),
        "dependency_resubmit": Mock(return_value={"evidence": ["queue:accepted"]}),
        "bootstrap_install": Mock(return_value={"evidence": ["nixos:switched", "probes:passed"]}),
        "bootstrap_rollback": Mock(return_value={"receipt": "nixos:rollback"}),
    }
    providers.update(changes)
    return TypedEffectHandlerRegistry(bootstrap_contract_resolver=resolver, **providers), providers


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
        ("approval-platform-bootstrap-deployment", {
            "bootstrap_contract_ref": BOOTSTRAP_REF,
            "bootstrap_contract_hash": BOOTSTRAP_HASH,
        }),
    ],
)
def test_registered_effects_consume_exact_authority_then_invoke_only_their_handler(kind, parameters):
    registry, providers = _registry()
    authority = _AuthorityStore()
    generation = BOOTSTRAP_GENERATION if kind == "approval-platform-bootstrap-deployment" else "generation-2"
    effect = TypedEffect.parse({"kind": kind, "generation": generation, "parameters": parameters})

    receipt = AuthorityEffectController(registry, authority).execute(request_id="request:1", effect=effect, executor_principal=EXECUTOR)

    authority.begin_execution.assert_called_once_with(
        "request:1", effect_hash=effect.effect_hash, generation=generation,
        handler_id=receipt.handler_id,
        executor_principal=EXECUTOR,
    )
    authority.complete_execution.assert_called_once_with(
        "authority:1", outcome="succeeded", evidence=receipt.evidence,
        rollback_receipt=None, detail="",
    )
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.effect_hash == effect.effect_hash
    assert receipt.executor_principal == EXECUTOR
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
    authority = _AuthorityStore()
    with pytest.raises(ValueError):
        parsed = TypedEffect.parse(effect)
        AuthorityEffectController(registry, authority).execute(request_id="request:1", effect=parsed, executor_principal=EXECUTOR)
    authority.begin_execution.assert_not_called()


@pytest.mark.parametrize(
    ("exception", "outcome"),
    [(RetryableEffect("provider unavailable"), EffectOutcome.RETRY), (AmbiguousEffect("outcome unknown"), EffectOutcome.AMBIGUOUS)],
)
def test_retry_and_ambiguity_emit_distinct_receipts(exception, outcome):
    handler = Mock(side_effect=exception)
    registry, _ = _registry(dependency_resubmit=handler)
    effect = TypedEffect.parse({"kind": "dependency-resubmit", "generation": "g2", "parameters": {"dependency_id": "W", "queue_id": "coding", "failed_generation": "g1"}})
    authority = _AuthorityStore(receipt_id="a")

    receipt = AuthorityEffectController(registry, authority).execute(request_id="r", effect=effect, executor_principal=EXECUTOR)

    assert receipt.outcome is outcome
    assert str(exception) in receipt.detail
    authority.complete_execution.assert_called_once_with(
        "a", outcome=outcome.value, evidence=(), rollback_receipt=None, detail=str(exception),
    )


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

    authority = _AuthorityStore()
    receipt = AuthorityEffectController(registry, authority).execute(request_id="request:1", effect=effect, executor_principal=EXECUTOR)

    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt == "rollback:exact"
    rollback.assert_called_once()
    authority.complete_execution.assert_called_once_with(
        "authority:1", outcome="rolled_back", evidence=(),
        rollback_receipt="rollback:exact", detail="selection failed",
    )


def test_authority_rejection_prevents_handler_invocation():
    registry, providers = _registry()
    effect = TypedEffect.parse({"kind": "dependency-resubmit", "generation": "g2", "parameters": {"dependency_id": "W", "queue_id": "coding", "failed_generation": "g1"}})

    with pytest.raises(ValueError, match="already executing"):
        AuthorityEffectController(registry, _AuthorityStore(begin_error=ValueError("already executing"))).execute(request_id="r", effect=effect, executor_principal=EXECUTOR)
    assert all(provider.call_count == 0 for provider in providers.values())


def test_authority_canary_is_internal_receipt_only_and_cannot_broaden_purpose():
    registry, providers = _registry()
    authority = _AuthorityStore(receipt_id="authority:canary")
    effect = TypedEffect.parse({
        "kind": "authority-canary", "generation": "w10-canary-1",
        "parameters": {"canary_id": "canary:w10-1", "purpose": "verify-plan-authority-roundtrip"},
    })
    receipt = AuthorityEffectController(registry, authority).execute(request_id="request:canary", effect=effect, executor_principal=EXECUTOR)
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.handler_id == "authority-canary-receipt-only@1"
    assert receipt.evidence[0].startswith("authority-canary:sha256:")
    assert all(provider.call_count == 0 for provider in providers.values())

    broadened = TypedEffect.parse({
        "kind": "authority-canary", "generation": "w10-canary-2",
        "parameters": {"canary_id": "canary:w10-2", "purpose": "deploy-platform"},
    })
    with pytest.raises(ValueError, match="harmless registered bound"):
        AuthorityEffectController(registry, authority).execute(request_id="request:bad", effect=broadened, executor_principal=EXECUTOR)
    assert authority.begin_execution.call_count == 1


def test_bootstrap_effect_rejects_host_path_command_digest_and_cas_broadening_before_consumption():
    registry, _ = _registry()
    authority = _AuthorityStore()
    base = {"bootstrap_contract_ref": BOOTSTRAP_REF, "bootstrap_contract_hash": BOOTSTRAP_HASH}
    changes = (
        {"target_host": "other"}, {"candidate_commit": SHA},
        {"bootstrap_contract_ref": "symbolic:latest"}, {"bootstrap_contract_hash": "sha256:" + "0" * 64},
        {"command": "nixos-rebuild switch"},
    )
    for change in changes:
        effect = {"kind": "approval-platform-bootstrap-deployment", "generation": BOOTSTRAP_GENERATION, "parameters": {**base, **change}}
        with pytest.raises(ValueError):
            AuthorityEffectController(registry, authority).execute(
                request_id="bootstrap", effect=TypedEffect.parse(effect), executor_principal=EXECUTOR,
            )
    with pytest.raises(ValueError, match="does not match"):
        AuthorityEffectController(registry, authority).execute(
            request_id="bootstrap",
            effect=TypedEffect.parse({
                "kind": "approval-platform-bootstrap-deployment", "generation": "wrong-generation", "parameters": base,
            }),
            executor_principal=EXECUTOR,
        )
    authority.begin_execution.assert_not_called()


def test_bootstrap_provider_failure_rolls_back_only_registered_prior_closure():
    install = Mock(side_effect=RuntimeError("health probe failed"))
    rollback = Mock(return_value={"receipt": "nixos:prior-closure-restored"})
    registry, _ = _registry(bootstrap_install=install, bootstrap_rollback=rollback)
    parameters = {"bootstrap_contract_ref": BOOTSTRAP_REF, "bootstrap_contract_hash": BOOTSTRAP_HASH}
    effect = TypedEffect.parse({
        "kind": "approval-platform-bootstrap-deployment", "generation": BOOTSTRAP_GENERATION, "parameters": parameters,
    })
    authority = _AuthorityStore(receipt_id="bootstrap:attempt")
    receipt = AuthorityEffectController(registry, authority).execute(request_id="bootstrap", effect=effect, executor_principal=EXECUTOR)
    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt == "nixos:prior-closure-restored"
    rollback.assert_called_once_with(parameters)


def test_bootstrap_dry_run_provider_receives_only_a_verified_immutable_contract_binding():
    registry, providers = _registry()
    authority = _AuthorityStore(receipt_id="bootstrap:dry-run")
    parameters = {"bootstrap_contract_ref": BOOTSTRAP_REF, "bootstrap_contract_hash": BOOTSTRAP_HASH}
    receipt = AuthorityEffectController(registry, authority).execute(
        request_id="bootstrap:dry-run",
        effect=TypedEffect.parse({
            "kind": "approval-platform-bootstrap-deployment", "generation": BOOTSTRAP_GENERATION,
            "parameters": parameters,
        }),
        executor_principal=EXECUTOR,
    )

    assert receipt.outcome is EffectOutcome.SUCCEEDED
    providers["bootstrap_install"].assert_called_once_with(parameters)


def test_bootstrap_effect_without_an_exact_contract_resolver_holds_before_authority_consumption():
    registry, _ = _registry(bootstrap_contract_resolver=None)
    authority = _AuthorityStore()
    effect = TypedEffect.parse({
        "kind": "approval-platform-bootstrap-deployment", "generation": BOOTSTRAP_GENERATION,
        "parameters": {"bootstrap_contract_ref": BOOTSTRAP_REF, "bootstrap_contract_hash": BOOTSTRAP_HASH},
    })

    with pytest.raises(ValueError, match="resolver is not mounted"):
        AuthorityEffectController(registry, authority).execute(
            request_id="bootstrap:unmounted", effect=effect, executor_principal=EXECUTOR,
        )
    authority.begin_execution.assert_not_called()
