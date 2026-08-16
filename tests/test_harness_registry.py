import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from tgw.harness_registry import (
    ProviderRegistryError,
    execution_card_provider_fields,
    load_registry,
    observe_health,
    select_provider,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "agent-services/catalogs/harness-providers-v1.json"
PROMPTCRAFT_ROOT = ROOT / "agent-services/providers/promptcraft"
sys.path.insert(0, str(PROMPTCRAFT_ROOT))

from promptcraft.handoff import ExecutionCard, craft_handoff, verify_for_launcher  # noqa: E402


def executable(path: Path):
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


def environment(tmp_path):
    codex = executable(tmp_path / "codex-runner")
    controller = executable(tmp_path / "controller-runner")
    skill = tmp_path / "tgw-plan"
    promptcraft = tmp_path / "promptcraft"
    handoff = tmp_path / "promptcraft-handoff"
    skill.mkdir()
    promptcraft.mkdir()
    handoff.write_text("adapter")
    adapters = {
        "tgw-plan": skill,
        "promptcraft": promptcraft,
        "promptcraft-card-handoff": handoff,
    }
    config = {"commands": {"codex-implement": [codex, "run"], "controller-verify": [controller, "verify"]}}
    return config, adapters


def test_role_selection_uses_qualification_health_and_preference_not_brand(tmp_path):
    registry = load_registry(CATALOG)
    config, adapters = environment(tmp_path)
    health = observe_health(registry, coding_config=config, adapters=adapters)

    implementation = select_provider(
        registry, health, role="implementation", adapters=adapters, required_capabilities=["source-mutation"]
    )
    controller = select_provider(
        registry, health, role="controller-verification", adapters=adapters, required_capabilities=["tests"]
    )

    assert implementation["selected_provider"] == "codex-local-runner"
    assert controller["selected_provider"] == "controller-local-runner"
    assert Path(implementation["runner_argv"][0]).is_absolute()
    assert set(implementation["adapter_bindings"]) == {"tgw-plan", "promptcraft"}


def test_same_provider_can_fill_controller_role_when_qualified_alternative_is_unhealthy(tmp_path):
    registry = load_registry(CATALOG)
    config, adapters = environment(tmp_path)
    os.unlink(config["commands"]["controller-verify"][0])
    health = observe_health(registry, coding_config=config, adapters=adapters)

    selected = select_provider(
        registry, health, role="controller-verification", adapters=adapters, required_capabilities=["tests"]
    )

    assert selected["selected_provider"] == "codex-local-runner"


def test_independence_exclusion_is_a_selection_constraint(tmp_path):
    registry = load_registry(CATALOG)
    config, adapters = environment(tmp_path)
    health = observe_health(registry, coding_config=config, adapters=adapters)

    selected = select_provider(
        registry,
        health,
        role="independent-review",
        adapters=adapters,
        required_capabilities=["tests"],
        independent_from=["codex-local-runner"],
    )

    assert selected["selected_provider"] == "controller-local-runner"
    codex = next(item for item in selected["considered"] if item["provider"] == "codex-local-runner")
    assert "independence-exclusion" in codex["reasons"]


def test_missing_claude_runner_remains_explicitly_unavailable(tmp_path):
    registry = load_registry(CATALOG)
    config, adapters = environment(tmp_path)
    health = observe_health(registry, coding_config=config, adapters=adapters)

    assert health["claude-local-runner"].available is False
    assert "not present" in health["claude-local-runner"].reason
    unavailable = select_provider(
        registry,
        health,
        role="implementation",
        adapters=adapters,
        required_capabilities=["repository-shell"],
        independent_from=["codex-local-runner"],
    )
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["selected_provider"] is None
    claude = next(item for item in unavailable["considered"] if item["provider"] == "claude-local-runner")
    assert any(reason.startswith("unavailable:") for reason in claude["reasons"])


def test_missing_shared_skill_or_promptcraft_adapter_holds_provider(tmp_path):
    registry = load_registry(CATALOG)
    config, adapters = environment(tmp_path)
    adapters["promptcraft"].rmdir()
    health = observe_health(registry, coding_config=config, adapters=adapters)

    assert health["codex-local-runner"].available is False
    assert health["codex-local-runner"].reason == "missing canonical adapters: promptcraft"
    selected = select_provider(
        registry,
        health,
        role="implementation",
        adapters=adapters,
        required_capabilities=["source-mutation"],
    )
    assert selected["status"] == "UNAVAILABLE"


def test_selection_fields_flow_mechanically_into_promptcraft_card(tmp_path):
    registry = load_registry(CATALOG)
    config, adapters = environment(tmp_path)
    health = observe_health(registry, coding_config=config, adapters=adapters)
    selection = select_provider(
        registry, health, role="implementation", adapters=adapters, required_capabilities=["source-mutation"]
    )
    provider_fields = execution_card_provider_fields(selection)
    plan_commit = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
    resource_service = {
        "schema": "tgw-registered-resource-service/v1",
        "id": "registry-resource-service",
        "endpoint": "https://resources.invalid",
        "credential_env": None,
        "timeout_seconds": 5,
    }

    def binding(ref, content):
        return {"ref": ref, "hash": "sha256:" + hashlib.sha256(content.encode()).hexdigest()}

    card = ExecutionCard.create(
        {
            "card_id": "card-registry-1",
            "solution_id": "sha256:solution",
            "role": selection["role"],
            **provider_fields,
            "plan_commit": plan_commit,
            "resource_service": {
                "id": resource_service["id"],
                "descriptor_hash": "sha256:" + hashlib.sha256(
                    json.dumps(resource_service, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
            },
            "bindings": {
                "plan_input": binding("plan:p", "plan"),
                "plan_commit": binding("plan-commit:fb9", plan_commit),
                "plan_graph": binding("graph:g", "graph"),
                "codegraph_snapshot": binding("code:c", "code"),
                "source_tree": binding("git:s", "source"),
                "execution_environment": binding("env:e", "environment"),
                "authority_conditions": binding("auth:a", "authority"),
                "receipt_sink": binding("receipt:r", "sink"),
            },
            "authority": ["local source edit"],
            "exclusions": ["no deployment"],
            "acceptance": ["tests pass"],
            "lease": {"id": "lease:l", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
        }
    )

    receipt_unsigned = {
        "schema": "tgw-execution-resource-receipt/v1",
        "card_hash": card.hash,
        "plan_commit": plan_commit,
        "resources": {name: value for name, value in sorted(card.value["bindings"].items())},
    }
    resource_receipt = {
        **receipt_unsigned,
        "receipt_hash": "sha256:" + hashlib.sha256(json.dumps(receipt_unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    invocation = verify_for_launcher(
        craft_handoff(
            {
                "card": card.value,
                "resource_receipt": resource_receipt,
                "resource_service": resource_service,
            },
            receiver_identity="run:1",
        )
    )
    assert invocation["selected_provider"] == selection["selected_provider"]
    assert provider_fields["receiver_profile"] == selection["receiver_profile"]


def test_unavailable_selection_cannot_be_bound_to_card(tmp_path):
    registry = load_registry(CATALOG)
    _, adapters = environment(tmp_path)
    health = observe_health(registry, coding_config={"commands": {}}, adapters=adapters)
    selection = select_provider(registry, health, role="implementation", adapters=adapters)

    with pytest.raises(ProviderRegistryError, match="unavailable"):
        execution_card_provider_fields(selection)
