#!/usr/bin/python3
"""Install the exact A3 local launcher prerequisite without exposing its key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import pwd
import stat
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path("/opt/TGW/runtime/a3-successor-v5")
LAUNCHER_PATH = RUNTIME_ROOT / "bin/tgw-a3-successor-local-launcher"
SOURCE_PATH = RUNTIME_ROOT / "share/nixos_a3_local_launcher.py"
CONFIG_PATH = Path("/etc/tgw/a3-successor-v5-launcher.json")
KEY_PATH = Path("/etc/tgw/a3-successor-attestation.key")
PUBLIC_PATH = Path("/etc/tgw/a3-successor-attestation.pub")
PREREQUISITE_PATH = Path("/etc/tgw/a3-successor-v5-launcher-prerequisite.json")
WRAPPER_KEY_PATH = Path("/etc/tgw/nix-observer-render-attestation.key")
_Q = 2**255 - 19
_D = -121665 * pow(121666, _Q - 2, _Q) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


class InstallError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


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
    return (
        (x1 * y2 + x2 * y1) * pow(1 + denominator, _Q - 2, _Q) % _Q,
        (y1 * y2 + x1 * x2) * pow(1 - denominator, _Q - 2, _Q) % _Q,
    )


def _scalarmult(point: tuple[int, int], scalar: int) -> tuple[int, int]:
    result = (0, 1)
    addend = point
    while scalar:
        if scalar & 1:
            result = _edwards(result, addend)
        addend = _edwards(addend, addend)
        scalar >>= 1
    return result


def public_key(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise InstallError("signing seed is not exactly 32 bytes")
    hashed = hashlib.sha512(seed).digest()
    scalar = 2**254 + sum(2**index * ((hashed[index // 8] >> (index & 7)) & 1) for index in range(3, 254))
    x, y = _scalarmult(_B, scalar)
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _pkcs8_pem(seed: bytes) -> bytes:
    der = bytes.fromhex("302e020100300506032b657004220420") + seed
    encoded = base64.b64encode(der)
    lines = [encoded[offset : offset + 64] for offset in range(0, len(encoded), 64)]
    return b"-----BEGIN PRIVATE KEY-----\n" + b"\n".join(lines) + b"\n-----END PRIVATE KEY-----\n"


def _ensure_directory(path: Path, mode: int) -> None:
    path.mkdir(mode=mode, parents=True, exist_ok=True)
    metadata = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise InstallError(f"directory authority differs: {path}")
    if stat.S_IMODE(metadata.st_mode) != mode:
        os.chmod(path, mode, follow_symlinks=False)
        metadata = path.stat(follow_symlinks=False)
        if stat.S_IMODE(metadata.st_mode) != mode:
            raise InstallError(f"directory mode could not be made exact: {path}")


def _complete_write(fd: int, raw: bytes) -> None:
    offset = 0
    while offset < len(raw):
        written = os.write(fd, raw[offset:])
        if written <= 0:
            raise InstallError("artifact write did not advance")
        offset += written


def _read_exact(path: Path, mode: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        raw = bytearray()
        while True:
            block = os.read(fd, 65_536)
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        named = os.stat(path, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != mode
            or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns)
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise InstallError(f"installed artifact identity differs: {path}")
        return bytes(raw)
    finally:
        os.close(fd)


def _publish(path: Path, raw: bytes, mode: int) -> None:
    if path.exists():
        if _read_exact(path, mode) != raw:
            raise InstallError(f"refusing to replace differing installed artifact: {path}")
        return
    temporary = path.parent / f".{path.name}.install-{os.getpid()}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _complete_write(fd, raw)
        os.fchmod(fd, mode)
        os.fchown(fd, 0, 0)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.link(temporary, path, follow_symlinks=False)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError:
        if _read_exact(path, mode) != raw:
            raise InstallError(f"raced installed artifact differs: {path}")
    finally:
        temporary.unlink(missing_ok=True)
    if _read_exact(path, mode) != raw:
        raise InstallError(f"installed artifact readback differs: {path}")


def _read_source(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        raw = bytearray()
        while True:
            block = os.read(fd, 65_536)
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns):
            raise InstallError("launcher source changed while held")
        return bytes(raw)
    finally:
        os.close(fd)


def install(source: Path) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise InstallError("launcher installation requires root")
    source_raw = _read_source(source)
    _ensure_directory(RUNTIME_ROOT, 0o755)
    _ensure_directory(LAUNCHER_PATH.parent, 0o755)
    _ensure_directory(SOURCE_PATH.parent, 0o755)
    _ensure_directory(CONFIG_PATH.parent, 0o755)
    _publish(LAUNCHER_PATH, source_raw, 0o555)
    _publish(SOURCE_PATH, source_raw, 0o444)
    if KEY_PATH.exists():
        seed = _read_exact(KEY_PATH, 0o400)
    else:
        seed = os.urandom(32)
        _publish(KEY_PATH, seed, 0o400)
    public = public_key(seed)
    _publish(PUBLIC_PATH, public, 0o444)
    wrapper_key = _pkcs8_pem(seed)
    _publish(WRAPPER_KEY_PATH, wrapper_key, 0o400)
    account = pwd.getpwnam("codex")
    config = {
        "schema": "tgw-nixos-a3-local-launcher-config/v1",
        "codex_uid": account.pw_uid,
        "codex_gid": account.pw_gid,
        "signing_key_path": str(KEY_PATH),
        "signing_key_sha256": sha(seed),
        "attestation_public_key_path": str(PUBLIC_PATH),
        "attestation_public_key_sha256": sha(public),
        "prerequisite_path": str(PREREQUISITE_PATH),
        "max_timeout_seconds": 1800,
        "max_output_bytes": 67_108_864,
        "max_processes": 32,
        "max_memory_bytes": 2_147_483_648,
    }
    config_raw = canonical(config)
    _publish(CONFIG_PATH, config_raw, 0o444)
    prerequisite = {
        "schema": "tgw-nixos-a3-local-launcher-prerequisite/v1",
        "status": "SATISFIED",
        "prerequisite": "EXTERNAL_PREREQUISITE",
        "launcher_source_sha256": sha(source_raw),
        "launcher_executable_sha256": sha(source_raw),
        "launcher_config_sha256": sha(config_raw),
        "attestation_public_key_sha256": sha(public),
        "packet_schema": "tgw-nixos-a3-local-launch-packet/v1",
        "response_schema": "tgw-nixos-a3-local-launch-response/v1",
        "attestation_schema": "tgw-nixos-a3-local-netns-attestation/v1",
        "raw_evidence_schema": "tgw-nixos-a3-raw-link-route-probes/v1",
        "raw_evidence_signed_by": sha(public),
    }
    prerequisite["receipt_sha256"] = sha(canonical(prerequisite))
    prerequisite_raw = canonical(prerequisite)
    _publish(PREREQUISITE_PATH, prerequisite_raw, 0o444)
    return {
        "schema": "tgw-nixos-a3-local-launcher-install-result/v1",
        "launcher": {"path": str(LAUNCHER_PATH), "sha256": sha(source_raw), "size": len(source_raw), "mode": 0o555},
        "source": {"path": str(SOURCE_PATH), "sha256": sha(source_raw), "size": len(source_raw), "mode": 0o444},
        "config": {"path": str(CONFIG_PATH), "sha256": sha(config_raw), "size": len(config_raw), "mode": 0o444},
        "public_key": {"path": str(PUBLIC_PATH), "sha256": sha(public), "size": len(public), "mode": 0o444},
        "signing_key_ref": "external-root-0400:" + sha(seed),
        "wrapper_signing_key_ref": "external-root-0400:" + sha(wrapper_key),
        "prerequisite": {"path": str(PREREQUISITE_PATH), "sha256": sha(prerequisite_raw), "size": len(prerequisite_raw), "mode": 0o444},
        "prerequisite_receipt_sha256": prerequisite["receipt_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--launcher-source", type=Path, required=True)
    arguments = parser.parse_args()
    print(canonical(install(arguments.launcher_source)).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
