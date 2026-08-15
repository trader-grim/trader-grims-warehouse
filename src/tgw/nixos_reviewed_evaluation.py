"""Closed remote provider for immutable, non-activating NixOS evaluation."""

from __future__ import annotations

import fcntl
import io
import json
import os
import re
import secrets
import selectors
import shlex
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
    "import hashlib,json,struct,sys; n=struct.unpack('!Q',sys.stdin.buffer.read(8))[0]; "
    "s=sys.stdin.buffer.read(n); h=sys.stdin.buffer.read(64).decode(); r=sys.stdin.buffer.read(71).decode(); "
    "d={'schema':'tgw-nixos-reviewed-evaluation-bootstrap-failure/v1','outcome':'FAILED','stage':'request-validation',"
    "'diagnostic_code':'IDENTITY_MISMATCH','exception_class':'EvaluationError','request_hash':r,'provider_sha256':'sha256:'+h,"
    "'stdout_bytes':0,'stdout_sha256':'sha256:'+hashlib.sha256(b'').hexdigest(),'stderr_bytes':0,'stderr_sha256':'sha256:'+hashlib.sha256(b'').hexdigest(),"
    "'forbidden_effects':{'activation':False,'profile_write':False,'home_db_write':False,'live_flake_write':False,'deployment':False}}; "
    "d['receipt_sha256']='sha256:'+hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':')).encode()).hexdigest(); "
    "hashlib.sha256(s).hexdigest()==h or (sys.stdout.write(json.dumps(d,sort_keys=True,separators=(',',':'))),sys.exit(91)); "
    "exec(compile(s,'<tgw-reviewed-evaluator>','exec'),"
    "{'__name__':'__main__','_BOOTSTRAP_PROVIDER_SHA256':'sha256:'+h,'_BOOTSTRAP_REQUEST_HASH':r,"
    "**({'_BOOTSTRAP_EXECUTOR':globals()['_BOOTSTRAP_EXECUTOR']} if '_BOOTSTRAP_EXECUTOR' in globals() else {})})"
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
BOOTSTRAP_FAILURE_SCHEMA = "tgw-nixos-reviewed-evaluation-bootstrap-failure/v1"
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
    def __init__(self, receipt: Mapping[str, Any], *, receipt_ref: Mapping[str, Any] | None = None, persistence_error: bool = False):
        super().__init__("remote reviewed evaluation emitted a validated failure receipt")
        self.receipt = dict(receipt)
        self.receipt_ref = dict(receipt_ref) if receipt_ref is not None else None
        self.persistence_error = persistence_error
        self.reconciliation_outcome = "AMBIGUOUS" if persistence_error else receipt["outcome"]


class StepFailure(EvaluationError):
    def __init__(self, message: str, *, step: str, diagnostic_code: str, return_code: int | None, stdout: bytes, stderr: bytes):
        super().__init__(message)
        self.step, self.return_code, self.stdout, self.stderr = step, return_code, stdout, stderr
        self.diagnostic_code = diagnostic_code


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


def create_bootstrap_failure_receipt(*, request_hash: str, provider_sha256: str, diagnostic_code: str, exception_class: str) -> dict[str, Any]:
    receipt = {
        "schema": BOOTSTRAP_FAILURE_SCHEMA,
        "outcome": "FAILED",
        "stage": "request-validation",
        "diagnostic_code": diagnostic_code,
        "exception_class": exception_class,
        "request_hash": request_hash,
        "provider_sha256": provider_sha256,
        "stdout_bytes": 0,
        "stdout_sha256": "sha256:" + sha256(b"").hexdigest(),
        "stderr_bytes": 0,
        "stderr_sha256": "sha256:" + sha256(b"").hexdigest(),
        "forbidden_effects": {"activation": False, "profile_write": False, "home_db_write": False, "live_flake_write": False, "deployment": False},
    }
    receipt["receipt_sha256"] = "sha256:" + sha256(_canonical(receipt)).hexdigest()
    return receipt


