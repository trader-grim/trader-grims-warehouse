"""Closed remote provider for immutable, non-activating NixOS evaluation."""

from __future__ import annotations

import io
import json
import os
import re
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
    "s=sys.stdin.buffer.read(n); h=sys.stdin.buffer.read(64).decode(); "
    "hashlib.sha256(s).hexdigest()==h or sys.exit(91); "
    "exec(compile(s,'<tgw-reviewed-evaluator>','exec'),"
    "{'__name__':'__main__','_BOOTSTRAP_PROVIDER_SHA256':'sha256:'+h})"
)
EXECUTABLES = {
    "git": "/run/current-system/sw/bin/git", "nix": "/run/current-system/sw/bin/nix",
    "nix_store": "/run/current-system/sw/bin/nix-store", "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze",
}
REMOTE_HOST = "100.107.99.66"
REMOTE_USER = "codex"
UNITS = (
    "tgw-review-egress@.service",
    "tgw-review-egress-attest@.service",
    "tgw-review-egress-namespace@.service",
)


class EvaluationError(ValueError):
    pass


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


def _packet_header(parameters: Mapping[str, str], provider_source: bytes) -> bytes:
    request = _canonical(dict(parameters))
    if len(request) > 64 * 1024:
        raise EvaluationError("evaluation request is oversized")
    return struct.pack("!Q", len(provider_source)) + provider_source + sha256(provider_source).hexdigest().encode() + struct.pack("!Q", len(request)) + request


