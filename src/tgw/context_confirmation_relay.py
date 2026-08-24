"""Credential-free Unix-socket relay for same-client Context confirmation.

Ordinary MCP children can submit their already protected ``context_status``
object through a local socket.  The root relay binds that object to Unix peer
credentials and is the only process that receives the actor-fleet bearer.  It
has one allowlisted provider operation and cannot relay a command or path.
"""

from __future__ import annotations

import grp
import hashlib
import json
import os
import pwd
import re
import secrets
import select
import signal
import socket
import stat
import struct
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TRANSACTION = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_MAX_REQUEST = 4 * 1024 * 1024
_MAX_RESPONSE = 2 * 1024 * 1024
_SOCKET = Path("/run/tgw/context-confirmation.sock")
_ALLOWED_ENDPOINTS = {
    "http://100.68.223.70:7556",
    "http://127.0.0.1:7556",
    "http://[::1]:7556",
}
_CONTEXT_ENVIRONMENT_KEYS = {
    "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0", "HOME",
    "LANG", "PATH", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE",
    "PYTHONSAFEPATH", "TGW_CONTEXT_ACTOR", "TGW_CONTEXT_ENDPOINT",
    "TGW_CONTEXT_ENVIRONMENT_CATALOG", "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH",
    "TGW_CONTEXT_FLEET_CONVERGENCE", "TGW_CONTEXT_GENERATION",
    "TGW_CONTEXT_PLAN_COMMIT", "TGW_CONTEXT_PLAN_REPOSITORY",
    "TGW_CONTEXT_PLAN_ROOT", "TGW_CONTEXT_PLAN_SOLUTION",
    "TGW_CONTEXT_PROFILE", "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE",
    "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256", "TGW_CONTEXT_RUNTIME_ENTRYPOINT",
    "TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256", "TGW_CONTEXT_RUNTIME_EXECUTABLE",
    "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE", "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE",
    "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256", "TGW_CONTEXT_RUNTIME_MODULE",
    "TGW_CONTEXT_RUNTIME_MODULE_SHA256", "TGW_CONTEXT_RUNTIME_ROOT",
    "TGW_CONTEXT_SOURCE_COMMIT", "TGW_CONTEXT_SOURCE_ROOT",
    "TGW_CONTEXT_SOURCE_TREE", "TGW_CONTEXT_STABLE_LAUNCHER",
    "TGW_CONTEXT_STABLE_LAUNCHER_SHA256", "TGW_CONTEXT_STARTUP_BINDING", "TMPDIR",
}


