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
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, fields
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

_HEX64 = set("0123456789abcdef")
_SEAL = object()
_PROBE_NAMES = ("direct", "dns", "private", "metadata")


def _sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71 or any(char not in _HEX64 for char in value[7:]):
        raise A3EvaluationError(f"{label} is not an exact SHA-256 identity")
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
    if not isinstance(item["size"], int) or item["size"] <= 0:
        raise A3EvaluationError(f"{label} size is invalid")
    if not isinstance(item["uid"], int) or not isinstance(item["gid"], int):
        raise A3EvaluationError(f"{label} ownership is invalid")
    if not isinstance(item["mode"], int) or item["mode"] & 0o022 or item["mode"] & 0o777 not in {0o400, 0o440, 0o444}:
        raise A3EvaluationError(f"{label} must be non-writable data")
    return item


def _validate_directory_identity(value: Any, label: str) -> dict[str, Any]:
    item = dict(_exact(value, {"path", "dev", "ino", "uid", "gid", "mode"}, label))
    if not isinstance(item["path"], str) or not item["path"].startswith("/") or ".." in Path(item["path"]).parts:
        raise A3EvaluationError(f"{label} path is invalid")
    if any(not isinstance(item[key], int) for key in ("dev", "ino", "uid", "gid", "mode")) or item["dev"] <= 0 or item["ino"] <= 0:
        raise A3EvaluationError(f"{label} metadata is invalid")
    if item["uid"] != 0 or item["mode"] != 0o700:
        raise A3EvaluationError(f"{label} must be root-owned mode 0700")
    return item


