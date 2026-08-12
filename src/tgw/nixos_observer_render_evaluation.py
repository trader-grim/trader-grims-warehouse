"""Hash-bound SSH transport for the audited, non-deploying observer render.

Production has no compiled-in candidate or request.  A source-freeze step may
install one root-owned composition descriptor only after the exact source has
passed review.  The descriptor binds every local artifact, the audited A2 base,
the fixed remote wrapper and its installation receipt, and the one request.  A
missing or stale descriptor is a HOLD and cannot reach SSH.
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
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, NoReturn, Protocol, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from tgw import nix_observer_render_evaluation as provider
from tgw import nix_observer_render_helper as helper
from tgw import nix_observer_render_remote as remote_bootstrap
from tgw.nix_observer_render_evaluation import validate_request, validate_result

EFFECT_KIND = "nixos-observer-render-evaluation"
AUDITED_A2_COMMIT = "45ccc1f5643c6c81bba836dcdbd3cb46392c4679"
HELPER_SHA256 = "sha256:bfbd824429a1449f50166b71417c010c48b60f3d579e6050fb082d8d41724eb9"
REMOTE_WRAPPER_PATH = "/run/current-system/sw/bin/tgw-nix-observer-render-wrapper"
REMOTE_SUDO_PATH = "/run/wrappers/bin/sudo"
PRODUCTION_COMPOSITION_PATH = Path("/etc/tgw/nix-observer-render-composition.json")
TERMINAL_RECEIPT_ROOT = Path("/opt/TGW/tgw-lib/actors/codex/nixos-observer-render-terminals")
ATTEMPT_RECEIPT_ROOT = Path("/opt/TGW/tgw-lib/actors/codex/nixos-observer-render-attempts")

_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_SHA1 = re.compile(r"[0-9a-f]{40}")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}")
_NETNS = re.compile(r"net:\[[1-9][0-9]*\]")
_COMPOSITION_SCHEMA = "tgw-nixos-observer-render-composition/v2"
_ATTEMPT_SCHEMA = "tgw-nixos-observer-render-attempt/v1"
_WRAPPER_SCHEMA = "tgw-nixos-observer-render-wrapper-envelope/v1"
_HANDLER_SCHEMA = "tgw-nixos-observer-render-handler-result/v1"


class RenderTransportError(ValueError):
    """The fixed transport, packet, terminal, or identity was invalid."""


class CompositionHold(RenderTransportError):
    """The post-audit immutable production composition is absent or stale."""


class RemoteRenderFailure(RenderTransportError):
    def __init__(self, terminal: Mapping[str, Any], terminal_ref: Mapping[str, Any], attempt_ref: Mapping[str, Any]):
        super().__init__(f"remote observer render terminated {terminal['outcome']}")
        self.terminal = dict(terminal)
        self.terminal_ref = dict(terminal_ref)
        self.attempt_ref = dict(attempt_ref)


class RemoteAttemptAmbiguous(RenderTransportError):
    def __init__(self, reason: str, attempt_receipt: Mapping[str, Any], attempt_ref: Mapping[str, Any]):
        super().__init__(f"remote observer render is AMBIGUOUS: {reason}")
        self.attempt_receipt = dict(attempt_receipt)
        self.attempt_ref = dict(attempt_ref)
        self.reconciliation = dict(attempt_receipt["reconciliation"])


class TerminalPersistenceError(RemoteAttemptAmbiguous):
    def __init__(self, terminal: Mapping[str, Any], attempt_receipt: Mapping[str, Any], attempt_ref: Mapping[str, Any]):
        super().__init__("validated terminal could not be persisted", attempt_receipt, attempt_ref)
        self.terminal = dict(terminal)


class ReceiptStore(Protocol):
    def persist(self, value: Mapping[str, Any]) -> Mapping[str, Any]: ...


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + sha256(value).hexdigest()


def _self_hashed(value: Mapping[str, Any], *, field: str = "receipt_sha256") -> bool:
    unsigned = dict(value)
    claimed = unsigned.pop(field, None)
    return isinstance(claimed, str) and claimed == _digest_bytes(canonical(unsigned))


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


def _open_file_identity(
    value: Mapping[str, Any], *, label: str, maximum: int, secret: bool = False, allow_group_write: bool = False
) -> tuple[int, bytes]:
    fields = {"path", "sha256", "size", "owner_uid", "mode"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise CompositionHold(f"{label} identity is not closed")
    path = value.get("path")
    if not isinstance(path, str) or not path.startswith("/") or not _DIGEST.fullmatch(str(value.get("sha256"))):
        raise CompositionHold(f"{label} identity is invalid")
    if type(value.get("size")) is not int or not 1 <= value["size"] <= maximum:
        raise CompositionHold(f"{label} size is invalid")
    if type(value.get("owner_uid")) is not int or type(value.get("mode")) is not int:
        raise CompositionHold(f"{label} ownership is invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        raw, metadata = _read_held(fd, maximum=maximum, label=label)
    except (OSError, RenderTransportError) as exc:
        raise CompositionHold(f"{label} is not installed") from exc
    if (
        metadata.st_size != value["size"]
        or metadata.st_uid != value["owner_uid"]
        or stat.S_IMODE(metadata.st_mode) != value["mode"]
        or _digest_bytes(raw) != value["sha256"]
        or metadata.st_mode & (0o002 if allow_group_write else 0o022)
        or secret
        and stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
    ):
        os.close(fd)
        raise CompositionHold(f"{label} installed identity mismatch")
    return fd, raw


def _file_identity(
    value: Mapping[str, Any], *, label: str, maximum: int, secret: bool = False, allow_group_write: bool = False
) -> bytes:
    fd, raw = _open_file_identity(value, label=label, maximum=maximum, secret=secret, allow_group_write=allow_group_write)
    os.close(fd)
    return raw


def _exact_file(path: Path, value: Mapping[str, Any], *, label: str, maximum: int, allow_group_write: bool = False) -> bytes:
    try:
        if path.resolve(strict=True) != Path(str(value.get("path"))).resolve(strict=True):
            raise CompositionHold(f"{label} path does not name the running component")
    except OSError as exc:
        raise CompositionHold(f"{label} path is unavailable") from exc
    return _file_identity(value, label=label, maximum=maximum, allow_group_write=allow_group_write)


@dataclass(frozen=True)
class RenderComposition:
    value: Mapping[str, Any]
    request: Mapping[str, Any]
    test_mode: bool = False

    @property
    def receipt_sha256(self) -> str:
        return str(self.value["receipt_sha256"])

    @property
    def source(self) -> Mapping[str, Any]:
        return self.value["source"]

    @property
    def components(self) -> Mapping[str, Any]:
        return self.value["components"]

    @property
    def ssh(self) -> Mapping[str, Any]:
        return self.value["ssh"]

    @property
    def wrapper(self) -> Mapping[str, Any]:
        return self.value["wrapper"]

    def validate_request(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        exact = validate_request(candidate)
        if canonical(exact) != canonical(self.request) or exact["request_sha256"] != self.value["request_sha256"]:
            raise CompositionHold("effect request does not match the frozen composition")
        source = self.source
        if (
            exact["source_commit"] != source["commit"]
            or exact["source_tree"] != source["tree"]
            or exact["artifact_ref"] != source["artifact_ref"]
            or exact["archive_sha256"] != source["archive"]["sha256"]
            or exact["provider_sha256"] != self.components["provider"]["sha256"]
            or self.components["helper"]["sha256"] != HELPER_SHA256
        ):
            raise CompositionHold("request/source/A2 component binding is stale")
        return exact

    def revalidate_runtime(self) -> None:
        _exact_file(
            Path(__file__), self.components["transport"], label="running transport", maximum=2 * 1024 * 1024, allow_group_write=self.test_mode
        )
        _exact_file(Path(helper.__file__), self.components["helper"], label="audited helper", maximum=helper.MAX_HELPER_BYTES, allow_group_write=self.test_mode)
        _exact_file(Path(provider.__file__), self.components["provider"], label="A2 provider", maximum=2 * 1024 * 1024, allow_group_write=self.test_mode)
        _exact_file(
            Path(remote_bootstrap.__file__),
            self.components["remote_bootstrap"],
            label="remote bootstrap",
            maximum=256 * 1024,
            allow_group_write=self.test_mode,
        )
        self.validate_request(self.request)


def _validate_source_audit(value: Mapping[str, Any], composition: Mapping[str, Any]) -> None:
    fields = {
        "schema",
        "status",
        "audited_a2_commit",
        "source",
        "helper_sha256",
        "provider_sha256",
        "transport_sha256",
        "remote_bootstrap_sha256",
        "observed_at",
        "receipt_sha256",
    }
    source = composition["source"]
    expected_source = {"commit": source["commit"], "tree": source["tree"]}
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or not _self_hashed(value)
        or value["schema"] != "tgw-nixos-observer-render-source-audit/v1"
        or value["status"] != "PASS"
        or value["audited_a2_commit"] != AUDITED_A2_COMMIT
        or value["source"] != expected_source
        or value["helper_sha256"] != composition["components"]["helper"]["sha256"]
        or value["provider_sha256"] != composition["components"]["provider"]["sha256"]
        or value["transport_sha256"] != composition["components"]["transport"]["sha256"]
        or value["remote_bootstrap_sha256"] != composition["components"]["remote_bootstrap"]["sha256"]
        or not isinstance(value["observed_at"], str)
    ):
        raise CompositionHold("fresh exact-source PASS receipt is absent")


def _timestamp(value: Any, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CompositionHold(f"{label} timestamp is invalid")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise CompositionHold(f"{label} timestamp is invalid") from exc


def _validate_freeze_receipt(value: Mapping[str, Any], audit: Mapping[str, Any], composition: Mapping[str, Any]) -> None:
    fields = {
        "schema",
        "status",
        "source_pass_receipt_sha256",
        "source",
        "request_sha256",
        "sequence",
        "observed_at",
        "receipt_sha256",
    }
    source = composition["source"]
    expected_source = {
        "commit": source["commit"],
        "tree": source["tree"],
        "artifact_ref": source["artifact_ref"],
        "archive_sha256": source["archive"]["sha256"],
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or not _self_hashed(value)
        or value["schema"] != "tgw-nixos-observer-render-freeze-receipt/v1"
        or value["status"] != "FROZEN_AFTER_SOURCE_PASS"
        or value["source_pass_receipt_sha256"] != source["audit_receipt"]["sha256"]
        or value["source"] != expected_source
        or value["request_sha256"] != composition["request_sha256"]
        or value["sequence"] != ["SOURCE_PASS", "SOURCE_FREEZE", "REQUEST_FREEZE"]
        or _timestamp(value["observed_at"], label="source freeze") <= _timestamp(audit["observed_at"], label="source PASS")
    ):
        raise CompositionHold("source/request freeze was not generated after source PASS")


def _validate_wrapper_prerequisite(value: Mapping[str, Any], composition: Mapping[str, Any]) -> None:
    fields = {
        "schema",
        "status",
        "wrapper",
        "remote_bootstrap",
        "helper",
        "python",
        "ip",
        "sudo",
        "sudoers",
        "attestation_public_key_sha256",
        "remote_uid",
        "remote_gid",
        "receipt_sha256",
    }
    wrapper = composition["wrapper"]
    components = composition["components"]
    expected_wrapper = {
        "path": REMOTE_WRAPPER_PATH,
        "sha256": wrapper["sha256"],
        "owner_uid": 0,
        "mode": 0o555,
        "no_argv": True,
    }
    expected_sudoers = {
        "user": composition["ssh"]["remote_user"],
        "runas": "root",
        "command": REMOTE_WRAPPER_PATH,
        "arguments": [],
        "nopasswd": True,
        "sha256": wrapper["sudoers_sha256"],
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or not _self_hashed(value)
        or value["schema"] != "tgw-nixos-observer-render-wrapper-prerequisite/v1"
        or value["status"] != "INSTALLED"
        or value["wrapper"] != expected_wrapper
        or value["remote_bootstrap"]
        != {"path": components["remote_bootstrap_remote_path"], "sha256": components["remote_bootstrap"]["sha256"]}
        or value["helper"] != {"path": components["helper_remote_path"], "sha256": HELPER_SHA256}
        or value["python"] != {"path": wrapper["remote_python_path"], "sha256": wrapper["remote_python_sha256"]}
        or value["ip"] != {"path": wrapper["remote_ip_path"], "sha256": wrapper["remote_ip_sha256"]}
        or value["sudo"] != {"path": REMOTE_SUDO_PATH, "sha256": wrapper["remote_sudo_sha256"]}
        or value["sudoers"] != expected_sudoers
        or value["attestation_public_key_sha256"] != wrapper["attestation_public_key"]["sha256"]
        or value["remote_uid"] != wrapper["remote_uid"]
        or value["remote_gid"] != wrapper["remote_gid"]
    ):
        raise CompositionHold("fixed wrapper/sudoers prerequisite is not installed")


def load_composition(
    path: Path = PRODUCTION_COMPOSITION_PATH, *, _trusted_owner_uid: int = 0, _allow_test_source: bool = False
) -> RenderComposition:
    if not _allow_test_source:
        current = path.parent
        while True:
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise CompositionHold("render composition parent is unavailable") from exc
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_mode & 0o022:
                raise CompositionHold("render composition parent is mutable")
            if current == current.parent:
                break
            current = current.parent
    try:
        descriptor_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        raw, metadata = _read_held(descriptor_fd, maximum=64 * 1024, label="render composition")
    except (OSError, RenderTransportError) as exc:
        raise CompositionHold("render composition is not installed") from exc
    finally:
        if "descriptor_fd" in locals():
            os.close(descriptor_fd)
    if metadata.st_uid != _trusted_owner_uid or stat.S_IMODE(metadata.st_mode) != 0o400:
        raise CompositionHold("render composition ownership is not immutable")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositionHold("render composition is malformed") from exc
    fields = {
        "schema",
        "audited_a2_commit",
        "request_sha256",
        "request",
        "source",
        "components",
        "ssh",
        "wrapper",
        "receipt_roots",
        "receipt_sha256",
    }
    if not isinstance(value, dict) or canonical(value) != raw or set(value) != fields or not _self_hashed(value):
        raise CompositionHold("render composition schema or self-hash is invalid")
    if value["schema"] != _COMPOSITION_SCHEMA or value["audited_a2_commit"] != AUDITED_A2_COMMIT:
        raise CompositionHold("render composition does not bind the audited A2 base")
    if not _DIGEST.fullmatch(str(value["request_sha256"])):
        raise CompositionHold("render composition request binding is invalid")
    if not isinstance(value["request"], Mapping) or set(value["request"]) != {"artifact", "request_sha256"}:
        raise CompositionHold("render composition request artifact is not exact")
    request_raw = _file_identity(value["request"]["artifact"], label="frozen request", maximum=helper.MAX_REQUEST_BYTES)
    try:
        request = validate_request(json.loads(request_raw))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CompositionHold("frozen request artifact is invalid") from exc
    if canonical(request) != request_raw or request["request_sha256"] != value["request_sha256"] or value["request"]["request_sha256"] != value["request_sha256"]:
        raise CompositionHold("frozen request artifact binding mismatch")
    source = value["source"]
    if (
        not isinstance(source, Mapping)
        or set(source) != {"commit", "tree", "artifact_ref", "archive", "audit_receipt", "freeze_receipt"}
        or not _SHA1.fullmatch(str(source["commit"]))
        or not _SHA1.fullmatch(str(source["tree"]))
        or source["artifact_ref"] != "artifact:" + source["archive"].get("sha256", "")
    ):
        raise CompositionHold("render source composition is invalid")
    _file_identity(source["archive"], label="source archive", maximum=helper.MAX_ARCHIVE_BYTES)
    components = value["components"]
    component_fields = {"transport", "helper", "provider", "remote_bootstrap", "remote_bootstrap_remote_path", "helper_remote_path"}
    if not isinstance(components, Mapping) or set(components) != component_fields:
        raise CompositionHold("render component composition is not closed")
    composition = RenderComposition(value, request, _allow_test_source)
    composition.revalidate_runtime()
    audit_raw = _file_identity(source["audit_receipt"], label="source audit receipt", maximum=64 * 1024)
    try:
        audit = json.loads(audit_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositionHold("source audit receipt is malformed") from exc
    if canonical(audit) != audit_raw:
        raise CompositionHold("source audit receipt is not canonical")
    _validate_source_audit(audit, value)
    freeze_raw = _file_identity(source["freeze_receipt"], label="source/request freeze receipt", maximum=64 * 1024)
    try:
        freeze = json.loads(freeze_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositionHold("source/request freeze receipt is malformed") from exc
    if canonical(freeze) != freeze_raw:
        raise CompositionHold("source/request freeze receipt is not canonical")
    _validate_freeze_receipt(freeze, audit, value)
    ssh = value["ssh"]
    ssh_fields = {"executable", "remote_host", "remote_user", "remote_port", "known_hosts", "identity_file"}
    if not isinstance(ssh, Mapping) or set(ssh) != ssh_fields:
        raise CompositionHold("SSH composition is not closed")
    try:
        ipaddress.ip_address(ssh["remote_host"])
    except (TypeError, ValueError) as exc:
        raise CompositionHold("SSH host is not a literal IP") from exc
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", str(ssh["remote_user"])) or type(ssh["remote_port"]) is not int or not 1 <= ssh["remote_port"] <= 65535:
        raise CompositionHold("SSH user or port is invalid")
    _file_identity(ssh["executable"], label="SSH executable", maximum=16 * 1024 * 1024)
    _file_identity(ssh["known_hosts"], label="known-hosts", maximum=4096)
    _file_identity(ssh["identity_file"], label="dedicated SSH identity", maximum=64 * 1024, secret=True)
    wrapper = value["wrapper"]
    wrapper_fields = {
        "path",
        "sha256",
        "prerequisite_receipt",
        "sudoers_sha256",
        "attestation_public_key",
        "remote_uid",
        "remote_gid",
        "remote_python_path",
        "remote_python_sha256",
        "remote_ip_path",
        "remote_ip_sha256",
        "remote_sudo_sha256",
    }
    if (
        not isinstance(wrapper, Mapping)
        or set(wrapper) != wrapper_fields
        or wrapper["path"] != REMOTE_WRAPPER_PATH
        or not _DIGEST.fullmatch(str(wrapper["sha256"]))
        or not _DIGEST.fullmatch(str(wrapper["sudoers_sha256"]))
        or type(wrapper["remote_uid"]) is not int
        or type(wrapper["remote_gid"]) is not int
        or wrapper["remote_uid"] <= 0
        or wrapper["remote_gid"] <= 0
        or not isinstance(wrapper["remote_python_path"], str)
        or not wrapper["remote_python_path"].startswith("/")
        or not _DIGEST.fullmatch(str(wrapper["remote_python_sha256"]))
        or not isinstance(wrapper["remote_ip_path"], str)
        or not wrapper["remote_ip_path"].startswith("/")
        or not _DIGEST.fullmatch(str(wrapper["remote_ip_sha256"]))
        or not _DIGEST.fullmatch(str(wrapper["remote_sudo_sha256"]))
    ):
        raise CompositionHold("fixed wrapper identity is invalid")
    public_key = _file_identity(wrapper["attestation_public_key"], label="wrapper attestation public key", maximum=128)
    if len(public_key) != 32:
        raise CompositionHold("wrapper attestation public key is not raw Ed25519")
    prerequisite_raw = _file_identity(wrapper["prerequisite_receipt"], label="wrapper prerequisite receipt", maximum=64 * 1024)
    try:
        prerequisite = json.loads(prerequisite_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositionHold("wrapper prerequisite receipt is malformed") from exc
    if canonical(prerequisite) != prerequisite_raw:
        raise CompositionHold("wrapper prerequisite receipt is not canonical")
    _validate_wrapper_prerequisite(prerequisite, value)
    roots = value["receipt_roots"]
    if not isinstance(roots, Mapping) or set(roots) != {"attempts", "terminals"} or any(not isinstance(item, str) or not item.startswith("/") for item in roots.values()):
        raise CompositionHold("local receipt roots are not exact")
    return composition


def serialize_remote_argv(argv: Sequence[str]) -> str:
    """Perform OpenSSH's unavoidable login-shell serialization exactly once."""
    if not argv or any(not isinstance(token, str) or not token or any(char in token for char in "\x00\r\n") for token in argv):
        raise RenderTransportError("remote argv contains an unsafe token")
    return shlex.join(argv)


