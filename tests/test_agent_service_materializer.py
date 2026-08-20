import hashlib
import json
import subprocess
import sys
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


def test_claude_legacy_skill_is_held_while_promptcraft_is_materialized(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    legacy = project / ".claude/skills/tgw-plan"
    legacy.mkdir(parents=True)
    marker = legacy / "SKILL.md"
    marker.write_text("legacy Claude policy\n")

    result = materialize("claude", home=home, project=project, source_root=ROOT, apply=True)

    assert result["ok"] is True
    assert result["legacy_held"] is True
    assert result["actions"][0]["status"] == "HELD_LEGACY"
    assert marker.read_text() == "legacy Claude policy\n"
    provider = project / ".claude/providers/promptcraft"
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


def _complete_bundle(tmp_path):
    source = tmp_path / "candidate"
    skill_plan = source / "skills/plan"
    skill_review = source / "skills/review"
    skill_plan.mkdir(parents=True)
    skill_review.mkdir(parents=True)
    (skill_plan / "SKILL.md").write_text("plan\n")
    (skill_review / "SKILL.md").write_text("review\n")
    files = {}
    for name, content in {
        "launcher": "#!/bin/sh\nexit 73\n",
        "mcp": '{"endpoint":"tgw-context"}\n',
        "bootstrap": '{"status":"PASS"}\n',
    }.items():
        path = source / name
        path.write_text(content)
        files[name] = path
    catalog = {"schema": "tgw-execution-environment-catalog/v3", "actors": {"codex": {}}}
    catalog_path = source / "catalog.json"
    catalog_path.write_text(json.dumps(catalog))
    files["catalog"] = catalog_path
    home, project = tmp_path / "home", tmp_path / "project"

    def file_hash(path):
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    bindings = [
        {"kind": "skill", "name": "tgw-plan", "source": str(skill_plan), "destination": str(home / ".codex/skills/tgw-plan"), "sha256": materialize_module.tree_digest(skill_plan)},
        {"kind": "skill", "name": "tgw-review", "source": str(skill_review), "destination": str(home / ".codex/skills/tgw-review"), "sha256": materialize_module.tree_digest(skill_review)},
        {"kind": "mcp", "name": "tgw-context", "source": str(files["mcp"]), "destination": str(home / ".codex/tgw-context.json"), "sha256": file_hash(files["mcp"])},
        {"kind": "launcher", "name": "launcher", "source": str(files["launcher"]), "destination": str(home / ".local/bin/tgw-codex"), "sha256": file_hash(files["launcher"])},
        {"kind": "bootstrap", "name": "bootstrap-receipt", "source": str(files["bootstrap"]), "destination": str(home / ".codex/tgw-bootstrap.json"), "sha256": file_hash(files["bootstrap"])},
        {"kind": "environment", "name": "environment-catalog", "source": str(files["catalog"]), "destination": str(home / ".codex/tgw-environment.json"), "sha256": file_hash(files["catalog"])},
    ]
    mcp_binding = [{
        "endpoint": "tgw-context", "source_sha256": file_hash(files["mcp"]),
        "destination": str(home / ".codex/tgw-context.json"),
    }]
    local = {
        "bootstrap_receipt_hash": file_hash(files["bootstrap"]),
        "launcher": {"path": str(home / ".local/bin/tgw-codex"), "sha256": file_hash(files["launcher"])},
        "skills": {"tgw-plan": materialize_module.tree_digest(skill_plan), "tgw-review": materialize_module.tree_digest(skill_review)},
        "hooks": {},
        "mcp": {"endpoints": ["tgw-context"], "binding_hash": materialize_module._bundle_binding_hash(mcp_binding)},
    }
    canonical_catalog = json.dumps(catalog, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    body = {
        "schema": "tgw-actor-contract-receipt/v1", "status": "READY",
        "catalog_hash": "sha256:" + hashlib.sha256(canonical_catalog).hexdigest(),
        "actor": "codex", "profile": "development", "plan": {}, "code_graph": {},
        "local": local, "diagnostics": [], "activation": "declarative-only",
    }
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    contract = sign_actor_contract(
        {**body, "receipt_hash": "sha256:" + hashlib.sha256(encoded).hexdigest()},
        signing_private_key=SIGNER,
    )
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1",
        "generation": "sha256:" + "b" * 64,
        "actors": {"codex": {"home": str(home), "project": str(project), "bindings": bindings}},
    }
    return source, home, bundle, {"codex": contract}


def test_complete_actor_contract_materializes_every_declared_boundary(tmp_path):
    source, home, bundle, contracts = _complete_bundle(tmp_path)
    dry = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY,
    )
    assert dry["status"] == "PREPARED"
    assert not home.exists()
    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
    )
    assert applied["status"] == "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED"
    assert {item["kind"] for item in applied["bindings"]} == {
        "skill", "mcp", "launcher", "bootstrap", "environment",
    }
    assert all(Path(item["destination"]).is_symlink() for item in applied["bindings"])
    assert applied["activation"] == "required-in-current-quiet-refresh-transaction"


def test_complete_actor_contract_refuses_source_drift_before_writes(tmp_path):
    source, home, bundle, contracts = _complete_bundle(tmp_path)
    (source / "mcp").write_text('{"endpoint":"forged"}\n')
    with pytest.raises(InstallError, match="digest"):
        materialize_complete_actor_contracts(
            bundle, source_root=source, contracts=contracts,
            trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
        )
    assert not home.exists()


def test_complete_actor_contract_rollback_removes_one_new_generation(tmp_path):
    source, home, bundle, contracts = _complete_bundle(tmp_path)
    applied = materialize_complete_actor_contracts(
        bundle, source_root=source, contracts=contracts,
        trusted_contract_public_key=SIGNER_PUBLIC_KEY, apply=True,
    )
    rollback_complete_actor_contracts(applied)
    assert not (home / ".codex/skills/tgw-plan").exists()
    assert not (home / ".codex/tgw-context.json").exists()
