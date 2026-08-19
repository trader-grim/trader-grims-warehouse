"""W18 local actor-contract compilation; deliberately no installation effects."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

CATALOG_SCHEMA = "tgw-execution-environment-catalog/v1"
CONTRACT_SCHEMA = "tgw-actor-contract-receipt/v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"[0-9a-f]{40}$")


class ActorContractError(ValueError):
    """The requested actor contract is malformed or not catalog-bound."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _require_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ActorContractError(f"{label} must be an exact sha256 hash")
    return value


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ActorContractError(f"{label} must be a string list")
    if len(value) != len(set(value)):
        raise ActorContractError(f"{label} contains duplicates")
    return value


def _catalog(raw: Mapping[str, Any]) -> dict[str, Any]:
    catalog = dict(raw)
    if catalog.get("schema") != CATALOG_SCHEMA:
        raise ActorContractError("unsupported environment catalog")
    if not isinstance(catalog.get("actors"), dict) or not isinstance(catalog.get("profiles"), dict):
        raise ActorContractError("catalog actor or profile registry is invalid")
    lock = catalog.get("flake_lock")
    if not isinstance(lock, dict) or set(lock) != {"path", "sha256"} or lock["path"] != "flake.lock":
        raise ActorContractError("catalog flake lock binding is invalid")
    _require_hash(lock["sha256"], "catalog flake lock")
    return catalog


def compile_actor_contract(
    *,
    catalog: Mapping[str, Any],
    actor: str,
    profile: str,
    plan_commit: str,
    plan_solution_hash: str,
    code_graph: Mapping[str, Any],
    local: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile a deterministic receipt; mismatches quarantine before any launch."""
    verified = _catalog(catalog)
    if not isinstance(actor, str) or actor not in verified["actors"]:
        raise ActorContractError("actor is absent from the catalog")
    if not isinstance(profile, str) or profile not in verified["profiles"]:
        raise ActorContractError("profile is absent from the catalog")
    if not _COMMIT.fullmatch(plan_commit):
        raise ActorContractError("Plan commit must be exact")
    _require_hash(plan_solution_hash, "Plan solution")
    graph = dict(code_graph)
    if (
        set(graph) != {"commit", "tree", "freshness_hash"}
        or not _COMMIT.fullmatch(graph["commit"])
        or not _COMMIT.fullmatch(graph["tree"])
    ):
        raise ActorContractError("CodeGraph binding is invalid")
    _require_hash(graph["freshness_hash"], "CodeGraph freshness")
    local_value = dict(local)
    required_local = {"bootstrap_receipt_hash", "launcher", "skills", "hooks", "mcp"}
    if set(local_value) != required_local:
        raise ActorContractError("local actor contract fields are invalid")
    _require_hash(local_value["bootstrap_receipt_hash"], "bootstrap receipt")
    for field in ("skills", "hooks"):
        values = local_value[field]
        if not isinstance(values, Mapping) or any(not isinstance(key, str) for key in values):
            raise ActorContractError(f"local {field} are invalid")
        for value in values.values():
            _require_hash(value, f"local {field} hash")
    launcher = local_value["launcher"]
    if not isinstance(launcher, Mapping) or set(launcher) != {"path", "sha256"} or not isinstance(launcher["path"], str) or not launcher["path"].startswith("/"):
        raise ActorContractError("local launcher is invalid")
    _require_hash(launcher["sha256"], "local launcher")
    mcp = local_value["mcp"]
    if not isinstance(mcp, Mapping) or set(mcp) != {"endpoints", "binding_hash"}:
        raise ActorContractError("local MCP registration is invalid")
    _strings(mcp["endpoints"], "local MCP endpoints")
    _require_hash(mcp["binding_hash"], "local MCP binding")

    declared_actor = verified["actors"][actor]
    declared_profile = verified["profiles"][profile]
    diagnostics: list[dict[str, str]] = []
    if not declared_actor.get("enabled"):
        diagnostics.append({"code": "ACTOR_DISABLED", "detail": actor})
    if profile not in declared_actor.get("permitted_profiles", []):
        diagnostics.append({"code": "PROFILE_NOT_PERMITTED", "detail": profile})
    if declared_profile.get("state") != "ready-for-preflight":
        diagnostics.append({"code": "PROFILE_NOT_READY", "detail": profile})
    for field, local_name in (("required_skills", "skills"), ("required_hooks", "hooks")):
        missing = sorted(set(declared_actor.get(field, [])) - set(local_value[local_name]))
        diagnostics.extend({"code": "MISSING_" + field.upper(), "detail": item} for item in missing)
    missing_mcp = sorted(set(declared_actor.get("required_mcp_endpoints", [])) - set(mcp["endpoints"]))
    diagnostics.extend({"code": "MISSING_MCP_ENDPOINT", "detail": item} for item in missing_mcp)
    body = {
        "schema": CONTRACT_SCHEMA,
        "status": "QUARANTINED" if diagnostics else "READY",
        "catalog_hash": _hash(verified),
        "actor": actor,
        "profile": profile,
        "plan": {"commit": plan_commit, "solution_hash": plan_solution_hash},
        "code_graph": graph,
        "local": local_value,
        "diagnostics": diagnostics,
        "activation": "declarative-only",
    }
    return {**body, "receipt_hash": _hash(body)}