def _sealed_memfd(content: bytes, name: str) -> int:
    fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    os.write(fd, content)
    os.fchmod(fd, 0o400)
    os.lseek(fd, 0, os.SEEK_SET)
    seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    fcntl.fcntl(fd, fcntl.F_ADD_SEALS, seals)
    if fcntl.fcntl(fd, fcntl.F_GET_SEALS) != seals:
        os.close(fd)
        raise RenderTransportError(f"{name} descriptor did not seal")
    return fd


class ImmutableTerminalReceiptStore:
    """Content-addressed receipt store using exclusive, fsynced writes."""

    def __init__(self, root: Path):
        self.root = root
        try:
            parent = root.parent.lstat()
        except OSError as exc:
            raise CompositionHold("receipt-store parent is unavailable") from exc
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid not in {0, os.geteuid()} or parent.st_mode & 0o022:
            raise RenderTransportError("receipt-store parent is unsafe")
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
                raise RenderTransportError("receipt-store identity is unsafe")
            self._directory_fd = os.open(root.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._parent_fd)
        except BaseException:
            os.close(self._parent_fd)
            raise
        self.readiness = {
            "schema": "tgw-render-receipt-store-readiness/v1",
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

    def persist(self, value: Mapping[str, Any]) -> dict[str, Any]:
        content = canonical(dict(value))
        digest = sha256(content).hexdigest()
        name = digest + ".json"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._directory_fd)
        except FileExistsError:
            metadata = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o400 or metadata.st_size != len(content):
                raise RenderTransportError("existing immutable receipt is unsafe")
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
            with os.fdopen(fd, "rb") as existing:
                opened = os.fstat(existing.fileno())
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino) or existing.read() != content:
                    raise RenderTransportError("existing immutable receipt is contradictory")
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


