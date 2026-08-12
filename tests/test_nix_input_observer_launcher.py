import hashlib
import json
import os
from pathlib import Path

import pytest

from tgw.nix_input_observer_launcher import SCHEMA, LauncherError, load_descriptor


def _descriptor(tmp_path: Path) -> Path:
    python = tmp_path / "python"
    ip = tmp_path / "ip"
    launcher = tmp_path / "launcher"
    python.write_bytes(b"python")
    ip.write_bytes(b"ip")
    launcher.write_bytes(b"launcher")
    python.chmod(0o500)
    ip.chmod(0o500)
    launcher.chmod(0o500)
    value = {
        "schema": SCHEMA,
        "uid": os.getuid() or 1004,
        "gid": os.getgid() or 1004,
        "launcher": str(launcher),
        "python": str(python),
        "ip": str(ip),
        "launcher_sha256": "sha256:" + hashlib.sha256(b"launcher").hexdigest(),
        "python_sha256": "sha256:" + hashlib.sha256(b"python").hexdigest(),
        "ip_sha256": "sha256:" + hashlib.sha256(b"ip").hexdigest(),
        "sudo_rule_sha256": "sha256:" + "a" * 64,
        "observer_cgroup": "0::/system.slice/tgw-observer.scope",
    }
    path = tmp_path / "descriptor.json"
    path.write_text(json.dumps(value))
    path.chmod(0o400)
    return path


def test_descriptor_is_closed_and_holds_exact_tools(tmp_path, monkeypatch):
    path = _descriptor(tmp_path)
    descriptor, held = load_descriptor(path, expected_owner_uid=os.getuid())
    assert descriptor["schema"] == SCHEMA
    assert descriptor["_descriptor_sha256"].startswith("sha256:")
    assert set(held) == {"launcher", "python", "ip"}
    for fd in held.values():
        os.close(fd)


@pytest.mark.parametrize("mutation", ["extra", "digest", "relative", "mode"])
def test_descriptor_tampering_fails_closed(tmp_path, monkeypatch, mutation):
    path = _descriptor(tmp_path)
    value = json.loads(path.read_text())
    if mutation == "extra":
        value["command"] = "sh"
    elif mutation == "digest":
        value["python_sha256"] = "sha256:" + "0" * 64
    elif mutation == "relative":
        value["python"] = "python"
    else:
        path.chmod(0o666)
    if mutation != "mode":
        path.chmod(0o600)
        path.write_text(json.dumps(value))
        path.chmod(0o400)
    with pytest.raises(LauncherError):
        load_descriptor(path, expected_owner_uid=os.getuid())


def test_launcher_source_has_fixed_no_argument_privilege_drop_contract():
    source = Path("src/tgw/nix_input_observer_launcher.py").read_text()
    for required in ("CLONE_NEWNET", "os.setgroups([])", "os.setresgid", "os.setresuid", "PR_SET_NO_NEW_PRIVS", "PR_SET_SECUREBITS", "PR_CAPBSET_DROP", "PR_CAP_AMBIENT_CLEAR_ALL"):
        assert required in source
    assert "shell=True" not in source and "os.system" not in source
