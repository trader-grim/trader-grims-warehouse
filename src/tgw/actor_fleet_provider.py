"""Privileged, closed W18 actor-fleet provider for the ``tgw-lib`` host.

The Plan/API coordinator runs on ``tgw-prod``.  Actor accounts, canonical
source, harness worktrees and actor-local MCP registrations run on ``tgw-lib``.
This provider is therefore deliberately separate from the production
platform-control provider.  It accepts only the fixed actor refresh state
machine and never accepts a command, path, account, service or candidate
selector from its caller.
"""

from __future__ import annotations

import base64
import binascii
import fcntl
import grp
import hashlib
import importlib.util
import json
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import anyio
import uvicorn
import yaml
from fastapi import FastAPI, Header, HTTPException
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tgw.admission_recovery import validate_release_admission
from tgw.config import DEFAULT_CONFIG, load_operational_config
from tgw.context_source_guard import ContextSourceGuardError, validate_context_source
from tgw.release_installer import ReleaseError
from tgw.release_installer import verify as verify_release

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_UNIT = re.compile(r"[A-Za-z0-9_.@-]+\.(?:service|timer)\Z")
_CONTEXT_MCP_TOOLS = {
    "tgw_context_status",
    "tgw_context_confirm_rebind",
    "tgw_context_bundle",
    "tgw_context_plan_graph",
    "tgw_context_plan_source",
    "tgw_context_runbooks",
    "tgw_context_code_graph",
    "tgw_context_onboarding",
}
_ACTOR_VERIFICATION_MAX_INPUT = 4 * 1024 * 1024
_CONTEXT_REGISTRATION_MAX_INPUT = 256 * 1024
_CURRENT_PLAN_SOURCE_PATHS = (
    "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml",
    "pp/PP-ACTOR-MCP-BOUNDARY-001.md",
    "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml",
    "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml",
)
_COORDINATOR_JOURNAL_MAX_INPUT = 8 * 1024 * 1024
_DEFAULT_ACTOR_FLEET_STATE_ROOT = Path("/var/lib/tgw/actor-fleet")
_DEFAULT_CONTEXT_UPDATE_ROOT = Path("/var/lib/tgw/context-update")
_DEFAULT_COORDINATOR_TRANSACTION_ROOT = Path(
    "/var/lib/tgw/context-update/transactions"
)
_DEFAULT_CONTEXT_UPDATE_SCRATCH_ROOT = Path("/var/cache/tgw/context-update")
_STABLE_CONTEXT_LAUNCHER = Path("/opt/TGW/tgw-lib/bin/tgw-actor")
_ACTOR_PROVIDER_CONFIG = Path(
    "/opt/TGW/tgw-lib/config/tgw-governed-actor-control.json"
)
_ACTOR_PUBLIC_TRUST = Path("/etc/tgw/trust/actor-contract.pub")
_ENVIRONMENT_PUBLIC_TRUST = Path("/etc/tgw/trust/environment-preflight.pub")
_ADMISSION_PUBLIC_TRUST = Path("/etc/tgw/trust/release-admission.pub")
_DEEPSEEK_USER_SERVICE = "dsh.service"
_DEEPSEEK_USER_UNIT = Path("/home/deepseek/.config/systemd/user/dsh.service")
_DEEPSEEK_LINGER = Path("/var/lib/systemd/linger/deepseek")
_DEEPSEEK_UID = 1005
_MANAGED_SERVICE_ALLOWLIST = {
    "tgw-coding-provision-pull.timer",
    "tgw-coding-provision-pull.service",
}
_COORDINATOR_SERVICE_PREIMAGES = {
    "provider-service": "tgw-actor-fleet-provider.service",
    "relay-service": "tgw-context-confirmation-relay.service",
}
_COORDINATOR_SERVICE_PROPERTIES = {
    "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID",
    "FragmentPath", "ExecMainStartTimestampMonotonic",
}
_COORDINATOR_EFFECT_ACTIONS = (
    "INSTALL_PLATFORM_TRUST", "PUBLISH_ADMISSION", "INSTALL_CATALOG",
    "SELECT_RELEASE",
    "INSTALL_ACTOR_HOST", "INSTALL_STABLE_LAUNCHER",
    "INSTALL_DIRECT_STATUS", "INSTALL_CONFIRMATION_RELAY",
    "RESTART_PROVIDER", "BIND_COORDINATOR", "QUIESCE_ACTORS",
    "REBUILD_ACTORS", "ACTIVATE_ACTORS", "VERIFY_COLD_CONTINUITY",
    "TRANSITION_DEEPSEEK_SERVICE", "RESTART_ACTORS", "HEALTH_ACTORS",
    "VERIFY_ACTORS", "FINALIZE_TRANSACTION",
)
_JOURNAL_LEDGER_LINK_FIELDS = {
    "journal_payload_sha256",
    "ledger_evidence",
    "ledger_pending",
    "ledger_sequence",
    "ledger_record_sha256",
}
_ACTOR_VERIFICATION_BOOTSTRAP = (
    "import runpy,sys;"
    "sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module('tgw.actor_fleet_provider',run_name='__main__')"
)