def _attempt_receipt(composition: RenderComposition, request: Mapping[str, Any], generation: str) -> dict[str, Any]:
    value = {
        "schema": _ATTEMPT_SCHEMA,
        "outcome": "LAUNCHED_UNRECONCILED",
        "generation": generation,
        "request_sha256": request["request_sha256"],
        "composition_sha256": composition.receipt_sha256,
        "source_commit": request["source_commit"],
        "source_tree": request["source_tree"],
        "archive_sha256": request["archive_sha256"],
        "helper_sha256": HELPER_SHA256,
        "provider_sha256": request["provider_sha256"],
        "transport_sha256": composition.components["transport"]["sha256"],
        "remote_bootstrap_sha256": composition.components["remote_bootstrap"]["sha256"],
        "remote_python_sha256": composition.wrapper["remote_python_sha256"],
        "remote_ip_sha256": composition.wrapper["remote_ip_sha256"],
        "remote_sudo_sha256": composition.wrapper["remote_sudo_sha256"],
        "ssh_sha256": composition.ssh["executable"]["sha256"],
        "known_hosts_sha256": composition.ssh["known_hosts"]["sha256"],
        "ssh_identity_sha256": composition.ssh["identity_file"]["sha256"],
        "remote": f"{composition.ssh['remote_user']}@{composition.ssh['remote_host']}:{composition.ssh['remote_port']}",
        "wrapper_sha256": composition.wrapper["sha256"],
        "wrapper_prerequisite_receipt_sha256": composition.wrapper["prerequisite_receipt"]["sha256"],
        "reconciliation": {
            "schema": "tgw-nixos-observer-render-reconciliation-binding/v1",
            "required": True,
            "request_sha256": request["request_sha256"],
            "composition_sha256": composition.receipt_sha256,
            "accept_only": [helper.PHASE1_FAILURE_SCHEMA, helper.SUCCESS_SCHEMA, helper.A2_FAILURE_SCHEMA],
        },
    }
    value["receipt_sha256"] = _digest_bytes(canonical(value))
    return value


