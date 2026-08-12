"""Closed remote provider for immutable, non-activating NixOS evaluation."""

from __future__ import annotations

import fcntl
import io
import json
import os
import re
import secrets
import selectors
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

SSH_EXECUTABLE = "/usr/bin/ssh"
REMOTE_PYTHON = "/run/current-system/sw/bin/python3"
BOOTSTRAP = (
    "import hashlib,struct,sys; n=struct.unpack('!Q',sys.stdin.buffer.read(8))[0]; "
    "s=sys.stdin.buffer.read(n); h=sys.stdin.buffer.read(64).decode(); r=sys.stdin.buffer.read(71).decode(); "
    "hashlib.sha256(s).hexdigest()==h or sys.exit(91); "
    "exec(compile(s,'<tgw-reviewed-evaluator>','exec'),"
    "{'__name__':'__main__','_BOOTSTRAP_PROVIDER_SHA256':'sha256:'+h,'_BOOTSTRAP_REQUEST_HASH':r})"
)
EXECUTABLES = {
    "git": "/run/current-system/sw/bin/git",
    "nix": "/run/current-system/sw/bin/nix",
    "nix_store": "/run/current-system/sw/bin/nix-store",
    "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze",
}
REMOTE_HOST = "100.107.99.66"
REMOTE_USER = "codex"
UNITS = (
    "tgw-review-egress@.service",
    "tgw-review-egress-attest@.service",
    "tgw-review-egress-namespace@.service",
)
_FAILURE_CONTEXT: dict[str, Any] = {"stage": "request-validation", "cleanup_result": "not-created"}


class EvaluationError(ValueError):
    pass


FAILURE_SCHEMA = "tgw-nixos-reviewed-evaluation-failure/v1"
FAILURE_STAGES = frozenset(
    {
        "request-validation",
        "provider-identity",
        "executable-identity",
        "scratch-root",
        "run-scratch",
        "archive-stream",
        "archive-verify",
        "source-tree",
        "source-digests",
        "nix-eval",
        "nix-build",
        "unit-extract",
        "systemd-verify",
        "closure-manifest",
        "version-probes",
        "cleanup",
        "internal",
    }
)
SUBPROCESS_STEPS = frozenset({"none", "git-init", "git-add", "git-write-tree", "nix-eval", "nix-build", "systemd-verify", "nix-requisites", "nix-hash", "systemd-version", "nix-version"})
DIAGNOSTIC_CODES = frozenset({"VALIDATION_REFUSED", "IDENTITY_MISMATCH", "BOUND_EXCEEDED", "SUBPROCESS_FAILED", "CLEANUP_FAILED", "INTERNAL_ERROR"})
EXCEPTION_CLASSES = frozenset({"EvaluationError", "TimeoutExpired", "OSError", "ValueError"})


class RemoteEvaluationFailure(EvaluationError):
    def __init__(self, receipt: Mapping[str, Any]):
        super().__init__("remote reviewed evaluation emitted a validated failure receipt")
        self.receipt = dict(receipt)


class StepFailure(EvaluationError):
    def __init__(self, message: str, *, step: str, return_code: int | None, stdout: bytes, stderr: bytes):
        super().__init__(message)
        self.step, self.return_code, self.stdout, self.stderr = step, return_code, stdout, stderr


