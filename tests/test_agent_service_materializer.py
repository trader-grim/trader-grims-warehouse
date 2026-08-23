import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.actor_contract import actor_contract_public_key, sign_actor_contract

ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ROOT / "agent-services/installers"
sys.path.insert(0, str(INSTALLERS.parent))

import installers.materialize as materialize_module  # noqa: E402
from installers.materialize import (  # noqa: E402
    InstallError,
    materialize,
    materialize_complete_actor_contracts,
    materialize_fleet,
    rollback_complete_actor_contracts,
    rollback_fleet,
)

SIGNER = Ed25519PrivateKey.from_private_bytes(b"z" * 32)
SIGNER_PUBLIC_KEY = actor_contract_public_key(SIGNER)


@pytest.mark.parametrize(
    ("skill", "role_heading"),
    (("tgw-plan", "## Establish the binding"), ("tgw-review", "## Classify the review")),
)
def test_governed_skills_surface_generation_status_before_role_work(
    skill,
    role_heading,
):
    text = (ROOT / f"agent-services/skills/{skill}/SKILL.md").read_text()
    normalized = " ".join(text.split())

    assert text.index("## Establish session generation") < text.index(role_heading)
    assert "call the installed `tgw_context_status` tool with no arguments" in normalized
    assert "`generation_status.line`" in normalized
    assert "before" in normalized.split("`generation_status.line`", 1)[1].split(
        role_heading.removeprefix("## "), 1
    )[0]
    assert "never blocks, delays, or overrides an explicit owner command" in normalized


def test_governed_skills_use_only_authority_bound_context_contracts():
    plan = (ROOT / "agent-services/skills/tgw-plan/SKILL.md").read_text()
    plan_normalized = " ".join(plan.split())
    positions = [
        plan.index(name)
        for name in (
            "tgw_context_status",
            "tgw_context_bundle",
            "tgw_context_plan_graph",
            "tgw_context_plan_source",
            "tgw_context_runbooks",
        )
    ]

    assert positions == sorted(positions)
    assert "Refuse direct filesystem or Git reads" in plan_normalized
    assert "as Plan authority or fallback" in plan_normalized
    assert "verify_plan_root.py" not in plan
    assert "/opt/TGW/library/plans" not in plan

    review = (ROOT / "agent-services/skills/tgw-review/SKILL.md").read_text()
    review_normalized = " ".join(review.split())
    assert review.index("tgw_context_status") < review.index("tgw_context_bundle")
    assert (
        "Inspect only the exact candidate and base furnished by the execution card"
        in review_normalized
    )
    assert "admitted inspection tool or source binding" in review_normalized
    assert "Never discover or substitute a local checkout" in review_normalized


@pytest.mark.parametrize(
    ("target", "destinations"),
    [
        ("codex", (".codex/skills/tgw-plan", ".codex/skills/tgw-review", ".codex/providers/promptcraft")),
        ("hermes", (".hermes/skills/tgw-plan", ".hermes/providers/promptcraft")),
    ],
)
def test_dry_run_is_write_free_then_apply_is_current(tmp_path, target, destinations):
    home = tmp_path / "home"
    project = tmp_path / "project"
    dry = materialize(target, home=home, project=project, source_root=ROOT)

    assert dry["ok"] is True
    assert [action["status"] for action in dry["actions"]] == ["WOULD_INSTALL"] * len(destinations)
    assert not home.exists()
    installed = materialize(target, home=home, project=project, source_root=ROOT, apply=True)
    assert [action["status"] for action in installed["actions"]] == ["INSTALLED"] * len(destinations)
    for relative in destinations:
        assert (home / relative).is_symlink()
    current = materialize(target, home=home, project=project, source_root=ROOT)
    assert [action["status"] for action in current["actions"]] == ["CURRENT"] * len(destinations)
    assert [action["source_digest"] for action in current["actions"]] == [
        action["source_digest"] for action in installed["actions"]
    ]


