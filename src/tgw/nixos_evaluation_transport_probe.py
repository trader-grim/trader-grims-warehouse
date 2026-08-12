"""Fixed, zero-effect SSH/sudo/Python transport preflight for reviewed evaluation."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from tgw.nixos_reviewed_evaluation import REMOTE_HOST, REMOTE_PYTHON, REMOTE_USER, SSH_EXECUTABLE

SCHEMA = "tgw-nixos-reviewed-evaluation-transport-probe/v1"
REMOTE_PROGRAM = (
    "import hashlib,json,platform,sys; b=sys.stdin.buffer.read(71); "
    "r=b.decode() if len(b)==71 else ''; "
    "d={'schema':'tgw-nixos-reviewed-evaluation-transport-probe/v1','request_hash':r,"
    "'reached':['ssh','sudo','python','stdin-frame'],'python_version':platform.python_version(),"
    "'remote_python_sha256':'sha256:'+hashlib.sha256(open(sys.executable,'rb').read()).hexdigest(),"
    "'forbidden_effects':{'scratch':False,'archive':False,'nix':False,'build':False,'activation':False,"
    "'profile_write':False,'home_db_write':False,'live_flake_write':False,'deployment':False}}; "
    "d['receipt_sha256']='sha256:'+hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest(); "
    "sys.stdout.write(json.dumps(d,sort_keys=True,separators=(',',':')))"
)


class TransportProbeError(ValueError):
    pass


def _sealed(content: bytes) -> int:
    fd = os.memfd_create("tgw-transport-probe-known-hosts", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.write(fd, content)
    os.lseek(fd, 0, os.SEEK_SET)
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
    return fd


def validate_transport_probe(value: Any, *, request_hash: str, remote_python_sha256: str) -> dict[str, Any]:
    fields = {"schema", "request_hash", "reached", "python_version", "remote_python_sha256", "forbidden_effects", "receipt_sha256"}
    if not isinstance(value, dict) or set(value) != fields or len(json.dumps(value, sort_keys=True, separators=(",", ":"))) > 4096:
        raise TransportProbeError("transport probe schema is invalid")
    if value["schema"] != SCHEMA or value["request_hash"] != request_hash or value["reached"] != ["ssh", "sudo", "python", "stdin-frame"]:
        raise TransportProbeError("transport probe binding is invalid")
    if value["remote_python_sha256"] != remote_python_sha256 or not re.fullmatch(r"\d+\.\d+\.\d+", value["python_version"]):
        raise TransportProbeError("transport probe Python identity is invalid")
    forbidden = {"scratch", "archive", "nix", "build", "activation", "profile_write", "home_db_write", "live_flake_write", "deployment"}
    if not isinstance(value["forbidden_effects"], dict) or set(value["forbidden_effects"]) != forbidden or any(value["forbidden_effects"].values()):
        raise TransportProbeError("transport probe claims a forbidden effect")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256")
    canonical = json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()
    if claimed != "sha256:" + hashlib.sha256(canonical).hexdigest():
        raise TransportProbeError("transport probe self-hash mismatch")
    return dict(value)


def run_transport_probe(*, known_hosts: Path, request_hash: str, ssh_sha256: str, known_hosts_sha256: str, remote_python_sha256: str) -> dict[str, Any]:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", request_hash):
        raise TransportProbeError("transport probe request hash is invalid")
    ssh_fd = os.open(SSH_EXECUTABLE, os.O_RDONLY | os.O_NOFOLLOW)
    hosts_fd = os.open(known_hosts, os.O_RDONLY | os.O_NOFOLLOW)
    sealed_fd = None
    try:
        ssh_meta, hosts_meta = os.fstat(ssh_fd), os.fstat(hosts_fd)
        if not stat.S_ISREG(ssh_meta.st_mode) or not stat.S_ISREG(hosts_meta.st_mode) or hosts_meta.st_mode & 0o022:
            raise TransportProbeError("transport probe local identities are unsafe")
        ssh_bytes = os.read(ssh_fd, ssh_meta.st_size)
        hosts_bytes = os.read(hosts_fd, hosts_meta.st_size)
        if "sha256:" + hashlib.sha256(ssh_bytes).hexdigest() != ssh_sha256 or "sha256:" + hashlib.sha256(hosts_bytes).hexdigest() != known_hosts_sha256:
            raise TransportProbeError("transport probe local identity mismatch")
        sealed_fd = _sealed(hosts_bytes)
        os.lseek(ssh_fd, 0, os.SEEK_SET)
        command = [
            f"/proc/self/fd/{ssh_fd}",
            "-F",
            "/dev/null",
            "-oBatchMode=yes",
            "-oClearAllForwardings=yes",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_fd}",
            "--",
            f"{REMOTE_USER}@{REMOTE_HOST}",
            "sudo",
            "-n",
            "--",
            REMOTE_PYTHON,
            "-I",
            "-c",
            REMOTE_PROGRAM,
        ]
        completed = subprocess.run(command, input=request_hash.encode(), capture_output=True, timeout=30, check=False, pass_fds=(ssh_fd, sealed_fd))
    finally:
        if sealed_fd is not None:
            os.close(sealed_fd)
        os.close(hosts_fd)
        os.close(ssh_fd)
    if completed.returncode != 0:
        raise TransportProbeError(
            "transport probe failed: return_code="
            + str(max(-255, min(255, completed.returncode)))
            + " stdout_sha256=sha256:"
            + hashlib.sha256(completed.stdout).hexdigest()
            + " stderr_sha256=sha256:"
            + hashlib.sha256(completed.stderr).hexdigest()
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise TransportProbeError("transport probe returned malformed JSON") from exc
    return validate_transport_probe(value, request_hash=request_hash, remote_python_sha256=remote_python_sha256)
