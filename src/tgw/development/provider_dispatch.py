"""Resolve neutral coding roles to a validated local queue adapter.

The evaluator owns treatment eligibility; this module keeps provider selection
separate from that treatment identity.  In particular, ``codex-implement`` is
an adapter queue, never the canonical implementation role.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tgw.harness_registry import (
    ProviderRegistryError,
    load_registry,
    observe_health,
    select_provider,
)


class ProviderDispatchError(ValueError):
    """A neutral role cannot be safely adapted to a local queue."""


@dataclass(frozen=True)
class ProviderAdapter:
    role: str
    selected_provider: str
    treatment_id: str
    queue_name: str


_IMPLEMENTATION = ProviderAdapter(
    role="implementation",
    selected_provider="codex-local-runner",
    treatment_id="codex-implement",
    queue_name="codex-implement",
)


def source_adapter_paths() -> dict[str, Path]:
    """Return the canonical source-local adapters required by the registry."""
    root = Path(__file__).resolve().parents[3]
    return {
        "tgw-plan": root / "agent-services/skills/tgw-plan",
        "promptcraft": root / "agent-services/providers/promptcraft",
        "promptcraft-card-handoff": root / "agent-services/providers/promptcraft/bin/promptcraft-handoff",
    }


def source_registry_path() -> Path:
    return Path(__file__).resolve().parents[3] / "agent-services/catalogs/harness-providers-v1.json"


def resolve_implementation_adapter(
    coding_config: Mapping[str, Any],
    *,
    registry_path: str | Path | None = None,
    adapters: Mapping[str, str | Path] | None = None,
) -> ProviderAdapter:
    """Select the implementation provider then prove its queue adapter.

    This intentionally accepts no treatment name: callers begin from the
    canonical role and cannot turn a queue label into provider authority.
    """
    try:
        registry = load_registry(registry_path or source_registry_path())
        bound_adapters = dict(adapters or source_adapter_paths())
        health = observe_health(registry, coding_config=coding_config, adapters=bound_adapters)
        selection = select_provider(
            registry, health, role="implementation", adapters=bound_adapters,
            required_capabilities=("source-mutation",),
        )
    except (OSError, ProviderRegistryError, ValueError) as exc:
        raise ProviderDispatchError(f"implementation provider catalog is invalid: {exc}") from exc
    if selection.get("status") != "SELECTED":
        raise ProviderDispatchError("no eligible implementation provider")
    if selection.get("selected_provider") != _IMPLEMENTATION.selected_provider:
        raise ProviderDispatchError("selected implementation provider has no local queue adapter")

    provider = next(
        (item for item in registry["providers"] if item.get("id") == _IMPLEMENTATION.selected_provider),
        None,
    )
    runner = provider.get("runner") if isinstance(provider, dict) else None
    if not isinstance(runner, dict) or runner.get("kind") != "configured-argv" or runner.get("key") != _IMPLEMENTATION.treatment_id:
        raise ProviderDispatchError("implementation provider adapter mapping is malformed")
    return _IMPLEMENTATION


def validate_implementation_adapter(payload: Mapping[str, Any], queue_name: str) -> dict[str, str] | None:
    """Fail closed if a neutral implementation payload loses its adapter binding."""
    fields = ("coding_role", "selected_provider", "adapter_treatment_id", "adapter_queue_name")
    present = [field in payload for field in fields]
    if not any(present):
        return None
    if not all(present):
        raise ProviderDispatchError("coding payload has incomplete provider adapter binding")
    value = {field: payload[field] for field in fields}
    if value != {
        "coding_role": _IMPLEMENTATION.role,
        "selected_provider": _IMPLEMENTATION.selected_provider,
        "adapter_treatment_id": _IMPLEMENTATION.treatment_id,
        "adapter_queue_name": _IMPLEMENTATION.queue_name,
    }:
        raise ProviderDispatchError("coding payload provider adapter binding does not match implementation")
    if payload.get("treatment_id") != _IMPLEMENTATION.treatment_id or queue_name != _IMPLEMENTATION.queue_name:
        raise ProviderDispatchError("coding payload treatment does not match implementation adapter")
    return value