@dataclass(frozen=True)
class TransportExchange:
    command: tuple[str, ...]
    returncode: int
    stdout: bytes
    binding: helper.WireBinding
    tool_descriptor: Mapping[str, Any]
    attempt_receipt: Mapping[str, Any]
    attempt_ref: Mapping[str, Any]


class SshObserverRenderTransport:
    """Send one exact packet through pinned OpenSSH to the fixed no-argv wrapper."""

    def __init__(
        self,
        composition: RenderComposition,
        attempt_store: ReceiptStore,
        *,
        tool_authority: helper.ToolAuthority = helper.PRODUCTION_GIT_AUTHORITY,
        invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
        _use_sudo: bool = True,
    ):
        self.composition = composition
        self.attempt_store = attempt_store
        self.tool_authority = tool_authority
        self.invoke = invoke
        self._use_sudo = _use_sudo

    def __call__(self, request: Mapping[str, Any], *, generation: str) -> TransportExchange:
        request = self.composition.validate_request(request)
        self.composition.revalidate_runtime()
        ssh = self.composition.ssh
        ssh_fd = hosts_fd = identity_fd = helper_fd = archive_fd = sealed_hosts_fd = sealed_identity_fd = -1
        attempt_receipt: dict[str, Any] | None = None
        attempt_ref: Mapping[str, Any] | None = None
        try:
            ssh_fd, ssh_raw = _open_file_identity(ssh["executable"], label="SSH executable", maximum=16 * 1024 * 1024)
            hosts_fd, hosts_raw = _open_file_identity(ssh["known_hosts"], label="known-hosts", maximum=4096)
            identity_fd, identity_raw = _open_file_identity(ssh["identity_file"], label="dedicated SSH identity", maximum=64 * 1024, secret=True)
            helper_fd, helper_raw = _open_file_identity(
                self.composition.components["helper"],
                label="audited helper",
                maximum=helper.MAX_HELPER_BYTES,
                allow_group_write=self.composition.test_mode,
            )
            archive_fd, archive_raw = _open_file_identity(self.composition.source["archive"], label="source archive", maximum=helper.MAX_ARCHIVE_BYTES)
            if _digest_bytes(helper_raw) != HELPER_SHA256 or _digest_bytes(archive_raw) != request["archive_sha256"]:
                raise CompositionHold("helper or source archive changed after composition")
            try:
                known_host_line = hosts_raw.decode("ascii").strip()
            except UnicodeDecodeError as exc:
                raise CompositionHold("known-hosts artifact is not ASCII") from exc
            host_token = ssh["remote_host"] if ssh["remote_port"] == 22 else f"[{ssh['remote_host']}]:{ssh['remote_port']}"
            if not re.fullmatch(re.escape(host_token) + r" (ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) ([A-Za-z0-9+/]+={0,2})", known_host_line):
                raise CompositionHold("known-hosts does not contain the one exact host key")
            if not identity_raw.startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----\n"):
                raise CompositionHold("dedicated SSH identity format is invalid")
            tool_descriptor = _tool_descriptor(request, self.tool_authority)
            packet_header, binding = _build_packet_header(
                request=request,
                helper_source=helper_raw,
                archive_size=len(archive_raw),
                archive_sha256=request["archive_sha256"],
                tool_descriptor=tool_descriptor,
            )
            sealed_hosts_fd = _sealed_memfd(hosts_raw, "tgw-render-known-hosts")
            sealed_identity_fd = _sealed_memfd(identity_raw, "tgw-render-ssh-identity")
            remote_argv = [self.composition.wrapper["path"]]
            if self._use_sudo:
                remote_argv = [REMOTE_SUDO_PATH, "-n", "--", *remote_argv]
            remote_command = serialize_remote_argv(remote_argv)
            command = [
                f"/proc/self/fd/{ssh_fd}",
                "-F",
                "/dev/null",
                "-oBatchMode=yes",
                "-oIdentitiesOnly=yes",
                "-oIdentityAgent=none",
                f"-oIdentityFile=/proc/{os.getpid()}/fd/{sealed_identity_fd}",
                "-oPreferredAuthentications=publickey",
                "-oPasswordAuthentication=no",
                "-oKbdInteractiveAuthentication=no",
                "-oNumberOfPasswordPrompts=0",
                "-oClearAllForwardings=yes",
                "-oStrictHostKeyChecking=yes",
                "-oGlobalKnownHostsFile=/dev/null",
                f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts_fd}",
                "-oCanonicalizeHostname=no",
                "-oProxyCommand=none",
                "-oProxyJump=none",
                "-oPermitLocalCommand=no",
                "-oRequestTTY=no",
                "-oLogLevel=ERROR",
            ]
            if ssh["remote_port"] != 22:
                command.extend(["-p", str(ssh["remote_port"])])
            command.extend(["--", f"{ssh['remote_user']}@{ssh['remote_host']}", remote_command])
            attempt_receipt = _attempt_receipt(self.composition, request, generation)
            try:
                attempt_ref = self.attempt_store.persist(attempt_receipt)
            except (OSError, RenderTransportError) as exc:
                raise RenderTransportError("immutable attempt receipt was not ready before launch") from exc
            timeout = int(request["max_duration_seconds"]) + 30
            maximum = min(32 * 1024 * 1024, int(request["max_output_bytes"]) * 2 + 16 * 1024)
            pass_fds = (ssh_fd, sealed_hosts_fd, sealed_identity_fd)
            try:
                if self.invoke is None:
                    completed = self._invoke_streaming(
                        command,
                        packet_header,
                        archive_fd,
                        timeout=timeout,
                        max_output=maximum,
                        pass_fds=pass_fds,
                    )
                else:
                    completed = self.invoke(
                        command,
                        input=packet_header + archive_raw,
                        capture_output=True,
                        timeout=timeout,
                        check=False,
                        pass_fds=pass_fds,
                        env={"LC_ALL": "C"},
                    )
            except BaseException as exc:
                raise RemoteAttemptAmbiguous("transport did not return a terminal receipt", attempt_receipt, attempt_ref) from exc
            if len(completed.stdout) > maximum:
                raise RemoteAttemptAmbiguous("remote receipt exceeded its bound", attempt_receipt, attempt_ref)
            return TransportExchange(tuple(command), completed.returncode, completed.stdout, binding, tool_descriptor, attempt_receipt, attempt_ref)
        finally:
            for fd in (sealed_identity_fd, sealed_hosts_fd, archive_fd, helper_fd, identity_fd, hosts_fd, ssh_fd):
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
            process = subprocess.Popen(
                command,
                stdin=packet,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                pass_fds=pass_fds,
                env={"LC_ALL": "C"},
            )
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
                    raise subprocess.TimeoutExpired(command, timeout)
                for key, _ in selector.select(min(remaining, 0.25)):
                    block = key.fileobj.read1(min(65536, max_output + 1 - len(output)))
                    if not block:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(block)
                if len(output) > max_output:
                    process.kill()
                    process.wait()
                    raise RuntimeError("remote receipt exceeded its bound")
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return subprocess.CompletedProcess(command, returncode, bytes(output), b"")