class ContextConfirmationRelayError(ValueError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _hashed(value: Mapping[str, Any], field: str, label: str) -> str:
    unsigned = dict(value)
    claimed = unsigned.pop(field, None)
    if not isinstance(claimed, str) or claimed != _hash(unsigned):
        raise ContextConfirmationRelayError(f"{label} hash differs")
    return claimed


def _peer_confirmation(
    confirmation: Mapping[str, Any], *, peer_pid: int, peer_uid: int,
) -> dict[str, Any]:
    required = {
        "schema", "transaction_id", "direction", "obligation_id", "status",
    }
    if (
        not isinstance(confirmation, Mapping)
        or set(confirmation) != required
        or confirmation.get("schema") != "tgw-context-client-confirmation/v1"
        or _TRANSACTION.fullmatch(str(confirmation.get("transaction_id", ""))) is None
        or confirmation.get("direction") not in {"successor", "rollback"}
        or _HASH.fullmatch(str(confirmation.get("obligation_id", ""))) is None
        or not isinstance(confirmation.get("status"), Mapping)
    ):
        raise ContextConfirmationRelayError("Context confirmation is invalid")
    status = dict(confirmation["status"])
    if status.get("schema") != "tgw-context-service/v1":
        raise ContextConfirmationRelayError("Context status schema is invalid")
    _hashed(status, "context_sha256", "Context status")
    runtime = status.get("runtime")
    process = runtime.get("process") if isinstance(runtime, Mapping) else None
    startup = status.get("startup")
    fleet = status.get("fleet_convergence")
    transaction = fleet.get("transaction") if isinstance(fleet, Mapping) else None
    if (
        not isinstance(process, Mapping)
        or not isinstance(startup, Mapping)
        or not isinstance(transaction, Mapping)
    ):
        raise ContextConfirmationRelayError("Context status identity is incomplete")
    _hashed(process, "identity_hash", "Context process identity")
    observed_process = _process_identity(peer_pid)
    environment = _process_environment(peer_pid)
    actor = startup.get("actor")
    try:
        actor_uid = pwd.getpwnam(str(actor)).pw_uid
    except KeyError as exc:
        raise ContextConfirmationRelayError("Context actor is unknown") from exc
    if (
        dict(process) != observed_process
        or process.get("pid") != peer_pid
        or process.get("uid") != peer_uid
        or actor_uid != peer_uid
        or transaction.get("transaction_id") != confirmation["transaction_id"]
        or transaction.get("direction") != confirmation["direction"]
    ):
        raise ContextConfirmationRelayError("Context confirmation peer differs")
    _verify_closed_environment(
        environment, actor=str(actor), status=status, runtime=runtime,
    )
    matches = [
        item for item in transaction.get("obligations", [])
        if isinstance(item, Mapping)
        and item.get("obligation_id") == confirmation["obligation_id"]
        and item.get("actor") == actor
    ]
    if len(matches) != 1:
        raise ContextConfirmationRelayError("Context confirmation obligation differs")
    return dict(confirmation)


def _bounded_proc(path: Path, maximum: int) -> bytes:
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise ContextConfirmationRelayError("Context process record exceeds its bound")
    return raw


def _process_environment(pid: int) -> dict[str, str]:
    try:
        rows = _bounded_proc(Path("/proc") / str(pid) / "environ", 1024 * 1024)
        result: dict[str, str] = {}
        for row in rows.split(b"\0"):
            if not row:
                continue
            name, raw = row.split(b"=", 1)
            decoded = name.decode("ascii")
            if decoded in result:
                raise ContextConfirmationRelayError("Context process environment is duplicated")
            result[decoded] = raw.decode("utf-8")
        return result
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ContextConfirmationRelayError("Context process environment is unavailable") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _verify_closed_environment(
    environment: Mapping[str, str], *, actor: str, status: Mapping[str, Any],
    runtime: Mapping[str, Any],
) -> None:
    if set(environment) != _CONTEXT_ENVIRONMENT_KEYS:
        raise ContextConfirmationRelayError("Context process environment is not closed")
    startup = status["startup"]
    source = status.get("source")
    catalog = status.get("environment")
    runtime_executable = str(runtime.get("executable"))
    environment_executable = environment.get(
        "TGW_CONTEXT_RUNTIME_EXECUTABLE", ""
    )
    try:
        executable_matches = (
            bool(environment_executable)
            and Path(environment_executable).is_absolute()
            and Path(environment_executable).resolve(strict=True)
            == Path(runtime_executable).resolve(strict=True)
        )
    except OSError:
        executable_matches = False
    if not executable_matches:
        raise ContextConfirmationRelayError(
            "Context process executable binding differs"
        )
    expected = {
        "TGW_CONTEXT_ACTOR": actor,
        "TGW_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_CONTEXT_GENERATION": str(startup.get("generation")),
        "TGW_CONTEXT_STARTUP_BINDING": str(startup.get("binding_path")),
        "TGW_CONTEXT_SOURCE_COMMIT": str(source.get("commit")) if isinstance(source, Mapping) else "",
        "TGW_CONTEXT_SOURCE_TREE": str(source.get("tree")) if isinstance(source, Mapping) else "",
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": str(catalog.get("catalog_hash")) if isinstance(catalog, Mapping) else "",
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT": str(runtime.get("entrypoint")),
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256": str(runtime.get("entrypoint_sha256")),
        "TGW_CONTEXT_RUNTIME_MODULE": str(runtime.get("startup_module")),
        "TGW_CONTEXT_RUNTIME_MODULE_SHA256": str(runtime.get("startup_module_sha256")),
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE": str(runtime.get("context_module")),
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256": str(runtime.get("context_module_sha256")),
        "TGW_CONTEXT_STABLE_LAUNCHER": str(runtime.get("stable_launcher")),
        "TGW_CONTEXT_STABLE_LAUNCHER_SHA256": str(runtime.get("stable_launcher_sha256")),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE": environment_executable,
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256": str(runtime.get("executable_sha256")),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE": str(runtime.get("executable_device")),
        "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE": str(runtime.get("executable_inode")),
    }
    if any(environment.get(name) != value for name, value in expected.items()):
        raise ContextConfirmationRelayError("Context process environment binding differs")
    if environment["HOME"] != f"/home/{actor}" or environment["TMPDIR"].startswith("/tmp"):
        raise ContextConfirmationRelayError("Context process runtime roots are unsafe")
    for path_name, hash_name in (
        ("TGW_CONTEXT_RUNTIME_ENTRYPOINT", "TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256"),
        ("TGW_CONTEXT_RUNTIME_MODULE", "TGW_CONTEXT_RUNTIME_MODULE_SHA256"),
        ("TGW_CONTEXT_RUNTIME_CONTEXT_MODULE", "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256"),
        ("TGW_CONTEXT_STABLE_LAUNCHER", "TGW_CONTEXT_STABLE_LAUNCHER_SHA256"),
        ("TGW_CONTEXT_RUNTIME_EXECUTABLE", "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256"),
    ):
        path = Path(environment[path_name])
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise ContextConfirmationRelayError("Context runtime file is unavailable") from exc
        if not target.is_file() or _file_sha256(target) != environment[hash_name]:
            raise ContextConfirmationRelayError("Context runtime file hash differs")


def _process_identity(pid: int) -> dict[str, Any]:
    """Capture the same exact process identity projected by Context status."""
    root = Path("/proc") / str(pid)
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8",
        ).strip()
        status_rows = _bounded_proc(root / "status", 64 * 1024).decode().splitlines()
        status = {
            row.split(":", 1)[0]: row.split(":", 1)[1].strip()
            for row in status_rows if ":" in row
        }
        raw_stat = _bounded_proc(root / "stat", 64 * 1024).decode()
        arguments = [
            value.decode("utf-8", errors="replace")
            for value in _bounded_proc(root / "cmdline", 256 * 1024).split(b"\0")
            if value
        ]
        executable_link = root / "exe"
        before = executable_link.stat()
        executable = executable_link.resolve(strict=True)
        digest = hashlib.sha256()
        with executable_link.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        after = executable_link.stat()
    except (OSError, KeyError, UnicodeDecodeError, ValueError) as exc:
        raise ContextConfirmationRelayError("Context process identity is unavailable") from exc
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ContextConfirmationRelayError("Context process identity changed")
    shape = [Path(arguments[0]).name if arguments else ""]
    shape.extend(
        item for item in arguments[1:]
        if item.startswith("--") or item in {"-m", "tgw.context_mcp_server"}
    )
    value = {
        "boot_id": boot_id,
        "pid": pid,
        "start_ticks": int(raw_stat.rsplit(") ", 1)[1].split()[19]),
        "uid": int(status["Uid"].split()[0]),
        "ppid": int(status.get("PPid", "0")),
        "executable_path": str(executable),
        "executable_device": before.st_dev,
        "executable_inode": before.st_ino,
        "executable_sha256": "sha256:" + digest.hexdigest(),
        "cmdline_shape": shape,
        "cmdline_sha256": _hash(arguments),
    }
    return {**value, "identity_hash": _hash(value)}