def create_failure_receipt(
    *,
    context: Mapping[str, Any],
    stage: str,
    diagnostic_code: str,
    exception_class: str,
    cleanup_result: str,
    subprocess_step: str = "none",
    return_code: int | None = None,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> dict[str, Any]:
    outcome = "AMBIGUOUS" if cleanup_result not in {"removed", "retained-existing", "not-created"} else "FAILED"
    receipt = {
        "schema": FAILURE_SCHEMA,
        "outcome": outcome,
        "stage": stage,
        "diagnostic_code": diagnostic_code,
        "exception_class": exception_class,
        "request_hash": context.get("request_hash"),
        "effect_hash": context.get("effect_hash"),
        "generation": context.get("generation"),
        "provider_sha256": context.get("provider_sha256"),
        "scratch_root_created": bool(context.get("scratch_root_created", False)),
        "run_created": bool(context.get("run_created", False)),
        "cleanup_attempted": bool(context.get("cleanup_attempted", False)),
        "cleanup_result": cleanup_result,
        "subprocess_step": subprocess_step,
        "return_code": return_code,
        "stdout_bytes": len(stdout),
        "stdout_sha256": "sha256:" + sha256(stdout).hexdigest(),
        "stderr_bytes": len(stderr),
        "stderr_sha256": "sha256:" + sha256(stderr).hexdigest(),
        "forbidden_effects": {"activation": False, "profile_write": False, "home_db_write": False, "live_flake_write": False, "deployment": False},
    }
    receipt["receipt_sha256"] = "sha256:" + sha256(_canonical(receipt)).hexdigest()
    return receipt


def _effect_hash(parameters: Mapping[str, str]) -> str:
    effect = {"kind": "nixos-reviewed-evaluation", "generation": parameters["generation"], "parameters": {key: value for key, value in parameters.items() if key != "generation"}}
    return "effect:sha256:" + sha256(_canonical(effect)).hexdigest()


def validate_failure_receipt(value: Any, parameters: Mapping[str, str], *, request_hash: str) -> dict[str, Any]:
    if not isinstance(value, dict) or len(_canonical(value)) > 8192:
        raise EvaluationError("remote failure receipt is absent or oversized")
    required = {
        "schema",
        "outcome",
        "stage",
        "diagnostic_code",
        "exception_class",
        "request_hash",
        "effect_hash",
        "generation",
        "provider_sha256",
        "scratch_root_created",
        "run_created",
        "cleanup_attempted",
        "cleanup_result",
        "subprocess_step",
        "return_code",
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "forbidden_effects",
        "receipt_sha256",
    }
    if (
        set(value) != required
        or value["schema"] != FAILURE_SCHEMA
        or value["stage"] not in FAILURE_STAGES
        or value["diagnostic_code"] not in DIAGNOSTIC_CODES
        or value["exception_class"] not in EXCEPTION_CLASSES
        or value["subprocess_step"] not in SUBPROCESS_STEPS
    ):
        raise EvaluationError("remote failure receipt schema is invalid")
    if (
        value["request_hash"] != request_hash
        or value["effect_hash"] != _effect_hash(parameters)
        or value["generation"] != parameters["generation"]
        or value["provider_sha256"] != parameters["provider_sha256"]
    ):
        raise EvaluationError("remote failure receipt binding mismatch")
    if value["outcome"] not in {"FAILED", "AMBIGUOUS"} or value["cleanup_result"] not in {"removed", "retained-existing", "not-created", "failed", "unknown"}:
        raise EvaluationError("remote failure receipt outcome is invalid")
    expected_outcome = "FAILED" if value["cleanup_result"] in {"removed", "retained-existing", "not-created"} else "AMBIGUOUS"
    if value["outcome"] != expected_outcome:
        raise EvaluationError("remote failure receipt cleanup outcome is contradictory")
    if any(type(value[key]) is not bool for key in ("scratch_root_created", "run_created", "cleanup_attempted")):
        raise EvaluationError("remote failure receipt lifecycle facts are invalid")
    if type(value["return_code"]) not in {int, type(None)} or isinstance(value["return_code"], int) and not -255 <= value["return_code"] <= 255:
        raise EvaluationError("remote failure receipt return code is invalid")
    for prefix in ("stdout", "stderr"):
        if type(value[prefix + "_bytes"]) is not int or not 0 <= value[prefix + "_bytes"] <= 16 * 1024 * 1024 or not re.fullmatch(r"sha256:[0-9a-f]{64}", value[prefix + "_sha256"]):
            raise EvaluationError("remote failure receipt diagnostic digest is invalid")
    if (
        not isinstance(value["forbidden_effects"], dict)
        or any(type(item) is not bool for item in value["forbidden_effects"].values())
        or any(value["forbidden_effects"].values())
        or set(value["forbidden_effects"]) != {"activation", "profile_write", "home_db_write", "live_flake_write", "deployment"}
    ):
        raise EvaluationError("remote failure receipt claims a forbidden effect")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256")
    if claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest():
        raise EvaluationError("remote failure receipt self-hash mismatch")
    return dict(value)


class ArtifactResolver(Protocol):
    def __call__(self, artifact_ref: str) -> Path: ...


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _sealed_memfd(name: str, content: bytes) -> int:
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.write(fd, content)
    os.lseek(fd, 0, os.SEEK_SET)
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
    if fcntl.fcntl(fd, fcntl.F_GET_SEALS) != seals:
        os.close(fd)
        raise EvaluationError("known-hosts memfd did not seal")
    return fd


def _packet_header(parameters: Mapping[str, str], provider_source: bytes, request_hash: str) -> bytes:
    request = _canonical(dict(parameters))
    if len(request) > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", request_hash):
        raise EvaluationError("request hash is invalid")
    return struct.pack("!Q", len(provider_source)) + provider_source + sha256(provider_source).hexdigest().encode() + request_hash.encode() + struct.pack("!Q", len(request)) + request


def _validate_remote_parameters(value: Any) -> dict[str, str]:
    """Standalone mirror of the closed effect boundary; imports no TGW package."""
    keys = {
        "target_host",
        "flake_repository_id",
        "artifact_ref",
        "source_commit",
        "source_tree",
        "source_archive_sha256",
        "flake_lock_sha256",
        "archive_root",
        "module_path",
        "module_sha256",
        "provider_sha256",
        "ssh_sha256",
        "known_hosts_sha256",
        "remote_python_sha256",
        "git_sha256",
        "nix_sha256",
        "nix_store_sha256",
        "systemd_analyze_sha256",
        "scratch_id",
        "system",
        "evaluation_target",
        "unit_set",
        "output_schema",
        "nix_network_policy",
        "minimum_systemd_version",
        "max_duration_seconds",
        "max_output_bytes",
        "max_archive_bytes",
        "max_unpacked_bytes",
        "max_files",
        "activate",
        "profile_write",
        "home_db_write",
        "operation_id",
        "generation",
    }
    if not isinstance(value, dict) or set(value) != keys or any(not isinstance(item, str) or not item for item in value.values()):
        raise EvaluationError("remote evaluation parameters are not the exact typed object")
    fixed = {
        "target_host": "tgw-prod",
        "flake_repository_id": "tgw-flake",
        "archive_root": "trader-grims-warehouse",
        "module_path": "nix/review-egress.nix",
        "system": "x86_64-linux",
        "evaluation_target": "review-egress-systemd-units",
        "unit_set": ",".join(UNITS),
        "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
        "nix_network_policy": "offline-no-substituters",
        "activate": "false",
        "profile_write": "false",
        "home_db_write": "false",
    }
    if any(value.get(key) != expected for key, expected in fixed.items()):
        raise EvaluationError("remote evaluation fixed boundary mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", value["source_commit"]) or not re.fullmatch(r"[0-9a-f]{40}", value["source_tree"]):
        raise EvaluationError("remote source Git identity is invalid")
    digest_keys = {key for key in keys if key.endswith("_sha256")}
    if any(not re.fullmatch(r"(?:sha256:)?[0-9a-f]{64}", value[key]) for key in digest_keys):
        raise EvaluationError("remote digest binding is invalid")
    if value["artifact_ref"] != "artifact:sha256:" + value["source_archive_sha256"].removeprefix("sha256:"):
        raise EvaluationError("remote artifact identity mismatch")
    identity = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}")
    if not value["scratch_id"].startswith("nixos-review:") or not identity.fullmatch(value["scratch_id"]) or not identity.fullmatch(value["operation_id"]):
        raise EvaluationError("remote symbolic identity is invalid")
    bounds = tuple(int(value[key]) for key in ("minimum_systemd_version", "max_duration_seconds", "max_output_bytes", "max_archive_bytes", "max_unpacked_bytes", "max_files"))
    systemd, duration, output, archive, unpacked, files = bounds
    if systemd < 257 or not 1 <= duration <= 900 or not 1024 <= output <= 16 * 1024**2 or not 1024 <= archive <= 128 * 1024**2 or not archive <= unpacked <= 512 * 1024**2 or not 1 <= files <= 100_000:
        raise EvaluationError("remote resource bound is invalid")
    return dict(value)


