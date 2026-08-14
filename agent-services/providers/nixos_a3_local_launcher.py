#!/usr/bin/python3
"""Root-only, no-argument launcher for the local A3 successor evaluator.

This file is deliberately standalone: the installed launcher consumes no
site-packages and does not import the mutable TGW source tree.  Its only input
is one bounded canonical JSON packet on stdin.  Every logical command runs as
the configured unprivileged identity in a fresh, loopback-only network
namespace.  The launcher returns a bounded response with a signed Ed25519
attestation over the namespace, negative probes, and child lifecycle facts.
"""

from __future__ import annotations

import base64
import ctypes
import fcntl
import hashlib
import json
import os
import selectors
import signal
import socket
import stat
import struct
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

PACKET_SCHEMA = "tgw-nixos-a3-local-launch-packet/v1"
RESPONSE_SCHEMA = "tgw-nixos-a3-local-launch-response/v1"
ATTESTATION_SCHEMA = "tgw-nixos-a3-local-netns-attestation/v1"
CONFIG_SCHEMA = "tgw-nixos-a3-local-launcher-config/v1"
RAW_EVIDENCE_SCHEMA = "tgw-nixos-a3-raw-link-route-probes/v1"
CONFIG_PATH = "/etc/tgw/a3-successor-v5-launcher.json"
CGROUP_ROOT = Path("/sys/fs/cgroup/tgw-a3-successor")
MAX_PACKET_BYTES = 1_048_576
MAX_DIAGNOSTIC_BYTES = 65_536
_HEX = set("0123456789abcdef")
_PROBES = ("direct", "dns", "private", "metadata")
_PACKET_KEYS = {
    "schema",
    "composition_sha256",
    "request_sha256",
    "launch_nonce",
    "attempt_id",
    "issued_at",
    "expires_at",
    "logical_argv",
    "cwd",
    "env",
    "timeout_seconds",
    "max_output_bytes",
    "pass_fds",
    "launcher_sha256",
    "config_sha256",
    "prerequisite_sha256",
    "packet_sha256",
}
_CONFIG_KEYS = {
    "schema",
    "codex_uid",
    "codex_gid",
    "signing_key_path",
    "signing_key_sha256",
    "attestation_public_key_path",
    "attestation_public_key_sha256",
    "prerequisite_path",
    "max_timeout_seconds",
    "max_output_bytes",
    "max_processes",
    "max_memory_bytes",
}


class LauncherError(RuntimeError):
    """A fail-closed launcher error safe to report as a bounded diagnostic."""


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32), ("permitted", ctypes.c_uint32), ("inheritable", ctypes.c_uint32)]


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest_raw(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    return digest_raw(canonical(value))


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise LauncherError(f"{label} has an invalid closed schema")
    return value


def _strict_int(value: Any, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise LauncherError(f"{label} is outside its closed integer bound")
    return value


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:") or any(char not in _HEX for char in value[7:]):
        raise LauncherError(f"{label} is not an exact SHA-256 identity")
    return value


def _read_all(fd: int, maximum: int, label: str) -> bytes:
    result = bytearray()
    while len(result) <= maximum:
        block = os.read(fd, min(65_536, maximum + 1 - len(result)))
        if not block:
            break
        result.extend(block)
    if len(result) > maximum:
        raise LauncherError(f"{label} exceeds its byte bound")
    return bytes(result)


def _safe_open(path_value: str, *, mode: int, label: str) -> tuple[int, os.stat_result, bytes]:
    path = Path(path_value)
    if not path.is_absolute() or ".." in path.parts:
        raise LauncherError(f"{label} path is not absolute and normalized")
    parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for component in path.parts[1:-1]:
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
            metadata = os.fstat(child_fd)
            observed_mode = stat.S_IMODE(metadata.st_mode)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or observed_mode & 0o022:
                os.close(child_fd)
                raise LauncherError(f"{label} has an unsafe ancestor")
            os.close(parent_fd)
            parent_fd = child_fd
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
        try:
            before = os.fstat(fd)
            raw = _read_all(fd, MAX_PACKET_BYTES, label)
            after = os.fstat(fd)
            named = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != 0
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != mode
                or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns)
                or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise LauncherError(f"{label} held identity is invalid")
            os.lseek(fd, 0, os.SEEK_SET)
            result = fd
            fd = -1
            return result, before, raw
        finally:
            if fd >= 0:
                os.close(fd)
    finally:
        os.close(parent_fd)


def _read_launcher() -> bytes:
    path = sys.argv[0]
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        metadata = os.fstat(fd)
        raw = _read_all(fd, MAX_PACKET_BYTES, "launcher executable")
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o555:
            raise LauncherError("launcher executable identity is invalid")
        return raw
    finally:
        os.close(fd)


# Minimal RFC 8032 Ed25519 implementation.  It signs only the small canonical
# attestation and keeps the root-only 32-byte seed in local memory.
_Q = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _Q - 2, _Q) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * pow(_D * y * y + 1, _Q - 2, _Q)
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q:
        x = x * _I % _Q
    return _Q - x if x & 1 else x