def _validate_remote_parameters(value: Any) -> dict[str, str]:
    """Standalone mirror of the closed effect boundary; imports no TGW package."""
    keys = {
        "target_host", "flake_repository_id", "artifact_ref", "source_commit", "source_tree",
        "source_archive_sha256", "flake_lock_sha256", "archive_root", "module_path", "module_sha256",
        "provider_sha256", "ssh_sha256", "known_hosts_sha256", "remote_python_sha256", "git_sha256",
        "nix_sha256", "nix_store_sha256", "systemd_analyze_sha256", "scratch_id", "system", "evaluation_target", "unit_set",
        "output_schema", "nix_network_policy", "minimum_systemd_version", "max_duration_seconds",
        "max_output_bytes", "max_archive_bytes", "max_unpacked_bytes", "max_files", "activate",
        "profile_write", "home_db_write", "operation_id", "generation",
    }
    if not isinstance(value, dict) or set(value) != keys or any(not isinstance(item, str) or not item for item in value.values()):
        raise EvaluationError("remote evaluation parameters are not the exact typed object")
    fixed = {
        "target_host": "tgw-prod", "flake_repository_id": "tgw-flake", "archive_root": "trader-grims-warehouse",
        "module_path": "nix/review-egress.nix", "system": "x86_64-linux", "evaluation_target": "review-egress-systemd-units",
        "unit_set": ",".join(UNITS), "output_schema": "tgw-nixos-reviewed-evaluation-receipt/v1",
        "nix_network_policy": "offline-no-substituters", "activate": "false", "profile_write": "false", "home_db_write": "false",
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
    bounds = tuple(int(value[key]) for key in ("minimum_systemd_version", "max_duration_seconds", "max_output_bytes", "max_archive_bytes", "max_unpacked_bytes", "max_files"))
    systemd, duration, output, archive, unpacked, files = bounds
    if systemd < 257 or not 1 <= duration <= 900 or not 1024 <= output <= 16 * 1024**2 or not 1024 <= archive <= 128 * 1024**2 or not archive <= unpacked <= 512 * 1024**2 or not 1 <= files <= 100_000:
        raise EvaluationError("remote resource bound is invalid")
    return dict(value)


class SshReviewedEvaluationProvider:
    """Resolve one content-addressed artifact and invoke one fixed remote helper."""

    def __init__(self, resolve_artifact: ArtifactResolver, *, known_hosts: Path, invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None):
        self.resolve_artifact = resolve_artifact
        self.known_hosts = known_hosts
        self.invoke = invoke

    def __call__(self, parameters: Mapping[str, str]) -> Mapping[str, Any]:
        archive = self.resolve_artifact(parameters["artifact_ref"])
        if not archive.is_file() or _digest_file(archive) != "sha256:" + parameters["source_archive_sha256"].removeprefix("sha256:"):
            raise EvaluationError("resolved source artifact digest mismatch")
        if archive.stat().st_size > int(parameters["max_archive_bytes"]):
            raise EvaluationError("resolved source artifact exceeds its bound")
        ssh_matches = _digest_file(Path(SSH_EXECUTABLE)) == "sha256:" + parameters["ssh_sha256"].removeprefix("sha256:")
        host_key_matches = _digest_file(self.known_hosts) == "sha256:" + parameters["known_hosts_sha256"].removeprefix("sha256:")
        if not ssh_matches or not host_key_matches:
            raise EvaluationError("SSH executable or host-key pin mismatch")
        admitted_hosts = {REMOTE_HOST, f"[{REMOTE_HOST}]:22"}
        host_tokens = {token for line in self.known_hosts.read_text().splitlines() if line and not line.startswith("#") for token in line.split()[0].split(",")}
        if not host_tokens or not host_tokens <= admitted_hosts:
            raise EvaluationError("known-hosts contains an unbound host identity")
        provider_source = Path(__file__).read_bytes()
        command = [
            SSH_EXECUTABLE, "-F", "/dev/null", "-oBatchMode=yes", "-oClearAllForwardings=yes",
            "-oStrictHostKeyChecking=yes", "-oUserKnownHostsFile=" + str(self.known_hosts),
            "--", f"{REMOTE_USER}@{REMOTE_HOST}", "sudo", "-n", "--", REMOTE_PYTHON, "-I", "-c", BOOTSTRAP,
        ]
        header = _packet_header(parameters, provider_source)
        if self.invoke is None:
            completed = self._invoke_streaming(command, header, archive, timeout=int(parameters["max_duration_seconds"]) + 30, max_output=int(parameters["max_output_bytes"]))
        else:
            completed = self.invoke(command, input=header + archive.read_bytes(), capture_output=True, timeout=int(parameters["max_duration_seconds"]) + 30, check=False)
        if completed.returncode:
            raise EvaluationError("remote reviewed evaluation failed")
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
    def _invoke_streaming(command: list[str], header: bytes, archive: Path, *, timeout: int, max_output: int) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryFile() as packet:
            packet.write(header)
            with archive.open("rb") as source:
                shutil.copyfileobj(source, packet, length=1024 * 1024)
            packet.seek(0)
            process = subprocess.Popen(command, stdin=packet, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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
        "PATH": "/run/current-system/sw/bin:/usr/bin:/bin", "HOME": str(cwd),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
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
            raise EvaluationError("fixed evaluation step timed out")
        for key, _ in selector.select(min(remaining, 0.25)):
            block = key.fileobj.read1(min(65536, max_output + 1 - len(output)))
            if not block:
                selector.unregister(key.fileobj)
            else:
                output.extend(block)
        if len(output) > max_output:
            process.kill()
            process.wait()
            raise EvaluationError("fixed evaluation step exceeded its output bound")
    if process.wait() != 0:
        raise EvaluationError(f"fixed evaluation step failed: {Path(argv[0]).name}")
    return output.decode()


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


def execute_packet(stream: io.BufferedReader, *, run: Callable[..., str] = _run, scratch_root: Path = Path("/var/tmp/tgw-reviewed-evaluation"), scratch_uid: int = 0) -> dict[str, Any]:
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
    timeout = int(bound["max_duration_seconds"])
    root_stat = scratch_root.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise EvaluationError("scratch root is not a real directory")
    if root_stat.st_uid != scratch_uid or stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise EvaluationError("scratch root is not root-owned mode 0700")
    scratch = Path(tempfile.mkdtemp(prefix="run-", dir=scratch_root))
    scratch_stat = scratch.lstat()
    if scratch_stat.st_uid != scratch_uid or stat.S_IMODE(scratch_stat.st_mode) != 0o700 or not stat.S_ISDIR(scratch_stat.st_mode):
        raise EvaluationError("atomic scratch directory identity mismatch")
    archive = scratch / "source.tar"
    extract_root = scratch / "source"
    source = extract_root / bound["archive_root"]
    receipt = None
    try:
        scratch_fd = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
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
        archive_commit = _safe_extract(archive, extract_root, expected_root=bound["archive_root"], max_files=int(bound["max_files"]), max_bytes=int(bound["max_unpacked_bytes"]))
        if archive_commit != bound["source_commit"]:
            raise EvaluationError("archive commit identity mismatch")
        git = [EXECUTABLES["git"], "-c", "core.hooksPath=/dev/null", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false"]
        run(git + ["init", "-q"], cwd=source, timeout=timeout)
        run(git + ["add", "-A"], cwd=source, timeout=timeout)
        if run(git + ["write-tree"], cwd=source, timeout=timeout).strip() != bound["source_tree"]:
            raise EvaluationError("unpacked source tree mismatch")
        lock_matches = _digest_file(source / "flake.lock") == "sha256:" + bound["flake_lock_sha256"].removeprefix("sha256:")
        module_matches = _digest_file(source / bound["module_path"]) == "sha256:" + bound["module_sha256"].removeprefix("sha256:")
        if not lock_matches or not module_matches:
            raise EvaluationError("lock or module digest mismatch")
        base = [
            EXECUTABLES["nix"], "--offline", "--option", "substituters", "",
            "--option", "allow-import-from-derivation", "false", "--option", "pure-eval", "true", "--no-write-lock-file",
        ]
        drv = run(base + ["eval", "--raw", ".#nixosConfigurations.tgw-prod.config.system.build.toplevel.drvPath"], cwd=source, timeout=timeout).strip()
        build_log = run(base + ["build", "--no-link", "--print-out-paths", ".#nixosConfigurations.tgw-prod.config.system.build.toplevel"], cwd=source, timeout=timeout)
        closure = build_log.strip()
        if "\n" in closure or not closure.startswith("/nix/store/"):
            raise EvaluationError("Nix build returned an unexpected closure set")
        unit_paths = [Path(closure) / "etc/systemd/system" / unit for unit in UNITS]
        if any(not path.is_file() for path in unit_paths):
            raise EvaluationError("generated unit set is incomplete")
        verify_log = run([EXECUTABLES["systemd_analyze"], "verify", *map(str, unit_paths)], cwd=source, timeout=timeout)
        eval_log = drv + "\n"
        requisites_raw = run([EXECUTABLES["nix_store"], "--query", "--requisites", closure], cwd=source, timeout=timeout)
        requisites = sorted(set(requisites_raw.splitlines()))
        if not requisites or closure not in requisites or any(not item.startswith("/nix/store/") for item in requisites):
            raise EvaluationError("Nix closure requisites are incomplete")
        closure_manifest = [{"path": item, "nar_sha256": "sha256:" + run(base + ["hash", "path", "--type", "sha256", "--base16", item], cwd=source, timeout=timeout).strip()} for item in requisites]
        receipt = {
            "schema": bound["output_schema"], "outcome": "verified", "source_commit": bound["source_commit"],
            "source_tree": bound["source_tree"], "source_archive_sha256": bound["source_archive_sha256"],
            "flake_lock_sha256": bound["flake_lock_sha256"], "module_sha256": bound["module_sha256"],
            "provider_sha256": bound["provider_sha256"], "executables": EXECUTABLES,
            "ssh_sha256": bound["ssh_sha256"], "known_hosts_sha256": bound["known_hosts_sha256"],
            "executable_sha256": executable_digests,
            "scratch_id": bound["scratch_id"], "activate": False,
            "profile_write": False, "home_db_write": False, "system": bound["system"],
            "evaluation_target": bound["evaluation_target"], "evaluated_config_drv": drv,
            "closure_manifest_sha256": "sha256:" + sha256(_canonical(closure_manifest)).hexdigest(),
            "closure_path_count": len(closure_manifest),
            "eval_log_sha256": "sha256:" + sha256(eval_log.encode()).hexdigest(),
            "build_log_sha256": "sha256:" + sha256(build_log.encode()).hexdigest(),
            "systemd_verify_output_sha256": "sha256:" + sha256(verify_log.encode()).hexdigest(),
            "systemd_verify_exit": 0, "systemd_version": int(run([EXECUTABLES["systemd_analyze"], "--version"], cwd=source, timeout=timeout).split()[1]),
            "nix_version": run([EXECUTABLES["nix"], "--version"], cwd=source, timeout=timeout).strip(),
            "unit_sha256": {unit: _digest_file(path) for unit, path in zip(UNITS, unit_paths, strict=True)},
            "evidence": [],
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=False)
    if scratch.exists() or receipt is None:
        raise EvaluationError("scratch cleanup was not verified")
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
        print(f"reviewed evaluation refused: {exc}", file=sys.stderr)
        return 1