class SshReviewedEvaluationProvider:
    """Resolve one content-addressed artifact and invoke one fixed remote helper."""

    def __init__(
        self,
        resolve_artifact: ArtifactResolver,
        *,
        known_hosts: Path,
        request_hash: str = "",
        failure_sink: Callable[[Mapping[str, Any]], None] | None = None,
        invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    ):
        self.resolve_artifact = resolve_artifact
        self.known_hosts = known_hosts
        self.request_hash = request_hash
        self.failure_sink = failure_sink
        self.invoke = invoke

    def __call__(self, parameters: Mapping[str, str]) -> Mapping[str, Any]:
        archive = self.resolve_artifact(parameters["artifact_ref"])
        if not archive.is_file() or _digest_file(archive) != "sha256:" + parameters["source_archive_sha256"].removeprefix("sha256:"):
            raise EvaluationError("resolved source artifact digest mismatch")
        if archive.stat().st_size > int(parameters["max_archive_bytes"]):
            raise EvaluationError("resolved source artifact exceeds its bound")
        provider_source = Path(__file__).read_bytes()
        ssh_fd = os.open(SSH_EXECUTABLE, os.O_RDONLY | os.O_NOFOLLOW)
        hosts_fd = os.open(self.known_hosts, os.O_RDONLY | os.O_NOFOLLOW)
        sealed_hosts_fd = None
        try:
            ssh_stat, hosts_stat = os.fstat(ssh_fd), os.fstat(hosts_fd)
            if not stat.S_ISREG(ssh_stat.st_mode) or not stat.S_ISREG(hosts_stat.st_mode) or hosts_stat.st_uid not in {0, os.geteuid()} or hosts_stat.st_mode & 0o022:
                raise EvaluationError("SSH executable or known-hosts ownership is unsafe")
            ssh_bytes, hosts_bytes = os.read(ssh_fd, ssh_stat.st_size), os.read(hosts_fd, hosts_stat.st_size)
            ssh_digest = "sha256:" + sha256(ssh_bytes).hexdigest()
            hosts_digest = "sha256:" + sha256(hosts_bytes).hexdigest()
            if ssh_digest != "sha256:" + parameters["ssh_sha256"].removeprefix("sha256:") or hosts_digest != "sha256:" + parameters["known_hosts_sha256"].removeprefix("sha256:"):
                raise EvaluationError("SSH executable or host-key pin mismatch")
            try:
                host_line = hosts_bytes.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise EvaluationError("known-hosts is not ASCII") from exc
            if not re.fullmatch(r"(100\.107\.99\.66|\[100\.107\.99\.66\]:22) (ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) ([A-Za-z0-9+/]+={0,2})", host_line):
                raise EvaluationError("known-hosts is not the one admitted host key")
            sealed_hosts_fd = _sealed_memfd("tgw-known-hosts", hosts_bytes)
            os.lseek(ssh_fd, 0, os.SEEK_SET)
            command = [
                f"/proc/self/fd/{ssh_fd}",
                "-F",
                "/dev/null",
                "-oBatchMode=yes",
                "-oClearAllForwardings=yes",
                "-oStrictHostKeyChecking=yes",
                f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts_fd}",
                "--",
                f"{REMOTE_USER}@{REMOTE_HOST}",
                "sudo",
                "-n",
                "--",
                REMOTE_PYTHON,
                "-I",
                "-c",
                BOOTSTRAP,
            ]
            header = _packet_header(parameters, provider_source, self.request_hash)
            pass_fds = (ssh_fd, sealed_hosts_fd)
            if self.invoke is None:
                completed = self._invoke_streaming(command, header, archive, timeout=int(parameters["max_duration_seconds"]) + 30, max_output=int(parameters["max_output_bytes"]), pass_fds=pass_fds)
            else:
                completed = self.invoke(command, input=header + archive.read_bytes(), capture_output=True, timeout=int(parameters["max_duration_seconds"]) + 30, check=False, pass_fds=pass_fds)
        finally:
            if sealed_hosts_fd is not None:
                os.close(sealed_hosts_fd)
            os.close(hosts_fd)
            os.close(ssh_fd)
        if completed.returncode:
            try:
                failure = validate_failure_receipt(json.loads(completed.stdout), parameters, request_hash=self.request_hash)
            except (json.JSONDecodeError, UnicodeDecodeError, EvaluationError) as exc:
                raise EvaluationError("remote reviewed evaluation failed without a valid failure receipt") from exc
            if self.failure_sink is not None:
                self.failure_sink(failure)
            raise RemoteEvaluationFailure(failure)
        if len(completed.stdout) > int(parameters["max_output_bytes"]):
            raise EvaluationError("remote reviewed evaluation output exceeded its bound")
        try:
            result = json.loads(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvaluationError("remote reviewed evaluation returned malformed JSON") from exc
        if not isinstance(result, dict):
            raise EvaluationError("remote reviewed evaluation receipt is not an object")
        return result

    @staticmethod
    def _invoke_streaming(command: list[str], header: bytes, archive: Path, *, timeout: int, max_output: int, pass_fds: tuple[int, ...]) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryFile() as packet:
            packet.write(header)
            with archive.open("rb") as source:
                shutil.copyfileobj(source, packet, length=1024 * 1024)
            packet.seek(0)
            process = subprocess.Popen(command, stdin=packet, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, pass_fds=pass_fds)
            assert process.stdout is not None
            output = bytearray()
            deadline = time.monotonic() + timeout
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ)
            while process.poll() is None or selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait()
                    raise EvaluationError("remote reviewed evaluation timed out")
                events = selector.select(min(remaining, 0.25))
                for key, _ in events:
                    block = key.fileobj.read1(min(65536, max_output + 1 - len(output)))
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(block)
                if len(output) > max_output:
                    process.kill()
                    process.wait()
                    raise EvaluationError("remote reviewed evaluation output exceeded its bound")
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise EvaluationError("remote reviewed evaluation timed out") from exc
        return subprocess.CompletedProcess(command, returncode, bytes(output), b"")