def validate_bootstrap_failure_receipt(value: Any, *, request_hash: str, provider_sha256: str) -> dict[str, Any]:
    required = {
        "schema",
        "outcome",
        "stage",
        "diagnostic_code",
        "exception_class",
        "request_hash",
        "provider_sha256",
        "stdout_bytes",
        "stdout_sha256",
        "stderr_bytes",
        "stderr_sha256",
        "forbidden_effects",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or len(_canonical(value)) > 4096 or set(value) != required:
        raise EvaluationError("remote bootstrap failure receipt schema is invalid")
    if value["schema"] != BOOTSTRAP_FAILURE_SCHEMA or value["outcome"] != "FAILED" or value["stage"] != "request-validation":
        raise EvaluationError("remote bootstrap failure receipt outcome is invalid")
    if value["request_hash"] != request_hash or value["provider_sha256"] != provider_sha256:
        raise EvaluationError("remote bootstrap failure receipt binding mismatch")
    if value["diagnostic_code"] not in DIAGNOSTIC_CODES or value["exception_class"] not in EXCEPTION_CLASSES:
        raise EvaluationError("remote bootstrap failure diagnostic is invalid")
    if value["stdout_bytes"] != 0 or value["stderr_bytes"] != 0 or value["stdout_sha256"] != "sha256:" + sha256(b"").hexdigest() or value["stderr_sha256"] != "sha256:" + sha256(b"").hexdigest():
        raise EvaluationError("remote bootstrap failure exposed diagnostics")
    if set(value["forbidden_effects"]) != {"activation", "profile_write", "home_db_write", "live_flake_write", "deployment"} or any(value["forbidden_effects"].values()):
        raise EvaluationError("remote bootstrap failure claims a forbidden effect")
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256")
    if claimed != "sha256:" + sha256(_canonical(unsigned)).hexdigest():
        raise EvaluationError("remote bootstrap failure receipt self-hash mismatch")
    return dict(value)


def _effect_hash(effect: Mapping[str, Any]) -> str:
    return "effect:sha256:" + sha256(_canonical(effect)).hexdigest()


def validate_failure_receipt(value: Any, effect: Mapping[str, Any], *, request_hash: str) -> dict[str, Any]:
    parameters = effect["parameters"]
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
        or value["effect_hash"] != _effect_hash(effect)
        or value["generation"] != effect["generation"]
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
        if type(value[prefix + "_bytes"]) is not int or not 0 <= value[prefix + "_bytes"] <= int(parameters["max_output_bytes"]) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value[prefix + "_sha256"]):
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


def serialize_remote_argv(argv: list[str]) -> str:
    """Serialize fixed argv once for OpenSSH's mandatory remote login shell."""
    if not argv or any("\x00" in token or "\n" in token or "\r" in token for token in argv):
        raise EvaluationError("remote argv contains an unsafe token")
    return shlex.join(argv)


class ImmutableFailureReceiptStore:
    """Controller-owned, content-addressed receipt store with atomic exclusive writes."""

    def __init__(self, root: Path):
        self.root = root
        parent_meta = root.parent.lstat()
        if not stat.S_ISDIR(parent_meta.st_mode) or parent_meta.st_uid not in {0, os.geteuid()} or parent_meta.st_mode & 0o022:
            raise EvaluationError("failure receipt store parent is unsafe")
        self._parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        created = False
        try:
            try:
                os.mkdir(root.name, mode=0o700, dir_fd=self._parent_fd)
                created = True
            except FileExistsError:
                pass
            metadata = os.stat(root.name, dir_fd=self._parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise EvaluationError("failure receipt store identity is unsafe")
            self._directory_fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)
        except Exception:
            os.close(self._parent_fd)
            raise
        self.readiness = {
            "schema": "tgw-nixos-evaluation-failure-store-readiness/v1",
            "path": str(root),
            "created": created,
            "owner_uid": metadata.st_uid,
            "mode": "0700",
            "ready": True,
        }
        self.readiness["receipt_sha256"] = "sha256:" + sha256(_canonical(self.readiness)).hexdigest()

    def close(self) -> None:
        os.close(self._directory_fd)
        os.close(self._parent_fd)

    def persist(self, receipt: Mapping[str, Any]) -> dict[str, Any]:
        content = _canonical(dict(receipt))
        digest = sha256(content).hexdigest()
        name = digest + ".json"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._directory_fd)
        except FileExistsError:
            metadata = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o400 or metadata.st_size != len(content):
                raise EvaluationError("existing failure receipt artifact is unsafe")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
            with os.fdopen(fd, "rb") as existing:
                opened = os.fstat(existing.fileno())
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino) or sha256(existing.read()).hexdigest() != digest:
                    raise EvaluationError("existing failure receipt artifact is contradictory")
        else:
            with os.fdopen(fd, "wb") as sink:
                sink.write(content)
                sink.flush()
                os.fsync(sink.fileno())
            os.fsync(self._directory_fd)
        return {"artifact_ref": "artifact:sha256:" + digest, "path": str(self.root / name), "sha256": "sha256:" + digest, "size": len(content)}


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