class ActorFleetError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _strong_process_identity_is_exact(value: Any, *, uid: int) -> bool:
    fields = {
        "boot_id", "pid", "start_ticks", "uid", "ppid", "executable_path",
        "executable_device", "executable_inode", "executable_sha256",
        "cmdline_shape", "cmdline_sha256", "identity_hash",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        return False
    unsigned = dict(value)
    claimed = unsigned.pop("identity_hash", None)
    return (
        isinstance(value.get("boot_id"), str)
        and bool(value["boot_id"])
        and isinstance(value.get("pid"), int)
        and value["pid"] > 1
        and isinstance(value.get("start_ticks"), int)
        and value["start_ticks"] > 0
        and value.get("uid") == uid
        and isinstance(value.get("ppid"), int)
        and value["ppid"] >= 0
        and isinstance(value.get("executable_path"), str)
        and Path(value["executable_path"]).is_absolute()
        and ".." not in Path(value["executable_path"]).parts
        and isinstance(value.get("executable_device"), int)
        and value["executable_device"] >= 0
        and isinstance(value.get("executable_inode"), int)
        and value["executable_inode"] > 0
        and _HASH.fullmatch(str(value.get("executable_sha256", ""))) is not None
        and isinstance(value.get("cmdline_shape"), list)
        and bool(value["cmdline_shape"])
        and all(isinstance(item, str) for item in value["cmdline_shape"])
        and _HASH.fullmatch(str(value.get("cmdline_sha256", ""))) is not None
        and claimed == _hash(unsigned)
    )


def _journal_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the durable provider payload independent of its ledger link."""

    return {
        key: item for key, item in value.items()
        if key not in _JOURNAL_LEDGER_LINK_FIELDS
    }


def _directory(value: Any, label: str) -> Path:
    path = Path(value) if isinstance(value, (str, os.PathLike)) else Path()
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents or not path.is_dir() or path.is_symlink():
        raise ActorFleetError(f"{label} must be a durable directory outside /tmp")
    return path


def _regular(value: Any, label: str) -> Path:
    path = Path(value) if isinstance(value, (str, os.PathLike)) else Path()
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents or not path.is_file() or path.is_symlink():
        raise ActorFleetError(f"{label} is unavailable")
    return path


def _trusted_public_key(value: Any, label: str) -> bytes:
    path = _regular(value, label)
    observed = path.stat(follow_symlinks=False)
    if observed.st_mode & 0o022 or (os.geteuid() == 0 and observed.st_uid != 0):
        raise ActorFleetError(f"{label} is not root protected")
    raw = path.read_bytes()
    if len(raw) != 32:
        raise ActorFleetError(f"{label} must contain one raw Ed25519 public key")
    return raw


def _contract_public_key(value: Any, label: str) -> tuple[str, bytes]:
    """Return one canonical base64-encoded raw Ed25519 verifier identity."""

    if not isinstance(value, str):
        raise ActorFleetError(f"{label} is invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ActorFleetError(f"{label} is invalid") from exc
    if len(raw) != 32 or base64.b64encode(raw).decode("ascii") != value:
        raise ActorFleetError(f"{label} is invalid")
    return value, raw


def _actor_contract_is_signed_by(
    contract: Mapping[str, Any], trusted_public_key: str
) -> bool:
    """Verify both the actor-contract receipt and its Ed25519 signature."""

    if contract.get("issuer_public_key") != trusted_public_key:
        return False
    receipt_body = dict(contract)
    receipt_hash = receipt_body.pop("receipt_hash", None)
    receipt_body.pop("issuer_public_key", None)
    receipt_body.pop("signature", None)
    signed = dict(contract)
    signature = signed.pop("signature", None)
    signed.pop("issuer_public_key", None)
    if receipt_hash != _hash(receipt_body) or not isinstance(signature, str):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )

        _normalized, raw_key = _contract_public_key(
            trusted_public_key, "actor contract verifier"
        )
        raw_signature = base64.b64decode(signature, validate=True)
        if len(raw_signature) != 64:
            return False
        Ed25519PublicKey.from_public_bytes(raw_key).verify(
            raw_signature, _canonical(signed)
        )
    except Exception:
        return False
    return True


def _verified_release(release_root: Path, child: Path) -> dict[str, Any]:
    """Verify an installed immutable release before importing any of its code."""
    if child.is_symlink() or not child.is_dir():
        raise ActorFleetError("actor release is not an immutable directory")
    for path in (child, *child.rglob("*")):
        observed = path.stat(follow_symlinks=False)
        relative = path.relative_to(child).as_posix() if path != child else "."
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ActorFleetError(f"actor release contains an unsafe path: {relative}")
        if observed.st_mode & 0o022 or (os.geteuid() == 0 and observed.st_uid != 0):
            raise ActorFleetError(f"actor release is not root protected: {relative}")
    try:
        verify_release(release_root.parent, child.name)
    except (OSError, TypeError, ValueError, ReleaseError) as exc:
        raise ActorFleetError("actor release content does not match its manifest") from exc
    return _read_json(child / ".release-manifest.json", "actor release manifest")


def _shared_attempt_root(value: Any, label: str, group: grp.struct_group) -> Path:
    """Verify one bootstrap-owned, shared actor attempt root.

    The provider intentionally does not repair this layout on startup.  A
    missing or drifted directory holds dispatch until the versioned host
    bootstrap restores it, so a service restart cannot silently broaden
    filesystem access.
    """
    path = _directory(value, label)
    observed = path.stat(follow_symlinks=False)
    if observed.st_gid != group.gr_gid or stat.S_IMODE(observed.st_mode) != 0o2770:
        raise ActorFleetError(f"{label} must be group {group.gr_name} with mode 2770")
    if os.geteuid() == 0 and observed.st_uid != 0:
        raise ActorFleetError(f"{label} must be root-owned")
    return path


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorFleetError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ActorFleetError(f"{label} is invalid")
    return value


def _binding_digest(path: Path) -> str:
    """Hash one post-activation binding using the materializer's rules."""
    if path.is_symlink():
        raise ActorFleetError("actor binding source cannot be a symlink")
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
    elif path.is_dir():
        files = [item for item in path.rglob("*") if item.is_file() and not item.is_symlink() and "__pycache__" not in item.parts]
        for item in sorted(files, key=lambda value: value.relative_to(path).as_posix()):
            digest.update(item.relative_to(path).as_posix().encode())
            digest.update(b"\0")
            digest.update(item.read_bytes())
            digest.update(b"\0")
    else:
        raise ActorFleetError("actor binding source is unavailable")
    return "sha256:" + digest.hexdigest()


def _context_registration(
    path: Path,
    expected_source: Path | None = None,
) -> tuple[str, list[str], dict[str, str]]:
    """Load one materialized harness registration without harness fallback."""

    def endpoint_from(raw: bytes, source_path: Path) -> Mapping[str, Any]:
        content = raw.decode("utf-8")
        if source_path.suffix == ".toml":
            value = tomllib.loads(content)
            endpoint = value["mcp_servers"]["tgw-context"]
        elif source_path.suffix in {".yaml", ".yml"}:
            root = yaml.compose(content, Loader=yaml.BaseLoader)

            def node_value(
                node: yaml.Node, *, depth: int = 0, active: frozenset[int] = frozenset()
            ) -> Any:
                if depth > 32 or id(node) in active:
                    raise ActorFleetError("actor Context MCP YAML graph is unsafe")
                nested = active | {id(node)}
                if isinstance(node, yaml.ScalarNode):
                    return node.value
                if isinstance(node, yaml.SequenceNode):
                    return [
                        node_value(item, depth=depth + 1, active=nested)
                        for item in node.value
                    ]
                if isinstance(node, yaml.MappingNode):
                    result: dict[str, Any] = {}
                    for key, item in node.value:
                        name = str(node_value(key, depth=depth + 1, active=nested))
                        if name in result:
                            raise ActorFleetError(
                                "actor Context MCP YAML mapping is duplicated"
                            )
                        result[name] = node_value(
                            item, depth=depth + 1, active=nested
                        )
                    return result
                raise ActorFleetError("actor Context MCP YAML node is invalid")

            if not isinstance(root, yaml.SequenceNode):
                raise ActorFleetError("actor Context MCP patch root is invalid")
            rows: list[Mapping[str, Any]] = []
            for operation_node in root.value:
                if not isinstance(operation_node, yaml.MappingNode):
                    raise ActorFleetError("actor Context MCP patch operation is invalid")
                for key_node, inserted_node in operation_node.value:
                    if (
                        not isinstance(key_node, yaml.ScalarNode)
                        or key_node.value != "insert"
                    ):
                        continue
                    if not isinstance(inserted_node, yaml.SequenceNode):
                        raise ActorFleetError("actor Context MCP insert is invalid")
                    inserted = [node_value(item) for item in inserted_node.value]
                    if not all(isinstance(item, Mapping) for item in inserted):
                        raise ActorFleetError("actor Context MCP insert row is invalid")
                    rows.extend(inserted)
            matches = [
                item for item in rows
                if item.get("id") == "tgw-context"
                or (
                    isinstance(item.get("config"), Mapping)
                    and item["config"].get("serverName") == "tgw-context"
                )
            ]
            if len(matches) != 1:
                raise ActorFleetError(
                    "actor Context MCP registration has duplicate effective targets"
                )
            row = matches[0]
            endpoint = row["config"]
        else:
            value = json.loads(content)
            endpoint = value["mcpServers"]["tgw-context"]
        if not isinstance(endpoint, Mapping):
            raise ActorFleetError("actor Context MCP registration is invalid")
        return endpoint

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise ActorFleetError("actor Context MCP binding changed")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_CONTEXT_REGISTRATION_MAX_INPUT + 1)
            if len(raw) > _CONTEXT_REGISTRATION_MAX_INPUT:
                raise ActorFleetError("actor Context MCP registration is too large")
        finally:
            os.close(descriptor)
        endpoint = endpoint_from(raw, path)
        if expected_source is not None:
            expected_raw = expected_source.read_bytes()
            if len(expected_raw) > _CONTEXT_REGISTRATION_MAX_INPUT:
                raise ActorFleetError("actor Context MCP registration is too large")
            if dict(endpoint) != dict(endpoint_from(expected_raw, expected_source)):
                raise ActorFleetError("actor Context MCP active-store projection differs")
    except (
        OSError,
        IndexError,
        KeyError,
        StopIteration,
        TypeError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as exc:
        raise ActorFleetError("actor Context MCP registration is invalid") from exc
    command, args, environment = endpoint.get("command"), endpoint.get("args"), endpoint.get("env")
    if (
        not isinstance(command, str)
        or not command.startswith("/")
        or not isinstance(args, list)
        or not args
        or not all(isinstance(arg, str) and arg for arg in args)
        or not isinstance(environment, Mapping)
        or not all(isinstance(name, str) and isinstance(raw, str) for name, raw in environment.items())
    ):
        raise ActorFleetError("actor Context MCP registration is incomplete")
    return command, list(args), dict(environment)


def _actor_context_process_inventory(
    actors: list[str],
    *,
    proc_root: Path = Path("/proc"),
) -> list[dict[str, Any]]:
    """Return every live Context MCP child owned by a registered actor."""
    procfs_root = proc_root
    actor_by_uid = {pwd.getpwnam(actor).pw_uid: actor for actor in actors}
    try:
        boot_id = (procfs_root / "sys" / "kernel" / "random" / "boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise ActorFleetError("actor Context MCP boot identity is unavailable") from exc
    if not re.fullmatch(r"[0-9a-f-]{36}", boot_id):
        raise ActorFleetError("actor Context MCP boot identity is invalid")

    def bounded(path: Path, limit: int) -> bytes:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            raise ActorFleetError("actor Context MCP proc record exceeds its bound")
        return raw

    def process_identity(
        pid: int,
        *,
        status_rows: list[str] | None = None,
        arguments: list[str] | None = None,
    ) -> dict[str, Any]:
        root = procfs_root / str(pid)
        if status_rows is None:
            status_rows = bounded(root / "status", 64 * 1024).decode(
                "utf-8"
            ).splitlines()
        status = {
            row.split(":", 1)[0]: row.split(":", 1)[1].strip()
            for row in status_rows if ":" in row
        }
        raw_stat = bounded(root / "stat", 64 * 1024).decode("utf-8")
        start_ticks = int(raw_stat.rsplit(") ", 1)[1].split()[19])
        if arguments is None:
            arguments = [
                raw.decode("utf-8", errors="replace")
                for raw in bounded(root / "cmdline", 256 * 1024).split(b"\0") if raw
            ]
        executable = root / "exe"
        executable_state = executable.stat()
        executable_path = str(executable.resolve(strict=True))
        executable_digest = hashlib.sha256()
        with executable.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                executable_digest.update(chunk)
        executable_state_after = executable.stat()
        if (
            executable_state.st_dev != executable_state_after.st_dev
            or executable_state.st_ino != executable_state_after.st_ino
        ):
            raise ActorFleetError("actor Context MCP executable identity changed")
        executable_hash = "sha256:" + executable_digest.hexdigest()
        shape = [Path(arguments[0]).name if arguments else ""]
        shape.extend(
            item for item in arguments[1:]
            if item.startswith("--") or item in {"-m", "tgw.context_mcp_server"}
        )
        value = {
            "boot_id": boot_id,
            "pid": pid,
            "start_ticks": start_ticks,
            "uid": int(status["Uid"].split()[0]),
            "ppid": int(status.get("PPid", "0")),
            "executable_path": executable_path,
            "executable_device": executable_state.st_dev,
            "executable_inode": executable_state.st_ino,
            "executable_sha256": executable_hash,
            "cmdline_shape": shape,
            "cmdline_sha256": _hash(arguments),
        }
        return {**value, "identity_hash": _hash(value), "arguments": arguments}

    inventory: list[dict[str, Any]] = []
    for process_dir in procfs_root.iterdir():
        if not process_dir.name.isdigit() or not process_dir.is_dir():
            continue
        try:
            # Filter unrelated processes before touching their executable or
            # environment.  Cross-UID /proc access is needed only for the
            # declared actor-owned Context candidates.
            status_rows = bounded(process_dir / "status", 64 * 1024).decode(
                "utf-8"
            ).splitlines()
            status = {
                row.split(":", 1)[0]: row.split(":", 1)[1].strip()
                for row in status_rows if ":" in row
            }
            uid = int(status["Uid"].split()[0])
            actor = actor_by_uid.get(uid)
            if actor is None:
                continue
            arguments = [
                raw.decode("utf-8", errors="replace")
                for raw in bounded(process_dir / "cmdline", 256 * 1024).split(b"\0") if raw
            ]
            direct = any(
                arguments[index:index + 2] == ["-m", "tgw.context_mcp_server"]
                for index in range(max(0, len(arguments) - 1))
            )
            stable_launcher = (
                "--context-mcp" in arguments
                and str(_STABLE_CONTEXT_LAUNCHER) in arguments
            )
            actor_launcher = "--context-mcp" in arguments and any(
                Path(argument).name in {"tgw-actor", "tgw_actor_startup.py"}
                for argument in arguments
            )
            if not direct and not actor_launcher:
                continue
            identity = process_identity(
                int(process_dir.name), status_rows=status_rows, arguments=arguments
            )
            identity_arguments = identity.pop("arguments")
            environment = {}
            for row in bounded(process_dir / "environ", 1024 * 1024).split(b"\0"):
                if b"=" in row:
                    name, raw = row.split(b"=", 1)
                    environment[name.decode(errors="replace")] = raw.decode(errors="replace")
            parent = None
            if identity["ppid"] > 0:
                if (procfs_root / str(identity["ppid"])).is_dir():
                    try:
                        parent = process_identity(identity["ppid"])
                        parent.pop("arguments", None)
                    except (FileNotFoundError, ProcessLookupError):
                        parent = {
                            "state": "PARENT_CHANGED_DURING_INVENTORY",
                            "pid": identity["ppid"],
                        }
                else:
                    parent = {
                        "state": "PARENT_CHANGED_DURING_INVENTORY",
                        "pid": identity["ppid"],
                    }
            inventory.append(
                {
                    **identity,
                    "arguments": identity_arguments,
                    "actor": actor,
                    "endpoint": environment.get("TGW_CONTEXT_ENDPOINT", "tgw-context"),
                    "profile": environment.get("TGW_CONTEXT_PROFILE", ""),
                    "parent": parent,
                    "stable_launcher": stable_launcher,
                    "guarded": bool(environment.get("TGW_CONTEXT_STARTUP_BINDING")),
                    "startup_binding": environment.get("TGW_CONTEXT_STARTUP_BINDING", ""),
                    "generation": environment.get("TGW_CONTEXT_GENERATION", ""),
                    "plan": environment.get("TGW_CONTEXT_PLAN_COMMIT", ""),
                    "solution": environment.get("TGW_CONTEXT_PLAN_SOLUTION", ""),
                    "source_commit": environment.get("TGW_CONTEXT_SOURCE_COMMIT", ""),
                    "source_tree": environment.get("TGW_CONTEXT_SOURCE_TREE", ""),
                    "source_root": environment.get("TGW_CONTEXT_SOURCE_ROOT", ""),
                    "catalog": environment.get("TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH", ""),
                    "runtime_entrypoint_sha256": environment.get(
                        "TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256", ""
                    ),
                    "runtime_entrypoint": environment.get(
                        "TGW_CONTEXT_RUNTIME_ENTRYPOINT", ""
                    ),
                    "runtime_module_sha256": environment.get(
                        "TGW_CONTEXT_RUNTIME_MODULE_SHA256", ""
                    ),
                    "runtime_module": environment.get(
                        "TGW_CONTEXT_RUNTIME_MODULE", ""
                    ),
                    "runtime_context_module_sha256": environment.get(
                        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256", ""
                    ),
                    "runtime_context_module": environment.get(
                        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE", ""
                    ),
                    "stable_launcher_sha256": environment.get(
                        "TGW_CONTEXT_STABLE_LAUNCHER_SHA256", ""
                    ),
                    "stable_launcher_path": environment.get(
                        "TGW_CONTEXT_STABLE_LAUNCHER", ""
                    ),
                    "runtime_executable": environment.get(
                        "TGW_CONTEXT_RUNTIME_EXECUTABLE", ""
                    ),
                    "runtime_executable_sha256": environment.get(
                        "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256", ""
                    ),
                    "runtime_executable_device": environment.get(
                        "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE", ""
                    ),
                    "runtime_executable_inode": environment.get(
                        "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE", ""
                    ),
                    "environment_keys": sorted(environment),
                    "environment_sha256": _hash(environment),
                }
            )
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (KeyError, OSError, ValueError) as exc:
            raise ActorFleetError(f"actor Context MCP live-process inspection failed: {process_dir.name}") from exc
    return sorted(inventory, key=lambda item: (item["actor"], item["pid"]))


def _actor_context_mcp_probe(
    actor: str,
    registration_path: Path,
    registration_source: Path,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Exercise the actor's real stdio registration and authoritative tools."""
    command, args, configured_environment = _context_registration(
        registration_path,
        registration_source,
    )

    async def inspect() -> dict[str, Any]:
        actor_home = pwd.getpwnam(actor).pw_dir
        parameters = StdioServerParameters(
            command=command,
            args=args,
            env={
                "HOME": actor_home,
                "LANG": "C.UTF-8",
                "PATH": "/usr/bin:/bin",
                **configured_environment,
            },
        )
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    if names != _CONTEXT_MCP_TOOLS:
                        raise ActorFleetError("actor Context MCP tool surface is incomplete")

                    async def call(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
                        result = await session.call_tool(name, dict(arguments))
                        if result.isError or len(result.content) != 1 or not hasattr(result.content[0], "text"):
                            raise ActorFleetError(f"actor Context MCP call failed: {name}")
                        try:
                            value = json.loads(result.content[0].text)
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ActorFleetError(f"actor Context MCP result is invalid: {name}") from exc
                        if not isinstance(value, dict) or value.get("ok") is False:
                            raise ActorFleetError(f"actor Context MCP result held: {name}")
                        return value

                    status = await call("tgw_context_status", {})
                    onboarding = await call("tgw_context_onboarding", {"actor": actor})
                    bundle = await call(
                        "tgw_context_bundle",
                        {
                            "task": "verify the materialized actor Context MCP binding",
                            "receiver": actor,
                            "limit": 12,
                        },
                    )
                    current_sources: dict[str, dict[str, Any]] = {}
                    for path in _CURRENT_PLAN_SOURCE_PATHS:
                        chunks: list[dict[str, Any]] = []
                        start_line = 1
                        for _page in range(32):
                            chunk = await call(
                                "tgw_context_plan_source",
                                {
                                    "path": path,
                                    "authority": "current-plan",
                                    "start_line": start_line,
                                    "max_lines": 250,
                                },
                            )
                            chunks.append(chunk)
                            total_lines = chunk.get("total_lines")
                            end_line = chunk.get("end_line")
                            if (
                                not isinstance(total_lines, int)
                                or not isinstance(end_line, int)
                                or chunk.get("start_line") != start_line
                                or end_line < start_line - 1
                                or end_line > total_lines
                            ):
                                raise ActorFleetError(
                                    "actor Context MCP source pagination differs"
                                )
                            if end_line == total_lines:
                                break
                            start_line = end_line + 1
                        else:
                            raise ActorFleetError(
                                "actor Context MCP source pagination exceeds bound"
                            )
                        current_sources[path] = {
                            "authority": chunks[0].get("authority"),
                            "commit": chunks[0].get("commit"),
                            "tree": chunks[0].get("tree"),
                            "confined_path": chunks[0].get("confined_path"),
                            "blob_sha256": chunks[0].get("blob_sha256"),
                            "bytes": chunks[0].get("bytes"),
                            "total_lines": chunks[0].get("total_lines"),
                            "chunks": chunks,
                            "chunks_sha256": _hash(chunks),
                        }
        revisions = request["revisions"]
        if (
            status.get("plan", {}).get("approved_commit") != revisions["plan"]
            or status.get("plan", {}).get("approved_solution_hash") != revisions["solution"]
            or status.get("plan", {}).get("evidence_head")
            != revisions["evidence_plan"]
            or status.get("plan", {}).get("evidence_tree")
            != revisions["evidence_tree"]
            or status.get("source", {}).get("commit") != revisions["source"]
            or status.get("source", {}).get("tree") != revisions["source_tree"]
            or status.get("environment", {}).get("catalog_hash") != revisions["catalog"]
            or onboarding.get("actor") != actor
            or onboarding.get("plan", {}).get("approved_commit") != revisions["plan"]
            or onboarding.get("source", {}).get("commit") != revisions["source"]
            or bundle.get("receiver") != actor
            or bundle.get("status", {}).get("source", {}).get("commit") != revisions["source"]
            or bundle.get("status", {}).get("environment", {}).get("catalog_hash") != revisions["catalog"]
            or any(
                source.get("authority") != "current-plan"
                or source.get("commit")
                != status.get("plan", {}).get("evidence_head")
                or source.get("tree")
                != status.get("plan", {}).get("evidence_tree")
                or source.get("confined_path") != path
                or source.get("blob_sha256")
                != revisions["current_plan_sources"][path]
                or not isinstance(source.get("chunks"), list)
                or not source["chunks"]
                or source.get("chunks_sha256") != _hash(source["chunks"])
                or any(
                    chunk.get("authority") != source["authority"]
                    or chunk.get("commit") != source["commit"]
                    or chunk.get("tree") != source["tree"]
                    or chunk.get("confined_path") != path
                    or chunk.get("blob_sha256") != source["blob_sha256"]
                    or chunk.get("bytes") != source["bytes"]
                    or chunk.get("total_lines") != source["total_lines"]
                    or _HASH.fullmatch(
                        str(chunk.get("content_sha256", ""))
                    ) is None
                    or not isinstance(chunk.get("content"), str)
                    or (
                        "sha256:" + hashlib.sha256(
                            chunk.get("content", "").encode()
                        ).hexdigest()
                    ) != chunk.get("content_sha256")
                    for chunk in source["chunks"]
                )
                or source["chunks"][0].get("start_line") != 1
                or source["chunks"][-1].get("end_line")
                != source.get("total_lines")
                or any(
                    later.get("start_line") != earlier.get("end_line") + 1
                    for earlier, later in zip(
                        source["chunks"], source["chunks"][1:], strict=False
                    )
                )
                for path, source in current_sources.items()
            )
        ):
            raise ActorFleetError("actor Context MCP returned mixed revision bindings")
        proof = {
            "schema": "tgw-actor-context-mcp-proof/v1",
            "status": "PASS",
            "actor": actor,
            "tools": sorted(names),
            "plan": revisions["plan"],
            "solution": revisions["solution"],
            "evidence_plan": revisions["evidence_plan"],
            "evidence_tree": revisions["evidence_tree"],
            "source": revisions["source"],
            "source_tree": revisions["source_tree"],
            "catalog": revisions["catalog"],
            "onboarding_bundle_sha256": onboarding.get("bundle_sha256"),
            "task_bundle_sha256": bundle.get("bundle_sha256"),
            "current_plan_sources_sha256": _hash(current_sources),
            "current_plan_source_identities_sha256": _hash(
                revisions["current_plan_sources"]
            ),
        }
        return {**proof, "proof_hash": _hash(proof)}

    try:
        return anyio.run(inspect)
    except ActorFleetError:
        raise
    except Exception as exc:
        raise ActorFleetError("actor Context MCP positive fixture failed") from exc


def _actor_verification_payload(
    actor: str,
    request: Mapping[str, Any],
    bindings: list[Mapping[str, Any]],
    bundle: Mapping[str, Any],
    contract: Mapping[str, Any],
    context_probe: Callable[[str, Path, Path, Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify materialized inputs in a fresh process already running as the actor."""
    try:
        for binding in bindings:
            destination, source = Path(str(binding["destination"])), Path(str(binding["source"]))
            if binding.get("kind") == "mcp" and binding.get("materialization") in {
                "claude-user-json", "codex-user-toml", "deepseek-patch-yaml",
            }:
                _context_registration(destination, source)
            elif not destination.is_symlink() or destination.resolve(strict=False) != source:
                raise ActorFleetError("actor contract binding changed")
            if _binding_digest(source) != binding["sha256"]:
                raise ActorFleetError("actor contract binding content changed")
        by_kind: dict[str, dict[str, Mapping[str, Any]]] = {}
        for binding in bindings:
            kind = str(binding["kind"])
            identity = (
                str(binding.get("capability", binding["name"]))
                if kind == "skill" else str(binding["name"])
            )
            previous = by_kind.setdefault(kind, {}).get(identity)
            if (
                previous is not None
                and previous.get("sha256") != binding.get("sha256")
            ):
                raise ActorFleetError(
                    f"actor capability materializations differ: {actor}:{identity}"
                )
            by_kind[kind][identity] = binding
        mcp_bindings = [
            {
                "endpoint": binding.get("endpoint", name),
                "source_sha256": binding["sha256"],
                "destination": binding["destination"],
            }
            for name, binding in sorted(by_kind.get("mcp", {}).items())
        ]
        instruction_bindings = {
            name: {
                "path": str(binding["destination"]),
                "sha256": str(binding["sha256"]),
            }
            for name, binding in sorted(by_kind.get("instruction", {}).items())
        }
        environment_binding = by_kind.get("environment", {}).get("environment-catalog")
        bootstrap_binding = by_kind.get("bootstrap", {}).get("bootstrap-receipt")
        if environment_binding is None or bootstrap_binding is None:
            raise ActorFleetError("actor environment or bootstrap binding is missing")
        environment = _read_json(Path(str(environment_binding["source"])), "actor environment catalog")
        bootstrap = _read_json(Path(str(bootstrap_binding["source"])), "actor bootstrap receipt")
        unsigned_bootstrap = dict(bootstrap)
        claimed_bootstrap = unsigned_bootstrap.pop("receipt_hash", None)
        local = contract.get("local") if isinstance(contract.get("local"), Mapping) else {}
        specification = bundle["actors"][actor]
        profile = environment.get("profiles", {}).get(contract.get("profile"), {})
        actor_declaration = environment.get("actors", {}).get(actor, {})
        if (
            _hash(environment) != contract.get("catalog_hash")
            or claimed_bootstrap != _hash(unsigned_bootstrap)
            or bootstrap.get("status") != "READY"
            or bootstrap.get("actor") != actor
            or bootstrap.get("generation") != request["successor_generation"]
            or bootstrap.get("catalog_hash") != request["revisions"]["catalog"]
            or bootstrap.get("plan")
            != {
                "commit": request["revisions"]["plan"],
                "solution_hash": request["revisions"]["solution"],
            }
            or bootstrap.get("code_graph", {}).get("commit") != request["revisions"]["source"]
            or bootstrap.get("launcher") != local.get("launcher")
            or bootstrap.get("skills") != local.get("skills")
            or bootstrap.get("hooks") != local.get("hooks")
            or bootstrap.get("mcp") != local.get("mcp")
            or bootstrap.get("instructions", {}) != instruction_bindings
            or contract.get("plan")
            != {
                "commit": request["revisions"]["plan"],
                "solution_hash": request["revisions"]["solution"],
            }
            or contract.get("code_graph", {}).get("commit") != request["revisions"]["source"]
            or set(by_kind.get("skill", {})) != set(local.get("skills", {}))
            or set(by_kind.get("hook", {})) != set(local.get("hooks", {}))
            or {
                str(binding.get("endpoint", name))
                for name, binding in by_kind.get("mcp", {}).items()
            } != set(local.get("mcp", {}).get("endpoints", []))
            or _hash(mcp_bindings) != local.get("mcp", {}).get("binding_hash")
            or by_kind.get("launcher", {}).get("launcher", {}).get("destination")
            != local.get("launcher", {}).get("path")
            or by_kind.get("launcher", {}).get("launcher", {}).get("sha256")
            != local.get("launcher", {}).get("sha256")
            or by_kind.get("bootstrap", {}).get("bootstrap-receipt", {}).get("sha256")
            != local.get("bootstrap_receipt_hash")
            or set(instruction_bindings) != {"agent-entry-point"}
            or actor_declaration.get("enabled") is not True
            or contract.get("profile") not in actor_declaration.get("permitted_profiles", [])
            or profile.get("state") != "ready-for-preflight"
        ):
            raise ActorFleetError("actor startup binding is stale or mixed")
        context_binding = by_kind.get("mcp", {}).get("tgw-context")
        if context_binding is None:
            raise ActorFleetError("actor Context MCP binding is missing")
        primary_command, primary_args, primary_environment = _context_registration(
            Path(str(context_binding["destination"])),
            Path(str(context_binding["source"])),
        )
        primary_semantic_hash = _hash(
            {
                "endpoint": "tgw-context",
                "command": primary_command,
                "args": primary_args,
                "env": primary_environment,
            }
        )
        mcp_proof = dict(
            context_probe(
                actor,
                Path(str(context_binding["destination"])),
                Path(str(context_binding["source"])),
                request,
            )
        )
        if mcp_proof.get("status") != "PASS" or mcp_proof.get("actor") != actor:
            raise ActorFleetError("actor Context MCP positive fixture failed")
        return {
            "status": "PASS",
            "uid": os.geteuid(),
            "plan": request["revisions"]["plan"],
            "solution": request["revisions"]["solution"],
            "source": request["revisions"]["source"],
            "catalog": request["revisions"]["catalog"],
            "generation": request["successor_generation"],
            "profile": contract["profile"],
            "required_capabilities": sorted(profile.get("broker_capabilities", [])),
            "instruction_entry_point_path": instruction_bindings[
                "agent-entry-point"
            ]["path"],
            "instruction_entry_point_sha256": instruction_bindings[
                "agent-entry-point"
            ]["sha256"],
            "context_mcp_proof": mcp_proof,
            "primary_real_store_semantic_sha256": primary_semantic_hash,
            "project": specification["project"],
        }
    except Exception as exc:
        return {"status": "FAIL", "reason": str(exc)}


def _actor_verification_worker_main() -> int:
    try:
        raw = sys.stdin.buffer.read(_ACTOR_VERIFICATION_MAX_INPUT + 1)
        if len(raw) > _ACTOR_VERIFICATION_MAX_INPUT:
            raise ActorFleetError("actor verification worker input is too large")
        value = json.loads(raw)
        if not isinstance(value, Mapping) or set(value) != {
            "schema", "actor", "request", "bindings", "bundle", "contract",
        } or value.get("schema") != "tgw-actor-verification-worker-input/v1":
            raise ActorFleetError("actor verification worker input is invalid")
        request = _request(value["request"])
        actor = str(value["actor"])
        if actor not in request["actors"] or not isinstance(value["bindings"], list):
            raise ActorFleetError("actor verification worker binding is invalid")
        proof = _actor_verification_payload(
            actor,
            request,
            list(value["bindings"]),
            value["bundle"],
            value["contract"],
            _actor_context_mcp_probe,
        )
    except Exception as exc:
        proof = {"status": "FAIL", "reason": str(exc)}
    sys.stdout.buffer.write(_canonical(proof))
    return 0 if proof.get("status") == "PASS" else 1


def _validate_actor_verification_proof(
    proof: Mapping[str, Any],
    *,
    actor: str,
    uid: int,
    request: Mapping[str, Any],
    bundle: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    if not isinstance(proof, Mapping):
        raise ActorFleetError("actor verification proof is invalid")
    fields = {
        "status", "uid", "plan", "solution", "source", "catalog", "generation",
        "profile", "required_capabilities", "context_mcp_proof", "project",
        "primary_real_store_semantic_sha256", "instruction_entry_point_path",
        "instruction_entry_point_sha256",
    }
    specification = bundle["actors"][actor]
    instruction_entries = [
        item
        for item in specification["bindings"]
        if item.get("kind") == "instruction"
        and item.get("name") == "agent-entry-point"
    ]
    if len(instruction_entries) != 1:
        raise ActorFleetError("actor instruction entry point is missing")
    expected_instruction = instruction_entries[0]
    environment_binding = next(
        item for item in specification["bindings"]
        if item["kind"] == "environment" and item["name"] == "environment-catalog"
    )
    environment = _read_json(Path(str(environment_binding["source"])), "actor environment catalog")
    expected_capabilities = sorted(
        environment.get("profiles", {}).get(contract.get("profile"), {}).get("broker_capabilities", [])
    )
    mcp_proof = proof.get("context_mcp_proof")
    mcp_fields = {
        "schema", "status", "actor", "tools", "plan", "solution", "source", "catalog",
        "onboarding_bundle_sha256", "task_bundle_sha256",
        "evidence_plan", "evidence_tree", "source_tree",
        "current_plan_sources_sha256", "current_plan_source_identities_sha256",
        "proof_hash",
    }
    if not isinstance(mcp_proof, Mapping) or set(mcp_proof) != mcp_fields:
        raise ActorFleetError("actor Context MCP proof fields are invalid")
    unsigned_mcp = dict(mcp_proof)
    claimed_mcp_hash = unsigned_mcp.pop("proof_hash")
    revisions = request["revisions"]
    if (
        set(proof) != fields
        or proof.get("status") != "PASS"
        or proof.get("uid") != uid
        or proof.get("plan") != revisions["plan"]
        or proof.get("solution") != revisions["solution"]
        or proof.get("source") != revisions["source"]
        or proof.get("catalog") != revisions["catalog"]
        or proof.get("generation") != request["successor_generation"]
        or proof.get("profile") != contract.get("profile")
        or proof.get("required_capabilities") != expected_capabilities
        or proof.get("project") != specification.get("project")
        or proof.get("instruction_entry_point_path")
        != expected_instruction.get("destination")
        or proof.get("instruction_entry_point_sha256")
        != expected_instruction.get("sha256")
        or _HASH.fullmatch(
            str(proof.get("instruction_entry_point_sha256", ""))
        ) is None
        or _HASH.fullmatch(
            str(proof.get("primary_real_store_semantic_sha256", ""))
        ) is None
        or mcp_proof.get("schema") != "tgw-actor-context-mcp-proof/v1"
        or mcp_proof.get("status") != "PASS"
        or mcp_proof.get("actor") != actor
        or mcp_proof.get("tools") != sorted(_CONTEXT_MCP_TOOLS)
        or mcp_proof.get("plan") != revisions["plan"]
        or mcp_proof.get("solution") != revisions["solution"]
        or mcp_proof.get("evidence_plan") != revisions["evidence_plan"]
        or mcp_proof.get("evidence_tree") != revisions["evidence_tree"]
        or mcp_proof.get("source") != revisions["source"]
        or mcp_proof.get("source_tree") != revisions["source_tree"]
        or mcp_proof.get("catalog") != revisions["catalog"]
        or any(_HASH.fullmatch(str(mcp_proof.get(name))) is None for name in (
            "onboarding_bundle_sha256", "task_bundle_sha256",
            "current_plan_sources_sha256",
        ))
        or mcp_proof.get("current_plan_source_identities_sha256")
        != _hash(revisions["current_plan_sources"])
        or claimed_mcp_hash != _hash(unsigned_mcp)
    ):
        raise ActorFleetError("actor verification proof differs from expected revisions")


def _atomic(
    path: Path,
    value: Mapping[str, Any],
    *,
    mode: int = 0o600,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    stage = path.with_name(
        f".{path.name}.next-{os.getpid()}-{secrets.token_hex(8)}"
    )
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            if uid is not None or gid is not None:
                os.fchown(
                    handle.fileno(),
                    -1 if uid is None else uid,
                    -1 if gid is None else gid,
                )
            os.fsync(handle.fileno())
        os.replace(stage, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


def _validate_coordinator_preimage_node(
    value: Any,
    *,
    nested: bool,
    entry_count: list[int],
) -> None:
    """Validate one bounded, exact node in the root-private preimage tree."""

    entry_count[0] += 1
    if entry_count[0] > 20_000 or not isinstance(value, Mapping):
        raise ActorFleetError("coordinator preimage entry bound differs")
    fields = {"kind", "mode", "uid", "gid", "nlink", "payload"}
    if nested:
        fields.add("relative_path")
    if set(value) != fields:
        raise ActorFleetError("coordinator preimage node is invalid")
    if nested:
        relative = value.get("relative_path")
        if (
            not isinstance(relative, str)
            or relative in {"", ".", ".."}
            or "/" in relative
            or "\0" in relative
            or len(os.fsencode(relative)) > 255
        ):
            raise ActorFleetError("coordinator preimage relative path is unsafe")
    kind = value.get("kind")
    if kind not in {"absent", "file", "symlink", "directory"}:
        raise ActorFleetError("coordinator preimage kind is invalid")
    metadata = tuple(value.get(name) for name in ("mode", "uid", "gid", "nlink"))
    if kind == "absent":
        if metadata != (None, None, None, None) or value.get("payload") != {}:
            raise ActorFleetError("coordinator absent preimage is invalid")
        return
    mode, uid, gid, nlink = metadata
    if (
        any(isinstance(item, bool) or not isinstance(item, int) for item in metadata)
        or not 0 <= mode <= 0o7777
        or uid < 0
        or gid < 0
        or nlink < 1
    ):
        raise ActorFleetError("coordinator preimage metadata is invalid")
    payload = value.get("payload")
    if kind == "file":
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"encoding", "content"}
            or payload.get("encoding") != "base64"
            or not isinstance(payload.get("content"), str)
        ):
            raise ActorFleetError("coordinator file preimage is invalid")
        try:
            raw = base64.b64decode(payload["content"], validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ActorFleetError("coordinator file preimage is invalid") from exc
        if (
            len(raw) > 4 * 1024 * 1024
            or base64.b64encode(raw).decode("ascii") != payload["content"]
        ):
            raise ActorFleetError("coordinator file preimage is not canonical")
        return
    if kind == "symlink":
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"target"}
            or not isinstance(payload.get("target"), str)
            or not payload["target"]
            or "\0" in payload["target"]
        ):
            raise ActorFleetError("coordinator symlink preimage is invalid")
        return
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"coverage", "entries"}
        or payload.get("coverage") not in {"recursive", "metadata-only"}
        or not isinstance(payload.get("entries"), list)
        or (
            payload.get("coverage") == "metadata-only"
            and payload.get("entries") != []
        )
    ):
        raise ActorFleetError("coordinator directory preimage is invalid")
    names: set[str] = set()
    for child in payload["entries"]:
        _validate_coordinator_preimage_node(
            child, nested=True, entry_count=entry_count
        )
        name = str(child["relative_path"])
        if name in names:
            raise ActorFleetError("coordinator directory entry is duplicated")
        names.add(name)


def _coordinator_file_preimage_bytes(
    value: Mapping[str, Any], label: str
) -> bytes:
    """Decode one already-validated private file preimage."""
    payload = value.get("payload")
    if value.get("kind") != "file" or not isinstance(payload, Mapping):
        raise ActorFleetError(f"{label} preimage is unavailable")
    try:
        return base64.b64decode(str(payload.get("content", "")), validate=True)
    except binascii.Error as exc:
        raise ActorFleetError(f"{label} preimage is invalid") from exc


def _coordinator_file_preimage_json(
    value: Mapping[str, Any], label: str
) -> dict[str, Any]:
    """Decode one already-validated private file preimage as bounded JSON."""

    try:
        decoded = json.loads(_coordinator_file_preimage_bytes(value, label))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActorFleetError(f"{label} preimage is invalid") from exc
    if not isinstance(decoded, dict):
        raise ActorFleetError(f"{label} preimage is invalid")
    return decoded


def _request(value: Any) -> dict[str, Any]:
    fields = {
        "schema",
        "transaction_id",
        "idempotency_key",
        "predecessor_generation",
        "successor_generation",
        "revisions",
        "actors",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != "tgw-w18-fleet-refresh-request/v1":
        raise ActorFleetError("actor fleet request is invalid")
    revisions = value.get("revisions")
    required = {
        "plan", "solution", "evidence_plan", "evidence_tree", "source",
        "source_tree", "current_plan_sources", "catalog", "bootstrap",
        "broker_policy", "review", "admission",
    }
    if not isinstance(revisions, Mapping) or set(revisions) != required:
        raise ActorFleetError("actor fleet revisions are incomplete")
    if any(
        _COMMIT.fullmatch(str(revisions[name])) is None
        for name in ("plan", "evidence_plan", "evidence_tree", "source", "source_tree")
    ):
        raise ActorFleetError("actor fleet Git revisions are invalid")
    if any(
        not isinstance(revisions[name], str)
        or _HASH.fullmatch(revisions[name]) is None
        for name in required
        - {
            "plan", "evidence_plan", "evidence_tree", "source", "source_tree",
            "current_plan_sources",
        }
    ):
        raise ActorFleetError("actor fleet content revisions are invalid")
    current_plan_sources = revisions.get("current_plan_sources")
    if (
        not isinstance(current_plan_sources, Mapping)
        or set(current_plan_sources) != set(_CURRENT_PLAN_SOURCE_PATHS)
        or any(
            _HASH.fullmatch(str(current_plan_sources[path])) is None
            for path in _CURRENT_PLAN_SOURCE_PATHS
        )
    ):
        raise ActorFleetError("actor fleet current Plan source revisions are invalid")
    actors = value.get("actors")
    if not isinstance(actors, list) or not actors or actors != sorted(set(actors)) or any(not isinstance(actor, str) or not actor for actor in actors):
        raise ActorFleetError("actor fleet actor set is invalid")
    return dict(value)


class ActorFleetProvider:
    """Materialize and verify one admitted actor generation on ``tgw-lib``."""

    def __init__(
        self,
        value: Mapping[str, Any],
        *,
        service_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        materializer_loader: Callable[[Path], Any] | None = None,
        current_time: Callable[[], datetime] | None = None,
        actor_context_probe: Callable[[str, Path, Path, Mapping[str, Any]], Mapping[str, Any]] | None = None,
        actor_context_process_inventory: Callable[[list[str]], list[dict[str, Any]]] | None = None,
        coordinator_transaction_root: Path = _DEFAULT_COORDINATOR_TRANSACTION_ROOT,
    ):
        required = {
            "schema",
            "token_sha256",
            "state_root",
            "release_root",
            "admission_root",
            "actor_generation_root",
            "admission_public_key",
            "contract_public_key",
            "systemctl_path",
            "managed_services",
            "quiescence_units",
            "actor_group",
            "attempt_workspace_root",
            "attempt_cache_root",
            "actor_cache_root",
            "startup_binding_root",
        }
        if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != "tgw-actor-fleet-provider/v1":
            raise ActorFleetError("actor fleet provider configuration is invalid")
        if not isinstance(value.get("token_sha256"), str) or _HASH.fullmatch(value["token_sha256"]) is None:
            raise ActorFleetError("actor fleet provider token binding is invalid")
        self.token_sha256 = value["token_sha256"]
        try:
            actor_group = grp.getgrnam(str(value["actor_group"]))
        except KeyError as exc:
            raise ActorFleetError("actor group is unavailable") from exc
        self.state_root = _directory(value["state_root"], "actor fleet state root")
        state_root_state = self.state_root.stat(follow_symlinks=False)
        if (
            state_root_state.st_mode & 0o022
            or stat.S_IMODE(state_root_state.st_mode) != 0o750
            or state_root_state.st_gid != actor_group.gr_gid
            or (
                os.geteuid() == 0
                and (
                    state_root_state.st_uid != 0
                    or self.state_root != _DEFAULT_ACTOR_FLEET_STATE_ROOT
                )
            )
        ):
            raise ActorFleetError("actor fleet state root is not root protected")
        if os.geteuid() == 0:
            for ancestor, expected_mode in {
                Path("/var"): None,
                Path("/var/lib"): None,
                Path("/var/lib/tgw"): 0o755,
            }.items():
                try:
                    ancestor_state = ancestor.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ActorFleetError(
                        "actor fleet state ancestry is unavailable"
                    ) from exc
                if (
                    ancestor.is_symlink()
                    or not stat.S_ISDIR(ancestor_state.st_mode)
                    or ancestor.resolve(strict=True) != ancestor
                    or ancestor_state.st_uid != 0
                    or ancestor_state.st_mode & 0o022
                    or (
                        expected_mode is not None
                        and stat.S_IMODE(ancestor_state.st_mode)
                        != expected_mode
                    )
                ):
                    raise ActorFleetError(
                        "actor fleet state ancestry is not protected"
                    )
        self.private_state_root = _directory(
            self.state_root / "private", "actor fleet private state root"
        )
        private_state = self.private_state_root.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(private_state.st_mode) != 0o700
            or (
                os.geteuid() == 0
                and (private_state.st_uid != 0 or private_state.st_gid != 0)
            )
        ):
            raise ActorFleetError(
                "actor fleet private state root is not root protected"
            )
        self.release_root = _directory(value["release_root"], "actor release root")
        self.admission_root = _directory(value["admission_root"], "actor admission root")
        self.actor_generation_root = _directory(value["actor_generation_root"], "actor generation root")
        self.admission_public_key = _trusted_public_key(
            value["admission_public_key"],
            "actor release admission public key",
        )
        self.startup_binding_root = _directory(
            value["startup_binding_root"],
            "actor startup binding root",
        )
        startup_root_state = self.startup_binding_root.stat(follow_symlinks=False)
        if startup_root_state.st_mode & 0o022 or (os.geteuid() == 0 and startup_root_state.st_uid != 0):
            raise ActorFleetError("actor startup binding root is not root protected")
        self.actor_group = actor_group.gr_name
        self.actor_group_gid = actor_group.gr_gid
        self.attempt_workspace_root = _shared_attempt_root(
            value["attempt_workspace_root"],
            "actor attempt workspace root",
            actor_group,
        )
        self.attempt_cache_root = _shared_attempt_root(
            value["attempt_cache_root"],
            "actor attempt cache root",
            actor_group,
        )
        self.actor_cache_root = _directory(value["actor_cache_root"], "actor Context MCP cache root")
        actor_cache_state = self.actor_cache_root.stat(follow_symlinks=False)
        if actor_cache_state.st_mode & 0o022 or (os.geteuid() == 0 and actor_cache_state.st_uid != 0):
            raise ActorFleetError("actor Context MCP cache root is not root protected")
        self.systemctl = _regular(value["systemctl_path"], "systemctl executable")
        services = value.get("managed_services")
        if (
            not isinstance(services, list)
            or not services
            or services != sorted(set(services))
            or any(unit not in _MANAGED_SERVICE_ALLOWLIST for unit in services)
        ):
            raise ActorFleetError("managed actor service set is invalid")
        self.services = list(services)
        quiescence = value.get("quiescence_units")
        if (
            not isinstance(quiescence, list)
            or quiescence != sorted(set(quiescence))
            or any(unit not in _MANAGED_SERVICE_ALLOWLIST for unit in quiescence)
        ):
            raise ActorFleetError("actor quiescence unit set is invalid")
        self.quiescence_units = list(quiescence)
        self.contract_public_key, self.contract_public_key_bytes = (
            _contract_public_key(
                value.get("contract_public_key"), "actor contract signer"
            )
        )
        self._run = service_runner or self._run_service
        self._load_materializer = materializer_loader or self._materializer
        self._actor_context_probe = actor_context_probe or _actor_context_mcp_probe
        self._actor_context_process_inventory = actor_context_process_inventory or _actor_context_process_inventory
        self._current_time = current_time or (lambda: datetime.now(timezone.utc))
        self.coordinator_transaction_root = Path(coordinator_transaction_root)

    def authorized(self, authorization: str | None) -> bool:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return False
        return "sha256:" + hashlib.sha256(authorization[7:].encode()).hexdigest() == self.token_sha256

    def _run_service(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(self.systemctl), *arguments], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)

    def _service(self, operation: str, *, expected: str) -> None:
        for unit in self.services:
            result = self._run([operation, unit])
            if result.returncode != 0:
                raise ActorFleetError(f"managed actor service {operation} failed: {unit}")
        for unit in self.services:
            result = self._run(["is-active", unit])
            if result.returncode not in {0, 3} or result.stdout.strip() != expected:
                raise ActorFleetError(f"managed actor service state mismatch: {unit}:{result.stdout.strip()}")

    def _journal_path(self, transaction_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", transaction_id):
            raise ActorFleetError("actor transaction identity is invalid")
        return self.private_state_root / f"{transaction_id}.actor-provider.json"

    def _validated_journal_ledger_link(
        self, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        sequence = journal.get("ledger_sequence")
        record_sha256 = journal.get("ledger_record_sha256")
        link = journal.get("ledger_evidence")
        if (
            not isinstance(sequence, int)
            or sequence < 1
            or _HASH.fullmatch(str(record_sha256 or "")) is None
            or not isinstance(link, Mapping)
        ):
            raise ActorFleetError("actor provider journal ledger link differs")
        path = self.state_root / "generation-ledger" / (
            f"{sequence:012d}-{str(record_sha256).removeprefix('sha256:')}.json"
        )
        try:
            observed = path.stat(follow_symlinks=False)
            entry = _read_json(path, "actor generation ledger segment")
        except OSError as exc:
            raise ActorFleetError(
                "actor provider journal ledger segment is unavailable"
            ) from exc
        unsigned_entry = dict(entry)
        claimed_entry = unsigned_entry.pop("record_sha256", None)
        evidence = entry.get("evidence_receipts")
        if (
            path.is_symlink()
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) != 0o640
            or observed.st_gid != self.actor_group_gid
            or (os.geteuid() == 0 and observed.st_uid != 0)
            or entry.get("schema") != "tgw-generation-ledger-entry/v1"
            or entry.get("record_role") != "PROVIDER_PHASE"
            or entry.get("sequence") != sequence
            or claimed_entry != record_sha256
            or claimed_entry != _hash(unsigned_entry)
            or not isinstance(evidence, Mapping)
        ):
            raise ActorFleetError("actor provider journal ledger segment differs")
        unsigned_evidence = dict(evidence)
        claimed_evidence = unsigned_evidence.pop("evidence_sha256", None)
        expected_link = self._ledger_evidence_link(
            sequence, str(record_sha256), evidence
        )
        if (
            claimed_evidence != _hash(unsigned_evidence)
            or dict(link) != expected_link
        ):
            raise ActorFleetError("actor provider journal ledger evidence differs")
        return expected_link

    def _journal(self, transaction_id: str) -> dict[str, Any]:
        path = self._journal_path(transaction_id)
        if not path.exists() and not path.is_symlink():
            return {
                "schema": "tgw-actor-fleet-journal/v1",
                "transaction_id": transaction_id,
                "status": "NEW",
                "request": None,
                "candidate_release": None,
                "materialization": None,
            }
        if path.is_symlink() or not path.is_file():
            raise ActorFleetError("actor provider journal is unsafe")
        observed = path.stat(follow_symlinks=False)
        if observed.st_nlink != 1 or observed.st_mode & 0o022 or (
            os.geteuid() == 0 and observed.st_uid != 0
        ):
            raise ActorFleetError("actor provider journal is not protected")
        value = _read_json(path, "actor provider journal")
        if (
            value.get("schema") != "tgw-actor-fleet-journal/v1"
            or value.get("transaction_id") != transaction_id
        ):
            raise ActorFleetError("actor provider journal identity differs")
        claimed_payload = value.get("journal_payload_sha256")
        if claimed_payload is not None:
            if claimed_payload != _hash(_journal_payload(value)):
                raise ActorFleetError("actor provider journal payload differs")
            if value.get("ledger_pending") is True:
                ledger = self._append_generation_ledger(value)
                value["ledger_pending"] = False
                value["ledger_sequence"] = ledger["sequence"]
                value["ledger_record_sha256"] = ledger["record_sha256"]
                value["ledger_evidence"] = ledger["evidence_link"]
                _atomic(
                    path, value,
                    uid=0 if os.geteuid() == 0 else None,
                    gid=0 if os.geteuid() == 0 else None,
                )
            elif (
                value.get("ledger_pending") is not False
                or not isinstance(value.get("ledger_sequence"), int)
                or _HASH.fullmatch(str(value.get("ledger_record_sha256", "")))
                is None
            ):
                raise ActorFleetError("actor provider journal ledger link differs")
            else:
                self._validated_journal_ledger_link(value)
        return value

    def _save(
        self,
        journal: Mapping[str, Any],
        *,
        allow_ambiguous: bool = False,
    ) -> None:
        now = self._current_time().astimezone(timezone.utc).isoformat(
            timespec="seconds"
        ).replace("+00:00", "Z")
        value = _journal_payload(journal)
        value.setdefault("created_at", now)
        value["updated_at"] = now
        value["journal_payload_sha256"] = _hash(value)
        value["ledger_pending"] = True
        # The private state is the durable referent.  Persist it before the
        # sanitized provider phase ledger, then repair only its ledger link if
        # a crash lands between these two writes.
        _atomic(
            self._journal_path(str(value["transaction_id"])), value,
            uid=0 if os.geteuid() == 0 else None,
            gid=0 if os.geteuid() == 0 else None,
        )
        ledger = self._append_generation_ledger(value)
        value["ledger_pending"] = False
        value["ledger_sequence"] = ledger["sequence"]
        value["ledger_record_sha256"] = ledger["record_sha256"]
        value["ledger_evidence"] = ledger["evidence_link"]
        _atomic(
            self._journal_path(str(value["transaction_id"])), value,
            uid=0 if os.geteuid() == 0 else None,
            gid=0 if os.geteuid() == 0 else None,
        )
        if isinstance(journal, dict):
            journal.clear()
            journal.update(value)
        projection = self._write_fleet_convergence_projection()
        if projection.get("state") == "AMBIGUOUS" and not allow_ambiguous:
            raise ActorFleetError(
                "actor fleet has unsuperseded nonterminal transactions"
            )

    def _ledger_evidence_receipts(
        self, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Build the bounded, non-secret evidence retained in one ledger segment."""

        transaction_id = str(journal.get("transaction_id", ""))
        coordinator = journal.get("coordinator_binding")
        opening = (
            coordinator.get("coordinator_opening")
            if isinstance(coordinator, Mapping) else None
        )
        review = (
            opening.get("review_receipt")
            if isinstance(opening, Mapping) else None
        )
        admission = (
            opening.get("admission_receipt")
            if isinstance(opening, Mapping) else None
        )
        if not isinstance(review, Mapping) or not isinstance(admission, Mapping):
            raise ActorFleetError("actor provider ledger evidence is unavailable")

        actor_receipts: list[dict[str, Any]] = []
        actor_verifications = journal.get("actor_verifications", {})
        if not isinstance(actor_verifications, Mapping):
            raise ActorFleetError("actor provider verification evidence is invalid")
        for actor, verification in sorted(actor_verifications.items()):
            proof = (
                verification.get("proof")
                if isinstance(verification, Mapping) else None
            )
            if (
                actor not in {"claude", "codex", "deepseek"}
                or not isinstance(verification, Mapping)
                or not isinstance(proof, Mapping)
                or verification.get("actor_proof_hash") != _hash(proof)
                or verification.get("primary_real_store_semantic_sha256")
                != proof.get("primary_real_store_semantic_sha256")
                or verification.get("instruction_entry_point_path")
                != proof.get("instruction_entry_point_path")
                or verification.get("instruction_entry_point_sha256")
                != proof.get("instruction_entry_point_sha256")
                or _HASH.fullmatch(
                    str(verification.get("context_mcp_proof_hash", ""))
                )
                is None
                or _HASH.fullmatch(
                    str(verification.get("instruction_entry_point_sha256", ""))
                )
                is None
                or not isinstance(verification.get("verified_at"), str)
            ):
                raise ActorFleetError(
                    "actor provider verification evidence differs"
                )
            receipt_body = {
                "schema": "tgw-actor-verification-receipt/v1",
                "transaction_id": transaction_id,
                "actor": actor,
                "proof": dict(proof),
                "actor_proof_hash": verification["actor_proof_hash"],
                "context_mcp_proof_hash": verification[
                    "context_mcp_proof_hash"
                ],
                "primary_real_store_semantic_sha256": verification[
                    "primary_real_store_semantic_sha256"
                ],
                "instruction_entry_point_path": verification[
                    "instruction_entry_point_path"
                ],
                "instruction_entry_point_sha256": verification[
                    "instruction_entry_point_sha256"
                ],
                "live_context_state": verification.get("live_context_state"),
                "verified_at": verification["verified_at"],
            }
            actor_receipts.append(
                {**receipt_body, "receipt_sha256": _hash(receipt_body)}
            )

        rebind = journal.get("context_rebind")
        confirmations = (
            rebind.get("confirmations", {})
            if isinstance(rebind, Mapping) else {}
        )
        if not isinstance(confirmations, Mapping):
            raise ActorFleetError("actor Context confirmation evidence is invalid")
        confirmation_receipts: list[dict[str, Any]] = []
        for obligation_id, confirmation in sorted(confirmations.items()):
            if not isinstance(confirmation, Mapping):
                raise ActorFleetError(
                    "actor Context confirmation evidence is invalid"
                )
            unsigned_confirmation = dict(confirmation)
            confirmation_hash = unsigned_confirmation.pop(
                "confirmation_hash", None
            )
            if (
                str(confirmation.get("obligation_id")) != str(obligation_id)
                or confirmation.get("transaction_id") != transaction_id
                or confirmation_hash != _hash(unsigned_confirmation)
            ):
                raise ActorFleetError(
                    "actor Context confirmation evidence differs"
                )
            confirmation_receipts.append(dict(confirmation))

        transition_history = (
            rebind.get("parent_transition_history", [])
            if isinstance(rebind, Mapping) else []
        )
        if not isinstance(transition_history, list):
            raise ActorFleetError("actor parent transition evidence is invalid")
        transition_receipts: list[dict[str, Any]] = []
        for transition in transition_history:
            if not isinstance(transition, Mapping):
                raise ActorFleetError("actor parent transition evidence is invalid")
            unsigned_transition = dict(transition)
            provider_hash = unsigned_transition.pop(
                "provider_record_sha256", None
            )
            if (
                transition.get("transaction_id") != transaction_id
                or provider_hash != _hash(unsigned_transition)
            ):
                raise ActorFleetError("actor parent transition evidence differs")
            transition_receipts.append(dict(transition))

        cold_receipt: dict[str, Any] | None = None
        action_receipt: dict[str, Any] | None = None
        coordinator_root = self.coordinator_transaction_root / transaction_id
        cold_path = coordinator_root / "cold-continuity-receipt.json"
        if cold_path.exists() or cold_path.is_symlink():
            cold_receipt = self._coordinator_private_receipt(
                transaction_id,
                "cold-continuity-receipt.json",
                "cold continuity receipt",
            )
            unsigned_cold = dict(cold_receipt)
            cold_hash = unsigned_cold.pop("receipt_sha256", None)
            if (
                set(cold_receipt) != {
                    "schema", "status", "transaction_id", "actor",
                    "actor_generation", "proof_sha256", "transcript_sha256",
                    "workspace_peak_bytes", "completed_at", "receipt_sha256",
                }
                or cold_receipt.get("schema")
                != "tgw-context-cold-handoff-receipt/v1"
                or cold_receipt.get("status") != "PASS"
                or cold_receipt.get("transaction_id") != transaction_id
                or cold_receipt.get("actor") != "claude"
                or cold_hash != _hash(unsigned_cold)
            ):
                raise ActorFleetError("cold continuity ledger evidence differs")
        action_path = coordinator_root / "deepseek-service-action.json"
        if action_path.exists() or action_path.is_symlink():
            action_receipt = self._coordinator_private_receipt(
                transaction_id,
                "deepseek-service-action.json",
                "DeepSeek service action receipt",
            )
            unsigned_action = dict(action_receipt)
            action_hash = unsigned_action.pop("action_receipt_sha256", None)
            if (
                set(action_receipt) != {
                    "schema", "status", "transaction_id", "service_unit",
                    "unit_path", "unit_sha256", "baseline_sha256",
                    "lifecycle_action", "classification",
                    "linger_enabled_by_transaction",
                    "old_parent_identity_hash", "new_parent_identity_hash",
                    "cold_handoff_receipt_sha256", "completed_at",
                    "action_receipt_sha256",
                }
                or action_receipt.get("schema")
                != "tgw-deepseek-managed-service-action/v1"
                or action_receipt.get("status") != "PASS"
                or action_receipt.get("transaction_id") != transaction_id
                or action_hash != _hash(unsigned_action)
                or not isinstance(cold_receipt, Mapping)
                or action_receipt.get("cold_handoff_receipt_sha256")
                != cold_receipt.get("receipt_sha256")
            ):
                raise ActorFleetError(
                    "DeepSeek service ledger evidence differs"
                )

        terminal_receipt: dict[str, Any] | None = None
        status = journal.get("status")
        if status in {"VERIFIED", "ROLLED_BACK"}:
            request = journal.get("request")
            latest = (
                rebind.get("latest") if isinstance(rebind, Mapping) else None
            )
            if not isinstance(request, Mapping) or not isinstance(latest, Mapping):
                raise ActorFleetError(
                    "actor provider terminal convergence evidence is unavailable"
                )
            pending = latest.get("pending")
            if pending != []:
                raise ActorFleetError(
                    "actor provider terminal convergence remains pending"
                )
            terminal_body = {
                "schema": "tgw-provider-terminal-convergence-receipt/v1",
                "transaction_id": transaction_id,
                "direction": (
                    rebind.get("direction")
                    if isinstance(rebind, Mapping) else "successor"
                ),
                "status": status,
                "predecessor_generation": request.get("predecessor_generation"),
                "successor_generation": request.get("successor_generation"),
                "revisions_sha256": _hash(request.get("revisions")),
                "obligations_sha256": _hash(rebind.get("obligations")),
                "dispositions_sha256": _hash(latest.get("dispositions")),
                "actor_verification_receipt_hashes": [
                    item["receipt_sha256"] for item in actor_receipts
                ],
                "client_confirmation_hashes": [
                    item["confirmation_hash"] for item in confirmation_receipts
                ],
                "parent_transition_hashes": [
                    item["provider_record_sha256"]
                    for item in transition_receipts
                ],
                "rollback_registration_probes_sha256": (
                    _hash(journal.get("rollback_registration_probes"))
                    if isinstance(
                        journal.get("rollback_registration_probes"), list
                    )
                    else None
                ),
                "completed_at": journal.get("updated_at"),
            }
            terminal_receipt = {
                **terminal_body,
                "receipt_sha256": _hash(terminal_body),
            }

        body = {
            "schema": "tgw-provider-ledger-evidence/v1",
            "transaction_id": transaction_id,
            "provider_status": status,
            "review_receipt": dict(review),
            "admission_receipt": dict(admission),
            "actor_verification_receipts": actor_receipts,
            "client_confirmation_receipts": confirmation_receipts,
            "parent_transition_receipts": transition_receipts,
            "cold_handoff_receipt": cold_receipt,
            "managed_service_action_receipt": action_receipt,
            "terminal_convergence_receipt": terminal_receipt,
        }
        if len(_canonical(body)) > 4 * 1024 * 1024:
            raise ActorFleetError("actor provider ledger evidence exceeds its bound")
        return {**body, "evidence_sha256": _hash(body)}

    @staticmethod
    def _ledger_evidence_link(
        sequence: int, record_sha256: str, evidence: Mapping[str, Any]
    ) -> dict[str, Any]:
        actor_receipts = evidence.get("actor_verification_receipts", [])
        confirmations = evidence.get("client_confirmation_receipts", [])
        transitions = evidence.get("parent_transition_receipts", [])
        review = evidence.get("review_receipt")
        admission = evidence.get("admission_receipt")
        cold = evidence.get("cold_handoff_receipt")
        action = evidence.get("managed_service_action_receipt")
        terminal = evidence.get("terminal_convergence_receipt")
        if (
            not isinstance(actor_receipts, list)
            or not isinstance(confirmations, list)
            or not isinstance(transitions, list)
            or not isinstance(review, Mapping)
            or not isinstance(admission, Mapping)
        ):
            raise ActorFleetError("actor provider ledger evidence link is invalid")
        body = {
            "schema": "tgw-provider-ledger-evidence-link/v1",
            "sequence": sequence,
            "record_sha256": record_sha256,
            "evidence_sha256": evidence.get("evidence_sha256"),
            "review_receipt_sha256": review.get("receipt_hash"),
            "admission_receipt_sha256": admission.get("receipt_hash"),
            "actor_verification_receipt_hashes": {
                str(item.get("actor")): item.get("receipt_sha256")
                for item in actor_receipts if isinstance(item, Mapping)
            },
            "client_confirmation_hashes": sorted(
                str(item.get("confirmation_hash"))
                for item in confirmations if isinstance(item, Mapping)
            ),
            "parent_transition_hashes": sorted(
                str(item.get("provider_record_sha256"))
                for item in transitions if isinstance(item, Mapping)
            ),
            "cold_handoff_receipt_sha256": (
                cold.get("receipt_sha256")
                if isinstance(cold, Mapping) else None
            ),
            "managed_service_action_receipt_sha256": (
                action.get("action_receipt_sha256")
                if isinstance(action, Mapping) else None
            ),
            "terminal_convergence_receipt_sha256": (
                terminal.get("receipt_sha256")
                if isinstance(terminal, Mapping) else None
            ),
        }
        if (
            _HASH.fullmatch(str(body["record_sha256"])) is None
            or _HASH.fullmatch(str(body["evidence_sha256"])) is None
            or _HASH.fullmatch(str(body["review_receipt_sha256"])) is None
            or _HASH.fullmatch(str(body["admission_receipt_sha256"])) is None
        ):
            raise ActorFleetError("actor provider ledger evidence link differs")
        return {**body, "link_sha256": _hash(body)}

    def _append_generation_ledger(
        self, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Append one immutable hash-linked lifecycle record before effects."""
        root = self.state_root / "generation-ledger"
        if not root.exists():
            root.mkdir(mode=0o750)
            os.chmod(root, 0o750)
            if os.geteuid() == 0:
                os.chown(root, 0, self.actor_group_gid)
            directory_fd = os.open(
                self.state_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if root.is_symlink() or not root.is_dir():
            raise ActorFleetError("actor generation ledger root is unsafe")
        root_state = root.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(root_state.st_mode) != 0o750
            or root_state.st_gid != self.actor_group_gid
            or (
                os.geteuid() == 0 and root_state.st_uid != 0
            )
        ):
            raise ActorFleetError("actor generation ledger root is not protected")
        lock_path = self.state_root / ".generation-ledger.lock"
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            segments = sorted(root.glob("*.json"))
            if len(segments) > 100_000:
                raise ActorFleetError("actor generation ledger exceeds its bound")
            previous_hash: str | None = None
            expected_sequence = 1
            entries: list[dict[str, Any]] = []
            for segment in segments:
                if segment.is_symlink() or not segment.is_file():
                    raise ActorFleetError("actor generation ledger segment is unsafe")
                segment_state = segment.stat(follow_symlinks=False)
                if (
                    segment_state.st_nlink != 1
                    or stat.S_IMODE(segment_state.st_mode) != 0o640
                    or segment_state.st_gid != self.actor_group_gid
                    or (os.geteuid() == 0 and segment_state.st_uid != 0)
                ):
                    raise ActorFleetError(
                        "actor generation ledger segment is not protected"
                    )
                value = _read_json(segment, "actor generation ledger segment")
                unsigned = dict(value)
                claimed = unsigned.pop("record_sha256", None)
                if (
                    value.get("schema") != "tgw-generation-ledger-entry/v1"
                    or value.get("sequence") != expected_sequence
                    or value.get("previous_record_sha256") != previous_hash
                    or claimed != _hash(unsigned)
                    or segment.name
                    != f"{expected_sequence:012d}-{str(claimed).removeprefix('sha256:')}.json"
                ):
                    raise ActorFleetError("actor generation ledger chain differs")
                previous_hash = claimed
                expected_sequence += 1
                entries.append(value)
            request = journal.get("request")
            rebind = journal.get("context_rebind")
            if re.fullmatch(
                r"[0-9a-f]{64}", str(journal.get("private_nonce", ""))
            ) is None:
                raise ActorFleetError("actor provider private journal salt differs")
            latest = (
                rebind.get("latest") if isinstance(rebind, Mapping)
                and isinstance(rebind.get("latest"), Mapping) else {}
            )
            try:
                boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                    encoding="utf-8"
                ).strip()
            except OSError as exc:
                raise ActorFleetError(
                    "actor generation ledger boot identity is unavailable"
                ) from exc
            revisions = (
                request.get("revisions") if isinstance(request, Mapping)
                and isinstance(request.get("revisions"), Mapping) else {}
            )
            actor_verifications = (
                journal.get("actor_verifications", {})
                if isinstance(journal.get("actor_verifications"), Mapping) else {}
            )
            materialization = journal.get("materialization")
            materialization_evidence = None
            if isinstance(materialization, Mapping):
                raw_bindings = materialization.get("bindings")
                if not isinstance(raw_bindings, list):
                    raise ActorFleetError(
                        "actor materialization ledger evidence is invalid"
                    )
                materialization_evidence = {
                    "schema": materialization.get("schema"),
                    "generation": materialization.get("generation"),
                    "mode": materialization.get("mode"),
                    "status": materialization.get("status"),
                    "actors": materialization.get("actors"),
                    "bindings": [
                        {
                            name: binding.get(name)
                            for name in (
                                "actor", "kind", "name", "capability",
                                "endpoint", "source", "destination", "sha256",
                                "materialization", "status",
                            )
                            if name in binding
                        }
                        for binding in raw_bindings
                        if isinstance(binding, Mapping)
                    ],
                    "activation": materialization.get("activation"),
                }
                if len(materialization_evidence["bindings"]) != len(raw_bindings):
                    raise ActorFleetError(
                        "actor materialization ledger evidence is invalid"
                    )
            confirmations = (
                rebind.get("confirmations", {})
                if isinstance(rebind, Mapping)
                and isinstance(rebind.get("confirmations"), Mapping) else {}
            )
            journal_payload_sha256 = journal.get("journal_payload_sha256")
            if (
                _HASH.fullmatch(str(journal_payload_sha256 or "")) is None
                or journal_payload_sha256 != _hash(_journal_payload(journal))
            ):
                raise ActorFleetError("actor provider ledger payload is invalid")
            coordinator_binding = journal.get("coordinator_binding")
            if not isinstance(coordinator_binding, Mapping):
                raise ActorFleetError("actor provider coordinator binding is missing")
            evidence_receipts = self._ledger_evidence_receipts(journal)
            event_id = _hash(
                {
                    "transaction_id": journal.get("transaction_id"),
                    "provider_status": journal.get("status"),
                    "journal_payload_sha256": journal_payload_sha256,
                }
            )
            prior_events = [
                item for item in entries if item.get("event_id") == event_id
            ]
            if prior_events:
                if len(prior_events) != 1 or prior_events[0] is not entries[-1]:
                    raise ActorFleetError(
                        "actor provider ledger retry is not the chain tail"
                    )
                prior = prior_events[0]
                if (
                    prior.get("journal_payload_sha256")
                    != journal_payload_sha256
                    or prior.get("transaction_id") != journal.get("transaction_id")
                    or prior.get("provider_status") != journal.get("status")
                    or prior.get("evidence_receipts") != evidence_receipts
                ):
                    raise ActorFleetError("actor provider ledger event differs")
                return {
                    "sequence": prior["sequence"],
                    "record_sha256": prior["record_sha256"],
                    "evidence_link": self._ledger_evidence_link(
                        prior["sequence"],
                        prior["record_sha256"],
                        evidence_receipts,
                    ),
                }
            body = {
                "schema": "tgw-generation-ledger-entry/v1",
                "record_role": "PROVIDER_PHASE",
                "event_id": event_id,
                "sequence": expected_sequence,
                "previous_record_sha256": previous_hash,
                "recorded_at": journal.get("updated_at"),
                "boot_id": boot_id,
                "transaction_id": journal.get("transaction_id"),
                "provider_status": journal.get("status"),
                "direction": (
                    rebind.get("direction")
                    if isinstance(rebind, Mapping) else "successor"
                ),
                "predecessor_generation": (
                    request.get("predecessor_generation")
                    if isinstance(request, Mapping) else None
                ),
                "successor_generation": (
                    request.get("successor_generation")
                    if isinstance(request, Mapping) else None
                ),
                "revisions_sha256": (
                    _hash(request.get("revisions"))
                    if isinstance(request, Mapping)
                    and isinstance(request.get("revisions"), Mapping) else None
                ),
                "approved_plan_commit": revisions.get("plan"),
                "approved_plan_solution_hash": revisions.get("solution"),
                "evidence_plan_commit": revisions.get("evidence_plan"),
                "evidence_plan_tree": revisions.get("evidence_tree"),
                "source_commit": revisions.get("source"),
                "source_tree": revisions.get("source_tree"),
                "current_plan_sources_sha256": (
                    _hash(revisions.get("current_plan_sources"))
                    if isinstance(revisions.get("current_plan_sources"), Mapping)
                    else None
                ),
                "catalog_hash": revisions.get("catalog"),
                "bootstrap_hash": revisions.get("bootstrap"),
                "broker_policy_hash": revisions.get("broker_policy"),
                "admission_receipt_hash": revisions.get("admission"),
                "review_receipt_hash": revisions.get("review"),
                "evidence_receipts": evidence_receipts,
                "journal_payload_sha256": journal_payload_sha256,
                "coordinator_binding_sha256": coordinator_binding.get(
                    "binding_sha256"
                ),
                "coordinator_journal_sha256": coordinator_binding.get(
                    "coordinator_journal_sha256"
                ),
                "coordinator_ledger_opening_sha256": coordinator_binding.get(
                    "coordinator_ledger_opening_sha256"
                ),
                "effect_plan_sha256": coordinator_binding.get(
                    "effect_plan_sha256"
                ),
                "obligations_sha256": (
                    _hash(rebind.get("obligations"))
                    if isinstance(rebind, Mapping)
                    and isinstance(rebind.get("obligations"), list) else None
                ),
                "latest_dispositions_sha256": (
                    _hash(latest.get("dispositions"))
                    if isinstance(latest.get("dispositions"), list) else None
                ),
                "latest_pending_sha256": (
                    _hash(latest.get("pending"))
                    if isinstance(latest.get("pending"), list) else None
                ),
                "materialization_sha256": (
                    _hash(materialization_evidence)
                    if materialization_evidence is not None else None
                ),
                "startup_binding_plan_sha256": (
                    _hash(journal.get("startup_binding_plan"))
                    if isinstance(journal.get("startup_binding_plan"), list) else None
                ),
                "real_store_evidence_sha256": _hash(
                    [
                        {
                            "actor": actor,
                            "semantic_sha256": proof.get(
                                "primary_real_store_semantic_sha256"
                            ),
                            "instruction_path": proof.get(
                                "instruction_entry_point_path"
                            ),
                            "instruction_sha256": proof.get(
                                "instruction_entry_point_sha256"
                            ),
                            "proof_sha256": proof.get("actor_proof_hash"),
                        }
                        for actor, proof in sorted(actor_verifications.items())
                        if isinstance(proof, Mapping)
                    ]
                ),
                "cold_handoff_evidence_sha256": _hash(
                    sorted(
                        str(proof.get("confirmation_hash"))
                        for proof in confirmations.values()
                        if isinstance(proof, Mapping)
                    )
                ),
                "parent_transitions_sha256": (
                    _hash(rebind.get("parent_transitions"))
                    if isinstance(rebind, Mapping)
                    and isinstance(rebind.get("parent_transitions"), Mapping)
                    else None
                ),
                "supersessions_sha256": _hash(
                    sorted(str(item) for item in journal.get("supersessions", []))
                ) if isinstance(journal.get("supersessions", []), list) else None,
                "final_disposition": (
                    journal.get("status")
                    if journal.get("status") in {"VERIFIED", "ROLLED_BACK"}
                    else None
                ),
            }
            entry = {**body, "record_sha256": _hash(body)}
            destination = root / (
                f"{expected_sequence:012d}-"
                f"{entry['record_sha256'].removeprefix('sha256:')}.json"
            )
            if destination.exists() or destination.is_symlink():
                raise ActorFleetError("actor generation ledger segment conflicts")
            _atomic(
                destination, entry, mode=0o640,
                uid=0 if os.geteuid() == 0 else None,
                gid=self.actor_group_gid,
            )
            return {
                "sequence": expected_sequence,
                "record_sha256": entry["record_sha256"],
                "evidence_link": self._ledger_evidence_link(
                    expected_sequence,
                    entry["record_sha256"],
                    evidence_receipts,
                ),
            }
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @property
    def _fleet_convergence_path(self) -> Path:
        return self.state_root / "fleet-convergence.json"

    @property
    def _fleet_active_pointer_path(self) -> Path:
        return self.state_root / "active-fleet-transaction.json"

    @property
    def _fleet_supersession_root(self) -> Path:
        return self.state_root / "fleet-supersessions"

    def _active_fleet_pointer(self) -> dict[str, Any] | None:
        path = self._fleet_active_pointer_path
        if not path.exists() and not path.is_symlink():
            return None
        value = _read_json(path, "actor fleet active pointer")
        observed = path.stat(follow_symlinks=False)
        unsigned = dict(value)
        claimed = unsigned.pop("pointer_sha256", None)
        if (
            path.is_symlink()
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) != 0o640
            or observed.st_gid != self.actor_group_gid
            or (os.geteuid() == 0 and observed.st_uid != 0)
            or value.get("schema") != "tgw-active-fleet-transaction/v1"
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{0,127}",
                str(value.get("transaction_id", "")),
            )
            or claimed != _hash(unsigned)
        ):
            raise ActorFleetError("actor fleet active pointer is invalid")
        return value

    def _write_active_fleet_pointer(
        self,
        transaction_id: str,
        *,
        planned: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if planned is None:
            body = {
                "schema": "tgw-active-fleet-transaction/v1",
                "transaction_id": transaction_id,
                "updated_at": self._current_time().astimezone(
                    timezone.utc
                ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            pointer = {**body, "pointer_sha256": _hash(body)}
        else:
            pointer = dict(planned)
            unsigned = dict(pointer)
            claimed = unsigned.pop("pointer_sha256", None)
            if (
                set(pointer)
                != {"schema", "transaction_id", "updated_at", "pointer_sha256"}
                or pointer.get("schema")
                != "tgw-active-fleet-transaction/v1"
                or pointer.get("transaction_id") != transaction_id
                or claimed != _hash(unsigned)
            ):
                raise ActorFleetError("planned actor fleet pointer is invalid")
        path = self._fleet_active_pointer_path
        if path.exists() or path.is_symlink():
            existing = self._active_fleet_pointer()
            if existing == pointer:
                return pointer
        _atomic(
            path, pointer, mode=0o640,
            uid=0 if os.geteuid() == 0 else None,
            gid=self.actor_group_gid,
        )
        return pointer

    def _fleet_supersessions(self) -> dict[str, dict[str, Any]]:
        root = self._fleet_supersession_root
        if not root.exists() and not root.is_symlink():
            return {}
        if root.is_symlink() or not root.is_dir():
            raise ActorFleetError("actor fleet supersession root is unsafe")
        root_state = root.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(root_state.st_mode) != 0o750
            or root_state.st_gid != self.actor_group_gid
            or (os.geteuid() == 0 and root_state.st_uid != 0)
        ):
            raise ActorFleetError("actor fleet supersession root is not protected")
        result: dict[str, dict[str, Any]] = {}
        for path in sorted(root.glob("*.json")):
            value = _read_json(path, "actor fleet supersession")
            unsigned = dict(value)
            observed = path.stat(follow_symlinks=False)
            claimed = unsigned.pop("supersession_sha256", None)
            old_id = str(value.get("superseded_transaction_id", ""))
            if (
                path.is_symlink()
                or observed.st_nlink != 1
                or stat.S_IMODE(observed.st_mode) != 0o640
                or observed.st_gid != self.actor_group_gid
                or (os.geteuid() == 0 and observed.st_uid != 0)
                or value.get("schema") != "tgw-fleet-supersession/v1"
                or path.name != f"{old_id}.json"
                or old_id in result
                or claimed != _hash(unsigned)
                or value.get("disposition") not in {
                    "ABANDONED_NONTERMINAL", "SUPERSEDED_FOR_SUCCESSOR_REPAIR"
                }
            ):
                raise ActorFleetError("actor fleet supersession is invalid")
            result[old_id] = value
        return result

    def _fleet_convergence_projection(
        self, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        ledger_evidence = self._validated_journal_ledger_link(journal)
        request = journal.get("request")
        if not isinstance(request, Mapping):
            raise ActorFleetError("actor fleet convergence request is unavailable")
        value = _request(request)
        rebind = journal.get("context_rebind")
        direction = (
            str(rebind.get("direction"))
            if isinstance(rebind, Mapping) else "successor"
        )
        if direction not in {"successor", "rollback"}:
            raise ActorFleetError("actor fleet convergence direction is invalid")
        target_generation = value["successor_generation"]
        revisions = value["revisions"]
        target_revisions = {
            "approved_plan": revisions["plan"],
            "approved_solution": revisions["solution"],
            "evidence_plan": revisions["evidence_plan"],
            "evidence_tree": revisions["evidence_tree"],
            "source_commit": revisions["source"],
            "source_tree": revisions["source_tree"],
            "current_plan_sources": dict(revisions["current_plan_sources"]),
            "current_plan_sources_sha256": _hash(
                revisions["current_plan_sources"]
            ),
            "catalog": revisions["catalog"],
            "bootstrap": revisions["bootstrap"],
            "broker_policy": revisions["broker_policy"],
            "admission": revisions["admission"],
            "review": revisions["review"],
        }
        if direction == "rollback":
            target_bindings = (
                rebind.get("target_bindings") if isinstance(rebind, Mapping) else None
            )
            targets = [
                item for item in target_bindings.values()
                if isinstance(item, Mapping)
            ] if isinstance(target_bindings, Mapping) else []
            normalized = {
                (
                    item.get("expected_generation"),
                    item.get("expected_plan_commit"),
                    item.get("expected_solution_hash"),
                    item.get("expected_source_commit"),
                    item.get("expected_source_tree"),
                    item.get("expected_catalog_hash"),
                )
                for item in targets
            }
            if len(targets) != len(value["actors"]) or len(normalized) != 1:
                raise ActorFleetError("actor rollback convergence target is ambiguous")
            generation, plan, solution, source, source_tree, catalog = next(
                iter(normalized)
            )
            target_generation = generation
            target_revisions.update(
                {
                    "approved_plan": plan, "approved_solution": solution,
                    "source_commit": source, "source_tree": source_tree,
                    "catalog": catalog,
                }
            )
        obligations = (
            list(rebind.get("obligations", []))
            if isinstance(rebind, Mapping) and isinstance(rebind.get("obligations"), list)
            else []
        )
        latest = (
            rebind.get("latest") if isinstance(rebind, Mapping)
            and isinstance(rebind.get("latest"), Mapping) else {}
        )
        latest_dispositions = {
            str(item.get("obligation_id")): item
            for item in latest.get("dispositions", [])
            if isinstance(item, Mapping) and item.get("obligation_id")
        }
        latest_pending = [
            item for item in latest.get("pending", []) if isinstance(item, Mapping)
        ]
        confirmations = (
            rebind.get("confirmations", {}) if isinstance(rebind, Mapping)
            and isinstance(rebind.get("confirmations"), Mapping) else {}
        )
        parent_transitions = (
            rebind.get("parent_transitions", {}) if isinstance(rebind, Mapping)
            and isinstance(rebind.get("parent_transitions"), Mapping) else {}
        )
        projected_obligations: list[dict[str, Any]] = []
        for obligation in obligations:
            if not isinstance(obligation, Mapping):
                raise ActorFleetError("actor fleet convergence obligation is invalid")
            obligation_id = str(obligation.get("obligation_id", ""))
            baseline = obligation.get("baseline")
            disposition = latest_dispositions.get(obligation_id)
            confirmation = confirmations.get(obligation_id)
            parent_transition = parent_transitions.get(obligation_id)
            projected_obligations.append(
                {
                    "obligation_id": obligation_id,
                    "actor": obligation.get("actor"),
                    "baseline_state": obligation.get("baseline_state"),
                    "checkpoint_disposition": obligation.get(
                        "checkpoint_disposition"
                    ),
                    "path_identity_hash": (
                        baseline.get("path_identity_hash")
                        if isinstance(baseline, Mapping) else None
                    ),
                    "parent_identity_hash": (
                        baseline.get("parent", {}).get("identity_hash")
                        if isinstance(baseline, Mapping)
                        and isinstance(baseline.get("parent"), Mapping) else None
                    ),
                    "baseline_child_identity_hashes": (
                        sorted(str(item) for item in baseline.get(
                            "child_identity_hashes", []
                        )) if isinstance(baseline, Mapping) else []
                    ),
                    "replacement_policy": obligation.get("replacement_policy"),
                    "disposition": (
                        disposition.get("disposition")
                        if isinstance(disposition, Mapping) else None
                    ),
                    "pending_reasons": sorted(
                        str(item.get("reason")) for item in latest_pending
                        if item.get("obligation_id") in {None, obligation_id}
                    ),
                    "client_confirmation_hash": (
                        confirmation.get("confirmation_hash")
                        if isinstance(confirmation, Mapping) else None
                    ),
                    "parent_transition_hash": (
                        parent_transition.get("provider_record_sha256")
                        if isinstance(parent_transition, Mapping) else None
                    ),
                    "parent_transition_disposition": (
                        parent_transition.get("disposition")
                        if isinstance(parent_transition, Mapping) else None
                    ),
                }
            )
        try:
            boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="utf-8"
            ).strip()
        except OSError as exc:
            raise ActorFleetError("actor fleet boot identity is unavailable") from exc
        selected_release: dict[str, Any] | None = None
        raw_release = journal.get("candidate_release")
        if isinstance(raw_release, str):
            release = Path(raw_release)
            manifest = _verified_release(self.release_root, release)
            if (
                manifest.get("commit") != revisions["source"]
                or manifest.get("git_tree") != revisions["source_tree"]
            ):
                raise ActorFleetError("actor selected release revision differs")
            selected_release = {
                "path": str(release),
                "generation": manifest.get("generation"),
                "commit": manifest.get("commit"),
                "tree": manifest.get("git_tree"),
                "manifest_sha256": _hash(manifest),
            }
        actor_verifications = (
            journal.get("actor_verifications", {})
            if isinstance(journal.get("actor_verifications", {}), Mapping)
            else {}
        )
        confirmation_hashes = sorted(
            str(item.get("confirmation_hash"))
            for item in confirmations.values()
            if isinstance(item, Mapping)
        )
        body = {
            "schema": "tgw-fleet-convergence-projection/v1",
            "status": journal.get("status"),
            "transaction_id": journal.get("transaction_id"),
            "actors": value["actors"],
            "direction": direction,
            "predecessor_generation": value["predecessor_generation"],
            "successor_generation": value["successor_generation"],
            "target_generation": target_generation,
            "target_revisions": target_revisions,
            "boot_id": boot_id,
            "created_at": journal.get("created_at"),
            "updated_at": journal.get("updated_at"),
            "journal_sha256": _hash(journal),
            "journal_payload_sha256": journal.get("journal_payload_sha256"),
            "ledger_sequence": journal.get("ledger_sequence"),
            "ledger_record_sha256": journal.get("ledger_record_sha256"),
            "ledger_evidence": ledger_evidence,
            "coordinator_binding_sha256": (
                journal.get("coordinator_binding", {}).get("binding_sha256")
                if isinstance(journal.get("coordinator_binding"), Mapping)
                else None
            ),
            "confinement_state": "NON_CONFINING_ACTOR_COMPOSITE_STORES",
            "selected_release": selected_release,
            "admission_evidence": {
                "review_receipt_sha256": revisions["review"],
                "admission_receipt_sha256": revisions["admission"],
                "ledger_sequence": ledger_evidence["sequence"],
                "ledger_record_sha256": ledger_evidence["record_sha256"],
            },
            "real_store_evidence_sha256": _hash(
                [
                    {
                        "actor": actor,
                        "semantic_sha256": proof.get(
                            "primary_real_store_semantic_sha256"
                        ),
                        "instruction_path": proof.get(
                            "instruction_entry_point_path"
                        ),
                        "instruction_sha256": proof.get(
                            "instruction_entry_point_sha256"
                        ),
                        "proof_sha256": proof.get("actor_proof_hash"),
                    }
                    for actor, proof in sorted(actor_verifications.items())
                    if isinstance(proof, Mapping)
                ]
            ),
            "cold_handoff_evidence_sha256": _hash(confirmation_hashes),
            "cold_handoff_receipt_sha256": ledger_evidence[
                "cold_handoff_receipt_sha256"
            ],
            "managed_service_action_receipt_sha256": ledger_evidence[
                "managed_service_action_receipt_sha256"
            ],
            "terminal_convergence_receipt_sha256": ledger_evidence[
                "terminal_convergence_receipt_sha256"
            ],
            "actor_verifications": [
                {
                    "actor": actor,
                    "actor_proof_hash": proof.get("actor_proof_hash"),
                    "verification_receipt_sha256": ledger_evidence[
                        "actor_verification_receipt_hashes"
                    ].get(actor),
                    "context_mcp_proof_hash": proof.get(
                        "context_mcp_proof_hash"
                    ),
                    "primary_real_store_semantic_sha256": proof.get(
                        "primary_real_store_semantic_sha256"
                    ),
                    "instruction_entry_point_path": proof.get(
                        "instruction_entry_point_path"
                    ),
                    "instruction_entry_point_sha256": proof.get(
                        "instruction_entry_point_sha256"
                    ),
                    "live_context_state": proof.get("live_context_state"),
                    "verified_at": proof.get("verified_at"),
                }
                for actor, proof in sorted(
                    actor_verifications.items()
                )
                if isinstance(proof, Mapping)
            ],
            "last_verified_at": max(
                (
                    str(proof.get("verified_at"))
                    for proof in journal.get("actor_verifications", {}).values()
                    if isinstance(proof, Mapping) and proof.get("verified_at")
                ),
                default=None,
            ) if isinstance(journal.get("actor_verifications", {}), Mapping) else None,
            "obligations": projected_obligations,
            "obligations_sha256": _hash(obligations),
            "global_pending": [
                dict(item) for item in latest_pending
                if item.get("obligation_id") is None
            ],
        }
        return {**body, "projection_sha256": _hash(body)}

    def _write_fleet_convergence_projection(self) -> dict[str, Any]:
        journals: list[dict[str, Any]] = []
        for path in sorted(self.private_state_root.glob("*.actor-provider.json")):
            if path.is_symlink() or not path.is_file():
                raise ActorFleetError("actor fleet convergence journal is unsafe")
            observed = path.stat(follow_symlinks=False)
            if observed.st_mode & 0o022 or (
                os.geteuid() == 0 and observed.st_uid != 0
            ):
                raise ActorFleetError("actor fleet convergence journal is not protected")
            journal = _read_json(path, "actor fleet convergence journal")
            if journal.get("schema") != "tgw-actor-fleet-journal/v1":
                raise ActorFleetError("actor fleet convergence journal schema differs")
            journals.append(journal)
        by_id = {
            str(item.get("transaction_id")): item for item in journals
        }
        active = [
            item for item in journals
            if item.get("status") not in {"VERIFIED", "ROLLED_BACK"}
        ]
        pointer = self._active_fleet_pointer()
        pointer_id = (
            str(pointer.get("transaction_id"))
            if isinstance(pointer, Mapping) else None
        )
        supersessions = self._fleet_supersessions()
        selected: dict[str, Any] | None = None
        ambiguous: list[str] = []
        if active:
            active_by_id = {
                str(item.get("transaction_id")): item for item in active
            }
            if pointer_id in active_by_id:
                selected = active_by_id[pointer_id]
            elif len(active) == 1 and (
                pointer_id is None
                or pointer_id not in by_id
                or by_id[pointer_id].get("status") in {"VERIFIED", "ROLLED_BACK"}
            ):
                selected = active[0]
                self._write_active_fleet_pointer(str(selected["transaction_id"]))
                pointer = self._active_fleet_pointer()
                pointer_id = str(selected["transaction_id"])
            else:
                ambiguous = sorted(active_by_id)
            if selected is not None:
                for item in active:
                    old_id = str(item.get("transaction_id"))
                    if old_id == selected.get("transaction_id"):
                        continue
                    supersession = supersessions.get(old_id)
                    if (
                        not isinstance(supersession, Mapping)
                        or supersession.get("successor_transaction_id")
                        != selected.get("transaction_id")
                        or supersession.get("superseded_journal_sha256")
                        != _hash(item)
                    ):
                        ambiguous.append(old_id)
        else:
            selected = (
                by_id.get(pointer_id) if pointer_id is not None else None
            )
            if selected is None or selected.get("status") not in {
                "VERIFIED", "ROLLED_BACK"
            }:
                selected = max(
                    journals,
                    key=lambda item: (
                        str(item.get("updated_at", "")),
                        str(item.get("transaction_id", "")),
                    ),
                    default=None,
                )
                if selected is not None:
                    self._write_active_fleet_pointer(str(selected["transaction_id"]))
                    pointer = self._active_fleet_pointer()
                    pointer_id = str(selected["transaction_id"])
        if ambiguous:
            body = {
                "schema": "tgw-fleet-convergence-set/v1",
                "state": "AMBIGUOUS",
                "generation_status": "HOLD",
                "active_transaction_ids": sorted(set(ambiguous)),
                "active_pointer_sha256": (
                    pointer.get("pointer_sha256")
                    if isinstance(pointer, Mapping) else None
                ),
                "supersessions_sha256": _hash(supersessions),
            }
            projection = {**body, "projection_sha256": _hash(body)}
        else:
            if selected is None:
                body = {
                    "schema": "tgw-fleet-convergence-set/v1",
                    "state": "NO_TRANSACTION",
                    "generation_status": "HOLD",
                    "active_transaction_ids": [],
                    "active_pointer_sha256": None,
                    "supersessions_sha256": _hash(supersessions),
                }
                projection = {**body, "projection_sha256": _hash(body)}
            else:
                selected_projection = self._fleet_convergence_projection(selected)
                pending_reasons = {
                    str(reason)
                    for obligation in selected_projection["obligations"]
                    for reason in obligation["pending_reasons"]
                }
                pending_reasons.update(
                    str(item.get("reason"))
                    for item in selected_projection["global_pending"]
                    if isinstance(item, Mapping)
                )
                if any(
                    "STALE" in reason or "UNCLASSIFIED" in reason
                    or "NOT_UNIQUE" in reason
                    for reason in pending_reasons
                ):
                    generation_status = "MIXED"
                elif selected.get("status") in {
                    "RESTART_REQUIRED", "ROLLBACK_RESTART_REQUIRED"
                }:
                    generation_status = "RESTART_REQUIRED"
                elif selected.get("status") in {"VERIFIED", "ROLLED_BACK"}:
                    generation_status = "CURRENT"
                else:
                    generation_status = "UPDATE_PENDING"
                body = {
                    "schema": "tgw-fleet-convergence-set/v1",
                    "state": "ACTIVE" if active else "TERMINAL",
                    "generation_status": generation_status,
                    "active_transaction_ids": (
                        [str(selected["transaction_id"])]
                        if selected.get("status") not in {"VERIFIED", "ROLLED_BACK"}
                        else []
                    ),
                    "active_pointer_sha256": (
                        pointer.get("pointer_sha256")
                        if isinstance(pointer, Mapping) else None
                    ),
                    "supersessions_sha256": _hash(supersessions),
                    "transaction": selected_projection,
                }
                projection = {**body, "projection_sha256": _hash(body)}
        _atomic(
            self._fleet_convergence_path, projection, mode=0o640,
            uid=0 if os.geteuid() == 0 else None,
            gid=self.actor_group_gid,
        )
        return projection

    def generation_status(self) -> dict[str, Any]:
        """Return the one-line provider status without selecting an MCP child."""
        observed = self._fleet_convergence_path.stat(follow_symlinks=False)
        projection = _read_json(
            self._fleet_convergence_path, "actor fleet convergence projection"
        )
        unsigned = dict(projection)
        claimed = unsigned.pop("projection_sha256", None)
        status = projection.get("generation_status")
        if (
            claimed != _hash(unsigned)
            or self._fleet_convergence_path.is_symlink()
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) != 0o640
            or observed.st_gid != self.actor_group_gid
            or (os.geteuid() == 0 and observed.st_uid != 0)
            or status not in {
                "CURRENT", "UPDATE_PENDING", "RESTART_REQUIRED", "MIXED", "HOLD"
            }
        ):
            raise ActorFleetError("actor fleet generation status is invalid")
        transaction = projection.get("transaction")
        revisions = (
            transaction.get("target_revisions")
            if isinstance(transaction, Mapping)
            and isinstance(transaction.get("target_revisions"), Mapping) else {}
        )
        pending = (
            sum(
                len(item.get("pending_reasons", []))
                for item in transaction.get("obligations", [])
                if isinstance(item, Mapping)
            ) + len(transaction.get("global_pending", []))
            if isinstance(transaction, Mapping) else 0
        )
        transaction_id = (
            str(transaction.get("transaction_id"))
            if isinstance(transaction, Mapping) else "none"
        )
        generation = (
            str(transaction.get("target_generation", "none"))
            .removeprefix("sha256:")[:12]
            if isinstance(transaction, Mapping) else "none"
        )
        approved = str(revisions.get("approved_plan", "none"))[:12]
        evidence = str(revisions.get("evidence_plan", "none"))[:12]
        source = str(revisions.get("source_commit", "none"))[:12]
        line = (
            f"TGW Context generation: client=INDEPENDENT fleet={status} "
            f"aggregate={status} gen={generation} approved={approved} "
            f"evidence={evidence} source={source} "
            f"tx={transaction_id} pending={pending}"
        )
        return {
            "status": status,
            "client_state": "INDEPENDENT",
            "fleet_state": status,
            "line": line,
            "projection_sha256": claimed,
        }

    def _validate_coordinator_binding(
        self,
        request: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        fields = {
            "schema", "outer_transaction_id", "actor_request_sha256",
            "coordinator_journal_sha256",
            "coordinator_ledger_opening_sha256", "effect_plan_sha256",
            "binding_sha256",
        }
        if not isinstance(binding, Mapping):
            raise ActorFleetError("actor coordinator binding is invalid")
        unsigned = dict(binding)
        claimed = unsigned.pop("binding_sha256", None)
        transaction_id = str(request["transaction_id"])
        if (
            set(binding) != fields
            or binding.get("schema")
            != "tgw-context-update-coordinator-binding/v1"
            or binding.get("outer_transaction_id") != transaction_id
            or binding.get("actor_request_sha256") != _hash(request)
            or any(
                _HASH.fullmatch(str(binding.get(name, ""))) is None
                for name in (
                    "coordinator_journal_sha256",
                    "coordinator_ledger_opening_sha256",
                    "effect_plan_sha256",
                )
            )
            or claimed != _hash(unsigned)
        ):
            raise ActorFleetError("actor coordinator binding is invalid")

        transaction_root = self.coordinator_transaction_root / transaction_id
        journal_path = transaction_root / "private-journal.json"
        try:
            coordinator_root_state = self.coordinator_transaction_root.stat(
                follow_symlinks=False
            )
            transaction_root_state = transaction_root.stat(follow_symlinks=False)
        except OSError as exc:
            raise ActorFleetError("coordinator private journal is unavailable") from exc
        if (
            not self.coordinator_transaction_root.is_absolute()
            or self.coordinator_transaction_root == Path("/tmp")
            or Path("/tmp") in self.coordinator_transaction_root.parents
            or self.coordinator_transaction_root.is_symlink()
            or self.coordinator_transaction_root.resolve(strict=True)
            != self.coordinator_transaction_root
            or stat.S_IMODE(coordinator_root_state.st_mode) != 0o700
            or stat.S_IMODE(transaction_root_state.st_mode) != 0o700
            or (
                os.geteuid() == 0
                and (
                    self.coordinator_transaction_root
                    != _DEFAULT_COORDINATOR_TRANSACTION_ROOT
                    or coordinator_root_state.st_uid != 0
                    or transaction_root_state.st_uid != 0
                    or coordinator_root_state.st_gid != 0
                    or transaction_root_state.st_gid != 0
                )
            )
            or transaction_root.is_symlink()
            or not transaction_root.is_dir()
            or transaction_root.resolve(strict=True) != transaction_root
            or transaction_root.parent != self.coordinator_transaction_root
            or journal_path.is_symlink()
            or not journal_path.is_file()
        ):
            raise ActorFleetError("coordinator private journal is unavailable")
        if os.geteuid() == 0:
            protected_ancestors = {
                Path("/var/lib/tgw"): 0o755,
                Path("/var/lib/tgw/context-update"): 0o755,
                _DEFAULT_COORDINATOR_TRANSACTION_ROOT: 0o700,
            }
            for ancestor, expected_mode in protected_ancestors.items():
                try:
                    observed = ancestor.stat(follow_symlinks=False)
                except OSError as exc:
                    raise ActorFleetError(
                        "coordinator private journal ancestry is unavailable"
                    ) from exc
                if (
                    ancestor.is_symlink()
                    or not stat.S_ISDIR(observed.st_mode)
                    or ancestor.resolve(strict=True) != ancestor
                    or observed.st_uid != 0
                    or observed.st_gid != 0
                    or stat.S_IMODE(observed.st_mode) != expected_mode
                ):
                    raise ActorFleetError(
                        "coordinator private journal ancestry is not protected"
                    )
        try:
            descriptor = os.open(
                journal_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            try:
                before = os.fstat(descriptor)
                raw = os.read(descriptor, _COORDINATOR_JOURNAL_MAX_INPUT + 1)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            private = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ActorFleetError("coordinator private journal is invalid") from exc
        if (
            not isinstance(private, Mapping)
            or len(raw) > _COORDINATOR_JOURNAL_MAX_INPUT
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or not stat.S_ISREG(before.st_mode)
            or (os.geteuid() == 0 and before.st_uid != 0)
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or private.get("schema")
            != "tgw-context-update-private-journal/v1"
            or private.get("transaction_id") != transaction_id
            or set(private) != {
                "schema", "transaction_id", "created_at", "nonce",
                "request_sha256", "candidate", "preimages",
                "managed_services", "quiescence_units", "service_preimages",
                "effect_plan", "rollback_order",
            }
            or not isinstance(private.get("created_at"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(private.get("nonce", "")))
            is None
            or _HASH.fullmatch(str(private.get("request_sha256", ""))) is None
            or private.get("managed_services") != self.services
            or private.get("quiescence_units") != self.quiescence_units
            or _hash(private) != binding.get("coordinator_journal_sha256")
        ):
            raise ActorFleetError("coordinator private journal binding differs")
        candidate = private.get("candidate")
        if (
            not isinstance(candidate, Mapping)
            or set(candidate) != {
                "commit", "tree", "release_generation", "actor_generation",
                "release_manifest_sha256", "catalog_sha256",
                "admission_receipt_sha256", "review_receipt_sha256",
                "prepared_evidence_sha256",
            }
            or candidate.get("commit") != request["revisions"]["source"]
            or candidate.get("tree") != request["revisions"]["source_tree"]
            or candidate.get("actor_generation")
            != request["successor_generation"]
            or candidate.get("catalog_sha256") != request["revisions"]["catalog"]
            or candidate.get("admission_receipt_sha256")
            != request["revisions"]["admission"]
            or candidate.get("review_receipt_sha256")
            != request["revisions"]["review"]
            or not isinstance(candidate.get("release_generation"), str)
            or not candidate.get("release_generation")
            or _HASH.fullmatch(
                str(candidate.get("release_manifest_sha256", ""))
            ) is None
            or _HASH.fullmatch(
                str(candidate.get("prepared_evidence_sha256", ""))
            ) is None
        ):
            raise ActorFleetError("coordinator candidate binding differs")
        candidate_release = self._candidate(request)
        candidate_manifest = _verified_release(
            self.release_root, candidate_release
        )
        if (
            candidate.get("release_generation")
            != candidate_manifest.get("generation")
            or candidate.get("commit") != candidate_manifest.get("commit")
            or candidate.get("tree") != candidate_manifest.get("git_tree")
            or candidate.get("release_manifest_sha256")
            != _hash(candidate_manifest)
        ):
            raise ActorFleetError("coordinator release candidate differs")
        _materializer, actor_bundle, _contracts = self._actor_inputs(
            candidate_release, request
        )
        preimages = private.get("preimages")
        service_preimages = private.get("service_preimages")
        effect_plan = private.get("effect_plan")
        if (
            not isinstance(preimages, list)
            or len(preimages) > 4096
            or not isinstance(service_preimages, list)
            or len(service_preimages) > 64
            or not isinstance(effect_plan, Mapping)
            or set(effect_plan)
            != {
                "schema", "transaction_id", "effects", "effect_plan_sha256"
            }
            or effect_plan.get("schema")
            != "tgw-context-update-effect-plan/v1"
            or effect_plan.get("transaction_id") != transaction_id
        ):
            raise ActorFleetError("coordinator effect plan is invalid")
        unsigned_plan = dict(effect_plan)
        plan_hash = unsigned_plan.pop("effect_plan_sha256", None)
        effects = effect_plan.get("effects")
        if (
            plan_hash != _hash(unsigned_plan)
            or plan_hash != binding.get("effect_plan_sha256")
            or not isinstance(effects, list)
            or not effects
            or len(effects) > 4096
            or private.get("rollback_order")
            != list(reversed([item.get("sequence") for item in effects]))
        ):
            raise ActorFleetError("coordinator effect plan binding differs")
        allowed_roots = (
            Path("/home/codex"), Path("/home/claude"), Path("/home/deepseek"),
            Path("/etc/tgw"), Path("/etc/systemd/system"),
            Path("/etc/tmpfiles.d"), Path("/etc/sudoers.d"),
            Path("/opt/TGW/w/attempts"), Path("/opt/TGW/var/cache/tgw"),
            Path("/opt/TGW/tgw-lib"), Path("/var/lib/tgw"),
            Path("/var/cache/tgw"), Path("/var/lib/systemd/linger"),
        )
        target_ids: set[str] = set()
        target_paths: set[Path] = set()
        target_kinds: dict[str, str] = {}
        entry_count = [0]
        for preimage in preimages:
            if not isinstance(preimage, Mapping) or set(preimage) != {
                "target_id", "path", "kind", "mode", "uid", "gid", "nlink",
                "payload",
            }:
                raise ActorFleetError("coordinator preimage is invalid")
            target_id = str(preimage.get("target_id", ""))
            path = Path(str(preimage.get("path", "")))
            if (
                re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", target_id) is None
                or target_id in target_ids
                or path in target_paths
                or not path.is_absolute()
                or ".." in path.parts
                or path == Path("/tmp")
                or Path("/tmp") in path.parents
                or path == Path("/opt/TGW/var/tmp")
                or Path("/opt/TGW/var/tmp") in path.parents
                or not any(path == root or root in path.parents for root in allowed_roots)
                or preimage.get("kind")
                not in {"absent", "file", "symlink", "directory"}
            ):
                raise ActorFleetError("coordinator preimage binding differs")
            _validate_coordinator_preimage_node(
                {
                    name: preimage[name]
                    for name in ("kind", "mode", "uid", "gid", "nlink", "payload")
                },
                nested=False,
                entry_count=entry_count,
            )
            target_ids.add(target_id)
            target_paths.add(path)
            target_kinds[target_id] = str(preimage["kind"])

        expected_paths = {
            "actor-public-trust": _ACTOR_PUBLIC_TRUST,
            "environment-public-trust": _ENVIRONMENT_PUBLIC_TRUST,
            "admission-public-trust": _ADMISSION_PUBLIC_TRUST,
            "provider-config": _ACTOR_PROVIDER_CONFIG,
            "release-admission": self.admission_root
            / f"{request['revisions']['admission'].removeprefix('sha256:')}.json",
            "environment-catalog": Path(
                "/etc/tgw/execution-environment-catalog.json"
            ),
            "release-selector": self.release_root / "current",
            "provider-unit": Path(
                "/etc/systemd/system/tgw-actor-fleet-provider.service"
            ),
            "provider-tmpfiles": Path("/etc/tmpfiles.d/tgw-actor-host.conf"),
            "relay-unit": Path(
                "/etc/systemd/system/tgw-context-confirmation-relay.service"
            ),
            "stable-launcher": _STABLE_CONTEXT_LAUNCHER,
            "stable-bin-parent": _STABLE_CONTEXT_LAUNCHER.parent,
            "status-executable": Path(
                "/opt/TGW/tgw-lib/bin/tgw-context-generation-status"
            ),
            "status-sudoers": Path(
                "/etc/sudoers.d/tgw-context-generation-status"
            ),
            "provider-state-journal": self._journal_path(
                str(request["transaction_id"])
            ),
            "provider-state-materializer": self.private_state_root
            / f"{request['transaction_id']}.actor-materializer.json",
            "provider-state-projection": self._fleet_convergence_path,
            "provider-state-pointer": self.state_root
            / "active-fleet-transaction.json",
            "cold-continuity-workspace": _DEFAULT_CONTEXT_UPDATE_SCRATCH_ROOT
            / transaction_id / "claude-cold-continuity",
            "transaction-scratch-root": _DEFAULT_CONTEXT_UPDATE_SCRATCH_ROOT
            / transaction_id,
            "cold-continuity-transcript": transaction_root
            / "cold-continuity-transcript.jsonl",
            "cold-continuity-receipt": transaction_root
            / "cold-continuity-receipt.json",
            "deepseek-service-action-receipt": transaction_root
            / "deepseek-service-action.json",
            "deepseek-service-progress": transaction_root
            / "deepseek-service-progress.json",
            "deepseek-linger-token": transaction_root / "deepseek-linger-token",
            "deepseek-linger": _DEEPSEEK_LINGER,
            "provider-attestation-receipt": transaction_root
            / "provider-attestation.json",
            "coordinator-terminal-receipt": transaction_root
            / "terminal-receipt.json",
        }
        tmpfiles_source = (
            candidate_release
            / "config/environment/tmpfiles.d/tgw-actor-host.conf"
        )
        try:
            tmpfiles_rows = tmpfiles_source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ActorFleetError(
                "candidate actor tmpfiles policy is unavailable"
            ) from exc
        for row in tmpfiles_rows:
            stripped = row.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            path = Path(fields[1]) if len(fields) == 6 else Path()
            if (
                len(fields) != 6
                or fields[0] != "d"
                or not path.is_absolute()
                or ".." in path.parts
                or "%" in fields[1]
            ):
                raise ActorFleetError("candidate actor tmpfiles policy is unbounded")
            identity = hashlib.sha256(str(path).encode()).hexdigest()[:16]
            expected_paths[f"tmpfiles-dir-{identity}"] = path
        preimage_records_by_id = {
            str(item["target_id"]): item for item in preimages
        }
        preimages_by_id = {
            str(item["target_id"]): Path(str(item["path"])) for item in preimages
        }
        observed_paths = set(expected_paths.values())
        for actor in request["actors"]:
            startup = self.startup_binding_root / f"{actor}-startup.json"
            expected_paths[f"startup-{actor}"] = startup
            expected_paths[f"actor-cache-{actor}"] = (
                self.actor_cache_root / actor
                / request["successor_generation"].removeprefix("sha256:")
            )
            observed_paths.add(startup)
            specification = actor_bundle.get("actors", {}).get(actor)
            bindings = (
                specification.get("bindings")
                if isinstance(specification, Mapping) else None
            )
            if not isinstance(bindings, list):
                raise ActorFleetError("actor generation preimage bindings differ")
            home = Path(pwd.getpwnam(actor).pw_dir)
            parent_paths: set[Path] = set()
            for index, actor_binding in enumerate(bindings):
                if not isinstance(actor_binding, Mapping):
                    raise ActorFleetError(
                        "actor generation preimage binding is invalid"
                    )
                destination = Path(str(actor_binding.get("destination", "")))
                target_id = f"actor-{actor}-{index:03d}"
                if (
                    not destination.is_absolute()
                    or destination == home
                    or home not in destination.parents
                    or destination in observed_paths
                    or target_id in expected_paths
                ):
                    raise ActorFleetError(
                        "actor generation preimage destination differs"
                    )
                expected_paths[target_id] = destination
                observed_paths.add(destination)
                parent = destination.parent
                while parent != home:
                    if parent not in observed_paths:
                        parent_paths.add(parent)
                    parent = parent.parent
            for parent in sorted(parent_paths, key=str):
                identity = hashlib.sha256(str(parent).encode()).hexdigest()[:16]
                target_id = f"parent-{identity}"
                existing = expected_paths.get(target_id)
                if existing is not None and existing != parent:
                    raise ActorFleetError(
                        "actor generation parent preimage identity collides"
                    )
                expected_paths[target_id] = parent
                observed_paths.add(parent)
        if preimages_by_id != expected_paths:
            raise ActorFleetError("coordinator preimage destination set differs")

        predecessor_config = _coordinator_file_preimage_json(
            preimage_records_by_id["provider-config"],
            "predecessor actor provider config",
        )
        predecessor_provider = predecessor_config.get("actor_fleet_provider")
        if not isinstance(predecessor_provider, Mapping):
            raise ActorFleetError(
                "predecessor actor provider trust preimage differs"
            )
        predecessor_key, predecessor_key_bytes = _contract_public_key(
            predecessor_provider.get("contract_public_key"),
            "predecessor actor contract verifier",
        )
        startup_preimage_hashes: dict[str, str] = {}
        predecessor_revision_bindings: set[tuple[str, str, str, str]] = set()
        for actor in request["actors"]:
            startup_preimage = preimage_records_by_id[f"startup-{actor}"]
            previous_startup = _coordinator_file_preimage_json(
                startup_preimage, f"predecessor startup binding {actor}"
            )
            if (
                previous_startup.get("schema")
                == "tgw-actor-startup-binding/v1"
                and (
                    set(previous_startup) != {
                        "schema", "actor", "trusted_public_key",
                        "expected_generation", "expected_plan_commit",
                        "expected_solution_hash", "expected_source_commit",
                        "expected_catalog_hash",
                    }
                    or _COMMIT.fullmatch(
                        str(previous_startup.get("expected_plan_commit", ""))
                    ) is None
                    or _HASH.fullmatch(
                        str(previous_startup.get("expected_solution_hash", ""))
                    ) is None
                    or _COMMIT.fullmatch(
                        str(previous_startup.get("expected_source_commit", ""))
                    ) is None
                    or _HASH.fullmatch(
                        str(previous_startup.get("expected_catalog_hash", ""))
                    ) is None
                )
            ):
                raise ActorFleetError(
                    "predecessor v1 startup binding is invalid"
                )
            if (
                previous_startup.get("schema")
                not in {
                    "tgw-actor-startup-binding/v1",
                    "tgw-actor-startup-binding/v2",
                    "tgw-actor-startup-binding/v3",
                }
                or previous_startup.get("actor") != actor
                or previous_startup.get("expected_generation")
                != request["predecessor_generation"]
                or previous_startup.get("trusted_public_key")
                != predecessor_key
            ):
                raise ActorFleetError(
                    "predecessor actor startup trust binding differs"
                )
            predecessor_revision_bindings.add(
                (
                    str(previous_startup.get("expected_plan_commit", "")),
                    str(previous_startup.get("expected_solution_hash", "")),
                    str(previous_startup.get("expected_source_commit", "")),
                    str(previous_startup.get("expected_catalog_hash", "")),
                )
            )
            startup_preimage_hashes[actor] = "sha256:" + hashlib.sha256(
                _coordinator_file_preimage_bytes(
                    startup_preimage, f"predecessor startup binding {actor}"
                )
            ).hexdigest()
        if len(predecessor_revision_bindings) != 1:
            raise ActorFleetError(
                "predecessor startup revision binding is ambiguous"
            )
        target_key, target_key_bytes = _contract_public_key(
            self.contract_public_key, "successor actor contract verifier"
        )
        predecessor_plan, predecessor_solution, predecessor_source, predecessor_catalog = (
            next(iter(predecessor_revision_bindings))
        )
        trust_body = {
            "schema": "tgw-actor-contract-directional-trust/v1",
            "transaction_id": transaction_id,
            "predecessor_generation": request["predecessor_generation"],
            "successor_generation": request["successor_generation"],
            "predecessor_revisions": {
                "plan": predecessor_plan,
                "solution": predecessor_solution,
                "source": predecessor_source,
                "catalog": predecessor_catalog,
            },
            "predecessor_contract_public_key": predecessor_key,
            "predecessor_contract_public_sha256": "sha256:"
            + hashlib.sha256(predecessor_key_bytes).hexdigest(),
            "successor_contract_public_key": target_key,
            "successor_contract_public_sha256": "sha256:"
            + hashlib.sha256(target_key_bytes).hexdigest(),
            "provider_config_preimage_sha256": "sha256:"
            + hashlib.sha256(
                _coordinator_file_preimage_bytes(
                    preimage_records_by_id["provider-config"],
                    "predecessor actor provider config",
                )
            ).hexdigest(),
            "startup_preimage_sha256": startup_preimage_hashes,
        }
        contract_trust = {**trust_body, "trust_sha256": _hash(trust_body)}

        services: dict[str, Mapping[str, Any]] = {}
        managed_service_names = sorted(
            set(self.services) | set(self.quiescence_units)
        )
        expected_standard_services = {
            **_COORDINATOR_SERVICE_PREIMAGES,
            **{
                "managed-service-"
                + hashlib.sha256(unit.encode()).hexdigest()[:16]: unit
                for unit in managed_service_names
            },
        }
        for service in service_preimages:
            if not isinstance(service, Mapping):
                raise ActorFleetError("coordinator service preimage is invalid")
            target_id = str(service.get("target_id", ""))
            if target_id in services:
                raise ActorFleetError("coordinator service preimage differs")
            if target_id == "deepseek-user-service":
                deepseek_fields = {
                    "target_id", "service", "actor", "uid", "unit_path",
                    "unit_sha256", "unit_mode", "unit_uid", "unit_gid",
                    "unit_nlink", "unit_file_state", "runtime_directory", "bus_path",
                    "runtime_present", "manager_available", "linger_path",
                    "linger_present", "linger_sha256", "login", "properties",
                    "parent_identity",
                }
                properties = service.get("properties")
                parent = service.get("parent_identity")
                linger_preimage = preimage_records_by_id.get(
                    "deepseek-linger"
                )
                linger_preimage_hash = (
                    "sha256:"
                    + hashlib.sha256(
                        _coordinator_file_preimage_bytes(
                            linger_preimage, "DeepSeek linger"
                        )
                    ).hexdigest()
                    if isinstance(linger_preimage, Mapping)
                    and linger_preimage.get("kind") == "file"
                    else None
                )
                if (
                    set(service) != deepseek_fields
                    or service.get("service") != _DEEPSEEK_USER_SERVICE
                    or service.get("actor") != "deepseek"
                    or service.get("uid") != _DEEPSEEK_UID
                    or service.get("unit_path") != str(_DEEPSEEK_USER_UNIT)
                    or _HASH.fullmatch(
                        str(service.get("unit_sha256", ""))
                    ) is None
                    or not isinstance(service.get("unit_mode"), int)
                    or not 0 <= service["unit_mode"] <= 0o7777
                    or service.get("unit_uid") != _DEEPSEEK_UID
                    or not isinstance(service.get("unit_gid"), int)
                    or service["unit_gid"] < 0
                    or service.get("unit_nlink") != 1
                    or not isinstance(service.get("unit_file_state"), str)
                    or not service["unit_file_state"]
                    or service.get("runtime_directory") != "/run/user/1005"
                    or service.get("bus_path") != "/run/user/1005/bus"
                    or not isinstance(service.get("runtime_present"), bool)
                    or not isinstance(service.get("manager_available"), bool)
                    or service.get("linger_path") != str(_DEEPSEEK_LINGER)
                    or not isinstance(service.get("linger_present"), bool)
                    or (
                        service["linger_present"]
                        and _HASH.fullmatch(
                            str(service.get("linger_sha256", ""))
                        ) is None
                    )
                    or (
                        not service["linger_present"]
                        and service.get("linger_sha256") is not None
                    )
                    or (
                        service["linger_present"]
                        and (
                            not isinstance(linger_preimage, Mapping)
                            or linger_preimage.get("kind") != "file"
                            or linger_preimage_hash
                            != service.get("linger_sha256")
                        )
                    )
                    or (
                        not service["linger_present"]
                        and (
                            not isinstance(linger_preimage, Mapping)
                            or linger_preimage.get("kind") != "absent"
                        )
                    )
                    or not isinstance(service.get("login"), Mapping)
                    or set(service["login"]) != {"Linger", "State", "Sessions"}
                    or any(
                        not isinstance(item, str)
                        for item in service["login"].values()
                    )
                    or service["login"].get("Linger") not in {"yes", "no"}
                    or (service["login"].get("Linger") == "yes")
                    is not service["linger_present"]
                    or (
                        service["manager_available"]
                        and (
                            not isinstance(properties, Mapping)
                            or set(properties)
                            != _COORDINATOR_SERVICE_PROPERTIES
                            or any(
                                not isinstance(item, str)
                                for item in properties.values()
                            )
                        )
                    )
                    or (
                        not service["manager_available"]
                        and (properties is not None or parent is not None)
                    )
                    or (
                        isinstance(properties, Mapping)
                        and properties.get("ActiveState") == "active"
                        and (
                            not _strong_process_identity_is_exact(
                                parent, uid=_DEEPSEEK_UID
                            )
                            or str(parent.get("pid"))
                            != properties.get("MainPID")
                        )
                    )
                    or (
                        isinstance(properties, Mapping)
                        and properties.get("ActiveState") != "active"
                        and (
                            parent is not None
                            or properties.get("MainPID") != "0"
                        )
                    )
                ):
                    raise ActorFleetError(
                        "coordinator DeepSeek service preimage differs"
                    )
                services[target_id] = service
                continue
            properties = service.get("properties")
            if (
                set(service) != {"target_id", "service", "properties"}
                or expected_standard_services.get(target_id)
                != service.get("service")
                or not isinstance(properties, Mapping)
                or set(properties) != _COORDINATOR_SERVICE_PROPERTIES
                or any(not isinstance(item, str) for item in properties.values())
            ):
                raise ActorFleetError("coordinator service preimage differs")
            services[target_id] = service
        if set(services) != {
            *expected_standard_services,
            "deepseek-user-service",
        }:
            raise ActorFleetError("coordinator service preimage set differs")

        managed_service_ids = sorted(
            name for name in services if name.startswith("managed-service-")
        )
        actor_ids = sorted(
            name for name in expected_paths
            if name.startswith(("actor-", "startup-", "parent-"))
        )
        tmpfiles_ids = sorted(
            name for name in expected_paths if name.startswith("tmpfiles-dir-")
        )
        provider_state_ids = sorted(
            name for name in expected_paths if name.startswith("provider-state-")
        )
        targets_by_action = {
            "INSTALL_PLATFORM_TRUST": [
                ("FILESYSTEM", "actor-public-trust"),
                ("FILESYSTEM", "environment-public-trust"),
                ("FILESYSTEM", "admission-public-trust"),
                ("FILESYSTEM", "provider-config"),
            ],
            "PUBLISH_ADMISSION": [("FILESYSTEM", "release-admission")],
            "INSTALL_CATALOG": [("FILESYSTEM", "environment-catalog")],
            "SELECT_RELEASE": [("FILESYSTEM", "release-selector")],
            "INSTALL_ACTOR_HOST": [
                ("FILESYSTEM", "provider-unit"),
                ("FILESYSTEM", "provider-tmpfiles"),
                *(("FILESYSTEM", name) for name in tmpfiles_ids),
            ],
            "INSTALL_STABLE_LAUNCHER": [
                ("FILESYSTEM", "stable-launcher"),
                ("FILESYSTEM", "stable-bin-parent"),
            ],
            "INSTALL_DIRECT_STATUS": [
                ("FILESYSTEM", "status-executable"),
                ("FILESYSTEM", "status-sudoers"),
                ("FILESYSTEM", "stable-bin-parent"),
            ],
            "INSTALL_CONFIRMATION_RELAY": [("FILESYSTEM", "relay-unit")],
            "RESTART_PROVIDER": [
                ("SERVICE", "provider-service"),
                ("SERVICE", "relay-service"),
                ("FILESYSTEM", "provider-attestation-receipt"),
            ],
            "BIND_COORDINATOR": [
                ("PROVIDER", "actor-fleet-provider-api"),
                *(("FILESYSTEM", name) for name in provider_state_ids),
            ],
            "QUIESCE_ACTORS": [
                ("PROVIDER", "actor-fleet-provider-api"),
                *(("SERVICE", name) for name in managed_service_ids),
            ],
            "REBUILD_ACTORS": [
                ("PROVIDER", "actor-fleet-provider-api"),
                *(
                    ("FILESYSTEM", name) for name in expected_paths
                    if name.startswith("actor-cache-")
                ),
            ],
            "ACTIVATE_ACTORS": [
                ("PROVIDER", "actor-fleet-provider-api"),
                *(("FILESYSTEM", name) for name in actor_ids),
            ],
            "VERIFY_COLD_CONTINUITY": [
                ("FILESYSTEM", "transaction-scratch-root"),
                ("FILESYSTEM", "cold-continuity-workspace"),
                ("FILESYSTEM", "cold-continuity-transcript"),
                ("FILESYSTEM", "cold-continuity-receipt"),
            ],
            "TRANSITION_DEEPSEEK_SERVICE": [
                ("SERVICE", "deepseek-user-service"),
                ("FILESYSTEM", "deepseek-service-action-receipt"),
                ("FILESYSTEM", "deepseek-service-progress"),
                ("FILESYSTEM", "deepseek-linger-token"),
                ("FILESYSTEM", "deepseek-linger"),
                ("PROVIDER", "actor-fleet-provider-api"),
            ],
            "RESTART_ACTORS": [
                ("PROVIDER", "actor-fleet-provider-api"),
                *(("SERVICE", name) for name in managed_service_ids),
            ],
            "HEALTH_ACTORS": [("PROVIDER", "actor-fleet-provider-api")],
            "VERIFY_ACTORS": [("PROVIDER", "actor-fleet-provider-api")],
            "FINALIZE_TRANSACTION": [
                ("FILESYSTEM", "coordinator-terminal-receipt"),
                ("COORDINATOR", "coordinator-progress")
            ],
        }
        assigned_filesystem: set[str] = set()
        assigned_services: set[str] = set()
        sequences: list[int] = []
        for expected_sequence, action in enumerate(
            _COORDINATOR_EFFECT_ACTIONS, 1
        ):
            effect = (
                effects[expected_sequence - 1]
                if len(effects) >= expected_sequence else None
            )
            expected_targets = []
            for target_class, target_id in targets_by_action[action]:
                expected_kind = {
                    "FILESYSTEM": target_kinds.get(target_id),
                    "SERVICE": "service",
                    "PROVIDER": "provider-request",
                    "COORDINATOR": "private-progress",
                }[target_class]
                if expected_kind is None:
                    raise ActorFleetError("coordinator effect target is absent")
                expected_targets.append(
                    {
                        "target_class": target_class,
                        "target_id": target_id,
                        "expected_preimage_kind": expected_kind,
                    }
                )
                if target_class == "FILESYSTEM":
                    assigned_filesystem.add(target_id)
                elif target_class == "SERVICE":
                    assigned_services.add(target_id)
            if (
                not isinstance(effect, Mapping)
                or set(effect) != {"sequence", "action", "targets"}
                or not isinstance(effect.get("sequence"), int)
                or effect.get("sequence") != expected_sequence
                or effect.get("action") != action
                or effect.get("targets") != expected_targets
            ):
                raise ActorFleetError("coordinator effect is invalid")
            sequences.append(effect["sequence"])
        if (
            len(effects) != len(_COORDINATOR_EFFECT_ACTIONS)
            or sequences != list(range(1, len(effects) + 1))
            or assigned_filesystem != set(expected_paths)
            or assigned_services != set(services)
        ):
            raise ActorFleetError("coordinator effect order is invalid")

        ledger_root = self.state_root / "generation-ledger"
        if ledger_root.is_symlink() or not ledger_root.is_dir():
            raise ActorFleetError("coordinator ledger opening is unavailable")
        previous_hash: str | None = None
        expected_sequence = 1
        opening: Mapping[str, Any] | None = None
        for segment in sorted(ledger_root.glob("*.json")):
            observed = segment.stat(follow_symlinks=False)
            entry = _read_json(segment, "coordinator generation ledger")
            unsigned_entry = dict(entry)
            record_hash = unsigned_entry.pop("record_sha256", None)
            if (
                segment.is_symlink()
                or observed.st_nlink != 1
                or stat.S_IMODE(observed.st_mode) != 0o640
                or observed.st_gid != self.actor_group_gid
                or (os.geteuid() == 0 and observed.st_uid != 0)
                or entry.get("schema") != "tgw-generation-ledger-entry/v1"
                or entry.get("sequence") != expected_sequence
                or entry.get("previous_record_sha256") != previous_hash
                or record_hash != _hash(unsigned_entry)
                or segment.name
                != f"{expected_sequence:012d}-{str(record_hash).removeprefix('sha256:')}.json"
            ):
                raise ActorFleetError("coordinator generation ledger differs")
            if record_hash == binding.get("coordinator_ledger_opening_sha256"):
                opening = entry
            previous_hash = str(record_hash)
            expected_sequence += 1
        review_receipt = (
            opening.get("review_receipt")
            if isinstance(opening, Mapping) else None
        )
        admission_receipt = (
            opening.get("admission_receipt")
            if isinstance(opening, Mapping) else None
        )
        expected_review = {
            "status": "PASS",
            "candidate_commit": request["revisions"]["source"],
            "solution_hash": request["revisions"]["solution"],
            "receipt_hash": request["revisions"]["review"],
        }
        try:
            if not isinstance(admission_receipt, Mapping):
                raise ActorFleetError(
                    "coordinator admission evidence is unavailable"
                )
            validated_admission = validate_release_admission(
                admission_receipt,
                candidate_commit=request["revisions"]["source"],
                candidate_tree=request["revisions"]["source_tree"],
                trusted_public_key=self.admission_public_key,
                current_time=self._current_time().astimezone(
                    timezone.utc
                ).isoformat(timespec="seconds").replace("+00:00", "Z"),
                current_plan_commit=request["revisions"]["plan"],
                current_solution_hash=request["revisions"]["solution"],
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ActorFleetError(
                "coordinator admission evidence differs"
            ) from exc
        if (
            not isinstance(opening, Mapping)
            or opening.get("record_role") != "COORDINATOR_OPENING"
            or opening.get("provider_status") != "PREPARED"
            or opening.get("transaction_id") != transaction_id
            or opening.get("request_sha256") != private.get("request_sha256")
            or opening.get("actor_request_sha256") != _hash(request)
            or opening.get("coordinator_journal_sha256")
            != binding.get("coordinator_journal_sha256")
            or opening.get("effect_plan_sha256")
            != binding.get("effect_plan_sha256")
            or opening.get("candidate_commit") != candidate.get("commit")
            or opening.get("candidate_tree") != candidate.get("tree")
            or opening.get("admission_receipt_hash")
            != candidate.get("admission_receipt_sha256")
            or opening.get("review_receipt_hash")
            != candidate.get("review_receipt_sha256")
            or review_receipt != expected_review
            or validated_admission != admission_receipt
            or admission_receipt.get("receipt_hash")
            != request["revisions"]["admission"]
            or opening.get("predecessor_actor_public_sha256")
            != contract_trust["predecessor_contract_public_sha256"]
            or opening.get("successor_actor_public_sha256")
            != contract_trust["successor_contract_public_sha256"]
            or _HASH.fullmatch(
                str(opening.get("trust_projection_sha256", ""))
            ) is None
        ):
            raise ActorFleetError("coordinator ledger opening binding differs")
        return {
            **dict(binding),
            "contract_trust": contract_trust,
            "coordinator_opening": {
                "sequence": opening.get("sequence"),
                "record_sha256": opening.get("record_sha256"),
                "review_receipt": dict(review_receipt),
                "admission_receipt": dict(admission_receipt),
                "trust_projection_sha256": opening.get(
                    "trust_projection_sha256"
                ),
            },
        }

    def bind_coordinator(
        self,
        request: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Bind the provider to the root journal opened before all effects."""

        value = _request(request)
        validated = self._validate_coordinator_binding(value, binding)
        journal = self._journal(value["transaction_id"])
        if journal.get("status") == "COORDINATOR_BOUND":
            if (
                journal.get("request") != value
                or journal.get("coordinator_binding") != validated
            ):
                raise ActorFleetError("actor coordinator binding already differs")
        elif journal.get("status") == "NEW" and journal.get("request") is None:
            now = self._current_time().astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z")
            journal.update(
                {
                    "request": value,
                    "status": "COORDINATOR_BOUND",
                    "coordinator_binding": validated,
                    # Salt every whole-private-journal digest so a shared
                    # ledger hash cannot be used as an oracle for composite
                    # store preimages retained only in root-private state.
                    "private_nonce": secrets.token_hex(32),
                    "created_at": now,
                    "updated_at": now,
                }
            )
            # The coordinator opening already precedes this effect.  Provider
            # phase ledger records begin with the later REBIND_PLANNED save.
            _atomic(
                self._journal_path(value["transaction_id"]), journal,
                uid=0 if os.geteuid() == 0 else None,
                gid=0 if os.geteuid() == 0 else None,
            )
        else:
            raise ActorFleetError("actor coordinator binding is not legal")
        return {
            "status": "COORDINATOR_BOUND",
            "transaction_id": value["transaction_id"],
            "binding_sha256": validated["binding_sha256"],
        }

    def nonterminal_transactions(self) -> dict[str, Any]:
        """Return exact hashes needed for an explicit root supersession."""
        transactions = []
        for path in sorted(self.private_state_root.glob("*.actor-provider.json")):
            journal = _read_json(path, "actor fleet convergence journal")
            if journal.get("status") in {"VERIFIED", "ROLLED_BACK"}:
                continue
            transactions.append(
                {
                    "transaction_id": journal.get("transaction_id"),
                    "status": journal.get("status"),
                    "journal_sha256": _hash(journal),
                }
            )
        return {
            "status": "NONTERMINAL_INVENTORY",
            "transactions": transactions,
            "inventory_sha256": _hash(transactions),
        }

    def supersede_transactions(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Bind abandoned nonterminal journals to one explicit successor."""
        if (
            not isinstance(request, Mapping)
            or set(request) != {"schema", "successor_transaction_id", "records"}
            or request.get("schema") != "tgw-fleet-supersession-request/v1"
            or not isinstance(request.get("records"), list)
            or not request["records"]
        ):
            raise ActorFleetError("actor fleet supersession request is invalid")
        successor_id = str(request.get("successor_transaction_id", ""))
        successor = self._journal(successor_id)
        if (
            successor.get("request") is None
            or successor.get("status") in {"NEW", "VERIFIED", "ROLLED_BACK"}
            or not isinstance(successor.get("coordinator_binding"), Mapping)
        ):
            raise ActorFleetError("actor fleet supersession successor is invalid")
        planned_records: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in request["records"]:
            if (
                not isinstance(raw, Mapping)
                or set(raw) != {
                    "transaction_id", "journal_sha256", "disposition"
                }
            ):
                raise ActorFleetError("actor fleet supersession record is invalid")
            old_id = str(raw.get("transaction_id", ""))
            if old_id == successor_id or old_id in seen:
                raise ActorFleetError("actor fleet supersession identity is invalid")
            seen.add(old_id)
            old = self._journal(old_id)
            if (
                old.get("request") is None
                or old.get("status") in {"NEW", "VERIFIED", "ROLLED_BACK"}
                or raw.get("journal_sha256") != _hash(old)
                or raw.get("disposition") not in {
                    "ABANDONED_NONTERMINAL", "SUPERSEDED_FOR_SUCCESSOR_REPAIR"
                }
            ):
                raise ActorFleetError("actor fleet supersession binding differs")
            body = {
                "schema": "tgw-fleet-supersession/v1",
                "superseded_transaction_id": old_id,
                "superseded_journal_sha256": raw["journal_sha256"],
                "disposition": raw["disposition"],
                "successor_transaction_id": successor_id,
                "recorded_at": self._current_time().astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                ).replace("+00:00", "Z"),
            }
            record = {**body, "supersession_sha256": _hash(body)}
            planned_records.append(record)
        planned_records.sort(key=lambda item: str(item["superseded_transaction_id"]))
        pointer_body = {
            "schema": "tgw-active-fleet-transaction/v1",
            "transaction_id": successor_id,
            "updated_at": self._current_time().astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
        }
        pointer = {**pointer_body, "pointer_sha256": _hash(pointer_body)}
        plan_body = {
            "schema": "tgw-fleet-supersession-plan/v1",
            "successor_transaction_id": successor_id,
            "records": planned_records,
            "apply_order": [
                str(item["superseded_transaction_id"])
                for item in planned_records
            ],
            # Supersession evidence is immutable history.  Recovery resumes
            # forward in apply_order and never deletes or rewrites records.
            "rollback_order": [],
            "removal_order": [],
            "active_pointer": pointer,
        }
        plan = {
            **plan_body,
            "plan_sha256": _hash(plan_body),
            "phase": "PLANNED",
        }
        existing_plan = successor.get("supersession_plan")
        if isinstance(existing_plan, Mapping):
            existing_stable = dict(existing_plan)
            existing_stable.pop("phase", None)
            planned_stable = dict(plan)
            planned_stable.pop("phase", None)
            if existing_stable != planned_stable:
                raise ActorFleetError("actor fleet supersession plan already differs")
            plan = dict(existing_plan)
        else:
            successor["supersession_plan"] = plan
            # This private plan and its append-once provider ledger entry are
            # durable before the supersession directory, records, or pointer.
            self._save(successor, allow_ambiguous=True)

        root = self._fleet_supersession_root
        if not root.exists() and not root.is_symlink():
            root.mkdir(mode=0o750)
            os.chmod(root, 0o750)
            if os.geteuid() == 0:
                os.chown(root, 0, self.actor_group_gid)
            directory_fd = os.open(
                self.state_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        if root.is_symlink() or not root.is_dir():
            raise ActorFleetError("actor fleet supersession root is unsafe")
        root_state = root.stat(follow_symlinks=False)
        if (
            stat.S_IMODE(root_state.st_mode) != 0o750
            or root_state.st_gid != self.actor_group_gid
            or (os.geteuid() == 0 and root_state.st_uid != 0)
        ):
            raise ActorFleetError("actor fleet supersession root is not protected")
        written: list[str] = []
        hashes: list[str] = []
        for record in plan["records"]:
            old_id = str(record["superseded_transaction_id"])
            destination = root / f"{old_id}.json"
            if destination.exists() or destination.is_symlink():
                existing = _read_json(destination, "actor fleet supersession")
                if existing != record:
                    raise ActorFleetError("actor fleet supersession already differs")
            else:
                _atomic(
                    destination, record, mode=0o640,
                    uid=0 if os.geteuid() == 0 else None,
                    gid=self.actor_group_gid,
                )
            written.append(old_id)
            hashes.append(str(record["supersession_sha256"]))
        self._write_active_fleet_pointer(
            successor_id, planned=plan["active_pointer"]
        )
        plan["phase"] = "APPLIED"
        successor["supersession_plan"] = plan
        successor["supersessions"] = sorted(hashes)
        self._save(successor)
        return {
            "status": "SUPERSEDED",
            "successor_transaction_id": successor_id,
            "superseded_transaction_ids": sorted(written),
            "supersessions_sha256": _hash(sorted(hashes)),
        }

    def _is_exact_context_process_for_binding(
        self,
        item: Mapping[str, Any],
        actor: str,
        binding: Mapping[str, Any] | None,
        *,
        require_v3: bool,
    ) -> bool:
        """Verify one child from protected files, never from claimed env alone."""

        if not isinstance(binding, Mapping):
            return False
        schema = binding.get("schema")
        if schema not in {
            "tgw-actor-startup-binding/v1", "tgw-actor-startup-binding/v2",
            "tgw-actor-startup-binding/v3",
        } or (require_v3 and schema != "tgw-actor-startup-binding/v3"):
            return False
        try:
            generation = str(binding["expected_generation"])
            source_commit = str(binding["expected_source_commit"])
            catalog_hash = str(binding["expected_catalog_hash"])
            if (
                _HASH.fullmatch(generation) is None
                or _COMMIT.fullmatch(source_commit) is None
                or _HASH.fullmatch(catalog_hash) is None
                or binding.get("actor") != actor
            ):
                return False
            generation_root = self.actor_generation_root / generation.removeprefix(
                "sha256:"
            )
            environment_catalog = _read_json(
                generation_root / "environment-catalog.json",
                "bound actor environment catalog",
            )
            generation_receipt = _read_json(
                generation_root / "generation-receipt.json",
                "bound actor generation receipt",
            )
            contract = _read_json(
                generation_root / "contracts" / f"{actor}.json",
                f"bound actor contract {actor}",
            )
            trusted_contract_public_key, _trusted_key_bytes = (
                _contract_public_key(
                    binding.get("trusted_public_key"),
                    "bound actor contract verifier",
                )
            )
            unsigned_generation_receipt = dict(generation_receipt)
            generation_receipt_hash = unsigned_generation_receipt.pop(
                "receipt_hash", None
            )
            contract_receipt_hashes = generation_receipt.get(
                "contract_receipt_hashes"
            )
            generation_identity = generation_receipt.get("generation_identity")
            if not isinstance(generation_identity, Mapping):
                return False
            if schema == "tgw-actor-startup-binding/v1":
                if set(binding) != {
                    "schema", "actor", "trusted_public_key",
                    "expected_generation", "expected_plan_commit",
                    "expected_solution_hash", "expected_source_commit",
                    "expected_catalog_hash",
                }:
                    return False
                source_tree = str(generation_identity.get("source_tree", ""))
                source_root_value = generation_identity.get(
                    "context_source_root"
                )
            else:
                source_tree = str(binding["expected_source_tree"])
                source_root_value = binding["context_source_root"]
            if (
                _COMMIT.fullmatch(source_tree) is None
                or generation_identity.get("source_commit") != source_commit
                or generation_identity.get("source_tree") != source_tree
                or generation_identity.get("catalog_hash") != catalog_hash
                or generation_identity.get("plan_commit")
                != binding.get("expected_plan_commit")
                or generation_identity.get("solution_hash")
                != binding.get("expected_solution_hash")
            ):
                return False
            profile = str(contract["profile"])
            git_tools = [
                tool for tool in environment_catalog.get("profiles", {})
                .get(profile, {}).get("tools", [])
                if isinstance(tool, Mapping) and tool.get("name") == "git"
            ]
            if (
                _hash(environment_catalog) != catalog_hash
                or generation_receipt_hash
                != _hash(unsigned_generation_receipt)
                or generation_receipt.get("status") != "PREPARED"
                or generation_receipt.get("generation") != generation
                or generation_receipt.get("signer_public_key")
                != trusted_contract_public_key
                or not isinstance(contract_receipt_hashes, Mapping)
                or contract_receipt_hashes.get(actor)
                != contract.get("receipt_hash")
                or not _actor_contract_is_signed_by(
                    contract, trusted_contract_public_key
                )
                or contract.get("actor") != actor
                or contract.get("catalog_hash") != catalog_hash
                or contract.get("plan") != {
                    "commit": binding.get("expected_plan_commit"),
                    "solution_hash": binding.get("expected_solution_hash"),
                }
                or contract.get("code_graph", {}).get("commit") != source_commit
                or contract.get("code_graph", {}).get("tree") != source_tree
                or len(git_tools) != 1
            ):
                return False
            git_path = Path(str(git_tools[0]["executable_path"]))
            git_state = git_path.stat(follow_symlinks=False)
            if (
                git_path.is_symlink()
                or not stat.S_ISREG(git_state.st_mode)
                or "sha256:" + hashlib.sha256(git_path.read_bytes()).hexdigest()
                != git_tools[0].get("executable_sha256")
            ):
                return False
            source_root, observed_commit, observed_tree = validate_context_source(
                Path(str(source_root_value)),
                str(git_path),
                expected_commit=source_commit,
                expected_tree=source_tree,
            )
            if observed_commit != source_commit or observed_tree != source_tree:
                return False
            entrypoint = source_root / "scripts" / "tgw_actor_startup.py"
            startup_module = source_root / "src" / "tgw" / "actor_startup.py"
            context_module = source_root / "src" / "tgw" / "context_mcp_server.py"
            runtime_paths = (entrypoint, startup_module, context_module)
            if any(path.is_symlink() or not path.is_file() for path in runtime_paths):
                return False

            def raw_sha(path: Path) -> str:
                return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

            home = Path(pwd.getpwnam(actor).pw_dir)
            if schema == "tgw-actor-startup-binding/v3":
                stable_launcher = Path(str(binding.get("stable_launcher_path", "")))
                stable_state = stable_launcher.stat(follow_symlinks=False)
                stable_parent_state = stable_launcher.parent.stat(
                    follow_symlinks=False
                )
                if (
                    stable_launcher != _STABLE_CONTEXT_LAUNCHER
                    or stable_launcher.is_symlink()
                    or not stat.S_ISREG(stable_state.st_mode)
                    or stable_state.st_uid != 0
                    or stable_state.st_mode & 0o022
                    or stable_state.st_nlink != 1
                    or stable_launcher.parent.is_symlink()
                    or not stat.S_ISDIR(stable_parent_state.st_mode)
                    or stable_parent_state.st_uid != 0
                    or stable_parent_state.st_mode & 0o022
                ):
                    return False
            else:
                stable_launcher = home / ".local/bin/tgw-actor"
                stable_target = stable_launcher.resolve(strict=True)
                stable_state = stable_target.stat(follow_symlinks=False)
                if (
                    not stable_launcher.is_symlink()
                    or not stat.S_ISREG(stable_state.st_mode)
                    or stable_state.st_uid != 0
                    or stable_state.st_mode & 0o022
                ):
                    return False
            if stable_launcher.read_bytes() != entrypoint.read_bytes():
                return False
            shebang = entrypoint.read_bytes().splitlines()[0].decode("ascii")
            if not shebang.startswith("#!/") or " " in shebang:
                return False
            executable_argument = shebang.removeprefix("#!")
            executable = Path(executable_argument).resolve(strict=True)
            executable_state = executable.stat(follow_symlinks=False)
            executable_sha256 = raw_sha(executable)
            runtime_mode = bool(item.get("runtime_entrypoint"))
            binding_path = self.startup_binding_root / f"{actor}-startup.json"
            cache_root = (
                self.actor_cache_root / actor / generation.removeprefix("sha256:")
                / "context-mcp"
            )
            common_environment = {
                "TGW_CONTEXT_PLAN_COMMIT": str(binding["expected_plan_commit"]),
                "TGW_CONTEXT_PLAN_SOLUTION": str(binding["expected_solution_hash"]),
                "TGW_CONTEXT_PLAN_REPOSITORY": "/opt/TGW/library/plans",
                "TGW_CONTEXT_PLAN_ROOT": (
                    f"/opt/TGW/library/approved/{binding['expected_plan_commit']}"
                ),
                "TGW_CONTEXT_SOURCE_ROOT": str(source_root),
                "TGW_CONTEXT_RUNTIME_ROOT": "/opt/TGW/tgw-lib/var/context",
                "TGW_CONTEXT_ENVIRONMENT_CATALOG": (
                    "/etc/tgw/execution-environment-catalog.json"
                ),
                "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": catalog_hash,
                "TGW_CONTEXT_ACTOR": actor,
                "TGW_CONTEXT_GENERATION": generation,
                "TGW_CONTEXT_SOURCE_COMMIT": source_commit,
                "TGW_CONTEXT_SOURCE_TREE": source_tree,
                "TGW_CONTEXT_STARTUP_BINDING": str(binding_path),
                "HOME": str(home),
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "TMPDIR": str(cache_root),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "safe.directory",
                "GIT_CONFIG_VALUE_0": str(source_root),
                "PATH": f"{git_path.parent}:/usr/bin:/bin",
            }
            if runtime_mode:
                fleet_path = binding.get("fleet_convergence_path")
                if not isinstance(fleet_path, str) or not Path(fleet_path).is_absolute():
                    return False
                expected_arguments = [
                    str(executable), "-I", "-s", "-P", str(entrypoint),
                    "--context-mcp-runtime", "--context-mcp",
                    "--context-mcp-stable-launcher", str(stable_launcher),
                ]
                expected_environment = {
                    **common_environment,
                    "TGW_CONTEXT_ENDPOINT": "tgw-context",
                    "TGW_CONTEXT_PROFILE": profile,
                    "TGW_CONTEXT_FLEET_CONVERGENCE": fleet_path,
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONSAFEPATH": "1",
                    "TGW_CONTEXT_RUNTIME_ENTRYPOINT": str(entrypoint),
                    "TGW_CONTEXT_RUNTIME_ENTRYPOINT_SHA256": raw_sha(entrypoint),
                    "TGW_CONTEXT_RUNTIME_MODULE": str(startup_module),
                    "TGW_CONTEXT_RUNTIME_MODULE_SHA256": raw_sha(startup_module),
                    "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE": str(context_module),
                    "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE_SHA256": raw_sha(
                        context_module
                    ),
                    "TGW_CONTEXT_STABLE_LAUNCHER": str(stable_launcher),
                    "TGW_CONTEXT_STABLE_LAUNCHER_SHA256": raw_sha(
                        stable_launcher
                    ),
                    "TGW_CONTEXT_RUNTIME_EXECUTABLE": str(executable),
                    "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256": executable_sha256,
                    "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE": str(
                        executable_state.st_dev
                    ),
                    "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE": str(
                        executable_state.st_ino
                    ),
                }
            else:
                if schema not in {
                    "tgw-actor-startup-binding/v1",
                    "tgw-actor-startup-binding/v2",
                }:
                    return False
                expected_arguments = [
                    executable_argument, str(stable_launcher), "--context-mcp"
                ]
                if schema == "tgw-actor-startup-binding/v1":
                    expected_environment = {
                        name: value for name, value in common_environment.items()
                        if name not in {
                            "TGW_CONTEXT_ACTOR", "TGW_CONTEXT_GENERATION",
                            "TGW_CONTEXT_SOURCE_COMMIT",
                            "TGW_CONTEXT_SOURCE_TREE",
                            "TGW_CONTEXT_STARTUP_BINDING",
                        }
                    }
                else:
                    expected_environment = common_environment
            expected_shape = [Path(expected_arguments[0]).name]
            expected_shape.extend(
                argument for argument in expected_arguments[1:]
                if argument.startswith("--")
                or argument in {"-m", "tgw.context_mcp_server"}
            )
        except (
            ContextSourceGuardError, KeyError, OSError, TypeError,
            UnicodeDecodeError, ValueError,
        ):
            return False
        shared_exact = (
            item.get("actor") == actor
            and item.get("source_root") == str(source_root)
            and item.get("catalog") == catalog_hash
            and item.get("arguments") == expected_arguments
            and item.get("cmdline_shape") == expected_shape
            and item.get("cmdline_sha256") == _hash(expected_arguments)
            and item.get("executable_path") == str(executable)
            and item.get("executable_device") == executable_state.st_dev
            and item.get("executable_inode") == executable_state.st_ino
            and item.get("executable_sha256") == executable_sha256
            and item.get("environment_keys") == sorted(expected_environment)
            and item.get("environment_sha256") == _hash(expected_environment)
        )
        if schema == "tgw-actor-startup-binding/v1":
            exact = (
                shared_exact
                and runtime_mode is False
                and item.get("guarded") is False
                and item.get("startup_binding") == ""
                and item.get("generation") == ""
                and item.get("plan") == binding.get("expected_plan_commit")
                and item.get("solution")
                == binding.get("expected_solution_hash")
                and item.get("source_commit") == ""
                and item.get("source_tree") == ""
            )
        else:
            exact = (
                shared_exact
                and item.get("guarded") is True
                and item.get("startup_binding") == str(binding_path)
                and item.get("generation") == generation
                and item.get("plan") == binding.get("expected_plan_commit")
                and item.get("solution")
                == binding.get("expected_solution_hash")
                and item.get("source_commit") == source_commit
                and item.get("source_tree") == source_tree
            )
        if not exact:
            return False
        runtime_expected = {
            "runtime_entrypoint": str(entrypoint),
            "runtime_entrypoint_sha256": raw_sha(entrypoint),
            "runtime_module": str(startup_module),
            "runtime_module_sha256": raw_sha(startup_module),
            "runtime_context_module": str(context_module),
            "runtime_context_module_sha256": raw_sha(context_module),
            "stable_launcher_path": str(stable_launcher),
            "stable_launcher_sha256": raw_sha(stable_launcher),
            "runtime_executable": str(executable),
            "runtime_executable_sha256": executable_sha256,
            "runtime_executable_device": str(executable_state.st_dev),
            "runtime_executable_inode": str(executable_state.st_ino),
        }
        if runtime_mode:
            return item.get("stable_launcher") is (
                stable_launcher == _STABLE_CONTEXT_LAUNCHER
            ) and all(item.get(name) == expected for name, expected in runtime_expected.items())
        return (
            item.get("stable_launcher") is False
            and all(item.get(name) == "" for name in runtime_expected)
        )

    def _is_current_context_process(
        self,
        item: Mapping[str, Any],
        request: Mapping[str, Any],
    ) -> bool:
        actor = str(item.get("actor"))
        if actor not in request["actors"]:
            return False
        binding = self._startup_binding(actor, request)
        return self._is_exact_context_process_for_binding(
            item, actor, binding, require_v3=True
        )

    def _context_inventory_state(
        self,
        processes: list[Mapping[str, Any]],
        request: Mapping[str, Any],
    ) -> str:
        if not processes:
            return "IDLE_NO_LIVE"
        if all(self._is_current_context_process(item, request) for item in processes):
            return "ACTIVE_SUCCESSOR"
        return "STALE_OR_MIXED"

    @staticmethod
    def _freeze_context_obligations(
        actors: list[str],
        processes: list[Mapping[str, Any]],
        *,
        direction: str,
    ) -> list[dict[str, Any]]:
        obligations: list[dict[str, Any]] = []
        for actor in actors:
            actor_processes = [dict(item) for item in processes if item.get("actor") == actor]
            if not actor_processes:
                obligations.append(
                    {
                        "obligation_id": _hash(
                            {"direction": direction, "actor": actor, "state": "IDLE"}
                        ),
                        "actor": actor,
                        "baseline_state": "IDLE",
                        "baseline": None,
                        "replacement_policy": "WAIT_EXTERNAL_RESTART",
                    }
                )
                continue
            paths: dict[str, list[dict[str, Any]]] = {}
            for process in actor_processes:
                parent = process.get("parent")
                path_identity = {
                    "actor": actor,
                    "endpoint": process.get("endpoint", "tgw-context"),
                    "profile": process.get("profile", ""),
                    "parent_identity_hash": (
                        parent.get("identity_hash")
                        if isinstance(parent, Mapping) else _hash(parent)
                    ),
                }
                paths.setdefault(_hash(path_identity), []).append(process)
            for path_hash, children in sorted(paths.items()):
                children.sort(key=lambda item: str(item.get("identity_hash")))
                parent = children[0].get("parent")
                obligations.append(
                    {
                        "obligation_id": _hash(
                            {
                                "direction": direction,
                                "actor": actor,
                                "path_identity_hash": path_hash,
                            }
                        ),
                        "actor": actor,
                        "baseline_state": "LIVE",
                        "baseline": {
                            "path_identity_hash": path_hash,
                            "endpoint": children[0].get("endpoint", "tgw-context"),
                            "profile": children[0].get("profile", ""),
                            "parent": parent,
                            "children": children,
                            "child_identity_hashes": [
                                item.get("identity_hash") for item in children
                            ],
                        },
                        # No checked-in harness promises automatic child
                        # replacement; Deepseek explicitly sets reconnect=false.
                        "replacement_policy": "WAIT_EXTERNAL_RESTART",
                    }
                )
        return obligations

    @staticmethod
    def _same_logical_parent(
        observed: Mapping[str, Any] | None,
        baseline: Mapping[str, Any] | None,
    ) -> bool:
        return (
            isinstance(observed, Mapping)
            and isinstance(baseline, Mapping)
            and all(
                observed.get(name) == baseline.get(name)
                for name in (
                    "boot_id", "pid", "start_ticks", "uid",
                    "executable_path", "executable_device",
                    "executable_inode", "executable_sha256",
                    "cmdline_shape", "cmdline_sha256", "identity_hash",
                )
            )
        ) or observed is None and baseline is None

    def _parent_matches_obligation(
        self,
        observed: Mapping[str, Any] | None,
        baseline: Mapping[str, Any] | None,
        obligation_id: str,
        transitions: Mapping[str, Any] | None,
    ) -> bool:
        if self._same_logical_parent(observed, baseline):
            return True
        transition = (
            transitions.get(obligation_id)
            if isinstance(transitions, Mapping) else None
        )
        return (
            isinstance(observed, Mapping)
            and isinstance(baseline, Mapping)
            and isinstance(transition, Mapping)
            and transition.get("old_parent_identity_hash")
            == baseline.get("identity_hash")
            and transition.get("new_parent_identity_hash")
            == observed.get("identity_hash")
        )

    def _capture_late_context_paths(
        self,
        journal: dict[str, Any],
        processes: list[Mapping[str, Any]],
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Append new children/paths without regenerating frozen obligations."""

        rebind = journal.get("context_rebind")
        if not isinstance(rebind, Mapping) or not isinstance(
            rebind.get("obligations"), list
        ):
            raise ActorFleetError("actor Context rebind obligations are unavailable")
        updated = dict(rebind)
        obligations = [dict(item) for item in rebind["obligations"]]
        transitions = (
            rebind.get("parent_transitions", {})
            if isinstance(rebind.get("parent_transitions", {}), Mapping)
            else {}
        )
        direction = str(rebind.get("direction"))
        target_bindings = rebind.get("target_bindings")

        def is_target(process: Mapping[str, Any]) -> bool:
            actor = str(process.get("actor"))
            if direction == "rollback":
                target = (
                    target_bindings.get(actor)
                    if isinstance(target_bindings, Mapping) else None
                )
                return self._is_context_process_for_startup_binding(
                    process, actor, target
                )
            return self._is_current_context_process(process, request)

        known_identities = {
            str(identity)
            for obligation in obligations
            for baseline in [obligation.get("baseline")]
            if isinstance(baseline, Mapping)
            for name in (
                "child_identity_hashes", "target_child_identity_hashes"
            )
            for identity in baseline.get(name, [])
        }
        new_processes = [
            dict(item) for item in processes
            if str(item.get("identity_hash")) not in known_identities
        ]
        unrepresented: list[dict[str, Any]] = []
        path_additions: list[dict[str, Any]] = []
        for process in new_processes:
            matches: list[int] = []
            for index, obligation in enumerate(obligations):
                baseline = obligation.get("baseline")
                if (
                    not isinstance(baseline, Mapping)
                    or obligation.get("actor") != process.get("actor")
                    or baseline.get("endpoint")
                    != process.get("endpoint", "tgw-context")
                    or baseline.get("profile") != process.get("profile", "")
                    or not self._parent_matches_obligation(
                        process.get("parent"), baseline.get("parent"),
                        str(obligation.get("obligation_id")), transitions,
                    )
                ):
                    continue
                matches.append(index)
            if len(matches) > 1:
                raise ActorFleetError(
                    "actor Context late child path is ambiguously represented"
                )
            if not matches:
                if (
                    isinstance(process.get("parent"), Mapping)
                    and isinstance(
                        process.get("parent", {}).get("identity_hash"), str
                    )
                ):
                    unrepresented.append(process)
                else:
                    # A captured child with a changed/inaccessible parent may
                    # never disappear into an idle inventory classification.
                    path_additions.append(
                        {
                            "disposition": "LATE_ARRIVAL_UNCLASSIFIED",
                            "child_identity_hash": process.get("identity_hash"),
                        }
                    )
                continue
            index = matches[0]
            obligation = dict(obligations[index])
            baseline = dict(obligation["baseline"])
            identity = str(process.get("identity_hash"))
            target = is_target(process)
            identity_field = (
                "target_child_identity_hashes"
                if target else "child_identity_hashes"
            )
            identities = {
                str(item) for item in baseline.get(identity_field, [])
            }
            identities.add(identity)
            baseline[identity_field] = sorted(identities)
            late_children = list(baseline.get("late_children", []))
            late_children.append(process)
            baseline["late_children"] = late_children
            obligation["baseline"] = baseline
            obligations[index] = obligation
            path_additions.append(
                {
                    "disposition": (
                        "LATE_ARRIVAL_CURRENT"
                        if target else "LATE_ARRIVAL_STALE"
                    ),
                    "obligation_id": obligation.get("obligation_id"),
                    "path_identity_hash": baseline.get("path_identity_hash"),
                    "child_identity_hash": identity,
                    "parent_identity_hash": (
                        process.get("parent", {}).get("identity_hash")
                        if isinstance(process.get("parent"), Mapping) else None
                    ),
                }
            )

        groups: dict[str, list[dict[str, Any]]] = {}
        for process in unrepresented:
            path_identity = {
                "actor": process.get("actor"),
                "endpoint": process.get("endpoint", "tgw-context"),
                "profile": process.get("profile", ""),
                "parent_identity_hash": process["parent"]["identity_hash"],
            }
            groups.setdefault(_hash(path_identity), []).append(process)
        appended: list[str] = []
        for path_hash, children in sorted(groups.items()):
            children.sort(key=lambda item: str(item.get("identity_hash")))
            actor = str(children[0].get("actor"))
            target_children = [item for item in children if is_target(item)]
            stale_children = [item for item in children if not is_target(item)]
            state = "LATE_CURRENT" if not stale_children else "LATE_STALE"
            obligation_id = _hash(
                {
                    "direction": direction,
                    "late_arrival": True,
                    "actor": actor,
                    "path_identity_hash": path_hash,
                    "children": [
                        item.get("identity_hash") for item in children
                    ],
                }
            )
            obligations.append(
                {
                    "obligation_id": obligation_id,
                    "actor": actor,
                    "baseline_state": state,
                    "checkpoint_disposition": "LATE_ARRIVAL",
                    "baseline": {
                        "path_identity_hash": path_hash,
                        "endpoint": children[0].get(
                            "endpoint", "tgw-context"
                        ),
                        "profile": children[0].get("profile", ""),
                        "parent": children[0].get("parent"),
                        "children": children,
                        "child_identity_hashes": [
                            item.get("identity_hash") for item in stale_children
                        ],
                        "target_child_identity_hashes": [
                            item.get("identity_hash") for item in target_children
                        ],
                    },
                    "replacement_policy": "WAIT_EXTERNAL_RESTART",
                }
            )
            appended.append(obligation_id)
        if appended or path_additions:
            updated["obligations"] = obligations
            arrivals = list(updated.get("late_arrivals", []))
            arrivals.append(
                {
                    "disposition": "LATE_ARRIVAL",
                    "obligation_ids": appended,
                    "path_additions": path_additions,
                    "observed_sha256": _hash(new_processes),
                    "captured_at": self._current_time().astimezone(
                        timezone.utc
                    ).isoformat(timespec="seconds").replace("+00:00", "Z"),
                }
            )
            updated["late_arrivals"] = arrivals
            journal["context_rebind"] = updated
            self._save(journal)
        return updated


    def _reconcile_context_obligations(
        self,
        obligations: list[Mapping[str, Any]],
        processes: list[Mapping[str, Any]],
        request: Mapping[str, Any],
        confirmations: Mapping[str, Any] | None = None,
        parent_transitions: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        tracked_child_identities = {
            str(identity)
            for obligation in obligations
            if isinstance(obligation, Mapping)
            for baseline in [obligation.get("baseline")]
            if isinstance(baseline, Mapping)
            for identity in baseline.get("child_identity_hashes", [])
        }
        stale = [
            item for item in processes
            if not self._is_current_context_process(item, request)
            and str(item.get("identity_hash")) not in tracked_child_identities
        ]
        pending: list[dict[str, Any]] = []
        dispositions: list[dict[str, Any]] = []
        if stale:
            pending.append(
                {
                    "reason": "UNEXPECTED_STALE_OR_UNCLASSIFIED_PATH",
                    "identities": [item.get("identity_hash") for item in stale],
                }
            )
        used: set[str] = set()
        identities = {str(item.get("identity_hash")) for item in processes}
        late_actors = {
            str(item.get("actor")) for item in obligations
            if item.get("baseline_state") in {"LATE_CURRENT", "LATE_STALE"}
        }
        for obligation in obligations:
            actor = str(obligation.get("actor"))
            obligation_id = str(obligation.get("obligation_id"))
            actor_processes = [item for item in processes if item.get("actor") == actor]
            if obligation.get("baseline_state") == "IDLE":
                if actor in late_actors:
                    dispositions.append(
                        {
                            "obligation_id": obligation_id,
                            "disposition": "IDLE_BASELINE_LATE_PATH_TRACKED",
                        }
                    )
                    continue
                confirmation = (
                    confirmations.get(obligation_id)
                    if isinstance(confirmations, Mapping) else None
                )
                current = [
                    item for item in actor_processes
                    if self._is_current_context_process(item, request)
                ]
                if not actor_processes:
                    if (
                        not isinstance(confirmation, Mapping)
                        or confirmation.get("transaction_id")
                        != request["transaction_id"]
                        or confirmation.get("direction") != "successor"
                        or confirmation.get("actor") != actor
                        or not isinstance(
                            confirmation.get("parent_identity_hash"), str
                        )
                        or confirmation.get("process_identity_hash") in identities
                    ):
                        pending.append(
                            {
                                "obligation_id": obligation_id,
                                "reason": "ORDINARY_HARNESS_HANDOFF_REQUIRED",
                            }
                        )
                    else:
                        dispositions.append(
                            {
                                "obligation_id": obligation_id,
                                "disposition": "IDLE_CONFIRMED_HANDOFF_COMPLETE",
                                "client_confirmation_hash": confirmation.get(
                                    "confirmation_hash"
                                ),
                            }
                        )
                elif len(current) != 1 or len(actor_processes) != 1:
                    pending.append(
                        {
                            "obligation_id": obligation_id,
                            "reason": "IDLE_TO_CURRENT_PATH_NOT_UNIQUE",
                        }
                    )
                else:
                    successor = current[0]
                    identity = str(successor.get("identity_hash"))
                    parent = successor.get("parent")
                    if (
                        not isinstance(confirmation, Mapping)
                        or confirmation.get("transaction_id")
                        != request["transaction_id"]
                        or confirmation.get("direction") != "successor"
                        or confirmation.get("actor") != actor
                        or confirmation.get("process_identity_hash") != identity
                        or confirmation.get("parent_identity_hash")
                        != (parent.get("identity_hash") if isinstance(parent, Mapping) else None)
                    ):
                        pending.append(
                            {
                                "obligation_id": obligation_id,
                                "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
                                "process_identity_hash": identity,
                            }
                        )
                    else:
                        used.add(identity)
                        dispositions.append(
                            {
                                "obligation_id": obligation_id,
                                "disposition": "IDLE_TO_CURRENT_CONFIRMED",
                                "successor_identity_hash": identity,
                                "client_confirmation_hash": confirmation.get(
                                    "confirmation_hash"
                                ),
                            }
                        )
                continue
            baseline = obligation.get("baseline")
            if obligation.get("baseline_state") == "LATE_CURRENT":
                baseline_parent = (
                    baseline.get("parent") if isinstance(baseline, Mapping) else None
                )
                path_processes = [
                    item for item in actor_processes
                    if isinstance(baseline, Mapping)
                    and item.get("endpoint", "tgw-context")
                    == baseline.get("endpoint")
                    and item.get("profile", "") == baseline.get("profile")
                    and self._parent_matches_obligation(
                        item.get("parent"), baseline_parent, obligation_id,
                        parent_transitions,
                    )
                ]
                current = [
                    item for item in path_processes
                    if self._is_current_context_process(item, request)
                ]
                confirmation = (
                    confirmations.get(obligation_id)
                    if isinstance(confirmations, Mapping) else None
                )
                if not path_processes:
                    if (
                        not isinstance(confirmation, Mapping)
                        or confirmation.get("transaction_id")
                        != request["transaction_id"]
                        or confirmation.get("direction") != "successor"
                        or confirmation.get("actor") != actor
                        or confirmation.get("process_identity_hash") in identities
                    ):
                        pending.append(
                            {
                                "obligation_id": obligation_id,
                                "reason": "LATE_CURRENT_HANDOFF_REQUIRED",
                            }
                        )
                    else:
                        dispositions.append(
                            {
                                "obligation_id": obligation_id,
                                "disposition": "LATE_CURRENT_HANDOFF_COMPLETE",
                                "client_confirmation_hash": confirmation.get(
                                    "confirmation_hash"
                                ),
                            }
                        )
                    continue
                if len(path_processes) != 1 or len(current) != 1:
                    pending.append(
                        {
                            "obligation_id": obligation_id,
                            "reason": "LATE_CURRENT_PATH_NOT_UNIQUE",
                        }
                    )
                    continue
                successor = current[0]
                identity = str(successor.get("identity_hash"))
                parent = successor.get("parent")
                if (
                    not isinstance(confirmation, Mapping)
                    or confirmation.get("transaction_id")
                    != request["transaction_id"]
                    or confirmation.get("direction") != "successor"
                    or confirmation.get("actor") != actor
                    or confirmation.get("process_identity_hash") != identity
                    or confirmation.get("parent_identity_hash")
                    != (
                        parent.get("identity_hash")
                        if isinstance(parent, Mapping) else None
                    )
                ):
                    pending.append(
                        {
                            "obligation_id": obligation_id,
                            "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
                            "process_identity_hash": identity,
                        }
                    )
                    continue
                used.add(identity)
                dispositions.append(
                    {
                        "obligation_id": obligation_id,
                        "disposition": "LATE_CURRENT_CONFIRMED",
                        "successor_identity_hash": identity,
                        "client_confirmation_hash": confirmation.get(
                            "confirmation_hash"
                        ),
                    }
                )
                continue
            old_identities = {
                str(identity) for identity in baseline.get("child_identity_hashes", [])
            } if isinstance(baseline, Mapping) else set()
            surviving = sorted(old_identities & identities)
            if surviving:
                pending.append(
                    {
                        "obligation_id": obligation_id,
                        "reason": "OLD_IDENTITIES_STILL_LIVE",
                        "identities": surviving,
                    }
                )
                continue
            baseline_parent = baseline.get("parent") if isinstance(baseline, Mapping) else None
            candidates = []
            for item in actor_processes:
                identity = str(item.get("identity_hash"))
                if identity in used or not self._is_current_context_process(item, request):
                    continue
                parent = item.get("parent")
                if self._parent_matches_obligation(
                    parent, baseline_parent, obligation_id, parent_transitions
                ):
                    candidates.append(item)
            if len(candidates) != 1:
                pending.append(
                    {"obligation_id": obligation_id, "reason": "CURRENT_SUCCESSOR_PATH_NOT_UNIQUE"}
                )
                continue
            successor = candidates[0]
            used.add(str(successor.get("identity_hash")))
            confirmation = (
                confirmations.get(obligation_id)
                if isinstance(confirmations, Mapping) else None
            )
            successor_parent = successor.get("parent")
            if (
                not isinstance(confirmation, Mapping)
                or confirmation.get("transaction_id") != request["transaction_id"]
                or confirmation.get("direction") != "successor"
                or confirmation.get("actor") != actor
                or confirmation.get("process_identity_hash")
                != successor.get("identity_hash")
                or confirmation.get("parent_identity_hash")
                != (
                    successor_parent.get("identity_hash")
                    if isinstance(successor_parent, Mapping) else None
                )
            ):
                pending.append(
                    {
                        "obligation_id": obligation_id,
                        "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
                        "process_identity_hash": successor.get("identity_hash"),
                    }
                )
                continue
            dispositions.append(
                {
                    "obligation_id": obligation_id,
                    "disposition": "CURRENT_SUCCESSOR_OBSERVED",
                    "successor_identity_hash": successor.get("identity_hash"),
                    "retired_child_identity_hashes": sorted(old_identities),
                    "client_confirmation_hash": confirmation.get("confirmation_hash"),
                }
            )
        unused_current = [
            item.get("identity_hash") for item in processes
            if self._is_current_context_process(item, request)
            and str(item.get("identity_hash")) not in used
        ]
        if unused_current:
            pending.append(
                {
                    "reason": "UNEXPECTED_CURRENT_PATH",
                    "identities": unused_current,
                }
            )
        return pending, dispositions

    def confirm_context_rebind(self, confirmation: Mapping[str, Any]) -> dict[str, Any]:
        """Persist status read through the already-connected harness session."""
        if not isinstance(confirmation, Mapping) or set(confirmation) != {
            "schema", "transaction_id", "direction", "obligation_id", "status",
        } or confirmation.get("schema") != "tgw-context-client-confirmation/v1":
            raise ActorFleetError("actor Context client confirmation is invalid")
        journal = self._journal(str(confirmation.get("transaction_id")))
        rebind = journal.get("context_rebind")
        if (
            journal.get("status") not in {"RESTART_REQUIRED", "ROLLBACK_RESTART_REQUIRED"}
            or not isinstance(rebind, Mapping)
            or rebind.get("direction") != confirmation.get("direction")
            or not isinstance(rebind.get("obligations"), list)
        ):
            raise ActorFleetError("actor Context client confirmation is not expected")
        obligation = next(
            (
                item for item in rebind["obligations"]
                if isinstance(item, Mapping)
                and item.get("obligation_id") == confirmation.get("obligation_id")
            ),
            None,
        )
        if not isinstance(obligation, Mapping) or obligation.get("baseline_state") not in {
            "LIVE", "IDLE", "LATE_CURRENT", "LATE_STALE",
        }:
            raise ActorFleetError("actor Context client confirmation obligation is invalid")
        status = confirmation.get("status")
        if not isinstance(status, Mapping):
            raise ActorFleetError("actor Context client status is invalid")
        unsigned_status = dict(status)
        claimed_status = unsigned_status.pop("context_sha256", None)
        runtime = status.get("runtime")
        process = runtime.get("process") if isinstance(runtime, Mapping) else None
        startup = status.get("startup")
        fleet = status.get("fleet_convergence")
        fleet_transaction = (
            fleet.get("transaction") if isinstance(fleet, Mapping) else None
        )
        projected_obligations = (
            fleet_transaction.get("obligations", [])
            if isinstance(fleet_transaction, Mapping) else []
        )
        if rebind.get("direction") == "rollback":
            target_bindings = rebind.get("target_bindings")
            target = (
                target_bindings.get(str(obligation["actor"]))
                if isinstance(target_bindings, Mapping) else None
            )
            if not isinstance(target, Mapping):
                raise ActorFleetError("actor rollback Context target is idle")
            expected_plan = target.get("expected_plan_commit")
            expected_solution = target.get("expected_solution_hash")
            expected_source = target.get("expected_source_commit")
            expected_catalog = target.get("expected_catalog_hash")
            expected_generation = target.get("expected_generation")
        else:
            revisions = journal["request"]["revisions"]
            expected_plan = revisions["plan"]
            expected_solution = revisions["solution"]
            expected_source = revisions["source"]
            expected_catalog = revisions["catalog"]
            expected_generation = journal["request"]["successor_generation"]
        if (
            claimed_status != _hash(unsigned_status)
            or not isinstance(process, Mapping)
            or not isinstance(startup, Mapping)
            or not isinstance(fleet_transaction, Mapping)
            or fleet_transaction.get("transaction_id")
            != journal["transaction_id"]
            or fleet_transaction.get("direction") != rebind.get("direction")
            or not any(
                isinstance(item, Mapping)
                and item.get("obligation_id") == confirmation.get("obligation_id")
                for item in projected_obligations
            )
            or startup.get("actor") != obligation.get("actor")
            or startup.get("generation") != expected_generation
            or status.get("plan", {}).get("approved_commit") != expected_plan
            or status.get("plan", {}).get("approved_solution_hash") != expected_solution
            or status.get("source", {}).get("commit") != expected_source
            or status.get("environment", {}).get("catalog_hash") != expected_catalog
        ):
            raise ActorFleetError("actor Context client status binding differs")
        inventory = self._actor_context_process_inventory(journal["request"]["actors"])
        current = next(
            (
                item for item in inventory
                if item.get("actor") == obligation.get("actor")
                and item.get("identity_hash") == process.get("identity_hash")
            ),
            None,
        )
        if rebind.get("direction") == "rollback":
            is_current = isinstance(current, Mapping) and self._is_context_process_for_startup_binding(
                current, str(obligation["actor"]), target
            )
        else:
            is_current = isinstance(current, Mapping) and self._is_current_context_process(
                current, journal["request"]
            )
        if not is_current:
            raise ActorFleetError("actor Context confirmed process is not current")
        baseline = obligation.get("baseline")
        baseline_parent = (
            baseline.get("parent") if isinstance(baseline, Mapping) else None
        )
        if obligation.get("baseline_state") == "IDLE":
            updated_rebind = self._capture_late_context_paths(
                journal, inventory, journal["request"]
            )
            parent = current.get("parent")
            replacement = [
                item for item in updated_rebind["obligations"]
                if isinstance(item, Mapping)
                and item.get("baseline_state") == "LATE_CURRENT"
                and item.get("actor") == obligation.get("actor")
                and isinstance(item.get("baseline"), Mapping)
                and self._same_logical_parent(
                    item["baseline"].get("parent"), parent
                )
                and current.get("identity_hash")
                in item["baseline"].get("target_child_identity_hashes", [])
            ]
            if len(replacement) != 1:
                raise ActorFleetError(
                    "actor idle Context path could not be durably classified"
                )
            return {
                "status": "RETRY_REQUIRED",
                "transaction_id": journal["transaction_id"],
                "obligation_id": replacement[0]["obligation_id"],
                "previous_obligation_id": confirmation["obligation_id"],
            }
        if not self._parent_matches_obligation(
                current.get("parent"), baseline_parent,
                str(confirmation["obligation_id"]),
                rebind.get("parent_transitions"),
            ):
            raise ActorFleetError(
                "actor Context confirmed logical client path differs"
            )
        for name in (
            "boot_id", "pid", "start_ticks", "uid", "ppid", "executable_path",
            "executable_device", "executable_inode", "executable_sha256",
            "cmdline_shape", "cmdline_sha256", "identity_hash",
        ):
            if process.get(name) != current.get(name):
                raise ActorFleetError("actor Context client process identity differs")
        parent = current.get("parent")
        if not isinstance(parent, Mapping) or not isinstance(
            parent.get("identity_hash"), str
        ):
            raise ActorFleetError(
                "actor Context ordinary harness parent identity is unavailable"
            )
        record = {
            "schema": "tgw-context-client-confirmation-receipt/v1",
            "transaction_id": journal["transaction_id"],
            "direction": rebind["direction"],
            "obligation_id": confirmation["obligation_id"],
            "actor": obligation["actor"],
            "process_identity_hash": current["identity_hash"],
            "parent_identity_hash": parent["identity_hash"],
            "context_status_hash": claimed_status,
            "confirmed_at": self._current_time().astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
        }
        record["confirmation_hash"] = _hash(record)
        updated = dict(rebind)
        confirmations = dict(updated.get("confirmations", {}))
        confirmations[str(confirmation["obligation_id"])] = record
        updated["confirmations"] = confirmations
        journal["context_rebind"] = updated
        self._save(journal)
        return {
            "status": "CONFIRMED",
            "transaction_id": journal["transaction_id"],
            "obligation_id": confirmation["obligation_id"],
            "confirmation_hash": record["confirmation_hash"],
        }

    def _coordinator_private_receipt(
        self, transaction_id: str, filename: str, label: str
    ) -> dict[str, Any]:
        """Read one fixed root-private coordinator receipt through a bound dirfd."""

        if filename not in {
            "cold-continuity-receipt.json", "deepseek-service-action.json"
        }:
            raise ActorFleetError(f"{label} identity is invalid")
        transaction_root = self.coordinator_transaction_root / transaction_id
        try:
            root_fd = os.open(
                transaction_root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            try:
                root_state = os.fstat(root_fd)
                descriptor = os.open(
                    filename,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=root_fd,
                )
                try:
                    before = os.fstat(descriptor)
                    chunks: list[bytes] = []
                    size = 0
                    while True:
                        chunk = os.read(descriptor, 64 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 1024 * 1024:
                            raise ActorFleetError(f"{label} exceeds its bound")
                        chunks.append(chunk)
                    after = os.fstat(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                os.close(root_fd)
            value = json.loads(b"".join(chunks))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ActorFleetError(f"{label} is invalid") from exc
        if (
            not isinstance(value, dict)
            or not stat.S_ISDIR(root_state.st_mode)
            or stat.S_IMODE(root_state.st_mode) != 0o700
            or not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_nlink != 1
            or (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns
            )
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (
                os.geteuid() == 0
                and (
                    root_state.st_uid != 0
                    or root_state.st_gid != 0
                    or before.st_uid != 0
                    or before.st_gid != 0
                )
            )
        ):
            raise ActorFleetError(f"{label} is not protected")
        return value

    def record_context_parent_transition(
        self, transition: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Record one root-coordinator-attested harness parent replacement."""

        fields = {
            "schema", "transaction_id", "direction", "obligation_id",
            "disposition", "service_unit", "old_parent_identity_hash",
            "new_parent_identity_hash", "action_receipt_sha256",
            "transition_sha256",
        }
        if not isinstance(transition, Mapping) or set(transition) != fields:
            raise ActorFleetError("actor Context parent transition is invalid")
        unsigned = dict(transition)
        claimed = unsigned.pop("transition_sha256", None)
        disposition = transition.get("disposition")
        service_unit = transition.get("service_unit")
        if (
            transition.get("schema") != "tgw-context-parent-transition/v1"
            or transition.get("direction") not in {"successor", "rollback"}
            or disposition not in {
                "OPERATOR_HARNESS_RESTART", "DECLARED_USER_SERVICE_RESTART"
            }
            or (
                disposition == "DECLARED_USER_SERVICE_RESTART"
                and (
                    not isinstance(service_unit, str)
                    or _UNIT.fullmatch(service_unit) is None
                    or not service_unit.endswith(".service")
                )
            )
            or (
                disposition == "OPERATOR_HARNESS_RESTART"
                and service_unit != ""
            )
            or any(
                _HASH.fullmatch(str(transition.get(name, ""))) is None
                for name in (
                    "obligation_id", "old_parent_identity_hash",
                    "new_parent_identity_hash", "action_receipt_sha256",
                )
            )
            or transition.get("old_parent_identity_hash")
            == transition.get("new_parent_identity_hash")
            or claimed != _hash(unsigned)
        ):
            raise ActorFleetError("actor Context parent transition is invalid")
        journal = self._journal(str(transition.get("transaction_id", "")))
        rebind = journal.get("context_rebind")
        cold_managed_transition = (
            journal.get("status") == "ACTIVATED"
            and transition.get("direction") == "successor"
            and disposition == "DECLARED_USER_SERVICE_RESTART"
            and service_unit == _DEEPSEEK_USER_SERVICE
        )
        if (
            (
                journal.get("status")
                not in {"RESTART_REQUIRED", "ROLLBACK_RESTART_REQUIRED"}
                and not cold_managed_transition
            )
            or not isinstance(rebind, Mapping)
            or rebind.get("direction") != transition.get("direction")
            or not isinstance(rebind.get("obligations"), list)
        ):
            raise ActorFleetError("actor Context parent transition is not expected")
        obligation = next(
            (
                item for item in rebind["obligations"]
                if isinstance(item, Mapping)
                and item.get("obligation_id") == transition.get("obligation_id")
            ),
            None,
        )
        baseline = obligation.get("baseline") if isinstance(obligation, Mapping) else None
        baseline_parent = baseline.get("parent") if isinstance(baseline, Mapping) else None
        if (
            not isinstance(obligation, Mapping)
            or obligation.get("baseline_state") not in {"LIVE", "LATE_STALE"}
            or not isinstance(baseline_parent, Mapping)
            or baseline_parent.get("identity_hash")
            != transition.get("old_parent_identity_hash")
        ):
            raise ActorFleetError("actor Context parent transition baseline differs")
        if cold_managed_transition:
            if obligation.get("actor") != "deepseek":
                raise ActorFleetError(
                    "actor Context managed transition actor differs"
                )
            cold = self._coordinator_private_receipt(
                str(journal["transaction_id"]),
                "cold-continuity-receipt.json",
                "cold continuity receipt",
            )
            cold_unsigned = dict(cold)
            cold_hash = cold_unsigned.pop("receipt_sha256", None)
            if (
                set(cold) != {
                    "schema", "status", "transaction_id", "actor",
                    "actor_generation", "proof_sha256", "transcript_sha256",
                    "workspace_peak_bytes", "completed_at", "receipt_sha256",
                }
                or cold.get("schema")
                != "tgw-context-cold-handoff-receipt/v1"
                or cold.get("status") != "PASS"
                or cold.get("transaction_id") != journal["transaction_id"]
                or cold.get("actor") != "claude"
                or cold.get("actor_generation")
                != journal["request"]["successor_generation"]
                or any(
                    _HASH.fullmatch(str(cold.get(name, ""))) is None
                    for name in ("proof_sha256", "transcript_sha256")
                )
                or not isinstance(cold.get("workspace_peak_bytes"), int)
                or cold["workspace_peak_bytes"] < 0
                or cold["workspace_peak_bytes"] > 64 * 1024 * 1024
                or not isinstance(cold.get("completed_at"), str)
                or not cold["completed_at"]
                or cold_hash != _hash(cold_unsigned)
            ):
                raise ActorFleetError(
                    "actor Context managed transition cold proof differs"
                )
            action = self._coordinator_private_receipt(
                str(journal["transaction_id"]),
                "deepseek-service-action.json",
                "DeepSeek service action receipt",
            )
            action_unsigned = dict(action)
            action_hash = action_unsigned.pop("action_receipt_sha256", None)
            if (
                set(action) != {
                    "schema", "status", "transaction_id", "service_unit",
                    "unit_path", "unit_sha256", "baseline_sha256",
                    "lifecycle_action", "classification",
                    "linger_enabled_by_transaction",
                    "old_parent_identity_hash", "new_parent_identity_hash",
                    "cold_handoff_receipt_sha256", "completed_at",
                    "action_receipt_sha256",
                }
                or action.get("schema")
                != "tgw-deepseek-managed-service-action/v1"
                or action.get("status") != "PASS"
                or action.get("transaction_id") != journal["transaction_id"]
                or action.get("service_unit") != _DEEPSEEK_USER_SERVICE
                or action.get("unit_path") != str(_DEEPSEEK_USER_UNIT)
                or any(
                    _HASH.fullmatch(str(action.get(name, ""))) is None
                    for name in ("unit_sha256", "baseline_sha256")
                )
                or action.get("lifecycle_action") != "RESTART"
                or action.get("classification")
                != "DECLARED_USER_SERVICE_RESTART"
                or not isinstance(
                    action.get("linger_enabled_by_transaction"), bool
                )
                or action.get("old_parent_identity_hash")
                != transition["old_parent_identity_hash"]
                or action.get("new_parent_identity_hash")
                != transition["new_parent_identity_hash"]
                or action.get("cold_handoff_receipt_sha256") != cold_hash
                or not isinstance(action.get("completed_at"), str)
                or not action["completed_at"]
                or action_hash != transition["action_receipt_sha256"]
                or action_hash != _hash(action_unsigned)
            ):
                raise ActorFleetError(
                    "actor Context managed transition action proof differs"
                )
        inventory = self._actor_context_process_inventory(
            journal["request"]["actors"]
        )
        old_live = [
            item for item in inventory
            if isinstance(item.get("parent"), Mapping)
            and item["parent"].get("identity_hash")
            == transition["old_parent_identity_hash"]
        ]
        new_path = [
            item for item in inventory
            if item.get("actor") == obligation.get("actor")
            and item.get("endpoint", "tgw-context") == baseline.get("endpoint")
            and item.get("profile", "") == baseline.get("profile")
            and isinstance(item.get("parent"), Mapping)
            and item["parent"].get("identity_hash")
            == transition["new_parent_identity_hash"]
        ]
        if old_live or not new_path:
            raise ActorFleetError("actor Context parent transition process differs")
        new_parents = {
            _hash(item["parent"]): item["parent"] for item in new_path
        }
        if (
            len(new_parents) != 1
            or next(iter(new_parents.values())).get("identity_hash")
            != transition["new_parent_identity_hash"]
        ):
            raise ActorFleetError("actor Context parent transition is ambiguous")
        updated = dict(rebind)
        transitions = dict(updated.get("parent_transitions", {}))
        existing = transitions.get(str(transition["obligation_id"]))
        if isinstance(existing, Mapping):
            stable_existing = dict(existing)
            stable_existing.pop("recorded_at", None)
            stable_existing.pop("provider_record_sha256", None)
            if stable_existing != dict(transition):
                raise ActorFleetError("actor Context parent transition already differs")
            record = dict(existing)
        else:
            record = {
                **dict(transition),
                "recorded_at": self._current_time().astimezone(
                    timezone.utc
                ).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            record["provider_record_sha256"] = _hash(record)
            transitions[str(transition["obligation_id"])] = record
            history = list(updated.get("parent_transition_history", []))
            history.append(record)
            updated["parent_transition_history"] = history
            updated["parent_transitions"] = transitions
            journal["context_rebind"] = updated
            self._save(journal)
        return {
            "status": "TRANSITION_RECORDED",
            "transaction_id": journal["transaction_id"],
            "obligation_id": transition["obligation_id"],
            "provider_record_sha256": record["provider_record_sha256"],
        }

    def _is_context_process_for_startup_binding(
        self,
        item: Mapping[str, Any],
        actor: str,
        binding: Mapping[str, Any] | None,
    ) -> bool:
        return self._is_exact_context_process_for_binding(
            item, actor, binding, require_v3=False
        )

    def _reconcile_rollback_obligations(
        self,
        obligations: list[Mapping[str, Any]],
        processes: list[Mapping[str, Any]],
        target_bindings: Mapping[str, Any],
        confirmations: Mapping[str, Any] | None,
        parent_transitions: Mapping[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        pending: list[dict[str, Any]] = []
        dispositions: list[dict[str, Any]] = []
        used: set[str] = set()
        identities = {str(item.get("identity_hash")) for item in processes}
        late_actors = {
            str(item.get("actor")) for item in obligations
            if item.get("baseline_state") in {"LATE_CURRENT", "LATE_STALE"}
        }
        for obligation in obligations:
            actor = str(obligation.get("actor"))
            obligation_id = str(obligation.get("obligation_id"))
            target = target_bindings.get(actor)
            actor_processes = [item for item in processes if item.get("actor") == actor]
            if obligation.get("baseline_state") == "IDLE":
                if actor in late_actors:
                    dispositions.append(
                        {
                            "obligation_id": obligation_id,
                            "disposition": "ROLLBACK_IDLE_BASELINE_LATE_PATH_TRACKED",
                        }
                    )
                    continue
                confirmation_record = (
                    confirmations.get(obligation_id)
                    if isinstance(confirmations, Mapping) else None
                )
                target_processes = [
                    item for item in actor_processes
                    if self._is_context_process_for_startup_binding(item, actor, target)
                ]
                if not actor_processes:
                    if (
                        not isinstance(confirmation_record, Mapping)
                        or confirmation_record.get("direction") != "rollback"
                        or confirmation_record.get("actor") != actor
                        or not isinstance(
                            confirmation_record.get("parent_identity_hash"), str
                        )
                        or confirmation_record.get("process_identity_hash") in identities
                    ):
                        pending.append(
                            {
                                "obligation_id": obligation_id,
                                "reason": "ROLLBACK_ORDINARY_HARNESS_HANDOFF_REQUIRED",
                            }
                        )
                    else:
                        dispositions.append(
                            {
                                "obligation_id": obligation_id,
                                "disposition": "ROLLBACK_IDLE_CONFIRMED_HANDOFF_COMPLETE",
                                "client_confirmation_hash": confirmation_record.get(
                                    "confirmation_hash"
                                ),
                            }
                        )
                elif len(target_processes) != 1 or len(actor_processes) != 1:
                    pending.append(
                        {"obligation_id": obligation_id, "reason": "ROLLBACK_IDLE_TARGET_NOT_UNIQUE"}
                    )
                else:
                    predecessor = target_processes[0]
                    identity = str(predecessor.get("identity_hash"))
                    parent = predecessor.get("parent")
                    if (
                        not isinstance(confirmation_record, Mapping)
                        or confirmation_record.get("direction") != "rollback"
                        or confirmation_record.get("actor") != actor
                        or confirmation_record.get("process_identity_hash") != identity
                        or confirmation_record.get("parent_identity_hash")
                        != (parent.get("identity_hash") if isinstance(parent, Mapping) else None)
                    ):
                        pending.append(
                            {
                                "obligation_id": obligation_id,
                                "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
                                "process_identity_hash": identity,
                            }
                        )
                    else:
                        used.add(identity)
                        dispositions.append(
                            {
                                "obligation_id": obligation_id,
                                "disposition": "IDLE_TO_PREDECESSOR_CONFIRMED",
                                "predecessor_identity_hash": identity,
                                "client_confirmation_hash": confirmation_record.get(
                                    "confirmation_hash"
                                ),
                            }
                        )
                continue
            baseline = obligation.get("baseline")
            if obligation.get("baseline_state") == "LATE_CURRENT":
                baseline_parent = (
                    baseline.get("parent") if isinstance(baseline, Mapping) else None
                )
                path_processes = [
                    item for item in actor_processes
                    if isinstance(baseline, Mapping)
                    and item.get("endpoint", "tgw-context")
                    == baseline.get("endpoint")
                    and item.get("profile", "") == baseline.get("profile")
                    and self._parent_matches_obligation(
                        item.get("parent"), baseline_parent, obligation_id,
                        parent_transitions,
                    )
                ]
                target_processes = [
                    item for item in path_processes
                    if self._is_context_process_for_startup_binding(
                        item, actor, target
                    )
                ]
                confirmation_record = (
                    confirmations.get(obligation_id)
                    if isinstance(confirmations, Mapping) else None
                )
                if not path_processes:
                    if (
                        not isinstance(confirmation_record, Mapping)
                        or confirmation_record.get("direction") != "rollback"
                        or confirmation_record.get("actor") != actor
                        or confirmation_record.get("process_identity_hash") in identities
                    ):
                        pending.append(
                            {
                                "obligation_id": obligation_id,
                                "reason": "ROLLBACK_LATE_CURRENT_HANDOFF_REQUIRED",
                            }
                        )
                    else:
                        dispositions.append(
                            {
                                "obligation_id": obligation_id,
                                "disposition": "ROLLBACK_LATE_CURRENT_HANDOFF_COMPLETE",
                                "client_confirmation_hash": confirmation_record.get(
                                    "confirmation_hash"
                                ),
                            }
                        )
                    continue
                if len(path_processes) != 1 or len(target_processes) != 1:
                    pending.append(
                        {
                            "obligation_id": obligation_id,
                            "reason": "ROLLBACK_LATE_CURRENT_PATH_NOT_UNIQUE",
                        }
                    )
                    continue
                predecessor = target_processes[0]
                identity = str(predecessor.get("identity_hash"))
                parent = predecessor.get("parent")
                if (
                    not isinstance(confirmation_record, Mapping)
                    or confirmation_record.get("direction") != "rollback"
                    or confirmation_record.get("actor") != actor
                    or confirmation_record.get("process_identity_hash") != identity
                    or confirmation_record.get("parent_identity_hash")
                    != (
                        parent.get("identity_hash")
                        if isinstance(parent, Mapping) else None
                    )
                ):
                    pending.append(
                        {
                            "obligation_id": obligation_id,
                            "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
                            "process_identity_hash": identity,
                        }
                    )
                    continue
                used.add(identity)
                dispositions.append(
                    {
                        "obligation_id": obligation_id,
                        "disposition": "ROLLBACK_LATE_CURRENT_CONFIRMED",
                        "predecessor_identity_hash": identity,
                        "client_confirmation_hash": confirmation_record.get(
                            "confirmation_hash"
                        ),
                    }
                )
                continue
            baseline_children = (
                baseline.get("children", []) if isinstance(baseline, Mapping) else []
            )
            old_identities = {
                str(item) for item in (
                    baseline.get("child_identity_hashes", [])
                    if isinstance(baseline, Mapping) else []
                )
            }
            retained_target_identities = {
                str(item.get("identity_hash"))
                for item in baseline_children
                if isinstance(item, Mapping)
                and self._is_context_process_for_startup_binding(item, actor, target)
            }
            surviving_wrong = sorted(
                (old_identities - retained_target_identities) & identities
            )
            if surviving_wrong:
                pending.append(
                    {
                        "obligation_id": obligation_id,
                        "reason": "OLD_SUCCESSOR_IDENTITIES_STILL_LIVE",
                        "identities": surviving_wrong,
                    }
                )
                continue
            baseline_parent = baseline.get("parent") if isinstance(baseline, Mapping) else None
            candidates = []
            for item in actor_processes:
                identity = str(item.get("identity_hash"))
                if identity in used or not self._is_context_process_for_startup_binding(
                    item, actor, target
                ):
                    continue
                parent = item.get("parent")
                if self._parent_matches_obligation(
                    parent, baseline_parent, obligation_id, parent_transitions
                ):
                    candidates.append(item)
            if len(candidates) != 1:
                pending.append(
                    {"obligation_id": obligation_id, "reason": "PREDECESSOR_PATH_NOT_UNIQUE"}
                )
                continue
            predecessor = candidates[0]
            used.add(str(predecessor.get("identity_hash")))
            confirmation_record = (
                confirmations.get(obligation_id)
                if isinstance(confirmations, Mapping) else None
            )
            parent = predecessor.get("parent")
            if (
                not isinstance(confirmation_record, Mapping)
                or confirmation_record.get("direction") != "rollback"
                or confirmation_record.get("actor") != actor
                or confirmation_record.get("process_identity_hash")
                != predecessor.get("identity_hash")
                or confirmation_record.get("parent_identity_hash")
                != (parent.get("identity_hash") if isinstance(parent, Mapping) else None)
            ):
                pending.append(
                    {
                        "obligation_id": obligation_id,
                        "reason": "SAME_CLIENT_SESSION_CONFIRMATION_REQUIRED",
                        "process_identity_hash": predecessor.get("identity_hash"),
                    }
                )
                continue
            dispositions.append(
                {
                    "obligation_id": obligation_id,
                    "disposition": (
                        "PREDECESSOR_ALREADY_SERVING"
                        if str(predecessor.get("identity_hash"))
                        in retained_target_identities
                        else "CURRENT_PREDECESSOR_OBSERVED"
                    ),
                    "predecessor_identity_hash": predecessor.get("identity_hash"),
                    "retired_successor_identity_hashes": sorted(
                        old_identities - retained_target_identities
                    ),
                    "client_confirmation_hash": confirmation_record.get("confirmation_hash"),
                }
            )
        unexpected = [
            item.get("identity_hash") for item in processes
            if str(item.get("identity_hash")) not in used
        ]
        if unexpected:
            pending.append(
                {"reason": "UNEXPECTED_ROLLBACK_CONTEXT_PATH", "identities": unexpected}
            )
        return pending, dispositions

    def _journal_release(self, journal: Mapping[str, Any]) -> Path:
        raw = journal.get("candidate_release")
        if not isinstance(raw, str):
            raise ActorFleetError("actor journal candidate release is unavailable")
        try:
            release = Path(raw).resolve(strict=True)
        except OSError as exc:
            raise ActorFleetError("actor journal candidate release is unavailable") from exc
        if release.parent != self.release_root.resolve(strict=True):
            raise ActorFleetError("actor journal candidate release escapes release root")
        _verified_release(self.release_root, release)
        return release

    @staticmethod
    def _materializer(release: Path) -> Any:
        source = release / "agent-services/installers/materialize.py"
        if not source.is_file() or source.is_symlink():
            raise ActorFleetError("candidate actor materializer is unavailable")
        spec = importlib.util.spec_from_file_location("tgw_candidate_actor_materializer", source)
        if spec is None or spec.loader is None:
            raise ActorFleetError("candidate actor materializer cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(spec.name, None)
            raise
        return module

    def _candidate(self, request: Mapping[str, Any]) -> Path:
        admission_hash = request["revisions"]["admission"]
        admission = _read_json(self.admission_root / (admission_hash.removeprefix("sha256:") + ".json"), "actor admission receipt")
        matches = []
        for child in self.release_root.iterdir():
            manifest_path = child / ".release-manifest.json"
            if child.is_dir() and not child.is_symlink() and manifest_path.is_file() and not manifest_path.is_symlink():
                manifest = _verified_release(self.release_root, child)
                if manifest.get("commit") == request["revisions"]["source"]:
                    matches.append((child.resolve(), manifest))
        if len(matches) != 1:
            raise ActorFleetError("exact admitted actor release is unavailable or ambiguous")
        release, manifest = matches[0]
        if admission.get("receipt_hash") != admission_hash:
            raise ActorFleetError("actor admission receipt hash is not exact")
        try:
            validate_release_admission(
                admission,
                candidate_commit=request["revisions"]["source"],
                candidate_tree=str(manifest.get("git_tree", "")),
                trusted_public_key=self.admission_public_key,
                current_time=self._current_time().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
                current_plan_commit=request["revisions"]["plan"],
                current_solution_hash=request["revisions"]["solution"],
            )
        except (OSError, TypeError, ValueError) as exc:
            raise ActorFleetError("actor admission receipt is not exact") from exc
        return release

    def _generation_root(self, request: Mapping[str, Any]) -> Path:
        root = self.actor_generation_root / request["successor_generation"].removeprefix("sha256:")
        if not root.is_dir() or root.is_symlink():
            raise ActorFleetError("complete actor generation is unavailable")
        return root

    def _actor_inputs(self, release: Path, request: Mapping[str, Any]) -> tuple[Any, dict[str, Any], dict[str, dict[str, Any]]]:
        root = self._generation_root(request)
        bundle = _read_json(root / "bundle.json", "complete actor bundle")
        environment = _read_json(root / "environment-catalog.json", "actor environment catalog")
        bootstrap_revision = environment.get("bootstrap_revision")
        broker_policy_revision = environment.get("broker_policy_revision")
        if (
            _hash(environment) != request["revisions"]["catalog"]
            or not isinstance(bootstrap_revision, Mapping)
            or bootstrap_revision.get("content_sha256") != request["revisions"]["bootstrap"]
            or not isinstance(broker_policy_revision, Mapping)
            or broker_policy_revision.get("content_sha256") != request["revisions"]["broker_policy"]
        ):
            raise ActorFleetError("actor catalog bootstrap or broker-policy revision is not exact")
        receipt = _read_json(root / "generation-receipt.json", "actor generation receipt")
        unsigned_receipt = dict(receipt)
        claimed_receipt = unsigned_receipt.pop("receipt_hash", None)
        identity = receipt.get("generation_identity") if isinstance(receipt.get("generation_identity"), Mapping) else {}
        if (
            bundle.get("generation") != request["successor_generation"]
            or sorted(bundle.get("actors", {})) != request["actors"]
            or claimed_receipt != _hash(unsigned_receipt)
            or receipt.get("status") != "PREPARED"
            or receipt.get("generation") != request["successor_generation"]
            or receipt.get("actors") != request["actors"]
            or receipt.get("bundle_hash") != _hash(bundle)
            or receipt.get("signer_public_key") != self.contract_public_key
            or identity.get("catalog_hash") != request["revisions"]["catalog"]
            or identity.get("plan_commit") != request["revisions"]["plan"]
            or identity.get("solution_hash") != request["revisions"]["solution"]
            or identity.get("source_commit") != request["revisions"]["source"]
            or identity.get("source_tree") != request["revisions"]["source_tree"]
        ):
            raise ActorFleetError("actor generation receipt is not exact")
        contracts: dict[str, dict[str, Any]] = {}
        for actor in request["actors"]:
            contract = _read_json(root / "contracts" / f"{actor}.json", f"actor contract {actor}")
            if (
                contract.get("actor") != actor
                or contract.get("catalog_hash") != request["revisions"]["catalog"]
                or contract.get("plan") != {"commit": request["revisions"]["plan"], "solution_hash": request["revisions"]["solution"]}
                or contract.get("code_graph", {}).get("commit") != request["revisions"]["source"]
            ):
                raise ActorFleetError(f"actor contract revision mismatch: {actor}")
            contracts[actor] = contract
        return self._load_materializer(release), bundle, contracts

    def _startup_binding(self, actor: str, request: Mapping[str, Any]) -> dict[str, str]:
        receipt = _read_json(
            self._generation_root(request) / "generation-receipt.json",
            "actor generation receipt",
        )
        identity = receipt.get("generation_identity")
        source_root = Path(str(identity.get("context_source_root", ""))) if isinstance(identity, Mapping) else Path()
        source_tree = str(identity.get("source_tree", "")) if isinstance(identity, Mapping) else ""
        environment = _read_json(
            self._generation_root(request) / "environment-catalog.json",
            "actor environment catalog",
        )
        contract = _read_json(
            self._generation_root(request) / "contracts" / f"{actor}.json",
            f"actor contract {actor}",
        )
        tools = environment.get("profiles", {}).get(contract.get("profile"), {}).get("tools", [])
        git_tools = [item for item in tools if isinstance(item, Mapping) and item.get("name") == "git"]
        if (
            not source_root.is_absolute()
            or not source_root.is_dir()
            or source_root.is_symlink()
            or _COMMIT.fullmatch(source_tree) is None
            or identity.get("source_commit") != request["revisions"]["source"]
            or source_tree != request["revisions"]["source_tree"]
            or len(git_tools) != 1
            or not isinstance(git_tools[0].get("executable_path"), str)
        ):
            raise ActorFleetError("actor generation Context source binding is invalid")
        try:
            source_root, observed_commit, observed_tree = validate_context_source(
                source_root,
                str(git_tools[0]["executable_path"]),
                expected_commit=str(request["revisions"]["source"]),
                expected_tree=source_tree,
            )
        except ContextSourceGuardError as exc:
            raise ActorFleetError(f"actor generation Context source is not protected: {exc}") from exc
        if observed_commit != identity.get("source_commit") or observed_tree != source_tree:
            raise ActorFleetError("actor generation Context source binding is invalid")
        return {
            "schema": "tgw-actor-startup-binding/v3",
            "actor": actor,
            "trusted_public_key": self.contract_public_key,
            "expected_generation": str(request["successor_generation"]),
            "expected_plan_commit": str(request["revisions"]["plan"]),
            "expected_solution_hash": str(request["revisions"]["solution"]),
            "expected_source_commit": str(request["revisions"]["source"]),
            "expected_source_tree": source_tree,
            "context_source_root": str(source_root),
            "expected_catalog_hash": str(request["revisions"]["catalog"]),
            "fleet_convergence_path": str(self._fleet_convergence_path),
            "stable_launcher_path": str(_STABLE_CONTEXT_LAUNCHER),
        }

    def _directional_contract_trust(
        self, journal: Mapping[str, Any]
    ) -> dict[str, Any]:
        coordinator = journal.get("coordinator_binding")
        trust = (
            coordinator.get("contract_trust")
            if isinstance(coordinator, Mapping) else None
        )
        fields = {
            "schema", "transaction_id", "predecessor_generation",
            "successor_generation", "predecessor_revisions",
            "predecessor_contract_public_key",
            "predecessor_contract_public_sha256",
            "successor_contract_public_key",
            "successor_contract_public_sha256",
            "provider_config_preimage_sha256", "startup_preimage_sha256",
            "trust_sha256",
        }
        request = journal.get("request")
        if not isinstance(trust, Mapping) or not isinstance(request, Mapping):
            raise ActorFleetError("actor directional contract trust is unavailable")
        unsigned = dict(trust)
        claimed = unsigned.pop("trust_sha256", None)
        _predecessor_key, predecessor_raw = _contract_public_key(
            trust.get("predecessor_contract_public_key"),
            "predecessor actor contract verifier",
        )
        successor_key, successor_raw = _contract_public_key(
            trust.get("successor_contract_public_key"),
            "successor actor contract verifier",
        )
        if (
            set(trust) != fields
            or trust.get("schema")
            != "tgw-actor-contract-directional-trust/v1"
            or trust.get("transaction_id") != journal.get("transaction_id")
            or trust.get("predecessor_generation")
            != request.get("predecessor_generation")
            or trust.get("successor_generation")
            != request.get("successor_generation")
            or not isinstance(trust.get("predecessor_revisions"), Mapping)
            or set(trust["predecessor_revisions"])
            != {"plan", "solution", "source", "catalog"}
            or _COMMIT.fullmatch(
                str(trust["predecessor_revisions"].get("plan", ""))
            ) is None
            or _HASH.fullmatch(
                str(trust["predecessor_revisions"].get("solution", ""))
            ) is None
            or _COMMIT.fullmatch(
                str(trust["predecessor_revisions"].get("source", ""))
            ) is None
            or _HASH.fullmatch(
                str(trust["predecessor_revisions"].get("catalog", ""))
            ) is None
            or trust.get("predecessor_contract_public_sha256")
            != "sha256:" + hashlib.sha256(predecessor_raw).hexdigest()
            or trust.get("successor_contract_public_sha256")
            != "sha256:" + hashlib.sha256(successor_raw).hexdigest()
            or successor_key != self.contract_public_key
            or _HASH.fullmatch(
                str(trust.get("provider_config_preimage_sha256", ""))
            ) is None
            or not isinstance(trust.get("startup_preimage_sha256"), Mapping)
            or set(trust["startup_preimage_sha256"]) != set(request["actors"])
            or any(
                _HASH.fullmatch(str(item)) is None
                for item in trust["startup_preimage_sha256"].values()
            )
            or claimed != _hash(unsigned)
        ):
            raise ActorFleetError("actor directional contract trust differs")
        return dict(trust)

    def _plan_startup_bindings(
        self,
        request: Mapping[str, Any],
        journal: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Freeze every startup-binding preimage before the first replacement."""
        trust = self._directional_contract_trust(journal)
        receipts: list[dict[str, Any]] = []
        for actor in request["actors"]:
            path = self.startup_binding_root / f"{actor}-startup.json"
            expected = self._startup_binding(actor, request)
            previous = None
            if path.exists() and not path.is_symlink():
                previous = _read_json(path, f"previous actor startup binding {actor}")
                if (
                    "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                    != trust["startup_preimage_sha256"][actor]
                    or previous.get("expected_generation")
                    != trust["predecessor_generation"]
                    or previous.get("trusted_public_key")
                    != trust["predecessor_contract_public_key"]
                ):
                    raise ActorFleetError(
                        "actor predecessor startup binding changed after planning"
                    )
            elif path.exists() or path.is_symlink():
                raise ActorFleetError("actor startup binding target is unsafe")
            receipts.append(
                {
                    "actor": actor,
                    "path": str(path),
                    "previous": previous,
                    "previous_hash": _hash(previous) if previous is not None else None,
                    "installed": expected,
                    "installed_hash": _hash(expected),
                }
            )
        return receipts

    def _install_startup_bindings(
        self,
        request: Mapping[str, Any],
        journal: Mapping[str, Any],
        *,
        repair: bool = False,
    ) -> list[dict[str, Any]]:
        plan = journal.get("startup_binding_plan")
        if not isinstance(plan, list) or len(plan) != len(request["actors"]):
            raise ActorFleetError("actor startup binding effect plan is unavailable")
        receipts: list[dict[str, Any]] = []
        for actor in request["actors"]:
            record = next(
                (item for item in plan if isinstance(item, Mapping) and item.get("actor") == actor),
                None,
            )
            if not isinstance(record, Mapping):
                raise ActorFleetError("actor startup binding effect plan is incomplete")
            path = Path(str(record.get("path")))
            expected = self._startup_binding(actor, request)
            if record.get("installed") != expected or record.get("installed_hash") != _hash(expected):
                raise ActorFleetError("actor startup binding effect plan changed")
            previous = record.get("previous")
            if path.exists() and not path.is_symlink():
                current = _read_json(path, f"current actor startup binding {actor}")
                if current == expected:
                    pass
                elif (
                    isinstance(previous, Mapping)
                    and current == previous
                    and record.get("previous_hash") == _hash(previous)
                ):
                    _atomic(
                        path, expected, mode=0o444,
                        uid=0 if os.geteuid() == 0 else None,
                        gid=0 if os.geteuid() == 0 else None,
                    )
                else:
                    raise ActorFleetError("actor startup binding changed concurrently")
            elif path.exists() or path.is_symlink() or previous is not None:
                raise ActorFleetError("actor startup binding changed concurrently")
            else:
                _atomic(
                    path, expected, mode=0o444,
                    uid=0 if os.geteuid() == 0 else None,
                    gid=0 if os.geteuid() == 0 else None,
                )
            receipts.append(dict(record))
        return receipts

    def _prepare_context_cache_roots(self, value: Mapping[str, Any]) -> list[str]:
        base = self.actor_cache_root
        generation = str(value["successor_generation"]).removeprefix("sha256:")
        roots: list[str] = []
        for actor in value["actors"]:
            account = pwd.getpwnam(actor)
            actor_root = base / actor
            generation_root = actor_root / generation
            cache_root = generation_root / "context-mcp"
            for directory in (actor_root, generation_root):
                directory.mkdir(mode=0o711, exist_ok=True)
                if directory.is_symlink() or not directory.is_dir():
                    raise ActorFleetError("actor Context MCP cache parent is unsafe")
                directory.chmod(0o711)
                if os.geteuid() == 0:
                    os.chown(directory, 0, account.pw_gid)
            cache_root.mkdir(mode=0o700, exist_ok=True)
            if cache_root.is_symlink() or not cache_root.is_dir():
                raise ActorFleetError("actor Context MCP cache root is unsafe")
            cache_root.chmod(0o700)
            if os.geteuid() == 0:
                os.chown(cache_root, account.pw_uid, account.pw_gid)
            observed = cache_root.stat(follow_symlinks=False)
            if observed.st_uid != account.pw_uid or stat.S_IMODE(observed.st_mode) != 0o700:
                raise ActorFleetError("actor Context MCP cache root ownership differs")
            roots.append(str(cache_root))
        return roots

    def quiesce(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["request"] != value or journal["status"] not in {
            "COORDINATOR_BOUND", "REBIND_PLANNED", "QUIESCED",
        }:
            raise ActorFleetError("actor fleet quiesce is not legal")
        if not isinstance(journal.get("coordinator_binding"), Mapping):
            raise ActorFleetError("actor fleet coordinator binding is unavailable")
        if journal["status"] == "QUIESCED":
            return {
                "status": "QUIESCED",
                "transaction_id": value["transaction_id"],
                "services": self.services,
                "quiescence_units": self.quiescence_units,
                "context_rebind_obligations_hash": _hash(
                    journal["context_rebind"]["obligations"]
                ),
            }
        if journal["status"] == "COORDINATOR_BOUND":
            before = self._actor_context_process_inventory(value["actors"])
            obligations = self._freeze_context_obligations(
                value["actors"], before, direction="successor"
            )
            journal.update(
                {
                    "request": value,
                    "status": "REBIND_PLANNED",
                    "context_rebind": {
                        "schema": "tgw-actor-context-rebind/v2",
                        "direction": "successor",
                        "baseline": before,
                        "obligations": obligations,
                        "managed_service_restart_intent": False,
                        "attempts": [],
                    },
                }
            )
            self._save(journal)
        self._service("stop", expected="inactive")
        for unit in self.quiescence_units:
            result = self._run(["stop", unit])
            if result.returncode != 0:
                raise ActorFleetError(f"actor quiescence stop failed: {unit}")
            observed = self._run(["is-active", unit])
            if observed.returncode not in {0, 3} or observed.stdout.strip() != "inactive":
                raise ActorFleetError(f"actor quiescence state mismatch: {unit}:{observed.stdout.strip()}")
        journal.update({"request": value, "status": "QUIESCED"})
        self._save(journal)
        return {
            "status": "QUIESCED",
            "transaction_id": value["transaction_id"],
            "services": self.services,
            "quiescence_units": self.quiescence_units,
            "context_rebind_obligations_hash": _hash(
                journal["context_rebind"]["obligations"]
            ),
        }

    def rebuild(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "QUIESCED":
            raise ActorFleetError("actor fleet is not quiesced")
        self._directional_contract_trust(journal)
        release = self._candidate(value)
        materializer, bundle, contracts = self._actor_inputs(release, value)
        prepared = materializer.materialize_complete_actor_contracts(
            bundle,
            source_root=release,
            contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            replace_existing=True,
            additional_source_roots=(self._generation_root(value),),
        )
        if prepared.get("status") != "PREPARED":
            raise ActorFleetError("complete actor bundle did not prepare")
        context_cache_roots = self._prepare_context_cache_roots(value)
        journal.update(
            {
                "status": "REBUILT",
                "candidate_release": str(release),
                "context_cache_roots": context_cache_roots,
            }
        )
        self._save(journal)
        return {"status": "REBUILT", "transaction_id": value["transaction_id"], "candidate_commit": value["revisions"]["source"], "preflight_hash": _hash(prepared)}

    def activate(self, request: Mapping[str, Any], rebuilt: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {
            "REBUILT", "ACTIVATING", "MATERIALIZED", "STARTUP_BINDINGS_PLANNED",
        } or rebuilt.get("candidate_commit") != value["revisions"]["source"]:
            raise ActorFleetError("actor activation is not bound to rebuild")
        self._directional_contract_trust(journal)
        release = self._journal_release(journal)
        materializer, bundle, contracts = self._actor_inputs(release, value)
        transaction_path = self.private_state_root / (
            f"{value['transaction_id']}.actor-materializer.json"
        )
        if journal["status"] == "REBUILT":
            journal.update(
                {
                    "status": "ACTIVATING",
                    "materializer_transaction": str(transaction_path),
                }
            )
            self._save(journal)
        applied = journal.get("materialization")
        if not isinstance(applied, Mapping):
            applied = materializer.materialize_complete_actor_contracts(
                bundle,
                source_root=release,
                contracts=contracts,
                trusted_contract_public_key=self.contract_public_key,
                apply=True,
                replace_existing=True,
                additional_source_roots=(self._generation_root(value),),
                transaction_journal_path=transaction_path,
            )
            if applied.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
                raise ActorFleetError("complete actor bundle did not activate")
            journal.update({"status": "MATERIALIZED", "materialization": dict(applied)})
            self._save(journal)
        if not isinstance(journal.get("startup_binding_plan"), list):
            journal.update(
                {
                    "status": "STARTUP_BINDINGS_PLANNED",
                    "startup_binding_plan": self._plan_startup_bindings(
                        value, journal
                    ),
                }
            )
            self._save(journal)
        startup_bindings = self._install_startup_bindings(value, journal)
        journal.update(
            {
                "status": "ACTIVATED",
                "materialization": dict(applied),
                "startup_bindings": startup_bindings,
            }
        )
        self._save(journal)
        return {"status": "ACTIVATED", "transaction_id": value["transaction_id"], "generation": value["successor_generation"], "materialization_hash": _hash(applied)}

    def restart(self, activated: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(activated.get("transaction_id")))
        if journal["status"] not in {"ACTIVATED", "RESTART_REQUIRED"} or activated.get("generation") != journal["request"]["successor_generation"]:
            raise ActorFleetError("actor restart is not bound to activation")
        request = _request(journal["request"])
        rebind = journal.get("context_rebind")
        if (
            not isinstance(rebind, Mapping)
            or rebind.get("schema") != "tgw-actor-context-rebind/v2"
            or rebind.get("direction") != "successor"
            or not isinstance(rebind.get("obligations"), list)
        ):
            raise ActorFleetError("actor Context MCP rebind obligations are unavailable")
        rebind = dict(rebind)
        if rebind.get("managed_service_restart_intent") is not True:
            rebind["managed_service_restart_intent"] = True
            journal["context_rebind"] = rebind
            self._save(journal)
        if rebind.get("managed_service_restart_completed") is not True:
            self._service("restart", expected="active")
            rebind["managed_service_restart_completed"] = True
            journal["context_rebind"] = rebind
            self._save(journal)
        after = self._actor_context_process_inventory(request["actors"])
        rebind = self._capture_late_context_paths(
            journal, after, request
        )
        pending, dispositions = self._reconcile_context_obligations(
            list(rebind["obligations"]), after, request,
            rebind.get("confirmations") if isinstance(rebind, Mapping) else None,
            rebind.get("parent_transitions") if isinstance(rebind, Mapping) else None,
        )
        attempt = {
            "observed": after,
            "inventory_state": self._context_inventory_state(after, request),
            "pending": pending,
            "dispositions": dispositions,
        }
        attempts = list(rebind.get("attempts", []))
        attempts.append(attempt)
        rebind["attempts"] = attempts
        rebind["latest"] = attempt
        journal["context_rebind"] = rebind
        if pending:
            journal["status"] = "RESTART_REQUIRED"
            self._save(journal)
            return {
                "status": "RESTART_REQUIRED",
                "lifecycle": "WAIT_EXTERNAL_RESTART",
                "transaction_id": journal["transaction_id"],
                "services": self.services,
                "pending": pending,
                "context_rebind_obligations_hash": _hash(rebind["obligations"]),
            }
        process_restart = {
            "schema": "tgw-actor-context-process-rebind/v2",
            "state": attempt["inventory_state"],
            "obligations": rebind["obligations"],
            "dispositions": dispositions,
            "observed": after,
        }
        journal["status"] = "RESTARTED"
        journal["context_process_restart"] = process_restart
        self._save(journal)
        return {
            "status": "RESTARTED",
            "transaction_id": journal["transaction_id"],
            "services": self.services,
            "context_process_restart_hash": _hash(process_restart),
            "context_process_state": attempt["inventory_state"],
        }

    def health(self, restarted: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(restarted.get("transaction_id")))
        if journal["status"] != "RESTARTED":
            raise ActorFleetError("actor health is not bound to restart")
        self._service("is-active", expected="active")
        request = _request(journal["request"])
        processes = self._actor_context_process_inventory(request["actors"])
        rebind = journal.get("context_rebind")
        if not isinstance(rebind, Mapping) or not isinstance(rebind.get("obligations"), list):
            raise ActorFleetError("actor Context MCP health obligations are unavailable")
        rebind = self._capture_late_context_paths(
            journal, processes, request
        )
        pending, dispositions = self._reconcile_context_obligations(
            list(rebind["obligations"]), processes, request,
            rebind.get("confirmations") if isinstance(rebind, Mapping) else None,
            rebind.get("parent_transitions") if isinstance(rebind, Mapping) else None,
        )
        state = self._context_inventory_state(processes, request)
        if pending or state == "STALE_OR_MIXED":
            updated = dict(rebind)
            attempt = {
                "observed": processes,
                "inventory_state": state,
                "pending": pending,
                "dispositions": dispositions,
            }
            attempts = list(updated.get("attempts", []))
            attempts.append(attempt)
            updated["attempts"] = attempts
            updated["latest"] = attempt
            journal["context_rebind"] = updated
            journal["status"] = "RESTART_REQUIRED"
            self._save(journal)
            return {
                "status": "RESTART_REQUIRED",
                "lifecycle": "WAIT_EXTERNAL_RESTART",
                "transaction_id": journal["transaction_id"],
                "pending": pending,
                "context_rebind_obligations_hash": _hash(updated["obligations"]),
            }
        journal["status"] = "HEALTHY"
        journal["context_process_health"] = {
            "state": state, "observed": processes, "dispositions": dispositions,
        }
        self._save(journal)
        return {
            "status": "HEALTHY",
            "transaction_id": journal["transaction_id"],
            "services": self.services,
            "context_process_state": state,
            "context_processes_hash": _hash(processes) if processes else None,
        }

    def verify_actor(self, actor: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {"HEALTHY", "VERIFYING", "VERIFIED"} or actor not in value["actors"]:
            raise ActorFleetError("actor verification is not legal")
        self._directional_contract_trust(journal)
        bindings = [item for item in (journal.get("materialization") or {}).get("bindings", []) if item.get("actor") == actor]
        if not bindings:
            raise ActorFleetError("actor has no materialized contract")
        release = self._journal_release(journal)
        _materializer, bundle, contracts = self._actor_inputs(release, value)
        contract = contracts[actor]
        startup_path = self.startup_binding_root / f"{actor}-startup.json"
        if (
            startup_path.is_symlink()
            or not startup_path.is_file()
            or _read_json(startup_path, f"actor startup binding {actor}") != self._startup_binding(actor, value)
            or startup_path.stat(follow_symlinks=False).st_mode & 0o022
        ):
            raise ActorFleetError("actor root-owned startup binding differs from generation")
        specification = bundle["actors"][actor]
        declared = {
            (
                item["kind"], item["name"], item.get("capability"),
                item["destination"],
            ): item["sha256"]
            for item in specification["bindings"]
        }
        observed = {
            (
                item.get("kind"), item.get("name"), item.get("capability"),
                item.get("destination"),
            ): item.get("sha256")
            for item in bindings
        }
        if observed != declared:
            raise ActorFleetError("actor materialization differs from its complete bundle")
        account = pwd.getpwnam(actor)
        worker_input = {
            "schema": "tgw-actor-verification-worker-input/v1",
            "actor": actor,
            "request": value,
            "bindings": bindings,
            "bundle": bundle,
            "contract": contract,
        }
        if self._actor_context_probe is _actor_context_mcp_probe:
            cache_root = self.actor_cache_root / actor / value["successor_generation"].removeprefix("sha256:") / "context-mcp"
            manifest = _read_json(release / ".release-manifest.json", "actor release manifest")
            raw_source_root = manifest.get("src_root")
            if not isinstance(raw_source_root, str) or not raw_source_root or Path(raw_source_root).is_absolute():
                raise ActorFleetError("actor release source root is invalid")
            source_root = (release / raw_source_root).resolve(strict=True)
            if release not in source_root.parents or not source_root.is_dir() or source_root.is_symlink():
                raise ActorFleetError("actor release source root escapes the admitted release")
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-s", "-P", "-c", _ACTOR_VERIFICATION_BOOTSTRAP,
                    str(source_root), "--verify-actor-worker",
                ],
                input=_canonical(worker_input),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=60,
                check=False,
                cwd=source_root,
                env={
                    "HOME": account.pw_dir,
                    "LANG": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                    "PYTHONSAFEPATH": "1",
                    "TMPDIR": str(cache_root),
                },
                user=account.pw_uid,
                group=account.pw_gid,
                extra_groups=os.getgrouplist(actor, account.pw_gid),
            )
            raw, worker_status = completed.stdout, completed.returncode
        else:
            proof = _actor_verification_payload(
                actor, value, bindings, bundle, contract, self._actor_context_probe,
            )
            raw, worker_status = _canonical(proof), 0 if proof.get("status") == "PASS" else 1
        try:
            proof = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ActorFleetError(f"actor contract verification failed: {actor}") from exc
        if not isinstance(proof, Mapping):
            raise ActorFleetError(f"actor contract verification failed: {actor}")
        if worker_status != 0:
            reason = str(proof.get("reason", "worker refused"))[:240]
            raise ActorFleetError(f"actor contract verification failed: {actor}: {reason}")
        _validate_actor_verification_proof(
            proof,
            actor=actor,
            uid=account.pw_uid,
            request=value,
            bundle=bundle,
            contract=contract,
        )
        live_processes = self._actor_context_process_inventory(value["actors"])
        rebind = journal.get("context_rebind")
        if not isinstance(rebind, Mapping) or not isinstance(rebind.get("obligations"), list):
            raise ActorFleetError("actor verification lacks live-path obligations")
        rebind = self._capture_late_context_paths(
            journal, live_processes, value
        )
        pending, _dispositions = self._reconcile_context_obligations(
            list(rebind["obligations"]), live_processes, value,
            rebind.get("confirmations") if isinstance(rebind, Mapping) else None,
            rebind.get("parent_transitions") if isinstance(rebind, Mapping) else None,
        )
        if pending or self._context_inventory_state(live_processes, value) == "STALE_OR_MIXED":
            updated = dict(rebind)
            attempt = {
                "observed": live_processes,
                "inventory_state": self._context_inventory_state(
                    live_processes, value
                ),
                "pending": pending,
                "dispositions": _dispositions,
            }
            attempts = list(updated.get("attempts", []))
            attempts.append(attempt)
            updated["attempts"] = attempts
            updated["latest"] = attempt
            journal["context_rebind"] = updated
            journal["status"] = "RESTART_REQUIRED"
            self._save(journal)
            return {
                "status": "RESTART_REQUIRED",
                "lifecycle": "WAIT_EXTERNAL_RESTART",
                "transaction_id": journal["transaction_id"],
                "actor": actor,
                "pending": pending,
                "context_rebind_obligations_hash": _hash(updated["obligations"]),
            }
        actor_processes = [item for item in live_processes if item.get("actor") == actor]
        actor_state = self._context_inventory_state(actor_processes, value)
        actor_verifications = dict(journal.get("actor_verifications", {}))
        actor_verifications[actor] = {
            "proof": dict(proof),
            "actor_proof_hash": _hash(proof),
            "context_mcp_proof_hash": proof["context_mcp_proof"]["proof_hash"],
            "primary_real_store_semantic_sha256": proof[
                "primary_real_store_semantic_sha256"
            ],
            "instruction_entry_point_path": proof[
                "instruction_entry_point_path"
            ],
            "instruction_entry_point_sha256": proof[
                "instruction_entry_point_sha256"
            ],
            "live_context_state": actor_state,
            "verified_at": self._current_time().astimezone(timezone.utc).isoformat(
                timespec="seconds"
            ).replace("+00:00", "Z"),
        }
        journal["actor_verifications"] = actor_verifications
        journal["status"] = (
            "VERIFIED"
            if set(actor_verifications) == set(value["actors"])
            else "VERIFYING"
        )
        self._save(journal)
        return {
            "status": "VERIFIED",
            "actor": actor,
            "uid": account.pw_uid,
            "generation": value["successor_generation"],
            "plan": proof["plan"],
            "solution": proof["solution"],
            "source": proof["source"],
            "catalog": proof["catalog"],
            "profile": proof["profile"],
            "required_capabilities": proof["required_capabilities"],
            "context_mcp_proof_hash": proof["context_mcp_proof"]["proof_hash"],
            "primary_real_store_semantic_sha256": proof[
                "primary_real_store_semantic_sha256"
            ],
            "instruction_entry_point_path": proof[
                "instruction_entry_point_path"
            ],
            "instruction_entry_point_sha256": proof[
                "instruction_entry_point_sha256"
            ],
            "bindings_hash": _hash(bindings),
            "actor_proof_hash": _hash(proof),
            "live_context_state": actor_state,
            # Empty inventory is an explicit idle state, never a live proof.
            "live_context_processes_hash": _hash(actor_processes) if actor_processes else None,
        }

    def rollback(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        startup_plan = journal.get("startup_binding_plan")
        if not isinstance(startup_plan, list):
            raise ActorFleetError("actor startup rollback effect plan is unavailable")
        trust = self._directional_contract_trust(journal)
        for item in startup_plan:
            if (
                not isinstance(item, Mapping)
                or not isinstance(item.get("previous"), Mapping)
                or not isinstance(item.get("installed"), Mapping)
                or item["previous"].get("trusted_public_key")
                != trust["predecessor_contract_public_key"]
                or item["previous"].get("expected_generation")
                != trust["predecessor_generation"]
                or item["previous"].get("expected_plan_commit")
                != trust["predecessor_revisions"]["plan"]
                or item["previous"].get("expected_solution_hash")
                != trust["predecessor_revisions"]["solution"]
                or item["previous"].get("expected_source_commit")
                != trust["predecessor_revisions"]["source"]
                or item["previous"].get("expected_catalog_hash")
                != trust["predecessor_revisions"]["catalog"]
                or item["installed"].get("trusted_public_key")
                != trust["successor_contract_public_key"]
                or item["installed"].get("expected_generation")
                != trust["successor_generation"]
            ):
                raise ActorFleetError(
                    "actor rollback contract trust direction differs"
                )
        target_bindings = {
            str(item.get("actor")): item.get("previous")
            for item in startup_plan if isinstance(item, Mapping)
        }
        rebind = journal.get("context_rebind")
        if not isinstance(rebind, Mapping) or rebind.get("direction") != "rollback":
            before = self._actor_context_process_inventory(value["actors"])
            obligations = self._freeze_context_obligations(
                value["actors"], before, direction="rollback"
            )
            journal["forward_context_rebind"] = rebind
            journal["context_rebind"] = {
                "schema": "tgw-actor-context-rebind/v2",
                "direction": "rollback",
                "baseline": before,
                "obligations": obligations,
                "target_bindings": target_bindings,
                "managed_service_restart_intent": False,
                "attempts": [],
            }
            journal["status"] = "ROLLBACK_REBIND_PLANNED"
            self._save(journal)
            rebind = journal["context_rebind"]
        else:
            rebind = dict(rebind)
            if rebind.get("target_bindings") != target_bindings:
                raise ActorFleetError("actor rollback target binding changed")

        applied = journal.get("materialization")
        if journal.get("materialization_rolled_back") is not True:
            if not isinstance(applied, dict):
                # A crash can occur after the materializer committed effects
                # but before its receipt reached this provider journal.  Resume
                # the exact durable materializer transaction to reconstruct it.
                release = self._journal_release(journal)
                materializer, bundle, contracts = self._actor_inputs(release, value)
                applied = materializer.materialize_complete_actor_contracts(
                    bundle,
                    source_root=release,
                    contracts=contracts,
                    trusted_contract_public_key=self.contract_public_key,
                    apply=True,
                    replace_existing=True,
                    additional_source_roots=(self._generation_root(value),),
                    transaction_journal_path=journal.get("materializer_transaction"),
                )
                journal["materialization"] = applied
                self._save(journal)
            release = self._journal_release(journal)
            self._load_materializer(release).rollback_complete_actor_contracts(applied)
            journal["materialization_rolled_back"] = True
            self._save(journal)

        if journal.get("startup_bindings_rolled_back") is not True:
            for entry in reversed(startup_plan):
                if not isinstance(entry, Mapping):
                    raise ActorFleetError("actor startup rollback effect is invalid")
                path = Path(str(entry.get("path")))
                installed = entry.get("installed")
                previous = entry.get("previous")
                if path.exists() and not path.is_symlink():
                    current = _read_json(path, "current actor startup binding")
                    if current == previous:
                        continue
                    if current != installed or _hash(current) != entry.get("installed_hash"):
                        raise ActorFleetError("actor startup binding changed before rollback")
                    if previous is None:
                        path.unlink()
                        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(directory)
                        finally:
                            os.close(directory)
                    elif isinstance(previous, Mapping):
                        _atomic(
                            path, previous, mode=0o444,
                            uid=0 if os.geteuid() == 0 else None,
                            gid=0 if os.geteuid() == 0 else None,
                        )
                    else:
                        raise ActorFleetError("actor startup rollback value is invalid")
                elif path.exists() or path.is_symlink() or previous is not None:
                    raise ActorFleetError("actor startup rollback target changed")
            journal["startup_bindings_rolled_back"] = True
            self._save(journal)

        if not isinstance(journal.get("rollback_registration_probes"), list):
            if not isinstance(applied, Mapping):
                raise ActorFleetError("actor rollback materialization is unavailable")
            proofs: list[dict[str, Any]] = []
            mcp_bindings = [
                item for item in applied.get("bindings", [])
                if isinstance(item, Mapping) and item.get("kind") == "mcp"
            ]
            for actor in value["actors"]:
                target = target_bindings.get(actor)
                actor_mcp = [item for item in mcp_bindings if item.get("actor") == actor]
                if not isinstance(target, Mapping) or not actor_mcp:
                    raise ActorFleetError(
                        f"actor predecessor registration probe is unavailable: {actor}"
                    )
                predecessor_request = {
                    **value,
                    "successor_generation": target["expected_generation"],
                    "revisions": {
                        **value["revisions"],
                        "plan": target["expected_plan_commit"],
                        "solution": target["expected_solution_hash"],
                        "source": target["expected_source_commit"],
                        "catalog": target["expected_catalog_hash"],
                    },
                }
                for binding in actor_mcp:
                    destination = Path(str(binding["destination"]))
                    proof = self._actor_context_probe(
                        actor, destination, destination, predecessor_request
                    )
                    proofs.append(
                        {
                            "actor": actor,
                            "destination": str(destination),
                            "proof_hash": _hash(proof),
                        }
                    )
            journal["rollback_registration_probes"] = proofs
            self._save(journal)

        rebind = dict(journal["context_rebind"])
        if rebind.get("managed_service_restart_intent") is not True:
            rebind["managed_service_restart_intent"] = True
            journal["context_rebind"] = rebind
            self._save(journal)
        if rebind.get("managed_service_restart_completed") is not True:
            self._service("restart", expected="active")
            rebind["managed_service_restart_completed"] = True
            journal["context_rebind"] = rebind
            self._save(journal)
        after = self._actor_context_process_inventory(value["actors"])
        rebind = self._capture_late_context_paths(
            journal, after, value
        )
        pending, dispositions = self._reconcile_rollback_obligations(
            list(rebind["obligations"]), after, target_bindings,
            rebind.get("confirmations") if isinstance(rebind, Mapping) else None,
            rebind.get("parent_transitions") if isinstance(rebind, Mapping) else None,
        )
        attempt = {
            "observed": after, "pending": pending, "dispositions": dispositions,
        }
        attempts = list(rebind.get("attempts", []))
        attempts.append(attempt)
        rebind["attempts"] = attempts
        rebind["latest"] = attempt
        journal["context_rebind"] = rebind
        if pending:
            journal["status"] = "ROLLBACK_RESTART_REQUIRED"
            self._save(journal)
            return {
                "status": "RESTART_REQUIRED",
                "lifecycle": "WAIT_EXTERNAL_RESTART",
                "direction": "rollback",
                "transaction_id": value["transaction_id"],
                "pending": pending,
                "context_rebind_obligations_hash": _hash(rebind["obligations"]),
            }
        journal["status"] = "ROLLED_BACK"
        self._save(journal)
        return {
            "status": "ROLLED_BACK", "transaction_id": value["transaction_id"],
            "generation": value["predecessor_generation"],
            "context_rebind_obligations_hash": _hash(rebind["obligations"]),
        }

    def repair(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Re-apply only the admitted generation already bound to a transaction."""
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal.get("request") != value or not isinstance(journal.get("candidate_release"), str):
            raise ActorFleetError("actor repair is not bound to an admitted transaction")
        self._directional_contract_trust(journal)
        release = Path(journal["candidate_release"])
        if release != self._candidate(value):
            raise ActorFleetError("actor repair candidate changed")
        materializer, bundle, contracts = self._actor_inputs(release, value)
        transaction_path = journal.get("materializer_transaction")
        original_materialization = journal.get("materialization")
        if not isinstance(transaction_path, str) or not isinstance(
            original_materialization, Mapping
        ):
            raise ActorFleetError("actor repair rollback lineage is unavailable")
        repaired = materializer.materialize_complete_actor_contracts(
            bundle,
            source_root=release,
            contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            apply=True,
            replace_existing=True,
            additional_source_roots=(self._generation_root(value),),
            transaction_journal_path=transaction_path,
        )
        if repaired.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
            raise ActorFleetError("complete actor bundle did not repair")
        if repaired.get("rollback_journal") != original_materialization.get(
            "rollback_journal"
        ):
            raise ActorFleetError("actor repair changed rollback lineage")
        startup_bindings = self._install_startup_bindings(value, journal, repair=True)
        journal.update(
            {
                "status": "REPAIRED",
                "materialization": repaired,
                "startup_bindings": startup_bindings,
            }
        )
        self._save(journal)
        return {
            "status": "REPAIRED",
            "transaction_id": value["transaction_id"],
            "generation": value["successor_generation"],
            "materialization_hash": _hash(repaired),
        }

    def dispatch(self, step: str, arguments: list[Any]) -> Mapping[str, Any]:
        operations = {
            "bind-coordinator": (self.bind_coordinator, 2),
            "quiesce": (self.quiesce, 1),
            "rebuild": (self.rebuild, 1),
            "activate": (self.activate, 2),
            "restart": (self.restart, 1),
            "health": (self.health, 1),
            "verify-actor": (self.verify_actor, 2),
            "confirm-context-rebind": (self.confirm_context_rebind, 1),
            "record-context-parent-transition": (
                self.record_context_parent_transition, 1
            ),
            "generation-status": (self.generation_status, 0),
            "nonterminal-transactions": (self.nonterminal_transactions, 0),
            "supersede-transactions": (self.supersede_transactions, 1),
            "rollback": (self.rollback, 1),
            "repair": (self.repair, 1),
        }
        target = operations.get(step)
        if target is None or len(arguments) != target[1]:
            raise ActorFleetError("actor provider step or arguments are not allowlisted")
        return target[0](*arguments)


def create_actor_fleet_app(config: Mapping[str, Any], **provider_kwargs: Any) -> FastAPI:
    provider = ActorFleetProvider(config.get("actor_fleet_provider"), **provider_kwargs)
    app = FastAPI(title="TGW actor fleet provider", docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/actor-fleet/{step}")
    def actor_fleet(step: str, body: Mapping[str, Any], authorization: str | None = Header(default=None)) -> Mapping[str, Any]:
        if not provider.authorized(authorization):
            raise HTTPException(status_code=401, detail="actor fleet authentication failed")
        invocation = {key: body[key] for key in ("schema", "step", "arguments") if key in body}
        if (
            set(body) != {"schema", "step", "arguments", "invocation_hash"}
            or body.get("schema") != "tgw-actor-fleet-provider-invocation/v1"
            or body.get("step") != step
            or body.get("invocation_hash") != _hash(invocation)
        ):
            raise HTTPException(status_code=409, detail="actor provider invocation binding is invalid")
        try:
            result = provider.dispatch(step, list(body["arguments"]))
        except (OSError, KeyError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"schema": "tgw-actor-fleet-provider-response/v1", "provider_id": "tgw-actor-fleet-provider@1", "step": step, "invocation_hash": body["invocation_hash"], "result": result}

    return app


def main() -> int:
    if sys.argv[1:] == ["--verify-actor-worker"]:
        return _actor_verification_worker_main()
    if sys.argv[1:]:
        raise ActorFleetError("actor fleet provider arguments are invalid")
    config = load_operational_config(Path(os.environ.get("TGW_CONFIG", str(DEFAULT_CONFIG))))
    host = os.environ.get("TGW_ACTOR_FLEET_HOST", "127.0.0.1")
    port = int(os.environ.get("TGW_ACTOR_FLEET_PORT", "7556"))
    uvicorn.run(create_actor_fleet_app(config), host=host, port=port, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
