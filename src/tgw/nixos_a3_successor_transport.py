"""Sealed, local-on-tgw-prod transport for A3 successor evaluation.

Production does not accept an SSH endpoint or an injected callable.  A fixed,
root-owned, no-argument launcher receives one canonical packet on stdin.  The
launcher is an external installation prerequisite: it creates a fresh network
namespace, proves that it is loopback-only, drops to the admitted codex uid/gid
with no capabilities and ``no_new_privs``, and returns a signed observation.

This module defines and validates that source contract.  It does not install the
launcher and cannot silently substitute the fixture-only transport used by tests.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from tgw import nixos_a3_successor_remote as remote
from tgw.nixos_a3_successor_evaluation import (
    A3EvaluationError,
    A3KnownFailure,
    canonical,
    digest,
    validate_file_identity,
    validate_request,
)
from tgw.nixos_a3_successor_remote import Completed

TRANSPORT_SCHEMA = "tgw-nixos-a3-local-production-transport/v1"
COMPOSITION_SCHEMA = "tgw-nixos-a3-local-production-composition/v1"
LAUNCH_PACKET_SCHEMA = "tgw-nixos-a3-local-launch-packet/v1"
LAUNCH_RESPONSE_SCHEMA = "tgw-nixos-a3-local-launch-response/v1"
ATTESTATION_SCHEMA = "tgw-nixos-a3-local-netns-attestation/v1"
LAUNCH_EVIDENCE_SCHEMA = "tgw-nixos-a3-launch-evidence/v1"
REPLAY_CLAIM_SCHEMA = "tgw-nixos-a3-launch-replay-claim/v1"
REPLAY_CLAIM_REF_SCHEMA = "tgw-nixos-a3-launch-replay-claim-ref/v1"

_HEX64 = set("0123456789abcdef")
_SEAL = object()
_PROBE_NAMES = ("direct", "dns", "private", "metadata")
_RFC3339_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71 or any(char not in _HEX64 for char in value[7:]):
        raise A3EvaluationError(f"{label} is not an exact SHA-256 identity")
    return value


def _strict_int(value: Any, label: str, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise A3EvaluationError(f"{label} is not an integer in the closed range")
    return value


def _exact(value: Any, keys: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise A3EvaluationError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _validate_data_identity(value: Any, label: str) -> dict[str, Any]:
    item = dict(_exact(value, {"path", "sha256", "size", "uid", "gid", "mode"}, label))
    path = item["path"]
    if not isinstance(path, str) or not path.startswith("/") or ".." in Path(path).parts:
        raise A3EvaluationError(f"{label} path is not absolute and normalized")
    _sha(item["sha256"], f"{label}.sha256")
    _strict_int(item["size"], f"{label} size", minimum=1)
    _strict_int(item["uid"], f"{label} uid")
    _strict_int(item["gid"], f"{label} gid")
    _strict_int(item["mode"], f"{label} mode", maximum=0o7777)
    if item["mode"] & 0o022 or item["mode"] & 0o777 not in {0o400, 0o440, 0o444}:
        raise A3EvaluationError(f"{label} must be non-writable data")
    return item


def _validate_directory_identity(value: Any, label: str, *, trusted_uid: int = 0) -> dict[str, Any]:
    item = dict(_exact(value, {"path", "dev", "ino", "uid", "gid", "mode"}, label))
    if not isinstance(item["path"], str) or not item["path"].startswith("/") or ".." in Path(item["path"]).parts:
        raise A3EvaluationError(f"{label} path is invalid")
    for key in ("dev", "ino"):
        _strict_int(item[key], f"{label} {key}", minimum=1)
    for key in ("uid", "gid"):
        _strict_int(item[key], f"{label} {key}")
    _strict_int(item["mode"], f"{label} mode", maximum=0o7777)
    if item["uid"] != trusted_uid or item["mode"] != 0o700:
        raise A3EvaluationError(f"{label} must be trusted-owner mode 0700")
    return item


def _read_held(value: Mapping[str, Any], *, label: str, executable: bool) -> tuple[bytes, os.stat_result]:
    identity = validate_file_identity(value, label=label) if executable else _validate_data_identity(value, label)
    parent_path = Path(identity["path"]).parent
    parent_metadata = os.stat(parent_path, follow_symlinks=False)
    parent_identity = {
        "path": str(parent_path),
        "dev": parent_metadata.st_dev,
        "ino": parent_metadata.st_ino,
        "uid": parent_metadata.st_uid,
        "gid": parent_metadata.st_gid,
        "mode": stat.S_IMODE(parent_metadata.st_mode),
    }
    parent_fd = _open_live_directory(parent_identity, f"{label} parent", trusted_uid=identity["uid"], require_mode_0700=False)
    fd = os.open(Path(identity["path"]).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    try:
        before = os.fstat(fd)
        raw = bytearray()
        while len(raw) <= identity["size"]:
            block = os.read(fd, min(1024 * 1024, identity["size"] + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        named = os.stat(Path(identity["path"]).name, dir_fd=parent_fd, follow_symlinks=False)
    finally:
        os.close(fd)
        os.close(parent_fd)
    observed = {
        "sha256": digest(bytes(raw)),
        "size": len(raw),
        "uid": before.st_uid,
        "gid": before.st_gid,
        "mode": stat.S_IMODE(before.st_mode),
    }
    if (
        not stat.S_ISREG(before.st_mode)
        or any(observed[key] != identity[key] for key in observed)
        or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns)
        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise A3EvaluationError(f"{label} held identity changed")
    return bytes(raw), before


def _open_live_directory(identity: Mapping[str, Any], label: str, *, trusted_uid: int = 0, require_mode_0700: bool = True) -> int:
    if require_mode_0700:
        item = _validate_directory_identity(identity, label, trusted_uid=trusted_uid)
    else:
        item = dict(_exact(identity, {"path", "dev", "ino", "uid", "gid", "mode"}, label))
        if not isinstance(item["path"], str) or not item["path"].startswith("/") or ".." in Path(item["path"]).parts:
            raise A3EvaluationError(f"{label} path is invalid")
        for key in ("dev", "ino"):
            _strict_int(item[key], f"{label} {key}", minimum=1)
        for key in ("uid", "gid"):
            _strict_int(item[key], f"{label} {key}")
        _strict_int(item["mode"], f"{label} mode", maximum=0o7777)
    parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in Path(item["path"]).parts[1:]:
            before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            held = os.fstat(next_fd)
            after = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            mode = stat.S_IMODE(held.st_mode)
            sticky_root = held.st_uid == 0 and bool(mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(held.st_mode)
                or held.st_uid not in {0, trusted_uid}
                or (mode & 0o022 and not sticky_root)
                or (before.st_dev, before.st_ino) != (held.st_dev, held.st_ino)
                or (after.st_dev, after.st_ino) != (held.st_dev, held.st_ino)
            ):
                os.close(next_fd)
                raise A3EvaluationError(f"{label} component walk found an unsafe ancestor")
            os.close(parent_fd)
            parent_fd = next_fd
        metadata = os.fstat(parent_fd)
        if any(
            item[key] != observed
            for key, observed in {
                "dev": metadata.st_dev,
                "ino": metadata.st_ino,
                "uid": metadata.st_uid,
                "gid": metadata.st_gid,
                "mode": stat.S_IMODE(metadata.st_mode),
            }.items()
        ):
            raise A3EvaluationError(f"{label} live identity changed")
        final_mode = stat.S_IMODE(metadata.st_mode)
        if not require_mode_0700 and metadata.st_uid not in {0, trusted_uid}:
            raise A3EvaluationError(f"{label} final directory owner is unsafe")
        if not require_mode_0700 and final_mode & 0o022 and not (metadata.st_uid == 0 and final_mode & stat.S_ISVTX):
            raise A3EvaluationError(f"{label} final directory is writable")
        result = parent_fd
        parent_fd = -1
        return result
    finally:
        if parent_fd >= 0:
            os.close(parent_fd)


def _validate_live_directory(identity: Mapping[str, Any], label: str) -> Path:
    fd = _open_live_directory(identity, label)
    os.close(fd)
    item = _validate_directory_identity(identity, label)
    return Path(item["path"])


def _open_held_executable(identity: Mapping[str, Any], label: str, *, _after_open: Any = None) -> tuple[int, int, os.stat_result, bytes]:
    item = validate_file_identity(identity, label=label)
    parent_path = Path(item["path"]).parent
    parent_metadata = os.stat(parent_path, follow_symlinks=False)
    parent_identity = {
        "path": str(parent_path),
        "dev": parent_metadata.st_dev,
        "ino": parent_metadata.st_ino,
        "uid": parent_metadata.st_uid,
        "gid": parent_metadata.st_gid,
        "mode": stat.S_IMODE(parent_metadata.st_mode),
    }
    parent_fd = _open_live_directory(parent_identity, f"{label} parent", trusted_uid=item["uid"], require_mode_0700=False)
    try:
        fd = os.open(Path(item["path"]).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    except Exception:
        os.close(parent_fd)
        raise
    try:
        before = os.fstat(fd)
        if _after_open is not None:
            _after_open(Path(item["path"]))
        raw = os.read(fd, item["size"] + 1)
        os.lseek(fd, 0, os.SEEK_SET)
        named = os.stat(Path(item["path"]).name, dir_fd=parent_fd, follow_symlinks=False)
    except Exception as exc:
        os.close(fd)
        os.close(parent_fd)
        if isinstance(exc, A3EvaluationError):
            raise
        raise A3EvaluationError(f"{label} held/named execution identity could not be proven") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or len(raw) != item["size"]
        or digest(raw) != item["sha256"]
        or before.st_uid != item["uid"]
        or before.st_gid != item["gid"]
        or stat.S_IMODE(before.st_mode) != item["mode"]
        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    ):
        os.close(fd)
        os.close(parent_fd)
        raise A3EvaluationError(f"{label} exact held execution identity mismatch")
    return parent_fd, fd, before, raw


def _observe_current_cas(identity: Mapping[str, Any], expected: str, *, _after_open: Any = None) -> dict[str, Any]:
    """Execute the held, no-argument, read-only current-system observer."""
    parent_fd, fd, before, observer_raw = _open_held_executable(identity, "current-CAS observer")
    try:
        if _after_open is not None:
            _after_open(Path(identity["path"]))
        process = subprocess.Popen(
            [f"/proc/{os.getpid()}/fd/{fd}"],
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": ""},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(fd,),
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired as exc:
            _terminate_group(process)
            raise A3EvaluationError("current-CAS observer timed out") from exc
        after = os.fstat(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        after_raw = os.read(fd, identity["size"] + 1)
        try:
            named = os.stat(Path(identity["path"]).name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            raise A3EvaluationError("current-CAS observer named identity disappeared") from exc
    finally:
        os.close(fd)
        os.close(parent_fd)
    if (
        process.returncode != 0
        or len(stdout) + len(stderr) > 4096
        or stderr
        or stdout != (expected + "\n").encode()
        or after_raw != observer_raw
        or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns)
        or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise A3EvaluationError("current-CAS observer did not reproduce the exact admitted state")
    return {"returncode": process.returncode, "stdout_sha256": digest(stdout), "stderr_sha256": digest(stderr)}


class StepFailure(A3KnownFailure):
    """A bounded subprocess failure with known process-group state."""

    def __init__(
        self,
        message: str,
        *,
        step: str,
        returncode: int | None,
        stdout: bytes,
        stderr: bytes,
        process_state: str,
        cleanup: str,
    ) -> None:
        super().__init__(
            message,
            stage="local-launcher",
            step=step,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            cleanup=cleanup,
        )
        self.process_state = process_state


def _group_empty(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _wait_group_empty(pgid: int, deadline: float) -> bool:
    while time.monotonic() < deadline:
        if _group_empty(pgid):
            return True
        time.sleep(0.01)
    return _group_empty(pgid)


def _terminate_group(process: subprocess.Popen[bytes], *, grace_seconds: float = 2.0) -> tuple[int | None, str, str]:
    """Terminate and reap the leader, then prove the entire group is empty."""
    pgid = process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        returncode = process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            returncode = process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            return None, "UNKNOWN", "UNKNOWN"
    if _wait_group_empty(pgid, time.monotonic() + grace_seconds):
        return returncode, "REAPED_GROUP_EMPTY", "REMOVED"
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if _wait_group_empty(pgid, time.monotonic() + grace_seconds):
        return returncode, "REAPED_GROUP_EMPTY", "REMOVED"
    return returncode, "UNKNOWN", "UNKNOWN"


def _finish_group(process: subprocess.Popen[bytes], *, grace_seconds: float = 2.0) -> tuple[int, str, str]:
    """After normal leader exit, kill any surviving descendants and prove ESRCH."""
    returncode = process.wait()
    if _group_empty(process.pid):
        return returncode, "REAPED_GROUP_EMPTY", "REMOVED"
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if _wait_group_empty(process.pid, time.monotonic() + grace_seconds):
        return returncode, "REAPED_GROUP_EMPTY", "REMOVED"
    return returncode, "UNKNOWN", "UNKNOWN"


def _stream_bounded(
    process: subprocess.Popen[bytes],
    *,
    input_bytes: bytes,
    timeout: int,
    max_output: int,
) -> tuple[int, bytes, bytes, str]:
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    try:
        process.stdin.write(input_bytes)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        process.stdin.close()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                returncode, process_state, cleanup = _terminate_group(process)
                raise StepFailure(
                    "fixed local launcher timed out",
                    step="timeout",
                    returncode=returncode,
                    stdout=bytes(output["stdout"]),
                    stderr=bytes(output["stderr"]),
                    process_state=process_state,
                    cleanup=cleanup,
                )
            for key, _ in selector.select(0.05):
                block = os.read(key.fd, 65536)
                if not block:
                    selector.unregister(key.fileobj)
                    continue
                output[key.data].extend(block)
                if len(output["stdout"]) + len(output["stderr"]) > max_output:
                    returncode, process_state, cleanup = _terminate_group(process)
                    raise StepFailure(
                        "fixed local launcher exceeded output bound",
                        step="output-bound",
                        returncode=returncode,
                        stdout=bytes(output["stdout"][:max_output]),
                        stderr=bytes(output["stderr"][:max_output]),
                        process_state=process_state,
                        cleanup=cleanup,
                    )
    finally:
        selector.close()
    returncode, process_state, cleanup = _finish_group(process)
    if cleanup != "REMOVED":
        raise StepFailure(
            "local launcher process group could not be proven empty",
            step="process-group",
            returncode=returncode,
            stdout=bytes(output["stdout"]),
            stderr=bytes(output["stderr"]),
            process_state=process_state,
            cleanup=cleanup,
        )
    return returncode, bytes(output["stdout"]), bytes(output["stderr"]), process_state


def _probe_set(value: Any, label: str) -> Mapping[str, Any]:
    probes = _exact(value, set(_PROBE_NAMES), label)
    for name in _PROBE_NAMES:
        probe = _exact(probes[name], {"attempted", "connected", "evidence_sha256"}, f"{label}.{name}")
        if probe["attempted"] is not True or probe["connected"] is not False:
            raise A3EvaluationError(f"{label}.{name} is not negative evidence")
        _sha(probe["evidence_sha256"], f"{label}.{name}.evidence")
    return probes


def _parse_rfc3339(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise A3EvaluationError(f"{label} is not an RFC3339 UTC timestamp")
    try:
        parsed = datetime.strptime(value, _RFC3339_FORMAT).replace(tzinfo=UTC)
    except ValueError as exc:
        raise A3EvaluationError(f"{label} is not an RFC3339 UTC timestamp") from exc
    if parsed.strftime(_RFC3339_FORMAT) != value:
        raise A3EvaluationError(f"{label} is not canonical RFC3339 UTC")
    return parsed


def validate_launcher_attestation(
    value: Any,
    *,
    packet_sha256: str,
    composition_sha256: str,
    request_sha256: str,
    launch_nonce: str,
    attempt_id: str,
    issued_at: str,
    expires_at: str,
    public_key_raw: bytes,
    uid: int,
    gid: int,
    now: datetime | None = None,
    max_duration_seconds: int = 1800,
) -> dict[str, Any]:
    attestation = dict(
        _exact(
            value,
            {
                "schema",
                "packet_sha256",
                "composition_sha256",
                "request_sha256",
                "launch_nonce",
                "attempt_id",
                "issued_at",
                "started_at",
                "ended_at",
                "expires_at",
                "netns",
                "child",
                "probes",
                "signature",
            },
            "launcher attestation",
        )
    )
    if (
        attestation["schema"] != ATTESTATION_SCHEMA
        or attestation["packet_sha256"] != packet_sha256
        or attestation["composition_sha256"] != composition_sha256
        or attestation["request_sha256"] != request_sha256
        or attestation["launch_nonce"] != launch_nonce
        or attestation["attempt_id"] != attempt_id
        or attestation["issued_at"] != issued_at
        or attestation["expires_at"] != expires_at
    ):
        raise A3EvaluationError("launcher attestation binding mismatch")
    issued = _parse_rfc3339(attestation["issued_at"], "launcher issued_at")
    started = _parse_rfc3339(attestation["started_at"], "launcher started_at")
    ended = _parse_rfc3339(attestation["ended_at"], "launcher ended_at")
    expires = _parse_rfc3339(attestation["expires_at"], "launcher expires_at")
    observed_now = datetime.now(UTC) if now is None else now.astimezone(UTC)
    skew = timedelta(seconds=5)
    if not issued <= started <= ended <= expires or expires - issued > timedelta(seconds=max_duration_seconds) or observed_now < issued - skew or observed_now > expires + skew:
        raise A3EvaluationError("launcher attestation is stale, future-dated, or outside its duration")
    netns = _exact(attestation["netns"], {"start_inode", "end_inode", "lo_only", "routes_empty", "link_sha256", "route_sha256"}, "launcher netns")
    if (
        isinstance(netns["start_inode"], bool)
        or not isinstance(netns["start_inode"], int)
        or netns["start_inode"] <= 0
        or netns["end_inode"] != netns["start_inode"]
        or netns["lo_only"] is not True
        or netns["routes_empty"] is not True
    ):
        raise A3EvaluationError("launcher did not prove one loopback-only route-free namespace")
    _sha(netns["link_sha256"], "launcher link evidence")
    _sha(netns["route_sha256"], "launcher route evidence")
    child = _exact(attestation["child"], {"pid", "starttime", "exe", "uid", "gid", "capabilities", "no_new_privs"}, "launcher child")
    if (
        isinstance(child["pid"], bool)
        or not isinstance(child["pid"], int)
        or child["pid"] <= 1
        or isinstance(child["starttime"], bool)
        or not isinstance(child["starttime"], int)
        or child["starttime"] <= 0
        or not isinstance(child["exe"], str)
        or not child["exe"].startswith("/proc/")
        or child["uid"] != uid
        or child["gid"] != gid
        or child["capabilities"] != []
        or child["no_new_privs"] is not True
    ):
        raise A3EvaluationError("launcher child privilege evidence is invalid")
    probes = _exact(attestation["probes"], {"pre", "post"}, "launcher probes")
    _probe_set(probes["pre"], "launcher pre probes")
    _probe_set(probes["post"], "launcher post probes")
    signature = attestation.pop("signature")
    if not isinstance(signature, str) or not signature.startswith("ed25519:"):
        raise A3EvaluationError("launcher signature encoding is invalid")
    try:
        signature_raw = bytes.fromhex(signature.removeprefix("ed25519:"))
        if len(public_key_raw) != 32:
            raise ValueError("wrong public key size")
        Ed25519PublicKey.from_public_bytes(public_key_raw).verify(signature_raw, canonical(attestation))
    except (InvalidSignature, ValueError) as exc:
        raise A3EvaluationError("launcher attestation signature is invalid") from exc
    attestation["signature"] = signature
    return attestation


@dataclass(frozen=True, slots=True)
class A3LocalProductionComposition:
    schema: str
    request_sha256: str
    runner_source: Mapping[str, Any]
    helper: Mapping[str, Any]
    product_archive: Mapping[str, Any]
    integration_archive: Mapping[str, Any]
    target: Mapping[str, Any]
    current_cas_observer: Mapping[str, Any]
    current_cas_observation: Mapping[str, Any]
    scratch_root: Mapping[str, Any]
    receipt_roots: Mapping[str, Any]
    tools: Mapping[str, Any]
    tool_versions: Mapping[str, Any]
    root_launcher: Mapping[str, Any]
    launcher_source: Mapping[str, Any]
    launcher_config: Mapping[str, Any]
    netns_prerequisite: Mapping[str, Any]
    prerequisite_status: str
    attestation_public_key: Mapping[str, Any]
    signing_key_ref: str
    codex_identity: Mapping[str, Any]
    bounds: Mapping[str, Any]
    composition_sha256: str

    def unsigned(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self) if field.name != "composition_sha256"}


def validate_local_composition(composition: A3LocalProductionComposition, request_value: Mapping[str, Any], *, reviewed_source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request = validate_request(request_value, reviewed_source=reviewed_source)
    if composition.schema != COMPOSITION_SCHEMA or composition.request_sha256 != request["request_sha256"] or composition.composition_sha256 != digest(composition.unsigned()):
        raise A3EvaluationError("local production composition identity is invalid")
    if composition.target != {"host": "tgw-prod", "system": "x86_64-linux", "expected_current": request["target"]["expected_current"]}:
        raise A3EvaluationError("local production target/CAS binding mismatch")
    helper_raw, _ = _read_held(composition.helper, label="remote helper", executable=False)
    installed_helper = Path(remote.__file__).resolve(strict=True)
    if Path(composition.helper["path"]).resolve(strict=True) != installed_helper or digest(helper_raw) != composition.helper["sha256"]:
        raise A3EvaluationError("composition helper is not the exact installed helper consumed")
    runner_raw, _ = _read_held(composition.runner_source, label="local runner source", executable=False)
    installed_runner = Path(__file__).resolve(strict=True)
    if Path(composition.runner_source["path"]).resolve(strict=True) != installed_runner or digest(runner_raw) != composition.runner_source["sha256"]:
        raise A3EvaluationError("composition runner source is not the exact installed transport consumed")
    for label, identity, expected_hash, expected_size in (
        ("product archive", composition.product_archive, request["source"]["archive_sha256"], request["source"]["archive_size"]),
        ("integration archive", composition.integration_archive, request["integration"]["archive_sha256"], request["integration"]["archive_size"]),
    ):
        raw, _ = _read_held(identity, label=label, executable=False)
        if digest(raw) != expected_hash or len(raw) != expected_size:
            raise A3EvaluationError(f"{label} differs from the request")
    if composition.tools != request["tools"]:
        raise A3EvaluationError("composition held-tool set differs from request")
    if composition.tool_versions != request["expected_tool_versions"]:
        raise A3EvaluationError("composition tool-version set differs from request")
    for name, identity in composition.tools.items():
        _read_held(identity, label=f"composition tool {name}", executable=True)
    _read_held(composition.root_launcher, label="root launcher", executable=True)
    launcher_source_raw, _ = _read_held(composition.launcher_source, label="audited launcher source", executable=False)
    _read_held(composition.current_cas_observer, label="current-CAS observer", executable=True)
    launcher_config_raw, _ = _read_held(composition.launcher_config, label="launcher config", executable=False)
    prerequisite_raw, _ = _read_held(composition.netns_prerequisite, label="netns prerequisite receipt", executable=False)
    public_key_raw, _ = _read_held(composition.attestation_public_key, label="launcher attestation public key", executable=False)
    authority = request["validation_authority"]
    if (
        len(public_key_raw) != 32
        or composition.attestation_public_key != authority["attestation_public_key"]
        or composition.receipt_roots["replay"] != authority["replay_root"]
        or composition.codex_identity != {"uid": authority["child_uid"], "gid": authority["child_gid"]}
        or authority["trusted_uid"] != 0
    ):
        raise A3EvaluationError("launcher attestation public key is not exact raw Ed25519")
    if composition.prerequisite_status != "EXTERNAL_PREREQUISITE":
        raise A3EvaluationError("raw kernel isolation implementation must remain an explicit external prerequisite")
    try:
        prerequisite = json.loads(prerequisite_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A3EvaluationError("external launcher prerequisite receipt is invalid") from exc
    prerequisite_fields = {
        "schema",
        "status",
        "prerequisite",
        "launcher_source_sha256",
        "launcher_executable_sha256",
        "launcher_config_sha256",
        "attestation_public_key_sha256",
        "packet_schema",
        "response_schema",
        "attestation_schema",
        "raw_evidence_schema",
        "raw_evidence_signed_by",
        "receipt_sha256",
    }
    if (
        canonical(prerequisite) != prerequisite_raw
        or set(prerequisite) != prerequisite_fields
        or prerequisite["schema"] != "tgw-nixos-a3-local-launcher-prerequisite/v1"
        or prerequisite["status"] != "SATISFIED"
        or prerequisite["prerequisite"] != "EXTERNAL_PREREQUISITE"
        or prerequisite["launcher_source_sha256"] != digest(launcher_source_raw)
        or prerequisite["launcher_executable_sha256"] != composition.root_launcher["sha256"]
        or prerequisite["launcher_config_sha256"] != digest(launcher_config_raw)
        or prerequisite["attestation_public_key_sha256"] != digest(public_key_raw)
        or prerequisite["packet_schema"] != LAUNCH_PACKET_SCHEMA
        or prerequisite["response_schema"] != LAUNCH_RESPONSE_SCHEMA
        or prerequisite["attestation_schema"] != ATTESTATION_SCHEMA
        or prerequisite["raw_evidence_schema"] != "tgw-nixos-a3-raw-link-route-probes/v1"
        or prerequisite["raw_evidence_signed_by"] != digest(public_key_raw)
        or prerequisite["receipt_sha256"] != digest({key: item for key, item in prerequisite.items() if key != "receipt_sha256"})
    ):
        raise A3EvaluationError("external launcher prerequisite is absent or not bound to the audited implementation/protocol")
    if (
        not isinstance(composition.signing_key_ref, str)
        or not composition.signing_key_ref.startswith("external-root-0400:sha256:")
        or len(composition.signing_key_ref) != len("external-root-0400:sha256:") + 64
        or any(char not in _HEX64 for char in composition.signing_key_ref[-64:])
    ):
        raise A3EvaluationError("launcher signing identity must remain an external root 0400 reference")
    codex = _exact(composition.codex_identity, {"uid", "gid"}, "codex identity")
    if isinstance(codex["uid"], bool) or not isinstance(codex["uid"], int) or codex["uid"] <= 0 or isinstance(codex["gid"], bool) or not isinstance(codex["gid"], int) or codex["gid"] <= 0:
        raise A3EvaluationError("codex uid/gid are invalid")
    bounds = _exact(composition.bounds, {"timeout_seconds", "max_output_bytes", "term_grace_seconds", "max_processes", "max_memory_bytes"}, "local bounds")
    if bounds != {
        "timeout_seconds": request["policy"]["max_seconds"],
        "max_output_bytes": request["policy"]["max_output_bytes"],
        "term_grace_seconds": 2,
        "max_processes": 32,
        "max_memory_bytes": 2_147_483_648,
    }:
        raise A3EvaluationError("local process/resource bounds are not exact")
    _validate_live_directory(composition.scratch_root, "scratch root")
    roots = _exact(composition.receipt_roots, {"terminal", "readiness", "replay"}, "receipt roots")
    _validate_live_directory(roots["terminal"], "terminal receipt root")
    _validate_live_directory(roots["replay"], "nonce replay root")
    readiness_raw, _ = _read_held(roots["readiness"], label="transport readiness receipt", executable=False)
    try:
        readiness = json.loads(readiness_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A3EvaluationError("transport readiness receipt is not canonical JSON") from exc
    if canonical(readiness) != readiness_raw or readiness != composition.current_cas_observation:
        raise A3EvaluationError("transport readiness receipt differs from the sealed composition")
    observation = _exact(
        composition.current_cas_observation,
        {"schema", "observer_sha256", "expected_current", "observed_current", "returncode", "stdout_sha256", "stderr_sha256", "receipt_sha256"},
        "current-CAS observation",
    )
    if (
        observation["schema"] != "tgw-nixos-a3-current-cas-observation/v1"
        or observation["observer_sha256"] != composition.current_cas_observer["sha256"]
        or observation["expected_current"] != request["target"]["expected_current"]
        or observation["observed_current"] != request["target"]["expected_current"]
        or isinstance(observation["returncode"], bool)
        or observation["returncode"] != 0
        or observation["receipt_sha256"] != digest({key: item for key, item in observation.items() if key != "receipt_sha256"})
    ):
        raise A3EvaluationError("current-CAS readiness observation is invalid")
    if _observe_current_cas(composition.current_cas_observer, request["target"]["expected_current"]) != {
        "returncode": observation["returncode"],
        "stdout_sha256": observation["stdout_sha256"],
        "stderr_sha256": observation["stderr_sha256"],
    }:
        raise A3EvaluationError("live current-CAS observation differs from sealed readiness evidence")
    return request


class DurableNonceReplayStore:
    """One-use launch-challenge claims in an exact held immutable root."""

    def __init__(self, identity: Mapping[str, Any], *, _test_uid: int | None = None):
        self.identity = _validate_directory_identity(identity, "nonce replay root", trusted_uid=0 if _test_uid is None else _test_uid)
        self._root_fd = _open_live_directory(self.identity, "nonce replay root", trusted_uid=0 if _test_uid is None else _test_uid)
        metadata = os.fstat(self._root_fd)
        observed = {
            "path": self.identity["path"],
            "dev": metadata.st_dev,
            "ino": metadata.st_ino,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
        if observed != self.identity:
            os.close(self._root_fd)
            raise A3EvaluationError("nonce replay root held identity changed")

    def close(self) -> None:
        if self._root_fd >= 0:
            os.close(self._root_fd)
            self._root_fd = -1

    def claim(
        self,
        *,
        launch_nonce: str,
        attempt_id: str,
        request_sha256: str,
        composition_sha256: str,
        attestation_sha256: str,
    ) -> dict[str, Any]:
        value = {
            "schema": REPLAY_CLAIM_SCHEMA,
            "launch_nonce": launch_nonce,
            "attempt_id": attempt_id,
            "request_sha256": request_sha256,
            "composition_sha256": composition_sha256,
            "attestation_sha256": attestation_sha256,
        }
        value["claim_sha256"] = digest(value)
        raw = canonical(value)
        name = digest({"launch_nonce": launch_nonce}).removeprefix("sha256:") + ".json"
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._root_fd)
        except FileExistsError as exc:
            raise A3EvaluationError("launcher challenge was already consumed") from exc
        valid = False
        try:
            offset = 0
            while offset < len(raw):
                count = os.write(fd, raw[offset:])
                if count <= 0:
                    raise OSError("short replay claim write")
                offset += count
            os.fsync(fd)
            held = os.fstat(fd)
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, len(raw) + 1) != raw:
                raise A3EvaluationError("replay claim held readback mismatch")
            named_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._root_fd)
            try:
                named = os.fstat(named_fd)
                if (named.st_dev, named.st_ino, named.st_size) != (held.st_dev, held.st_ino, len(raw)) or os.read(named_fd, len(raw) + 1) != raw:
                    raise A3EvaluationError("replay claim named readback mismatch")
            finally:
                os.close(named_fd)
            os.fsync(self._root_fd)
            named = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            root = os.fstat(self._root_fd)
            reopened_root_fd = _open_live_directory(self.identity, "nonce replay root", trusted_uid=self.identity["uid"])
            try:
                named_root = os.fstat(reopened_root_fd)
            finally:
                os.close(reopened_root_fd)
            if (
                (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino)
                or (
                    root.st_dev,
                    root.st_ino,
                    root.st_uid,
                    root.st_gid,
                    stat.S_IMODE(root.st_mode),
                )
                != (
                    self.identity["dev"],
                    self.identity["ino"],
                    self.identity["uid"],
                    self.identity["gid"],
                    self.identity["mode"],
                )
                or (named_root.st_dev, named_root.st_ino) != (root.st_dev, root.st_ino)
            ):
                raise A3EvaluationError("replay claim/root changed during commit")
            valid = True
            return {
                "claim": value,
                "ref": {
                    "schema": REPLAY_CLAIM_REF_SCHEMA,
                    "name": name,
                    "claim_sha256": value["claim_sha256"],
                    "file_sha256": digest(raw),
                    "size": len(raw),
                },
            }
        finally:
            os.close(fd)
            if not valid:
                try:
                    os.unlink(name, dir_fd=self._root_fd)
                    os.fsync(self._root_fd)
                except OSError as exc:
                    raise A3EvaluationError("replay claim cleanup is ambiguous") from exc

    def read(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        ref = _exact(reference, {"schema", "name", "claim_sha256", "file_sha256", "size"}, "replay claim reference")
        _sha(ref["claim_sha256"], "replay claim self hash")
        _sha(ref["file_sha256"], "replay claim file hash")
        if (
            ref["schema"] != REPLAY_CLAIM_REF_SCHEMA
            or not isinstance(ref["name"], str)
            or not ref["name"].endswith(".json")
            or "/" in ref["name"]
            or isinstance(ref["size"], bool)
            or not isinstance(ref["size"], int)
            or not 1 <= ref["size"] <= 16_384
        ):
            raise A3EvaluationError("replay claim reference is invalid")
        try:
            fd = os.open(ref["name"], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self._root_fd)
        except OSError as exc:
            raise A3EvaluationError("durable replay claim artifact is absent or unsafe") from exc
        try:
            before = os.fstat(fd)
            raw = os.read(fd, ref["size"] + 1)
            after = os.fstat(fd)
            named = os.stat(ref["name"], dir_fd=self._root_fd, follow_symlinks=False)
        finally:
            os.close(fd)
        root = os.fstat(self._root_fd)
        reopened_root_fd = _open_live_directory(self.identity, "nonce replay root", trusted_uid=self.identity["uid"])
        try:
            named_root = os.fstat(reopened_root_fd)
        finally:
            os.close(reopened_root_fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_uid != self.identity["uid"]
            or before.st_nlink != 1
            or len(raw) != ref["size"]
            or digest(raw) != ref["file_sha256"]
            or (before.st_dev, before.st_ino, before.st_size, before.st_ctime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns)
            or (named.st_dev, named.st_ino) != (before.st_dev, before.st_ino)
            or (root.st_dev, root.st_ino) != (named_root.st_dev, named_root.st_ino)
            or (root.st_dev, root.st_ino, root.st_uid, root.st_gid, stat.S_IMODE(root.st_mode))
            != (self.identity["dev"], self.identity["ino"], self.identity["uid"], self.identity["gid"], self.identity["mode"])
        ):
            raise A3EvaluationError("durable replay claim/root identity changed")
        try:
            claim = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise A3EvaluationError("durable replay claim is invalid JSON") from exc
        if canonical(claim) != raw or not isinstance(claim, dict):
            raise A3EvaluationError("durable replay claim is not canonical")
        return claim


class ExactSubprocessRunner:
    """Invoke only the sealed no-argv root launcher and validate its response."""

    def __init__(self, composition: A3LocalProductionComposition):
        self._composition = composition
        self.attestations: list[dict[str, Any]] = []
        self._replay_store = DurableNonceReplayStore(composition.receipt_roots["replay"])

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: int,
        max_output: int,
        pass_fds: Sequence[int],
    ) -> Completed:
        if timeout != self._composition.bounds["timeout_seconds"] or max_output != self._composition.bounds["max_output_bytes"]:
            raise A3EvaluationError("logical subprocess bounds differ from sealed composition")
        config_raw, _ = _read_held(self._composition.launcher_config, label="launcher config", executable=False)
        prerequisite_raw, _ = _read_held(self._composition.netns_prerequisite, label="netns prerequisite receipt", executable=False)
        public_raw, _ = _read_held(self._composition.attestation_public_key, label="launcher attestation public key", executable=False)
        launcher_parent_fd, launcher_fd, launcher_before, launcher_raw = _open_held_executable(self._composition.root_launcher, "root launcher")
        issued = datetime.now(UTC)
        expires = issued + timedelta(seconds=timeout)
        issued_at = issued.strftime(_RFC3339_FORMAT)
        expires_at = expires.strftime(_RFC3339_FORMAT)
        launch_nonce = secrets.token_hex(32)
        attempt_id = "attempt:" + secrets.token_hex(32)
        packet = {
            "schema": LAUNCH_PACKET_SCHEMA,
            "composition_sha256": self._composition.composition_sha256,
            "request_sha256": self._composition.request_sha256,
            "launch_nonce": launch_nonce,
            "attempt_id": attempt_id,
            "issued_at": issued_at,
            "expires_at": expires_at,
            "logical_argv": list(argv),
            "cwd": str(cwd),
            "env": dict(sorted(env.items())),
            "timeout_seconds": timeout,
            "max_output_bytes": max_output,
            "pass_fds": list(pass_fds),
            "launcher_sha256": digest(launcher_raw),
            "config_sha256": digest(config_raw),
            "prerequisite_sha256": digest(prerequisite_raw),
        }
        packet["packet_sha256"] = digest(packet)
        packet_raw = canonical(packet)
        try:
            launcher_path = f"/proc/{os.getpid()}/fd/{launcher_fd}"
            try:
                process = subprocess.Popen(
                    [launcher_path],
                    cwd=cwd,
                    env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": ""},
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    shell=False,
                    close_fds=True,
                    pass_fds=tuple((*pass_fds, launcher_fd)),
                    start_new_session=True,
                )
            except OSError as exc:
                raise StepFailure(
                    "root launcher process creation was refused",
                    step="launcher",
                    returncode=-1,
                    stdout=b"",
                    stderr=str(exc).encode()[:4096],
                    process_state="NOT_CREATED",
                    cleanup="REMOVED",
                ) from exc
            rc, response_raw, launcher_stderr, outer_process_state = _stream_bounded(
                process,
                input_bytes=packet_raw,
                timeout=timeout,
                max_output=max_output * 2 + 131_072,
            )
            after = os.fstat(launcher_fd)
            os.lseek(launcher_fd, 0, os.SEEK_SET)
            launcher_after_raw = os.read(launcher_fd, self._composition.root_launcher["size"] + 1)
            try:
                named = os.stat(Path(self._composition.root_launcher["path"]).name, dir_fd=launcher_parent_fd, follow_symlinks=False)
            except OSError:
                named = None
            if (
                (after.st_dev, after.st_ino, after.st_size, after.st_ctime_ns) != (launcher_before.st_dev, launcher_before.st_ino, launcher_before.st_size, launcher_before.st_ctime_ns)
                or launcher_after_raw != launcher_raw
                or named is None
                or (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise StepFailure(
                    "root launcher named identity changed",
                    step="launcher-identity",
                    returncode=rc,
                    stdout=response_raw,
                    stderr=launcher_stderr,
                    process_state="REAPED",
                    cleanup="REMOVED",
                )
        finally:
            os.close(launcher_fd)
            os.close(launcher_parent_fd)
        if rc != 0:
            raise StepFailure(
                "root launcher refused the fixed packet",
                step="launcher",
                returncode=rc,
                stdout=response_raw,
                stderr=launcher_stderr,
                process_state="REAPED",
                cleanup="REMOVED",
            )
        try:
            response = _exact(
                json.loads(response_raw),
                {"schema", "packet_sha256", "returncode", "stdout_b64", "stderr_b64", "process_state", "cleanup", "process", "attestation"},
                "launcher response",
            )
            stdout = base64.b64decode(response["stdout_b64"], validate=True)
            stderr = base64.b64decode(response["stderr_b64"], validate=True)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise StepFailure(
                "root launcher response is invalid",
                step="response",
                returncode=rc,
                stdout=response_raw,
                stderr=launcher_stderr,
                process_state="REAPED",
                cleanup="REMOVED",
            ) from exc
        if (
            response["schema"] != LAUNCH_RESPONSE_SCHEMA
            or response["packet_sha256"] != packet["packet_sha256"]
            or isinstance(response["returncode"], bool)
            or not isinstance(response["returncode"], int)
            or response["process_state"] != "REAPED"
            or response["cleanup"] != "REMOVED"
            or outer_process_state != "REAPED_GROUP_EMPTY"
            or len(stdout) + len(stderr) > max_output
        ):
            raise StepFailure(
                "root launcher response violates terminal bounds",
                step="response-contract",
                returncode=rc,
                stdout=stdout[:max_output],
                stderr=stderr[:max_output],
                process_state=str(response.get("process_state", "UNKNOWN")),
                cleanup=str(response.get("cleanup", "UNKNOWN")),
            )
        attestation = validate_launcher_attestation(
            response["attestation"],
            packet_sha256=packet["packet_sha256"],
            composition_sha256=self._composition.composition_sha256,
            request_sha256=self._composition.request_sha256,
            launch_nonce=launch_nonce,
            attempt_id=attempt_id,
            issued_at=issued_at,
            expires_at=expires_at,
            public_key_raw=public_raw,
            uid=self._composition.codex_identity["uid"],
            gid=self._composition.codex_identity["gid"],
            now=datetime.now(UTC),
            max_duration_seconds=timeout,
        )
        child = attestation["child"]
        process_facts = _exact(
            response["process"],
            {"launcher_pid", "child_pid", "child_starttime", "child_exe", "child_reaped", "process_group_empty"},
            "launcher process facts",
        )
        if (
            isinstance(process_facts["launcher_pid"], bool)
            or not isinstance(process_facts["launcher_pid"], int)
            or process_facts["launcher_pid"] <= 1
            or process_facts["child_pid"] != child["pid"]
            or process_facts["child_starttime"] != child["starttime"]
            or process_facts["child_exe"] != child["exe"]
            or process_facts["child_reaped"] is not True
            or process_facts["process_group_empty"] is not True
        ):
            raise A3EvaluationError("signed child identity differs from launched/reaped process facts")
        replay_claim = self._replay_store.claim(
            launch_nonce=launch_nonce,
            attempt_id=attempt_id,
            request_sha256=self._composition.request_sha256,
            composition_sha256=self._composition.composition_sha256,
            attestation_sha256=digest(attestation),
        )
        evidence = {
            "schema": LAUNCH_EVIDENCE_SCHEMA,
            "challenge": {key: packet[key] for key in ("packet_sha256", "composition_sha256", "request_sha256", "launch_nonce", "attempt_id", "issued_at", "expires_at")},
            "signed_attestation": attestation,
            "replay_claim": replay_claim["claim"],
            "replay_claim_ref": replay_claim["ref"],
        }
        self.attestations.append(evidence)
        return Completed(response["returncode"], stdout, stderr, attestation=evidence, process_state="REAPED", process_facts=dict(process_facts))


class A3LocalProductionTransport:
    """Final sealed production transport; instances come only from the factory."""

    __slots__ = ("composition", "_runner", "reviewed_source")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("A3LocalProductionTransport is final")

    def __init__(self, composition: A3LocalProductionComposition, *, _token: object, reviewed_source: Mapping[str, Any] | None = None) -> None:
        if _token is not _SEAL:
            raise TypeError("use load_local_production_transport")
        self.composition = composition
        self.reviewed_source = dict(reviewed_source) if reviewed_source is not None else None
        self._runner = ExactSubprocessRunner(composition)

    def validate_sealed(self, request_value: Mapping[str, Any]) -> None:
        validate_local_composition(self.composition, request_value, reviewed_source=self.reviewed_source)

    def __call__(self, request_value: Mapping[str, Any]) -> Mapping[str, Any]:
        request = validate_local_composition(self.composition, request_value, reviewed_source=self.reviewed_source)
        source, _ = _read_held(self.composition.product_archive, label="product archive", executable=False)
        integration, _ = _read_held(self.composition.integration_archive, label="integration archive", executable=False)
        return remote.execute(
            request,
            tgw_archive=source,
            integration_archive=integration,
            runner=self._runner,
            scratch_parent=Path(self.composition.scratch_root["path"]),
            allow_fixture=False,
        )


class A3TestTransport:
    """Distinct fixture-only adapter; it can never satisfy production readiness."""

    __slots__ = ("_callable",)

    def __init__(self, fixture_callable: Any):
        self._callable = fixture_callable

    def __call__(self, request_value: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._callable(request_value)


def _load_local_production_transport(path: Path, *, _test_uid: int | None = None, _after_read: Any = None) -> A3LocalProductionTransport:
    manifest_path = Path(path)
    trusted_uid = 0 if _test_uid is None else _test_uid
    if not manifest_path.is_absolute() or ".." in manifest_path.parts:
        raise A3EvaluationError("local production composition manifest path is not absolute and normalized")
    parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    components: list[tuple[int, str, tuple[int, int, int, int]]] = []
    manifest_fd = -1
    try:
        for component in manifest_path.parent.parts[1:]:
            before = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            held = os.fstat(next_fd)
            after = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            identity = (held.st_dev, held.st_ino, held.st_uid, stat.S_IMODE(held.st_mode))
            sticky_root = held.st_uid == 0 and bool(stat.S_IMODE(held.st_mode) & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(held.st_mode)
                or held.st_uid not in {0, trusted_uid}
                or (stat.S_IMODE(held.st_mode) & 0o022 and not sticky_root)
                or (before.st_dev, before.st_ino) != identity[:2]
                or (after.st_dev, after.st_ino) != identity[:2]
            ):
                os.close(next_fd)
                raise A3EvaluationError("local composition component walk found an unsafe ancestor")
            components.append((parent_fd, component, (before.st_dev, before.st_ino, before.st_uid, stat.S_IMODE(before.st_mode))))
            parent_fd = next_fd
        named_before = os.stat(manifest_path.name, dir_fd=parent_fd, follow_symlinks=False)
        manifest_parent_before = os.fstat(parent_fd)
        manifest_fd = os.open(manifest_path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        held_before = os.fstat(manifest_fd)
        raw_buffer = bytearray()
        while len(raw_buffer) <= 16 * 1024 * 1024:
            block = os.read(manifest_fd, min(1024 * 1024, 16 * 1024 * 1024 + 1 - len(raw_buffer)))
            if not block:
                break
            raw_buffer.extend(block)
        raw = bytes(raw_buffer)
        if _after_read is not None:
            if _test_uid is None:
                raise A3EvaluationError("manifest test hook is forbidden in production")
            _after_read(manifest_path)
        held_after = os.fstat(manifest_fd)
        named_after = os.stat(manifest_path.name, dir_fd=parent_fd, follow_symlinks=False)
        manifest_parent_after = os.fstat(parent_fd)
        if (
            len(raw) > 16 * 1024 * 1024
            or not stat.S_ISREG(held_before.st_mode)
            or held_before.st_uid != trusted_uid
            or stat.S_IMODE(held_before.st_mode) != 0o400
            or (held_before.st_dev, held_before.st_ino, held_before.st_size, held_before.st_ctime_ns) != (held_after.st_dev, held_after.st_ino, held_after.st_size, held_after.st_ctime_ns)
            or (named_before.st_dev, named_before.st_ino) != (held_before.st_dev, held_before.st_ino)
            or (named_after.st_dev, named_after.st_ino) != (held_before.st_dev, held_before.st_ino)
            or (manifest_parent_before.st_dev, manifest_parent_before.st_ino, manifest_parent_before.st_mtime_ns, manifest_parent_before.st_ctime_ns)
            != (manifest_parent_after.st_dev, manifest_parent_after.st_ino, manifest_parent_after.st_mtime_ns, manifest_parent_after.st_ctime_ns)
        ):
            raise A3EvaluationError("local production composition manifest held/named identity changed")
        for ancestor_fd, component, identity in components:
            observed = os.stat(component, dir_fd=ancestor_fd, follow_symlinks=False)
            if (observed.st_dev, observed.st_ino, observed.st_uid, stat.S_IMODE(observed.st_mode)) != identity:
                raise A3EvaluationError("local production composition ancestor changed during use")
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        os.close(parent_fd)
        for ancestor_fd, _, _ in components:
            try:
                os.close(ancestor_fd)
            except OSError:
                pass
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A3EvaluationError("local production composition manifest is invalid") from exc
    if canonical(value) != raw or not isinstance(value, Mapping) or set(value) != {field.name for field in fields(A3LocalProductionComposition)}:
        raise A3EvaluationError("local production composition manifest is not exact canonical JSON")
    composition = A3LocalProductionComposition(**value)
    if composition.schema != COMPOSITION_SCHEMA or composition.composition_sha256 != digest(composition.unsigned()):
        raise A3EvaluationError("local production composition self identity is invalid")
    return A3LocalProductionTransport(composition, _token=_SEAL)


def load_local_production_transport(path: Path, *, reviewed_source: Mapping[str, Any] | None = None) -> A3LocalProductionTransport:
    """Load one immutable root-owned canonical composition manifest and seal it."""
    loaded = _load_local_production_transport(path)
    return A3LocalProductionTransport(loaded.composition, _token=_SEAL, reviewed_source=reviewed_source)


__all__ = [
    "A3LocalProductionComposition",
    "A3LocalProductionTransport",
    "A3TestTransport",
    "ATTESTATION_SCHEMA",
    "COMPOSITION_SCHEMA",
    "ExactSubprocessRunner",
    "StepFailure",
    "load_local_production_transport",
    "validate_launcher_attestation",
    "validate_local_composition",
]