def test_claude_uses_native_home_store_without_loading_project_legacy(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".claude/skills/tgw-plan"
    legacy.mkdir(parents=True)
    marker = legacy / "SKILL.md"
    marker.write_text("legacy Claude policy\n")

    result = materialize("claude", home=home, project=project, source_root=ROOT, apply=True)

    assert result["ok"] is True
    assert result["legacy_held"] is False
    assert [action["status"] for action in result["actions"]] == ["INSTALLED"] * 3
    assert marker.read_text() == "legacy Claude policy\n"
    assert (home / ".claude/skills/tgw-plan").resolve() == ROOT / "agent-services/skills/tgw-plan"
    assert (home / ".claude/skills/tgw-review").resolve() == ROOT / "agent-services/skills/tgw-review"
    provider = home / ".claude/providers/promptcraft"
    assert provider.is_symlink()
    assert provider.resolve() == ROOT / "agent-services/providers/promptcraft"


def test_non_claude_conflict_fails_without_overwrite(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    conflict = home / ".codex/skills/tgw-plan"
    conflict.mkdir(parents=True)
    marker = conflict / "SKILL.md"
    marker.write_text("unrelated\n")

    result = materialize("codex", home=home, project=project, source_root=ROOT, apply=True)

    assert result["ok"] is False
    assert result["actions"][0]["status"] == "CONFLICT"
    assert result["actions"][1]["status"] == "HELD_CONFLICT"
    assert marker.read_text() == "unrelated\n"
    assert not (home / ".codex/providers/promptcraft").exists()


def test_isolated_worker_receives_only_hash_checked_card_path(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    result = materialize(
        "isolated-worker", home=home, project=project, source_root=ROOT, apply=True
    )

    assert [(item["capability"], item["status"]) for item in result["actions"]] == [
        ("promptcraft-card-handoff", "INSTALLED")
    ]
    handoff = project / ".tgw-worker/bin/promptcraft-handoff"
    assert handoff.is_symlink()
    assert handoff.resolve() == ROOT / "agent-services/providers/promptcraft/bin/promptcraft-handoff"
    assert not (project / ".tgw-worker/skills").exists()
    assert not (project / ".tgw-worker/providers").exists()


def test_cli_dry_run_json_uses_temp_roots_and_writes_nothing(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    completed = subprocess.run(
        [
            str(INSTALLERS / "materialize-agent-services"),
            "codex",
            "--home",
            str(home),
            "--project",
            str(project),
            "--source-root",
            str(ROOT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["schema"] == "tgw-agent-service-installation/v1"
    assert result["mode"] == "dry-run"
    assert not home.exists()
    assert not project.exists()


def test_installed_skill_symlink_passes_existing_digest_verifier(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    materialize("codex", home=home, project=project, source_root=ROOT, apply=True)
    canonical = ROOT / "agent-services/skills/tgw-plan"
    adapter = home / ".codex/skills/tgw-plan"

    completed = subprocess.run(
        [sys.executable, str(canonical / "scripts/check_adapters.py"), str(canonical), str(adapter)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["ok"] is True


def test_apply_failure_rolls_back_all_links_created_by_invocation(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = tmp_path / "project"
    real_symlink = materialize_module.os.symlink
    calls = 0

    def fail_second(source, destination, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-link failure")
        return real_symlink(source, destination, **kwargs)

    monkeypatch.setattr(materialize_module.os, "symlink", fail_second)
    with pytest.raises(OSError, match="injected"):
        materialize("codex", home=home, project=project, source_root=ROOT, apply=True)
    assert not (home / ".codex/skills/tgw-plan").exists()
    assert not (home / ".codex/providers/promptcraft").exists()


def _ready_contract(actor):
    body = {
        "schema": "tgw-actor-contract-receipt/v1", "status": "READY", "catalog_hash": "sha256:" + "a" * 64,
        "actor": actor, "profile": "development", "plan": {}, "code_graph": {}, "local": {},
        "diagnostics": [], "activation": "declarative-only",
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return sign_actor_contract(
        {**body, "receipt_hash": "sha256:" + hashlib.sha256(encoded).hexdigest()},
        signing_private_key=SIGNER,
    )


def test_fleet_materialization_is_all_or_rollback_and_never_activates(tmp_path, monkeypatch):
    codex_home, deepseek_home, project = tmp_path / "codex", tmp_path / "deepseek", tmp_path / "project"
    actors = {
        "codex": {"home": codex_home, "project": project},
        "deepseek": {"home": deepseek_home, "project": project},
    }
    contracts = {actor: _ready_contract(actor) for actor in actors}
    dry = materialize_fleet(actors, source_root=ROOT, contracts=contracts, trusted_contract_public_key=SIGNER_PUBLIC_KEY)
    assert dry["status"] == "PREPARED" and not codex_home.exists() and not deepseek_home.exists()
    applied = materialize_fleet(actors, source_root=ROOT, contracts=contracts, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True)
    assert applied["status"] == "MATERIALIZED_NOT_ACTIVATED"
    assert (codex_home / ".codex/skills/tgw-plan").is_symlink()
    assert (codex_home / ".codex/skills/tgw-review").is_symlink()
    assert (deepseek_home / ".dsh/skills/tgw-plan").is_symlink()
    assert (deepseek_home / ".dsh/skills/tgw-review").is_symlink()

    broken = tmp_path / "broken"
    real_symlink = materialize_module.os.symlink
    calls = 0
    def fail_second(source, destination, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected fleet failure")
        return real_symlink(source, destination, **kwargs)
    monkeypatch.setattr(materialize_module.os, "symlink", fail_second)
    with pytest.raises(OSError, match="fleet failure"):
        materialize_fleet(
            {"codex": {"home": broken, "project": project}, "deepseek": {"home": tmp_path / "other", "project": project}},
            source_root=ROOT, contracts=contracts, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        )
    assert not (broken / ".codex/skills/tgw-plan").exists()


def test_fleet_refuses_quarantined_actor_before_any_write(tmp_path):
    actors = {"codex": {"home": tmp_path / "home", "project": tmp_path / "project"}}
    contract = _ready_contract("codex")
    contract["status"] = "QUARANTINED"
    with pytest.raises(InstallError, match="not READY"):
        materialize_fleet(actors, source_root=ROOT, contracts={"codex": contract}, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True)
    assert not (tmp_path / "home").exists()


def test_fleet_refuses_forged_ready_contract_before_any_write(tmp_path):
    actors = {"codex": {"home": tmp_path / "home", "project": tmp_path / "project"}}
    contract = _ready_contract("codex")
    contract["signature"] = "A" * 88
    with pytest.raises(InstallError, match="not READY"):
        materialize_fleet(actors, source_root=ROOT, contracts={"codex": contract}, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True)
    assert not (tmp_path / "home").exists()


def test_fleet_replaces_old_generation_and_retains_exact_rollback_links(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    old_root = tmp_path / "old"
    old_root.mkdir()
    old_skill = old_root / "tgw-plan"
    old_skill.mkdir()
    destination = home / ".codex/skills/tgw-plan"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(old_skill, target_is_directory=True)
    result = materialize_fleet(
        {"codex": {"home": home, "project": project}}, source_root=ROOT,
        contracts={"codex": _ready_contract("codex")}, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True, replace_existing=True,
    )
    assert next(action for action in result["actors"][0]["actions"] if action["capability"] == "tgw-plan")["status"] == "REPLACED"
    assert destination.resolve() == ROOT / "agent-services/skills/tgw-plan"
    previous = home / ".codex/skills/.tgw-plan.tgw-w18-previous"
    assert previous.resolve() == old_skill
    rollback_fleet(result)
    assert destination.resolve() == old_skill


def test_fleet_rollback_refuses_a_newer_link_generation(tmp_path):
    home, project = tmp_path / "home", tmp_path / "project"
    result = materialize_fleet(
        {"codex": {"home": home, "project": project}}, source_root=ROOT,
        contracts={"codex": _ready_contract("codex")}, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
    )
    destination = home / ".codex/skills/tgw-plan"
    destination.unlink()
    newer = tmp_path / "newer"
    newer.mkdir()
    destination.symlink_to(newer, target_is_directory=True)
    with pytest.raises(InstallError, match="target changed"):
        rollback_fleet(result)


def test_fleet_replacement_failure_restores_old_generation(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    old_root = tmp_path / "old"
    old_root.mkdir()
    for name in ("tgw-plan", "tgw-review", "promptcraft"):
        (old_root / name).mkdir()
    for name in ("tgw-plan", "tgw-review", "promptcraft"):
        destination = home / (".codex/skills" if name != "promptcraft" else ".codex/providers") / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.symlink_to(old_root / name, target_is_directory=True)
    real_replace = materialize_module.os.replace
    calls = 0
    def fail_second_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("injected replacement failure")
        return real_replace(source, destination)
    monkeypatch.setattr(materialize_module.os, "replace", fail_second_replace)
    with pytest.raises(OSError, match="replacement failure"):
        materialize_fleet(
            {"codex": {"home": home, "project": project}}, source_root=ROOT,
            contracts={"codex": _ready_contract("codex")}, trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True, replace_existing=True,
        )
    for name in ("tgw-plan", "tgw-review", "promptcraft"):
        destination = home / (".codex/skills" if name != "promptcraft" else ".codex/providers") / name
        assert destination.resolve() == old_root / name


def test_fleet_refuses_replacement_target_changed_after_staging(tmp_path, monkeypatch):
    home, project = tmp_path / "home", tmp_path / "project"
    old = tmp_path / "old"
    old.mkdir()
    destination = home / ".codex/skills/tgw-plan"
    destination.parent.mkdir(parents=True)
    destination.symlink_to(old, target_is_directory=True)
    real_symlink = materialize_module.os.symlink
    calls = 0
    def replace_target_after_staging(source, target, **kwargs):
        nonlocal calls
        calls += 1
        result = real_symlink(source, target, **kwargs)
        if calls == 3:
            destination.unlink()
            destination.mkdir()
        return result
    monkeypatch.setattr(materialize_module.os, "symlink", replace_target_after_staging)
    with pytest.raises(InstallError, match="target changed"):
        materialize_fleet(
            {"codex": {"home": home, "project": project}}, source_root=ROOT,
            contracts={"codex": _ready_contract("codex")}, trusted_contract_public_key=SIGNER_PUBLIC_KEY,
            apply=True, replace_existing=True,
        )
    assert destination.is_dir()
    assert not (home / ".codex/skills/tgw-review").exists()
    assert not (home / ".codex/providers/promptcraft").exists()


def _complete_bundle(
    tmp_path, *, actor="codex", composite=False, alternate_store=False,
    instruction=False,
):
    source = tmp_path / "candidate"
    skill_plan = source / "skills/plan"
    skill_review = source / "skills/review"
    skill_plan.mkdir(parents=True)
    skill_review.mkdir(parents=True)
    (skill_plan / "SKILL.md").write_text("plan\n")
    (skill_review / "SKILL.md").write_text("review\n")
    files = {}
    if composite and actor == "codex":
        mcp_name = "mcp.toml"
        mcp_content = (
            '[mcp_servers."tgw-context"]\n'
            'command = "/home/codex/.local/bin/tgw-actor"\n'
            'args = ["--context-mcp"]\n'
        )
    elif composite and actor == "claude":
        mcp_name = "mcp.json"
        mcp_content = json.dumps({
            "mcpServers": {
                "tgw-context": {
                    "command": "/home/claude/.local/bin/tgw-actor",
                    "args": ["--context-mcp"],
                }
            }
        }) + "\n"
    elif composite and actor == "deepseek":
        mcp_name = "mcp.yml"
        mcp_content = (
            "- insert:\n"
            "    - id: tgw-context\n"
            "      name: '@deepseek-ai/dsh-mcp-client'\n"
            "      config:\n"
            "        serverName: tgw-context\n"
            "        transport: stdio\n"
            '        command: "/home/deepseek/.local/bin/tgw-actor"\n'
            "        args: ['--context-mcp']\n"
            "        failOnStartupError: true\n"
        )
    else:
        mcp_name = "mcp"
        mcp_content = '{"endpoint":"tgw-context"}\n'
    for name, content in {
        "launcher": "#!/bin/sh\nexit 73\n",
        mcp_name: mcp_content,
        "bootstrap": '{"status":"PASS"}\n',
    }.items():
        path = source / name
        path.write_text(content)
        files[name] = path
    files["mcp"] = files.pop(mcp_name)
    catalog = {"schema": "tgw-execution-environment-catalog/v3", "actors": {actor: {}}}
    catalog_path = source / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    files["catalog"] = catalog_path
    home, project = tmp_path / "home", tmp_path / "project"

    def file_hash(path):
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    actor_state_root = {
        "claude": ".claude",
        "codex": ".codex",
        "deepseek": ".dsh",
    }.get(actor, f".{actor}")
    if instruction:
        instruction_source = source / "AGENTS.md"
        instruction_source.write_text("# TGW agent entry point\n")
        files["instruction"] = instruction_source
        instruction_destination = {
            "claude": home / ".claude/CLAUDE.md",
            "codex": home / ".codex/AGENTS.md",
            "deepseek": home / ".dsh/AGENTS.md",
        }.get(actor, home / actor_state_root / "AGENTS.md")
        files["bootstrap"].write_text(json.dumps({
            "status": "PASS",
            "instructions": {
                "agent-entry-point": {
                    "path": str(instruction_destination),
                    "sha256": file_hash(instruction_source),
                }
            },
        }) + "\n")
    skill_root = f"{actor_state_root}/skills"
    if composite and actor == "claude":
        mcp_destination = (
            home / ".claude/.mcp.json" if alternate_store
            else home / ".claude.json"
        )
    elif composite and actor == "codex":
        mcp_destination = (
            home / ".tgw/codex-home/config.toml" if alternate_store
            else home / ".codex/config.toml"
        )
    elif composite and actor == "deepseek":
        mcp_destination = home / ".dsh/cordis.patch.yml"
    else:
        mcp_destination = home / actor_state_root / "tgw-context.json"
    launcher_destination = home / ".local/bin" / f"tgw-{actor}"
    bindings = []
    if instruction:
        bindings.append({
            "kind": "instruction",
            "name": "agent-entry-point",
            "capability": "agent-entry-point",
            "source": str(files["instruction"]),
            "destination": str(instruction_destination),
            "sha256": file_hash(files["instruction"]),
        })
    bindings.extend([
        {"kind": "skill", "name": "tgw-plan", "source": str(skill_plan), "destination": str(home / skill_root / "tgw-plan"), "sha256": materialize_module.tree_digest(skill_plan)},
        {"kind": "skill", "name": "tgw-review", "source": str(skill_review), "destination": str(home / skill_root / "tgw-review"), "sha256": materialize_module.tree_digest(skill_review)},
        {"kind": "mcp", "name": "tgw-context", "source": str(files["mcp"]), "destination": str(mcp_destination), "sha256": file_hash(files["mcp"])},
        {"kind": "launcher", "name": "launcher", "source": str(files["launcher"]), "destination": str(launcher_destination), "sha256": file_hash(files["launcher"])},
        {
            "kind": "bootstrap", "name": "bootstrap-receipt",
            "source": str(files["bootstrap"]),
            "destination": str(home / actor_state_root / "tgw-bootstrap.json"),
            "sha256": file_hash(files["bootstrap"]),
        },
        {
            "kind": "environment", "name": "environment-catalog",
            "source": str(files["catalog"]),
            "destination": str(home / actor_state_root / "tgw-environment.json"),
            "sha256": file_hash(files["catalog"]),
        },
    ])
    mcp_binding = [{
        "endpoint": "tgw-context", "source_sha256": file_hash(files["mcp"]),
        "destination": str(mcp_destination),
    }]
    local = {
        "bootstrap_receipt_hash": file_hash(files["bootstrap"]),
        "launcher": {"path": str(launcher_destination), "sha256": file_hash(files["launcher"])},
        "skills": {"tgw-plan": materialize_module.tree_digest(skill_plan), "tgw-review": materialize_module.tree_digest(skill_review)},
        "hooks": {},
        "mcp": {"endpoints": ["tgw-context"], "binding_hash": materialize_module._bundle_binding_hash(mcp_binding)},
    }
    canonical_catalog = json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    body = {
        "schema": "tgw-actor-contract-receipt/v1", "status": "READY",
        "catalog_hash": "sha256:" + hashlib.sha256(canonical_catalog).hexdigest(),
        "actor": actor, "profile": "development", "plan": {}, "code_graph": {},
        "local": local, "diagnostics": [], "activation": "declarative-only",
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    contract = sign_actor_contract(
        {**body, "receipt_hash": "sha256:" + hashlib.sha256(encoded).hexdigest()},
        signing_private_key=SIGNER,
    )
    contract_path = source / "actor-contract.json"
    contract_path.write_text(json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n")
    bindings.append({
        "kind": "contract", "name": "actor-contract", "source": str(contract_path),
        "destination": str(home / ".tgw/actor-contract.json"),
        "sha256": file_hash(contract_path),
    })
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1",
        "generation": "sha256:" + "b" * 64,
        "actors": {actor: {"home": str(home), "project": str(project), "bindings": bindings}},
    }
    return source, home, bundle, {actor: contract}


def _prepare_complete_apply(tmp_path, bundle, name="materialize"):
    """Create only the real actor/store parents and one durable journal parent."""

    for specification in bundle["actors"].values():
        Path(specification["home"]).mkdir(parents=True, exist_ok=True)
        Path(specification["project"]).mkdir(parents=True, exist_ok=True)
        for binding in specification["bindings"]:
            Path(binding["destination"]).parent.mkdir(parents=True, exist_ok=True)
    journal_root = tmp_path / "transaction-journals"
    journal_root.mkdir(exist_ok=True)
    return journal_root / f"{name}.json"


def test_complete_actor_contract_materializes_every_declared_boundary(tmp_path):
    source, home, bundle, contracts = _complete_bundle(tmp_path)
    dry = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY,
    )
    assert dry["status"] == "PREPARED"
    assert not home.exists()
    journal = _prepare_complete_apply(tmp_path, bundle)
    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )
    assert applied["status"] == "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert {item["kind"] for item in applied["bindings"]} == {
        "skill", "mcp", "launcher", "bootstrap", "environment", "contract",
    }
    assert all(Path(item["destination"]).is_symlink() for item in applied["bindings"])
    assert applied["activation"] == "required-in-current-quiet-refresh-transaction"


def test_complete_actor_contract_binds_exact_instruction_hash_and_symlink(tmp_path):
    source, home, bundle, contracts = _complete_bundle(
        tmp_path, instruction=True,
    )
    instruction = next(
        item for item in bundle["actors"]["codex"]["bindings"]
        if item["kind"] == "instruction"
    )
    expected_hash = "sha256:" + hashlib.sha256(
        (source / "AGENTS.md").read_bytes()
    ).hexdigest()
    assert instruction["sha256"] == expected_hash
    journal = _prepare_complete_apply(tmp_path, bundle, "instruction")
    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )
    destination = home / ".codex/AGENTS.md"
    assert destination.is_symlink()
    assert destination.resolve() == source / "AGENTS.md"
    receipt = next(
        item for item in applied["bindings"]
        if item["kind"] == "instruction"
    )
    assert receipt["capability"] == "agent-entry-point"
    assert receipt["sha256"] == expected_hash


def test_complete_actor_contract_refuses_unsafe_regular_instruction_file(
    tmp_path,
):
    source, home, bundle, contracts = _complete_bundle(
        tmp_path, instruction=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, "unsafe-instruction")
    destination = home / ".codex/AGENTS.md"
    destination.write_text("unmanaged instructions\n")
    destination.chmod(0o644)

    with pytest.raises(InstallError, match="destination conflict"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            replace_existing=True, transaction_journal_path=journal,
        )

    assert not destination.is_symlink()
    assert destination.read_text() == "unmanaged instructions\n"


@pytest.mark.skipif(
    os.geteuid() != 0,
    reason="root ownership is required for the protected instruction preimage",
)
def test_complete_actor_contract_replaces_and_rolls_back_protected_instruction(
    tmp_path,
):
    source, home, bundle, contracts = _complete_bundle(
        tmp_path, instruction=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, "protected-instruction")
    destination = home / ".codex/AGENTS.md"
    destination.write_text("protected predecessor\n")
    destination.chmod(0o444)

    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        replace_existing=True, transaction_journal_path=journal,
    )
    assert destination.is_symlink()
    assert destination.resolve() == source / "AGENTS.md"

    rollback_complete_actor_contracts(applied)
    assert destination.is_file() and not destination.is_symlink()
    assert destination.read_text() == "protected predecessor\n"
    assert destination.stat().st_uid == 0
    assert destination.stat().st_mode & 0o777 == 0o444


def test_complete_actor_contract_resolves_tracked_sources_inside_exact_release(tmp_path):
    source_root, _home, bundle, contracts = _complete_bundle(tmp_path)
    for actor in bundle["actors"].values():
        for binding in actor["bindings"]:
            binding["source"] = str(Path(binding["source"]).relative_to(source_root))
    prepared = materialize_complete_actor_contracts(
        bundle, source_root=source_root, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY,
    )
    assert prepared["status"] == "PREPARED"
    assert all(Path(item["source"]).is_absolute() for item in prepared["bindings"])


def test_complete_actor_contract_accepts_only_explicit_external_generation_artifacts(tmp_path):
    source_root, _home, bundle, contracts = _complete_bundle(tmp_path)
    generation_root = tmp_path / "generation-artifacts"
    generation_root.mkdir()
    external = generation_root / "environment.json"
    catalog_binding = next(
        item for item in bundle["actors"]["codex"]["bindings"] if item["kind"] == "environment"
    )
    external.write_bytes(Path(catalog_binding["source"]).read_bytes())
    catalog_binding["source"] = str(external)
    prepared = materialize_complete_actor_contracts(
        bundle, source_root=source_root, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY,
        additional_source_roots=(generation_root,),
    )
    assert prepared["status"] == "PREPARED"
    with pytest.raises(InstallError, match="escapes the candidate"):
        materialize_complete_actor_contracts(
            bundle, source_root=source_root, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY,
        )


def test_complete_actor_contract_refuses_source_drift_before_writes(tmp_path):
    source, home, bundle, contracts = _complete_bundle(tmp_path)
    (source / "mcp").write_text('{"endpoint":"forged"}\n')
    journal = _prepare_complete_apply(tmp_path, bundle)
    with pytest.raises(InstallError, match="digest"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            transaction_journal_path=journal,
        )
    assert not any(Path(item["destination"]).exists() for item in bundle["actors"]["codex"]["bindings"])


def test_complete_actor_contract_rollback_removes_one_new_generation(tmp_path):
    source, home, bundle, contracts = _complete_bundle(tmp_path)
    journal = _prepare_complete_apply(tmp_path, bundle)
    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )
    rollback_complete_actor_contracts(applied)
    assert not (home / ".codex/skills/tgw-plan").exists()
    assert not (home / ".codex/tgw-context.json").exists()


def test_complete_actor_transaction_resumes_exactly_after_one_applied_effect(
    tmp_path, monkeypatch,
):
    source, _home, bundle, contracts = _complete_bundle(tmp_path)
    journal = _prepare_complete_apply(tmp_path, bundle, "resume")
    real_apply = materialize_module._apply_effect
    calls = 0
    interrupted = False

    def interrupt_second(effect, desired):
        nonlocal calls, interrupted
        calls += 1
        if calls == 2 and not interrupted:
            interrupted = True
            raise OSError("injected materializer interruption")
        return real_apply(effect, desired)

    monkeypatch.setattr(materialize_module, "_apply_effect", interrupt_second)
    with pytest.raises(OSError, match="interruption"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            transaction_journal_path=journal,
        )

    partial = json.loads(journal.read_text())
    assert partial["status"] == "APPLYING"
    assert len(partial["completed"]) == 1
    resumed = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )
    assert resumed["status"] == "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert json.loads(journal.read_text())["status"] == "APPLIED"
    assert all(
        Path(binding["destination"]).is_symlink()
        for binding in bundle["actors"]["codex"]["bindings"]
    )


def test_complete_actor_transaction_rejects_destination_created_after_plan(
    tmp_path, monkeypatch,
):
    source, _home, bundle, contracts = _complete_bundle(tmp_path)
    journal = _prepare_complete_apply(tmp_path, bundle, "destination-cas")
    destination = Path(bundle["actors"]["codex"]["bindings"][0]["destination"])
    real_atomic_json = materialize_module._atomic_json
    injected = False

    def create_attacker_after_plan(path, value):
        nonlocal injected
        real_atomic_json(path, value)
        if value.get("status") == "PLANNED" and not injected:
            injected = True
            destination.write_text("attacker-owned destination\n")

    monkeypatch.setattr(materialize_module, "_atomic_json", create_attacker_after_plan)
    with pytest.raises(InstallError, match="changed concurrently"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            transaction_journal_path=journal,
        )
    assert destination.read_text() == "attacker-owned destination\n"
    assert not any(
        Path(binding["destination"]).is_symlink()
        for binding in bundle["actors"]["codex"]["bindings"]
    )


def test_complete_actor_transaction_rejects_destination_parent_swap_after_plan(
    tmp_path, monkeypatch,
):
    source, _home, bundle, contracts = _complete_bundle(tmp_path)
    journal = _prepare_complete_apply(tmp_path, bundle, "parent-cas")
    destination = Path(bundle["actors"]["codex"]["bindings"][0]["destination"])
    original_parent = destination.parent
    displaced_parent = original_parent.with_name(original_parent.name + "-displaced")
    real_atomic_json = materialize_module._atomic_json
    injected = False

    def swap_parent_after_plan(path, value):
        nonlocal injected
        real_atomic_json(path, value)
        if value.get("status") == "PLANNED" and not injected:
            injected = True
            original_parent.rename(displaced_parent)
            original_parent.mkdir()

    monkeypatch.setattr(materialize_module, "_atomic_json", swap_parent_after_plan)
    with pytest.raises(InstallError, match="parent identity changed"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            transaction_journal_path=journal,
        )
    assert list(original_parent.iterdir()) == []
    assert not (displaced_parent / destination.name).exists()


@pytest.mark.parametrize("actor", ["claude", "codex"])
def test_complete_actor_projects_real_composite_store_without_losing_unrelated_keys(
    tmp_path, actor,
):
    source, home, bundle, contracts = _complete_bundle(
        tmp_path, actor=actor, composite=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, f"{actor}-composite")
    mcp_binding = next(
        item for item in bundle["actors"][actor]["bindings"] if item["kind"] == "mcp"
    )
    destination = Path(mcp_binding["destination"])
    if actor == "claude":
        before = (
            json.dumps({
                "theme": "dark",
                "mcpServers": {"unrelated": {"command": "/bin/true"}},
            }, sort_keys=True) + "\n"
        ).encode()
    else:
        before = (
            'model = "gpt-5"\n\n'
            '[mcp_servers.unrelated]\n'
            'command = "/bin/true"\n'
        ).encode()
    destination.write_bytes(before)
    destination.chmod(0o640)
    original = destination.stat(follow_symlinks=False)

    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )
    assert not destination.is_symlink()
    projected = destination.stat(follow_symlinks=False)
    assert (projected.st_uid, projected.st_gid, projected.st_mode & 0o777) == (
        original.st_uid, original.st_gid, 0o640,
    )
    if actor == "claude":
        parsed = json.loads(destination.read_text())
        assert parsed["theme"] == "dark"
        assert parsed["mcpServers"]["unrelated"] == {"command": "/bin/true"}
        assert parsed["mcpServers"]["tgw-context"]["args"] == ["--context-mcp"]
    else:
        parsed = tomllib.loads(destination.read_text())
        assert parsed["model"] == "gpt-5"
        assert parsed["mcp_servers"]["unrelated"] == {"command": "/bin/true"}
        assert parsed["mcp_servers"]["tgw-context"]["args"] == ["--context-mcp"]
    mcp_receipt = next(item for item in applied["bindings"] if item["kind"] == "mcp")
    assert mcp_receipt["status"] == "PROJECTED"
    assert mcp_receipt["materialization"] in {"claude-user-json", "codex-user-toml"}

    rollback_complete_actor_contracts(applied)
    assert destination.read_bytes() == before
    assert destination.stat(follow_symlinks=False).st_mode & 0o777 == 0o640
    assert json.loads(journal.read_text())["status"] == "ROLLED_BACK"


@pytest.mark.parametrize("actor", ["claude", "codex"])
def test_complete_actor_projects_retained_alternate_store_target_only(
    tmp_path, actor,
):
    source, home, bundle, contracts = _complete_bundle(
        tmp_path, actor=actor, composite=True, alternate_store=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, f"{actor}-alternate")
    mcp_binding = next(
        item for item in bundle["actors"][actor]["bindings"] if item["kind"] == "mcp"
    )
    destination = Path(mcp_binding["destination"])
    expected_destination = (
        home / ".claude/.mcp.json" if actor == "claude"
        else home / ".tgw/codex-home/config.toml"
    )
    assert destination == expected_destination
    if actor == "claude":
        before = (
            '{"mcpServers":{"retained":{"command":"/bin/true"}},'
            '"projectSetting":"untouched"}\n'
        ).encode()
    else:
        before = (
            'approval_policy = "never"\n\n'
            '[mcp_servers.retained]\n'
            'command = "/bin/true"\n'
        ).encode()
    destination.write_bytes(before)

    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )

    assert destination.is_file() and not destination.is_symlink()
    if actor == "claude":
        projected = json.loads(destination.read_text())
        assert projected["projectSetting"] == "untouched"
        assert projected["mcpServers"]["retained"] == {"command": "/bin/true"}
        assert projected["mcpServers"]["tgw-context"]["args"] == ["--context-mcp"]
    else:
        projected = tomllib.loads(destination.read_text())
        assert projected["approval_policy"] == "never"
        assert projected["mcp_servers"]["retained"] == {"command": "/bin/true"}
        assert projected["mcp_servers"]["tgw-context"]["args"] == ["--context-mcp"]
    mcp_receipt = next(item for item in applied["bindings"] if item["kind"] == "mcp")
    assert mcp_receipt["status"] == "PROJECTED"
    assert mcp_receipt["materialization"] == (
        "claude-user-json" if actor == "claude" else "codex-user-toml"
    )

    rollback_complete_actor_contracts(applied)
    assert destination.read_bytes() == before


def test_complete_actor_projects_deepseek_target_only_without_rewriting_yaml(
    tmp_path,
):
    source, _home, bundle, contracts = _complete_bundle(
        tmp_path, actor="deepseek", composite=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, "deepseek-composite")
    mcp_binding = next(
        item
        for item in bundle["actors"]["deepseek"]["bindings"]
        if item["kind"] == "mcp"
    )
    destination = Path(mcp_binding["destination"])
    preserved_head = (
        "# retained top-level comment\n"
        "- remove:\n"
        "    - id: retired-unrelated # retained inline comment\n"
        "- insert:\n"
        "    - id: unrelated\n"
        "      name: custom-plugin\n"
        "      config:\n"
        "        transform: !!js/function >\n"
        "          function (value) { return value; }\n"
    ).encode()
    managed_preimage = (
        "- insert:\n"
        "    - id: tgw-context\n"
        "      name: legacy-client\n"
        "      config:\n"
        "        command: /legacy/launcher\n"
    ).encode()
    preserved_tail = (
        "# retained trailing comment\n"
        "- remove:\n"
        "    - id: another-unrelated\n"
    ).encode()
    before = preserved_head + managed_preimage + preserved_tail
    destination.write_bytes(before)
    destination.chmod(0o640)

    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        transaction_journal_path=journal,
    )

    projected = destination.read_bytes()
    assert destination.is_file() and not destination.is_symlink()
    assert preserved_head in projected
    assert preserved_tail in projected
    assert b"!!js/function" in projected
    assert b"retained inline comment" in projected
    assert projected.count(b"id: tgw-context") == 1
    assert b"legacy-client" not in projected
    assert b"@deepseek-ai/dsh-mcp-client" in projected
    assert b'command: "/home/deepseek/.local/bin/tgw-actor"' in projected
    mcp_receipt = next(item for item in applied["bindings"] if item["kind"] == "mcp")
    assert mcp_receipt["status"] == "PROJECTED"
    assert mcp_receipt["materialization"] == "deepseek-patch-yaml"

    rollback_complete_actor_contracts(applied)
    assert destination.read_bytes() == before
    assert destination.stat(follow_symlinks=False).st_mode & 0o777 == 0o640


def test_complete_actor_refuses_duplicate_deepseek_target_without_any_write(
    tmp_path,
):
    source, _home, bundle, contracts = _complete_bundle(
        tmp_path, actor="deepseek", composite=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, "deepseek-duplicate")
    mcp_binding = next(
        item
        for item in bundle["actors"]["deepseek"]["bindings"]
        if item["kind"] == "mcp"
    )
    destination = Path(mcp_binding["destination"])
    before = (
        "# ambiguous managed ownership\n"
        "- insert:\n"
        "    - id: tgw-context\n"
        "      name: first\n"
        "- insert:\n"
        "    - id: tgw-context\n"
        "      name: second\n"
    ).encode()
    destination.write_bytes(before)

    with pytest.raises(InstallError, match="duplicat"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            transaction_journal_path=journal,
        )

    assert destination.read_bytes() == before
    assert not journal.exists()
    assert not any(
        Path(item["destination"]).exists()
        for item in bundle["actors"]["deepseek"]["bindings"]
        if item is not mcp_binding
    )


def test_complete_actor_retires_legacy_fixed_composite_backup_outside_live_store(
    tmp_path,
):
    source, home, bundle, contracts = _complete_bundle(
        tmp_path, actor="codex", composite=True,
    )
    journal = _prepare_complete_apply(tmp_path, bundle, "legacy-retirement")
    mcp_binding = next(
        item for item in bundle["actors"]["codex"]["bindings"] if item["kind"] == "mcp"
    )
    destination = Path(mcp_binding["destination"])
    fixed_backup = destination.with_name(f".{destination.name}.tgw-w18-previous")
    old_store = tmp_path / "old-codex-config.toml"
    old_store.write_text(
        'model = "legacy"\n\n[mcp_servers."tgw-context"]\n'
        'command = "/legacy/launcher"\n'
    )
    fixed_backup.symlink_to(old_store)

    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        replace_existing=True, transaction_journal_path=journal,
    )
    transaction = json.loads(journal.read_text())
    assert transaction["status"] == "APPLIED"
    assert transaction["retired"] == [str(fixed_backup)]
    retained = Path(transaction["retirements"][0]["retained"])
    assert retained.parent != destination.parent
    assert retained.is_symlink() and retained.resolve() == old_store
    assert not fixed_backup.exists() and not fixed_backup.is_symlink()
    assert destination.is_file() and not destination.is_symlink()
    assert tomllib.loads(destination.read_text())["model"] == "legacy"
    assert tomllib.loads(destination.read_text())["mcp_servers"]["tgw-context"][
        "args"
    ] == ["--context-mcp"]

    rollback_complete_actor_contracts(applied)
    assert destination.is_symlink() and destination.resolve() == old_store
    assert not fixed_backup.exists() and not fixed_backup.is_symlink()


def test_complete_actor_retires_both_allowlisted_duplicate_skill_links(
    tmp_path,
):
    source, _home, bundle, contracts = _complete_bundle(tmp_path)
    journal = _prepare_complete_apply(tmp_path, bundle, "duplicate-skill-retirement")
    skill_binding = next(
        item
        for item in bundle["actors"]["codex"]["bindings"]
        if item["kind"] == "skill" and item["name"] == "tgw-plan"
    )
    destination = Path(skill_binding["destination"])
    fixed = destination.with_name(f".{destination.name}.tgw-w18-previous")
    preserved = destination.with_name(
        f".{destination.name}.tgw-w18-previous.pre-a531-preserved-20260823"
    )
    legacy_roots = {
        destination: tmp_path / "active-old-skill",
        fixed: tmp_path / "fixed-old-skill",
        preserved: tmp_path / "pre-a531-old-skill",
    }
    for link, target in legacy_roots.items():
        target.mkdir()
        (target / "SKILL.md").write_text(f"legacy {target.name}\n")
        link.symlink_to(target, target_is_directory=True)
    exact_links = {link: str(link.readlink()) for link in legacy_roots}

    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        replace_existing=True, transaction_journal_path=journal,
    )

    transaction = json.loads(journal.read_text())
    expected_retired = {str(fixed), str(preserved)}
    assert set(transaction["retired"]) == expected_retired
    retirements = {
        item["path"]: item for item in transaction["retirements"]
        if item["path"] in expected_retired
    }
    assert set(retirements) == expected_retired
    assert all(item["adopted_by_destination"] is False for item in retirements.values())
    assert all(
        Path(item["retained"]).parent != destination.parent
        and Path(item["retained"]).is_symlink()
        for item in retirements.values()
    )
    assert destination.is_symlink()
    assert destination.resolve() == Path(skill_binding["source"])
    assert not list(destination.parent.glob(f".{destination.name}.tgw-w18-previous*"))

    rollback_complete_actor_contracts(applied)
    for link, target in legacy_roots.items():
        assert link.is_symlink()
        assert str(link.readlink()) == exact_links[link]
        assert link.resolve() == target
    assert json.loads(journal.read_text())["status"] == "ROLLED_BACK"


def test_complete_actor_refuses_unknown_legacy_skill_suffix_without_binding_writes(
    tmp_path,
):
    source, _home, bundle, contracts = _complete_bundle(tmp_path)
    journal = _prepare_complete_apply(tmp_path, bundle, "unknown-skill-retirement")
    skill_binding = next(
        item
        for item in bundle["actors"]["codex"]["bindings"]
        if item["kind"] == "skill" and item["name"] == "tgw-plan"
    )
    destination = Path(skill_binding["destination"])
    unknown = destination.with_name(
        f".{destination.name}.tgw-w18-previous.pre-a531-preserved-unknown"
    )
    legacy_root = tmp_path / "unknown-old-skill"
    legacy_root.mkdir()
    (legacy_root / "SKILL.md").write_text("unknown legacy sibling\n")
    unknown.symlink_to(legacy_root, target_is_directory=True)
    exact_link = str(unknown.readlink())

    with pytest.raises(InstallError, match="not allowlisted"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
            replace_existing=True, transaction_journal_path=journal,
        )

    assert unknown.is_symlink() and str(unknown.readlink()) == exact_link
    assert not journal.exists()
    assert not any(
        Path(item["destination"]).exists()
        or Path(item["destination"]).is_symlink()
        for item in bundle["actors"]["codex"]["bindings"]
    )
