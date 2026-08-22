import hashlib
import json
import subprocess
from pathlib import Path

from tgw.actor_host_bootstrap import HostPaths, install_actor_host, rollback_actor_host


def _fixture(root: Path):
    release = root / "release"
    unit_source = release / "config/environment/systemd/tgw-actor-fleet-provider.service"
    tmpfiles_source = release / "config/environment/tmpfiles.d/tgw-actor-host.conf"
    unit_source.parent.mkdir(parents=True)
    tmpfiles_source.parent.mkdir(parents=True)
    unit_source.write_text("[Service]\nExecStart=/exact/provider\n")
    tmpfiles_source.write_text("d /durable 0750 root root -\n")
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
    return paths


def test_actor_host_bootstrap_is_idempotent_and_rolls_back(durable_path):
    paths = _fixture(durable_path)
    paths.systemd_unit.parent.mkdir(parents=True)
    paths.tmpfiles_config.parent.mkdir(parents=True)
    paths.systemd_unit.write_text("prior unit\n")
    commands = []

    def run(arguments):
        commands.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    receipt = install_actor_host(
        "actor-host-one",
        paths=paths,
        runner=run,
        require_root=False,
    )
    assert receipt["status"] == "INSTALLED"
    assert paths.systemd_unit.read_text().startswith("[Service]")
    assert paths.tmpfiles_config.read_text().startswith("d /durable")
    assert install_actor_host(
        "actor-host-one",
        paths=paths,
        runner=run,
        require_root=False,
    ) == receipt
    rollback = rollback_actor_host(
        paths.receipt_root / "actor-host-one.json",
        paths=paths,
        runner=run,
        require_root=False,
    )
    assert rollback["status"] == "ROLLED_BACK"
    assert paths.systemd_unit.read_text() == "prior unit\n"
    assert not paths.tmpfiles_config.exists()
    assert any(command[-1] == "tgw-actor-fleet-provider.service" for command in commands)