class ActorFleetConfirmationClient:
    """One-operation HTTP client which never returns or logs its credential."""

    def __init__(
        self, *, endpoint: str, token: str, timeout: float = 30,
        opener: Any | None = None,
    ) -> None:
        parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
        if (
            endpoint.rstrip("/") not in _ALLOWED_ENDPOINTS
            or parsed is None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ContextConfirmationRelayError("actor fleet endpoint is not allowlisted")
        if not isinstance(token, str) or not token or any(character.isspace() for character in token):
            raise ContextConfirmationRelayError("actor fleet relay credential is unavailable")
        self.endpoint = endpoint.rstrip("/")
        self._token = token
        self.timeout = timeout
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())

    def confirm(self, confirmation: Mapping[str, Any]) -> dict[str, Any]:
        invocation = {
            "schema": "tgw-actor-fleet-provider-invocation/v1",
            "step": "confirm-context-rebind",
            "arguments": [dict(confirmation)],
        }
        body = {**invocation, "invocation_hash": _hash(invocation)}
        request = Request(
            self.endpoint + "/v1/actor-fleet/confirm-context-rebind",
            data=_canonical(body), method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self._token,
            },
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(_MAX_RESPONSE + 1)
                status = response.status
        except (HTTPError, URLError, OSError) as exc:
            raise ContextConfirmationRelayError("actor fleet confirmation failed") from exc
        if status != 200 or len(raw) > _MAX_RESPONSE:
            raise ContextConfirmationRelayError("actor fleet confirmation response is invalid")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextConfirmationRelayError("actor fleet confirmation response is invalid") from exc
        if (
            not isinstance(value, Mapping)
            or value.get("schema") != "tgw-actor-fleet-provider-response/v1"
            or value.get("step") != "confirm-context-rebind"
            or value.get("invocation_hash") != body["invocation_hash"]
            or not isinstance(value.get("result"), Mapping)
        ):
            raise ContextConfirmationRelayError("actor fleet confirmation response differs")
        result = dict(value["result"])
        status_value = result.get("status")
        if (
            status_value not in {"CONFIRMED", "RETRY_REQUIRED"}
            or result.get("transaction_id") != confirmation.get("transaction_id")
            or (
                status_value == "CONFIRMED"
                and result.get("obligation_id") != confirmation.get("obligation_id")
            )
            or (
                status_value == "RETRY_REQUIRED"
                and (
                    result.get("previous_obligation_id")
                    != confirmation.get("obligation_id")
                    or _HASH.fullmatch(str(result.get("obligation_id", ""))) is None
                    or result.get("obligation_id") == confirmation.get("obligation_id")
                )
            )
        ):
            raise ContextConfirmationRelayError("actor fleet did not confirm this Context client")
        return result


