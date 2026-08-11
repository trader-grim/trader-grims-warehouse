"""Validation for the clean TGW Steward operating contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


class AgentContractError(ValueError):
    """An agent contract grants unsupported or ambiguous authority."""


_TOP = {
    "schema", "actor_id", "version", "status", "display_name", "purpose",
    "authority", "modes", "effects", "prohibitions", "satellite_runtime_dependency",
    "persona_is_style_only",
}
_AUTHORITY = {
    "current_fact_sources", "historical_search_role", "historical_search_grants_authority",
    "memory_grants_authority", "plan_text_grants_effect_authority",
}
_MODES = {"executive-assistant", "librarian", "issue-manager"}
_EFFECTS = {"production", "infrastructure", "satellite", "memory_import"}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AgentContractError(f"{label} must be a string-keyed mapping")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise AgentContractError(f"{label} must be a canonical non-empty string")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AgentContractError(f"{label} must be a non-empty string list")
    if not all(isinstance(item, str) and item.strip() and item == item.strip() for item in value):
        raise AgentContractError(f"{label} must be a string list")
    if len(value) != len(set(value)):
        raise AgentContractError(f"{label} contains duplicates")
    return value


def validate_agent_contract(raw: Mapping[str, Any]) -> dict[str, Any]:
    contract = _mapping(dict(raw), "agent contract")
    if set(contract) != _TOP or contract.get("schema") != "tgw-agent-contract/v1":
        raise AgentContractError("agent contract fields or schema are not exact")
    if contract["actor_id"] != "tgw-steward" or contract["status"] != "active":
        raise AgentContractError("agent identity or status is invalid")
    if isinstance(contract["version"], bool) or not isinstance(contract["version"], int) or contract["version"] < 1:
        raise AgentContractError("agent contract version is invalid")
    for key in ("display_name", "purpose"):
        _string(contract[key], key)
    authority = _mapping(contract["authority"], "authority")
    if set(authority) != _AUTHORITY:
        raise AgentContractError("authority fields are not exact")
    if authority["current_fact_sources"] != [
        "environment-registry", "canonical-repositories", "exact-task-contract",
    ]:
        raise AgentContractError("current fact sources are not the admitted precedence")
    if authority["historical_search_role"] != "explicit-cited-reference-only":
        raise AgentContractError("historical search role is unsafe")
    for key in (
        "historical_search_grants_authority", "memory_grants_authority",
        "plan_text_grants_effect_authority",
    ):
        if authority[key] is not False:
            raise AgentContractError(f"{key} must be false")
    modes = _mapping(contract["modes"], "modes")
    if set(modes) != _MODES:
        raise AgentContractError("agent modes are not exact")
    for mode, entry_raw in modes.items():
        entry = _mapping(entry_raw, f"mode {mode}")
        if set(entry) != {"stores", "infrastructure_authority"}:
            raise AgentContractError(f"mode {mode} fields are not exact")
        _strings(entry["stores"], f"mode {mode} stores")
        if entry["infrastructure_authority"] is not False:
            raise AgentContractError(f"mode {mode} cannot grant infrastructure authority")
    effects = _mapping(contract["effects"], "effects")
    if set(effects) != _EFFECTS:
        raise AgentContractError("effect fields are not exact")
    if any(effects[key] != "none" for key in ("production", "infrastructure", "satellite")):
        raise AgentContractError("agent contract grants external effects")
    if effects["memory_import"] != "human-reviewed-batch-only":
        raise AgentContractError("memory import must remain human reviewed")
    _strings(contract["prohibitions"], "prohibitions")
    if contract["satellite_runtime_dependency"] is not False:
        raise AgentContractError("satellites cannot be runtime dependencies")
    if contract["persona_is_style_only"] is not True:
        raise AgentContractError("persona must be style only")
    return contract


def load_agent_contract(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentContractError(f"cannot load agent contract: {exc}") from exc
    return validate_agent_contract(_mapping(raw, "agent contract"))
