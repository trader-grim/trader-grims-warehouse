from __future__ import annotations

from copy import deepcopy

import pytest

from tgw.actor_contract import ActorContractError, compile_actor_contract

HASH = "sha256:" + "a" * 64
COMMIT = "b" * 40


def _catalog():
    return {
        "schema": "tgw-execution-environment-catalog/v1",
        "flake_lock": {"path": "flake.lock", "sha256": HASH},
        "actors": {
            "codex": {
                "enabled": True,
                "permitted_profiles": ["development"],
                "required_skills": ["tgw-plan"],
                "required_hooks": ["context-gate"],
                "required_mcp_endpoints": ["tgw-context"],
            }
        },
        "profiles": {"development": {"state": "ready-for-preflight"}},
    }


def _local():
    return {
        "bootstrap_receipt_hash": HASH,
        "launcher": {"path": "/opt/TGW/bin/launcher", "sha256": HASH},
        "skills": {"tgw-plan": HASH},
        "hooks": {"context-gate": HASH},
        "mcp": {"endpoints": ["tgw-context"], "binding_hash": HASH},
    }


def _compile(catalog=None, local=None):
    return compile_actor_contract(
        catalog=catalog or _catalog(),
        actor="codex",
        profile="development",
        plan_commit=COMMIT,
        plan_solution_hash=HASH,
        code_graph={"commit": COMMIT, "tree": COMMIT, "freshness_hash": HASH},
        local=local or _local(),
    )


def test_contract_is_deterministic_and_non_activating_when_exactly_bound():
    first, second = _compile(), _compile()
    assert first == second
    assert first["status"] == "READY"
    assert first["activation"] == "declarative-only"


def test_missing_catalog_contract_input_quarantines_before_launch():
    local = _local()
    local["mcp"]["endpoints"] = []
    local["hooks"] = {}
    receipt = _compile(local=local)
    assert receipt["status"] == "QUARANTINED"
    assert {item["code"] for item in receipt["diagnostics"]} == {"MISSING_REQUIRED_HOOKS", "MISSING_MCP_ENDPOINT"}


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value["profiles"]["development"].update({"state": "HOLD"}),
        lambda value: value["actors"]["codex"].update({"enabled": False}),
    ],
)
def test_catalog_hold_or_disabled_actor_quarantines(change):
    catalog = deepcopy(_catalog())
    change(catalog)
    assert _compile(catalog=catalog)["status"] == "QUARANTINED"


def test_malformed_binding_is_refused_not_quarantined_as_usable():
    with pytest.raises(ActorContractError, match="Plan commit"):
        compile_actor_contract(
            catalog=_catalog(),
            actor="codex",
            profile="development",
            plan_commit="not-a-commit",
            plan_solution_hash=HASH,
            code_graph={"commit": COMMIT, "tree": COMMIT, "freshness_hash": HASH},
            local=_local(),
        )


@pytest.mark.parametrize(
    ("plan_commit", "code_graph"),
    [
        (1, {"commit": COMMIT, "tree": COMMIT, "freshness_hash": HASH}),
        (COMMIT, {"commit": 1, "tree": COMMIT, "freshness_hash": HASH}),
        (COMMIT, {"commit": COMMIT, "tree": [], "freshness_hash": HASH}),
    ],
)
def test_non_string_commit_or_tree_is_refused_with_typed_error(plan_commit, code_graph):
    with pytest.raises(ActorContractError):
        compile_actor_contract(
            catalog=_catalog(), actor="codex", profile="development", plan_commit=plan_commit,
            plan_solution_hash=HASH, code_graph=code_graph, local=_local(),
        )