def _relay_response(*, result: Mapping[str, Any] | None = None, error: str | None = None) -> bytes:
    result_status = result.get("status") if isinstance(result, Mapping) else None
    body: dict[str, Any] = {
        "schema": "tgw-context-confirmation-relay-response/v1",
        "status": (
            str(result_status)
            if error is None and result_status in {"CONFIRMED", "RETRY_REQUIRED"}
            else "HOLD"
        ),
    }
    if error is None:
        body["result"] = dict(result or {})
    else:
        body["error"] = error
    return _canonical({**body, "response_sha256": _hash(body)}) + b"\n"


def _line(connection: socket.socket, maximum: int) -> bytes:
    raw = bytearray()
    while b"\n" not in raw and len(raw) <= maximum:
        chunk = connection.recv(min(65536, maximum + 1 - len(raw)))
        if not chunk:
            break
        raw.extend(chunk)
    if len(raw) > maximum or b"\n" not in raw:
        raise ContextConfirmationRelayError("Context confirmation socket frame is invalid")
    return bytes(raw).split(b"\n", 1)[0]


def _challenge_request(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextConfirmationRelayError("Context confirmation challenge is invalid") from exc
    if not isinstance(value, Mapping):
        raise ContextConfirmationRelayError("Context confirmation challenge is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("request_sha256", None)
    if (
        set(value) != {
            "schema", "transaction_id", "direction", "obligation_id",
            "request_sha256",
        }
        or value.get("schema") != "tgw-context-confirmation-challenge-request/v1"
        or _TRANSACTION.fullmatch(str(value.get("transaction_id", ""))) is None
        or value.get("direction") not in {"successor", "rollback"}
        or _HASH.fullmatch(str(value.get("obligation_id", ""))) is None
        or claimed != _hash(unsigned)
    ):
        raise ContextConfirmationRelayError("Context confirmation challenge differs")
    return dict(value)


def _challenge_response(nonce: str, process: Mapping[str, Any]) -> tuple[bytes, str]:
    body = {
        "schema": "tgw-context-confirmation-challenge/v1",
        "nonce": nonce,
        "peer_process_identity_hash": process["identity_hash"],
    }
    challenge_hash = _hash(body)
    return _canonical({**body, "challenge_sha256": challenge_hash}) + b"\n", challenge_hash


def _confirmed_request(
    raw: bytes, *, nonce: str, challenge_hash: str,
    challenge: Mapping[str, Any], peer_pid: int, peer_uid: int,
) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextConfirmationRelayError("Context confirmation response is invalid") from exc
    if not isinstance(value, Mapping):
        raise ContextConfirmationRelayError("Context confirmation response is invalid")
    unsigned = dict(value)
    claimed = unsigned.pop("request_sha256", None)
    if (
        set(value) != {
            "schema", "nonce", "challenge_sha256", "confirmation",
            "request_sha256",
        }
        or value.get("schema") != "tgw-context-confirmation-challenge-response/v1"
        or not secrets.compare_digest(str(value.get("nonce", "")), nonce)
        or value.get("challenge_sha256") != challenge_hash
        or claimed != _hash(unsigned)
    ):
        raise ContextConfirmationRelayError("Context confirmation challenge response differs")
    confirmation = _peer_confirmation(
        value["confirmation"], peer_pid=peer_pid, peer_uid=peer_uid,
    )
    if any(
        confirmation[name] != challenge[name]
        for name in ("transaction_id", "direction", "obligation_id")
    ):
        raise ContextConfirmationRelayError("Context confirmation changed after challenge")
    return confirmation


def submit_confirmation(
    confirmation: Mapping[str, Any], *, socket_path: Path = _SOCKET,
    timeout: float = 30,
) -> dict[str, Any]:
    """Submit from the MCP child without reading a provider credential."""
    if not socket_path.is_absolute() or socket_path == Path("/tmp") or Path("/tmp") in socket_path.parents:
        raise ContextConfirmationRelayError("Context confirmation socket is unsafe")
    challenge_body = {
        "schema": "tgw-context-confirmation-challenge-request/v1",
        "transaction_id": confirmation.get("transaction_id"),
        "direction": confirmation.get("direction"),
        "obligation_id": confirmation.get("obligation_id"),
    }
    challenge_request = _canonical(
        {**challenge_body, "request_sha256": _hash(challenge_body)},
    ) + b"\n"
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
        client.sendall(challenge_request)
        raw_challenge = _line(client, _MAX_RESPONSE)
        try:
            challenge = json.loads(raw_challenge)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextConfirmationRelayError("Context confirmation challenge is invalid") from exc
        if not isinstance(challenge, Mapping):
            raise ContextConfirmationRelayError("Context confirmation challenge is invalid")
        challenge_unsigned = dict(challenge)
        challenge_hash = challenge_unsigned.pop("challenge_sha256", None)
        if (
            challenge.get("schema") != "tgw-context-confirmation-challenge/v1"
            or challenge_hash != _hash(challenge_unsigned)
            or not isinstance(challenge.get("nonce"), str)
            or len(challenge["nonce"]) != 64
            or _HASH.fullmatch(str(challenge.get("peer_process_identity_hash", ""))) is None
        ):
            raise ContextConfirmationRelayError("Context confirmation challenge differs")
        response_body = {
            "schema": "tgw-context-confirmation-challenge-response/v1",
            "nonce": challenge["nonce"],
            "challenge_sha256": challenge_hash,
            "confirmation": dict(confirmation),
        }
        client.sendall(
            _canonical({**response_body, "request_sha256": _hash(response_body)})
            + b"\n"
        )
        raw_response = _line(client, _MAX_RESPONSE)
    except OSError as exc:
        raise ContextConfirmationRelayError("Context confirmation relay is unavailable") from exc
    finally:
        client.close()
    try:
        response = json.loads(raw_response)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContextConfirmationRelayError("Context confirmation relay response is invalid") from exc
    if not isinstance(response, Mapping):
        raise ContextConfirmationRelayError("Context confirmation relay response is invalid")
    _hashed(response, "response_sha256", "Context confirmation relay response")
    if (
        response.get("schema") != "tgw-context-confirmation-relay-response/v1"
        or response.get("status") not in {"CONFIRMED", "RETRY_REQUIRED"}
        or not isinstance(response.get("result"), Mapping)
        or response["result"].get("status") != response.get("status")
    ):
        raise ContextConfirmationRelayError(str(response.get("error", "Context confirmation was held")))
    return dict(response["result"])


def serve(
    *, socket_path: Path = _SOCKET, endpoint: str | None = None,
    token: str | None = None,
) -> None:
    if os.geteuid() != 0:
        raise ContextConfirmationRelayError("Context confirmation relay requires root")
    endpoint = endpoint or os.environ.get(
        "TGW_ACTOR_FLEET_ENDPOINT", "http://100.68.223.70:7556",
    )
    token = token or os.environ.get("TGW_ACTOR_FLEET_TOKEN", "")
    provider = ActorFleetConfirmationClient(endpoint=endpoint, token=token)
    group = grp.getgrnam("tgw-coders")
    if os.getegid() != group.gr_gid:
        raise ContextConfirmationRelayError(
            "Context confirmation relay effective group differs"
        )
    socket_path.parent.mkdir(parents=True, mode=0o750, exist_ok=True)
    if socket_path.exists() or socket_path.is_symlink():
        if socket_path.is_socket() and socket_path.stat().st_uid == 0:
            socket_path.unlink()
        else:
            raise ContextConfirmationRelayError("Context confirmation socket path is occupied")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    bound = socket_path.stat(follow_symlinks=False)
    if (
        socket_path.is_symlink()
        or not stat.S_ISSOCK(bound.st_mode)
        or bound.st_uid != 0
        or bound.st_gid != group.gr_gid
    ):
        server.close()
        socket_path.unlink(missing_ok=True)
        raise ContextConfirmationRelayError(
            "Context confirmation socket ownership differs"
        )
    os.chmod(socket_path, 0o660)
    server.listen(32)
    stopping = False

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        server.close()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopping:
            try:
                connection, _ = server.accept()
            except OSError:
                if stopping:
                    break
                raise
            with connection:
                peer_pid, peer_uid, _peer_gid = struct.unpack(
                    "3i", connection.getsockopt(
                        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"),
                    ),
                )
                pidfd = None
                try:
                    if not hasattr(os, "pidfd_open"):
                        raise ContextConfirmationRelayError("pidfd verification is unavailable")
                    pidfd = os.pidfd_open(peer_pid, 0)
                    challenge = _challenge_request(_line(connection, _MAX_REQUEST))
                    process_before = _process_identity(peer_pid)
                    if process_before["uid"] != peer_uid:
                        raise ContextConfirmationRelayError("Context confirmation peer changed")
                    nonce = secrets.token_hex(32)
                    challenge_frame, challenge_hash = _challenge_response(
                        nonce, process_before,
                    )
                    connection.sendall(challenge_frame)
                    confirmation = _confirmed_request(
                        _line(connection, _MAX_REQUEST), nonce=nonce,
                        challenge_hash=challenge_hash, challenge=challenge,
                        peer_pid=peer_pid, peer_uid=peer_uid,
                    )
                    if _process_identity(peer_pid) != process_before:
                        raise ContextConfirmationRelayError("Context confirmation peer changed during challenge")
                    watcher = select.poll()
                    watcher.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
                    if watcher.poll(0):
                        raise ContextConfirmationRelayError("Context confirmation peer exited")
                    result = provider.confirm(confirmation)
                    if watcher.poll(0) or _process_identity(peer_pid) != process_before:
                        raise ContextConfirmationRelayError("Context confirmation peer changed during provider confirmation")
                    response = _relay_response(result=result)
                except (OSError, TypeError, ValueError, ContextConfirmationRelayError) as exc:
                    response = _relay_response(error=str(exc))
                finally:
                    if pidfd is not None:
                        os.close(pidfd)
                connection.sendall(response)
    finally:
        server.close()
        if socket_path.is_socket():
            socket_path.unlink()


def main() -> int:
    try:
        serve()
    except (OSError, KeyError, TypeError, ValueError, ContextConfirmationRelayError) as exc:
        print(json.dumps({"status": "HOLD", "error": str(exc)}, sort_keys=True))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
