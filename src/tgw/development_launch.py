"""Closed validation and queue launch for harness-neutral W14 role cards."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from tgw.execution_resources import CARD_RESOURCE_NAMES

_HASH = re.compile(r"sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"[0-9a-f]{40}$")
_ROLES = frozenset({"implementation", "independent-review", "controller-verification"})


class DevelopmentLaunchError(ValueError):
    pass


def _hash(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_development_launch(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an immutable lifecycle without selecting a harness."""
    if not isinstance(parameters, Mapping) or set(parameters) != {
        "schema", "lifecycle", "source_commit", "freshness", "recovery_status",
        "provider_registry_hash",
    }:
        raise DevelopmentLaunchError("development launch parameters are not exact")
    if parameters.get("schema") != "tgw-development-launch/v1":
        raise DevelopmentLaunchError("development launch schema is invalid")
    source_commit = parameters.get("source_commit")
    registry_hash = parameters.get("provider_registry_hash")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise DevelopmentLaunchError("development launch source commit is invalid")
    if not isinstance(registry_hash, str) or _HASH.fullmatch(registry_hash) is None:
        raise DevelopmentLaunchError("development launch provider registry hash is invalid")
    freshness = parameters.get("freshness")
    if not isinstance(freshness, Mapping) or freshness.get("status") != "FRESH":
        raise DevelopmentLaunchError("development launch is held by stale projections")
    freshness_unsigned = dict(freshness)
    freshness_hash = freshness_unsigned.pop("receipt_hash", None)
    if freshness_hash != _hash(freshness_unsigned):
        raise DevelopmentLaunchError("development launch freshness receipt hash is invalid")
    recovery_status = parameters.get("recovery_status")
    if (
        not isinstance(recovery_status, Mapping)
        or recovery_status.get("schema") != "tgw-w18-fleet-transition-gate/v1"
        or recovery_status.get("status") != "ACTIVE"
    ):
        raise DevelopmentLaunchError("development launch is held by platform recovery")
    lifecycle = parameters.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        raise DevelopmentLaunchError("development lifecycle is invalid")
    lifecycle_unsigned = dict(lifecycle)
    lifecycle_hash = lifecycle_unsigned.pop("lifecycle_hash", None)
    if lifecycle_hash != _hash(lifecycle_unsigned):
        raise DevelopmentLaunchError("development lifecycle hash is invalid")
    resolution = lifecycle.get("resolution")
    cards = lifecycle.get("launch_cards")
    if not isinstance(resolution, Mapping) or resolution.get("status") != "RESOLVED" or not isinstance(cards, list) or not cards:
        raise DevelopmentLaunchError("development lifecycle has no resolved launch closure")
    seen: set[str] = set()
    execution_identities: set[str] = set()
    for card in cards:
        if not isinstance(card, Mapping) or card.get("state") != "PREPARED" or card.get("activation") != "declarative-only":
            raise DevelopmentLaunchError("development launch card is not prepared")
        if card.get("role") not in _ROLES:
            raise DevelopmentLaunchError("development launch card role is not harness-neutral")
        key = card.get("idempotency_key")
        if not isinstance(key, str) or _HASH.fullmatch(key) is None or key in seen:
            raise DevelopmentLaunchError("development launch card idempotency binding is invalid")
        seen.add(key)
        selection = card.get("provider_selection")
        if (
            not isinstance(selection, Mapping)
            or set(selection) != {"mode", "registry_id", "registry_hash", "selected_provider"}
            or selection.get("mode") != "launch-time-qualified-provider"
            or selection.get("registry_hash") != registry_hash
            or selection.get("selected_provider") is not None
        ):
            raise DevelopmentLaunchError("development launch card bypasses qualified provider selection")
        execution_identity = card.get("execution_identity")
        if not isinstance(execution_identity, str) or not execution_identity:
            raise DevelopmentLaunchError("development launch card execution identity is invalid")
        if execution_identity in execution_identities:
            raise DevelopmentLaunchError("mandatory development cards share an execution identity")
        execution_identities.add(execution_identity)
        template = card.get("execution_card_template")
        if not isinstance(template, Mapping) or set(template) != {
            "card_id", "solution_id", "plan_commit", "resource_service", "bindings",
            "authority", "exclusions", "acceptance", "lease",
        }:
            raise DevelopmentLaunchError("development execution-card template is invalid")
        if template.get("card_id") != key or template.get("solution_id") != card.get("plan", {}).get("solution_hash") or template.get("plan_commit") != card.get("plan", {}).get("commit"):
            raise DevelopmentLaunchError("development execution-card Plan binding is invalid")
        bindings = template.get("bindings")
        if not isinstance(bindings, Mapping) or set(bindings) != CARD_RESOURCE_NAMES:
            raise DevelopmentLaunchError("development execution-card resources are incomplete")
        for binding in bindings.values():
            if (
                not isinstance(binding, Mapping)
                or set(binding) != {"ref", "hash"}
                or not isinstance(binding.get("ref"), str)
                or not binding["ref"]
                or not isinstance(binding.get("hash"), str)
                or _HASH.fullmatch(binding["hash"]) is None
            ):
                raise DevelopmentLaunchError("development execution-card resource binding is invalid")
        if bindings["plan_graph"]["ref"] == bindings["codegraph_snapshot"]["ref"]:
            raise DevelopmentLaunchError("Plan Graph and CodeGraph bindings must remain distinct")
        for field in ("authority", "exclusions", "acceptance"):
            if not isinstance(template.get(field), list) or not all(isinstance(item, str) and item for item in template[field]):
                raise DevelopmentLaunchError(f"development execution-card {field} is invalid")
        service = template.get("resource_service")
        if not isinstance(service, Mapping) or set(service) != {"id", "client_id", "descriptor_hash", "catalog_ref", "catalog_hash"}:
            raise DevelopmentLaunchError("development execution-card resource service is invalid")
        lease = template.get("lease")
        if not isinstance(lease, Mapping) or set(lease) != {"id", "expires_at", "stop_policy"} or lease.get("id") != key:
            raise DevelopmentLaunchError("development execution-card lease is invalid")
    return {
        "schema": parameters["schema"],
        "lifecycle": dict(lifecycle),
        "source_commit": source_commit,
        "freshness": dict(freshness),
        "recovery_status": dict(recovery_status),
        "provider_registry_hash": registry_hash,
    }


def enqueue_development_launch(config: Mapping[str, Any], parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Launch the exact card sequence through the cross-host coding queue."""
    from tgw.coding_provision import create_development_request

    raw = dict(parameters)
    generation = raw.pop("generation", None)
    validated = validate_development_launch(raw)
    if generation != validated["lifecycle"]["lifecycle_hash"]:
        raise DevelopmentLaunchError("development launch provider generation mismatch")
    request = create_development_request(dict(config), launch=validated)
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise DevelopmentLaunchError("development launch queue returned no request identity")
    evidence = {
        "schema": "tgw-development-launch-enqueue-receipt/v1",
        "lifecycle_hash": validated["lifecycle"]["lifecycle_hash"],
        "queue_request_id": request_id,
        "source_commit": validated["source_commit"],
        "provider_registry_hash": validated["provider_registry_hash"],
    }
    return {
        "evidence": ["development-launch:" + _hash(evidence)],
        "queue_request_id": request_id,
    }
