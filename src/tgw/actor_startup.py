"""Provider-neutral, fail-closed W18 actor startup attestation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
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


def _expected(name: str, pattern: re.Pattern[str]) -> str:
    value = os.environ.get(name, "")
    if pattern.fullmatch(value) is None:
        raise ActorStartupError(f"{name} is missing or invalid")
    return value


def attest_actor_startup(
    *, home: str | Path, actor: str, trusted_public_key: str,
    expected_generation: str, expected_plan_commit: str,
    expected_solution_hash: str, expected_source_commit: str,
    expected_catalog_hash: str,
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
    contract_raw = _object(contract_path, "actor contract")
    try:
        contract = verify_signed_actor_contract(
            contract_raw, trusted_public_key=trusted_public_key,
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
        "schema": "tgw-actor-startup-attestation/v1", "status": "PASS",
        "actor": actor, "uid": os.geteuid(), "generation": expected_generation,
        "plan": expected_plan, "source_commit": expected_source_commit,
        "catalog_hash": expected_catalog_hash, "profile": contract["profile"],
        "contract_receipt_hash": contract["receipt_hash"],
        "bootstrap_receipt_hash": bootstrap["receipt_hash"],
        "required_capabilities": sorted(profile.get("broker_capabilities", [])),
        "fallback": "FORBIDDEN",
    }
    return {**body, "attestation_hash": _hash(body)}


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-actor-startup")
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--actor", default=pwd.getpwuid(os.geteuid()).pw_name)
    parser.add_argument("--context-mcp", action="store_true")
    args = parser.parse_args()
    try:
        result = attest_actor_startup(
            home=args.home, actor=args.actor,
            trusted_public_key=os.environ.get("TGW_ACTOR_CONTRACT_PUBLIC_KEY", ""),
            expected_generation=_expected("TGW_ACTOR_EXPECTED_GENERATION", _HASH),
            expected_plan_commit=_expected("TGW_ACTOR_EXPECTED_PLAN_COMMIT", _COMMIT),
            expected_solution_hash=_expected("TGW_ACTOR_EXPECTED_PLAN_SOLUTION", _HASH),
            expected_source_commit=_expected("TGW_ACTOR_EXPECTED_SOURCE_COMMIT", _COMMIT),
            expected_catalog_hash=_expected("TGW_ACTOR_EXPECTED_CATALOG_HASH", _HASH),
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": "tgw-actor-startup-attestation/v1", "status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 73
    if args.context_mcp:
        source_root = Path(__file__).resolve().parents[2]
        os.environ.update({
            "TGW_CONTEXT_PLAN_COMMIT": result["plan"]["commit"],
            "TGW_CONTEXT_PLAN_SOLUTION": result["plan"]["solution_hash"],
            "TGW_CONTEXT_PLAN_REPOSITORY": os.environ.get(
                "TGW_ACTOR_PLAN_REPOSITORY", "/opt/TGW/library/plans",
            ),
            "TGW_CONTEXT_PLAN_ROOT": os.environ.get(
                "TGW_ACTOR_APPROVED_PLAN_ROOT",
                f"/opt/TGW/library/approved/{result['plan']['commit']}",
            ),
            "TGW_CONTEXT_SOURCE_ROOT": str(source_root),
            "TGW_CONTEXT_RUNTIME_ROOT": os.environ.get(
                "TGW_ACTOR_CONTEXT_RUNTIME_ROOT", "/opt/TGW/tgw-lib/var/context",
            ),
            "TGW_CONTEXT_ENVIRONMENT_CATALOG": str(
                args.home / ".tgw" / "execution-environment-catalog.json"
            ),
            "TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH": result["catalog_hash"],
        })
        from tgw.context_mcp_server import main as context_main

        context_main()
        return 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
