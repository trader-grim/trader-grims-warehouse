import hashlib
import json
import os
import pwd
import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from tgw import actor_generation_builder, actor_startup
from tgw.actor_generation_builder import build_actor_generation
from tgw.actor_startup import ActorStartupError, attest_actor_startup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent-services"))
from installers.materialize import materialize_complete_actor_contracts  # noqa: E402


def _context_source(root: Path) -> tuple[Path, str, str]:
    source = root / "context-source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.name", "TGW fixture"], check=True)
    subprocess.run(["git", "-C", str(source), "config", "user.email", "fixture@example.invalid"], check=True)
    (source / "source.txt").write_text("exact actor Context MCP source\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "source.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
    commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return source, commit, tree


def _git_tool() -> dict[str, str]:
    path = Path(shutil.which("git") or "").resolve(strict=True)
    return {
        "name": "git",
        "executable_path": str(path),
        "executable_sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _protect_fixture_context_source(monkeypatch, source: Path) -> None:
    source = source.resolve(strict=True)

    def validate_fixture_source(
        candidate,
        _git_path,
        *,
        expected_commit=None,
        expected_tree=None,
    ):
        candidate = Path(candidate).resolve(strict=True)
        assert candidate == source
        status = subprocess.run(
            ["git", "-C", str(candidate), "status", "--porcelain=v1"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        commit = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "HEAD^{commit}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "HEAD^{tree}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert status == ""
        assert expected_commit in {None, commit}
        assert expected_tree in {None, tree}
        return candidate, commit, tree

    monkeypatch.setattr(
        actor_generation_builder,
        "validate_context_source",
        validate_fixture_source,
    )


def test_context_mcp_environment_uses_exact_generated_source_and_platform_bindings(monkeypatch):
    catalog = {"profiles": {"development": {"tools": []}}}
    catalog_hash = "sha256:" + hashlib.sha256(
        json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    commit, tree = "e" * 40, "d" * 40
    result = {
        "actor": "fixture",
        "generation": "sha256:" + "1" * 64,
        "plan": {"commit": "f" * 40, "solution_hash": "sha256:" + "2" * 64},
        "source_commit": commit,
        "source_tree": tree,
        "catalog_hash": catalog_hash,
        "profile": "development",
    }
    binding = {
        "trusted_public_key": "public-key",
        "context_source_root": "/opt/TGW/w/actors/fixture/exact-source",
        "expected_source_tree": tree,
        "fleet_convergence_path": (
            "/opt/TGW/tgw-lib/var/context-update/fleet-convergence.json"
        ),
    }
    source = "/opt/TGW/w/actors/fixture/exact-source"
    environment = {
        "TGW_ACTOR_CONTRACT_PUBLIC_KEY": "public-key",
        "TGW_ACTOR_EXPECTED_GENERATION": result["generation"],
        "TGW_ACTOR_EXPECTED_PLAN_COMMIT": result["plan"]["commit"],
        "TGW_ACTOR_EXPECTED_PLAN_SOLUTION": result["plan"]["solution_hash"],
        "TGW_ACTOR_EXPECTED_SOURCE_COMMIT": commit,
        "TGW_ACTOR_EXPECTED_CATALOG_HASH": catalog_hash,
        "TGW_ACTOR_CONTEXT_SOURCE_ROOT": source,
        "TGW_ACTOR_PLAN_REPOSITORY": "/opt/TGW/library/plans",
        "TGW_ACTOR_APPROVED_PLAN_ROOT": f"/opt/TGW/library/approved/{result['plan']['commit']}",
        "TGW_ACTOR_CONTEXT_RUNTIME_ROOT": "/opt/TGW/tgw-lib/var/context",
        "TGW_ACTOR_CONTEXT_CACHE_ROOT": (
            f"/opt/TGW/var/cache/tgw/actors/fixture/{result['generation'].removeprefix('sha256:')}/context-mcp"
        ),
        "TGW_ACTOR_ENVIRONMENT_CATALOG": "/etc/tgw/execution-environment-catalog.json",
    }
    monkeypatch.setattr(actor_startup, "_object", lambda _path, _label: catalog)
    monkeypatch.setattr(actor_startup, "_profile_tool", lambda _catalog, _profile, _name: "/nix/store/git/bin/git")
    monkeypatch.setattr(actor_startup, "_context_source_identity", lambda _path, _git: (commit, tree))
    monkeypatch.setattr(Path, "is_dir", lambda _path: True)
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setenv("TGW_ACTOR_EXPECTED_GENERATION", "sha256:" + "9" * 64)

    activated = actor_startup._context_mcp_environment(
        home=Path("/home/fixture"), result=result, binding=binding,
        binding_path=Path("/etc/tgw/actors/fixture-startup.json"),
    )

    assert activated["TGW_CONTEXT_SOURCE_ROOT"] == source
    assert activated["TGW_CONTEXT_ENVIRONMENT_CATALOG"] == "/etc/tgw/execution-environment-catalog.json"
    assert activated["GIT_CONFIG_VALUE_0"] == source
    assert activated["TMPDIR"] == environment["TGW_ACTOR_CONTEXT_CACHE_ROOT"]
    assert activated["TGW_CONTEXT_GENERATION"] == result["generation"]
    assert activated["TGW_CONTEXT_SOURCE_COMMIT"] == commit
    assert activated["TGW_CONTEXT_SOURCE_TREE"] == tree
    assert activated["TGW_CONTEXT_STARTUP_BINDING"] == "/etc/tgw/actors/fixture-startup.json"
    assert activated["TGW_CONTEXT_FLEET_CONVERGENCE"] == binding[
        "fleet_convergence_path"
    ]

    monkeypatch.setattr(actor_startup, "_context_source_identity", lambda _path, _git: ("0" * 40, tree))
    with pytest.raises(ActorStartupError, match="source binding"):
        actor_startup._context_mcp_environment(
            home=Path("/home/fixture"), result=result, binding=binding,
            binding_path=Path("/etc/tgw/actors/fixture-startup.json"),
        )


@pytest.fixture
def durable_path():
    path = Path("/opt/TGW/var/tmp") / f"actor-generation-test-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700)
    try:
        yield path
    finally:
        for directory in sorted(
            (item for item in path.rglob("*") if item.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            directory.chmod(0o700)
        shutil.rmtree(path)


def test_builder_emits_signed_complete_external_generation_consumable_by_materializer(
    durable_path, monkeypatch,
):
    tmp_path = durable_path
    context_source, source_commit, source_tree = _context_source(tmp_path)
    _protect_fixture_context_source(monkeypatch, context_source)
    actor = pwd.getpwuid(os.getuid()).pw_name
    source = tmp_path / "release"
    output = tmp_path / "generations"
    source.mkdir()
    output.mkdir()
    for name, content in {
        "AGENTS.md": "# TGW agent entry point\n",
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
                    "allowed_tools": {
                        "tgw_context_status": {"arguments": {}},
                        "tgw_context_confirm_rebind": {
                            "arguments": {
                                "transaction_id": {},
                                "direction": {},
                                "obligation_id": {},
                            }
                        },
                    },
                    "write_effects": (
                        "only a credential-free self-process/active-obligation "
                        "confirmation receipt"
                    ),
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
        "profiles": {"development": {"state": "ready-for-preflight", "tools": [_git_tool()]}},
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
                    {
                        "kind": "instruction",
                        "name": "agent-entry-point",
                        "capability": "agent-entry-point",
                        "source": "AGENTS.md",
                        "destination": str(home / ".codex/AGENTS.md"),
                    },
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
        context_source_root=context_source,
        output_root=output,
        signing_key_path=key_path,
        plan_commit="f" * 40,
        solution_hash="sha256:" + "2" * 64,
        source_commit=source_commit,
        source_tree=source_tree,
        freshness_hash="sha256:" + "3" * 64,
    )
    generation_root = output / receipt["generation"].removeprefix("sha256:")
    assert receipt["generation_identity"]["artifact_access"] == "immutable-public-inputs-v1"
    assert generation_root.stat().st_mode & 0o777 == 0o555
    assert all(path.stat().st_mode & 0o777 == 0o555 for path in generation_root.rglob("*") if path.is_dir())
    assert all(path.stat().st_mode & 0o777 == 0o444 for path in generation_root.rglob("*") if path.is_file())
    bundle = json.loads((generation_root / "bundle.json").read_text())
    contracts = {actor: json.loads((generation_root / "contracts" / f"{actor}.json").read_text())}
    bootstrap = json.loads((generation_root / "bootstrap" / f"{actor}.json").read_text())
    assert bootstrap["status"] == "READY"
    assert bootstrap["plan"] == {"commit": "f" * 40, "solution_hash": "sha256:" + "2" * 64}
    assert bootstrap["code_graph"]["commit"] == source_commit
    instruction_binding = next(
        item for item in bundle["actors"][actor]["bindings"]
        if item["kind"] == "instruction"
    )
    instruction_hash = "sha256:" + hashlib.sha256(
        (source / "AGENTS.md").read_bytes()
    ).hexdigest()
    assert instruction_binding == {
        "kind": "instruction",
        "name": "agent-entry-point",
        "capability": "agent-entry-point",
        "source": "AGENTS.md",
        "destination": str(home / ".codex/AGENTS.md"),
        "sha256": instruction_hash,
    }
    assert bootstrap["instructions"] == {
        "agent-entry-point": {
            "path": str(home / ".codex/AGENTS.md"),
            "sha256": instruction_hash,
        }
    }
    mcp_binding = next(item for item in bundle["actors"][actor]["bindings"] if item["kind"] == "mcp")
    registration = json.loads(Path(mcp_binding["source"]).read_text())
    registration_env = registration["mcpServers"]["tgw-context"]["env"]
    assert registration["tgw"]["fallback"] == "forbidden"
    assert registration_env == {
        "TGW_ACTOR_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_ACTOR_CONTEXT_REGISTRATION": "stable-launcher-v1",
    }
    successor = build_actor_generation(
        catalog_path=catalog_path,
        descriptor_path=descriptor_path,
        source_root=source,
        context_source_root=context_source,
        output_root=output,
        signing_key_path=key_path,
        plan_commit="a" * 40,
        solution_hash="sha256:" + "4" * 64,
        source_commit=source_commit,
        source_tree=source_tree,
        freshness_hash="sha256:" + "5" * 64,
    )
    successor_root = output / successor["generation"].removeprefix("sha256:")
    successor_bundle = json.loads((successor_root / "bundle.json").read_text())
    successor_mcp = next(
        item for item in successor_bundle["actors"][actor]["bindings"] if item["kind"] == "mcp"
    )
    assert Path(successor_mcp["source"]).read_bytes() == Path(mcp_binding["source"]).read_bytes()
    for binding in bundle["actors"][actor]["bindings"]:
        Path(binding["destination"]).parent.mkdir(parents=True, exist_ok=True)
    applied = materialize_complete_actor_contracts(
        bundle,
        source_root=source,
        contracts=contracts,
        trusted_contract_public_key=receipt["signer_public_key"],
        additional_source_roots=(generation_root,),
        transaction_journal_path=tmp_path / "materializer-transaction.json",
        apply=True,
    )
    assert applied["status"] == "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert (home / ".codex/AGENTS.md").is_symlink()
    assert (home / ".codex/AGENTS.md").resolve() == source / "AGENTS.md"
    assert (home / ".tgw/execution-environment-catalog.json").is_symlink()
    assert (home / ".tgw/actor-contract.json").is_symlink()
    attestation = attest_actor_startup(
        home=home,
        actor=actor,
        trusted_public_key=receipt["signer_public_key"],
        expected_generation=receipt["generation"],
        expected_plan_commit="f" * 40,
        expected_solution_hash="sha256:" + "2" * 64,
        expected_source_commit=source_commit,
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
            context_source_root=context_source,
            output_root=output,
            signing_key_path=key_path,
            plan_commit="f" * 40,
            solution_hash="sha256:" + "2" * 64,
            source_commit=source_commit,
            source_tree=source_tree,
            freshness_hash="sha256:" + "3" * 64,
        )
        == receipt
    )
    descriptor["actors"][actor]["bindings"] = [
        binding for binding in descriptor["actors"][actor]["bindings"]
        if binding["kind"] != "instruction"
    ]
    descriptor_path.write_text(json.dumps(descriptor))
    with pytest.raises(
        actor_generation_builder.ActorGenerationError,
        match="instruction entry point is incomplete",
    ):
        build_actor_generation(
            catalog_path=catalog_path,
            descriptor_path=descriptor_path,
            source_root=source,
            context_source_root=context_source,
            output_root=output,
            signing_key_path=key_path,
            plan_commit="f" * 40,
            solution_hash="sha256:" + "2" * 64,
            source_commit=source_commit,
            source_tree=source_tree,
            freshness_hash="sha256:" + "3" * 64,
        )


def test_checked_in_descriptor_builds_all_provider_neutral_actor_registrations(
    durable_path, monkeypatch,
):
    tmp_path = durable_path
    context_source, source_commit, source_tree = _context_source(tmp_path)
    _protect_fixture_context_source(monkeypatch, context_source)
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
                "tools": [_git_tool()],
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
        context_source_root=context_source,
        output_root=output,
        signing_key_path=key_path,
        plan_commit="f" * 40,
        solution_hash="sha256:" + "2" * 64,
        source_commit=source_commit,
        source_tree=source_tree,
        freshness_hash="sha256:" + "3" * 64,
    )
    generation = output / receipt["generation"].removeprefix("sha256:")
    bundle = json.loads((generation / "bundle.json").read_text())
    assert sorted(bundle["actors"]) == ["claude", "codex", "deepseek"]
    expected_instruction_destinations = {
        "claude": "/home/claude/.claude/CLAUDE.md",
        "codex": "/home/codex/.codex/AGENTS.md",
        "deepseek": "/home/deepseek/.dsh/AGENTS.md",
    }
    expected_instruction_hash = "sha256:" + hashlib.sha256(
        (ROOT / "AGENTS.md").read_bytes()
    ).hexdigest()
    for actor, destination in expected_instruction_destinations.items():
        instruction = next(
            item for item in bundle["actors"][actor]["bindings"]
            if item["kind"] == "instruction"
        )
        assert instruction == {
            "kind": "instruction",
            "name": "agent-entry-point",
            "capability": "agent-entry-point",
            "source": "AGENTS.md",
            "destination": destination,
            "sha256": expected_instruction_hash,
        }
        bootstrap_binding = next(
            item for item in bundle["actors"][actor]["bindings"]
            if item["kind"] == "bootstrap"
        )
        bootstrap = json.loads(Path(bootstrap_binding["source"]).read_text())
        assert bootstrap["instructions"] == {
            "agent-entry-point": {
                "path": destination,
                "sha256": expected_instruction_hash,
            }
        }
    generated_catalog = json.loads(
        (generation / "environment-catalog.json").read_text()
    )
    assert generated_catalog["actors"]["codex"]["required_skills"] == [
        "tgw-plan",
        "tgw-review",
    ]
    codex_skills = {
        item["name"]: item
        for item in bundle["actors"]["codex"]["bindings"]
        if item["kind"] == "skill"
    }
    assert set(codex_skills) == {
        "tgw-plan",
        "tgw-review",
        "tgw-plan-legacy-codex-home",
        "tgw-review-legacy-codex-home",
    }
    for capability in ("tgw-plan", "tgw-review"):
        ordinary = codex_skills[capability]
        legacy = codex_skills[f"{capability}-legacy-codex-home"]
        assert "capability" not in ordinary
        assert legacy["capability"] == capability
        assert ordinary["source"] == legacy["source"]
        assert ordinary["sha256"] == legacy["sha256"]
        assert ordinary["destination"] == (
            f"/home/codex/.codex/skills/{capability}"
        )
        assert legacy["destination"] == (
            f"/home/codex/.tgw/codex-home/skills/{capability}"
        )
    codex_contract_binding = next(
        item
        for item in bundle["actors"]["codex"]["bindings"]
        if item["kind"] == "contract"
    )
    codex_contract = json.loads(
        Path(codex_contract_binding["source"]).read_text()
    )
    assert set(codex_contract["local"]["skills"]) == {
        "tgw-plan",
        "tgw-review",
    }
    codex_binding = next(item for item in bundle["actors"]["codex"]["bindings"] if item["kind"] == "mcp")
    codex_config = tomllib.loads(Path(codex_binding["source"]).read_text())
    assert codex_config["mcp_servers"]["tgw-context"]["args"] == ["--context-mcp"]
    assert codex_config["mcp_servers"]["tgw-context"]["env"] == {
        "TGW_ACTOR_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_ACTOR_CONTEXT_REGISTRATION": "stable-launcher-v1",
    }
    deepseek_binding = next(item for item in bundle["actors"]["deepseek"]["bindings"] if item["kind"] == "mcp")
    deepseek_patch = yaml.safe_load(Path(deepseek_binding["source"]).read_text())
    deepseek_config = deepseek_patch[0]["insert"][0]["config"]
    assert deepseek_binding["destination"] == "/home/deepseek/.dsh/cordis.patch.yml"
    assert deepseek_config["command"] == "/opt/TGW/tgw-lib/bin/tgw-actor"
    assert deepseek_config["args"] == ["--context-mcp"]
    assert deepseek_config["env"] == {
        "TGW_ACTOR_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_ACTOR_CONTEXT_REGISTRATION": "stable-launcher-v1",
    }


def test_builder_records_and_removes_owned_crash_stale_stage_before_reuse(
    durable_path,
):
    output = durable_path / "generations"
    output.mkdir()
    generation_body = {
        "schema": "tgw-actor-generation-identity/v1",
        "fixture": "crash-stale-stage",
    }
    generation = actor_generation_builder._hash(generation_body)
    generation_hex = generation.removeprefix("sha256:")
    final = output / generation_hex
    stage = output / f".{generation_hex}.next-4242-0123456789abcdef"
    debris = stage / "contracts/partial.json"
    debris.parent.mkdir(parents=True)
    debris.write_text('{"partial":true}\n')
    stage_state = stage.stat(follow_symlinks=False)

    assert actor_generation_builder._reconcile_generation_stages(
        output,
        final=final,
        generation=generation,
        generation_body=generation_body,
    ) is None
    assert not stage.exists()
    reconciliation_path = output / f".{generation_hex}.reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text())
    recorded = reconciliation["stages"][stage.name]
    assert recorded["device"] == stage_state.st_dev
    assert recorded["inode"] == stage_state.st_ino
    assert recorded["status"] == "REMOVED"
    assert recorded["manifest_sha256"].startswith("sha256:")

    # An attacker cannot recreate a recorded name and have it silently removed.
    stage.mkdir()
    with pytest.raises(
        actor_generation_builder.ActorGenerationError,
        match="abandoned stage identity differs",
    ):
        actor_generation_builder._reconcile_generation_stages(
            output,
            final=final,
            generation=generation,
            generation_body=generation_body,
        )
    assert stage.is_dir()


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
        )
