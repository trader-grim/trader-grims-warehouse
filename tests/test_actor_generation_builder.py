import json
import os
import pwd
import shutil
import sys
import tomllib
import uuid
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from tgw.actor_generation_builder import build_actor_generation
from tgw.actor_startup import ActorStartupError, attest_actor_startup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-services"))
from installers.materialize import materialize_complete_actor_contracts  # noqa: E402


@pytest.fixture
def durable_path():
    path = Path("/opt/TGW/var/tmp") / f"actor-generation-test-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_builder_emits_signed_complete_external_generation_consumable_by_materializer(durable_path):
    tmp_path = durable_path
    actor = pwd.getpwuid(os.getuid()).pw_name
    source = tmp_path / "release"
    output = tmp_path / "generations"
    source.mkdir()
    output.mkdir()
    for name, content in {
        "skill/SKILL.md": "bounded plan\n",
        "mcp.json": json.dumps(
            {
                "schema": "tgw-mcp-registration-policy/v1",
                "harness": "generic",
                "endpoint": "tgw-context",
                "transport": "authenticated-registered-mcp",
                "fallback": "forbidden",
                "role_source": "signed-actor-contract",
                "harness_identity_grants_role": False,
                "allowed_tools": {"tgw_context_status": {"arguments": {}}},
                "write_effects": "none",
                "unregistered_tools": "forbidden",
                "stale_or_mixed_binding": "hold",
                "proposal_only": {
                    "on_missing_capability": True,
                    "has_effect_authority": False,
                    "recipient": "orchestrator",
                },
            }
        )
        + "\n",
        "launcher": "#!/bin/sh\nexit 73\n",
        "bootstrap.json": '{"status":"PASS"}\n',
    }.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    catalog = {
        "schema": "tgw-execution-environment-catalog/v3",
        "flake_lock": {"path": "flake.lock", "sha256": "sha256:" + "1" * 64},
        "actors": {
            actor: {
                "role": "execution-provider",
                "qualified_roles": ["implementation"],
                "enabled": True,
                "permitted_profiles": ["development"],
                "required_skills": ["tgw-plan"],
                "required_hooks": [],
                "required_mcp_endpoints": ["tgw-context"],
            }
        },
        "profiles": {"development": {"state": "ready-for-preflight"}},
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    home, project = tmp_path / "home", tmp_path / "project"
    descriptor = {
        "schema": "tgw-actor-generation-descriptor/v1",
        "actors": {
            actor: {
                "profile": "development",
                "home": str(home),
                "project": str(project),
                "bindings": [
                    {"kind": "skill", "name": "tgw-plan", "source": "skill", "destination": str(home / ".skills/tgw-plan")},
                    {"kind": "mcp", "name": "tgw-context", "source": "mcp.json", "destination": str(home / ".mcp/tgw-context.json")},
                    {"kind": "launcher", "name": "launcher", "source": "launcher", "destination": str(home / "bin/tgw-actor")},
                    {"kind": "bootstrap", "name": "bootstrap-receipt", "source": "bootstrap.json", "destination": str(home / ".tgw/bootstrap.json")},
                ],
            }
        },
    }
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(json.dumps(descriptor))
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.key"
    key_path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    key_path.chmod(0o600)
    receipt = build_actor_generation(
        catalog_path=catalog_path,
        descriptor_path=descriptor_path,
        source_root=source,
        output_root=output,
        signing_key_path=key_path,
        plan_commit="f" * 40,
        solution_hash="sha256:" + "2" * 64,
        source_commit="e" * 40,
        source_tree="d" * 40,
        freshness_hash="sha256:" + "3" * 64,
    )
    generation_root = output / receipt["generation"].removeprefix("sha256:")
    bundle = json.loads((generation_root / "bundle.json").read_text())
    contracts = {actor: json.loads((generation_root / "contracts" / f"{actor}.json").read_text())}
    bootstrap = json.loads((generation_root / "bootstrap" / f"{actor}.json").read_text())
    assert bootstrap["status"] == "READY"
    assert bootstrap["plan"] == {"commit": "f" * 40, "solution_hash": "sha256:" + "2" * 64}
    assert bootstrap["code_graph"]["commit"] == "e" * 40
    mcp_binding = next(item for item in bundle["actors"][actor]["bindings"] if item["kind"] == "mcp")
    registration = json.loads(Path(mcp_binding["source"]).read_text())
    registration_env = registration["mcpServers"]["tgw-context"]["env"]
    assert registration["tgw"]["fallback"] == "forbidden"
    assert registration_env["TGW_ACTOR_EXPECTED_GENERATION"] == receipt["generation"]
    assert registration_env["TGW_ACTOR_EXPECTED_PLAN_COMMIT"] == "f" * 40
    applied = materialize_complete_actor_contracts(
        bundle,
        source_root=source,
        contracts=contracts,
        trusted_contract_public_key=receipt["signer_public_key"],
        additional_source_roots=(generation_root,),
        apply=True,
    )
    assert applied["status"] == "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert (home / ".tgw/execution-environment-catalog.json").is_symlink()
    assert (home / ".tgw/actor-contract.json").is_symlink()
    attestation = attest_actor_startup(
        home=home,
        actor=actor,
        trusted_public_key=receipt["signer_public_key"],
        expected_generation=receipt["generation"],
        expected_plan_commit="f" * 40,
        expected_solution_hash="sha256:" + "2" * 64,
        expected_source_commit="e" * 40,
        expected_catalog_hash=receipt["generation_identity"]["catalog_hash"],
    )
    assert attestation["status"] == "PASS"
    assert attestation["fallback"] == "FORBIDDEN"
    with pytest.raises(ActorStartupError, match="stale or mixed"):
        attest_actor_startup(
            home=home,
            actor=actor,
            trusted_public_key=receipt["signer_public_key"],
            expected_generation=receipt["generation"],
            expected_plan_commit="f" * 40,
            expected_solution_hash="sha256:" + "9" * 64,
            expected_source_commit="e" * 40,
            expected_catalog_hash=receipt["generation_identity"]["catalog_hash"],
        )
    assert (
        build_actor_generation(
            catalog_path=catalog_path,
            descriptor_path=descriptor_path,
            source_root=source,
            output_root=output,
            signing_key_path=key_path,
            plan_commit="f" * 40,
            solution_hash="sha256:" + "2" * 64,
            source_commit="e" * 40,
            source_tree="d" * 40,
            freshness_hash="sha256:" + "3" * 64,
        )
        == receipt
    )


def test_checked_in_descriptor_builds_all_provider_neutral_actor_registrations(durable_path):
    tmp_path = durable_path
    output = tmp_path / "generations"
    output.mkdir()
    catalog = {
        "schema": "tgw-execution-environment-catalog/v3",
        "flake_lock": {"path": "flake.lock", "sha256": "1" * 64},
        "actors": {},
        "profiles": {
            "development": {
                "state": "ready-for-preflight",
                "broker_capabilities": ["plan-read"],
            }
        },
    }
    for actor in ("claude", "codex", "deepseek"):
        catalog["actors"][actor] = {
            "role": "execution-provider",
            "qualified_roles": ["implementation"],
            "enabled": True,
            "permitted_profiles": ["development"],
            "required_skills": ["tgw-plan", "tgw-review"],
            "required_hooks": [],
            "required_mcp_endpoints": ["tgw-context"],
        }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.key"
    key_path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    key_path.chmod(0o600)
    receipt = build_actor_generation(
        catalog_path=catalog_path,
        descriptor_path=ROOT / "config/environment/actor-generation-descriptor-v1.json",
        source_root=ROOT,
        output_root=output,
        signing_key_path=key_path,
        plan_commit="f" * 40,
        solution_hash="sha256:" + "2" * 64,
        source_commit="e" * 40,
        source_tree="d" * 40,
        freshness_hash="sha256:" + "3" * 64,
    )
    generation = output / receipt["generation"].removeprefix("sha256:")
    bundle = json.loads((generation / "bundle.json").read_text())
    assert sorted(bundle["actors"]) == ["claude", "codex", "deepseek"]
    codex_binding = next(item for item in bundle["actors"]["codex"]["bindings"] if item["kind"] == "mcp")
    codex_config = tomllib.loads(Path(codex_binding["source"]).read_text())
    assert codex_config["mcp_servers"]["tgw-context"]["args"] == ["--context-mcp"]
    assert codex_config["mcp_servers"]["tgw-context"]["env"]["TGW_ACTOR_EXPECTED_GENERATION"] == receipt["generation"]
    deepseek_binding = next(item for item in bundle["actors"]["deepseek"]["bindings"] if item["kind"] == "mcp")
    deepseek_patch = yaml.safe_load(Path(deepseek_binding["source"]).read_text())
    deepseek_config = deepseek_patch[0]["insert"][0]["config"]
    assert deepseek_binding["destination"] == "/home/deepseek/.dsh/tgw-context.patch.yml"
    assert deepseek_config["command"] == "/home/deepseek/.local/bin/tgw-actor"
    assert deepseek_config["args"] == ["--context-mcp"]
    assert deepseek_config["env"]["TGW_ACTOR_EXPECTED_GENERATION"] == receipt["generation"]


def test_actor_generation_rejects_content_free_mcp_policy(durable_path):
    policy = durable_path / "stub.json"
    policy.write_text(json.dumps({
        "schema": "tgw-mcp-registration-policy/v1",
        "harness": "generic",
        "endpoint": "tgw-context",
        "fallback": "forbidden",
    }))
    from tgw.actor_generation_builder import ActorGenerationError, _mcp_registration

    with pytest.raises(ActorGenerationError, match="policy is invalid"):
        _mcp_registration(
            policy_path=policy,
            actor="fixture",
            endpoint="tgw-context",
            launcher="/opt/TGW/bin/tgw-actor",
            actor_home="/home/fixture",
            generation="sha256:" + "1" * 64,
            plan_commit="f" * 40,
            solution_hash="sha256:" + "2" * 64,
            source_commit="e" * 40,
            catalog_hash="sha256:" + "3" * 64,
            trusted_public_key="fixture",
        )
