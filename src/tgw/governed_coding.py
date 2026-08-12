"""Provider-selected governed coding dispatch over compact Promptcraft cards."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

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
                "outcome": "unavailable",
                "established_conditions": [],
                "artifacts": [{"kind": "provider_selection", "considered": selection["considered"]}],
            }
        )
    provider_fields = execution_card_provider_fields(selection)
    card = _card(card_template, provider_fields, role)
    promptcraft = _promptcraft_path(adapters)
    handoff = _promptcraft(promptcraft, "craft", card, receiver_identity=execution_identity, run=run)
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
        },
    )
    artifacts: list[Any] = []
    outcome = "failed"
    established: list[str] = []
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
            if isinstance(raw_established, list) and all(isinstance(item, str) for item in raw_established):
                established = raw_established
            if isinstance(raw_artifacts, list):
                artifacts = raw_artifacts
    allowed = _ROLE_CONDITIONS[role]
    valid_success = (
        outcome == "satisfied"
        and _ROLE_REQUIRED[role] <= set(established)
        and set(established) <= allowed
    )
    if not valid_success:
        established = []
        if outcome == "satisfied":
            outcome = "failed"
            artifacts.append({"kind": "contract_failure", "detail": "runner claimed conditions outside role authority"})
    return _receipt(
        {
            "schema": RECEIPT_SCHEMA,
            "status": "PASS" if valid_success else "FAIL",
            "role": role,
            "selected_provider": selection["selected_provider"],
            "execution_identity": execution_identity,
            "card_hash": card["card_hash"],
            "promptcraft_receipt_hash": handoff["receipt"]["receipt_hash"],
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
    if receipt.get("status") == "PASS" and (
        not isinstance(receipt.get("selected_provider"), str)
        or not isinstance(receipt.get("card_hash"), str)
        or not isinstance(receipt.get("promptcraft_receipt_hash"), str)
        or not _ROLE_REQUIRED[str(receipt["role"])]
        <= set(receipt.get("established_conditions", ()))
    ):
        raise GovernedCodingError("passing governed coding receipt lacks role evidence")


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
