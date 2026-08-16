"""Sealed controller-side SSH provider for the W09 application transaction."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from tgw.a3_host_state_observation import (
    _bounded_stream,
    _group_empty_or_kill,
    _held_regular,
    _inode_identity,
    _post_reap_group_state,
    _sealed,
)
from tgw.a3_preintegration_observation import _run_held_bounded
from tgw.application_release_remote import RESPONSE_SCHEMA, SCHEMA
from tgw.platform_bootstrap import BootstrapStateAmbiguous

DESCRIPTOR_SCHEMA = "tgw-w09-application-release-provider/v1"
REMOTE_HELPER = "/run/current-system/sw/bin/tgw-application-release-helper"
REMOTE_SUDO = "/run/wrappers/bin/sudo"
REMOTE_COMMAND = f"{REMOTE_SUDO} -n -- {REMOTE_HELPER}"
_SEAL = object()
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ApplicationReleaseProviderError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ApplicationReleaseProviderError("provider value is not canonical JSON") from exc


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash(value: Any) -> str:
    return _hash_bytes(_canonical(value))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ApplicationReleaseProviderError(f"{label} is invalid")
    return dict(value)


def _validate_response_shape(response: Mapping[str, Any]) -> None:
    common = {
        "schema", "operation_id", "helper_sha256", "helper_config_sha256",
        "nix_system_path", "status", "receipt_sha256",
    }
    status = response.get("status")
    if status == "SUCCEEDED":
        expected = common | {"evidence"}
    elif status in {"RESTORED", "AMBIGUOUS"}:
        expected = common | {"generation", "predecessor_healthy", "evidence"}
        if "receipt" in response:
            expected.add("receipt")
        if "detail_hash" in response:
            expected.add("detail_hash")
    else:
        raise ApplicationReleaseProviderError("remote application release status is invalid")
    evidence = response.get("evidence")
    if (
        set(response) != expected or not isinstance(evidence, list) or not evidence
        or any(not isinstance(item, str) or not item or len(item) > 512 for item in evidence)
    ):
        raise ApplicationReleaseProviderError("remote application release evidence schema is invalid")
    if "detail_hash" in response and _SHA.fullmatch(str(response["detail_hash"])) is None:
        raise ApplicationReleaseProviderError("remote application release failure hash is invalid")


def validate_provider_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = _exact(value, {
        "schema", "target", "transport", "candidate", "runtime_config", "remote_boundary",
        "bounds", "prerequisite_receipt", "descriptor_hash",
    }, "application release provider descriptor")
    unsigned = dict(descriptor); claimed = unsigned.pop("descriptor_hash")
    if descriptor["schema"] != DESCRIPTOR_SCHEMA or claimed != _hash(unsigned):
        raise ApplicationReleaseProviderError("provider descriptor schema/hash is invalid")
    target = _exact(descriptor["target"], {"host", "address", "port", "user"}, "provider target")
    if target != {"host": "tgw-prod", "address": "100.107.99.66", "port": 22, "user": "tgw-release-bootstrap"}:
        raise ApplicationReleaseProviderError("provider target is not exact tgw-prod")
    transport = _exact(descriptor["transport"], {
        "ssh_path", "ssh_sha256", "ssh_keygen_path", "ssh_keygen_sha256",
        "known_hosts_path", "known_hosts_sha256",
        "identity_path", "identity_sha256", "helper_source_path", "helper_source_sha256",
    }, "provider transport")
    for name in ("ssh_path", "ssh_keygen_path", "known_hosts_path", "identity_path", "helper_source_path"):
        if not isinstance(transport[name], str) or not transport[name].startswith("/"):
            raise ApplicationReleaseProviderError("provider transport path is invalid")
    for name in ("ssh_sha256", "ssh_keygen_sha256", "known_hosts_sha256", "identity_sha256", "helper_source_sha256"):
        if _SHA.fullmatch(str(transport[name])) is None:
            raise ApplicationReleaseProviderError("provider transport hash is invalid")
    candidate = _exact(descriptor["candidate"], {
        "archive_path", "archive_sha256", "commit", "tree", "effect_parameters_sha256",
    }, "provider candidate")
    runtime = _exact(descriptor["runtime_config"], {"path", "content_sha256"}, "provider runtime config")
    for binding, path_name in ((candidate, "archive_path"), (runtime, "path")):
        if not isinstance(binding[path_name], str) or not binding[path_name].startswith("/"):
            raise ApplicationReleaseProviderError("provider artifact path is invalid")
    for digest in (candidate["archive_sha256"], candidate["effect_parameters_sha256"], runtime["content_sha256"]):
        if _SHA.fullmatch(str(digest)) is None:
            raise ApplicationReleaseProviderError("provider artifact hash is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", str(candidate["commit"])) or not re.fullmatch(r"[0-9a-f]{40}", str(candidate["tree"])):
        raise ApplicationReleaseProviderError("provider candidate identity is invalid")
    boundary = _exact(descriptor["remote_boundary"], {
        "forced_command", "sudo_command", "helper_path", "helper_sha256", "config_sha256",
        "authorized_public_key",
    }, "provider remote boundary")
    if boundary["forced_command"] != REMOTE_COMMAND or boundary["sudo_command"] != REMOTE_COMMAND or boundary["helper_path"] != REMOTE_HELPER:
        raise ApplicationReleaseProviderError("provider forced-command/sudo boundary is invalid")
    if any(_SHA.fullmatch(str(boundary[name])) is None for name in ("helper_sha256", "config_sha256")):
        raise ApplicationReleaseProviderError("provider remote boundary hash is invalid")
    if re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}", str(boundary["authorized_public_key"])) is None:
        raise ApplicationReleaseProviderError("provider authorized public key is invalid")
    bounds = _exact(descriptor["bounds"], {"timeout_seconds", "max_output_bytes", "max_diagnostic_bytes", "max_packet_bytes"}, "provider bounds")
    if not 1 <= int(bounds["timeout_seconds"]) <= 1800 or not 1024 <= int(bounds["max_output_bytes"]) <= 4 * 1024 * 1024 or not 1024 <= int(bounds["max_diagnostic_bytes"]) <= 1024 * 1024 or not 1024 <= int(bounds["max_packet_bytes"]) <= 192 * 1024 * 1024:
        raise ApplicationReleaseProviderError("provider bounds are invalid")
    prerequisite = _exact(descriptor["prerequisite_receipt"], {"ref", "path", "sha256"}, "provider prerequisite")
    if not isinstance(prerequisite["path"], str) or not prerequisite["path"].startswith("/") or _SHA.fullmatch(str(prerequisite["sha256"])) is None:
        raise ApplicationReleaseProviderError("provider prerequisite is invalid")
    return descriptor


def load_provider_descriptor(path: Path) -> dict[str, Any]:
    path = Path(path)
    metadata = path.lstat()
    if not path.is_absolute() or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o400:
        raise ApplicationReleaseProviderError("provider descriptor is not a protected controller artifact")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationReleaseProviderError("provider descriptor is unreadable") from exc
    return validate_provider_descriptor(value)


class SshApplicationReleaseProvider:
    """One exact provider; production construction is sealed by the factory."""

    __slots__ = ("_descriptor", "_fds", "_raw", "_identities", "_frozen")

    def __init_subclass__(cls, **kwargs: Any) -> None:
        raise TypeError("SshApplicationReleaseProvider is sealed")

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_frozen", False):
            raise AttributeError("sealed application release provider is immutable")
        object.__setattr__(self, name, value)

    @property
    def descriptor(self) -> Mapping[str, Any]:
        return self._descriptor

    @property
    def production_authority(self) -> bool:
        return True

    def __init__(
        self, descriptor: Mapping[str, Any], *, _token: object,
        _descriptor_artifact: tuple[Path, int, bytes, tuple[int, ...]],
    ) -> None:
        if _token is not _SEAL:
            raise TypeError("production application release provider must use sealed factory")
        self._descriptor = _freeze(validate_provider_descriptor(descriptor))
        transport = self.descriptor["transport"]
        candidate = self.descriptor["candidate"]
        runtime = self.descriptor["runtime_config"]
        specifications = (
            (Path(transport["ssh_path"]), transport["ssh_sha256"], True, {0o555, 0o755}, 0),
            (Path(transport["known_hosts_path"]), transport["known_hosts_sha256"], False, {0o400, 0o444}, 0),
            (Path(transport["identity_path"]), transport["identity_sha256"], False, {0o400}, os.getuid()),
            (Path(transport["helper_source_path"]), transport["helper_source_sha256"], False, {0o400, 0o444}, 0),
            (Path(candidate["archive_path"]), candidate["archive_sha256"], False, {0o400, 0o440, 0o444}, os.getuid()),
            (Path(runtime["path"]), runtime["content_sha256"], False, {0o400, 0o440, 0o444}, os.getuid()),
            (Path(self.descriptor["prerequisite_receipt"]["path"]), self.descriptor["prerequisite_receipt"]["sha256"], False, {0o400, 0o444}, 0),
            (Path(transport["ssh_keygen_path"]), transport["ssh_keygen_sha256"], True, {0o555, 0o755}, 0),
        )
        descriptor_path, descriptor_fd, descriptor_raw, descriptor_identity = _descriptor_artifact
        fds: list[int] = []
        raw_values: list[bytes] = []
        identities = []
        try:
            for path, digest, executable, modes, uid in specifications:
                fd, raw = _held_regular(path, digest, executable=executable)
                metadata = os.fstat(fd)
                if metadata.st_uid != uid or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) not in modes:
                    raise ApplicationReleaseProviderError("provider artifact metadata differs")
                fds.append(fd); raw_values.append(raw); identities.append((path, _inode_identity(metadata)))
            fds.append(descriptor_fd)
            raw_values.append(descriptor_raw)
            identities.append((descriptor_path, descriptor_identity))
        except Exception:
            for fd in reversed(fds): os.close(fd)
            if descriptor_fd not in fds:
                os.close(descriptor_fd)
            raise
        if _hash_bytes(raw_values[3]) != self.descriptor["remote_boundary"]["helper_sha256"]:
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("local reviewed helper differs from installed remote helper binding")
        known_hosts = raw_values[1].decode("ascii", errors="strict").strip().splitlines()
        if (
            len(known_hosts) != 1
            or re.fullmatch(r"tgw-prod ssh-ed25519 [A-Za-z0-9+/]+={0,2}", known_hosts[0]) is None
            or not raw_values[2].startswith(b"-----BEGIN OPENSSH PRIVATE KEY-----\n")
        ):
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider SSH identity/known-host grammar is invalid")
        try:
            prerequisite = json.loads(raw_values[6])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider prerequisite receipt is invalid") from exc
        prerequisite = _exact(prerequisite, {
            "schema", "receipt_id", "target_host", "remote_helper_sha256",
            "helper_config_sha256", "authorized_public_key_sha256", "verified", "receipt_hash",
        }, "provider prerequisite receipt")
        prerequisite_unsigned = dict(prerequisite)
        prerequisite_claimed = prerequisite_unsigned.pop("receipt_hash")
        if (
            prerequisite["schema"] != "tgw-w09-application-release-prerequisite/v1"
            or prerequisite["receipt_id"] != self.descriptor["prerequisite_receipt"]["ref"]
            or prerequisite["target_host"] != "tgw-prod"
            or prerequisite["remote_helper_sha256"] != self.descriptor["remote_boundary"]["helper_sha256"]
            or prerequisite["helper_config_sha256"] != self.descriptor["remote_boundary"]["config_sha256"]
            or prerequisite["authorized_public_key_sha256"] != _hash_bytes(
                (self.descriptor["remote_boundary"]["authorized_public_key"] + "\n").encode()
            )
            or prerequisite["verified"] is not True
            or prerequisite_claimed != _hash(prerequisite_unsigned)
        ):
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider prerequisite receipt binding differs")
        try:
            sealed_private = _sealed("w09-key-crossmatch", raw_values[2])
        except Exception:
            for fd in reversed(fds):
                os.close(fd)
            raise
        try:
            try:
                returncode, public_key, _diagnostic = _run_held_bounded(
                    [
                        f"/proc/{os.getpid()}/fd/{fds[7]}", "-y", "-f",
                        f"/proc/{os.getpid()}/fd/{sealed_private}",
                    ],
                    pass_fds=(fds[7], sealed_private), timeout=10, limit=16 * 1024,
                    env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
                )
            except Exception:
                for fd in reversed(fds):
                    os.close(fd)
                raise
        finally:
            os.close(sealed_private)
        if (
            returncode != 0
            or public_key.strip().decode("ascii", errors="strict")
            != self.descriptor["remote_boundary"]["authorized_public_key"]
        ):
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("dedicated private/public SSH key authority differs")
        self._fds, self._raw, self._identities = tuple(fds), tuple(raw_values), tuple(identities)
        self._frozen = True

    def close(self) -> None:
        for fd in reversed(getattr(self, "_fds", ())):
            try: os.close(fd)
            except OSError: pass
        object.__setattr__(self, "_fds", ())

    def __del__(self) -> None:
        self.close()

    def _check_parameters(self, parameters: Mapping[str, Any]) -> None:
        candidate = self.descriptor["candidate"]; runtime = self.descriptor["runtime_config"]
        if (
            parameters.get("candidate_commit") != candidate["commit"]
            or parameters.get("candidate_tree") != candidate["tree"]
            or parameters.get("archive_sha256") != candidate["archive_sha256"]
            or parameters.get("runtime_config", {}).get("content_sha256") != runtime["content_sha256"]
            or _hash(parameters) != candidate["effect_parameters_sha256"]
        ):
            raise ApplicationReleaseProviderError("effect parameters differ from mounted provider artifacts")

    def _packet(self, action: str, parameters: Mapping[str, Any]) -> bytes:
        self._check_parameters(parameters)
        unsigned = {
            "schema": SCHEMA, "action": action, "parameters": dict(parameters),
            "archive_b64": base64.b64encode(self._raw[4]).decode("ascii") if action == "install" else "",
            "config_b64": base64.b64encode(self._raw[5]).decode("ascii") if action == "install" else "",
        }
        return _canonical({**unsigned, "request_hash": _hash(unsigned)})

    def _dispatch(self, action: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
        packet = self._packet(action, parameters)
        bounds = self.descriptor["bounds"]
        if len(packet) > int(bounds["max_packet_bytes"]):
            raise ApplicationReleaseProviderError("application release packet exceeds fixed bound")
        ssh_fd = self._fds[0]
        sealed_hosts = _sealed("w09-app-hosts", self._raw[1])
        sealed_identity = -1
        try:
            sealed_identity = _sealed("w09-app-identity", self._raw[2])
            argv = [
                f"/proc/{os.getpid()}/fd/{ssh_fd}", "-F", "/dev/null", "-p", "22",
                "-oBatchMode=yes", "-oIdentitiesOnly=yes", "-oIdentityAgent=none", "-oClearAllForwardings=yes",
                "-oStrictHostKeyChecking=yes", "-oGlobalKnownHostsFile=/dev/null", "-oCanonicalizeHostname=no",
                "-oProxyCommand=none", "-oProxyJump=none", "-oPreferredAuthentications=publickey",
                "-oKbdInteractiveAuthentication=no", "-oGSSAPIAuthentication=no", "-oHostbasedAuthentication=no",
                "-oPubkeyAuthentication=yes", "-oPermitLocalCommand=no", "-oControlMaster=no", "-oControlPath=none",
                "-oUpdateHostKeys=no", "-oVerifyHostKeyDNS=no", "-oForwardAgent=no", "-oForwardX11=no",
                "-oHostKeyAlias=tgw-prod", f"-oUserKnownHostsFile=/proc/{os.getpid()}/fd/{sealed_hosts}",
                f"-oIdentityFile=/proc/{os.getpid()}/fd/{sealed_identity}", "-oPasswordAuthentication=no", "-T",
                "tgw-release-bootstrap@100.107.99.66",
            ]
            process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                start_new_session=True, pass_fds=(ssh_fd, sealed_hosts, sealed_identity),
                env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
            )
            stream_error: Exception | None = None
            try:
                stdout, _stderr = _bounded_stream(
                    process, packet, stdout_limit=int(bounds["max_output_bytes"]),
                    stderr_limit=int(bounds["max_diagnostic_bytes"]), timeout=int(bounds["timeout_seconds"]),
                )
            except Exception as exc:
                stream_error = exc; stdout = b""
            state = _group_empty_or_kill(process.pid)
            try: process.wait(timeout=1); state["reaped"] = True
            except subprocess.TimeoutExpired: state["reaped"] = False
            state = _post_reap_group_state(process.pid, state)
        finally:
            if sealed_identity >= 0:
                os.close(sealed_identity)
            os.close(sealed_hosts)
        if stream_error is not None or process.returncode != 0 or state.get("had_survivor") or not state.get("removed") or not state.get("reaped"):
            raise BootstrapStateAmbiguous("application release SSH lifecycle is ambiguous", evidence=("application-release-transport:" + _hash_bytes(stdout),), rollback_required=False)
        for (path, identity), fd, raw in zip(self._identities, self._fds, self._raw, strict=True):
            if _inode_identity(os.fstat(fd)) != identity or _hash_bytes(os.pread(fd, len(raw) + 1, 0)) != _hash_bytes(raw) or _inode_identity(os.stat(path, follow_symlinks=False)) != identity:
                raise BootstrapStateAmbiguous("application release provider artifact changed", evidence=("application-release-artifact:changed",), rollback_required=False)
        try:
            response = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BootstrapStateAmbiguous("application release response is invalid", evidence=("application-release-response:" + _hash_bytes(stdout),), rollback_required=False) from exc
        if not isinstance(response, dict):
            raise BootstrapStateAmbiguous("application release response is not an object", evidence=("application-release-response:" + _hash_bytes(stdout),), rollback_required=False)
        unsigned = dict(response); claimed = unsigned.pop("receipt_sha256", None)
        boundary = self.descriptor["remote_boundary"]
        if (
            response.get("schema") != RESPONSE_SCHEMA or claimed != _hash(unsigned)
            or response.get("operation_id") != parameters["operation_id"]
            or response.get("helper_sha256") != boundary["helper_sha256"]
            or response.get("helper_config_sha256") != boundary["config_sha256"]
            or response.get("nix_system_path") != parameters["nix_system_path"]
        ):
            raise BootstrapStateAmbiguous("application release response binding differs", evidence=("application-release-response:" + _hash_bytes(stdout),), rollback_required=False)
        try:
            _validate_response_shape(response)
        except ApplicationReleaseProviderError as exc:
            raise BootstrapStateAmbiguous(
                "application release response evidence differs",
                evidence=("application-release-response:" + _hash_bytes(stdout),),
                rollback_required=False,
            ) from exc
        return response

    def install(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self._dispatch("install", parameters)
        if result.get("status") == "SUCCEEDED" and isinstance(result.get("evidence"), list) and result["evidence"]:
            return {"evidence": list(result["evidence"]) + ["application-release:" + result["receipt_sha256"]]}
        if result.get("status") == "AMBIGUOUS":
            raise BootstrapStateAmbiguous("remote application deployment is ambiguous", evidence=tuple(result.get("evidence", ())), rollback_required=False)
        raise ApplicationReleaseProviderError("remote application deployment rolled back")

    def rollback(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self._dispatch("rollback", parameters)
        if result.get("status") != "RESTORED" or result.get("predecessor_healthy") is not True or not isinstance(result.get("receipt"), str):
            raise BootstrapStateAmbiguous("remote predecessor reconciliation is ambiguous", evidence=tuple(result.get("evidence", ())), rollback_required=False)
        return {"receipt": result["receipt"], "evidence": list(result.get("evidence", ())) }


def build_production_application_release_provider(descriptor_path: Path) -> SshApplicationReleaseProvider:
    """Return the exact sealed provider, or HOLD before any remote dispatch."""
    path = Path(descriptor_path)
    if not path.is_absolute():
        raise ApplicationReleaseProviderError("production provider descriptor path is not absolute")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        raw = os.pread(fd, 1024 * 1024 + 1, 0)
        named = os.stat(path, follow_symlinks=False)
        identity = _inode_identity(metadata)
        if (
            len(raw) > 1024 * 1024 or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o444
            or _inode_identity(named) != identity
        ):
            raise ApplicationReleaseProviderError("production provider descriptor is not root-protected")
        descriptor = validate_provider_descriptor(json.loads(raw))
    except Exception:
        os.close(fd)
        raise
    return SshApplicationReleaseProvider(
        descriptor, _token=_SEAL,
        _descriptor_artifact=(path, fd, raw, identity),
    )
