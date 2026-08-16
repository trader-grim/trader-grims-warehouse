"""Sealed controller-side SSH provider for the W09 application transaction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from datetime import datetime, timezone

from tgw.a3_host_state_observation import (
    _bounded_stream,
    _group_empty_or_kill,
    _held_regular,
    _inode_identity,
    _post_reap_group_state,
    _sealed,
)
from tgw.a3_preintegration_observation import _run_held_bounded
from tgw.application_release_remote import FRAMED_SCHEMA, RESPONSE_SCHEMA
from tgw.platform_bootstrap import BootstrapStateAmbiguous

DESCRIPTOR_SCHEMA = "tgw-w09-application-release-provider/v2"
REMOTE_SUDO = "/run/wrappers/bin/sudo"
REMOTE_PYTHON = "/run/current-system/sw/bin/python3"
FRAME_SCHEMA = "tgw-w09-memory-bootstrap-frame/v1"
_SEAL = object()
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _memory_bootstrap(
    *, installer_sha256: str, transaction_sha256: str,
    helper_config_sha256: str, python_sha256: str, sudo_sha256: str,
    nix_system_path: str,
) -> str:
    """Return the reviewed isolated root bootstrap with controller-fixed digests."""
    expected = {
        "release_installer": installer_sha256,
        "application_transaction": transaction_sha256,
        "helper_config": helper_config_sha256,
    }
    return f'''import hashlib,json,pathlib,sys,types
def read_exact(size):
    out=bytearray()
    while len(out)<size:
        block=sys.stdin.buffer.read(size-len(out))
        if not block: raise SystemExit(65)
        out.extend(block)
    return bytes(out)
def digest(raw): return "sha256:"+hashlib.sha256(raw).hexdigest()
if digest(pathlib.Path("/proc/self/exe").read_bytes())!={python_sha256!r}: raise SystemExit(66)
if digest(pathlib.Path({REMOTE_SUDO!r}).resolve().read_bytes())!={sudo_sha256!r}: raise SystemExit(66)
if pathlib.Path("/run/current-system").resolve().as_posix()!={nix_system_path!r}: raise SystemExit(66)
header_size=int.from_bytes(read_exact(8),"big")
if header_size<1 or header_size>1048576: raise SystemExit(67)
header=json.loads(read_exact(header_size))
unsigned=dict(header); claimed=unsigned.pop("frame_hash",None)
canonical=json.dumps(unsigned,sort_keys=True,separators=(",",":")).encode()
if set(header)!=set(("schema","request","blobs","frame_hash")) or header.get("schema")!={FRAME_SCHEMA!r} or claimed!=digest(canonical): raise SystemExit(68)
specs=header.get("blobs")
names=("release_installer","application_transaction","helper_config","candidate_archive","runtime_config")
if not isinstance(specs,list) or tuple(item.get("name") for item in specs if isinstance(item,dict))!=names: raise SystemExit(69)
blobs={{}}
total=0
for item in specs:
    if set(item)!=set(("name","size","sha256")) or isinstance(item["size"],bool) or not isinstance(item["size"],int) or item["size"]<0 or item["size"]>201326592: raise SystemExit(70)
    total+=item["size"]
    if total>201326592: raise SystemExit(70)
    raw=read_exact(item["size"])
    if digest(raw)!=item["sha256"]: raise SystemExit(71)
    blobs[item["name"]]=raw
if sys.stdin.buffer.read(1): raise SystemExit(72)
expected={expected!r}
if any(digest(blobs[name])!=value for name,value in expected.items()): raise SystemExit(73)
package=types.ModuleType("tgw"); package.__path__=[]; sys.modules["tgw"]=package
installer=types.ModuleType("tgw.release_installer"); installer.__file__="memory:release_installer"; sys.modules[installer.__name__]=installer
exec(compile(blobs["release_installer"],installer.__file__,"exec"),installer.__dict__)
transaction=types.ModuleType("tgw.application_release_remote"); transaction.__file__="memory:application_transaction"; sys.modules[transaction.__name__]=transaction
exec(compile(blobs["application_transaction"],transaction.__file__,"exec"),transaction.__dict__)
raise SystemExit(transaction.memory_main(header["request"],blobs["helper_config"],blobs["candidate_archive"],blobs["runtime_config"],{transaction_sha256!r}))
'''


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


def _protected_held(path: Path, digest: str, *, executable: bool) -> tuple[int, bytes]:
    absolute = path.absolute()
    for ancestor in (absolute.parent, *absolute.parents):
        metadata = os.lstat(ancestor)
        if (
            not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ApplicationReleaseProviderError("provider artifact ancestor is not root-protected")
        if ancestor == Path("/"):
            break
    return _held_regular(path, digest, executable=executable)


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
    if target != {"host": "tgw-prod", "address": "100.107.99.66", "port": 22, "user": "db"}:
        raise ApplicationReleaseProviderError("provider target is not exact tgw-prod")
    transport = _exact(descriptor["transport"], {
        "ssh_path", "ssh_sha256", "ssh_keygen_path", "ssh_keygen_sha256",
        "known_hosts_path", "known_hosts_sha256",
        "identity_path", "identity_sha256", "transaction_source_path", "transaction_source_sha256",
        "installer_source_path", "installer_source_sha256",
    }, "provider transport")
    for name in ("ssh_path", "ssh_keygen_path", "known_hosts_path", "identity_path", "transaction_source_path", "installer_source_path"):
        if not isinstance(transport[name], str) or not transport[name].startswith("/"):
            raise ApplicationReleaseProviderError("provider transport path is invalid")
    for name in ("ssh_sha256", "ssh_keygen_sha256", "known_hosts_sha256", "identity_sha256", "transaction_source_sha256", "installer_source_sha256"):
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
        "python_path", "python_sha256", "sudo_path", "sudo_sha256", "bootstrap_sha256", "config_path", "config_sha256",
        "authorized_public_key", "nix_system_path",
    }, "provider remote boundary")
    if boundary["python_path"] != REMOTE_PYTHON or boundary["sudo_path"] != REMOTE_SUDO or not str(boundary["config_path"]).startswith("/"):
        raise ApplicationReleaseProviderError("provider in-memory sudo boundary is invalid")
    if any(_SHA.fullmatch(str(boundary[name])) is None for name in ("python_sha256", "sudo_sha256", "bootstrap_sha256", "config_sha256")):
        raise ApplicationReleaseProviderError("provider remote boundary hash is invalid")
    if not str(boundary["nix_system_path"]).startswith("/nix/store/"):
        raise ApplicationReleaseProviderError("provider observed Nix prerequisite is invalid")
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

    __slots__ = ("_descriptor", "_fds", "_raw", "_identities", "_prerequisite", "_frozen")

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
            (Path(transport["transaction_source_path"]), transport["transaction_source_sha256"], False, {0o400, 0o444}, 0),
            (Path(transport["installer_source_path"]), transport["installer_source_sha256"], False, {0o400, 0o444}, 0),
            (Path(candidate["archive_path"]), candidate["archive_sha256"], False, {0o400, 0o440, 0o444}, os.getuid()),
            (Path(runtime["path"]), runtime["content_sha256"], False, {0o400, 0o440, 0o444}, os.getuid()),
            (Path(self.descriptor["remote_boundary"]["config_path"]), self.descriptor["remote_boundary"]["config_sha256"], False, {0o400, 0o444}, 0),
            (Path(self.descriptor["prerequisite_receipt"]["path"]), self.descriptor["prerequisite_receipt"]["sha256"], False, {0o400, 0o444}, 0),
            (Path(transport["ssh_keygen_path"]), transport["ssh_keygen_sha256"], True, {0o555, 0o755}, 0),
        )
        descriptor_path, descriptor_fd, descriptor_raw, descriptor_identity = _descriptor_artifact
        fds: list[int] = []
        raw_values: list[bytes] = []
        identities = []
        try:
            for path, digest, executable, modes, uid in specifications:
                fd, raw = _protected_held(path, digest, executable=executable)
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
        if (
            _hash_bytes(raw_values[3]) != transport["transaction_source_sha256"]
            or _hash_bytes(raw_values[4]) != transport["installer_source_sha256"]
        ):
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
            prerequisite = json.loads(raw_values[8])
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider prerequisite receipt is invalid") from exc
        prerequisite = _exact(prerequisite, {
            "schema", "receipt_id", "target_host", "observed_at", "expires_at",
            "python_sha256", "sudo_sha256", "nix_system_path", "predecessor_observation_hash",
            "sudo_db_root_nopasswd", "authorized_public_key_sha256", "verified", "receipt_hash",
        }, "provider prerequisite receipt")
        prerequisite_unsigned = dict(prerequisite)
        prerequisite_claimed = prerequisite_unsigned.pop("receipt_hash")
        if (
            prerequisite["schema"] != "tgw-w09-db-memory-bootstrap-observation/v1"
            or prerequisite["receipt_id"] != self.descriptor["prerequisite_receipt"]["ref"]
            or prerequisite["target_host"] != "tgw-prod"
            or prerequisite["python_sha256"] != self.descriptor["remote_boundary"]["python_sha256"]
            or prerequisite["sudo_sha256"] != self.descriptor["remote_boundary"]["sudo_sha256"]
            or prerequisite["nix_system_path"] != self.descriptor["remote_boundary"]["nix_system_path"]
            or prerequisite["sudo_db_root_nopasswd"] is not True
            or prerequisite["authorized_public_key_sha256"] != _hash_bytes(
                (self.descriptor["remote_boundary"]["authorized_public_key"] + "\n").encode()
            )
            or prerequisite["verified"] is not True
            or prerequisite_claimed != _hash(prerequisite_unsigned)
        ):
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider prerequisite receipt binding differs")
        try:
            observed_at = datetime.fromisoformat(str(prerequisite["observed_at"]).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(prerequisite["expires_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider prerequisite freshness is invalid") from exc
        current = datetime.now(timezone.utc)
        if observed_at.tzinfo is None or expires_at.tzinfo is None or not observed_at <= current <= expires_at:
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("provider prerequisite observation is stale")
        bootstrap = _memory_bootstrap(
            installer_sha256=transport["installer_source_sha256"],
            transaction_sha256=transport["transaction_source_sha256"],
            helper_config_sha256=self.descriptor["remote_boundary"]["config_sha256"],
            python_sha256=self.descriptor["remote_boundary"]["python_sha256"],
            sudo_sha256=self.descriptor["remote_boundary"]["sudo_sha256"],
            nix_system_path=self.descriptor["remote_boundary"]["nix_system_path"],
        )
        if _hash_bytes(bootstrap.encode()) != self.descriptor["remote_boundary"]["bootstrap_sha256"]:
            for fd in reversed(fds): os.close(fd)
            raise ApplicationReleaseProviderError("reviewed in-memory bootstrap binding differs")
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
                        f"/proc/{os.getpid()}/fd/{fds[9]}", "-y", "-f",
                        f"/proc/{os.getpid()}/fd/{sealed_private}",
                    ],
                    pass_fds=(fds[9], sealed_private), timeout=10, limit=16 * 1024,
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
        self._prerequisite = _freeze(prerequisite)
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
            or parameters.get("nix_system_path") != self._prerequisite["nix_system_path"]
            or parameters.get("predecessor_observation_hash") != self._prerequisite["predecessor_observation_hash"]
            or parameters.get("provider_observation_ref") != self.descriptor["prerequisite_receipt"]["ref"]
            or parameters.get("provider_observation_hash") != self.descriptor["prerequisite_receipt"]["sha256"]
            or _hash(parameters) != candidate["effect_parameters_sha256"]
        ):
            raise ApplicationReleaseProviderError("effect parameters differ from mounted provider artifacts")

    def _packet(self, action: str, parameters: Mapping[str, Any]) -> bytes:
        self._check_parameters(parameters)
        request_unsigned = {
            "schema": FRAMED_SCHEMA, "action": action, "parameters": dict(parameters),
        }
        request = {**request_unsigned, "request_hash": _hash(request_unsigned)}
        bodies = (
            ("release_installer", self._raw[4]),
            ("application_transaction", self._raw[3]),
            ("helper_config", self._raw[7]),
            ("candidate_archive", self._raw[5] if action == "install" else b""),
            ("runtime_config", self._raw[6] if action == "install" else b""),
        )
        frame_unsigned = {
            "schema": FRAME_SCHEMA,
            "request": request,
            "blobs": [
                {"name": name, "size": len(raw), "sha256": _hash_bytes(raw)}
                for name, raw in bodies
            ],
        }
        header = _canonical({**frame_unsigned, "frame_hash": _hash(frame_unsigned)})
        return len(header).to_bytes(8, "big") + header + b"".join(raw for _name, raw in bodies)

    def _dispatch(self, action: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
        try:
            observed_at = datetime.fromisoformat(str(self._prerequisite["observed_at"]).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(self._prerequisite["expires_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApplicationReleaseProviderError("mounted provider freshness is invalid") from exc
        current = datetime.now(timezone.utc)
        if not observed_at <= current <= expires_at:
            raise ApplicationReleaseProviderError("mounted provider observation expired before dispatch")
        packet = self._packet(action, parameters)
        bounds = self.descriptor["bounds"]
        if len(packet) > int(bounds["max_packet_bytes"]):
            raise ApplicationReleaseProviderError("application release packet exceeds fixed bound")
        ssh_fd = self._fds[0]
        transport = self.descriptor["transport"]
        boundary = self.descriptor["remote_boundary"]
        bootstrap = _memory_bootstrap(
            installer_sha256=transport["installer_source_sha256"],
            transaction_sha256=transport["transaction_source_sha256"],
            helper_config_sha256=boundary["config_sha256"],
            python_sha256=boundary["python_sha256"],
            sudo_sha256=boundary["sudo_sha256"],
            nix_system_path=boundary["nix_system_path"],
        )
        remote_command = shlex.join([
            REMOTE_SUDO, "-n", "--", REMOTE_PYTHON, "-I", "-S", "-c", bootstrap,
        ])
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
                "db@100.107.99.66", remote_command,
            ]
            try:
                process = subprocess.Popen(
                    argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    start_new_session=True, pass_fds=(ssh_fd, sealed_hosts, sealed_identity),
                    env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
                )
            except OSError as exc:
                raise BootstrapStateAmbiguous(
                    "application release dispatch failed before remote launch",
                    evidence=("application-release-prelaunch:" + _hash_bytes(str(exc).encode()),),
                    rollback_required=False,
                ) from exc
            stream_error: Exception | None = None
            try:
                stdout, stderr = _bounded_stream(
                    process, packet, stdout_limit=int(bounds["max_output_bytes"]),
                    stderr_limit=int(bounds["max_diagnostic_bytes"]), timeout=int(bounds["timeout_seconds"]),
                )
            except Exception as exc:
                stream_error = exc; stdout = b""; stderr = str(exc).encode()
            state = _group_empty_or_kill(process.pid)
            try: process.wait(timeout=1); state["reaped"] = True
            except subprocess.TimeoutExpired: state["reaped"] = False
            state = _post_reap_group_state(process.pid, state)
        finally:
            if sealed_identity >= 0:
                os.close(sealed_identity)
            os.close(sealed_hosts)
        if stream_error is not None or process.returncode != 0 or state.get("had_survivor") or not state.get("removed") or not state.get("reaped"):
            raise BootstrapStateAmbiguous(
                "application release SSH lifecycle is ambiguous",
                evidence=(
                    "application-release-transport-stdout:" + _hash_bytes(stdout),
                    "application-release-transport-stderr:" + _hash_bytes(stderr),
                ),
                rollback_required=action == "install",
            )
        for (path, identity), fd, raw in zip(self._identities, self._fds, self._raw, strict=True):
            if _inode_identity(os.fstat(fd)) != identity or _hash_bytes(os.pread(fd, len(raw) + 1, 0)) != _hash_bytes(raw) or _inode_identity(os.stat(path, follow_symlinks=False)) != identity:
                raise BootstrapStateAmbiguous("application release provider artifact changed", evidence=("application-release-artifact:changed",), rollback_required=action == "install")
        try:
            response = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BootstrapStateAmbiguous("application release response is invalid", evidence=("application-release-response:" + _hash_bytes(stdout),), rollback_required=action == "install") from exc
        if not isinstance(response, dict):
            raise BootstrapStateAmbiguous("application release response is not an object", evidence=("application-release-response:" + _hash_bytes(stdout),), rollback_required=action == "install")
        unsigned = dict(response); claimed = unsigned.pop("receipt_sha256", None)
        boundary = self.descriptor["remote_boundary"]
        if (
            response.get("schema") != RESPONSE_SCHEMA or claimed != _hash(unsigned)
            or response.get("operation_id") != parameters["operation_id"]
            or response.get("helper_sha256") != transport["transaction_source_sha256"]
            or response.get("helper_config_sha256") != boundary["config_sha256"]
            or response.get("nix_system_path") != parameters["nix_system_path"]
        ):
            raise BootstrapStateAmbiguous("application release response binding differs", evidence=("application-release-response:" + _hash_bytes(stdout),), rollback_required=action == "install")
        try:
            _validate_response_shape(response)
        except ApplicationReleaseProviderError as exc:
            raise BootstrapStateAmbiguous(
                "application release response evidence differs",
                evidence=("application-release-response:" + _hash_bytes(stdout),),
                rollback_required=action == "install",
            ) from exc
        return response

    def install(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        result = self._dispatch("install", parameters)
        if result.get("status") == "SUCCEEDED" and isinstance(result.get("evidence"), list) and result["evidence"]:
            return {"evidence": list(result["evidence"]) + ["application-release:" + result["receipt_sha256"]]}
        if result.get("status") == "AMBIGUOUS":
            raise BootstrapStateAmbiguous(
                "remote application deployment is ambiguous",
                evidence=tuple(result.get("evidence", ())), rollback_required=True,
            )
        if (
            result.get("status") == "RESTORED"
            and result.get("predecessor_healthy") is True
            and isinstance(result.get("receipt"), str)
        ):
            return {
                "terminal_outcome": "rolled_back",
                "rollback_receipt": result["receipt"],
                "evidence": list(result.get("evidence", ())),
            }
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
