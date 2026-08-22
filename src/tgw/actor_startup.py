"""Provider-neutral, fail-closed W18 actor startup attestation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from tgw.actor_contract import ActorContractError, verify_signed_actor_contract

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


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
        "expected_catalog_hash",
    }
    if set(value) != required or value.get("schema") != "tgw-actor-startup-binding/v1" or value.get("actor") != actor:
        raise ActorStartupError("actor startup binding is invalid")
    return {key: str(raw) for key, raw in value.items()}


def _protected_actor_link(path: Path) -> None:
    if not path.is_symlink():
        raise ActorStartupError(f"actor startup binding is not a materialized symlink: {path}")
    target = path.resolve(strict=True)
    observed = target.stat()
    if observed.st_uid != 0 or observed.st_mode & 0o022:
        raise ActorStartupError(f"actor startup source is actor-writable: {path}")


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
    if not path.is_absolute() or not path.is_dir() or path.is_symlink():
        raise ActorStartupError("actor Context MCP source root is invalid")

    def inspect(*args: str) -> str:
        completed = subprocess.run(
            [git, "-c", f"safe.directory={path}", "-C", str(path), *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise ActorStartupError("actor Context MCP source root is not an exact Git source")
        return completed.stdout.strip()

    commit = inspect("rev-parse", "HEAD")
    tree = inspect("rev-parse", "HEAD^{tree}")
    if _COMMIT.fullmatch(commit) is None or _COMMIT.fullmatch(tree) is None or inspect("status", "--porcelain=v1"):
        raise ActorStartupError("actor Context MCP source root is stale or dirty")
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
        "required_capabilities": sorted(profile.get("broker_capabilities", [])),
        "fallback": "FORBIDDEN",
    }
    return {**body, "attestation_hash": _hash(body)}


def _context_mcp_environment(
    *, home: Path, result: Mapping[str, Any], binding: Mapping[str, str], environment: Mapping[str, str]
) -> dict[str, str]:
    required_environment = {
        "TGW_ACTOR_CONTRACT_PUBLIC_KEY": binding["trusted_public_key"],
        "TGW_ACTOR_EXPECTED_GENERATION": result["generation"],
        "TGW_ACTOR_EXPECTED_PLAN_COMMIT": result["plan"]["commit"],
        "TGW_ACTOR_EXPECTED_PLAN_SOLUTION": result["plan"]["solution_hash"],
        "TGW_ACTOR_EXPECTED_SOURCE_COMMIT": result["source_commit"],
        "TGW_ACTOR_EXPECTED_CATALOG_HASH": result["catalog_hash"],
    }
    if any(environment.get(name) != value for name, value in required_environment.items()):
        raise ActorStartupError("actor MCP registration environment is stale or mixed")
    source_root = Path(environment.get("TGW_ACTOR_CONTEXT_SOURCE_ROOT", ""))
    catalog = _object(home / ".tgw" / "execution-environment-catalog.json", "actor environment catalog")
    git = _profile_tool(catalog, str(result["profile"]), "git")
    source_commit, source_tree = _context_source_identity(source_root, git)
    if source_commit != result["source_commit"] or source_tree != result["source_tree"]:
        raise ActorStartupError("actor Context MCP source binding is stale or mixed")
    plan_repository = environment.get("TGW_ACTOR_PLAN_REPOSITORY", "")
    approved_plan_root = environment.get("TGW_ACTOR_APPROVED_PLAN_ROOT", "")
    context_runtime_root = environment.get("TGW_ACTOR_CONTEXT_RUNTIME_ROOT", "")
    context_cache_root = environment.get("TGW_ACTOR_CONTEXT_CACHE_ROOT", "")
    environment_catalog = environment.get("TGW_ACTOR_ENVIRONMENT_CATALOG", "")
    expected_cache_root = (
        f"/opt/TGW/var/cache/tgw/actors/{result['actor']}/{str(result['generation']).removeprefix('sha256:')}/context-mcp"
    )
    if (
        plan_repository != "/opt/TGW/library/plans"
        or approved_plan_root != f"/opt/TGW/library/approved/{result['plan']['commit']}"
        or context_runtime_root != "/opt/TGW/tgw-lib/var/context"
        or context_cache_root != expected_cache_root
        or Path(context_cache_root).is_symlink()
        or not Path(context_cache_root).is_dir()
        or environment_catalog != "/etc/tgw/execution-environment-catalog.json"
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
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "TMPDIR": context_cache_root,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": str(source_root),
        "PATH": f"{Path(git).parent}:/usr/bin:/bin",
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-actor-startup")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--actor", default=pwd.getpwuid(os.geteuid()).pw_name)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--context-mcp", action="store_true")
    args = parser.parse_args()
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
                environment=os.environ,
            )
        except (OSError, ValueError) as exc:
            print(
                json.dumps(
                    {"schema": "tgw-actor-startup-attestation/v1", "status": "HOLD", "reason": str(exc)},
                    sort_keys=True,
                )
            )
            return 73
        os.environ.clear()
        os.environ.update(context_environment)
        from tgw.context_mcp_server import main as context_main

        context_main()
        return 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
