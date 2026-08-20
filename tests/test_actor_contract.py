from __future__ import annotations

from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.actor_contract import ActorContractError, actor_contract_public_key, compile_actor_contract, sign_actor_contract

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


def test_contract_signature_binds_the_exact_compiled_receipt():
    signer = Ed25519PrivateKey.from_private_bytes(b"x" * 32)
    signed = sign_actor_contract(_compile(), signing_private_key=signer)
    assert signed["issuer_public_key"] == actor_contract_public_key(signer)
    assert isinstance(signed["signature"], str)


def test_contract_accepts_nix_sri_flake_lock_hash():
    catalog = _catalog()
    catalog["flake_lock"]["sha256"] = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    assert _compile(catalog=catalog)["status"] == "READY"


def test_contract_accepts_nix_bare_hex_flake_lock_hash():
    catalog = _catalog()
    catalog["flake_lock"]["sha256"] = "e5f94446bdaaa47cd7aba1b0d5f67402dd645159561db9de0a48a1baebd1a967"
    assert _compile(catalog=catalog)["status"] == "READY"


def test_v2_actor_is_a_provider_for_neutral_roles_not_a_fixed_harness_assignment():
    catalog = _catalog()
    catalog["schema"] = "tgw-execution-environment-catalog/v2"
    catalog["actors"]["codex"].update({
        "role": "execution-provider",
        "qualified_roles": ["implementation", "controller-verification", "independent-review"],
    })
    assert _compile(catalog=catalog)["status"] == "READY"
    catalog["actors"]["codex"]["role"] = "implementer"
    with pytest.raises(ActorContractError, match="role qualification"):
        _compile(catalog=catalog)


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