def _read_held(value: Mapping[str, Any], *, label: str, executable: bool) -> tuple[bytes, os.stat_result]:
    identity = validate_file_identity(value, label=label) if executable else _validate_data_identity(value, label)
    fd = os.open(identity["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        raw = bytearray()
        while len(raw) <= identity["size"]:
            block = os.read(fd, min(1024 * 1024, identity["size"] + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(fd)
        named = os.stat(identity["path"], follow_symlinks=False)
    finally:
        os.close(fd)
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


def _validate_live_directory(identity: Mapping[str, Any], label: str) -> Path:
    item = _validate_directory_identity(identity, label)
    metadata = os.stat(item["path"], follow_symlinks=False)
    if not stat.S_ISDIR(metadata.st_mode) or any(
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
    return Path(item["path"])


def _observe_current_cas(identity: Mapping[str, Any], expected: str) -> dict[str, Any]:
    """Execute the held, no-argument, read-only current-system observer."""
    validate_file_identity(identity, label="current-CAS observer")
    fd = os.open(identity["path"], os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
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
        named = os.stat(identity["path"], follow_symlinks=False)
    finally:
        os.close(fd)
    if (
        process.returncode != 0
        or len(stdout) + len(stderr) > 4096
        or stderr
        or stdout != (expected + "\n").encode()
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


def _terminate_group(process: subprocess.Popen[bytes], *, grace_seconds: float = 2.0) -> tuple[int | None, str, str]:
    """Terminate the exact child session and prove it has been reaped."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        return process.wait(timeout=grace_seconds), "REAPED_AFTER_TERM", "REMOVED"
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            return process.wait(timeout=grace_seconds), "REAPED_AFTER_KILL", "REMOVED"
        except subprocess.TimeoutExpired:
            return None, "UNKNOWN", "UNKNOWN"


def _stream_bounded(
    process: subprocess.Popen[bytes],
    *,
    input_bytes: bytes,
    timeout: int,
    max_output: int,
) -> tuple[int, bytes, bytes]:
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
    return process.wait(), bytes(output["stdout"]), bytes(output["stderr"])


def _probe_set(value: Any, label: str) -> Mapping[str, Any]:
    probes = _exact(value, set(_PROBE_NAMES), label)
    for name in _PROBE_NAMES:
        probe = _exact(probes[name], {"attempted", "connected", "evidence_sha256"}, f"{label}.{name}")
        if probe["attempted"] is not True or probe["connected"] is not False:
            raise A3EvaluationError(f"{label}.{name} is not negative evidence")
        _sha(probe["evidence_sha256"], f"{label}.{name}.evidence")
    return probes


def validate_launcher_attestation(value: Any, *, packet_sha256: str, composition_sha256: str, public_key_raw: bytes, uid: int, gid: int) -> dict[str, Any]:
    attestation = dict(
        _exact(
            value,
            {"schema", "packet_sha256", "composition_sha256", "nonce", "started_at", "ended_at", "netns", "child", "probes", "signature"},
            "launcher attestation",
        )
    )
    if attestation["schema"] != ATTESTATION_SCHEMA or attestation["packet_sha256"] != packet_sha256 or attestation["composition_sha256"] != composition_sha256:
        raise A3EvaluationError("launcher attestation binding mismatch")
    if not isinstance(attestation["nonce"], str) or len(attestation["nonce"]) != 64 or any(char not in _HEX64 for char in attestation["nonce"]):
        raise A3EvaluationError("launcher attestation nonce is invalid")
    if not all(isinstance(attestation[name], str) and attestation[name].endswith("Z") for name in ("started_at", "ended_at")) or attestation["started_at"] >= attestation["ended_at"]:
        raise A3EvaluationError("launcher attestation time interval is invalid")
    netns = _exact(attestation["netns"], {"start_inode", "end_inode", "lo_only", "routes_empty", "link_sha256", "route_sha256"}, "launcher netns")
    if not isinstance(netns["start_inode"], int) or netns["start_inode"] <= 0 or netns["end_inode"] != netns["start_inode"] or netns["lo_only"] is not True or netns["routes_empty"] is not True:
        raise A3EvaluationError("launcher did not prove one loopback-only route-free namespace")
    _sha(netns["link_sha256"], "launcher link evidence")
    _sha(netns["route_sha256"], "launcher route evidence")
    child = _exact(attestation["child"], {"pid", "starttime", "exe", "uid", "gid", "capabilities", "no_new_privs"}, "launcher child")
    if (
        not isinstance(child["pid"], int)
        or child["pid"] <= 1
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
    launcher_config: Mapping[str, Any]
    netns_prerequisite: Mapping[str, Any]
    attestation_public_key: Mapping[str, Any]
    signing_key_ref: str
    codex_identity: Mapping[str, Any]
    bounds: Mapping[str, Any]
    composition_sha256: str

    def unsigned(self) -> dict[str, Any]:
        return {field.name: getattr(self, field.name) for field in fields(self) if field.name != "composition_sha256"}


def validate_local_composition(composition: A3LocalProductionComposition, request_value: Mapping[str, Any]) -> dict[str, Any]:
    request = validate_request(request_value)
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
    _read_held(composition.current_cas_observer, label="current-CAS observer", executable=True)
    _read_held(composition.launcher_config, label="launcher config", executable=False)
    _read_held(composition.netns_prerequisite, label="netns prerequisite receipt", executable=False)
    public_key_raw, _ = _read_held(composition.attestation_public_key, label="launcher attestation public key", executable=False)
    if len(public_key_raw) != 32:
        raise A3EvaluationError("launcher attestation public key is not exact raw Ed25519")
    if (
        not isinstance(composition.signing_key_ref, str)
        or not composition.signing_key_ref.startswith("external-root-0400:sha256:")
        or len(composition.signing_key_ref) != len("external-root-0400:sha256:") + 64
        or any(char not in _HEX64 for char in composition.signing_key_ref[-64:])
    ):
        raise A3EvaluationError("launcher signing identity must remain an external root 0400 reference")
    codex = _exact(composition.codex_identity, {"uid", "gid"}, "codex identity")
    if not isinstance(codex["uid"], int) or codex["uid"] <= 0 or not isinstance(codex["gid"], int) or codex["gid"] <= 0:
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
    roots = _exact(composition.receipt_roots, {"terminal", "readiness"}, "receipt roots")
    _validate_live_directory(roots["terminal"], "terminal receipt root")
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


class ExactSubprocessRunner:
    """Invoke only the sealed no-argv root launcher and validate its response."""

    def __init__(self, composition: A3LocalProductionComposition):
        self._composition = composition
        self.attestations: list[dict[str, Any]] = []

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
        launcher_raw, _ = _read_held(self._composition.root_launcher, label="root launcher", executable=True)
        config_raw, _ = _read_held(self._composition.launcher_config, label="launcher config", executable=False)
        prerequisite_raw, _ = _read_held(self._composition.netns_prerequisite, label="netns prerequisite receipt", executable=False)
        public_raw, _ = _read_held(self._composition.attestation_public_key, label="launcher attestation public key", executable=False)
        packet = {
            "schema": LAUNCH_PACKET_SCHEMA,
            "composition_sha256": self._composition.composition_sha256,
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
        launcher_fd = os.open(self._composition.root_launcher["path"], os.O_RDONLY | os.O_NOFOLLOW)
        try:
            launcher_path = f"/proc/{os.getpid()}/fd/{launcher_fd}"
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
            rc, response_raw, launcher_stderr = _stream_bounded(
                process,
                input_bytes=packet_raw,
                timeout=timeout,
                max_output=max_output * 2 + 131_072,
            )
            after = os.fstat(launcher_fd)
            named = os.stat(self._composition.root_launcher["path"], follow_symlinks=False)
            if (after.st_dev, after.st_ino) != (named.st_dev, named.st_ino):
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
            response = _exact(json.loads(response_raw), {"schema", "packet_sha256", "returncode", "stdout_b64", "stderr_b64", "process_state", "cleanup", "attestation"}, "launcher response")
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
            or not isinstance(response["returncode"], int)
            or response["process_state"] != "REAPED"
            or response["cleanup"] != "REMOVED"
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
            public_key_raw=public_raw,
            uid=self._composition.codex_identity["uid"],
            gid=self._composition.codex_identity["gid"],
        )
        self.attestations.append(attestation)
        return Completed(response["returncode"], stdout, stderr, attestation=attestation, process_state="REAPED")


class A3LocalProductionTransport:
    """Final sealed production transport; instances come only from the factory."""

    __slots__ = ("composition", "_runner")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("A3LocalProductionTransport is final")

    def __init__(self, composition: A3LocalProductionComposition, *, _token: object) -> None:
        if _token is not _SEAL:
            raise TypeError("use load_local_production_transport")
        self.composition = composition
        self._runner = ExactSubprocessRunner(composition)

    def validate_sealed(self, request_value: Mapping[str, Any]) -> None:
        validate_local_composition(self.composition, request_value)

    def __call__(self, request_value: Mapping[str, Any]) -> Mapping[str, Any]:
        request = validate_local_composition(self.composition, request_value)
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


def load_local_production_transport(path: Path) -> A3LocalProductionTransport:
    """Load one immutable canonical composition manifest and seal its instance."""
    manifest_path = Path(path)
    metadata = manifest_path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o400:
        raise A3EvaluationError("local production composition manifest must be root-owned mode 0400")
    raw = manifest_path.read_bytes()
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