def _probe_is_exact(value: Any) -> bool:
    return value == {
        "schema": "tgw-render-netns-negative-probe/v1",
        "links": ["lo"],
        "loopback_state": "down",
        "ipv4_route_count": 0,
        "ipv6_route_count": 0,
        "direct_probe": "ENETUNREACH",
    }


def _attestation_payload(value: Mapping[str, Any]) -> bytes:
    namespace = value["namespace"]
    child = value["child"]
    fields = [
        value["schema"],
        value["request_sha256"],
        value["helper_sha256"],
        value["remote_bootstrap_sha256"],
        value["remote_python_sha256"],
        value["remote_ip_sha256"],
        value["wrapper_sha256"],
        value["wrapper_prerequisite_receipt_sha256"],
        namespace["before"],
        namespace["after"],
        "1" if namespace["changed"] else "0",
        _digest_bytes(canonical(namespace["pre"])),
        _digest_bytes(canonical(namespace["post"])),
        str(child["uid"]),
        str(child["gid"]),
        str(child["returncode"]),
        str(child["terminal_bytes"]),
        child["terminal_sha256"],
    ]
    return b"\0".join(item.encode("ascii") for item in fields)


def validate_wrapper_envelope(value: Any, *, composition: RenderComposition) -> tuple[dict[str, Any], bytes]:
    fields = {
        "schema",
        "request_sha256",
        "helper_sha256",
        "remote_bootstrap_sha256",
        "remote_python_sha256",
        "remote_ip_sha256",
        "wrapper_sha256",
        "wrapper_prerequisite_receipt_sha256",
        "namespace",
        "child",
        "attestation",
    }
    if not isinstance(value, dict) or set(value) != fields or value["schema"] != _WRAPPER_SCHEMA:
        raise RenderTransportError("wrapper envelope is not exact")
    namespace = value["namespace"]
    child = value["child"]
    attestation = value["attestation"]
    if (
        value["request_sha256"] != composition.value["request_sha256"]
        or value["helper_sha256"] != HELPER_SHA256
        or value["remote_bootstrap_sha256"] != composition.components["remote_bootstrap"]["sha256"]
        or value["remote_python_sha256"] != composition.wrapper["remote_python_sha256"]
        or value["remote_ip_sha256"] != composition.wrapper["remote_ip_sha256"]
        or value["wrapper_sha256"] != composition.wrapper["sha256"]
        or value["wrapper_prerequisite_receipt_sha256"] != composition.wrapper["prerequisite_receipt"]["sha256"]
        or not isinstance(namespace, dict)
        or set(namespace) != {"schema", "before", "after", "changed", "pre", "post"}
        or namespace["schema"] != "tgw-render-network-namespace-evidence/v1"
        or not _NETNS.fullmatch(str(namespace["before"]))
        or not _NETNS.fullmatch(str(namespace["after"]))
        or namespace["before"] == namespace["after"]
        or namespace["changed"] is not True
        or not _probe_is_exact(namespace["pre"])
        or not _probe_is_exact(namespace["post"])
        or not isinstance(child, dict)
        or set(child) != {"uid", "gid", "returncode", "terminal_bytes", "terminal_sha256", "terminal_b64"}
        or child["uid"] != composition.wrapper["remote_uid"]
        or child["gid"] != composition.wrapper["remote_gid"]
        or type(child["returncode"]) is not int
        or not 0 <= child["returncode"] <= 255
        or type(child["terminal_bytes"]) is not int
        or not 1 <= child["terminal_bytes"] <= composition.request["max_output_bytes"]
        or not _DIGEST.fullmatch(str(child["terminal_sha256"]))
        or not isinstance(attestation, dict)
        or set(attestation) != {"algorithm", "public_key_sha256", "signature"}
        or attestation["algorithm"] != "ed25519"
        or attestation["public_key_sha256"] != composition.wrapper["attestation_public_key"]["sha256"]
    ):
        raise RenderTransportError("wrapper envelope binding or namespace evidence is invalid")
    try:
        terminal_raw = base64.b64decode(child["terminal_b64"], validate=True)
        signature = base64.b64decode(attestation["signature"], validate=True)
    except (TypeError, ValueError) as exc:
        raise RenderTransportError("wrapper envelope encoding is invalid") from exc
    if len(terminal_raw) != child["terminal_bytes"] or _digest_bytes(terminal_raw) != child["terminal_sha256"]:
        raise RenderTransportError("wrapper terminal binding is invalid")
    public_raw = _file_identity(composition.wrapper["attestation_public_key"], label="wrapper attestation public key", maximum=128)
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, _attestation_payload(value))
    except (ValueError, InvalidSignature) as exc:
        raise RenderTransportError("wrapper namespace attestation signature is invalid") from exc
    return dict(value), terminal_raw


