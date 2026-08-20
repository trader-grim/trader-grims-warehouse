"""Closed validation and queue launch for harness-neutral W14 role cards."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

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
        "schema", "lifecycle", "source_commit", "freshness", "provider_registry_hash",
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
    return {
        "schema": parameters["schema"],
        "lifecycle": dict(lifecycle),
        "source_commit": source_commit,
        "freshness": dict(freshness),
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
