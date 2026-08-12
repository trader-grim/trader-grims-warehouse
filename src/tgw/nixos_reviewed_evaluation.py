"""Closed remote provider for immutable, non-activating NixOS evaluation."""

from __future__ import annotations

import io
import json
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
EXECUTABLES = {"git": "/run/current-system/sw/bin/git", "nix": "/run/current-system/sw/bin/nix", "systemd_analyze": "/run/current-system/sw/bin/systemd-analyze"}
UNITS = (
    "tgw-review-egress@.service",
    "tgw-review-egress-attest@.service",
    "tgw-review-egress-namespace@.service",
)


class EvaluationError(RuntimeError):
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
        provider_source = Path(__file__).read_bytes()
        command = [
            SSH_EXECUTABLE, "-F", "/dev/null", "-oBatchMode=yes", "-oClearAllForwardings=yes",
            "-oStrictHostKeyChecking=yes", "-oUserKnownHostsFile=" + str(self.known_hosts),
            "--", "tgw-prod", "sudo", "-n", "--", REMOTE_PYTHON, "-I", "-c", BOOTSTRAP,
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


def _safe_extract(archive: Path, target: Path, *, max_files: int, max_bytes: int) -> str:
    with tarfile.open(archive) as source:
        commit = source.pax_headers.get("comment", "")
        if not commit or len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise EvaluationError("source archive lacks an exact Git commit identity")
        members = source.getmembers()
        if len(members) > max_files or sum(member.size for member in members) > max_bytes:
            raise EvaluationError("source archive exceeds unpack bounds")
        for member in members:
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or ".git" in member_path.parts or member.isdev() or member.issym() or member.islnk():
                raise EvaluationError("source archive contains an unsafe member")
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
    parameters = json.loads(request_raw)
    # Reuse the authority registry's exact closed parser before any filesystem write.
    from tgw.effect_handlers import TypedEffectHandlerRegistry
    from tgw.plan_authority import TypedEffect

    def unavailable(_: Mapping[str, str]) -> Mapping[str, Any]:
        raise EvaluationError("unavailable")

    registry = TypedEffectHandlerRegistry(release_install=unavailable, release_rollback=unavailable, flake_push=unavailable, flake_switch_record=unavailable, dependency_resubmit=unavailable)
    effect = TypedEffect.parse({"kind": "nixos-reviewed-evaluation", "generation": parameters.pop("generation"), "parameters": parameters})
    _, bound, _, _ = registry.prepare(effect)
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
    if scratch_root.exists() and (scratch_root.is_symlink() or not scratch_root.is_dir()):
        raise EvaluationError("scratch root is not a real directory")
    scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    scratch_root.chmod(0o700)
    root_stat = scratch_root.stat()
    if root_stat.st_uid != scratch_uid or stat.S_IMODE(root_stat.st_mode) != 0o700:
        raise EvaluationError("scratch root is not root-owned mode 0700")
    scratch = Path(tempfile.mkdtemp(prefix="run-", dir=scratch_root))
    archive = scratch / "source.tar"
    source = scratch / "source"
    try:
        with archive.open("xb") as sink:
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
        source.mkdir()
        archive_commit = _safe_extract(archive, source, max_files=int(bound["max_files"]), max_bytes=int(bound["max_unpacked_bytes"]))
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
            "--option", "allow-import-from-derivation", "false", "--no-write-lock-file",
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
        receipt: dict[str, Any] = {
            "schema": bound["output_schema"], "outcome": "verified", "source_commit": bound["source_commit"],
            "source_tree": bound["source_tree"], "source_archive_sha256": bound["source_archive_sha256"],
            "flake_lock_sha256": bound["flake_lock_sha256"], "module_sha256": bound["module_sha256"],
            "provider_sha256": bound["provider_sha256"], "executables": EXECUTABLES,
            "ssh_sha256": bound["ssh_sha256"], "known_hosts_sha256": bound["known_hosts_sha256"],
            "executable_sha256": executable_digests,
            "scratch_id": bound["scratch_id"], "cleanup": "removed", "activate": False,
            "profile_write": False, "home_db_write": False, "system": bound["system"],
            "evaluation_target": bound["evaluation_target"], "evaluated_config_drv": drv,
            "evaluated_closure_sha256": "sha256:" + run(base + ["hash", "path", "--type", "sha256", "--base16", closure], cwd=source, timeout=timeout).strip(),
            "eval_log_sha256": "sha256:" + sha256(eval_log.encode()).hexdigest(),
            "build_log_sha256": "sha256:" + sha256(build_log.encode()).hexdigest(),
            "systemd_verify_output_sha256": "sha256:" + sha256(verify_log.encode()).hexdigest(),
            "systemd_verify_exit": 0, "systemd_version": int(run([EXECUTABLES["systemd_analyze"], "--version"], cwd=source, timeout=timeout).split()[1]),
            "nix_version": run([EXECUTABLES["nix"], "--version"], cwd=source, timeout=timeout).strip(),
            "unit_sha256": {unit: _digest_file(path) for unit, path in zip(UNITS, unit_paths, strict=True)},
            "evidence": [],
        }
        receipt["receipt_sha256"] = "sha256:" + sha256(_canonical({key: value for key, value in receipt.items() if key != "evidence"})).hexdigest()
        receipt["evidence"] = ["nixos-evaluation:" + receipt["receipt_sha256"]]
        return receipt
    finally:
        shutil.rmtree(scratch, ignore_errors=False)


def main() -> int:
    try:
        receipt = execute_packet(sys.stdin.buffer)
        sys.stdout.buffer.write(_canonical(receipt))
        return 0
    except Exception as exc:
        print(f"reviewed evaluation refused: {exc}", file=sys.stderr)
        return 1