def validate_a2_response(
    value: Mapping[str, Any],
    *,
    binding: helper.WireBinding,
    request: Mapping[str, Any],
    tool_descriptor: Mapping[str, Any],
    authority: helper.A2Authority = helper.PRODUCTION_A2_AUTHORITY,
) -> dict[str, Any]:
    try:
        terminal = helper.validate_a2_terminal(value, binding=binding, request=request, tool_descriptor=tool_descriptor, authority=authority)
        if terminal["schema"] == helper.SUCCESS_SCHEMA:
            helper._validate_provider_receipt(terminal["provider_receipt"], request=request, authority=authority)
        return terminal
    except helper.RenderHelperError as exc:
        raise RenderTransportError("remote render A2 terminal validation failed") from exc


def _binding_from_composition(composition: RenderComposition, request: Mapping[str, Any]) -> tuple[helper.WireBinding, dict[str, Any]]:
    descriptor = _tool_descriptor(request, helper.PRODUCTION_GIT_AUTHORITY)
    return (
        helper.WireBinding(
            request_bytes=len(canonical(request)),
            helper_bytes=composition.components["helper"]["size"],
            tool_descriptor_bytes=len(canonical(descriptor)),
            archive_bytes=composition.source["archive"]["size"],
            request_sha256=request["request_sha256"],
            helper_sha256=HELPER_SHA256,
            tool_descriptor_sha256=_digest_bytes(canonical(descriptor)),
            archive_sha256=request["archive_sha256"],
        ),
        descriptor,
    )


