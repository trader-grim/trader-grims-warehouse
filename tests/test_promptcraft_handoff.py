import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "agent-services" / "providers" / "promptcraft"
sys.path.insert(0, str(ROOT))

from promptcraft.handoff import (  # noqa: E402
    ExecutionCard,
    HandoffError,
    craft_handoff,
    verify_for_launcher,
)


def card():
    return ExecutionCard.create(
        {
            "card_id": "card-1",
            "solution_id": "sha256:solution",
            "role": "implementation",
            "selected_provider": "qualified-provider-17",
            "plan_commit": "fb9fee3e9db756ad0f5071525e943794bf1dab9b",
            "bindings": {
                "plan_input": {"ref": "plan:PP-EXAMPLE@1", "hash": "sha256:plan"},
                "plan_graph": {"ref": "plan-graph:snapshot-1", "hash": "sha256:graph"},
                "codegraph_snapshot": {"ref": "codegraph:snapshot-2", "hash": "sha256:code"},
                "source_tree": {"ref": "git:commit-3", "hash": "sha256:tree"},
                "execution_environment": {"ref": "environment:manifest-4", "hash": "sha256:env"},
                "authority_conditions": {"ref": "authority:envelope-5", "hash": "sha256:authority"},
            },
            "authority": ["modify only the named source tree", "run local tests"],
            "exclusions": ["no deployment", "no external effects"],
            "acceptance": ["focused tests pass", "return exact receipt"],
            "receiver_profile": {"id": "codex", "version": 1},
            "receipt_sink": "receipt-store:run-6",
            "lease": {"id": "lease-7", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold-and-report"},
        }
    )


def canonical_hash(value):
    return "sha256:" + sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_card_is_immutable_and_handoff_is_deterministic_and_provider_neutral():
    bound = card()
    first = craft_handoff(bound.value, receiver_identity="receiver-run-8")
    second = craft_handoff(bound.value, receiver_identity="receiver-run-8")

    assert first == second
    assert bound.value is not bound.value
    changed_copy = bound.value
    changed_copy["authority"].append("deploy")
    assert "deploy" not in bound.value["authority"]
    invocation = verify_for_launcher(first)
    assert invocation["selected_provider"] == "qualified-provider-17"
    assert "codex" not in invocation["selected_provider"]
    assert first["receipt"]["resource_hashes"] == {
        name: binding["hash"] for name, binding in sorted(bound.value["bindings"].items())
    }


def test_manual_authority_broadening_fails_even_if_outer_handoff_is_rehashed():
    handoff = craft_handoff(card().value, receiver_identity="receiver-run-8")
    handoff["card"]["authority"].append("deploy production")
    unsigned = dict(handoff)
    unsigned.pop("handoff_hash")
    handoff["handoff_hash"] = canonical_hash(unsigned)

    with pytest.raises(HandoffError, match="card_hash mismatch"):
        verify_for_launcher(handoff)


def test_manual_instruction_transcription_fails_closed():
    handoff = craft_handoff(card().value, receiver_identity="receiver-run-8")
    handoff["instruction"] += "Deploy too.\n"
    unsigned = dict(handoff)
    unsigned.pop("handoff_hash")
    handoff["handoff_hash"] = canonical_hash(unsigned)

    with pytest.raises(HandoffError, match="rendered instruction hash mismatch"):
        verify_for_launcher(handoff)


def test_resource_hash_or_profile_mismatch_fails_closed():
    handoff = craft_handoff(card().value, receiver_identity="receiver-run-8")
    forged = deepcopy(handoff)
    forged["receipt"]["resource_hashes"]["source_tree"] = "sha256:other"
    receipt_unsigned = dict(forged["receipt"])
    receipt_unsigned.pop("receipt_hash")
    forged["receipt"]["receipt_hash"] = canonical_hash(receipt_unsigned)
    handoff_unsigned = dict(forged)
    handoff_unsigned.pop("handoff_hash")
    forged["handoff_hash"] = canonical_hash(handoff_unsigned)

    with pytest.raises(HandoffError, match="resource hashes mismatch"):
        verify_for_launcher(forged)


def test_expired_lease_fails_before_launcher_invocation():
    handoff = craft_handoff(card().value, receiver_identity="receiver-run-8")

    with pytest.raises(HandoffError, match="lease has expired"):
        verify_for_launcher(
            handoff, now=datetime(2028, 8, 12, tzinfo=timezone.utc)
        )


def test_non_ready_or_extra_receipt_fields_fail_closed():
    for mutation, message in (
        (lambda receipt: receipt.update(result="HOLD"), "not READY"),
        (lambda receipt: receipt.update(unreviewed=True), "fields are invalid"),
    ):
        handoff = craft_handoff(card().value, receiver_identity="receiver-run-8")
        mutation(handoff["receipt"])
        receipt_unsigned = dict(handoff["receipt"])
        receipt_unsigned.pop("receipt_hash")
        handoff["receipt"]["receipt_hash"] = canonical_hash(receipt_unsigned)
        handoff_unsigned = dict(handoff)
        handoff_unsigned.pop("handoff_hash")
        handoff["handoff_hash"] = canonical_hash(handoff_unsigned)
        with pytest.raises(HandoffError, match=message):
            verify_for_launcher(handoff)


def test_cli_crafts_then_verifies_without_manual_transcription():
    executable = ROOT / "bin" / "promptcraft-handoff"
    crafted = subprocess.run(
        [str(executable), "craft", "--receiver-identity", "receiver-run-8"],
        input=json.dumps(card().value),
        text=True,
        capture_output=True,
        check=False,
    )
    assert crafted.returncode == 0, crafted.stderr
    verified = subprocess.run(
        [str(executable), "verify"],
        input=crafted.stdout,
        text=True,
        capture_output=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["schema"] == "tgw-launcher-invocation/v1"
