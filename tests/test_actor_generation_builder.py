import json
import os
import pwd
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, NoEncryption

from tgw.actor_generation_builder import build_actor_generation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-services"))
from installers.materialize import materialize_complete_actor_contracts  # noqa: E402


def test_builder_emits_signed_complete_external_generation_consumable_by_materializer(tmp_path):
    actor = pwd.getpwuid(os.getuid()).pw_name
    source = tmp_path / "release"
    output = tmp_path / "generations"
    source.mkdir(); output.mkdir()
    for name, content in {
        "skill/SKILL.md": "bounded plan\n", "mcp.json": '{"endpoint":"tgw-context"}\n',
        "launcher": "#!/bin/sh\nexit 73\n", "bootstrap.json": '{"status":"PASS"}\n',
    }.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    catalog = {
        "schema": "tgw-execution-environment-catalog/v3",
        "flake_lock": {"path": "flake.lock", "sha256": "sha256:" + "1" * 64},
        "actors": {actor: {
            "role": "execution-provider", "qualified_roles": ["implementation"],
            "enabled": True, "permitted_profiles": ["development"],
            "required_skills": ["tgw-plan"], "required_hooks": [],
            "required_mcp_endpoints": ["tgw-context"],
        }},
        "profiles": {"development": {"state": "ready-for-preflight"}},
    }
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    home, project = tmp_path / "home", tmp_path / "project"
    descriptor = {
        "schema": "tgw-actor-generation-descriptor/v1",
        "actors": {actor: {
            "profile": "development", "home": str(home), "project": str(project),
            "bindings": [
                {"kind": "skill", "name": "tgw-plan", "source": "skill", "destination": str(home / ".skills/tgw-plan")},
                {"kind": "mcp", "name": "tgw-context", "source": "mcp.json", "destination": str(home / ".mcp/tgw-context.json")},
                {"kind": "launcher", "name": "launcher", "source": "launcher", "destination": str(home / "bin/tgw-actor")},
                {"kind": "bootstrap", "name": "bootstrap-receipt", "source": "bootstrap.json", "destination": str(home / ".tgw/bootstrap.json")},
            ],
        }},
    }
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_text(json.dumps(descriptor))
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "signing.key"
    key_path.write_bytes(key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()))
    key_path.chmod(0o600)
    receipt = build_actor_generation(
        catalog_path=catalog_path, descriptor_path=descriptor_path,
        source_root=source, output_root=output, signing_key_path=key_path,
        plan_commit="f" * 40, solution_hash="sha256:" + "2" * 64,
        source_commit="e" * 40, source_tree="d" * 40,
        freshness_hash="sha256:" + "3" * 64,
    )
    generation_root = output / receipt["generation"].removeprefix("sha256:")
    bundle = json.loads((generation_root / "bundle.json").read_text())
    contracts = {actor: json.loads((generation_root / "contracts" / f"{actor}.json").read_text())}
    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=receipt["signer_public_key"],
        additional_source_roots=(generation_root,), apply=True,
    )
    assert applied["status"] == "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert (home / ".tgw/execution-environment-catalog.json").is_symlink()
    assert (home / ".tgw/actor-contract.json").is_symlink()
    assert build_actor_generation(
        catalog_path=catalog_path, descriptor_path=descriptor_path,
        source_root=source, output_root=output, signing_key_path=key_path,
        plan_commit="f" * 40, solution_hash="sha256:" + "2" * 64,
        source_commit="e" * 40, source_tree="d" * 40,
        freshness_hash="sha256:" + "3" * 64,
    ) == receipt
