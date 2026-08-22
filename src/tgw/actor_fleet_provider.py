"""Privileged, closed W18 actor-fleet provider for the ``tgw-lib`` host.

The Plan/API coordinator runs on ``tgw-prod``.  Actor accounts, canonical
source, harness worktrees and actor-local MCP registrations run on ``tgw-lib``.
This provider is therefore deliberately separate from the production
platform-control provider.  It accepts only the fixed actor refresh state
machine and never accepts a command, path, account, service or candidate
selector from its caller.
"""

from __future__ import annotations

import grp
import hashlib
import importlib.util
import json
import os
import pwd
import re
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
from tgw.release_installer import ReleaseError
from tgw.release_installer import verify as verify_release

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_UNIT = re.compile(r"[A-Za-z0-9_.@-]+\.(?:service|timer)\Z")
_CONTEXT_MCP_TOOLS = {
    "tgw_context_status",
    "tgw_context_bundle",
    "tgw_context_plan_graph",
    "tgw_context_plan_source",
    "tgw_context_runbooks",
    "tgw_context_code_graph",
    "tgw_context_onboarding",
}
_ACTOR_VERIFICATION_MAX_INPUT = 4 * 1024 * 1024
_CONTEXT_REGISTRATION_MAX_INPUT = 256 * 1024
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


def _directory(value: Any, label: str) -> Path:
    path = Path(value) if isinstance(value, str) else Path()
    if not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents or not path.is_dir() or path.is_symlink():
        raise ActorFleetError(f"{label} must be a durable directory outside /tmp")
    return path


