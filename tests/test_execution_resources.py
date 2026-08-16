import hashlib
import json

import pytest

from tgw.execution_resources import (
    RegisteredResourceResolver,
    ResourceVerificationError,
    verify_card_resources,
)

PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"


def canonical_hash(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def card_and_resources(*, plan_commit_resource=PLAN_COMMIT):
    resources = {
        "plan:input": "specification-formatted Plan input",
        "plan:commit": plan_commit_resource,
        "plan:graph": "resolved Plan Graph snapshot",
        "codegraph:snapshot": "CodeGraph snapshot",
        "git:source": "source tree content",
        "environment:manifest": "execution environment manifest",
        "authority:conditions": "authority and solved conditions",
        "candidate:evidence": "candidate evidence descriptor",
        "receipt:sink": "registered receipt sink descriptor",
    }
    bindings = {
        name: {
            "ref": ref,
            "hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest(),
        }
        for name, (ref, content) in {
            "plan_input": ("plan:input", resources["plan:input"]),
            "plan_commit": ("plan:commit", resources["plan:commit"]),
            "plan_graph": ("plan:graph", resources["plan:graph"]),
            "codegraph_snapshot": ("codegraph:snapshot", resources["codegraph:snapshot"]),
            "source_tree": ("git:source", resources["git:source"]),
            "execution_environment": ("environment:manifest", resources["environment:manifest"]),
            "authority_conditions": ("authority:conditions", resources["authority:conditions"]),
            "candidate_evidence": ("candidate:evidence", resources["candidate:evidence"]),
            "receipt_sink": ("receipt:sink", resources["receipt:sink"]),
        }.items()
    }
    unsigned = {
        "schema": "tgw-execution-card/v1",
        "card_id": "resource-test-card",
        "plan_commit": PLAN_COMMIT,
        "bindings": bindings,
    }
    return {**unsigned, "card_hash": canonical_hash(unsigned)}, resources


def test_registered_resolver_fetches_every_card_resource_and_binds_one_receipt():
    card, resources = card_and_resources()

    receipt = verify_card_resources(card, RegisteredResourceResolver(resources))

    assert receipt["card_hash"] == card["card_hash"]
    assert receipt["plan_commit"] == PLAN_COMMIT
    assert receipt["resources"] == card["bindings"]
    assert receipt["receipt_hash"] == canonical_hash(
        {key: value for key, value in receipt.items() if key != "receipt_hash"}
    )


def test_registered_plan_commit_must_match_card_even_when_its_hash_is_valid():
    card, resources = card_and_resources(plan_commit_resource="other-plan-commit")

    with pytest.raises(ResourceVerificationError, match="Plan commit does not match card"):
        verify_card_resources(card, RegisteredResourceResolver(resources))


def test_plan_graph_cannot_stand_in_for_codegraph_snapshot():
    card, resources = card_and_resources()
    card["bindings"]["codegraph_snapshot"] = dict(card["bindings"]["plan_graph"])
    unsigned = dict(card)
    unsigned.pop("card_hash")
    card["card_hash"] = canonical_hash(unsigned)

    with pytest.raises(ResourceVerificationError, match="distinct registered references"):
        verify_card_resources(card, RegisteredResourceResolver(resources))