def _validate_attempt(value: Any, reference: Any, *, composition: RenderComposition, request: Mapping[str, Any], generation: str) -> dict[str, Any]:
    expected = _attempt_receipt(composition, request, generation)
    raw = canonical(expected)
    digest = _digest_bytes(raw)
    expected_ref = {
        "artifact_ref": "artifact:" + digest,
        "path": str(Path(composition.value["receipt_roots"]["attempts"]) / (digest.removeprefix("sha256:") + ".json")),
        "sha256": digest,
        "size": len(raw),
    }
    if value != expected or reference != expected_ref:
        raise RenderTransportError("immutable launch-attempt receipt binding is invalid")
    return expected


def validate_handler_success(value: Mapping[str, Any], *, request: Mapping[str, Any], composition: RenderComposition) -> dict[str, Any]:
    """Revalidate success from the compiled helper and exact composition, not terminal claims."""
    composition.revalidate_runtime()
    request = composition.validate_request(request)
    fields = {
        "schema",
        "generation",
        "composition_sha256",
        "attempt_receipt",
        "attempt_ref",
        "transport_receipt",
        "terminal",
        "terminal_ref",
        "receipt_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value["schema"] != _HANDLER_SCHEMA or not _self_hashed(value):
        raise RenderTransportError("render handler result is not exact")
    if value["composition_sha256"] != composition.receipt_sha256 or not isinstance(value["generation"], str) or not _IDENTITY.fullmatch(value["generation"]):
        raise RenderTransportError("render handler composition binding is invalid")
    _validate_attempt(value["attempt_receipt"], value["attempt_ref"], composition=composition, request=request, generation=value["generation"])
    envelope, terminal_raw = validate_wrapper_envelope(value["transport_receipt"], composition=composition)
    try:
        decoded = json.loads(terminal_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RenderTransportError("signed wrapper terminal is malformed") from exc
    if decoded != value["terminal"]:
        raise RenderTransportError("handler terminal differs from the signed wrapper terminal")
    binding, descriptor = _binding_from_composition(composition, request)
    terminal = validate_a2_response(decoded, binding=binding, request=request, tool_descriptor=descriptor, authority=helper.PRODUCTION_A2_AUTHORITY)
    validate_result(terminal["provider_receipt"], request=request)
    terminal_raw = canonical(terminal)
    terminal_digest = _digest_bytes(terminal_raw)
    expected_terminal_ref = {
        "artifact_ref": "artifact:" + terminal_digest,
        "path": str(Path(composition.value["receipt_roots"]["terminals"]) / (terminal_digest.removeprefix("sha256:") + ".json")),
        "sha256": terminal_digest,
        "size": len(terminal_raw),
    }
    if (
        envelope["child"]["returncode"] != 0
        or value["terminal_ref"] != expected_terminal_ref
    ):
        raise RenderTransportError("handler terminal persistence or exit binding is invalid")
    return dict(value)


class ObserverRenderController:
    def __init__(
        self,
        transport: SshObserverRenderTransport,
        terminal_store: ReceiptStore,
        *,
        composition: RenderComposition,
        authority: helper.A2Authority = helper.PRODUCTION_A2_AUTHORITY,
    ):
        self.transport = transport
        self.terminal_store = terminal_store
        self.composition = composition
        self.authority = authority

    @staticmethod
    def _ambiguous(reason: str, exchange: TransportExchange) -> NoReturn:
        raise RemoteAttemptAmbiguous(reason, exchange.attempt_receipt, exchange.attempt_ref)

    def __call__(self, effect: Mapping[str, Any]) -> Mapping[str, Any]:
        normalized, request = _validate_effect(effect)
        request = self.composition.validate_request(request)
        exchange = self.transport(request, generation=normalized["generation"])
        try:
            outer_untrusted = json.loads(exchange.stdout)
            transport_receipt, terminal_raw = validate_wrapper_envelope(outer_untrusted, composition=self.composition)
        except (UnicodeDecodeError, json.JSONDecodeError, RenderTransportError):
            self._ambiguous("wrapper receipt was empty, malformed, or lost", exchange)
        if exchange.returncode != transport_receipt["child"]["returncode"]:
            self._ambiguous("SSH and signed child exit status disagree", exchange)
        try:
            untrusted = json.loads(terminal_raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._ambiguous("signed child terminal is malformed", exchange)
        try:
            if not isinstance(untrusted, dict):
                raise RenderTransportError("remote render terminal is not an object")
            if untrusted.get("schema") == helper.PHASE1_FAILURE_SCHEMA:
                terminal = helper.validate_phase1_failure(untrusted, binding=exchange.binding)
                expected_returncode = 1
            elif untrusted.get("schema") in {helper.SUCCESS_SCHEMA, helper.A2_FAILURE_SCHEMA}:
                terminal = validate_a2_response(
                    untrusted,
                    binding=exchange.binding,
                    request=request,
                    tool_descriptor=exchange.tool_descriptor,
                    authority=self.authority,
                )
                expected_returncode = 0 if terminal["schema"] == helper.SUCCESS_SCHEMA else 2 if terminal["outcome"] == "AMBIGUOUS" else 1
            else:
                raise RenderTransportError("remote render terminal schema is unknown")
        except RenderTransportError:
            self._ambiguous("signed remote terminal failed exact validation", exchange)
        if transport_receipt["child"]["returncode"] != expected_returncode:
            self._ambiguous("signed terminal and child exit status disagree", exchange)
        try:
            terminal_ref = self.terminal_store.persist(terminal)
        except (OSError, RenderTransportError) as exc:
            raise TerminalPersistenceError(terminal, exchange.attempt_receipt, exchange.attempt_ref) from exc
        if terminal["schema"] != helper.SUCCESS_SCHEMA:
            raise RemoteRenderFailure(terminal, terminal_ref, exchange.attempt_ref)
        result = {
            "schema": _HANDLER_SCHEMA,
            "generation": normalized["generation"],
            "composition_sha256": self.composition.receipt_sha256,
            "attempt_receipt": dict(exchange.attempt_receipt),
            "attempt_ref": dict(exchange.attempt_ref),
            "transport_receipt": transport_receipt,
            "terminal": terminal,
            "terminal_ref": dict(terminal_ref),
        }
        result["receipt_sha256"] = _digest_bytes(canonical(result))
        return result


def compose_production_controller(
    *,
    composition_path: Path = PRODUCTION_COMPOSITION_PATH,
    invoke: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> tuple[ObserverRenderController, dict[str, Any]]:
    composition = load_composition(composition_path)
    attempt_store = ImmutableTerminalReceiptStore(Path(composition.value["receipt_roots"]["attempts"]))
    terminal_store = ImmutableTerminalReceiptStore(Path(composition.value["receipt_roots"]["terminals"]))
    transport = SshObserverRenderTransport(composition, attempt_store, invoke=invoke)
    controller = ObserverRenderController(transport, terminal_store, composition=composition)
    return controller, {
        "schema": _COMPOSITION_SCHEMA,
        "receipt_sha256": composition.receipt_sha256,
        "source_commit": composition.source["commit"],
        "source_tree": composition.source["tree"],
        "request_sha256": composition.value["request_sha256"],
        "audited_a2_commit": AUDITED_A2_COMMIT,
        "helper_sha256": HELPER_SHA256,
        "wrapper_sha256": composition.wrapper["sha256"],
        "attempt_store": attempt_store.readiness,
        "terminal_store": terminal_store.readiness,
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
        result = controller(effect)
    except CompositionHold as exc:
        error.write(f"render evaluation HOLD: {exc}\n")
        return 3
    except RemoteRenderFailure as exc:
        if exc.terminal.get("outcome") == "AMBIGUOUS":
            sink.write(
                canonical(
                    {
                        "schema": _ATTEMPT_SCHEMA,
                        "outcome": "AMBIGUOUS",
                        "attempt_ref": exc.attempt_ref,
                        "terminal": exc.terminal,
                    }
                )
            )
            return 2
        sink.write(canonical(exc.terminal))
        return 1
    except RemoteAttemptAmbiguous as exc:
        sink.write(canonical({"schema": _ATTEMPT_SCHEMA, "outcome": "AMBIGUOUS", "attempt_ref": exc.attempt_ref, "reconciliation": exc.reconciliation}))
        return 2
    except Exception as exc:
        error.write(f"render evaluation refused: {exc}\n")
        return 2
    sink.write(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