def _regular(value: Any, label: str) -> Path:
    path = Path(value) if isinstance(value, str) else Path()
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
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            if expected_source is not None:
                observed = os.fstat(descriptor)
                expected = expected_source.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(observed.st_mode)
                    or not stat.S_ISREG(expected.st_mode)
                    or (observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino)
                ):
                    raise ActorFleetError("actor Context MCP binding changed")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(_CONTEXT_REGISTRATION_MAX_INPUT + 1)
            if len(raw) > _CONTEXT_REGISTRATION_MAX_INPUT:
                raise ActorFleetError("actor Context MCP registration is too large")
        finally:
            os.close(descriptor)
        content = raw.decode("utf-8")
        if path.suffix == ".toml":
            value = tomllib.loads(content)
            endpoint = value["mcp_servers"]["tgw-context"]
        elif path.suffix in {".yaml", ".yml"}:
            value = yaml.safe_load(content)
            rows = value[0]["insert"]
            row = next(item for item in rows if item.get("id") == "tgw-context")
            endpoint = row["config"]
        else:
            value = json.loads(content)
            endpoint = value["mcpServers"]["tgw-context"]
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
    if not isinstance(endpoint, Mapping):
        raise ActorFleetError("actor Context MCP registration is invalid")
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
        revisions = request["revisions"]
        if (
            status.get("plan", {}).get("approved_commit") != revisions["plan"]
            or status.get("plan", {}).get("approved_solution_hash") != revisions["solution"]
            or status.get("source", {}).get("commit") != revisions["source"]
            or status.get("environment", {}).get("catalog_hash") != revisions["catalog"]
            or onboarding.get("actor") != actor
            or onboarding.get("plan", {}).get("approved_commit") != revisions["plan"]
            or onboarding.get("source", {}).get("commit") != revisions["source"]
            or bundle.get("receiver") != actor
            or bundle.get("status", {}).get("source", {}).get("commit") != revisions["source"]
            or bundle.get("status", {}).get("environment", {}).get("catalog_hash") != revisions["catalog"]
        ):
            raise ActorFleetError("actor Context MCP returned mixed revision bindings")
        proof = {
            "schema": "tgw-actor-context-mcp-proof/v1",
            "status": "PASS",
            "actor": actor,
            "tools": sorted(names),
            "plan": revisions["plan"],
            "solution": revisions["solution"],
            "source": revisions["source"],
            "catalog": revisions["catalog"],
            "onboarding_bundle_sha256": onboarding.get("bundle_sha256"),
            "task_bundle_sha256": bundle.get("bundle_sha256"),
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
            if not destination.is_symlink() or destination.resolve(strict=False) != source:
                raise ActorFleetError("actor contract binding changed")
            if _binding_digest(source) != binding["sha256"]:
                raise ActorFleetError("actor contract binding content changed")
        by_kind: dict[str, dict[str, Mapping[str, Any]]] = {}
        for binding in bindings:
            by_kind.setdefault(str(binding["kind"]), {})[str(binding["name"])] = binding
        mcp_bindings = [
            {
                "endpoint": name,
                "source_sha256": binding["sha256"],
                "destination": binding["destination"],
            }
            for name, binding in sorted(by_kind.get("mcp", {}).items())
        ]
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
            or contract.get("plan")
            != {
                "commit": request["revisions"]["plan"],
                "solution_hash": request["revisions"]["solution"],
            }
            or contract.get("code_graph", {}).get("commit") != request["revisions"]["source"]
            or set(by_kind.get("skill", {})) != set(local.get("skills", {}))
            or set(by_kind.get("hook", {})) != set(local.get("hooks", {}))
            or set(by_kind.get("mcp", {})) != set(local.get("mcp", {}).get("endpoints", []))
            or _hash(mcp_bindings) != local.get("mcp", {}).get("binding_hash")
            or by_kind.get("launcher", {}).get("launcher", {}).get("destination")
            != local.get("launcher", {}).get("path")
            or by_kind.get("launcher", {}).get("launcher", {}).get("sha256")
            != local.get("launcher", {}).get("sha256")
            or by_kind.get("bootstrap", {}).get("bootstrap-receipt", {}).get("sha256")
            != local.get("bootstrap_receipt_hash")
            or actor_declaration.get("enabled") is not True
            or contract.get("profile") not in actor_declaration.get("permitted_profiles", [])
            or profile.get("state") != "ready-for-preflight"
        ):
            raise ActorFleetError("actor startup binding is stale or mixed")
        context_binding = by_kind.get("mcp", {}).get("tgw-context")
        if context_binding is None:
            raise ActorFleetError("actor Context MCP binding is missing")
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
            "context_mcp_proof": mcp_proof,
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
    }
    specification = bundle["actors"][actor]
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
        "onboarding_bundle_sha256", "task_bundle_sha256", "proof_hash",
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
        or mcp_proof.get("schema") != "tgw-actor-context-mcp-proof/v1"
        or mcp_proof.get("status") != "PASS"
        or mcp_proof.get("actor") != actor
        or mcp_proof.get("tools") != sorted(_CONTEXT_MCP_TOOLS)
        or mcp_proof.get("plan") != revisions["plan"]
        or mcp_proof.get("solution") != revisions["solution"]
        or mcp_proof.get("source") != revisions["source"]
        or mcp_proof.get("catalog") != revisions["catalog"]
        or any(_HASH.fullmatch(str(mcp_proof.get(name))) is None for name in (
            "onboarding_bundle_sha256", "task_bundle_sha256",
        ))
        or claimed_mcp_hash != _hash(unsigned_mcp)
    ):
        raise ActorFleetError("actor verification proof differs from expected revisions")


