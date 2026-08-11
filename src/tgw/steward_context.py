"""Read-only, cited current-context answers for the clean TGW Steward."""

from __future__ import annotations

from typing import Any, Mapping

from tgw.agent_contract import validate_agent_contract
from tgw.environment_registry import (
    EnvironmentRegistryError,
    RetiredHostError,
    resolve_host,
    validate_registry,
)


class StewardContextError(ValueError):
    """A steward query is unsupported, ambiguous, or outside its authority."""


_QUERY_KEYS = {"schema", "kind", "identity"}
_KINDS = {"host", "repository", "authority", "historical-reference"}


def _query(raw: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != _QUERY_KEYS:
        raise StewardContextError("steward query fields are not exact")
    if raw.get("schema") != "tgw-steward-query/v1" or raw.get("kind") not in _KINDS:
        raise StewardContextError("steward query schema or kind is unsupported")
    identity = raw.get("identity")
    if not isinstance(identity, str) or not identity.strip() or identity != identity.strip():
        raise StewardContextError("steward query identity is invalid")
    return {"schema": raw["schema"], "kind": raw["kind"], "identity": identity}


def answer_steward_query(
    registry_raw: Mapping[str, Any],
    contract_raw: Mapping[str, Any],
    query_raw: Mapping[str, Any],
) -> dict[str, Any]:
    """Answer one structured query without history lookup or external effects."""
    registry = validate_registry(registry_raw)
    contract = validate_agent_contract(contract_raw)
    query = _query(query_raw)
    kind, identity = query["kind"], query["identity"]
    base = {
        "schema": "tgw-steward-answer/v1",
        "actor_id": contract["actor_id"],
        "contract_version": contract["version"],
        "registry_revision": registry["revision"],
        "query": query,
        "effects_permitted": False,
        "history_grants_authority": False,
        "satellite_runtime_dependency": False,
    }

    if kind == "host":
        try:
            host = resolve_host(registry, identity)
        except RetiredHostError as exc:
            return {
                **base,
                "result": "refused-retired",
                "classification": "historical-only",
                "facts": {},
                "citations": ["config/environment/registry.yaml#content.retired_hosts"],
                "reason": str(exc),
            }
        except EnvironmentRegistryError as exc:
            return {
                **base,
                "result": "unknown",
                "classification": "unregistered",
                "facts": {},
                "citations": ["config/environment/registry.yaml#content.unknowns"],
                "reason": str(exc),
            }
        return {
            **base,
            "result": "current",
            "classification": "current-registry-fact",
            "facts": {
                "host_role": host["host_role"],
                "canonical_name": host["canonical_name"],
                "roles": host["roles"],
                "verified_at": host["verified_at"],
            },
            "citations": [
                "config/environment/registry.yaml#content.hosts",
                *host["sources"],
            ],
            "reason": "resolved from the validated current environment registry",
        }

    if kind == "repository":
        repositories = registry["content"]["repositories"]
        if identity not in repositories:
            return {
                **base,
                "result": "unknown",
                "classification": "unregistered",
                "facts": {},
                "citations": ["config/environment/registry.yaml#content.repositories"],
                "reason": f"unknown repository identity: {identity}",
            }
        repository = repositories[identity]
        return {
            **base,
            "result": "current",
            "classification": "current-registry-fact",
            "facts": {
                "repository_id": identity,
                "host_role": repository["host_role"],
                "path": repository["path"],
                "branch": repository["branch"],
                "dirty_policy": repository["dirty_policy"],
            },
            "citations": [
                "config/environment/registry.yaml#content.repositories",
                *repository["sources"],
            ],
            "reason": "resolved from the validated current environment registry",
        }

    if kind == "authority":
        if identity not in {"tgw-steward", "hermes-tigwa", "Hermes"}:
            raise StewardContextError("authority query is outside the TGW Steward identity")
        return {
            **base,
            "result": "current",
            "classification": "current-agent-contract",
            "facts": {
                "display_name": contract["display_name"],
                "current_fact_sources": contract["authority"]["current_fact_sources"],
                "effects": contract["effects"],
                "modes": sorted(contract["modes"]),
                "persona_is_style_only": contract["persona_is_style_only"],
            },
            "citations": [
                "config/environment/actors/tgw-steward.json",
                "config/environment/registry.yaml#content.agents.hermes-tigwa",
            ],
            "reason": "resolved from the validated clean agent contract",
        }

    return {
        **base,
        "result": "historical-only",
        "classification": "explicit-cited-reference-only",
        "facts": {"reference_id": identity, "current_authority": False},
        "citations": [
            "config/environment/actors/tgw-steward.json#authority",
            "config/environment/registry.yaml#content.instructions",
        ],
        "reason": "historical references may inform review but cannot establish current facts or authority",
    }