def _packet_header(effect: Mapping[str, Any], provider_source: bytes, request_hash: str) -> bytes:
    request = _canonical(dict(effect))
    if len(request) > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", request_hash):
        raise EvaluationError("request hash is invalid")
    return struct.pack("!Q", len(provider_source)) + provider_source + sha256(provider_source).hexdigest().encode() + request_hash.encode() + struct.pack("!Q", len(request)) + request


def _validate_remote_effect(value: Any) -> tuple[dict[str, Any], dict[str, str]]:
    """Standalone mirror of the closed effect boundary; imports no TGW package."""
    if not isinstance(value, dict) or set(value) != {"kind", "generation", "parameters"}:
        raise EvaluationError("remote evaluation effect is not the exact typed envelope")
    if value["kind"] != "nixos-reviewed-evaluation" or not isinstance(value["generation"], str) or not value["generation"]:
        raise EvaluationError("remote evaluation effect identity is invalid")
    effect_generation = value["generation"]
    parameters = value["parameters"]
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
        "input_closure_manifest_json",
        "input_closure_manifest_sha256",
        "input_closure_path_count",
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
    }
    if not isinstance(parameters, dict) or set(parameters) != keys or any(not isinstance(item, str) or not item for item in parameters.values()):
        raise EvaluationError("remote evaluation parameters are not the exact typed object")
    value = parameters
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
    try:
        input_closure = json.loads(value["input_closure_manifest_json"])
    except json.JSONDecodeError as exc:
        raise EvaluationError("input closure manifest is invalid JSON") from exc
    store_path = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[A-Za-z0-9+._?=-]+")
    if (
        not isinstance(input_closure, list)
        or not 1 <= len(input_closure) <= 10_000
        or value["input_closure_path_count"] != str(len(input_closure))
        or "sha256:" + sha256(_canonical(input_closure)).hexdigest() != "sha256:" + value["input_closure_manifest_sha256"].removeprefix("sha256:")
    ):
        raise EvaluationError("input closure manifest binding is invalid")
    paths = []
    for item in input_closure:
        if (
            not isinstance(item, dict)
            or set(item) != {"lock_node", "lock_rev", "lock_nar_hash", "path", "nar_sha256"}
            or item["lock_node"] != "nixpkgs"
            or item["lock_rev"] != "ac62194c3917d5f474c1a844b6fd6da2db95077d"
            or item["lock_nar_hash"] != "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w="
            or not isinstance(item["path"], str)
            or not store_path.fullmatch(item["path"])
            or not isinstance(item["nar_sha256"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["nar_sha256"])
        ):
            raise EvaluationError("input closure manifest entry is invalid")
        paths.append(item["path"])
    if paths != sorted(set(paths)):
        raise EvaluationError("input closure paths must be unique and sorted")
    if len(input_closure) != 1:
        raise EvaluationError("input closure must contain exactly the locked nixpkgs source")
    identity = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}")
    if not value["scratch_id"].startswith("nixos-review:") or not identity.fullmatch(value["scratch_id"]) or not identity.fullmatch(value["operation_id"]):
        raise EvaluationError("remote symbolic identity is invalid")
    bounds = tuple(int(value[key]) for key in ("minimum_systemd_version", "max_duration_seconds", "max_output_bytes", "max_archive_bytes", "max_unpacked_bytes", "max_files"))
    systemd, duration, output, archive, unpacked, files = bounds
    if systemd < 257 or not 1 <= duration <= 900 or not 1024 <= output <= 16 * 1024**2 or not 1024 <= archive <= 128 * 1024**2 or not archive <= unpacked <= 512 * 1024**2 or not 1 <= files <= 100_000:
        raise EvaluationError("remote resource bound is invalid")
    return {"kind": "nixos-reviewed-evaluation", "generation": str(effect_generation), "parameters": dict(value)}, dict(value)


