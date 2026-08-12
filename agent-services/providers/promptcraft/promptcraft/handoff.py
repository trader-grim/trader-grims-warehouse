"""Hash-bound execution-card adaptation for mechanical launcher handoff."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .core import harness_profile

CARD_SCHEMA = "tgw-execution-card/v1"
RECEIPT_SCHEMA = "tgw-promptcraft-receipt/v1"
HANDOFF_SCHEMA = "tgw-launcher-handoff/v1"


class HandoffError(ValueError):
    """A bound object is incomplete, stale, or has been changed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _verify_hash(value: Mapping[str, Any], field: str) -> None:
    unsigned = dict(value)
    claimed = unsigned.pop(field, None)
    if claimed != _hash(unsigned):
        raise HandoffError(f"{field} mismatch")


@dataclass(frozen=True)
class ExecutionCard:
    _canonical_json: str

    @property
    def value(self) -> dict[str, Any]:
        return json.loads(self._canonical_json)

    @classmethod
    def create(cls, value: Mapping[str, Any]) -> "ExecutionCard":
        unsigned = dict(value)
        unsigned["schema"] = CARD_SCHEMA
        unsigned.pop("card_hash", None)
        unsigned["card_hash"] = _hash(unsigned)
        return cls.from_mapping(unsigned)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutionCard":
        if value.get("schema") != CARD_SCHEMA:
            raise HandoffError(f"expected {CARD_SCHEMA}")
        required = {
            "card_id",
            "solution_id",
            "role",
            "selected_provider",
            "plan_commit",
            "bindings",
            "authority",
            "exclusions",
            "acceptance",
            "receiver_profile",
            "receipt_sink",
            "lease",
            "card_hash",
        }
        missing = sorted(required - set(value))
        if missing:
            raise HandoffError("missing card fields: " + ", ".join(missing))
        for field in (
            "card_id",
            "solution_id",
            "role",
            "selected_provider",
            "plan_commit",
            "receipt_sink",
        ):
            if not isinstance(value[field], str) or not value[field]:
                raise HandoffError(f"{field} must be a non-empty string")
        bindings = value["bindings"]
        required_bindings = {
            "plan_input",
            "plan_graph",
            "codegraph_snapshot",
            "source_tree",
            "execution_environment",
            "authority_conditions",
        }
        if not isinstance(bindings, Mapping) or set(bindings) != required_bindings:
            raise HandoffError("card bindings must contain the exact required resource set")
        for name, binding in bindings.items():
            if not isinstance(binding, Mapping) or set(binding) != {"ref", "hash"}:
                raise HandoffError(f"binding {name} must contain only ref and hash")
            if not all(isinstance(binding[key], str) and binding[key] for key in ("ref", "hash")):
                raise HandoffError(f"binding {name} values must be non-empty strings")
        for field in ("authority", "exclusions", "acceptance"):
            if not isinstance(value[field], list) or not all(isinstance(item, str) for item in value[field]):
                raise HandoffError(f"{field} must be a string list")
        profile = value["receiver_profile"]
        if not isinstance(profile, Mapping) or set(profile) != {"id", "version"}:
            raise HandoffError("receiver_profile must contain only id and version")
        if not isinstance(profile["version"], int) or profile["version"] < 1:
            raise HandoffError("receiver profile version must be positive")
        lease = value["lease"]
        if not isinstance(lease, Mapping) or set(lease) != {"id", "expires_at", "stop_policy"}:
            raise HandoffError("lease must bind id, expires_at, and stop_policy")
        if not all(isinstance(lease[field], str) and lease[field] for field in lease):
            raise HandoffError("lease values must be non-empty strings")
        _verify_hash(value, "card_hash")
        return cls(_canonical(value).decode())

    @property
    def hash(self) -> str:
        return str(self.value["card_hash"])


