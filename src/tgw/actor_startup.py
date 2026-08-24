"""Provider-neutral, fail-closed W18 actor startup attestation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, NoReturn

from tgw.actor_contract import ActorContractError, verify_signed_actor_contract
from tgw.context_source_guard import (
    ContextSourceGuardError,
    closed_git_environment,
    validate_context_source,
)

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_INSTRUCTION_ENTRY_POINTS = {
    "claude": Path(".claude/CLAUDE.md"),
    "codex": Path(".codex/AGENTS.md"),
    "deepseek": Path(".dsh/AGENTS.md"),
}


class ActorStartupError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _file_hash(path: Path) -> str:
    target = path.resolve(strict=True)
    if not target.is_file() or target.is_symlink():
        raise ActorStartupError(f"actor startup file is unavailable: {path}")
    return "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()


def _object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActorStartupError(f"{label} is invalid") from exc
    if not isinstance(value, dict):
        raise ActorStartupError(f"{label} is invalid")
    return value


def _startup_binding(path: Path, actor: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise ActorStartupError("root-owned actor startup binding is unavailable")
    observed = path.stat(follow_symlinks=False)
    if observed.st_uid != 0 or observed.st_mode & 0o022:
        raise ActorStartupError("actor startup binding is not root-owned and immutable")
    value = _object(path, "actor startup binding")
    required = {
        "schema",
        "actor",
        "trusted_public_key",
        "expected_generation",
        "expected_plan_commit",
        "expected_solution_hash",
        "expected_source_commit",
        "expected_source_tree",
        "context_source_root",
        "expected_catalog_hash",
        "fleet_convergence_path",
        "stable_launcher_path",
    }
    if set(value) != required or value.get("schema") != "tgw-actor-startup-binding/v3" or value.get("actor") != actor:
        raise ActorStartupError("actor startup binding is invalid")
    source_root = Path(str(value.get("context_source_root", "")))
    if (
        not source_root.is_absolute()
        or source_root == Path("/tmp")
        or Path("/tmp") in source_root.parents
        or source_root == Path("/opt/TGW/var/tmp")
        or Path("/opt/TGW/var/tmp") in source_root.parents
        or _COMMIT.fullmatch(str(value.get("expected_source_tree", ""))) is None
        or not Path(str(value.get("fleet_convergence_path", ""))).is_absolute()
        or not Path(str(value.get("stable_launcher_path", ""))).is_absolute()
    ):
        raise ActorStartupError("actor startup source binding is invalid")
    return {key: str(raw) for key, raw in value.items()}


def _protected_actor_link(path: Path) -> None:
    if not path.is_symlink():
        raise ActorStartupError(f"actor startup binding is not a materialized symlink: {path}")
    target = path.resolve(strict=True)
    observed = target.stat()
    if observed.st_uid != 0 or observed.st_mode & 0o022:
        raise ActorStartupError(f"actor startup source is actor-writable: {path}")


def _instruction_entry_point(
    root: Path,
    actor: str,
    bootstrap: Mapping[str, Any],
    *,
    require_protected_source: bool,
) -> dict[str, str]:
    relative = _INSTRUCTION_ENTRY_POINTS.get(actor)
    instructions = bootstrap.get("instructions")
    if relative is None or not isinstance(instructions, Mapping) or set(instructions) != {
        "agent-entry-point"
    }:
        raise ActorStartupError("actor instruction entry point is missing")
    raw = instructions["agent-entry-point"]
    destination = root / relative
    if (
        not isinstance(raw, Mapping)
        or set(raw) != {"path", "sha256"}
        or raw.get("path") != str(destination)
        or _HASH.fullmatch(str(raw.get("sha256", ""))) is None
        or not destination.is_symlink()
        or _file_hash(destination) != raw.get("sha256")
    ):
        raise ActorStartupError("actor instruction entry point differs from generation")
    if require_protected_source:
        _protected_actor_link(destination)
    return {"path": str(destination), "sha256": str(raw["sha256"])}


def _protected_stable_launcher(path: Path) -> None:
    """Require a root-owned launcher entry in a non-actor-writable parent."""

    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ActorStartupError("root stable Context launcher is unavailable")
    entry = path.lstat()
    parent = path.parent.stat(follow_symlinks=False)
    target = path.resolve(strict=True)
    observed = target.stat(follow_symlinks=False)
    if (
        entry.st_uid != 0
        or entry.st_mode & 0o022
        or entry.st_nlink != 1
        or not stat.S_ISREG(entry.st_mode)
        or path.parent.is_symlink()
        or path.parent.resolve(strict=True) != path.parent
        or parent.st_uid != 0
        or parent.st_mode & 0o022
        or observed.st_uid != 0
        or observed.st_mode & 0o022
        or not target.is_file()
    ):
        raise ActorStartupError("root stable Context launcher is not protected")


def _profile_tool(catalog: Mapping[str, Any], profile: str, name: str) -> str:
    tools = catalog.get("profiles", {}).get(profile, {}).get("tools", [])
    matches = [item for item in tools if isinstance(item, Mapping) and item.get("name") == name]
    if len(matches) != 1:
        raise ActorStartupError(f"catalog-pinned {name} tool is unavailable")
    path, expected_hash = matches[0].get("executable_path"), matches[0].get("executable_sha256")
    if not isinstance(path, str) or not path.startswith("/") or _file_hash(Path(path)) != expected_hash:
        raise ActorStartupError(f"catalog-pinned {name} tool identity differs")
    return path


def _context_source_identity(path: Path, git: str) -> tuple[str, str]:
    try:
        _root, commit, tree = validate_context_source(path, git)
    except ContextSourceGuardError as exc:
        raise ActorStartupError(str(exc)) from exc
    return commit, tree


def attest_actor_startup(
    *,
    home: str | Path,
    actor: str,
    trusted_public_key: str,
    expected_generation: str,
    expected_plan_commit: str,
    expected_solution_hash: str,
    expected_source_commit: str,
    expected_catalog_hash: str,
    require_protected_sources: bool = False,
) -> dict[str, Any]:
    root = Path(home).resolve(strict=True)
    account = pwd.getpwuid(os.geteuid()).pw_name
    if actor != account:
        raise ActorStartupError("actor startup identity differs from the process account")
    for value, pattern, label in (
        (expected_generation, _HASH, "generation"),
        (expected_plan_commit, _COMMIT, "Plan commit"),
        (expected_solution_hash, _HASH, "Plan solution"),
        (expected_source_commit, _COMMIT, "source commit"),
        (expected_catalog_hash, _HASH, "environment catalog"),
    ):
        if pattern.fullmatch(value) is None:
            raise ActorStartupError(f"expected {label} is invalid")
    contract_path = root / ".tgw" / "actor-contract.json"
    bootstrap_path = root / ".tgw" / "bootstrap.json"
    catalog_path = root / ".tgw" / "execution-environment-catalog.json"
    if require_protected_sources:
        for path in (contract_path, bootstrap_path, catalog_path):
            _protected_actor_link(path)
    contract_raw = _object(contract_path, "actor contract")
    try:
        contract = verify_signed_actor_contract(
            contract_raw,
            trusted_public_key=trusted_public_key,
        )
    except ActorContractError as exc:
        raise ActorStartupError(str(exc)) from exc
    bootstrap = _object(bootstrap_path, "actor bootstrap receipt")
    catalog = _object(catalog_path, "actor environment catalog")
    unsigned_bootstrap = dict(bootstrap)
    claimed_bootstrap = unsigned_bootstrap.pop("receipt_hash", None)
    profile = catalog.get("profiles", {}).get(contract.get("profile"), {})
    declaration = catalog.get("actors", {}).get(actor, {})
    expected_plan = {"commit": expected_plan_commit, "solution_hash": expected_solution_hash}
    if (
        claimed_bootstrap != _hash(unsigned_bootstrap)
        or bootstrap.get("status") != "READY"
        or bootstrap.get("actor") != actor
        or bootstrap.get("generation") != expected_generation
        or bootstrap.get("plan") != expected_plan
        or bootstrap.get("code_graph", {}).get("commit") != expected_source_commit
        or bootstrap.get("catalog_hash") != expected_catalog_hash
        or _hash(catalog) != expected_catalog_hash
        or contract.get("actor") != actor
        or contract.get("status") != "READY"
        or contract.get("plan") != expected_plan
        or contract.get("code_graph", {}).get("commit") != expected_source_commit
        or contract.get("catalog_hash") != expected_catalog_hash
        or contract.get("local", {}).get("bootstrap_receipt_hash") != _file_hash(bootstrap_path)
        or bootstrap.get("launcher") != contract.get("local", {}).get("launcher")
        or bootstrap.get("skills") != contract.get("local", {}).get("skills")
        or bootstrap.get("hooks") != contract.get("local", {}).get("hooks")
        or bootstrap.get("mcp") != contract.get("local", {}).get("mcp")
        or declaration.get("enabled") is not True
        or contract.get("profile") not in declaration.get("permitted_profiles", [])
        or profile.get("state") != "ready-for-preflight"
    ):
        raise ActorStartupError("actor startup binding is stale or mixed")
    instruction_entry_point = _instruction_entry_point(
        root,
        actor,
        bootstrap,
        require_protected_source=require_protected_sources,
    )
    body = {
        "schema": "tgw-actor-startup-attestation/v1",
        "status": "PASS",
        "actor": actor,
        "uid": os.geteuid(),
        "generation": expected_generation,
        "plan": expected_plan,
        "source_commit": expected_source_commit,
        "source_tree": contract["code_graph"]["tree"],
        "catalog_hash": expected_catalog_hash,
        "profile": contract["profile"],
        "contract_receipt_hash": contract["receipt_hash"],
        "bootstrap_receipt_hash": bootstrap["receipt_hash"],
        "instruction_entry_point": instruction_entry_point,
        "required_capabilities": sorted(profile.get("broker_capabilities", [])),
        "fallback": "FORBIDDEN",
    }
    return {**body, "attestation_hash": _hash(body)}


def _context_mcp_environment(
    *, home: Path, result: Mapping[str, Any], binding: Mapping[str, str], binding_path: Path
) -> dict[str, str]:
    # Harnesses cache MCP configuration for the lifetime of the client process.
    # Generation-specific values therefore cannot be trusted from the inherited
    # registration environment.  The root-owned startup record is the sole
    # mutable cutover pointer; every new launcher invocation derives from it.
    source_root = Path(binding["context_source_root"])
    catalog = _object(home / ".tgw" / "execution-environment-catalog.json", "actor environment catalog")
    git = _profile_tool(catalog, str(result["profile"]), "git")
    source_commit, source_tree = _context_source_identity(source_root, git)
    if (
        source_commit != result["source_commit"]
        or source_tree != result["source_tree"]
        or source_tree != binding["expected_source_tree"]
    ):
        raise ActorStartupError("actor Context MCP source binding is stale or mixed")
    plan_repository = "/opt/TGW/library/plans"
    approved_plan_root = f"/opt/TGW/library/approved/{result['plan']['commit']}"
    context_runtime_root = "/opt/TGW/tgw-lib/var/context"
    environment_catalog = "/etc/tgw/execution-environment-catalog.json"
    expected_cache_root = (
        f"/opt/TGW/var/cache/tgw/actors/{result['actor']}/{str(result['generation']).removeprefix('sha256:')}/context-mcp"
    )
    context_cache_root = expected_cache_root
    if (
        Path(context_cache_root).is_symlink()
        or not Path(context_cache_root).is_dir()
        or _hash(_object(Path(environment_catalog), "system environment catalog")) != result["catalog_hash"]
    ):
        raise ActorStartupError("actor Context MCP platform binding is stale or mixed")
    return {
        "TGW_CONTEXT_PLAN_COMMIT": str(result["plan"]["commit"]),
        "TGW_CONTEXT_PLAN_SOLUTION": str(result["plan"]["solution_hash"]),
        "TGW_CONTEXT_PLAN_REPOSITORY": plan_repository,
        "TGW_CONTEXT_PLAN_ROOT": approved_plan_root,
        "TGW_CONTEXT_SOURCE_ROOT": str(source_root),
        "TGW_CONTEXT_RUNTIME_ROOT": context_runtime_root,
        "TGW_CONTEXT_ENVIRONMENT_CATALOG": environment_catalog,
        "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": str(result["catalog_hash"]),
        "TGW_CONTEXT_ACTOR": str(result["actor"]),
        "TGW_CONTEXT_ENDPOINT": "tgw-context",
        "TGW_CONTEXT_PROFILE": str(result["profile"]),
        "TGW_CONTEXT_GENERATION": str(result["generation"]),
        "TGW_CONTEXT_SOURCE_COMMIT": str(result["source_commit"]),
        "TGW_CONTEXT_SOURCE_TREE": str(result["source_tree"]),
        "TGW_CONTEXT_STARTUP_BINDING": str(binding_path),
        "TGW_CONTEXT_FLEET_CONVERGENCE": binding["fleet_convergence_path"],
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONSAFEPATH": "1",
        "TMPDIR": context_cache_root,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(source_root),
        "PATH": f"{Path(git).parent}:/usr/bin:/bin",
    }


def _exec_context_mcp_runtime(
    *,
    home: Path,
    actor: str,
    source_root: Path,
    environment: Mapping[str, str],
) -> NoReturn:
    """Cross execve into exact candidate bytes with a stable launcher argv identity."""

    stable_launcher = Path(str(environment["TGW_CONTEXT_STABLE_LAUNCHER"]))
    _protected_stable_launcher(stable_launcher)
    candidate_launcher = Path(environment["TGW_CONTEXT_RUNTIME_ENTRYPOINT"])
    # Preserve the virtual-environment entry path.  Resolving its interpreter
    # symlink before exec discards pyvenv.cfg discovery and therefore the MCP
    # runtime dependencies installed in that environment.
    executable = Path(sys.executable)
    if not executable.is_absolute() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise ActorStartupError("actor Context Python runtime is unavailable")
    arguments = [
        str(executable),
        "-I",
        "-s",
        "-P",
        str(candidate_launcher),
        "--context-mcp-runtime",
        "--context-mcp",
        "--context-mcp-stable-launcher",
        str(stable_launcher),
    ]
    os.execve(executable, arguments, dict(environment))
    raise AssertionError("execve returned")


def _git_blob(source_root: Path, git: str, commit: str, path: str) -> bytes:
    completed = subprocess.run(
        [
            git, "-c", f"safe.directory={source_root}",
            "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-C", str(source_root), "show", f"{commit}:{path}",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        env=closed_git_environment(git),
    )
    if completed.returncode:
        raise ActorStartupError(f"exact candidate runtime blob is unavailable: {path}")
    return completed.stdout


def _runtime_identity_environment(
    *,
    home: Path,
    result: Mapping[str, Any],
    binding: Mapping[str, str],
    context_environment: Mapping[str, str],
) -> dict[str, str]:
    source_root = Path(binding["context_source_root"])
    catalog = _object(home / ".tgw" / "execution-environment-catalog.json", "actor environment catalog")
    git = _profile_tool(catalog, str(result["profile"]), "git")
    paths = {
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT": source_root / "scripts" / "tgw_actor_startup.py",
        "TGW_CONTEXT_RUNTIME_MODULE": source_root / "src" / "tgw" / "actor_startup.py",
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE": source_root / "src" / "tgw" / "context_mcp_server.py",
    }
    git_paths = {
        "TGW_CONTEXT_RUNTIME_ENTRYPOINT": "scripts/tgw_actor_startup.py",
        "TGW_CONTEXT_RUNTIME_MODULE": "src/tgw/actor_startup.py",
        "TGW_CONTEXT_RUNTIME_CONTEXT_MODULE": "src/tgw/context_mcp_server.py",
    }
    identity = dict(context_environment)
    for name, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise ActorStartupError("exact candidate Context runtime is unavailable")
        raw = path.read_bytes()
        if raw != _git_blob(source_root, git, str(result["source_commit"]), git_paths[name]):
            raise ActorStartupError("exact candidate Context runtime differs from Git")
        identity[name] = str(path)
        identity[name + "_SHA256"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    stable_launcher = Path(binding["stable_launcher_path"])
    _protected_stable_launcher(stable_launcher)
    stable_raw = stable_launcher.resolve(strict=True).read_bytes()
    if stable_raw != _git_blob(
        source_root, git, str(result["source_commit"]), "scripts/tgw_actor_startup.py"
    ):
        raise ActorStartupError("materialized stable launcher differs from exact candidate")
    executable = Path(sys.executable)
    if not executable.is_absolute():
        raise ActorStartupError("actor Context Python runtime is unavailable")
    executable_state = executable.resolve(strict=True).stat(follow_symlinks=False)
    identity.update(
        {
            "TGW_CONTEXT_STABLE_LAUNCHER": str(stable_launcher),
            "TGW_CONTEXT_STABLE_LAUNCHER_SHA256": "sha256:" + hashlib.sha256(stable_raw).hexdigest(),
            "TGW_CONTEXT_RUNTIME_EXECUTABLE": str(executable),
            "TGW_CONTEXT_RUNTIME_EXECUTABLE_SHA256": "sha256:" + hashlib.sha256(executable.read_bytes()).hexdigest(),
            "TGW_CONTEXT_RUNTIME_EXECUTABLE_DEVICE": str(executable_state.st_dev),
            "TGW_CONTEXT_RUNTIME_EXECUTABLE_INODE": str(executable_state.st_ino),
        }
    )
    return identity


def _generation_status_line(binding: Mapping[str, str]) -> str:
    """Read the root-provider one-line status without trusting an MCP child."""
    path = Path(binding["fleet_convergence_path"])
    hold = (
        "TGW Context generation: client=CURRENT fleet=HOLD aggregate=HOLD "
        f"gen={binding['expected_generation'].removeprefix('sha256:')[:12]} "
        f"approved={binding['expected_plan_commit'][:12]} evidence=unknown "
        f"source={binding['expected_source_commit'][:12]} instructions=unknown "
        "tx=unknown pending=unknown"
    )
    try:
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            return hold
        observed = path.stat(follow_symlinks=False)
        if observed.st_uid != 0 or observed.st_mode & 0o022 or observed.st_size > 2_000_000:
            return hold
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return hold
        unsigned = dict(value)
        claimed = unsigned.pop("projection_sha256", None)
        status = value.get("generation_status")
        transaction = value.get("transaction")
        if (
            claimed != _hash(unsigned)
            or status not in {
                "CURRENT", "UPDATE_PENDING", "RESTART_REQUIRED", "MIXED", "HOLD"
            }
            or (
                isinstance(transaction, Mapping)
                and transaction.get("target_generation")
                != binding["expected_generation"]
            )
        ):
            return hold
        pending = (
            sum(
                len(item.get("pending_reasons", []))
                for item in transaction.get("obligations", [])
                if isinstance(item, Mapping)
            ) + len(transaction.get("global_pending", []))
            if isinstance(transaction, Mapping) else 0
        )
        revisions = (
            transaction.get("target_revisions", {})
            if isinstance(transaction, Mapping) else {}
        )
        transaction_id = (
            str(transaction.get("transaction_id", "none"))
            if isinstance(transaction, Mapping) else "none"
        )
        generation = binding["expected_generation"].removeprefix("sha256:")[:12]
        actor_verification = next(
            (
                item
                for item in transaction.get("actor_verifications", [])
                if isinstance(item, Mapping)
                and item.get("actor") == binding["actor"]
            ),
            None,
        ) if isinstance(transaction, Mapping) else None
        instruction_hash = (
            str(actor_verification.get("instruction_entry_point_sha256", ""))
            if isinstance(actor_verification, Mapping) else ""
        )
        if status == "CURRENT" and _HASH.fullmatch(instruction_hash) is None:
            return hold
        return (
            f"TGW Context generation: client=CURRENT fleet={status} "
            f"aggregate={status} gen={generation} "
            f"approved={str(revisions.get('approved_plan', binding['expected_plan_commit']))[:12]} "
            f"evidence={str(revisions.get('evidence_plan', 'unknown'))[:12]} "
            f"source={str(revisions.get('source_commit', binding['expected_source_commit']))[:12]} "
            f"instructions={instruction_hash.removeprefix('sha256:')[:12] or 'pending'} "
            f"tx={transaction_id} pending={pending}"
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return hold


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-actor-startup")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--actor", default=pwd.getpwuid(os.geteuid()).pw_name)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--context-mcp", action="store_true")
    parser.add_argument("--context-mcp-runtime", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--context-mcp-stable-launcher", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if (
        (args.context_mcp_runtime and not args.context_mcp)
        or (args.context_mcp_stable_launcher and not args.context_mcp_runtime)
    ):
        raise ActorStartupError("actor Context runtime mode is incomplete")
    try:
        binding_path = args.binding or Path(f"/etc/tgw/actors/{args.actor}-startup.json")
        binding = _startup_binding(binding_path, args.actor)
        result = attest_actor_startup(
            home=args.home,
            actor=args.actor,
            trusted_public_key=binding["trusted_public_key"],
            expected_generation=binding["expected_generation"],
            expected_plan_commit=binding["expected_plan_commit"],
            expected_solution_hash=binding["expected_solution_hash"],
            expected_source_commit=binding["expected_source_commit"],
            expected_catalog_hash=binding["expected_catalog_hash"],
            require_protected_sources=True,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": "tgw-actor-startup-attestation/v1", "status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 73
    if args.context_mcp:
        try:
            context_environment = _context_mcp_environment(
                home=args.home,
                result=result,
                binding=binding,
                binding_path=binding_path,
            )
            context_environment = _runtime_identity_environment(
                home=args.home,
                result=result,
                binding=binding,
                context_environment=context_environment,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {"schema": "tgw-actor-startup-attestation/v1", "status": "HOLD", "reason": str(exc)},
                    sort_keys=True,
                )
            )
            return 73
        if args.context_mcp_runtime:
            if dict(os.environ) != context_environment:
                print(
                    json.dumps(
                        {
                            "schema": "tgw-actor-startup-attestation/v1",
                            "status": "HOLD",
                            "reason": "exec-visible actor Context environment differs from re-attestation",
                        },
                        sort_keys=True,
                    )
                )
                return 73
            if Path(sys.argv[0]).resolve(strict=True) != Path(context_environment["TGW_CONTEXT_RUNTIME_ENTRYPOINT"]):
                print(
                    json.dumps(
                        {
                            "schema": "tgw-actor-startup-attestation/v1",
                            "status": "HOLD",
                            "reason": "loaded actor Context entrypoint differs from exact candidate",
                        },
                        sort_keys=True,
                    )
                )
                return 73
            if (
                args.context_mcp_stable_launcher
                != context_environment["TGW_CONTEXT_STABLE_LAUNCHER"]
            ):
                print(
                    json.dumps(
                        {
                            "schema": "tgw-actor-startup-attestation/v1",
                            "status": "HOLD",
                            "reason": "exec-visible stable launcher identity differs",
                        },
                        sort_keys=True,
                    )
                )
                return 73
            from tgw.context_mcp_server import main as context_main

            context_main()
            return 0
        print(
            _generation_status_line(binding),
            file=sys.stderr,
            flush=True,
        )
        try:
            _exec_context_mcp_runtime(
                home=args.home,
                actor=args.actor,
                source_root=Path(binding["context_source_root"]),
                environment=context_environment,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {"schema": "tgw-actor-startup-attestation/v1", "status": "HOLD", "reason": str(exc)},
                    sort_keys=True,
                )
            )
            return 73
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