class SshReviewedEvaluationProvider:
    """Resolve one content-addressed artifact and invoke one fixed remote helper."""

    def __init__(
        self,
        resolve_artifact: ArtifactResolver,
        *,
        known_hosts: Path,
        request_hash: str = "",
        failure_store: ImmutableFailureReceiptStore | None = None,
        invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    ):
        self.resolve_artifact = resolve_artifact
        self.known_hosts = known_hosts
        self.request_hash = request_hash
        self.failure_store = failure_store
        self.invoke = invoke

    def __call__(self, effect: Mapping[str, Any]) -> Mapping[str, Any]:
        canonical_effect, parameters = _validate_remote_effect(effect)
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
            remote_command = serialize_remote_argv(["sudo", "-n", "--", REMOTE_PYTHON, "-I", "-c", BOOTSTRAP])
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
                remote_command,
            ]
            header = _packet_header(canonical_effect, provider_source, self.request_hash)
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
                untrusted = json.loads(completed.stdout)
                if isinstance(untrusted, dict) and untrusted.get("schema") == BOOTSTRAP_FAILURE_SCHEMA:
                    failure = validate_bootstrap_failure_receipt(untrusted, request_hash=self.request_hash, provider_sha256=parameters["provider_sha256"])
                else:
                    failure = validate_failure_receipt(untrusted, canonical_effect, request_hash=self.request_hash)
            except (json.JSONDecodeError, UnicodeDecodeError, EvaluationError) as exc:
                raise EvaluationError("remote reviewed evaluation failed without a valid failure receipt") from exc
            receipt_ref = None
            persistence_error = False
            try:
                if self.failure_store is None:
                    raise EvaluationError("immutable failure receipt store is not configured")
                receipt_ref = self.failure_store.persist(failure)
            except (OSError, EvaluationError):
                persistence_error = True
            raise RemoteEvaluationFailure(failure, receipt_ref=receipt_ref, persistence_error=persistence_error)
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
            raise StepFailure("fixed evaluation step timed out", step=_subprocess_step(argv), diagnostic_code="BOUND_EXCEEDED", return_code=None, stdout=bytes(output), stderr=b"")
        for key, _ in selector.select(min(remaining, 0.25)):
            block = key.fileobj.read1(min(65536, max_output + 1 - len(output)))
            if not block:
                selector.unregister(key.fileobj)
            else:
                output.extend(block)
        if len(output) > max_output:
            process.kill()
            process.wait()
            raise StepFailure("fixed evaluation step exceeded its output bound", step=_subprocess_step(argv), diagnostic_code="BOUND_EXCEEDED", return_code=None, stdout=bytes(output), stderr=b"")
    return_code = process.wait()
    if return_code != 0:
        raise StepFailure(
            "fixed evaluation step failed", step=_subprocess_step(argv), diagnostic_code="SUBPROCESS_FAILED", return_code=max(-255, min(255, return_code)), stdout=bytes(output), stderr=b""
        )
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


