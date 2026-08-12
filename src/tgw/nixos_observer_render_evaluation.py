"""Fixed SSH transport and controller for the standalone observer render helper.

The remote wire is the exact Phase-A2 helper packet.  SSH, host-key, Python,
helper, source archive, prerequisite, and network-namespace identities are
selected by this composition; none are accepted from the effect request.
"""

from __future__ import annotations

import base64
import fcntl
import ipaddress
import json
import os
import re
import selectors
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Protocol, Sequence

from tgw import nix_observer_render_helper as helper
from tgw.nix_observer_render_evaluation import validate_request, validate_result

EFFECT_KIND = "nixos-observer-render-evaluation"
SSH_EXECUTABLE = Path("/usr/bin/ssh")
SSH_SHA256 = "sha256:af3b04ec5653755032fc18ad02445e4e51170e75d8bac4265647d423caa9a83e"
REMOTE_HOST = "100.107.99.66"
REMOTE_USER = "codex"
REMOTE_PORT = 22
REMOTE_PYTHON = "/run/current-system/sw/bin/python3"
REMOTE_PYTHON_SHA256 = "sha256:98c4668fa5f84d0106ecc8ed8a76d7ff13d91a70f5cce1cacbf0f2fcb5da1184"
HOST_PREREQUISITE_RECEIPT_SHA256 = helper.AUTHORIZED_RENDER_RECEIPT_SHA256
HELPER_SHA256 = "sha256:bfbd824429a1449f50166b71417c010c48b60f3d579e6050fb082d8d41724eb9"
KNOWN_HOSTS_SHA256 = "sha256:2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf"
SOURCE_REF = "artifact:sha256:0ca98c4d32a2ffb99af355c768d48d7c1024efab76d78edf6644ed821aff68ad"
SOURCE_PATH = Path(
    "/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/"
    "0ca98c4d32a2ffb99af355c768d48d7c1024efab76d78edf6644ed821aff68ad.tar"
)
KNOWN_HOSTS_PATH = Path(
    "/opt/TGW/tgw-lib/actors/codex/artifacts/sha256/"
    "2efd6fc4243b15b6d0b16a8da723911614198620cabf31bc822cf12520715cdf.known_hosts"
)
TERMINAL_RECEIPT_ROOT = Path("/opt/TGW/tgw-lib/actors/codex/nixos-observer-render-terminals")

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}")

# This program runs before the helper bootstrap.  It verifies the already
# probed Python executable, creates a new Linux network namespace in-process,
# and proves that no non-loopback interface or route survived.  The A2 packet
# remains byte-for-byte unchanged on stdin.
_ISOLATION_PREAMBLE = r'''import hashlib,os,socket,sys
def refuse(code):
 sys.stderr.write("render transport isolation refused: "+code+"\n"); raise SystemExit(97)
if len(sys.argv)!=3: refuse("arguments")
expected_path,expected_sha=sys.argv[1:]
if sys.executable!=expected_path: refuse("python-path")
h=hashlib.sha256()
with open("/proc/self/exe","rb") as source:
 while block:=source.read(1048576): h.update(block)
if "sha256:"+h.hexdigest()!=expected_sha: refuse("python-identity")
before=os.readlink("/proc/self/ns/net")
try: os.unshare(os.CLONE_NEWNET)
except Exception: refuse("unshare")
after=os.readlink("/proc/self/ns/net")
if before==after: refuse("namespace-unchanged")
if {name for _,name in socket.if_nameindex()}-{"lo"}: refuse("interface")
for route_file in ("/proc/net/route","/proc/net/ipv6_route"):
 try: lines=open(route_file,encoding="ascii").read().splitlines()
 except Exception: refuse("route-read")
 for line in lines[1:] if route_file.endswith("route") and lines and lines[0].startswith("Iface") else lines:
  fields=line.split()
  if fields and fields[-1]!="lo": refuse("route")
'''
REMOTE_ISOLATED_BOOTSTRAP = _ISOLATION_PREAMBLE + "\nexec(compile(" + repr(helper.BOOTSTRAP) + ",'<tgw-render-packet-bootstrap>','exec'))\n"
REMOTE_BOOTSTRAP_WRAPPER = "import base64,sys;code=sys.argv.pop();exec(compile(base64.b64decode(code),'<tgw-render-isolation>','exec'))"
REMOTE_ISOLATED_BOOTSTRAP_B64 = base64.b64encode(REMOTE_ISOLATED_BOOTSTRAP.encode()).decode("ascii")


