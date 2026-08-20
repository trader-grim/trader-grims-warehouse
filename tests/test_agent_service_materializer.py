import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLERS = ROOT / "agent-services/installers"
sys.path.insert(0, str(INSTALLERS.parent))

import installers.materialize as materialize_module  # noqa: E402
from installers.materialize import InstallError, materialize, materialize_fleet  # noqa: E402


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
    return {"actor": actor, "status": "READY", "receipt_hash": "sha256:" + "a" * 64}


def test_fleet_materialization_is_all_or_rollback_and_never_activates(tmp_path, monkeypatch):
    codex_home, deepseek_home, project = tmp_path / "codex", tmp_path / "deepseek", tmp_path / "project"
    actors = {
        "codex": {"home": codex_home, "project": project},
        "deepseek": {"home": deepseek_home, "project": project},
    }
    contracts = {actor: _ready_contract(actor) for actor in actors}
    dry = materialize_fleet(actors, source_root=ROOT, contracts=contracts)
    assert dry["status"] == "PREPARED" and not codex_home.exists() and not deepseek_home.exists()
    applied = materialize_fleet(actors, source_root=ROOT, contracts=contracts, apply=True)
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
            source_root=ROOT, contracts=contracts, apply=True,
        )
    assert not (broken / ".codex/skills/tgw-plan").exists()


def test_fleet_refuses_quarantined_actor_before_any_write(tmp_path):
    actors = {"codex": {"home": tmp_path / "home", "project": tmp_path / "project"}}
    with pytest.raises(InstallError, match="not READY"):
        materialize_fleet(actors, source_root=ROOT, contracts={"codex": {"actor": "codex", "status": "QUARANTINED"}}, apply=True)
    assert not (tmp_path / "home").exists()
