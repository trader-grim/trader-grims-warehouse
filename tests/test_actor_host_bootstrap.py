import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.actor_host_bootstrap import ActorHostBootstrapError, HostPaths, install_actor_host, rollback_actor_host


def _fixture(root: Path):
    release = root / "release"
    managed = root / "managed"
    unit_source = release / "config/environment/systemd/tgw-actor-fleet-provider.service"
    tmpfiles_source = release / "config/environment/tmpfiles.d/tgw-actor-host.conf"
    unit_source.parent.mkdir(parents=True)
    tmpfiles_source.parent.mkdir(parents=True)
    unit_source.write_text("[Service]\nExecStart=/exact/provider\n")
    tmpfiles_source.write_text(f"d {managed} 2770 root root -\n")
    files = {
        path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (unit_source, tmpfiles_source)
    }
    (release / ".release-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tgw-release-manifest-v1",
                "commit": "a" * 40,
                "git_tree": "b" * 40,
                "files": files,
            }
        )
    )
    release.chmod(0o755)
    current = root / "actor-runtime/current"
    current.parent.mkdir(parents=True)
    current.symlink_to(release)
    paths = HostPaths(
        current=current,
        systemd_unit=root / "etc/systemd/system/tgw-actor-fleet-provider.service",
        tmpfiles_config=root / "etc/tmpfiles.d/tgw-actor-host.conf",
        receipt_root=root / "receipts",
        systemctl=root / "bin/systemctl",
        systemd_tmpfiles=root / "bin/systemd-tmpfiles",
    )
    return paths, managed


class _HostRunner:
    def __init__(
        self, managed: Path, *, enabled: bool = False, active: bool = False, legacy_empty: bool = False,
        fail_once: str | None = None,
    ):
        self.managed = managed
        self.enabled = enabled
        self.active = active
        self.legacy_empty = legacy_empty
        self.fail_once = fail_once
        self.commands: list[list[str]] = []

    def __call__(self, arguments):
        self.commands.append(arguments)
        action = arguments[1]
        if action == "is-enabled":
            stdout = "enabled\n" if self.enabled else ("" if self.legacy_empty else "disabled\n")
            return subprocess.CompletedProcess(arguments, 0 if self.enabled else 1, stdout, "")
        if action == "is-active":
            stdout = "active\n" if self.active else ("" if self.legacy_empty else "inactive\n")
            return subprocess.CompletedProcess(arguments, 0 if self.active else 3, stdout, "")
        if self.fail_once == action:
            self.fail_once = None
            return subprocess.CompletedProcess(arguments, 1, "", "fixture failure")
        if action == "--create":
            self.managed.mkdir(mode=0o2770, exist_ok=True)
            self.managed.chmod(0o2770)
        elif action == "enable":
            self.enabled = True
        elif action == "disable":
            self.enabled = False
        elif action == "start":
            self.active = True
        elif action == "stop":
            self.active = False
        return subprocess.CompletedProcess(arguments, 0, "", "")


def test_actor_host_bootstrap_is_idempotent_and_rolls_back(durable_path):
    paths, managed = _fixture(durable_path)
    paths.systemd_unit.parent.mkdir(parents=True)
    paths.tmpfiles_config.parent.mkdir(parents=True)
    paths.systemd_unit.write_text("prior unit\n")
    runner = _HostRunner(managed)

    receipt = install_actor_host(
        "actor-host-one",
        paths=paths,
        runner=runner,
        require_root=False,
    )
    assert receipt["status"] == "INSTALLED"
    assert paths.systemd_unit.read_text().startswith("[Service]")
    assert paths.tmpfiles_config.read_text().startswith(f"d {managed}")
    assert install_actor_host(
        "actor-host-one",
        paths=paths,
        runner=runner,
        require_root=False,
    ) == receipt
    rollback = rollback_actor_host(
        paths.receipt_root / "actor-host-one.json",
        paths=paths,
        runner=runner,
        require_root=False,
    )
    assert rollback["status"] == "ROLLED_BACK"
    assert paths.systemd_unit.read_text() == "prior unit\n"
    assert not paths.tmpfiles_config.exists()
    assert not managed.exists()
    assert not runner.enabled
    assert json.loads((paths.receipt_root / "actor-host-one.json").read_text())["status"] == "ROLLED_BACK"
    assert rollback_actor_host(
        paths.receipt_root / "actor-host-one.json", paths=paths, runner=runner, require_root=False,
    ) == rollback
    assert any(command[1] == "disable" for command in runner.commands)