class RenderTransportError(ValueError):
    """The fixed transport, packet, terminal, or identity was invalid."""


class RemoteRenderFailure(RenderTransportError):
    def __init__(self, terminal: Mapping[str, Any], terminal_ref: Mapping[str, Any]):
        super().__init__(f"remote observer render terminated {terminal['outcome']}")
        self.terminal = dict(terminal)
        self.terminal_ref = dict(terminal_ref)


class TerminalPersistenceError(RenderTransportError):
    def __init__(self, terminal: Mapping[str, Any]):
        super().__init__("validated observer render terminal persistence is ambiguous")
        self.terminal = dict(terminal)


class ArtifactResolver(Protocol):
    def __call__(self, artifact_ref: str) -> Path: ...


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _read_held(fd: int, *, maximum: int, label: str) -> tuple[bytes, os.stat_result]:
    before = os.fstat(fd)
    identity = (before.st_dev, before.st_ino, before.st_size, before.st_mode, before.st_mtime_ns, before.st_ctime_ns)
    if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
        raise RenderTransportError(f"{label} is not a bounded regular artifact")
    os.lseek(fd, 0, os.SEEK_SET)
    content = bytearray()
    while block := os.read(fd, min(1024 * 1024, maximum + 1 - len(content))):
        content.extend(block)
        if len(content) > maximum:
            raise RenderTransportError(f"{label} exceeds its byte bound")
    after = os.fstat(fd)
    observed = (after.st_dev, after.st_ino, after.st_size, after.st_mode, after.st_mtime_ns, after.st_ctime_ns)
    if observed != identity or len(content) != before.st_size:
        raise RenderTransportError(f"{label} changed while held")
    os.lseek(fd, 0, os.SEEK_SET)
    return bytes(content), before


def serialize_remote_argv(argv: Sequence[str]) -> str:
    """Perform OpenSSH's unavoidable login-shell serialization exactly once."""
    if not argv or any(not isinstance(token, str) or not token or any(char in token for char in "\x00\r\n") for token in argv):
        raise RenderTransportError("remote argv contains an unsafe token")
    return shlex.join(argv)


