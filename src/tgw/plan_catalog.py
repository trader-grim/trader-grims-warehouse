"""Strict composition of Plan execution intent and provider-state catalogs.

Execution work units describe desired transitions.  They are never providers or
observations.  This adapter combines their declared capability identities with a
separate, explicit catalog whose objects carry implementation and evidence claims.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw.plan_solver import GRAPH_SCHEMA, ExecutionGraphAdapter, PlanResolutionError

CATALOG_SCHEMA = "tgw-plan-provider-catalog/v1"


def load_provider_catalog(path: str | Path) -> dict[str, Any]:
    """Load one catalog JSON object without consulting source-tree prose."""

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PlanResolutionError("provider catalog must be a JSON object")
    return value


def compose_catalog(
    execution: Mapping[str, Any],
    provider_catalog: Mapping[str, Any],
    *,
    plan_commit: str,
) -> dict[str, Any]:
    """Return a ``tgw-plan/v2`` graph from two independently typed inputs."""

    base = ExecutionGraphAdapter().adapt(execution, plan_commit=plan_commit)
    if provider_catalog.get("schema") != CATALOG_SCHEMA:
        raise PlanResolutionError(f"expected schema {CATALOG_SCHEMA!r}")
    if provider_catalog.get("plan_commit") != plan_commit:
        raise PlanResolutionError("provider catalog is not bound to the exact Plan commit")
    if provider_catalog.get("plan_id") != execution.get("plan_id"):
        raise PlanResolutionError("provider catalog plan_id does not match execution graph")

    raw_capabilities = provider_catalog.get("capabilities", ())
    raw_providers = provider_catalog.get("providers", ())
    raw_observations = provider_catalog.get("observations", ())
    for name, values in (
        ("capabilities", raw_capabilities),
        ("providers", raw_providers),
        ("observations", raw_observations),
    ):
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise PlanResolutionError(f"catalog {name} must be a sequence")

    capability_ids = {
        str(item["id"] if isinstance(item, Mapping) else item)
        for item in base["capabilities"]
    }
    for item in raw_capabilities:
        identity = item.get("id") if isinstance(item, Mapping) else item
        if not isinstance(identity, str) or not identity:
            raise PlanResolutionError("catalog capability IDs must be non-empty strings")
        capability_ids.add(identity)

    providers = [dict(item) for item in raw_providers if isinstance(item, Mapping)]
    if len(providers) != len(raw_providers):
        raise PlanResolutionError("catalog providers must be mappings")
    provider_ids = [item.get("id") for item in providers]
    if any(not isinstance(identity, str) or not identity for identity in provider_ids):
        raise PlanResolutionError("provider IDs must be non-empty strings")
    if len(set(provider_ids)) != len(provider_ids):
        raise PlanResolutionError("provider IDs must be unique")
    for provider in providers:
        provides = provider.get("provides")
        if not isinstance(provides, Sequence) or isinstance(provides, (str, bytes)) or not provides:
            raise PlanResolutionError(f"provider {provider['id']} must explicitly declare provides")
        undeclared = set(provides) - capability_ids
        if undeclared:
            raise PlanResolutionError(
                f"provider {provider['id']} references undeclared capabilities: {sorted(undeclared)}"
            )

    observations = [dict(item) for item in raw_observations if isinstance(item, Mapping)]
    if len(observations) != len(raw_observations):
        raise PlanResolutionError("catalog observations must be mappings")
    known_providers = set(provider_ids)
    for observation in observations:
        if observation.get("provider") not in known_providers:
            raise PlanResolutionError("observation references an unknown provider")
        if observation.get("capability") not in capability_ids:
            raise PlanResolutionError("observation references an unknown capability")
        if observation.get("capability") not in next(
            provider["provides"] for provider in providers if provider["id"] == observation["provider"]
        ):
            raise PlanResolutionError("observation capability is not provided by its provider")
        evidence = observation.get("evidence")
        if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)) or not evidence:
            raise PlanResolutionError("observations require explicit non-empty evidence")

    provided = {capability for provider in providers for capability in provider["provides"]}
    profile = base["target"]["profile"]
    profile_data = provider_catalog.get("profiles", {}).get(profile)
    if not isinstance(profile_data, Mapping) or not isinstance(
        profile_data.get("minimum_state"), str
    ):
        raise PlanResolutionError(f"provider catalog must declare target profile {profile!r}")
    base["target"]["minimum_state"] = profile_data["minimum_state"]
    base.update(
        {
            "schema": GRAPH_SCHEMA,
            "capabilities": [{"id": identity} for identity in sorted(capability_ids)],
            "providers": sorted(providers, key=lambda item: item["id"]),
            "observations": sorted(
                observations, key=lambda item: (item["capability"], item["provider"])
            ),
            "catalog_gaps": [
                item for item in base["catalog_gaps"] if item["capability"] not in provided
            ],
            "provider_catalog": {
                "schema": CATALOG_SCHEMA,
                "id": provider_catalog.get("id"),
            },
        }
    )
    return base