def test_actor_host_bootstrap_prepared_receipt_recovers_partial_failure(durable_path):
    paths, managed = _fixture(durable_path)
    paths.systemd_unit.parent.mkdir(parents=True)
    paths.tmpfiles_config.parent.mkdir(parents=True)
    paths.systemd_unit.write_text("prior unit\n")
    runner = _HostRunner(managed, fail_once="daemon-reload")

    with pytest.raises(ActorHostBootstrapError, match="daemon-reload"):
        install_actor_host("actor-host-partial", paths=paths, runner=runner, require_root=False)

    receipt_path = paths.receipt_root / "actor-host-partial.json"
    assert json.loads(receipt_path.read_text())["status"] == "PREPARED"
    rollback = rollback_actor_host(receipt_path, paths=paths, runner=runner, require_root=False)
    assert rollback["status"] == "ROLLED_BACK"
    assert paths.systemd_unit.read_text() == "prior unit\n"
    assert not paths.tmpfiles_config.exists()
    assert not managed.exists()


def test_actor_host_bootstrap_prepared_receipt_without_artifacts_does_not_stop_service(durable_path):
    paths, managed = _fixture(durable_path)
    runner = _HostRunner(managed, fail_once="daemon-reload")

    with pytest.raises(ActorHostBootstrapError, match="daemon-reload"):
        install_actor_host("actor-host-prepared", paths=paths, runner=runner, require_root=False)
    paths.systemd_unit.unlink()
    paths.tmpfiles_config.unlink()
    managed.rmdir()
    runner.commands.clear()

    rollback_actor_host(
        paths.receipt_root / "actor-host-prepared.json", paths=paths, runner=runner, require_root=False,
    )
    assert not any(command[1] == "stop" for command in runner.commands)


def test_actor_host_bootstrap_reinstall_holds_if_enablement_drifted(durable_path):
    paths, managed = _fixture(durable_path)
    runner = _HostRunner(managed)
    install_actor_host("actor-host-drift", paths=paths, runner=runner, require_root=False)
    runner.enabled = False

    with pytest.raises(ActorHostBootstrapError, match="operation id collision"):
        install_actor_host("actor-host-drift", paths=paths, runner=runner, require_root=False)


def test_actor_host_bootstrap_accepts_legacy_empty_systemd_state(durable_path):
    paths, managed = _fixture(durable_path)
    runner = _HostRunner(managed, legacy_empty=True)

    receipt = install_actor_host("actor-host-legacy", paths=paths, runner=runner, require_root=False)
    assert receipt["before"]["service_enablement"] == {"state": "not-found", "enabled": False}
    assert receipt["before"]["service_activity"] == {"state": "inactive", "active": False}
    assert rollback_actor_host(
        paths.receipt_root / "actor-host-legacy.json", paths=paths, runner=runner, require_root=False,
    )["status"] == "ROLLED_BACK"


def test_actor_host_bootstrap_rejects_symlinked_receipt_path(durable_path):
    paths, managed = _fixture(durable_path)
    paths.receipt_root.mkdir(parents=True)
    outside = durable_path / "outside-receipt.json"
    outside.write_text("preserve me\n")
    (paths.receipt_root / "actor-host-linked.json").symlink_to(outside)

    with pytest.raises(ActorHostBootstrapError, match="receipt path is a symlink"):
        install_actor_host(
            "actor-host-linked", paths=paths, runner=_HostRunner(managed), require_root=False,
        )
    assert outside.read_text() == "preserve me\n"
