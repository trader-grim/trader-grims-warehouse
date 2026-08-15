from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tgw.environment_registry import (
    EnvironmentRegistryError,
    RetiredHostError,
    content_revision,
    load_registry,
    resolve_host,
    resolved_agent_context,
    validate_registry,
)

REGISTRY = Path(__file__).parents[1] / "config/environment/registry.yaml"


def _registry():
    return load_registry(REGISTRY)


def test_repository_registry_is_valid_and_revision_bound():
    registry = _registry()
    assert registry["revision"] == content_revision(registry["content"])
    assert resolve_host(registry, "production")["canonical_name"] == "tgw-prod"
    assert resolve_host(registry, "tgw-lib")["host_role"] == "development"


@pytest.mark.parametrize("identity", ["a1131", "catnanny", "helicrew", "made-up"])
def test_retired_and_unknown_hosts_fail_closed(identity):
    error = RetiredHostError if identity == "a1131" else EnvironmentRegistryError
    with pytest.raises(error):
        resolve_host(_registry(), identity)


@pytest.mark.parametrize("actor", ["codex", "hermes-tigwa"])
def test_non_claude_context_cannot_receive_claude_contract(actor):
    context = resolved_agent_context(_registry(), actor)
    assert "CLAUDE.md" not in context["actor_instructions"]["authority_files"]
    assert "CLAUDE.md" in context["actor_instructions"]["excluded_authority_files"]
    assert context["instructions"]["memory_grants_authority"] is False
    assert context["instructions"]["history_grants_authority"] is False


def test_registry_rejects_revision_secret_and_silent_retired_redirect():
    registry = _registry()
    bad = deepcopy(registry)
    bad["revision"] = "sha256:" + "0" * 64
    with pytest.raises(EnvironmentRegistryError, match="revision"):
        validate_registry(bad)

    bad = deepcopy(registry)
    bad["content"]["api_token"] = "unsafe"
    bad["revision"] = content_revision(bad["content"])
    with pytest.raises(EnvironmentRegistryError):
        validate_registry(bad)

    bad = deepcopy(registry)
    bad["content"]["retired_hosts"]["a1131"]["behavior"] = "redirect"
    bad["revision"] = content_revision(bad["content"])
    with pytest.raises(EnvironmentRegistryError, match="fail"):
        validate_registry(bad)


def test_context_is_deterministic_and_actor_scoped():
    first = resolved_agent_context(_registry(), "codex")
    second = resolved_agent_context(_registry(), "codex")
    assert first == second
    assert first["actor_instructions"] != resolved_agent_context(_registry(), "claude-code")["actor_instructions"]