def execute_packet(
    stream: io.BufferedReader,
    *,
    run: Callable[..., str] = _run,
    scratch_root: Path = Path("/var/tmp/tgw-reviewed-evaluation"),
    scratch_uid: int = 0,
    stage_hook: Callable[[str], None] | None = None,
    cleanup_tree: Callable[..., None] = shutil.rmtree,
) -> dict[str, Any]:
    global _FAILURE_CONTEXT
    _FAILURE_CONTEXT = {"stage": "request-validation", "cleanup_result": "not-created"}

    def enter(stage: str) -> None:
        _FAILURE_CONTEXT["stage"] = stage
        if stage_hook is not None:
            stage_hook(stage)

    header = stream.read(8)
    if len(header) != 8:
        raise EvaluationError("evaluation packet header is truncated")
    request_size = struct.unpack("!Q", header)[0]
    if request_size > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    request_raw = stream.read(request_size)
    if len(request_raw) != request_size:
        raise EvaluationError("evaluation request is truncated")
    effect, bound = _validate_remote_effect(json.loads(request_raw))
    input_closure = json.loads(bound["input_closure_manifest_json"])
    _FAILURE_CONTEXT.update(
        {
            "request_hash": globals().get("_BOOTSTRAP_REQUEST_HASH", "unknown"),
            "effect_hash": _effect_hash(effect),
            "generation": effect["generation"],
            "provider_sha256": bound["provider_sha256"],
            "stage": "provider-identity",
        }
    )
    enter("provider-identity")
    provider_digest = globals().get("_BOOTSTRAP_PROVIDER_SHA256") or _digest_file(Path(__file__))
    if provider_digest != "sha256:" + bound["provider_sha256"].removeprefix("sha256:"):
        raise EvaluationError("installed evaluation provider digest mismatch")
    enter("executable-identity")
    executable_digests = {
        "remote_python": _digest_file(Path(REMOTE_PYTHON)),
        **{name: _digest_file(Path(path)) for name, path in EXECUTABLES.items()},
    }
    expected_digests = {name: "sha256:" + bound[name + "_sha256"].removeprefix("sha256:") for name in executable_digests}
    if executable_digests != expected_digests:
        raise EvaluationError("remote evaluation executable digest mismatch")
    enter("scratch-root")
    timeout = int(bound["max_duration_seconds"])
    parent_fd, root_fd, root_created = _prepare_scratch_root(scratch_root, expected_uid=scratch_uid)
    _FAILURE_CONTEXT.update({"scratch_root_created": root_created, "cleanup_result": "unknown", "stage": "run-scratch"})
    enter("run-scratch")
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
        enter("archive-stream")
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
        enter("archive-verify")
        archive_commit = _safe_extract(archive, extract_root, expected_root=bound["archive_root"], max_files=int(bound["max_files"]), max_bytes=int(bound["max_unpacked_bytes"]))
        if archive_commit != bound["source_commit"]:
            raise EvaluationError("archive commit identity mismatch")
        git = [EXECUTABLES["git"], "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false"]
        enter("source-tree")
        run(git + ["init", "-q"], cwd=source, timeout=timeout)
        # The archive is already bounded and every member was path/type/root
        # validated.  Force-index all of it so repository ignore rules cannot
        # hide files that are tracked in the candidate tree.
        run(git + ["add", "-f", "-A"], cwd=source, timeout=timeout)
        if run(git + ["write-tree"], cwd=source, timeout=timeout).strip() != bound["source_tree"]:
            raise EvaluationError("unpacked source tree mismatch")
        enter("source-digests")
        lock_matches = _digest_file(source / "flake.lock") == "sha256:" + bound["flake_lock_sha256"].removeprefix("sha256:")
        module_matches = _digest_file(source / bound["module_path"]) == "sha256:" + bound["module_sha256"].removeprefix("sha256:")
        if not lock_matches or not module_matches:
            raise EvaluationError("lock or module digest mismatch")
        enter("nix-eval")
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
        resolved_input = run(
            base + ["eval", "--raw", ".#inputIdentities.nixpkgs.outPath"],
            cwd=source,
            timeout=timeout,
        ).strip()
        if resolved_input != input_closure[0]["path"]:
            raise EvaluationError("offline resolved input set does not match the exact manifest")
        for item in input_closure:
            observed = run(base + ["hash", "path", "--type", "sha256", "--base16", item["path"]], cwd=source, timeout=timeout).strip()
            if observed != item["nar_sha256"].removeprefix("sha256:"):
                raise EvaluationError("offline input closure NAR identity mismatch")
        target = ".#packages.x86_64-linux.review-egress-systemd-units"
        drv = run(base + ["eval", "--raw", target + ".drvPath"], cwd=source, timeout=timeout).strip()
        enter("nix-build")
        build_log = run(base + ["build", "--no-link", "--print-out-paths", target], cwd=source, timeout=timeout)
        closure = build_log.strip()
        if "\n" in closure or not closure.startswith("/nix/store/"):
            raise EvaluationError("Nix build returned an unexpected closure set")
        unit_paths = [Path(closure) / "units" / unit for unit in UNITS]
        metadata_path = Path(closure) / "verifier-metadata.json"
        enter("unit-extract")
        if any(not path.is_file() for path in unit_paths) or not metadata_path.is_file():
            raise EvaluationError("generated unit set is incomplete")
        try:
            verifier_metadata = json.loads(metadata_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationError("generated verifier metadata is invalid") from exc
        if verifier_metadata != {
            "schema": "tgw-review-egress-systemd-units/v1",
            "system": bound["system"],
            "units": list(UNITS),
            "activation": False,
        }:
            raise EvaluationError("generated verifier metadata contract mismatch")
        output_entries = sorted(path.name for path in Path(closure).iterdir())
        unit_entries = sorted(path.name for path in (Path(closure) / "units").iterdir())
        if output_entries != ["units", "verifier-metadata.json"] or unit_entries != sorted(UNITS):
            raise EvaluationError("generated output contains an unexpected entry")
        enter("systemd-verify")
        verify_log = run([EXECUTABLES["systemd_analyze"], "verify", *map(str, unit_paths)], cwd=source, timeout=timeout)
        enter("closure-manifest")
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
        enter("version-probes")
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
            "input_closure_manifest": input_closure,
            "input_closure_manifest_sha256": bound["input_closure_manifest_sha256"],
            "input_closure_path_count": len(input_closure),
            "verifier_metadata": verifier_metadata,
            "verifier_metadata_sha256": _digest_file(metadata_path),
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
    except StepFailure as exc:
        _FAILURE_CONTEXT.update(
            {
                "subprocess_step": exc.step,
                "return_code": exc.return_code,
                "failure_stdout": exc.stdout,
                "failure_stderr": exc.stderr,
                "subprocess_diagnostic_code": exc.diagnostic_code,
            }
        )
        raise
    finally:
        _FAILURE_CONTEXT["cleanup_attempted"] = True
        try:
            cleanup_tree(scratch, ignore_errors=False)
            os.close(root_fd)
            if root_created:
                os.rmdir(scratch_root.name, dir_fd=parent_fd)
            _FAILURE_CONTEXT["cleanup_result"] = "removed" if root_created else "retained-existing"
        except Exception:
            _FAILURE_CONTEXT.update({"stage": "cleanup", "cleanup_result": "failed"})
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


def main(
    *,
    input_stream: io.BufferedReader | None = None,
    output_stream: io.BufferedWriter | None = None,
    execute_kwargs: Mapping[str, Any] | None = None,
) -> int:
    source = input_stream if input_stream is not None else sys.stdin.buffer
    sink = output_stream if output_stream is not None else sys.stdout.buffer
    try:
        executor = globals().get("_BOOTSTRAP_EXECUTOR", execute_packet)
        receipt = executor(source, **dict(execute_kwargs or {}))
        sink.write(_canonical(receipt))
        return 0
    except Exception as exc:
        exception_class = type(exc).__name__ if type(exc).__name__ in EXCEPTION_CLASSES else "EvaluationError"
        step_failure = exc if isinstance(exc, StepFailure) else None
        if _FAILURE_CONTEXT.get("stage") == "cleanup":
            code = "CLEANUP_FAILED"
        elif step_failure is not None:
            code = step_failure.diagnostic_code
        elif isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)):
            code = "BOUND_EXCEEDED"
        elif isinstance(exc, EvaluationError):
            code = "VALIDATION_REFUSED"
        else:
            code = "INTERNAL_ERROR"
        if not all(_FAILURE_CONTEXT.get(key) for key in ("effect_hash", "generation", "provider_sha256")):
            receipt = create_bootstrap_failure_receipt(
                request_hash=globals().get("_BOOTSTRAP_REQUEST_HASH", "sha256:" + "0" * 64),
                provider_sha256=globals().get("_BOOTSTRAP_PROVIDER_SHA256", "sha256:" + "0" * 64),
                diagnostic_code=code,
                exception_class=exception_class,
            )
        else:
            receipt = create_failure_receipt(
                context=_FAILURE_CONTEXT,
                stage=_FAILURE_CONTEXT.get("stage", "internal"),
                diagnostic_code=code,
                exception_class=exception_class,
                cleanup_result=_FAILURE_CONTEXT.get("cleanup_result", "unknown"),
                subprocess_step=step_failure.step if step_failure else _FAILURE_CONTEXT.get("subprocess_step", "none"),
                return_code=step_failure.return_code if step_failure else _FAILURE_CONTEXT.get("return_code"),
                stdout=step_failure.stdout if step_failure else _FAILURE_CONTEXT.get("failure_stdout", b""),
                stderr=step_failure.stderr if step_failure else _FAILURE_CONTEXT.get("failure_stderr", b""),
            )
        sink.write(_canonical(receipt))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