def _run(argv: list[str], *, cwd: Path, timeout: int, max_output: int = 16 * 1024 * 1024) -> str:
    clean_env = {
        "PATH": "/run/current-system/sw/bin:/usr/bin:/bin",
        "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=clean_env)
    assert process.stdout is not None
    output = bytearray()
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    while process.poll() is None or selector.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            process.kill()
            process.wait()
            raise StepFailure("fixed evaluation step timed out", step=_subprocess_step(argv), return_code=None, stdout=bytes(output), stderr=b"")
        for key, _ in selector.select(min(remaining, 0.25)):
            block = key.fileobj.read1(min(65536, max_output + 1 - len(output)))
            if not block:
                selector.unregister(key.fileobj)
            else:
                output.extend(block)
        if len(output) > max_output:
            process.kill()
            process.wait()
            raise StepFailure("fixed evaluation step exceeded its output bound", step=_subprocess_step(argv), return_code=None, stdout=bytes(output), stderr=b"")
    return_code = process.wait()
    if return_code != 0:
        raise StepFailure("fixed evaluation step failed", step=_subprocess_step(argv), return_code=max(-255, min(255, return_code)), stdout=bytes(output), stderr=b"")
    return output.decode()


def _subprocess_step(argv: list[str]) -> str:
    joined = " ".join(argv)
    if "write-tree" in argv:
        return "git-write-tree"
    if " init " in f" {joined} ":
        return "git-init"
    if " add " in f" {joined} ":
        return "git-add"
    if "systemd-analyze" in argv[0]:
        return "systemd-version" if "--version" in argv else "systemd-verify"
    if "--requisites" in argv:
        return "nix-requisites"
    if " hash " in f" {joined} ":
        return "nix-hash"
    if " eval " in f" {joined} ":
        return "nix-eval"
    if " build " in f" {joined} ":
        return "nix-build"
    if "--version" in argv:
        return "nix-version"
    return "none"


def _safe_extract(archive: Path, target: Path, *, expected_root: str, max_files: int, max_bytes: int) -> str:
    with tarfile.open(archive) as source:
        commit = source.pax_headers.get("comment", "")
        if not commit or len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise EvaluationError("source archive lacks an exact Git commit identity")
        members = source.getmembers()
        if len(members) > max_files or sum(member.size for member in members) > max_bytes:
            raise EvaluationError("source archive exceeds unpack bounds")
        normalized = []
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or ".git" in member_path.parts or not (member.isdir() or member.isfile()):
                raise EvaluationError("source archive contains an unsafe member")
            if not member_path.parts or member_path.parts[0] != expected_root:
                raise EvaluationError("source archive does not have the exact single root")
            normalized.append(member_path.as_posix().rstrip("/"))
        if len(normalized) != len(set(normalized)):
            raise EvaluationError("source archive contains duplicate normalized paths")
        source.extractall(target, filter="data")
        return commit


def _prepare_scratch_root(scratch_root: Path, *, expected_uid: int) -> tuple[int, int, bool]:
    """Open the parent and create only the fixed child when it is absent."""
    parent = scratch_root.parent
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        try:
            metadata = os.stat(scratch_root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            os.mkdir(scratch_root.name, mode=0o700, dir_fd=parent_fd)
            created = True
            metadata = os.stat(scratch_root.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise EvaluationError("scratch root is not root-owned mode 0700")
        root_fd = os.open(scratch_root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        return parent_fd, root_fd, created
    except Exception:
        if created:
            os.rmdir(scratch_root.name, dir_fd=parent_fd)
        os.close(parent_fd)
        raise


def execute_packet(stream: io.BufferedReader, *, run: Callable[..., str] = _run, scratch_root: Path = Path("/var/tmp/tgw-reviewed-evaluation"), scratch_uid: int = 0) -> dict[str, Any]:
    global _FAILURE_CONTEXT
    _FAILURE_CONTEXT = {"stage": "request-validation", "cleanup_result": "not-created"}
    header = stream.read(8)
    if len(header) != 8:
        raise EvaluationError("evaluation packet header is truncated")
    request_size = struct.unpack("!Q", header)[0]
    if request_size > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    request_raw = stream.read(request_size)
    if len(request_raw) != request_size:
        raise EvaluationError("evaluation request is truncated")
    bound = _validate_remote_parameters(json.loads(request_raw))
    _FAILURE_CONTEXT.update(
        {
            "request_hash": globals().get("_BOOTSTRAP_REQUEST_HASH", "unknown"),
            "effect_hash": _effect_hash(bound),
            "generation": bound["generation"],
            "provider_sha256": bound["provider_sha256"],
            "stage": "provider-identity",
        }
    )
    provider_digest = globals().get("_BOOTSTRAP_PROVIDER_SHA256") or _digest_file(Path(__file__))
    if provider_digest != "sha256:" + bound["provider_sha256"].removeprefix("sha256:"):
        raise EvaluationError("installed evaluation provider digest mismatch")
    executable_digests = {
        "remote_python": _digest_file(Path(REMOTE_PYTHON)),
        **{name: _digest_file(Path(path)) for name, path in EXECUTABLES.items()},
    }
    expected_digests = {name: "sha256:" + bound[name + "_sha256"].removeprefix("sha256:") for name in executable_digests}
    if executable_digests != expected_digests:
        raise EvaluationError("remote evaluation executable digest mismatch")
    _FAILURE_CONTEXT["stage"] = "scratch-root"
    timeout = int(bound["max_duration_seconds"])
    parent_fd, root_fd, root_created = _prepare_scratch_root(scratch_root, expected_uid=scratch_uid)
    _FAILURE_CONTEXT.update({"scratch_root_created": root_created, "cleanup_result": "unknown", "stage": "run-scratch"})
    scratch_name = "run-" + secrets.token_hex(16)
    os.mkdir(scratch_name, mode=0o700, dir_fd=root_fd)
    _FAILURE_CONTEXT["run_created"] = True
    scratch = scratch_root / scratch_name
    scratch_stat = scratch.lstat()
    if scratch_stat.st_uid != scratch_uid or stat.S_IMODE(scratch_stat.st_mode) != 0o700 or not stat.S_ISDIR(scratch_stat.st_mode):
        raise EvaluationError("atomic scratch directory identity mismatch")
    archive = scratch / "source.tar"
    extract_root = scratch / "source"
    source = extract_root / bound["archive_root"]
    receipt = None
    try:
        _FAILURE_CONTEXT["stage"] = "archive-stream"
        scratch_fd = os.open(scratch_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        archive_fd = os.open("source.tar", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=scratch_fd)
        os.close(scratch_fd)
        with os.fdopen(archive_fd, "wb") as sink:
            remaining = int(bound["max_archive_bytes"]) + 1
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                if not block:
                    break
                sink.write(block)
                remaining -= len(block)
            if remaining == 0 or stream.read(1):
                raise EvaluationError("received archive exceeds its bound")
        if _digest_file(archive) != "sha256:" + bound["source_archive_sha256"].removeprefix("sha256:"):
            raise EvaluationError("received archive digest mismatch")
        extract_root.mkdir()
        _FAILURE_CONTEXT["stage"] = "archive-verify"
        archive_commit = _safe_extract(archive, extract_root, expected_root=bound["archive_root"], max_files=int(bound["max_files"]), max_bytes=int(bound["max_unpacked_bytes"]))
        if archive_commit != bound["source_commit"]:
            raise EvaluationError("archive commit identity mismatch")
        git = [EXECUTABLES["git"], "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false"]
        _FAILURE_CONTEXT["stage"] = "source-tree"
        run(git + ["init", "-q"], cwd=source, timeout=timeout)
        run(git + ["add", "-A"], cwd=source, timeout=timeout)
        if run(git + ["write-tree"], cwd=source, timeout=timeout).strip() != bound["source_tree"]:
            raise EvaluationError("unpacked source tree mismatch")
        _FAILURE_CONTEXT["stage"] = "source-digests"
        lock_matches = _digest_file(source / "flake.lock") == "sha256:" + bound["flake_lock_sha256"].removeprefix("sha256:")
        module_matches = _digest_file(source / bound["module_path"]) == "sha256:" + bound["module_sha256"].removeprefix("sha256:")
        if not lock_matches or not module_matches:
            raise EvaluationError("lock or module digest mismatch")
        _FAILURE_CONTEXT["stage"] = "nix-eval"
        base = [
            EXECUTABLES["nix"],
            "--offline",
            "--option",
            "substituters",
            "",
            "--option",
            "allow-import-from-derivation",
            "false",
            "--option",
            "pure-eval",
            "true",
            "--no-write-lock-file",
        ]
        drv = run(base + ["eval", "--raw", ".#nixosConfigurations.tgw-prod.config.system.build.toplevel.drvPath"], cwd=source, timeout=timeout).strip()
        _FAILURE_CONTEXT["stage"] = "nix-build"
        build_log = run(base + ["build", "--no-link", "--print-out-paths", ".#nixosConfigurations.tgw-prod.config.system.build.toplevel"], cwd=source, timeout=timeout)
        closure = build_log.strip()
        if "\n" in closure or not closure.startswith("/nix/store/"):
            raise EvaluationError("Nix build returned an unexpected closure set")
        unit_paths = [Path(closure) / "etc/systemd/system" / unit for unit in UNITS]
        _FAILURE_CONTEXT["stage"] = "unit-extract"
        if any(not path.is_file() for path in unit_paths):
            raise EvaluationError("generated unit set is incomplete")
        _FAILURE_CONTEXT["stage"] = "systemd-verify"
        verify_log = run([EXECUTABLES["systemd_analyze"], "verify", *map(str, unit_paths)], cwd=source, timeout=timeout)
        _FAILURE_CONTEXT["stage"] = "closure-manifest"
        eval_log = drv + "\n"
        requisites_raw = run([EXECUTABLES["nix_store"], "--query", "--requisites", closure], cwd=source, timeout=timeout)
        requisites = sorted(set(requisites_raw.splitlines()))
        store_path = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+")
        if not requisites or len(requisites) > 10_000 or closure not in requisites or any(not store_path.fullmatch(item) for item in requisites):
            raise EvaluationError("Nix closure requisites are incomplete")
        closure_manifest = []
        for item in requisites:
            nar_hash = run(base + ["hash", "path", "--type", "sha256", "--base16", item], cwd=source, timeout=timeout).strip()
            if not re.fullmatch(r"[0-9a-f]{64}", nar_hash):
                raise EvaluationError("Nix requisite NAR hash is invalid")
            closure_manifest.append({"path": item, "nar_sha256": "sha256:" + nar_hash})
        closure_manifest_sha256 = "sha256:" + sha256(_canonical(closure_manifest)).hexdigest()
        _FAILURE_CONTEXT["stage"] = "version-probes"
        receipt = {
            "schema": bound["output_schema"],
            "outcome": "verified",
            "source_commit": bound["source_commit"],
            "source_tree": bound["source_tree"],
            "source_archive_sha256": bound["source_archive_sha256"],
            "flake_lock_sha256": bound["flake_lock_sha256"],
            "module_sha256": bound["module_sha256"],
            "provider_sha256": bound["provider_sha256"],
            "executables": EXECUTABLES,
            "ssh_sha256": bound["ssh_sha256"],
            "known_hosts_sha256": bound["known_hosts_sha256"],
            "executable_sha256": executable_digests,
            "scratch_id": bound["scratch_id"],
            "activate": False,
            "profile_write": False,
            "home_db_write": False,
            "system": bound["system"],
            "evaluation_target": bound["evaluation_target"],
            "evaluated_config_drv": drv,
            "closure_manifest": closure_manifest,
            "closure_manifest_ref": "inline:" + closure_manifest_sha256,
            "closure_manifest_sha256": closure_manifest_sha256,
            "closure_path_count": len(closure_manifest),
            "eval_log_sha256": "sha256:" + sha256(eval_log.encode()).hexdigest(),
            "build_log_sha256": "sha256:" + sha256(build_log.encode()).hexdigest(),
            "systemd_verify_output_sha256": "sha256:" + sha256(verify_log.encode()).hexdigest(),
            "systemd_verify_exit": 0,
            "systemd_version": int(run([EXECUTABLES["systemd_analyze"], "--version"], cwd=source, timeout=timeout).split()[1]),
            "nix_version": run([EXECUTABLES["nix"], "--version"], cwd=source, timeout=timeout).strip(),
            "unit_sha256": {unit: _digest_file(path) for unit, path in zip(UNITS, unit_paths, strict=True)},
            "evidence": [],
        }
    finally:
        _FAILURE_CONTEXT.update({"stage": "cleanup", "cleanup_attempted": True})
        try:
            shutil.rmtree(scratch, ignore_errors=False)
            os.close(root_fd)
            if root_created:
                os.rmdir(scratch_root.name, dir_fd=parent_fd)
            _FAILURE_CONTEXT["cleanup_result"] = "removed" if root_created else "retained-existing"
        except Exception:
            _FAILURE_CONTEXT["cleanup_result"] = "failed"
            raise
        finally:
            try:
                os.close(parent_fd)
            except OSError:
                pass
    if scratch.exists() or receipt is None:
        raise EvaluationError("scratch cleanup was not verified")
    receipt["scratch_root"] = {
        "path": str(scratch_root),
        "created_by_attempt": root_created,
        "final_state": "removed" if root_created else "retained-existing",
    }
    receipt["cleanup"] = "removed"
    receipt["receipt_sha256"] = "sha256:" + sha256(_canonical({key: value for key, value in receipt.items() if key != "evidence"})).hexdigest()
    receipt["evidence"] = ["nixos-evaluation:" + receipt["receipt_sha256"]]
    return receipt


def main() -> int:
    try:
        receipt = execute_packet(sys.stdin.buffer)
        sys.stdout.buffer.write(_canonical(receipt))
        return 0
    except Exception as exc:
        exception_class = type(exc).__name__ if type(exc).__name__ in EXCEPTION_CLASSES else "EvaluationError"
        code = "CLEANUP_FAILED" if _FAILURE_CONTEXT.get("stage") == "cleanup" else "SUBPROCESS_FAILED" if "failed" in str(exc).lower() else "VALIDATION_REFUSED"
        step_failure = exc if isinstance(exc, StepFailure) else None
        receipt = create_failure_receipt(
            context=_FAILURE_CONTEXT,
            stage=_FAILURE_CONTEXT.get("stage", "internal"),
            diagnostic_code=code,
            exception_class=exception_class,
            cleanup_result=_FAILURE_CONTEXT.get("cleanup_result", "unknown"),
            subprocess_step=step_failure.step if step_failure else "none",
            return_code=step_failure.return_code if step_failure else None,
            stdout=step_failure.stdout if step_failure else b"",
            stderr=step_failure.stderr if step_failure else b"",
        )
        sys.stdout.buffer.write(_canonical(receipt))
        return 1