def _sealed_memfd(content: bytes) -> int:
    fd = os.memfd_create("tgw-render-known-hosts", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.write(fd, content)
    os.lseek(fd, 0, os.SEEK_SET)
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
    if fcntl.fcntl(fd, fcntl.F_GET_SEALS) != seals:
        os.close(fd)
        raise RenderTransportError("known-hosts descriptor did not seal")
    return fd


@dataclass(frozen=True)
class NetworkNamespaceDescriptor:
    schema: str
    kind: str
    remote_python: str
    remote_python_sha256: str
    prerequisite_receipt_sha256: str
    network: bool

    def validate(self, request: Mapping[str, Any]) -> None:
        if (
            self.schema != "tgw-render-network-isolation/v1"
            or self.kind != "python-os-unshare-newnet"
            or self.remote_python != REMOTE_PYTHON
            or not _DIGEST.fullmatch(self.remote_python_sha256)
            or not _DIGEST.fullmatch(self.prerequisite_receipt_sha256)
            or self.prerequisite_receipt_sha256 != request["host_identity_receipt_sha256"]
            or self.network is not False
        ):
            raise RenderTransportError("render network-isolation descriptor is not exact")


PRODUCTION_NETWORK_NAMESPACE = NetworkNamespaceDescriptor(
    schema="tgw-render-network-isolation/v1",
    kind="python-os-unshare-newnet",
    remote_python=REMOTE_PYTHON,
    remote_python_sha256=REMOTE_PYTHON_SHA256,
    prerequisite_receipt_sha256=HOST_PREREQUISITE_RECEIPT_SHA256,
    network=False,
)


@dataclass(frozen=True)
class SshTransportIdentity:
    ssh_executable: Path = SSH_EXECUTABLE
    ssh_sha256: str = SSH_SHA256
    remote_host: str = REMOTE_HOST
    remote_user: str = REMOTE_USER
    remote_port: int = REMOTE_PORT
    helper_path: Path = Path(helper.__file__)
    helper_sha256: str = HELPER_SHA256
    known_hosts_sha256: str = KNOWN_HOSTS_SHA256
    namespace: NetworkNamespaceDescriptor = PRODUCTION_NETWORK_NAMESPACE
    require_sudo: bool = True


PRODUCTION_TRANSPORT_IDENTITY = SshTransportIdentity()


@dataclass(frozen=True)
class ExactRenderArtifactResolver:
    source_ref: str = SOURCE_REF
    source_path: Path = SOURCE_PATH

    def __call__(self, artifact_ref: str) -> Path:
        if artifact_ref != self.source_ref:
            raise RenderTransportError("render source artifact identity is not registered")
        return self.source_path


@dataclass(frozen=True)
class TransportExchange:
    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    binding: helper.WireBinding
    tool_descriptor: Mapping[str, Any]


class ImmutableTerminalReceiptStore:
    """Content-addressed terminal store using exclusive, fsynced writes."""

    def __init__(self, root: Path):
        self.root = root
        parent = root.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid not in {0, os.geteuid()} or parent.st_mode & 0o022:
            raise RenderTransportError("terminal store parent is unsafe")
        self._parent_fd = os.open(root.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        created = False
        try:
            try:
                os.mkdir(root.name, 0o700, dir_fd=self._parent_fd)
                created = True
            except FileExistsError:
                pass
            metadata = os.stat(root.name, dir_fd=self._parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
                raise RenderTransportError("terminal store identity is unsafe")
            self._directory_fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)
        except BaseException:
            os.close(self._parent_fd)
            raise
        self.readiness = {
            "schema": "tgw-render-terminal-store-readiness/v1",
            "path": str(root),
            "created": created,
            "owner_uid": metadata.st_uid,
            "mode": "0700",
            "ready": True,
        }
        self.readiness["receipt_sha256"] = _digest_bytes(canonical(self.readiness))

    def close(self) -> None:
        os.close(self._directory_fd)
        os.close(self._parent_fd)

    def persist(self, terminal: Mapping[str, Any]) -> dict[str, Any]:
        content = canonical(dict(terminal))
        digest = sha256(content).hexdigest()
        name = digest + ".json"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._directory_fd)
        except FileExistsError:
            metadata = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o400 or metadata.st_size != len(content):
                raise RenderTransportError("existing terminal receipt is unsafe")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
            with os.fdopen(fd, "rb") as existing:
                opened = os.fstat(existing.fileno())
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino) or existing.read() != content:
                    raise RenderTransportError("existing terminal receipt is contradictory")
        else:
            with os.fdopen(fd, "wb") as sink:
                sink.write(content)
                sink.flush()
                os.fsync(sink.fileno())
            os.fsync(self._directory_fd)
        return {
            "artifact_ref": "artifact:sha256:" + digest,
            "path": str(self.root / name),
            "sha256": "sha256:" + digest,
            "size": len(content),
        }


