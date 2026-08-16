"""Candidate-bound evidence for one governed execution role.

This is deliberately a compact join over already immutable objects: an
execution card, the registered-resource receipt produced before Promptcraft,
and the governed role receipt produced after the runner returns.  It does not
re-run a provider or reconstruct context from mutable worktree state.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    RESOURCE_RECEIPT_SCHEMA,
    ResourceVerificationError,
    resource_service_attestation_key,
    validate_harness_retrieval_attestation,
    verify_card_resource_service_catalog,
)
from tgw.governed_coding import validate_receipt

SCHEMA = "tgw-candidate-governed-execution-receipt/v1"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OBJECT = re.compile(r"[0-9a-f]{40}\Z")


class GovernedExecutionReceiptError(ValueError):
    """The card/resource/role evidence cannot be tied to one candidate."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bound_card(
    card: Mapping[str, Any], *, source_tree: str, plan_commit: str,
) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(card, Mapping):
        raise GovernedExecutionReceiptError("execution card is invalid")
    if card.get("schema") != "tgw-execution-card/v1":
        raise GovernedExecutionReceiptError("execution card schema is invalid")
    claimed = card.get("card_hash")
    unsigned = dict(card)
    unsigned.pop("card_hash", None)
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise GovernedExecutionReceiptError("execution card hash is invalid")
    for field in ("card_id", "solution_id", "role", "selected_provider", "plan_commit"):
        if not isinstance(card.get(field), str) or not card[field]:
            raise GovernedExecutionReceiptError(f"execution card {field} is invalid")
    if card.get("plan_commit") != plan_commit:
        raise GovernedExecutionReceiptError("execution card Plan commit does not match candidate")
    for field in ("authority", "exclusions", "acceptance"):
        if not isinstance(card.get(field), list) or not all(isinstance(item, str) for item in card[field]):
            raise GovernedExecutionReceiptError(f"execution card {field} is invalid")
    profile = card.get("receiver_profile")
    if not isinstance(profile, Mapping) or set(profile) != {"id", "version"}:
        raise GovernedExecutionReceiptError("execution card receiver profile is invalid")
    lease = card.get("lease")
    if not isinstance(lease, Mapping) or set(lease) != {"id", "expires_at", "stop_policy"}:
        raise GovernedExecutionReceiptError("execution card lease is invalid")
    bindings = card.get("bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != CARD_RESOURCE_NAMES:
        raise GovernedExecutionReceiptError("execution card resource bindings are invalid")
    for name, binding in bindings.items():
        if (
            not isinstance(binding, Mapping)
            or set(binding) != {"ref", "hash"}
            or not isinstance(binding["ref"], str)
            or not binding["ref"]
            or not isinstance(binding["hash"], str)
            or _SHA256.fullmatch(binding["hash"]) is None
        ):
            raise GovernedExecutionReceiptError(f"execution card binding {name} is invalid")
    resource_service = card.get("resource_service")
    if (
        not isinstance(resource_service, Mapping)
        or set(resource_service) != {"id", "client_id", "descriptor_hash", "catalog_ref", "catalog_hash"}
        or not isinstance(resource_service["id"], str)
        or not resource_service["id"]
        or not isinstance(resource_service["client_id"], str)
        or not resource_service["client_id"]
        or not isinstance(resource_service["descriptor_hash"], str)
        or _SHA256.fullmatch(resource_service["descriptor_hash"]) is None
        or not isinstance(resource_service["catalog_ref"], str)
        or not resource_service["catalog_ref"]
        or not isinstance(resource_service["catalog_hash"], str)
        or _SHA256.fullmatch(resource_service["catalog_hash"]) is None
    ):
        raise GovernedExecutionReceiptError("execution card resource service binding is invalid")
    if bindings["source_tree"]["ref"] != f"git:tree:{source_tree}":
        raise GovernedExecutionReceiptError("execution card source tree does not match candidate")
    return claimed, bindings


def _bound_resource_receipt(
    receipt: Mapping[str, Any], *, card_hash: str, plan_commit: str, bindings: Mapping[str, Any],
) -> str:
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RESOURCE_RECEIPT_SCHEMA:
        raise GovernedExecutionReceiptError("execution resource receipt is invalid")
    required = {"schema", "card_hash", "plan_commit", "resources", "receipt_hash"}
    if set(receipt) != required:
        raise GovernedExecutionReceiptError("execution resource receipt fields are invalid")
    unsigned = dict(receipt)
    claimed = unsigned.pop("receipt_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise GovernedExecutionReceiptError("execution resource receipt hash is invalid")
    if receipt["card_hash"] != card_hash or receipt["plan_commit"] != plan_commit:
        raise GovernedExecutionReceiptError("execution resource receipt card binding mismatch")
    expected_resources = {name: bindings[name] for name in sorted(bindings)}
    if receipt["resources"] != expected_resources:
        raise GovernedExecutionReceiptError("execution resource receipt resource binding mismatch")
    return claimed


def create_candidate_governed_execution_receipt(
    *, card: Mapping[str, Any], resource_receipt: Mapping[str, Any], role_receipt: Mapping[str, Any],
    resource_service_catalog: Mapping[str, Any], source_commit: str, source_tree: str, plan_commit: str,
) -> dict[str, Any]:
    """Create a PASS receipt only when all three role artifacts bind one candidate."""

    if not isinstance(source_commit, str) or _GIT_OBJECT.fullmatch(source_commit) is None:
        raise GovernedExecutionReceiptError("candidate source commit is invalid")
    if not isinstance(source_tree, str) or _GIT_OBJECT.fullmatch(source_tree) is None:
        raise GovernedExecutionReceiptError("candidate source tree is invalid")
    if not isinstance(plan_commit, str) or not plan_commit:
        raise GovernedExecutionReceiptError("candidate Plan commit is invalid")
    card_hash, bindings = _bound_card(card, source_tree=source_tree, plan_commit=plan_commit)
    try:
        verify_card_resource_service_catalog(card, resource_service_catalog)
        attestation_key = resource_service_attestation_key(
            resource_service_catalog, card["resource_service"]["id"], card["resource_service"]["client_id"],
        )
    except ResourceVerificationError as exc:
        raise GovernedExecutionReceiptError(f"execution card resource service catalog is invalid: {exc}") from exc
    resource_hash = _bound_resource_receipt(
        resource_receipt, card_hash=card_hash, plan_commit=plan_commit, bindings=bindings,
    )
    try:
        validate_receipt(role_receipt)
    except ValueError as exc:
        raise GovernedExecutionReceiptError(f"governed role receipt is invalid: {exc}") from exc
    if role_receipt.get("status") != "PASS":
        raise GovernedExecutionReceiptError("candidate governed role receipt is not passing")
    if (
        role_receipt.get("card_hash") != card_hash
        or role_receipt.get("resource_receipt_hash") != resource_hash
        or role_receipt.get("harness_resource_receipt_hash") != resource_hash
        or not isinstance(role_receipt.get("harness_retrieval_attestation_hash"), str)
        or _SHA256.fullmatch(str(role_receipt.get("harness_retrieval_attestation_hash"))) is None
        or not isinstance(role_receipt.get("harness_retrieval_attestation"), Mapping)
        or role_receipt["harness_retrieval_attestation"].get("attestation_hash")
        != role_receipt.get("harness_retrieval_attestation_hash")
        or role_receipt.get("selected_provider") != card["selected_provider"]
        or role_receipt.get("role") != card["role"]
        or role_receipt.get("resource_service_descriptor_hash") != card["resource_service"]["descriptor_hash"]
        or role_receipt.get("resource_service_client_id") != card["resource_service"]["client_id"]
        or role_receipt.get("resource_service_catalog_ref") != card["resource_service"]["catalog_ref"]
        or role_receipt.get("resource_service_catalog_hash") != card["resource_service"]["catalog_hash"]
    ):
        raise GovernedExecutionReceiptError("governed role receipt binding mismatch")
    try:
        validate_harness_retrieval_attestation(
            role_receipt["harness_retrieval_attestation"],
            expected={
                "card_hash": card_hash, "role": card["role"],
                "execution_identity": role_receipt["execution_identity"],
                "resource_receipt_hash": resource_hash, "resources": {name: bindings[name] for name in sorted(bindings)},
                "service_id": card["resource_service"]["id"],
                "client_id": card["resource_service"]["client_id"],
            },
            **attestation_key,
        )
    except ResourceVerificationError as exc:
        raise GovernedExecutionReceiptError(f"governed role retrieval attestation is invalid: {exc}") from exc
    role_hash = role_receipt.get("receipt_hash")
    if not isinstance(role_hash, str) or _SHA256.fullmatch(role_hash) is None:
        raise GovernedExecutionReceiptError("governed role receipt hash is invalid")
    unsigned = {
        "schema": SCHEMA,
        "status": "PASS",
        "source_commit": source_commit,
        "source_tree": source_tree,
        "plan_commit": plan_commit,
        "role": role_receipt["role"],
        "selected_provider": card["selected_provider"],
        "card_hash": card_hash,
        "resource_receipt_hash": resource_hash,
        "harness_retrieval_attestation_hash": role_receipt["harness_retrieval_attestation_hash"],
        "harness_retrieval_attestation": dict(role_receipt["harness_retrieval_attestation"]),
        "service_id": card["resource_service"]["id"],
        "service_client_id": card["resource_service"]["client_id"],
        "execution_identity": role_receipt["execution_identity"],
        "handoff_hash": role_receipt["handoff_hash"],
        "resource_service_descriptor_hash": card["resource_service"]["descriptor_hash"],
        "resource_service_catalog_ref": card["resource_service"]["catalog_ref"],
        "resource_service_catalog_hash": card["resource_service"]["catalog_hash"],
        "role_receipt_hash": role_hash,
    }
    return {**unsigned, "receipt_hash": _hash(unsigned)}


def verify_candidate_governed_execution_receipt(
    receipt: Mapping[str, Any], *, card: Mapping[str, Any], resource_receipt: Mapping[str, Any],
    role_receipt: Mapping[str, Any], resource_service_catalog: Mapping[str, Any],
    source_commit: str, source_tree: str, plan_commit: str,
) -> dict[str, Any]:
    """Verify a compact join against the immutable artifacts it names.

    A compact receipt contains hashes, not copied execution context.  It is
    therefore never sufficient by itself: callers must supply the exact card,
    resource/role receipts, and catalog retained by the receipt sink.
    """

    required = {
        "schema", "status", "source_commit", "source_tree", "plan_commit", "role",
        "selected_provider", "card_hash", "resource_receipt_hash", "harness_retrieval_attestation_hash",
        "harness_retrieval_attestation", "service_id", "service_client_id", "execution_identity", "handoff_hash",
        "resource_service_descriptor_hash",
        "resource_service_catalog_ref", "resource_service_catalog_hash", "role_receipt_hash", "receipt_hash",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != required or receipt.get("schema") != SCHEMA:
        raise GovernedExecutionReceiptError("candidate governed execution receipt schema is invalid")
    unsigned = dict(receipt)
    claimed = unsigned.pop("receipt_hash")
    if not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None or claimed != _hash(unsigned):
        raise GovernedExecutionReceiptError("candidate governed execution receipt hash is invalid")
    if receipt.get("status") != "PASS":
        raise GovernedExecutionReceiptError("candidate governed execution receipt is not passing")
    if (
        receipt.get("source_commit") != source_commit
        or receipt.get("source_tree") != source_tree
        or receipt.get("plan_commit") != plan_commit
    ):
        raise GovernedExecutionReceiptError("candidate governed execution receipt binding mismatch")
    for field in ("card_hash", "resource_receipt_hash", "harness_retrieval_attestation_hash", "role_receipt_hash"):
        if not isinstance(receipt.get(field), str) or _SHA256.fullmatch(receipt[field]) is None:
            raise GovernedExecutionReceiptError("candidate governed execution receipt artifact hash is invalid")
    attestation = receipt.get("harness_retrieval_attestation")
    if not isinstance(attestation, Mapping) or attestation.get("attestation_hash") != receipt["harness_retrieval_attestation_hash"]:
        raise GovernedExecutionReceiptError("candidate governed execution receipt attestation is invalid")
    try:
        attestation_key = resource_service_attestation_key(
            resource_service_catalog, str(receipt["service_id"]), str(receipt["service_client_id"]),
        )
        validate_harness_retrieval_attestation(
            attestation,
            expected={
                "service_id": receipt["service_id"], "card_hash": receipt["card_hash"],
                "client_id": receipt["service_client_id"],
                "role": receipt["role"], "execution_identity": receipt["execution_identity"],
                "handoff_hash": receipt["handoff_hash"],
                "resource_receipt_hash": receipt["resource_receipt_hash"],
            },
            **attestation_key,
        )
    except ResourceVerificationError as exc:
        raise GovernedExecutionReceiptError(f"candidate governed execution receipt attestation is invalid: {exc}") from exc
    expected_receipt = create_candidate_governed_execution_receipt(
        card=card,
        resource_receipt=resource_receipt,
        role_receipt=role_receipt,
        resource_service_catalog=resource_service_catalog,
        source_commit=source_commit,
        source_tree=source_tree,
        plan_commit=plan_commit,
    )
    if receipt != expected_receipt:
        raise GovernedExecutionReceiptError("candidate governed execution receipt artifacts do not match")
    return dict(receipt)
