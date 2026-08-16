"""Root-owned, no-argv forced-command helper for one W09 app transaction.

The SSH account cannot select commands.  One canonical JSON packet on stdin
drives this closed state machine; paths, services, probes and executables must
all equal the root-owned helper configuration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from tgw.a3_host_state_observation import (
    _group_empty_or_kill,
    _post_reap_group_state,
)
from tgw.release_installer import (
    current_generation,
    install_runtime_files,
    materialize,
    rollback,
    select,
    verify,
)

SCHEMA = "tgw-w09-application-release-request/v1"
CONFIG_SCHEMA = "tgw-w09-application-release-helper-config/v1"
RESPONSE_SCHEMA = "tgw-w09-application-release-response/v1"
CONFIG_PATH = Path("/etc/tgw/application-release-helper.json")
MAX_PACKET = 192 * 1024 * 1024
_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
MIGRATION_PATHS = (
    "src/tgw/plan_authority.sql",
    "src/tgw/queue/migrations/20260815_terminal_lease_expiry_fence.sql",
)
PROJECTION_PATH = "agent-services/plan-runtime/GOVERNED-EXECUTION-PLATFORM-f0a8cf22.json"


class ApplicationReleaseRemoteError(RuntimeError):
    pass


def _bounded_process(process: subprocess.Popen[bytes], stdin: bytes, *, limit: int, timeout: int) -> tuple[bytes, bytes]:
    selector = selectors.DefaultSelector()
    assert process.stdin and process.stdout and process.stderr
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}; offset = 0; deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(min(remaining, 0.25)):
                if key.data == "stdin":
                    if offset == len(stdin):
                        selector.unregister(key.fileobj); key.fileobj.close(); continue
                    try: offset += os.write(key.fd, stdin[offset:offset + 65536])
                    except BrokenPipeError: selector.unregister(key.fileobj); key.fileobj.close()
                else:
                    chunk = os.read(key.fd, 65536)
                    if not chunk: selector.unregister(key.fileobj); continue
                    outputs[key.data].extend(chunk)
                    if len(outputs[key.data]) > limit: raise ApplicationReleaseRemoteError(f"{key.data} exceeded fixed bound")
        process.wait(timeout=max(0.1, deadline - time.monotonic()))
        return bytes(outputs["stdout"]), bytes(outputs["stderr"])
    finally:
        selector.close()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed: stream.close()


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ApplicationReleaseRemoteError("application release value is not canonical JSON") from exc


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash(value: Any) -> str:
    return _hash_bytes(_canonical(value))


def _exact(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ApplicationReleaseRemoteError(f"{label} is invalid")
    return dict(value)


def _safe_root(path: Path, *, mode: int = 0o700) -> None:
    metadata = path.lstat()
    if not path.is_absolute() or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != mode:
        raise ApplicationReleaseRemoteError(f"protected root is unsafe: {path}")


def _read_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1 or stat.S_IMODE(metadata.st_mode) != 0o400:
        raise ApplicationReleaseRemoteError("helper config metadata is unsafe")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationReleaseRemoteError("helper config is unreadable") from exc
    config = _exact(value, {
        "schema", "target_host", "root_id", "release_root", "current_selector", "active_config_path",
        "services", "health_probes", "executables", "database", "receipt_root", "backup_root",
        "unrelated_paths", "max_archive_bytes", "max_config_bytes", "command_timeout_seconds",
        "helper_sha256", "config_sha256",
    }, "helper config")
    unsigned = dict(config); claimed = unsigned.pop("config_sha256")
    if claimed != _hash(unsigned) or config["schema"] != CONFIG_SCHEMA:
        raise ApplicationReleaseRemoteError("helper config self-hash/schema is invalid")
    module_path = Path(__file__)
    module_metadata = module_path.stat()
    if (
        not stat.S_ISREG(module_metadata.st_mode) or module_metadata.st_uid != 0
        or module_metadata.st_nlink != 1 or stat.S_IMODE(module_metadata.st_mode) & 0o022
        or _hash_bytes(module_path.read_bytes()) != config["helper_sha256"]
    ):
        raise ApplicationReleaseRemoteError("running helper module differs from reviewed source")
    if (
        config["target_host"] != "tgw-prod" or config["root_id"] != "production-releases"
        or config["release_root"] != "/opt/TGW" or config["current_selector"] != "/opt/TGW/current"
        or config["active_config_path"] != "/opt/TGW/config/tgw-api-config.json"
    ):
        raise ApplicationReleaseRemoteError("helper target paths are invalid")
    for name in ("services", "health_probes", "unrelated_paths"):
        if not isinstance(config[name], list) or not config[name] or config[name] != sorted(set(config[name])):
            raise ApplicationReleaseRemoteError(f"helper {name} are invalid")
    executables = _exact(config["executables"], {"systemctl", "pg_dump", "pg_restore", "psql"}, "helper executables")
    for binding in executables.values():
        item = _exact(binding, {"path", "sha256"}, "helper executable")
        if not str(item["path"]).startswith("/run/current-system/sw/bin/") or _SHA.fullmatch(str(item["sha256"])) is None:
            raise ApplicationReleaseRemoteError("helper executable binding is invalid")
    _exact(config["database"], {"name", "user", "host"}, "helper database")
    for name in ("receipt_root", "backup_root"):
        if not isinstance(config[name], str) or not config[name].startswith("/opt/TGW/var/"):
            raise ApplicationReleaseRemoteError("helper state root is invalid")
        _safe_root(Path(config[name]))
    if not 1 <= int(config["command_timeout_seconds"]) <= 900:
        raise ApplicationReleaseRemoteError("helper timeout is invalid")
    return config


def validate_request(value: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    request = _exact(value, {"schema", "action", "parameters", "archive_b64", "config_b64", "request_hash"}, "release request")
    unsigned = dict(request); claimed = unsigned.pop("request_hash")
    if request["schema"] != SCHEMA or claimed != _hash(unsigned) or request["action"] not in {"install", "rollback"}:
        raise ApplicationReleaseRemoteError("release request schema/hash/action is invalid")
    parameters = _exact(request["parameters"], {
        "generation", "candidate_commit", "candidate_tree", "archive_sha256", "artifact_ref", "root_id",
        "expected_current", "operation_id", "review_receipt", "controller_receipt", "migration_receipts",
        "projection", "runtime_config", "services", "health_probes", "nix_system_path",
        "predecessor_observation_ref", "predecessor_observation_hash", "immutable_generation_path",
        "predecessor",
    }, "release parameters")
    if (
        _GENERATION.fullmatch(str(parameters["generation"])) is None
        or _GENERATION.fullmatch(str(parameters["expected_current"])) is None
        or parameters["root_id"] != config["root_id"]
        or parameters["services"] != config["services"]
        or parameters["health_probes"] != config["health_probes"]
        or parameters["immutable_generation_path"] != f"/opt/TGW/releases/{parameters['generation']}"
    ):
        raise ApplicationReleaseRemoteError("release parameters differ from root-owned composition")
    predecessor = _exact(parameters["predecessor"], {
        "generation", "selector_target", "commit", "tree", "archive_sha256",
        "release_manifest_hash", "content_manifest_sha256", "projection_sha256",
        "runtime_config_sha256",
    }, "release predecessor")
    if (
        predecessor["generation"] != parameters["expected_current"]
        or predecessor["selector_target"] != f"/opt/TGW/releases/{parameters['expected_current']}"
        or not re.fullmatch(r"[0-9a-f]{40}", str(predecessor["commit"]))
        or not re.fullmatch(r"[0-9a-f]{40}", str(predecessor["tree"]))
        or any(_SHA.fullmatch(str(predecessor[name])) is None for name in (
            "archive_sha256", "release_manifest_hash", "content_manifest_sha256",
            "projection_sha256", "runtime_config_sha256",
        ))
    ):
        raise ApplicationReleaseRemoteError("release predecessor identity is invalid")
    projection = _exact(parameters["projection"], {"release_path", "content_sha256"}, "release projection")
    runtime_binding = _exact(parameters["runtime_config"], {
        "artifact_ref", "generation_path", "content_sha256", "overlay_manifest_sha256",
        "config_schema", "executor_principal", "operator_principals",
        "executor_credential_env", "credential_reference", "trusted_root", "trusted_uid",
        "forbidden_paths",
    }, "release runtime config")
    if projection["release_path"].startswith("/") or ".." in Path(projection["release_path"]).parts:
        raise ApplicationReleaseRemoteError("release projection path escapes the generation")
    if runtime_binding["generation_path"] != "config/tgw-api-config.json":
        raise ApplicationReleaseRemoteError("runtime config path differs from the sealed namespace")
    receipts = parameters["migration_receipts"]
    if not isinstance(receipts, list) or len(receipts) != len(MIGRATION_PATHS):
        raise ApplicationReleaseRemoteError("exact ordered migration receipts are absent")
    for receipt, expected_path in zip(receipts, MIGRATION_PATHS, strict=True):
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("migration_path") != expected_path
            or _SHA.fullmatch(str(receipt.get("migration_sha256"))) is None
            or _SHA.fullmatch(str(receipt.get("receipt_hash"))) is None
        ):
            raise ApplicationReleaseRemoteError("migration receipt order or identity differs")
    if request["action"] == "install":
        try:
            archive = base64.b64decode(request["archive_b64"], validate=True)
            runtime_config = base64.b64decode(request["config_b64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ApplicationReleaseRemoteError("release artifacts are not canonical base64") from exc
        if (
            not archive or len(archive) > int(config["max_archive_bytes"])
            or not runtime_config or len(runtime_config) > int(config["max_config_bytes"])
            or _hash_bytes(archive) != parameters["archive_sha256"]
            or _hash_bytes(runtime_config) != parameters["runtime_config"]["content_sha256"]
        ):
            raise ApplicationReleaseRemoteError("release artifact bytes differ from exact contract")
        request["archive"] = archive; request["runtime_config_bytes"] = runtime_config
    elif request["archive_b64"] != "" or request["config_b64"] != "":
        raise ApplicationReleaseRemoteError("rollback request carries unexpected artifact bytes")
    request["parameters"] = parameters
    return request


class HostRuntime:
    """Concrete fixed-operation adapter used only by the root helper."""

    def __init__(self, config: Mapping[str, Any]):
        self.config = config

    def _run(self, executable: str, arguments: Sequence[str], *, stdin: bytes = b"", limit: int = 1024 * 1024) -> bytes:
        binding = self.config["executables"][executable]
        path = Path(binding["path"])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            held = os.fstat(fd)
            raw = bytearray(); offset = 0
            while block := os.pread(fd, 1024 * 1024, offset):
                raw.extend(block); offset += len(block)
            named = os.stat(path, follow_symlinks=False)
            identity = (held.st_dev, held.st_ino, held.st_uid, held.st_gid, held.st_mode, held.st_size)
            if (
                _hash_bytes(bytes(raw)) != binding["sha256"] or not stat.S_ISREG(held.st_mode)
                or stat.S_IMODE(held.st_mode) not in {0o555, 0o755} or held.st_uid != 0 or held.st_nlink != 1
                or identity != (named.st_dev, named.st_ino, named.st_uid, named.st_gid, named.st_mode, named.st_size)
            ):
                raise ApplicationReleaseRemoteError(f"held {executable} executable differs")
            process = subprocess.Popen(
                [f"/proc/{os.getpid()}/fd/{fd}", *arguments], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                pass_fds=(fd,), env={"PATH": "", "LANG": "C", "LC_ALL": "C"},
            )
            try:
                stdout, stderr = _bounded_process(
                    process, stdin, limit=limit, timeout=int(self.config["command_timeout_seconds"]),
                )
            except Exception as exc:
                state = _group_empty_or_kill(process.pid)
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired as reap_exc:
                    raise ApplicationReleaseRemoteError(
                        f"{executable} leader could not be reaped"
                    ) from reap_exc
                state = _post_reap_group_state(process.pid, state)
                if not state.get("removed"):
                    raise ApplicationReleaseRemoteError(
                        f"{executable} process-group cleanup is ambiguous"
                    ) from exc
                raise ApplicationReleaseRemoteError(f"{executable} bounded lifecycle failed") from exc
            state = _post_reap_group_state(
                process.pid, _group_empty_or_kill(process.pid),
            )
            if state.get("had_survivor") or not state.get("removed"):
                raise ApplicationReleaseRemoteError(f"{executable} left a surviving process group")
            if process.returncode:
                raise ApplicationReleaseRemoteError(f"{executable} failed: {_hash_bytes(stderr)}")
            after = os.fstat(fd); named_after = os.stat(path, follow_symlinks=False)
            if identity != (after.st_dev, after.st_ino, after.st_uid, after.st_gid, after.st_mode, after.st_size) or identity != (named_after.st_dev, named_after.st_ino, named_after.st_uid, named_after.st_gid, named_after.st_mode, named_after.st_size):
                raise ApplicationReleaseRemoteError(f"named {executable} executable changed")
            return stdout
        finally:
            os.close(fd)

    def systemctl(self, verb: str, services: Sequence[str]) -> bytes:
        if verb not in {"stop", "restart", "is-active"} or list(services) != self.config["services"]:
            raise ApplicationReleaseRemoteError("systemd operation is outside the mounted service set")
        return self._run("systemctl", [verb, "--", *services])

    def quiesce(self) -> None:
        self.systemctl("stop", self.config["services"])
        states = self._run(
            "systemctl",
            ["show", "--property=ActiveState", "--value", "--", *self.config["services"]],
        ).decode("ascii", errors="strict").splitlines()
        if states != ["inactive"] * len(self.config["services"]):
            raise ApplicationReleaseRemoteError("service quiescence was not proven")

    def backup(self, destination: Path) -> None:
        db = self.config["database"]
        data = self._run("pg_dump", ["-Fc", "-h", db["host"], "-U", db["user"], db["name"]], limit=512 * 1024 * 1024)
        _write_once(destination, data, 0o400)

    def migrate(self, sources: Sequence[bytes]) -> None:
        db = self.config["database"]
        body = b"BEGIN;\n" + b"\n".join(sources) + b"\nCOMMIT;\n"
        self._run("psql", ["-X", "--no-psqlrc", "-v", "ON_ERROR_STOP=1", "-h", db["host"], "-U", db["user"], "-d", db["name"]], stdin=body)

    def restore(self, source: Path) -> None:
        db = self.config["database"]
        self._run("pg_restore", ["--clean", "--if-exists", "-h", db["host"], "-U", db["user"], "-d", db["name"], str(source)])

    def health(self) -> None:
        self.systemctl("is-active", self.config["services"])
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for probe in self.config["health_probes"]:
            if not probe.startswith("http://127.0.0.1:"):
                raise ApplicationReleaseRemoteError("health probe is outside loopback HTTP")
            with opener.open(probe, timeout=10) as response:
                if response.status != 200 or len(response.read(64 * 1024 + 1)) > 64 * 1024:
                    raise ApplicationReleaseRemoteError("health probe failed")


def _write_once(path: Path, raw: bytes, mode: int) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0: raise OSError("short protected write")
            offset += written
        os.fchmod(descriptor, mode); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _atomic(path: Path, raw: bytes, mode: int) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    _write_once(temporary, raw, mode)
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _persist(root: Path, prefix: str, value: Mapping[str, Any]) -> str:
    raw = _canonical(value) + b"\n"; digest = _hash_bytes(raw)
    path = root / f"{prefix}-{digest.removeprefix('sha256:')}.json"
    if path.exists():
        if path.read_bytes() != raw: raise ApplicationReleaseRemoteError("receipt hash collision")
    else:
        _write_once(path, raw, 0o400)
    return f"application-release:{prefix}:{digest}"


def _snapshot(paths: Sequence[str]) -> str:
    values = []
    for named in paths:
        path = Path(named)
        metadata = path.lstat()
        if (
            not path.is_absolute() or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1 or metadata.st_size > 64 * 1024 * 1024
        ):
            raise ApplicationReleaseRemoteError(
                "unrelated-state bindings must name exact bounded regular artifacts"
            )
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            held = os.fstat(fd)
            raw = os.pread(fd, metadata.st_size + 1, 0)
            named_after = os.stat(path, follow_symlinks=False)
            identity = (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_size)
            held_identity = (held.st_dev, held.st_ino, held.st_mode, held.st_size)
            after_identity = (
                named_after.st_dev, named_after.st_ino,
                named_after.st_mode, named_after.st_size,
            )
            if len(raw) != metadata.st_size or identity != held_identity or identity != after_identity:
                raise ApplicationReleaseRemoteError("unrelated-state artifact changed during observation")
            values.append([named, *identity, _hash_bytes(raw)])
        finally:
            os.close(fd)
    return _hash(values)


def _journal_path(config: Mapping[str, Any], operation: str) -> Path:
    if _GENERATION.fullmatch(operation) is None:
        raise ApplicationReleaseRemoteError("operation id is invalid")
    return Path(config["receipt_root"]) / f"journal-{operation}.json"


def _load_journal(config: Mapping[str, Any], operation: str) -> dict[str, Any]:
    path = _journal_path(config, operation)
    if not path.exists():
        return {"schema": "tgw-w09-application-release-journal/v1", "operation_id": operation, "stages": [], "evidence": []}
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or value.get("operation_id") != operation:
        raise ApplicationReleaseRemoteError("operation journal is invalid")
    return value


def _stage(config: Mapping[str, Any], journal: dict[str, Any], stage: str, evidence: Sequence[str]) -> str:
    if stage in journal["stages"]:
        return _hash({"operation": journal["operation_id"], "stage": stage})
    journal["stages"].append(stage); journal["evidence"].extend(evidence)
    path = _journal_path(config, journal["operation_id"])
    _atomic(path, _canonical(journal) + b"\n", 0o600)
    return _hash({"operation": journal["operation_id"], "stage": stage})


def _reconcile(parameters: Mapping[str, Any], config: Mapping[str, Any], runtime: HostRuntime, journal: dict[str, Any]) -> dict[str, Any]:
    root = Path(config["release_root"]); operation = parameters["operation_id"]
    evidence: list[str] = []
    try:
        selection = root / "receipts" / f"{operation}.json"
        if selection.exists() and current_generation(root) == parameters["generation"]:
            restored = rollback(root, selection, expected_current=parameters["generation"], operation_id=operation + "-rollback")
            evidence.append("selector:" + restored["operation_id"])
        config_backup = Path(config["receipt_root"]) / f"{operation}.config.backup"
        active_config = Path(config["active_config_path"])
        if config_backup.exists():
            _atomic(active_config, config_backup.read_bytes(), 0o640); evidence.append("config:restored")
        database_backup = Path(config["backup_root"]) / f"{operation}.dump"
        if database_backup.exists() and any(
            stage in journal["stages"]
            for stage in ("migration-restore-required", "migrations-applied")
        ):
            runtime.restore(database_backup); evidence.append("database:restored")
        runtime.systemctl("restart", config["services"]); runtime.health()
        evidence.extend(["services:predecessor", "health:predecessor"])
        receipt = _persist(Path(config["receipt_root"]), "rollback", {
            "schema": "tgw-w09-application-rollback/v1", "operation_id": operation,
            "generation": parameters["expected_current"], "evidence": evidence,
        })
        return {"status": "RESTORED", "receipt": receipt, "generation": parameters["expected_current"], "predecessor_healthy": True, "evidence": evidence}
    except Exception as exc:
        return {"status": "AMBIGUOUS", "generation": current_generation(root), "predecessor_healthy": False, "evidence": evidence + ["reconcile-error:" + _hash_bytes(str(exc).encode())]}


def execute(request_value: Mapping[str, Any], config: Mapping[str, Any], runtime: HostRuntime) -> dict[str, Any]:
    request = validate_request(request_value, config); parameters = request["parameters"]
    journal = _load_journal(config, parameters["operation_id"])
    if request["action"] == "rollback":
        result = _reconcile(parameters, config, runtime, journal)
        return _bound_response(parameters, result, config)
    root = Path(config["release_root"]); evidence: list[str] = []
    try:
        predecessor_binding = parameters["predecessor"]
        if current_generation(root) != parameters["expected_current"] or os.readlink("/run/current-system") != parameters["nix_system_path"]:
            raise ApplicationReleaseRemoteError("fresh predecessor selector/Nix observation differs")
        predecessor_release = root / "releases" / parameters["expected_current"]
        predecessor_verification = verify(root, parameters["expected_current"])
        predecessor_manifest = json.loads(
            (predecessor_release / ".release-manifest.json").read_bytes()
        )
        actual_predecessor = {
            "generation": parameters["expected_current"],
            "selector_target": str(predecessor_release),
            "commit": predecessor_manifest.get("commit"),
            "tree": predecessor_manifest.get("git_tree"),
            "archive_sha256": "sha256:" + str(predecessor_manifest.get("archive_sha256")),
            "release_manifest_hash": _hash(predecessor_manifest),
            "content_manifest_sha256": "sha256:" + str(predecessor_manifest.get("content_manifest_sha256")),
            "projection_sha256": _hash_bytes((predecessor_release / PROJECTION_PATH).read_bytes()),
            "runtime_config_sha256": _hash_bytes(Path(config["active_config_path"]).read_bytes()),
        }
        if predecessor_verification.get("status") != "PASS" or actual_predecessor != predecessor_binding:
            raise ApplicationReleaseRemoteError("fresh predecessor release/config identity differs")
        runtime.systemctl("is-active", config["services"])
        predecessor = _persist(Path(config["receipt_root"]), "preflight", {
            "schema": "tgw-w09-live-predecessor/v1", "bound_observation": parameters["predecessor_observation_hash"],
            "generation": parameters["expected_current"], "nix_system_path": parameters["nix_system_path"],
            "services": config["services"], "health_probes": config["health_probes"],
            "release": actual_predecessor,
        })
        unrelated = _snapshot(config["unrelated_paths"]); evidence += [predecessor, _stage(config, journal, "predecessor-verified", [predecessor])]
        runtime.quiesce(); evidence.append(_stage(config, journal, "services-quiesced", ["services:inactive"]))
        operation = parameters["operation_id"]
        config_backup = Path(config["receipt_root"]) / f"{operation}.config.backup"
        if not config_backup.exists(): _write_once(config_backup, Path(config["active_config_path"]).read_bytes(), 0o400)
        database_backup = Path(config["backup_root"]) / f"{operation}.dump"
        if not database_backup.exists(): runtime.backup(database_backup)
        backup_receipt = "backup:" + _hash_bytes(database_backup.read_bytes())
        evidence += [backup_receipt, _stage(config, journal, "database-backed-up", [backup_receipt])]
        archive_path = Path(config["receipt_root"]) / f"{parameters['archive_sha256'].removeprefix('sha256:')}.tar"
        if not archive_path.exists(): _write_once(archive_path, request["archive"], 0o400)
        manifest = materialize(
            root, archive_path, generation=parameters["generation"], commit=parameters["candidate_commit"],
            tree=parameters["candidate_tree"], archive_sha256=parameters["archive_sha256"].removeprefix("sha256:"),
        )
        verify(root, parameters["generation"])
        evidence.append(_stage(config, journal, "release-materialized", ["manifest:" + manifest["content_manifest_sha256"]]))
        release = root / "releases" / parameters["generation"]
        sources = []
        for receipt in parameters["migration_receipts"]:
            source = (release / receipt["migration_path"]).read_bytes()
            if _hash_bytes(source) != receipt["migration_sha256"]: raise ApplicationReleaseRemoteError("migration source differs")
            sources.append(source)
        evidence.append(_stage(
            config, journal, "migration-restore-required",
            ["database-restore:required-before-migration-dispatch"],
        ))
        runtime.migrate(sources)
        evidence.append(_stage(config, journal, "migrations-applied", ["migrations:ordered"]))
        installed = install_runtime_files(root, parameters["generation"], {parameters["runtime_config"]["generation_path"]: request["runtime_config_bytes"]})
        if (
            "sha256:" + str(installed.get("runtime_manifest_sha256"))
            != parameters["runtime_config"]["overlay_manifest_sha256"]
        ):
            raise ApplicationReleaseRemoteError("runtime overlay manifest differs from W09 contract")
        verify(root, parameters["generation"])
        evidence.append(_stage(config, journal, "runtime-staged", ["runtime:" + str(installed["runtime_manifest_sha256"])]))
        selected = select(root, parameters["generation"], expected_current=parameters["expected_current"], operation_id=operation)
        _atomic(Path(config["active_config_path"]), request["runtime_config_bytes"], 0o640)
        evidence.append(_stage(config, journal, "generation-activated", ["selection:" + selected["operation_id"]]))
        runtime.systemctl("restart", config["services"]); evidence.append(_stage(config, journal, "successor-restarted", ["services:successor"]))
        runtime.health()
        if _snapshot(config["unrelated_paths"]) != unrelated: raise ApplicationReleaseRemoteError("unrelated host state changed")
        evidence.append(_stage(config, journal, "successor-verified", ["health:successor", "unrelated:unchanged"]))
        result = {"status": "SUCCEEDED", "evidence": evidence}
    except Exception as exc:
        result = _reconcile(parameters, config, runtime, journal)
        result["detail_hash"] = _hash_bytes(str(exc).encode())
    return _bound_response(parameters, result, config)


def _bound_response(
    parameters: Mapping[str, Any], result: Mapping[str, Any], config: Mapping[str, Any],
) -> dict[str, Any]:
    unsigned = {
        "schema": RESPONSE_SCHEMA,
        "operation_id": parameters["operation_id"],
        "helper_sha256": config["helper_sha256"],
        "helper_config_sha256": config["config_sha256"],
        "nix_system_path": parameters["nix_system_path"],
        **dict(result),
    }
    return {**unsigned, "receipt_sha256": _hash(unsigned)}


def main() -> int:
    if os.geteuid() != 0 or os.environ.get("SSH_ORIGINAL_COMMAND", "") not in {"", None}:
        raise ApplicationReleaseRemoteError("helper requires root forced-command execution with no original argv")
    packet = sys.stdin.buffer.read(MAX_PACKET + 1)
    if len(packet) > MAX_PACKET: raise ApplicationReleaseRemoteError("request exceeds fixed packet bound")
    request = json.loads(packet)
    config = _read_config()
    response = execute(request, config, HostRuntime(config))
    sys.stdout.buffer.write(_canonical(response) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