def _validate_effect(value: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "generation", "parameters"}:
        raise RenderTransportError("render effect is not the exact typed envelope")
    if value["kind"] != EFFECT_KIND or not isinstance(value["generation"], str) or not _IDENTITY.fullmatch(value["generation"]):
        raise RenderTransportError("render effect identity is invalid")
    if not isinstance(value["parameters"], Mapping):
        raise RenderTransportError("render effect parameters are not an object")
    request = validate_request(value["parameters"])
    return {"kind": EFFECT_KIND, "generation": value["generation"], "parameters": request}, request


def _tool_descriptor(request: Mapping[str, Any], authority: helper.ToolAuthority) -> dict[str, Any]:
    value = helper._expected_tool_descriptor(request["request_sha256"], authority)
    return helper._validate_tool_descriptor(value, request_sha256=request["request_sha256"], authority=authority)


def _build_packet_header(
    *,
    request: Mapping[str, Any],
    helper_source: bytes,
    archive_size: int,
    archive_sha256: str,
    tool_descriptor: Mapping[str, Any],
) -> tuple[bytes, helper.WireBinding]:
    request_raw = canonical(dict(request))
    tool_raw = canonical(dict(tool_descriptor))
    lengths = (len(helper_source), len(request_raw), len(tool_raw), archive_size)
    if not (
        1 <= lengths[0] <= helper.MAX_HELPER_BYTES
        and 1 <= lengths[1] <= helper.MAX_REQUEST_BYTES
        and 1 <= lengths[2] <= helper.MAX_TOOL_DESCRIPTOR_BYTES
        and 1 <= lengths[3] <= helper.MAX_ARCHIVE_BYTES
    ):
        raise RenderTransportError("render packet member exceeds its bound")
    prefix = helper.PREFIX.pack(
        helper.MAGIC,
        helper.VERSION,
        *lengths,
        bytes.fromhex(request["request_sha256"].removeprefix("sha256:")),
        sha256(helper_source).digest(),
        sha256(tool_raw).digest(),
        bytes.fromhex(archive_sha256.removeprefix("sha256:")),
    )
    return prefix + helper_source + request_raw + tool_raw, helper.parse_prefix(prefix)


