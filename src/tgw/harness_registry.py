"""Harness-neutral role-provider registry and deterministic health selection."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REGISTRY_SCHEMA = "tgw-harness-provider-registry/v1"
SELECTION_SCHEMA = "tgw-harness-provider-selection/v1"


class ProviderRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    available: bool
    reason: str
    runner_argv: tuple[str, ...] = ()


def load_registry(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != REGISTRY_SCHEMA:
        raise ProviderRegistryError(f"expected {REGISTRY_SCHEMA}")
    providers = value.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ProviderRegistryError("registry providers must be a non-empty list")
    ids = [item.get("id") for item in providers if isinstance(item, dict)]
    if len(ids) != len(providers) or any(not isinstance(item, str) or not item for item in ids):
        raise ProviderRegistryError("provider IDs must be non-empty strings")
    if len(set(ids)) != len(ids):
        raise ProviderRegistryError("provider IDs must be unique")
    return value


def _command_health(command: Any) -> tuple[bool, str, tuple[str, ...]]:
    if not isinstance(command, Sequence) or isinstance(command, (str, bytes)) or not command:
        return False, "configured runner argv is missing", ()
    if not all(isinstance(item, str) and item for item in command):
        return False, "configured runner argv is invalid", ()
    executable = command[0]
    resolved = (
        str(Path(executable).absolute())
        if Path(executable).is_absolute() and Path(executable).is_file() and os.access(executable, os.X_OK)
        else shutil.which(executable)
    )
    if not resolved:
        return False, f"runner executable is unavailable: {executable}", ()
    return True, "configured runner executable is available", (resolved, *command[1:])


def observe_health(
    registry: Mapping[str, Any],
    *,
    coding_config: Mapping[str, Any],
    adapters: Mapping[str, str | Path],
) -> dict[str, ProviderHealth]:
    """Observe runners and adapter bindings without executing a provider."""

    commands = coding_config.get("commands", {})
    if not isinstance(commands, Mapping):
        commands = {}
    observed: dict[str, ProviderHealth] = {}
    for provider in registry["providers"]:
        runner = provider.get("runner", {})
        if runner.get("kind") == "unavailable":
            observed[provider["id"]] = ProviderHealth(
                provider["id"], False, str(runner.get("reason", "provider declared unavailable"))
            )
            continue
        if runner.get("kind") != "configured-argv" or not isinstance(runner.get("key"), str):
            raise ProviderRegistryError(f"provider {provider['id']} has an invalid runner object")
        available, reason, argv = _command_health(commands.get(runner["key"]))
        missing_adapters = [
            name
            for name in provider.get("adapter_requirements", ())
            if name not in adapters or not Path(adapters[name]).exists()
        ]
        if missing_adapters:
            available = False
            reason = "missing canonical adapters: " + ", ".join(sorted(missing_adapters))
            argv = ()
        observed[provider["id"]] = ProviderHealth(provider["id"], available, reason, argv)
    return observed


def select_provider(
    registry: Mapping[str, Any],
    health: Mapping[str, ProviderHealth],
    *,
    role: str,
    adapters: Mapping[str, str | Path],
    required_capabilities: Sequence[str] = (),
    independent_from: Sequence[str] = (),
) -> dict[str, Any]:
    """Select the best healthy qualified provider after all constraints apply."""

    required = set(required_capabilities)
    excluded = set(independent_from)
    considered: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for provider in registry["providers"]:
        provider_health = health.get(provider["id"])
        reasons: list[str] = []
        if role not in provider.get("qualified_roles", ()):
            reasons.append("role-not-qualified")
        missing = required - set(provider.get("capabilities", ()))
        if missing:
            reasons.append("missing-capabilities:" + ",".join(sorted(missing)))
        if provider["id"] in excluded:
            reasons.append("independence-exclusion")
        if provider_health is None or not provider_health.available:
            reasons.append("unavailable:" + (provider_health.reason if provider_health else "health-not-observed"))
        considered.append({"provider": provider["id"], "reasons": reasons or ["eligible"]})
        if not reasons:
            candidates.append(provider)
    if not candidates:
        return {
            "schema": SELECTION_SCHEMA,
            "role": role,
            "status": "UNAVAILABLE",
            "selected_provider": None,
            "considered": considered,
        }
    winner = sorted(candidates, key=lambda item: (-int(item.get("preference", 0)), item["id"]))[0]
    provider_health = health[winner["id"]]
    return {
        "schema": SELECTION_SCHEMA,
        "role": role,
        "status": "SELECTED",
        "selected_provider": winner["id"],
        "receiver_profile": winner["receiver_profile"],
        "runner_argv": list(provider_health.runner_argv),
        "adapter_bindings": {
            name: str(Path(path).resolve())
            for name, path in sorted(adapters.items())
            if name in winner.get("adapter_requirements", ())
        },
        "considered": considered,
    }


def execution_card_provider_fields(selection: Mapping[str, Any]) -> dict[str, Any]:
    """Project a successful selection into the two provider-neutral card fields."""

    if selection.get("schema") != SELECTION_SCHEMA or selection.get("status") != "SELECTED":
        raise ProviderRegistryError("cannot bind an unavailable provider selection to a card")
    provider = selection.get("selected_provider")
    profile = selection.get("receiver_profile")
    if not isinstance(provider, str) or not provider or not isinstance(profile, Mapping):
        raise ProviderRegistryError("selected provider binding is incomplete")
    return {"selected_provider": provider, "receiver_profile": dict(profile)}
