from unittest.mock import Mock

import pytest

from tgw.effect_handlers import AmbiguousEffect, AuthorityEffectController, EffectOutcome, RetryableEffect, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect

SHA = "a" * 40
TREE = "b" * 40
DIGEST = "c" * 64


def _registry(**changes):
    providers = {
        "release_install": Mock(return_value={"evidence": ["release:selected"]}),
        "release_rollback": Mock(return_value={"receipt": "rollback:1"}),
        "flake_push": Mock(return_value={"evidence": ["remote:readback"]}),
        "flake_switch_record": Mock(return_value={"evidence": ["switch:recorded"]}),
        "dependency_resubmit": Mock(return_value={"evidence": ["queue:accepted"]}),
    }
    providers.update(changes)
    return TypedEffectHandlerRegistry(**providers), providers


@pytest.mark.parametrize(
    ("kind", "parameters"),
    [
        (
            "coding-release",
            {"candidate_commit": SHA, "candidate_tree": TREE, "archive_sha256": DIGEST, "artifact_ref": "artifact:release-1", "expected_current": "release-a", "operation_id": "install-b"},
        ),
        ("bounded-flake-push", {"repository_id": "tgw-flake", "host_role": "production", "commit": SHA, "remote_ref": "origin/master"}),
        ("flake-switch-record-only", {"host_role": "production", "commit": SHA, "execution_receipt": "manual:1"}),
        ("dependency-resubmit", {"dependency_id": "W03", "queue_id": "coding", "failed_generation": "generation-1"}),
    ],
)
def test_all_four_registered_effects_consume_exact_authority_then_invoke_typed_provider(kind, parameters):
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
    assert sum(provider.call_count for provider in providers.values()) == 1


@pytest.mark.parametrize(
    "effect",
    [
        {
            "kind": "coding-release",
            "generation": "g",
            "parameters": {"candidate_commit": SHA, "candidate_tree": TREE, "archive_sha256": DIGEST, "artifact_ref": "/tmp/arbitrary", "expected_current": "a", "operation_id": "x", "command": "sh"},
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
            "parameters": {"candidate_commit": SHA, "candidate_tree": TREE, "archive_sha256": DIGEST, "artifact_ref": "artifact:1", "expected_current": "release-a", "operation_id": "install-b"},
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