def _atomic(path: Path, value: Mapping[str, Any], *, mode: int = 0o640) -> None:
    stage = path.with_name(f".{path.name}.next")
    if stage.exists() or stage.is_symlink():
        raise ActorFleetError(f"stale actor-provider staging path exists: {stage}")
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(stage, mode)
        os.replace(stage, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


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
    required = {"plan", "solution", "source", "catalog", "bootstrap", "broker_policy", "admission"}
    if not isinstance(revisions, Mapping) or set(revisions) != required:
        raise ActorFleetError("actor fleet revisions are incomplete")
    if _COMMIT.fullmatch(str(revisions["plan"])) is None or _COMMIT.fullmatch(str(revisions["source"])) is None:
        raise ActorFleetError("actor fleet Git revisions are invalid")
    if any(not isinstance(revisions[name], str) or _HASH.fullmatch(revisions[name]) is None for name in required - {"plan", "source"}):
        raise ActorFleetError("actor fleet content revisions are invalid")
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
        self.state_root = _directory(value["state_root"], "actor fleet state root")
        state_root_state = self.state_root.stat(follow_symlinks=False)
        if state_root_state.st_mode & 0o022 or (
            os.geteuid() == 0 and state_root_state.st_uid != 0
        ):
            raise ActorFleetError("actor fleet state root is not root protected")
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
        try:
            actor_group = grp.getgrnam(str(value["actor_group"]))
        except KeyError as exc:
            raise ActorFleetError("actor group is unavailable") from exc
        self.actor_group = actor_group.gr_name
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
        if not isinstance(services, list) or not services or services != sorted(set(services)) or any(not isinstance(unit, str) or _UNIT.fullmatch(unit) is None for unit in services):
            raise ActorFleetError("managed actor service set is invalid")
        self.services = list(services)
        quiescence = value.get("quiescence_units")
        if not isinstance(quiescence, list) or quiescence != sorted(set(quiescence)) or any(not isinstance(unit, str) or _UNIT.fullmatch(unit) is None for unit in quiescence):
            raise ActorFleetError("actor quiescence unit set is invalid")
        self.quiescence_units = list(quiescence)
        if not isinstance(value.get("contract_public_key"), str) or not value["contract_public_key"]:
            raise ActorFleetError("actor contract signer is invalid")
        self.contract_public_key = value["contract_public_key"]
        self._run = service_runner or self._run_service
        self._load_materializer = materializer_loader or self._materializer
        self._actor_context_probe = actor_context_probe or _actor_context_mcp_probe
        self._current_time = current_time or (lambda: datetime.now(timezone.utc))

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
        return self.state_root / f"{transaction_id}.actor-provider.json"

    def _journal(self, transaction_id: str) -> dict[str, Any]:
        path = self._journal_path(transaction_id)
        return (
            _read_json(path, "actor provider journal")
            if path.is_file() and not path.is_symlink()
            else {
                "schema": "tgw-actor-fleet-journal/v1",
                "transaction_id": transaction_id,
                "status": "NEW",
                "request": None,
                "candidate_release": None,
                "materialization": None,
            }
        )

    def _save(self, journal: Mapping[str, Any]) -> None:
        _atomic(self._journal_path(str(journal["transaction_id"])), journal)

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
        return {
            "schema": "tgw-actor-startup-binding/v1",
            "actor": actor,
            "trusted_public_key": self.contract_public_key,
            "expected_generation": str(request["successor_generation"]),
            "expected_plan_commit": str(request["revisions"]["plan"]),
            "expected_solution_hash": str(request["revisions"]["solution"]),
            "expected_source_commit": str(request["revisions"]["source"]),
            "expected_catalog_hash": str(request["revisions"]["catalog"]),
        }

    def _install_startup_bindings(
        self,
        request: Mapping[str, Any],
        journal: Mapping[str, Any],
        *,
        repair: bool = False,
    ) -> list[dict[str, Any]]:
        prior = journal.get("startup_bindings")
        if repair and not isinstance(prior, list):
            raise ActorFleetError("actor startup rollback state is unavailable")
        receipts: list[dict[str, Any]] = []
        for actor in request["actors"]:
            path = self.startup_binding_root / f"{actor}-startup.json"
            expected = self._startup_binding(actor, request)
            previous = None
            if isinstance(prior, list):
                record = next((item for item in prior if item.get("actor") == actor), None)
                if not isinstance(record, Mapping):
                    raise ActorFleetError("actor startup rollback state is incomplete")
                previous = record.get("previous")
            elif path.exists() and not path.is_symlink():
                previous = _read_json(path, f"previous actor startup binding {actor}")
            elif path.exists() or path.is_symlink():
                raise ActorFleetError("actor startup binding target is unsafe")
            _atomic(path, expected, mode=0o444)
            receipts.append(
                {
                    "actor": actor,
                    "path": str(path),
                    "previous": previous,
                    "installed_hash": _hash(expected),
                }
            )
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
        if journal["request"] not in (None, value) or journal["status"] not in {"NEW", "QUIESCED"}:
            raise ActorFleetError("actor fleet quiesce is not legal")
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
        }

    def rebuild(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] != "QUIESCED":
            raise ActorFleetError("actor fleet is not quiesced")
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
        if journal["status"] != "REBUILT" or rebuilt.get("candidate_commit") != value["revisions"]["source"]:
            raise ActorFleetError("actor activation is not bound to rebuild")
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
        )
        if applied.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
            raise ActorFleetError("complete actor bundle did not activate")
        startup_bindings = self._install_startup_bindings(value, journal)
        journal.update(
            {
                "status": "ACTIVATED",
                "materialization": applied,
                "startup_bindings": startup_bindings,
            }
        )
        self._save(journal)
        return {"status": "ACTIVATED", "transaction_id": value["transaction_id"], "generation": value["successor_generation"], "materialization_hash": _hash(applied)}

    def restart(self, activated: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(activated.get("transaction_id")))
        if journal["status"] != "ACTIVATED" or activated.get("generation") != journal["request"]["successor_generation"]:
            raise ActorFleetError("actor restart is not bound to activation")
        self._service("restart", expected="active")
        journal["status"] = "RESTARTED"
        self._save(journal)
        return {"status": "RESTARTED", "transaction_id": journal["transaction_id"], "services": self.services}

    def health(self, restarted: Mapping[str, Any]) -> dict[str, Any]:
        journal = self._journal(str(restarted.get("transaction_id")))
        if journal["status"] != "RESTARTED":
            raise ActorFleetError("actor health is not bound to restart")
        self._service("is-active", expected="active")
        journal["status"] = "HEALTHY"
        self._save(journal)
        return {"status": "HEALTHY", "transaction_id": journal["transaction_id"], "services": self.services}

    def verify_actor(self, actor: str, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal["status"] not in {"HEALTHY", "VERIFYING"} or actor not in value["actors"]:
            raise ActorFleetError("actor verification is not legal")
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
        declared = {(item["kind"], item["name"], item["destination"]): item["sha256"] for item in specification["bindings"]}
        observed = {(item.get("kind"), item.get("name"), item.get("destination")): item.get("sha256") for item in bindings}
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
        journal["status"] = "VERIFYING"
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
            "bindings_hash": _hash(bindings),
            "actor_proof_hash": _hash(proof),
        }

    def rollback(self, request: Mapping[str, Any]) -> dict[str, Any]:
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        applied = journal.get("materialization")
        if isinstance(applied, dict):
            release = self._journal_release(journal)
            self._load_materializer(release).rollback_complete_actor_contracts(applied)
        startup_bindings = journal.get("startup_bindings")
        if isinstance(startup_bindings, list):
            for entry in reversed(startup_bindings):
                path = Path(str(entry.get("path")))
                current = _read_json(path, "current actor startup binding")
                if _hash(current) != entry.get("installed_hash"):
                    raise ActorFleetError("actor startup binding changed before rollback")
                previous = entry.get("previous")
                if previous is None:
                    path.unlink()
                elif isinstance(previous, Mapping):
                    _atomic(path, previous, mode=0o444)
                else:
                    raise ActorFleetError("actor startup rollback value is invalid")
        self._service("restart", expected="active")
        journal["status"] = "ROLLED_BACK"
        self._save(journal)
        return {"status": "ROLLED_BACK", "transaction_id": value["transaction_id"], "generation": value["predecessor_generation"]}

    def repair(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Re-apply only the admitted generation already bound to a transaction."""
        value = _request(request)
        journal = self._journal(value["transaction_id"])
        if journal.get("request") != value or not isinstance(journal.get("candidate_release"), str):
            raise ActorFleetError("actor repair is not bound to an admitted transaction")
        release = Path(journal["candidate_release"])
        if release != self._candidate(value):
            raise ActorFleetError("actor repair candidate changed")
        materializer, bundle, contracts = self._actor_inputs(release, value)
        repaired = materializer.materialize_complete_actor_contracts(
            bundle,
            source_root=release,
            contracts=contracts,
            trusted_contract_public_key=self.contract_public_key,
            apply=True,
            replace_existing=True,
            additional_source_roots=(self._generation_root(value),),
        )
        if repaired.get("status") != "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED":
            raise ActorFleetError("complete actor bundle did not repair")
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
            "quiesce": (self.quiesce, 1),
            "rebuild": (self.rebuild, 1),
            "activate": (self.activate, 2),
            "restart": (self.restart, 1),
            "health": (self.health, 1),
            "verify-actor": (self.verify_actor, 2),
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