def craft_handoff(card_value: Mapping[str, Any], *, receiver_identity: str) -> dict[str, Any]:
    """Adapt a verified card and return one immutable mechanical handoff."""

    if not receiver_identity:
        raise HandoffError("receiver identity is required")
    card = ExecutionCard.from_mapping(card_value)
    profile_id = str(card.value["receiver_profile"]["id"])
    profile = harness_profile(profile_id)
    bindings = card.value["bindings"]
    instruction = "\n".join(
        [
            f"Execution card: {card.value['card_id']} ({card.hash})",
            f"Role: {card.value['role']}",
            f"Selected provider: {card.value['selected_provider']}",
            f"Plan commit: {card.value['plan_commit']}",
            "Retrieve and verify these authoritative resources:",
            *[
                f"- {name}: {binding['ref']} ({binding['hash']})"
                for name, binding in sorted(bindings.items())
            ],
            "Receiver-native strategy:",
            *[f"- {item}" for item in profile["required_strategy"]],
            "Authority (exact; do not broaden):",
            *[f"- {item}" for item in card.value["authority"]],
            "Exclusions (exact; do not omit):",
            *[f"- {item}" for item in card.value["exclusions"]],
            "Acceptance (exact; do not weaken):",
            *[f"- {item}" for item in card.value["acceptance"]],
            f"Receipt sink: {card.value['receipt_sink']}",
            f"Lease: {json.dumps(card.value['lease'], sort_keys=True, separators=(',', ':'))}",
        ]
    ) + "\n"
    receipt_unsigned = {
        "schema": RECEIPT_SCHEMA,
        "card_hash": card.hash,
        "resource_hashes": {
            name: binding["hash"] for name, binding in sorted(bindings.items())
        },
        "profile": {
            "id": profile_id,
            "version": card.value["receiver_profile"]["version"],
        },
        "rendered_instruction_hash": "sha256:" + hashlib.sha256(instruction.encode()).hexdigest(),
        "receiver_identity": receiver_identity,
        "intent_guard_hash": _hash(
            {
                "authority": card.value["authority"],
                "exclusions": card.value["exclusions"],
                "acceptance": card.value["acceptance"],
            }
        ),
        "result": "READY",
    }
    receipt = {**receipt_unsigned, "receipt_hash": _hash(receipt_unsigned)}
    handoff_unsigned = {
        "schema": HANDOFF_SCHEMA,
        "card": card.value,
        "instruction": instruction,
        "receipt": receipt,
    }
    return {**handoff_unsigned, "handoff_hash": _hash(handoff_unsigned)}


def verify_for_launcher(
    handoff: Mapping[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Verify every binding and return the minimal launcher invocation object."""

    if handoff.get("schema") != HANDOFF_SCHEMA:
        raise HandoffError(f"expected {HANDOFF_SCHEMA}")
    _verify_hash(handoff, "handoff_hash")
    card = ExecutionCard.from_mapping(handoff["card"])
    receipt = handoff.get("receipt")
    if not isinstance(receipt, Mapping) or receipt.get("schema") != RECEIPT_SCHEMA:
        raise HandoffError("invalid Promptcraft receipt")
    _verify_hash(receipt, "receipt_hash")
    if receipt["card_hash"] != card.hash:
        raise HandoffError("receipt card hash mismatch")
    expected_resources = {
        name: binding["hash"] for name, binding in sorted(card.value["bindings"].items())
    }
    if receipt["resource_hashes"] != expected_resources:
        raise HandoffError("receipt resource hashes mismatch")
    instruction = handoff.get("instruction")
    if not isinstance(instruction, str) or receipt["rendered_instruction_hash"] != "sha256:" + hashlib.sha256(instruction.encode()).hexdigest():
        raise HandoffError("rendered instruction hash mismatch")
    if receipt["intent_guard_hash"] != _hash(
        {
            "authority": card.value["authority"],
            "exclusions": card.value["exclusions"],
            "acceptance": card.value["acceptance"],
        }
    ):
        raise HandoffError("authority/acceptance guard mismatch")
    if receipt["profile"] != card.value["receiver_profile"]:
        raise HandoffError("receiver profile mismatch")
    try:
        expires_at = datetime.fromisoformat(
            card.value["lease"]["expires_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise HandoffError("lease expiry is not an ISO-8601 timestamp") from exc
    if expires_at.tzinfo is None:
        raise HandoffError("lease expiry must include a timezone")
    if expires_at <= (now or datetime.now(timezone.utc)):
        raise HandoffError("execution card lease has expired")
    return {
        "schema": "tgw-launcher-invocation/v1",
        "card_hash": card.hash,
        "role": card.value["role"],
        "selected_provider": card.value["selected_provider"],
        "receiver_identity": receipt["receiver_identity"],
        "instruction": instruction,
        "receipt_sink": card.value["receipt_sink"],
        "lease": card.value["lease"],
    }
