"""Fixed, no-argument privilege boundary for the zero-fetch Nix observer."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

DESCRIPTOR = Path("/etc/tgw/nix-input-observer-launcher.json")
SCHEMA = "tgw-nix-input-observer-launcher/v1"
PR_CAPBSET_DROP = 24
PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECUREBITS = 28
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4
SECBIT_NOROOT = 1
SECBIT_NOROOT_LOCKED = 2
SECBIT_NO_SETUID_FIXUP = 4
SECBIT_NO_SETUID_FIXUP_LOCKED = 8
CAP_LAST_CAP = 40
CLONE_NEWNET = 0x40000000


class LauncherError(RuntimeError):
    pass


def _digest_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return "sha256:" + digest.hexdigest()


def load_descriptor(path: Path = DESCRIPTOR, *, expected_owner_uid: int = 0) -> tuple[dict[str, object], dict[str, int]]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        stat = os.fstat(fd)
        if stat.st_uid != expected_owner_uid or stat.st_mode & 0o022 or not os.path.isfile(f"/proc/self/fd/{fd}") or stat.st_size > 16 * 1024:
            raise LauncherError("launcher descriptor ownership or mode is invalid")
        raw = os.read(fd, stat.st_size)
    finally:
        os.close(fd)
    value = json.loads(raw)
    expected = {"schema", "uid", "gid", "launcher", "python", "ip", "launcher_sha256", "python_sha256", "ip_sha256", "sudo_rule_sha256", "observer_cgroup"}
    if (
        not isinstance(value, dict)
        or set(value) != expected
        or value["schema"] != SCHEMA
        or not all(isinstance(value[key], int) and value[key] > 0 for key in ("uid", "gid"))
        or not all(isinstance(value[key], str) and value[key].startswith("/") for key in ("launcher", "python", "ip"))
        or not all(isinstance(value[key], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) for key in ("launcher_sha256", "python_sha256", "ip_sha256", "sudo_rule_sha256"))
        or not isinstance(value["observer_cgroup"], str)
        or not value["observer_cgroup"].startswith("0::/")
    ):
        raise LauncherError("launcher descriptor schema is invalid")
    value["_descriptor_sha256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    held: dict[str, int] = {}
    for name in ("launcher", "python", "ip"):
        tool = os.open(str(value[name]), os.O_RDONLY | os.O_NOFOLLOW)
        tool_stat = os.fstat(tool)
        if tool_stat.st_uid != expected_owner_uid or tool_stat.st_mode & 0o022 or not os.path.isfile(f"/proc/self/fd/{tool}") or _digest_fd(tool) != value[f"{name}_sha256"]:
            os.close(tool)
            raise LauncherError("launcher tool identity mismatch")
        held[name] = tool
    return value, held


def _drop_privileges(uid: int, gid: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    os.setgroups([])
    securebits = SECBIT_NOROOT | SECBIT_NOROOT_LOCKED | SECBIT_NO_SETUID_FIXUP | SECBIT_NO_SETUID_FIXUP_LOCKED
    if libc.prctl(PR_SET_SECUREBITS, securebits, 0, 0, 0) != 0:
        raise LauncherError("securebits could not be locked")
    for capability in range(CAP_LAST_CAP + 1):
        if libc.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) != 0:
            raise LauncherError("capability bounding set could not be cleared")
    if libc.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
        raise LauncherError("ambient capabilities could not be cleared")
    os.setresgid(gid, gid, gid)
    os.setresuid(uid, uid, uid)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise LauncherError("no_new_privs could not be established")


def launch(*, descriptor_path: Path = DESCRIPTOR, bootstrap: str, argv: list[str] | None = None) -> NoReturn:
    if argv if argv is not None else sys.argv[1:]:
        raise LauncherError("observer launcher accepts no arguments")
    descriptor, held = load_descriptor(descriptor_path)
    if Path("/proc/self/cgroup").read_text().strip() != descriptor["observer_cgroup"]:
        raise LauncherError("launcher cgroup identity mismatch")
    if hasattr(os, "unshare"):
        os.unshare(CLONE_NEWNET)
    elif ctypes.CDLL(None, use_errno=True).unshare(CLONE_NEWNET) != 0:
        raise LauncherError("network namespace creation failed")
    # The root boundary inspects only kernel network state. Helper/request/archive
    # bytes remain unread on stdin until after the permanent privilege drop.
    ip_path = f"/proc/{os.getpid()}/fd/{held['ip']}"
    links = json.loads(subprocess.run([ip_path, "-json", "link", "show"], capture_output=True, text=True, check=True, timeout=5).stdout)
    routes = json.loads(subprocess.run([ip_path, "-json", "route", "show"], capture_output=True, text=True, check=True, timeout=5).stdout)
    if routes or len(links) != 1 or links[0].get("ifname") != "lo" or "UP" in links[0].get("flags", []):
        raise LauncherError("new network namespace is not isolated")
    _drop_privileges(int(descriptor["uid"]), int(descriptor["gid"]))
    python_path = f"/proc/{os.getpid()}/fd/{held['python']}"
    env = {
        "HOME": "/var/empty",
        "NIX_REMOTE": "local",
        "PATH": "/run/current-system/sw/bin",
        "TGW_OBSERVER_DESCRIPTOR_SHA256": str(descriptor["_descriptor_sha256"]),
        "TGW_OBSERVER_SUDO_RULE_SHA256": str(descriptor["sudo_rule_sha256"]),
    }
    os.execve(python_path, [python_path, "-I", "-c", bootstrap], env)


def main() -> NoReturn:
    from tgw.nix_input_observation import BOOTSTRAP

    launch(bootstrap=BOOTSTRAP)


if __name__ == "__main__":
    main()