_BY = 4 * pow(5, _Q - 2, _Q) % _Q
_B = (_xrecover(_BY), _BY)


def _edwards(point: tuple[int, int], other: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = point
    x2, y2 = other
    denominator = _D * x1 * x2 * y1 * y2
    x3 = (x1 * y2 + x2 * y1) * pow(1 + denominator, _Q - 2, _Q)
    y3 = (y1 * y2 + x1 * x2) * pow(1 - denominator, _Q - 2, _Q)
    return x3 % _Q, y3 % _Q


def _scalarmult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _edwards(result, addend)
        addend = _edwards(addend, addend)
        scalar >>= 1
    return result


def _encode_point(point: tuple[int, int]) -> bytes:
    x, y = point
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _secret_scalar(seed: bytes) -> tuple[int, bytes]:
    hashed = hashlib.sha512(seed).digest()
    scalar = 2 ** 254 + sum(2**index * ((hashed[index // 8] >> (index & 7)) & 1) for index in range(3, 254))
    return scalar, hashed[32:]


def public_key(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise LauncherError("attestation signing seed must contain exactly 32 bytes")
    scalar, _ = _secret_scalar(seed)
    return _encode_point(_scalarmult(_B, scalar))


def sign(seed: bytes, message: bytes) -> bytes:
    scalar, prefix = _secret_scalar(seed)
    public = _encode_point(_scalarmult(_B, scalar))
    nonce = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    encoded_r = _encode_point(_scalarmult(_B, nonce))
    challenge = int.from_bytes(hashlib.sha512(encoded_r + public + message).digest(), "little") % _L
    return encoded_r + ((nonce + challenge * scalar) % _L).to_bytes(32, "little")


def _parse_packet(raw: bytes, config: Mapping[str, Any], launcher_raw: bytes, config_raw: bytes, prerequisite_raw: bytes) -> dict[str, Any]:
    try:
        packet = dict(_exact(json.loads(raw), _PACKET_KEYS, "launch packet"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LauncherError("launch packet is not canonical JSON") from exc
    if canonical(packet) != raw:
        raise LauncherError("launch packet bytes are not canonical")
    supplied_hash = packet.pop("packet_sha256")
    if supplied_hash != digest(packet):
        raise LauncherError("launch packet self-hash differs")
    packet["packet_sha256"] = supplied_hash
    if packet["schema"] != PACKET_SCHEMA:
        raise LauncherError("launch packet schema differs")
    for key in ("composition_sha256", "request_sha256", "launcher_sha256", "config_sha256", "prerequisite_sha256"):
        _sha(packet[key], f"packet {key}")
    if packet["launcher_sha256"] != digest_raw(launcher_raw) or packet["config_sha256"] != digest_raw(config_raw) or packet["prerequisite_sha256"] != digest_raw(prerequisite_raw):
        raise LauncherError("launch packet installed-artifact identity differs")
    if not isinstance(packet["launch_nonce"], str) or len(packet["launch_nonce"]) != 64 or any(char not in _HEX for char in packet["launch_nonce"]):
        raise LauncherError("launch nonce is invalid")
    if not isinstance(packet["attempt_id"], str) or not packet["attempt_id"].startswith("attempt:") or len(packet["attempt_id"]) != 72:
        raise LauncherError("attempt identity is invalid")
    argv = packet["logical_argv"]
    if not isinstance(argv, list) or not argv or len(argv) > 256 or any(not isinstance(item, str) or not item or "\x00" in item or len(item) > 16_384 for item in argv):
        raise LauncherError("logical argv is invalid")
    cwd = packet["cwd"]
    if not isinstance(cwd, str) or not cwd.startswith("/") or ".." in Path(cwd).parts:
        raise LauncherError("logical cwd is invalid")
    environment = packet["env"]
    if not isinstance(environment, dict) or len(environment) > 256 or list(environment) != sorted(environment) or any(
        not isinstance(key, str) or not key or "=" in key or "\x00" in key or not isinstance(value, str) or "\x00" in value or len(key) + len(value) > 65_536
        for key, value in environment.items()
    ):
        raise LauncherError("logical environment is invalid")
    timeout = _strict_int(packet["timeout_seconds"], "timeout", 1, config["max_timeout_seconds"])
    output = _strict_int(packet["max_output_bytes"], "output bound", 1, config["max_output_bytes"])
    pass_fds = packet["pass_fds"]
    if not isinstance(pass_fds, list) or len(pass_fds) > 256 or any(isinstance(fd, bool) or not isinstance(fd, int) or fd < 3 for fd in pass_fds) or len(set(pass_fds)) != len(pass_fds):
        raise LauncherError("pass-fd set is invalid")
    for fd in pass_fds:
        os.fstat(fd)
    packet["timeout_seconds"] = timeout
    packet["max_output_bytes"] = output
    return packet


def _load_config(config_path: str) -> tuple[dict[str, Any], bytes, bytes, bytes]:
    config_fd, _, config_raw = _safe_open(config_path, mode=0o444, label="launcher config")
    try:
        try:
            config = dict(_exact(json.loads(config_raw), _CONFIG_KEYS, "launcher config"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LauncherError("launcher config is not canonical JSON") from exc
        if canonical(config) != config_raw or config["schema"] != CONFIG_SCHEMA:
            raise LauncherError("launcher config bytes or schema differ")
        for key in ("codex_uid", "codex_gid"):
            _strict_int(config[key], key, 1, 2**31 - 1)
        _strict_int(config["max_timeout_seconds"], "max timeout", 1, 86_400)
        _strict_int(config["max_output_bytes"], "max output", 1, 1_073_741_824)
        _strict_int(config["max_processes"], "max processes", 1, 4096)
        _strict_int(config["max_memory_bytes"], "max memory", 16_777_216, 2**63 - 1)
        for key in ("signing_key_sha256", "attestation_public_key_sha256"):
            _sha(config[key], f"config {key}")
        key_fd, _, key_raw = _safe_open(config["signing_key_path"], mode=0o400, label="signing key")
        public_fd, _, public_raw = _safe_open(config["attestation_public_key_path"], mode=0o444, label="attestation public key")
        prerequisite_fd, _, prerequisite_raw = _safe_open(config["prerequisite_path"], mode=0o444, label="prerequisite receipt")
        try:
            if (
                digest_raw(key_raw) != config["signing_key_sha256"]
                or digest_raw(public_raw) != config["attestation_public_key_sha256"]
            ):
                raise LauncherError("launcher configured artifact digest differs")
            if public_key(key_raw) != public_raw:
                raise LauncherError("attestation public and private identities do not match")
            return config, config_raw, key_raw, prerequisite_raw
        finally:
            os.close(key_fd)
            os.close(public_fd)
            os.close(prerequisite_fd)
    finally:
        os.close(config_fd)


def _set_loopback_up() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        request = struct.pack("16sH14s", b"lo", 0, b"")
        flags = struct.unpack("16sH14s", fcntl.ioctl(sock.fileno(), 0x8913, request))[1]
        fcntl.ioctl(sock.fileno(), 0x8914, struct.pack("16sH14s", b"lo", flags | 0x1 | 0x40, b""))
    finally:
        sock.close()


def _netns_inode() -> int:
    return os.stat("/proc/self/ns/net").st_ino


def _network_evidence() -> tuple[dict[str, Any], bool, bool]:
    dev_raw = Path("/proc/net/dev").read_bytes()
    route4 = Path("/proc/net/route").read_bytes()
    route6 = Path("/proc/net/ipv6_route").read_bytes()
    interfaces = sorted(line.split(b":", 1)[0].strip().decode("ascii") for line in dev_raw.splitlines() if b":" in line)
    ipv4_routes = [line.decode("ascii") for line in route4.splitlines()[1:] if line.strip()]
    ipv6_routes = [line.decode("ascii") for line in route6.splitlines() if line.strip()]
    route_interfaces = [line.split()[-1] for line in ipv6_routes] + [line.split()[0] for line in ipv4_routes]
    evidence = {
        "schema": RAW_EVIDENCE_SCHEMA,
        "interfaces": interfaces,
        "ipv4_routes": ipv4_routes,
        "ipv6_routes": ipv6_routes,
    }
    return evidence, interfaces == ["lo"], all(name == "lo" for name in route_interfaces)


def _negative_probe(name: str) -> dict[str, Any]:
    started = time.monotonic_ns()
    connected = False
    error_name = "none"
    error_number: int | None = None
    target: Any
    try:
        if name == "dns":
            target = {"hostname": "a3-network-probe.invalid", "service": 443}
            socket.getaddrinfo(target["hostname"], target["service"], type=socket.SOCK_STREAM)
            connected = True
        else:
            host, port = {
                "direct": ("1.1.1.1", 443),
                "private": ("10.0.0.1", 443),
                "metadata": ("169.254.169.254", 80),
            }[name]
            target = {"host": host, "port": port}
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.25)
                probe.connect((host, port))
                connected = True
            finally:
                probe.close()
    except OSError as exc:
        error_name = type(exc).__name__
        error_number = exc.errno
    evidence = {
        "schema": RAW_EVIDENCE_SCHEMA,
        "probe": name,
        "target": target,
        "connected": connected,
        "error": error_name,
        "errno": error_number,
        "elapsed_ns": max(0, time.monotonic_ns() - started),
    }
    if connected:
        raise LauncherError(f"{name} isolation probe unexpectedly connected")
    return {"attempted": True, "connected": False, "evidence_sha256": digest(evidence)}


def _probe_set() -> dict[str, Any]:
    return {name: _negative_probe(name) for name in _PROBES}


def _proc_starttime(pid: int) -> int:
    raw = Path(f"/proc/{pid}/stat").read_text()
    end = raw.rfind(")")
    if end < 0:
        raise LauncherError("child stat record is malformed")
    return int(raw[end + 2 :].split()[19])


def _proc_privileges(pid: int) -> tuple[int, int, list[str], bool]:
    fields: dict[str, str] = {}
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    uid = int(fields["Uid"].split()[1])
    gid = int(fields["Gid"].split()[1])
    capabilities = [name for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb") if int(fields.get(name, "0"), 16) != 0]
    return uid, gid, capabilities, fields.get("NoNewPrivs") == "1"


def _group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_control(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC)
    try:
        raw = value.encode()
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise LauncherError(f"cgroup control write did not advance: {path.name}")
            offset += written
    finally:
        os.close(fd)


def _create_cgroup(packet: Mapping[str, Any], config: Mapping[str, Any]) -> Path:
    CGROUP_ROOT.mkdir(mode=0o755, exist_ok=True)
    root_metadata = CGROUP_ROOT.stat(follow_symlinks=False)
    if not stat.S_ISDIR(root_metadata.st_mode) or root_metadata.st_uid != 0:
        raise LauncherError("launcher cgroup root authority differs")
    controllers = set((CGROUP_ROOT / "cgroup.controllers").read_text().split())
    if not {"pids", "memory"} <= controllers:
        raise LauncherError("launcher cgroup controllers are unavailable")
    enabled = set((CGROUP_ROOT / "cgroup.subtree_control").read_text().split())
    if not {"pids", "memory"} <= enabled:
        _write_control(CGROUP_ROOT / "cgroup.subtree_control", "+pids +memory")
    group = CGROUP_ROOT / f"launch-{os.getpid()}-{packet['launch_nonce'][:16]}"
    group.mkdir(mode=0o755)
    try:
        _write_control(group / "pids.max", str(config["max_processes"]))
        _write_control(group / "memory.max", str(config["max_memory_bytes"]))
        if (group / "memory.swap.max").exists():
            _write_control(group / "memory.swap.max", "0")
        return group
    except BaseException:
        group.rmdir()
        raise


def _cgroup_populated(group: Path) -> bool:
    for line in (group / "cgroup.events").read_text().splitlines():
        key, value = line.split()
        if key == "populated":
            return value == "1"
    raise LauncherError("launcher cgroup omitted populated state")


def _remove_cgroup(group: Path, *, force: bool) -> None:
    if force and _cgroup_populated(group):
        _write_control(group / "cgroup.kill", "1")
    deadline = time.monotonic() + 2.0
    while _cgroup_populated(group) and time.monotonic() < deadline:
        time.sleep(0.01)
    if _cgroup_populated(group):
        raise LauncherError("logical cgroup could not be proven empty")
    group.rmdir()


def _cleanup_group_and_reap(pid: int, *, grace: float = 2.0) -> None:
    reaped = False
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if _group_exists(pid):
            try:
                os.killpg(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not reaped:
                try:
                    waited, _ = os.waitpid(pid, os.WNOHANG)
                    reaped = waited == pid
                except ChildProcessError:
                    reaped = True
            if reaped and not _group_exists(pid):
                return
            time.sleep(0.01)
    if not reaped:
        try:
            waited, _ = os.waitpid(pid, 0)
            reaped = waited == pid
        except ChildProcessError:
            reaped = True
    deadline = time.monotonic() + grace
    while _group_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.01)
    if not reaped or _group_exists(pid):
        raise LauncherError("logical process group could not be proven empty and reaped")


def _close_unadmitted_fds(admitted: set[int]) -> None:
    for item in os.listdir("/proc/self/fd"):
        try:
            fd = int(item)
        except ValueError:
            continue
        if fd > 2 and fd not in admitted:
            try:
                os.close(fd)
            except OSError:
                pass


def _launch(packet: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[int, bytes, bytes, dict[str, Any]]:
    cgroup = _create_cgroup(packet, config)
    stdout_r, stdout_w = os.pipe2(os.O_CLOEXEC)
    stderr_r, stderr_w = os.pipe2(os.O_CLOEXEC)
    ready_r, ready_w = os.pipe2(os.O_CLOEXEC)
    release_r, release_w = os.pipe2(os.O_CLOEXEC)
    pid = os.fork()
    if pid == 0:
        try:
            os.close(stdout_r)
            os.close(stderr_r)
            os.close(ready_r)
            os.close(release_w)
            os.setsid()
            os.dup2(stdout_w, 1)
            os.dup2(stderr_w, 2)
            os.close(stdout_w)
            os.close(stderr_w)
            libc = ctypes.CDLL(None, use_errno=True)
            for capability in range(64):
                if libc.prctl(24, capability, 0, 0, 0) != 0 and ctypes.get_errno() not in {0, 22}:  # PR_CAPBSET_DROP; EINVAL above kernel maximum
                    raise OSError(ctypes.get_errno(), f"PR_CAPBSET_DROP({capability})")
            os.setgroups([])
            os.setgid(config["codex_gid"])
            os.setuid(config["codex_uid"])
            capability_header = _CapHeader(0x20080522, 0)  # _LINUX_CAPABILITY_VERSION_3
            capability_data = (_CapData * 2)()
            if libc.capset(ctypes.byref(capability_header), ctypes.byref(capability_data)) != 0:
                raise OSError(ctypes.get_errno(), "capset(empty)")
            if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
                raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
            os.write(ready_w, b"R")
            os.close(ready_w)
            if os.read(release_r, 1) != b"G":
                os._exit(126)
            os.close(release_r)
            for fd in packet["pass_fds"]:
                os.set_inheritable(fd, True)
            _close_unadmitted_fds(set(packet["pass_fds"]))
            os.chdir(packet["cwd"])
            os.execvpe(packet["logical_argv"][0], packet["logical_argv"], packet["env"])
        except BaseException as exc:
            try:
                os.write(2, (type(exc).__name__ + ": " + str(exc)).encode()[:4096])
            except OSError:
                pass
            os._exit(126)
    os.close(stdout_w)
    os.close(stderr_w)
    os.close(ready_w)
    os.close(release_r)
    output = {stdout_r: bytearray(), stderr_r: bytearray()}
    returncode: int | None = None
    try:
        ready = os.read(ready_r, 1)
        if ready != b"R":
            _, status = os.waitpid(pid, 0)
            raise LauncherError(f"logical child did not reach the privilege boundary: {status}")
        starttime = _proc_starttime(pid)
        uid, gid, capabilities, no_new_privs = _proc_privileges(pid)
        if uid != config["codex_uid"] or gid != config["codex_gid"] or capabilities or not no_new_privs:
            raise LauncherError("logical child privilege drop is incomplete")
        _write_control(cgroup / "cgroup.procs", str(pid))
        os.write(release_w, b"G")
        os.close(release_w)
        release_w = -1
        selector = selectors.DefaultSelector()
        try:
            for fd in output:
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ)
            deadline = time.monotonic() + packet["timeout_seconds"]
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("logical child exceeded timeout")
                for key, _ in selector.select(min(remaining, 0.25)):
                    block = os.read(key.fd, 65_536)
                    if not block:
                        selector.unregister(key.fd)
                        continue
                    output[key.fd].extend(block)
                    if sum(len(value) for value in output.values()) > packet["max_output_bytes"]:
                        raise LauncherError("logical child exceeded output bound")
            waited_pid = 0
            status = 0
            while waited_pid == 0:
                if time.monotonic() >= deadline:
                    raise TimeoutError("logical child remained live after closing output")
                waited_pid, status = os.waitpid(pid, os.WNOHANG)
                if waited_pid == 0:
                    time.sleep(0.01)
            if waited_pid != pid:
                raise LauncherError("logical child reap identity differs")
            returncode = os.waitstatus_to_exitcode(status)
        finally:
            selector.close()
    except BaseException:
        try:
            _cleanup_group_and_reap(pid)
        finally:
            _remove_cgroup(cgroup, force=True)
        raise
    finally:
        os.close(ready_r)
        if release_w >= 0:
            os.close(release_w)
        for fd in output:
            os.close(fd)
    if _group_exists(pid):
        try:
            _cleanup_group_and_reap(pid)
        finally:
            _remove_cgroup(cgroup, force=True)
        raise LauncherError("logical child left process-group survivors")
    if _cgroup_populated(cgroup):
        _remove_cgroup(cgroup, force=True)
        raise LauncherError("logical child left cgroup descendants")
    _remove_cgroup(cgroup, force=False)
    if returncode is None:
        raise LauncherError("logical child return code is absent")
    facts = {
        "pid": pid,
        "starttime": starttime,
        "exe": f"/proc/{pid}/exe",
        "uid": uid,
        "gid": gid,
        "capabilities": capabilities,
        "no_new_privs": no_new_privs,
    }
    return returncode, bytes(output[stdout_r]), bytes(output[stderr_r]), facts


def _run(config_path: str) -> bytes:
    if os.geteuid() != 0 or len(sys.argv) != 1:
        raise LauncherError("launcher requires root and accepts no arguments")
    config, config_raw, signing_seed, prerequisite_raw = _load_config(config_path)
    launcher_raw = _read_launcher()
    packet_raw = _read_all(0, MAX_PACKET_BYTES, "launch packet")
    packet = _parse_packet(packet_raw, config, launcher_raw, config_raw, prerequisite_raw)
    os.unshare(os.CLONE_NEWNET)
    _set_loopback_up()
    started_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    start_inode = _netns_inode()
    before_network, lo_only_before, routes_only_lo_before = _network_evidence()
    if not lo_only_before or not routes_only_lo_before:
        raise LauncherError("fresh namespace is not loopback-only and route-isolated")
    pre_probes = _probe_set()
    returncode, stdout, stderr, child = _launch(packet, config)
    post_probes = _probe_set()
    after_network, lo_only_after, routes_only_lo_after = _network_evidence()
    end_inode = _netns_inode()
    if start_inode != end_inode or not lo_only_after or not routes_only_lo_after:
        raise LauncherError("network namespace changed during logical command")
    ended_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    attestation = {
        "schema": ATTESTATION_SCHEMA,
        "packet_sha256": packet["packet_sha256"],
        "composition_sha256": packet["composition_sha256"],
        "request_sha256": packet["request_sha256"],
        "launch_nonce": packet["launch_nonce"],
        "attempt_id": packet["attempt_id"],
        "issued_at": packet["issued_at"],
        "started_at": started_at,
        "ended_at": ended_at,
        "expires_at": packet["expires_at"],
        "netns": {
            "start_inode": start_inode,
            "end_inode": end_inode,
            "lo_only": True,
            "routes_empty": True,
            "link_sha256": digest({"before": before_network["interfaces"], "after": after_network["interfaces"]}),
            "route_sha256": digest(
                {
                    "before": {"ipv4": before_network["ipv4_routes"], "ipv6": before_network["ipv6_routes"]},
                    "after": {"ipv4": after_network["ipv4_routes"], "ipv6": after_network["ipv6_routes"]},
                }
            ),
        },
        "child": child,
        "probes": {"pre": pre_probes, "post": post_probes},
    }
    attestation["signature"] = "ed25519:" + sign(signing_seed, canonical(attestation)).hex()
    response = {
        "schema": RESPONSE_SCHEMA,
        "packet_sha256": packet["packet_sha256"],
        "returncode": returncode,
        "stdout_b64": base64.b64encode(stdout).decode("ascii"),
        "stderr_b64": base64.b64encode(stderr).decode("ascii"),
        "process_state": "REAPED",
        "cleanup": "REMOVED",
        "process": {
            "launcher_pid": os.getpid(),
            "child_pid": child["pid"],
            "child_starttime": child["starttime"],
            "child_exe": child["exe"],
            "child_reaped": True,
            "process_group_empty": True,
        },
        "attestation": attestation,
    }
    return canonical(response)


def main() -> int:
    try:
        response = _run(CONFIG_PATH)
        os.write(1, response)
        return 0
    except BaseException as exc:
        diagnostic = (type(exc).__name__ + ": " + str(exc)).encode()[:MAX_DIAGNOSTIC_BYTES]
        try:
            os.write(2, diagnostic)
        except OSError:
            pass
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