class SshObserverRenderTransport:
    """Send one exact helper packet through one pinned OpenSSH invocation."""

    def __init__(
        self,
        resolve_artifact: ArtifactResolver,
        *,
        known_hosts: Path,
        identity: SshTransportIdentity = PRODUCTION_TRANSPORT_IDENTITY,
        tool_authority: helper.ToolAuthority = helper.PRODUCTION_GIT_AUTHORITY,
        invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
        _test_identity: bool = False,
    ):
        self.resolve_artifact = resolve_artifact
        self.known_hosts = known_hosts
        self.identity = identity
        self.tool_authority = tool_authority
        self.invoke = invoke
        self._test_identity = _test_identity

    def _validate_identity(self, request: Mapping[str, Any]) -> None:
        identity = self.identity
        try:
            ipaddress.ip_address(identity.remote_host)
        except ValueError as exc:
            raise RenderTransportError("render SSH host must be a literal IP") from exc
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", identity.remote_user) or not 1 <= identity.remote_port <= 65535:
            raise RenderTransportError("render SSH user or port is invalid")
        for digest in (identity.ssh_sha256, identity.helper_sha256, identity.known_hosts_sha256):
            if not _DIGEST.fullmatch(digest):
                raise RenderTransportError("render transport digest is invalid")
        identity.namespace.validate(request)
        if not self._test_identity and identity != PRODUCTION_TRANSPORT_IDENTITY:
            raise RenderTransportError("production render transport identity drifted")

    def __call__(self, request: Mapping[str, Any]) -> TransportExchange:
        request = validate_request(request)
        self._validate_identity(request)
        archive_path = self.resolve_artifact(request["artifact_ref"])
        ssh_fd = hosts_fd = helper_fd = archive_fd = sealed_hosts_fd = -1
        try:
            ssh_fd = os.open(self.identity.ssh_executable, os.O_RDONLY | os.O_NOFOLLOW)
            hosts_fd = os.open(self.known_hosts, os.O_RDONLY | os.O_NOFOLLOW)
            helper_fd = os.open(self.identity.helper_path, os.O_RDONLY | os.O_NOFOLLOW)
            archive_fd = os.open(archive_path, os.O_RDONLY | os.O_NOFOLLOW)
            ssh_raw, ssh_stat = _read_held(ssh_fd, maximum=16 * 1024 * 1024, label="SSH executable")
            hosts_raw, hosts_stat = _read_held(hosts_fd, maximum=4096, label="known-hosts")
            helper_raw, _ = _read_held(helper_fd, maximum=helper.MAX_HELPER_BYTES, label="render helper")
            archive_raw, archive_stat = _read_held(archive_fd, maximum=helper.MAX_ARCHIVE_BYTES, label="render archive")
            if (
                not ssh_stat.st_mode & 0o111
                or hosts_stat.st_uid not in {0, os.geteuid()}
                or hosts_stat.st_mode & 0o022
                or _digest_bytes(ssh_raw) != self.identity.ssh_sha256
                or _digest_bytes(hosts_raw) != self.identity.known_hosts_sha256
                or _digest_bytes(helper_raw) != self.identity.helper_sha256
                or _digest_bytes(archive_raw) != request["archive_sha256"]
            ):
                raise RenderTransportError("render transport artifact identity mismatch")
            try:
                known_host_line = hosts_raw.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise RenderTransportError("render known-hosts artifact is not ASCII") from exc
            host_token = self.identity.remote_host if self.identity.remote_port == 22 else f"[{self.identity.remote_host}]:{self.identity.remote_port}"
            if not re.fullmatch(re.escape(host_token) + r" (ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) ([A-Za-z0-9+/]+={0,2})", known_host_line):
                raise RenderTransportError("render known-hosts artifact does not contain one exact host key")
            tool_descriptor = _tool_descriptor(request, self.tool_authority)
            packet_header, binding = _build_packet_header(
                request=request,
                helper_source=helper_raw,
                archive_size=archive_stat.st_size,
                archive_sha256=request["archive_sha256"],
                tool_descriptor=tool_descriptor,
            )
            sealed_hosts_fd = _sealed_memfd(hosts_raw)
            remote_argv = [
                self.identity.namespace.remote_python,
                "-I",
                "-c",
                REMOTE_BOOTSTRAP_WRAPPER,
                self.identity.namespace.remote_python,
                self.identity.namespace.remote_python_sha256,
                REMOTE_ISOLATED_BOOTSTRAP_B64,
            ]
            if self.identity.require_sudo:
                remote_argv = ["sudo", "-n", "--", *remote_argv]
            remote_command = serialize_remote_argv(remote_argv)
            command = [
                f"/proc/self/fd/{ssh_fd}",
                "-F",
                "/dev/null",
                "-oBatchMode=yes",
                "-oClearAllForwardings=yes",
                "-oStrictHostKeyChecking=yes",
                f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts_fd}",
            ]
            if self.identity.remote_port != 22:
                command.extend(["-p", str(self.identity.remote_port)])
            command.extend(["--", f"{self.identity.remote_user}@{self.identity.remote_host}", remote_command])
            timeout = int(request["max_duration_seconds"]) + 30
            maximum = int(request["max_output_bytes"])
            try:
                if self.invoke is None:
                    completed = self._invoke_streaming(
                        command,
                        packet_header,
                        archive_fd,
                        timeout=timeout,
                        max_output=maximum,
                        pass_fds=(ssh_fd, sealed_hosts_fd),
                    )
                else:
                    completed = self.invoke(
                        command,
                        input=packet_header + archive_raw,
                        capture_output=True,
                        timeout=timeout,
                        check=False,
                        pass_fds=(ssh_fd, sealed_hosts_fd),
                    )
            except subprocess.TimeoutExpired as exc:
                raise RenderTransportError("remote render timed out") from exc
            if len(completed.stdout) > maximum:
                raise RenderTransportError("remote render output exceeded its bound")
            return TransportExchange(tuple(command), completed.returncode, completed.stdout, binding, tool_descriptor)
        finally:
            for fd in (sealed_hosts_fd, archive_fd, helper_fd, hosts_fd, ssh_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    @staticmethod
    def _invoke_streaming(
        command: list[str],
        packet_header: bytes,
        archive_fd: int,
        *,
        timeout: int,
        max_output: int,
        pass_fds: tuple[int, ...],
    ) -> subprocess.CompletedProcess[bytes]:
        before = os.fstat(archive_fd)
        with tempfile.TemporaryFile() as packet:
            packet.write(packet_header)
            os.lseek(archive_fd, 0, os.SEEK_SET)
            with os.fdopen(os.dup(archive_fd), "rb") as source:
                shutil.copyfileobj(source, packet, length=1024 * 1024)
            after = os.fstat(archive_fd)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise RenderTransportError("render archive changed during packet construction")
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
                    raise RenderTransportError("remote render timed out")
                for key, _ in selector.select(min(remaining, 0.25)):
                    block = key.fileobj.read1(min(65536, max_output + 1 - len(output)))
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(block)
                if len(output) > max_output:
                    process.kill()
                    process.wait()
                    raise RenderTransportError("remote render output exceeded its bound")
            try:
                returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired as exc:
                process.kill()
                process.wait()
                raise RenderTransportError("remote render timed out") from exc
        return subprocess.CompletedProcess(command, returncode, bytes(output), b"")


def validate_a2_response(
    value: Mapping[str, Any],
    *,
    binding: helper.WireBinding,
    request: Mapping[str, Any],
    tool_descriptor: Mapping[str, Any],
    authority: helper.A2Authority = helper.PRODUCTION_A2_AUTHORITY,
) -> dict[str, Any]:
    """Validate the exact outer A2 envelope and its exact inner provider receipt."""
    try:
        terminal = helper.validate_a2_terminal(
            value,
            binding=binding,
            request=request,
            tool_descriptor=tool_descriptor,
            authority=authority,
        )
        if terminal["schema"] == helper.SUCCESS_SCHEMA:
            # The helper validator already performs this check from the bound
            # provider source.  Repeating it here makes the controller boundary
            # explicit and prevents envelope-only acceptance by future callers.
            helper._validate_provider_receipt(terminal["provider_receipt"], request=request, authority=authority)
        return terminal
    except helper.RenderHelperError as exc:
        raise RenderTransportError("remote render A2 terminal validation failed") from exc


def validate_handler_success(value: Mapping[str, Any], *, request: Mapping[str, Any]) -> dict[str, Any]:
    """Revalidate injected handler output without accepting a legacy shape."""
    if not isinstance(value, Mapping) or value.get("schema") != helper.SUCCESS_SCHEMA:
        raise RenderTransportError("render handler did not return the exact A2 success schema")
    fake_binding = helper.WireBinding(
        request_bytes=1,
        helper_bytes=1,
        tool_descriptor_bytes=1,
        archive_bytes=1,
        request_sha256=request["request_sha256"],
        helper_sha256=value.get("helper_sha256", ""),
        tool_descriptor_sha256="sha256:" + "0" * 64,
        archive_sha256=request["archive_sha256"],
    )
    descriptor = _tool_descriptor(request, helper.PRODUCTION_GIT_AUTHORITY)
    terminal = validate_a2_response(
        value,
        binding=fake_binding,
        request=request,
        tool_descriptor=descriptor,
        authority=helper.PRODUCTION_A2_AUTHORITY,
    )
    validate_result(terminal["provider_receipt"], request=request)
    return terminal


class ObserverRenderController:
    def __init__(
        self,
        transport: Callable[[Mapping[str, Any]], TransportExchange],
        terminal_store: ImmutableTerminalReceiptStore,
        *,
        authority: helper.A2Authority = helper.PRODUCTION_A2_AUTHORITY,
    ):
        self.transport = transport
        self.terminal_store = terminal_store
        self.authority = authority

    def __call__(self, effect: Mapping[str, Any]) -> Mapping[str, Any]:
        _, request = _validate_effect(effect)
        exchange = self.transport(request)
        try:
            untrusted = json.loads(exchange.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RenderTransportError("remote render returned malformed JSON") from exc
        if not isinstance(untrusted, dict):
            raise RenderTransportError("remote render terminal is not an object")
        if untrusted.get("schema") == helper.PHASE1_FAILURE_SCHEMA:
            terminal = helper.validate_phase1_failure(untrusted, binding=exchange.binding)
            if exchange.returncode == 0:
                raise RenderTransportError("Phase1 failure returned success status")
        elif untrusted.get("schema") in {helper.SUCCESS_SCHEMA, helper.A2_FAILURE_SCHEMA}:
            terminal = validate_a2_response(
                untrusted,
                binding=exchange.binding,
                request=request,
                tool_descriptor=exchange.tool_descriptor,
                authority=self.authority,
            )
            if (terminal["schema"] == helper.SUCCESS_SCHEMA) != (exchange.returncode == 0):
                raise RenderTransportError("remote render exit status contradicts its terminal")
        else:
            raise RenderTransportError("remote render terminal schema is unknown")
        try:
            terminal_ref = self.terminal_store.persist(terminal)
        except (OSError, RenderTransportError) as exc:
            raise TerminalPersistenceError(terminal) from exc
        if terminal["schema"] != helper.SUCCESS_SCHEMA:
            raise RemoteRenderFailure(terminal, terminal_ref)
        return terminal


def compose_production_controller(
    *,
    terminal_root: Path = TERMINAL_RECEIPT_ROOT,
    invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> tuple[ObserverRenderController, dict[str, Any]]:
    store = ImmutableTerminalReceiptStore(terminal_root)
    transport = SshObserverRenderTransport(
        ExactRenderArtifactResolver(),
        known_hosts=KNOWN_HOSTS_PATH,
        invoke=invoke,
    )
    controller = ObserverRenderController(transport, store)
    return controller, {
        "schema": "tgw-nixos-observer-render-composition/v1",
        "effect_kind": EFFECT_KIND,
        "source_ref": SOURCE_REF,
        "source_path": str(SOURCE_PATH),
        "known_hosts_path": str(KNOWN_HOSTS_PATH),
        "remote": f"{REMOTE_USER}@{REMOTE_HOST}",
        "network_isolation": PRODUCTION_NETWORK_NAMESPACE.__dict__,
        "terminal_store": store.readiness,
        "activation": False,
        "profile_write": False,
        "deployment": False,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
    error_stream: Any | None = None,
    compose: Callable[..., tuple[ObserverRenderController, Mapping[str, Any]]] = compose_production_controller,
) -> int:
    """Read one exact typed effect from stdin; no path or argv is accepted."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    error = sys.stderr if error_stream is None else error_stream
    if arguments:
        error.write("tgw-nixos-observer-render-evaluation accepts no arguments\n")
        return 2
    source = sys.stdin.buffer if input_stream is None else input_stream
    sink = sys.stdout.buffer if output_stream is None else output_stream
    raw = source.read(helper.MAX_REQUEST_BYTES + 1)
    if not raw or len(raw) > helper.MAX_REQUEST_BYTES:
        error.write("render effect input is absent or oversized\n")
        return 2
    try:
        effect = json.loads(raw)
        _validate_effect(effect)
        controller, _ = compose()
        terminal = controller(effect)
    except RemoteRenderFailure as exc:
        sink.write(canonical(exc.terminal))
        return 1
    except Exception as exc:
        error.write(f"render evaluation refused: {exc}\n")
        return 2
    sink.write(canonical(terminal))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
