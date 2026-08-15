from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tgw.agent_contract import AgentContractError, load_agent_contract, validate_agent_contract
from tgw.environment_registry import load_registry, resolved_agent_context

ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "config/environment/actors/tgw-steward.json"
REGISTRY = ROOT / "config/environment/registry.yaml"


def test_steward_contract_is_clean_and_registry_routed():
    contract = load_agent_contract(CONTRACT)
    assert contract["display_name"] == "Hermes"
    assert contract["effects"]["production"] == "none"
    context = resolved_agent_context(load_registry(REGISTRY), "hermes-tigwa")
    assert context["actor_instructions"]["authority_files"] == [
        "AGENTS.md", "config/environment/actors/tgw-steward.json",
    ]
    assert "CLAUDE.md" in context["actor_instructions"]["excluded_authority_files"]
    assert "docs/TGW-Plan-Vault/plan/pp/PP-HERMES-EA-001.md" in context["actor_instructions"]["excluded_authority_files"]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("authority", "memory_grants_authority"), True),
        (("authority", "historical_search_grants_authority"), True),
        (("effects", "production"), "write"),
        (("modes", "executive-assistant", "infrastructure_authority"), True),
        (("satellite_runtime_dependency",), True),
        (("persona_is_style_only",), False),
    ],
)
def test_contract_rejects_authority_escalation(path, value):
    contract = load_agent_contract(CONTRACT)
    target = contract
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(AgentContractError):
        validate_agent_contract(contract)


def test_contract_rejects_monolithic_or_missing_modes():
    contract = deepcopy(load_agent_contract(CONTRACT))
    contract["modes"] = {"everything": {"stores": ["memory"], "infrastructure_authority": False}}
    with pytest.raises(AgentContractError, match="modes"):
        validate_agent_contract(contract)
