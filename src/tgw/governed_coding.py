"""Provider-selected governed coding dispatch over compact Promptcraft cards."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.execution_resources import (
    HTTPRegisteredResourceResolver,
    ResourceResolver,
    ResourceVerificationError,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
    validate_harness_retrieval_attestation,
    verify_card_resource_service,
    verify_card_resources,
)
from tgw.harness_registry import (
    ProviderHealth,
    execution_card_provider_fields,
    select_provider,
)

RECEIPT_SCHEMA = "tgw-governed-coding-receipt/v1"
_ROLE_CONDITIONS = {
    "implementation": frozenset({"implemented", "tested", "linted"}),
    "independent-review": frozenset({"reviewed"}),
    "controller-verification": frozenset({"tested", "linted", "controller_verified"}),
}
_ROLE_REQUIRED = {
    "implementation": frozenset({"implemented"}),
    "independent-review": frozenset({"reviewed"}),
    "controller-verification": frozenset({"controller_verified"}),
}


class GovernedCodingError(ValueError):
    pass


Run = Callable[..., subprocess.CompletedProcess[str]]


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _card(template: Mapping[str, Any], provider_fields: Mapping[str, Any], role: str) -> dict[str, Any]:
    unsigned = {**dict(template), **provider_fields, "schema": "tgw-execution-card/v1", "role": role}
    unsigned.pop("card_hash", None)
    return {**unsigned, "card_hash": _hash(unsigned)}


def _promptcraft_path(adapters: Mapping[str, str | Path]) -> Path:
    if "promptcraft-card-handoff" in adapters:
        candidate = Path(adapters["promptcraft-card-handoff"])
    elif "promptcraft" in adapters:
        candidate = Path(adapters["promptcraft"]) / "bin/promptcraft-handoff"
    else:
        raise GovernedCodingError("Promptcraft card adapter is unavailable")
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise GovernedCodingError("Promptcraft card adapter is unavailable")
    return candidate.resolve()


def _promptcraft(
    executable: Path,
    operation: str,
    value: Mapping[str, Any],
    *,
    receiver_identity: str,
    run: Run,
) -> dict[str, Any]:
    command = [str(executable), operation]
    if operation == "craft":
        command.extend(["--receiver-identity", receiver_identity])
    completed = run(command, input=json.dumps(value), text=True, capture_output=True, check=False)
    if completed.returncode:
        raise GovernedCodingError(f"Promptcraft {operation} failed: {completed.stderr[-500:]}")
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise GovernedCodingError(f"Promptcraft {operation} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise GovernedCodingError(f"Promptcraft {operation} returned a non-object")
    return result


def _receipt(unsigned: Mapping[str, Any]) -> dict[str, Any]:
    return {**unsigned, "receipt_hash": _hash(unsigned)}


def dispatch_role(
    registry: Mapping[str, Any],
    health: Mapping[str, ProviderHealth],
    *,
    role: str,
    adapters: Mapping[str, str | Path],
    card_template: Mapping[str, Any],
    execution_identity: str,
    required_capabilities: Sequence[str] = (),
    independent_from: Sequence[str] = (),
    resource_resolver: ResourceResolver | None = None,
    resource_service: Mapping[str, Any] | None = None,
    resource_service_catalog: Mapping[str, Any] | None = None,
    run: Run = subprocess.run,
) -> dict[str, Any]:
    """Select, adapt, execute, and bind one role result to an immutable receipt."""

    if role not in _ROLE_CONDITIONS:
        raise GovernedCodingError(f"unsupported governed coding role: {role}")
    selection = select_provider(
        registry,
        health,
        role=role,
        adapters=adapters,
        required_capabilities=required_capabilities,
        independent_from=independent_from,
    )
    if selection["status"] != "SELECTED":
        return _receipt(
            {
                "schema": RECEIPT_SCHEMA,
                "status": "HOLD",
                "role": role,
                "selected_provider": None,
                "execution_identity": execution_identity,
                "card_hash": None,
                "promptcraft_receipt_hash": None,
                "resource_receipt_hash": None,
                "outcome": "unavailable",
                "established_conditions": [],
                "artifacts": [{"kind": "provider_selection", "considered": selection["considered"]}],
            }
        )
    provider_fields = execution_card_provider_fields(selection)
    card = _card(card_template, provider_fields, role)
    try:
        if resource_resolver is None or resource_service is None or resource_service_catalog is None:
            raise ResourceVerificationError("registered resource resolver is unavailable")
        verified_service, verified_catalog = verify_card_resource_service(
            card, resource_service, resource_service_catalog,
        )
        resource_receipt = verify_card_resources(card, resource_resolver)
    except ResourceVerificationError as exc:
        return _receipt(
            {
                "schema": RECEIPT_SCHEMA,
                "status": "HOLD",
                "role": role,
                "selected_provider": selection["selected_provider"],
                "execution_identity": execution_identity,
                "card_hash": card["card_hash"],
                "promptcraft_receipt_hash": None,
                "resource_receipt_hash": None,
                "outcome": "resource-verification",
                "established_conditions": [],
                "artifacts": [{"kind": "resource_verification", "detail": str(exc)}],
            }
        )
    promptcraft = _promptcraft_path(adapters)
    handoff = _promptcraft(
        promptcraft,
        "craft",
        {
            "card": card,
            "resource_receipt": resource_receipt,
            # This is a small service descriptor, not copied execution context.
            # The selected harness receives it and must retrieve the card-bound
            # sources itself before it may claim a passing role result.
            "resource_service": verified_service,
        },
        receiver_identity=execution_identity,
        run=run,
    )
    invocation = _promptcraft(promptcraft, "verify", handoff, receiver_identity=execution_identity, run=run)
    if invocation.get("selected_provider") != selection["selected_provider"]:
        raise GovernedCodingError("Promptcraft invocation provider does not match selection")
    runner = run(
        selection["runner_argv"],
        input=json.dumps(handoff),
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "TGW_EXECUTION_HANDOFF_HASH": str(handoff["handoff_hash"]),
            "TGW_EXECUTION_CARD_HASH": str(card["card_hash"]),
            "TGW_EXECUTION_RESOURCE_RECEIPT_HASH": str(resource_receipt["receipt_hash"]),
        },
    )
    artifacts: list[Any] = []
    outcome = "failed"
    established: list[str] = []
    harness_resource_receipt_hash: str | None = None
    harness_retrieval_attestation: Mapping[str, Any] | None = None
    if runner.returncode:
        artifacts.append({"kind": "runner_failure", "detail": runner.stderr[-500:]})
    else:
        try:
            result = json.loads(runner.stdout)
        except json.JSONDecodeError:
            result = None
        if isinstance(result, Mapping):
            outcome = str(result.get("outcome", "failed"))
            raw_established = result.get("established_conditions", [])
            raw_artifacts = result.get("artifacts", [])
            raw_resource_hash = result.get("resource_receipt_hash")
            raw_attestation = result.get("resource_retrieval_attestation")
            if isinstance(raw_established, list) and all(isinstance(item, str) for item in raw_established):
                established = raw_established
            if isinstance(raw_artifacts, list):
                artifacts = raw_artifacts
            if isinstance(raw_resource_hash, str):
                harness_resource_receipt_hash = raw_resource_hash
            if isinstance(raw_attestation, Mapping):
                harness_retrieval_attestation = raw_attestation
    allowed = _ROLE_CONDITIONS[role]
    attestation_hash: str | None = None
    attestation_error: str | None = None
    if not isinstance(resource_resolver, HTTPRegisteredResourceResolver):
        attestation_error = "registered resource resolver cannot verify harness retrieval attestation"
    elif harness_retrieval_attestation is None:
        attestation_error = "runner did not return a service-issued retrieval attestation"
    else:
        try:
                verified_attestation = resource_resolver.verify_harness_retrieval_attestation(
                harness_retrieval_attestation,
                card_hash=card["card_hash"], role=role,
                execution_identity=execution_identity, handoff_hash=handoff["handoff_hash"],
                resource_receipt_hash=resource_receipt["receipt_hash"], resources=card["bindings"],
                )
                attestation_hash = str(verified_attestation["attestation_hash"])
                harness_retrieval_attestation = verified_attestation
        except ResourceVerificationError as exc:
            attestation_error = str(exc)
    valid_success = (
        outcome == "satisfied"
        and _ROLE_REQUIRED[role] <= set(established)
        and set(established) <= allowed
        and harness_resource_receipt_hash == resource_receipt["receipt_hash"]
        and attestation_hash is not None
    )
    if not valid_success:
        established = []
        if outcome == "satisfied":
            outcome = "failed"
            detail = "runner claimed conditions outside role authority"
            if harness_resource_receipt_hash != resource_receipt["receipt_hash"]:
                detail = "runner did not prove retrieval of the card-bound resource receipt"
            elif attestation_error is not None:
                detail = attestation_error
            artifacts.append({"kind": "contract_failure", "detail": detail})
    return _receipt(
        {
            "schema": RECEIPT_SCHEMA,
            "status": "PASS" if valid_success else "FAIL",
            "role": role,
            "selected_provider": selection["selected_provider"],
            "execution_identity": execution_identity,
            "card_hash": card["card_hash"],
            "promptcraft_receipt_hash": handoff["receipt"]["receipt_hash"],
            "handoff_hash": handoff["handoff_hash"],
            "resource_receipt_hash": resource_receipt["receipt_hash"],
            "harness_resource_receipt_hash": harness_resource_receipt_hash,
            "harness_retrieval_attestation_hash": attestation_hash,
            "harness_retrieval_attestation": harness_retrieval_attestation,
            "resource_service_descriptor_hash": resource_service_descriptor_hash(verified_service),
            "resource_service_catalog_ref": verified_catalog["catalog_ref"],
            "resource_service_catalog_hash": resource_service_catalog_hash(verified_catalog),
            "outcome": outcome,
            "established_conditions": established,
            "artifacts": artifacts,
        }
    )


def validate_receipt(receipt: Mapping[str, Any]) -> None:
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise GovernedCodingError("invalid governed coding receipt schema")
    unsigned = dict(receipt)
    claimed = unsigned.pop("receipt_hash", None)
    if claimed != _hash(unsigned):
        raise GovernedCodingError("governed coding receipt hash mismatch")
    if receipt.get("role") not in _ROLE_CONDITIONS or receipt.get("status") not in {
        "PASS",
        "FAIL",
        "HOLD",
    }:
        raise GovernedCodingError("governed coding receipt contract is invalid")
    attestation = receipt.get("harness_retrieval_attestation")
    if receipt.get("status") == "PASS" and (
        not isinstance(receipt.get("selected_provider"), str)
        or not isinstance(receipt.get("card_hash"), str)
        or not isinstance(receipt.get("promptcraft_receipt_hash"), str)
        or not isinstance(receipt.get("handoff_hash"), str)
        or not isinstance(receipt.get("resource_receipt_hash"), str)
        or receipt.get("harness_resource_receipt_hash") != receipt.get("resource_receipt_hash")
        or not isinstance(receipt.get("resource_service_descriptor_hash"), str)
        or not isinstance(receipt.get("resource_service_catalog_ref"), str)
        or not isinstance(receipt.get("resource_service_catalog_hash"), str)
        or not isinstance(receipt.get("harness_retrieval_attestation_hash"), str)
        or not isinstance(attestation, Mapping)
        or attestation.get("attestation_hash") != receipt.get("harness_retrieval_attestation_hash")
        or not _ROLE_REQUIRED[str(receipt["role"])]
        <= set(receipt.get("established_conditions", ()))
    ):
        raise GovernedCodingError("passing governed coding receipt lacks role evidence")
    if receipt.get("status") == "PASS":
        try:
            validate_harness_retrieval_attestation(
                attestation,
                expected={
                    "card_hash": receipt["card_hash"], "role": receipt["role"],
                    "execution_identity": receipt["execution_identity"],
                    "handoff_hash": receipt["handoff_hash"],
                    "resource_receipt_hash": receipt["resource_receipt_hash"],
                },
            )
        except ResourceVerificationError as exc:
            raise GovernedCodingError(f"passing governed coding receipt attestation is invalid: {exc}") from exc


def admission_gate(receipts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Require successful implementation, independent review, and verification."""

    by_role: dict[str, Mapping[str, Any]] = {}
    reasons: list[str] = []
    for receipt in receipts:
        validate_receipt(receipt)
        role = str(receipt.get("role"))
        if role in by_role:
            reasons.append(f"duplicate-role:{role}")
        by_role[role] = receipt
    for role in _ROLE_CONDITIONS:
        receipt = by_role.get(role)
        if receipt is None:
            reasons.append(f"missing-role:{role}")
        elif receipt.get("status") != "PASS":
            reasons.append(f"failed-role:{role}")
    # Vendors/providers may repeat, but the three mandatory roles must be
    # independently bound executions. A controller sharing the implementation
    # or review context is not independent verification.
    contexts: dict[str, list[str]] = {}
    for role, receipt in by_role.items():
        identity = receipt.get("execution_identity")
        if isinstance(identity, str) and identity:
            contexts.setdefault(identity, []).append(role)
    for roles in contexts.values():
        if len(roles) > 1:
            reasons.append("shared-execution-context:" + ",".join(sorted(roles)))
    return {
        "schema": "tgw-coding-admission-gate/v1",
        "allowed": not reasons,
        "reasons": sorted(set(reasons)),
        "receipt_hashes": sorted(str(receipt["receipt_hash"]) for receipt in receipts),
    }
