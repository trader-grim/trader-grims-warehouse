"""Pure, fail-closed W14 request-to-launch lifecycle compiler.

This is deliberately a preparation seam.  It neither creates the declared
worktrees nor starts a role, changes configuration, or selects a Plan root.
Those effects require the later W14/W16 governed launcher.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

SCHEMA = "tgw-development-request-lifecycle/v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}$")
_COMMIT = re.compile(r"[0-9a-f]{40}$")
_IDENTITY = re.compile(r"[a-z][a-z0-9-]{0,63}$")


class DevelopmentRequestError(ValueError):
    """A request, resolver result, or prepared launch binding is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevelopmentRequestError(f"{label} must be a non-empty string")
    return value


def _hash_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise DevelopmentRequestError(f"{label} must be an exact sha256 hash")
    return value


def _request(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(raw)
    if set(value) != {"request_id", "original_request", "scope", "constraints", "effect_limits"}:
        raise DevelopmentRequestError("request fields are not exact")
    if not _IDENTITY.fullmatch(_string(value["request_id"], "request id")):
        raise DevelopmentRequestError("request id is invalid")
    for field in ("original_request", "scope"):
        _string(value[field], field)
    for field in ("constraints", "effect_limits"):
        if not isinstance(value[field], list) or not all(isinstance(item, str) and item for item in value[field]):
            raise DevelopmentRequestError(f"{field} must be a string list")
        if len(value[field]) != len(set(value[field])):
            raise DevelopmentRequestError(f"{field} contains duplicates")
    return value


def _allocation(raw: Mapping[str, Any], request_id: str) -> dict[str, str]:
    value = dict(raw)
    if set(value) != {"attempt_id", "worktree", "attempt_root"}:
        raise DevelopmentRequestError("allocation fields are not exact")
    attempt_id = _string(value["attempt_id"], "attempt id")
    if not _IDENTITY.fullmatch(attempt_id):
        raise DevelopmentRequestError("attempt id is invalid")
    for field in ("worktree", "attempt_root"):
        path = _string(value[field], field)
        if not path.startswith("/") or "/home/" in path or "/../" in path or f"/{request_id}/" not in path or f"/{attempt_id}" not in path:
            raise DevelopmentRequestError(f"{field} is not an isolated request-bound path")
    return value


def compile_request_lifecycle(*, request: Mapping[str, Any], resolution: Mapping[str, Any], allocation: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic prepared role cards, or a zero-launch held outcome."""
    requested = _request(request)
    allocated = _allocation(allocation, requested["request_id"])
    result = dict(resolution)
    status = result.get("status")
    if status not in {"RESOLVED", "CLARIFICATION_REQUIRED", "HELD"}:
        raise DevelopmentRequestError("resolution status is invalid")
    required = {"status", "alternatives", "confidence", "explanation"}
    if not required.issubset(result) or not isinstance(result["alternatives"], list) or not isinstance(result["confidence"], (int, float)):
        raise DevelopmentRequestError("resolution summary is invalid")
    _string(result["explanation"], "resolution explanation")
    request_hash = _hash(requested)
    body: dict[str, Any] = {"schema": SCHEMA, "request": requested, "request_hash": request_hash,
                            "resolution": result, "allocation": allocated, "launch_cards": [],
                            "activation": "declarative-only"}
    if status != "RESOLVED":
        if set(result) != required | {"clarification"} or not result["clarification"]:
            raise DevelopmentRequestError("non-resolved request requires a typed clarification")
        body["timeline"] = ["request-submitted", "resolution-" + status.lower()]
        return {**body, "lifecycle_hash": _hash(body)}
    expected = required | {"plan", "root", "closure"}
    if set(result) != expected:
        raise DevelopmentRequestError("resolved request fields are not exact")
    plan = result["plan"]
    if not isinstance(plan, Mapping) or set(plan) != {"commit", "solution_hash"} or not _COMMIT.fullmatch(plan["commit"]):
        raise DevelopmentRequestError("resolved Plan binding is invalid")
    _hash_value(plan["solution_hash"], "resolved Plan solution")
    root = result["root"]
    if not isinstance(root, Mapping) or set(root) != {"kind", "id"} or root["kind"] not in {"Plan", "PP", "Todo"}:
        raise DevelopmentRequestError("resolved root is invalid")
    _string(root["id"], "resolved root id")
    closure = result["closure"]
    if not isinstance(closure, list) or not closure:
        raise DevelopmentRequestError("resolved closure is invalid")
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for unit in closure:
        if not isinstance(unit, Mapping) or set(unit) != {"id", "depends_on", "roles"}:
            raise DevelopmentRequestError("closure unit fields are invalid")
        unit_id = _string(unit["id"], "closure unit id")
        if unit_id in seen or not isinstance(unit["depends_on"], list) or any(dep not in seen for dep in unit["depends_on"]):
            raise DevelopmentRequestError("closure is not dependency ordered")
        seen.add(unit_id)
        if not isinstance(unit["roles"], list) or not unit["roles"]:
            raise DevelopmentRequestError("closure unit roles are invalid")
        for role in unit["roles"]:
            if not _IDENTITY.fullmatch(_string(role, "role")):
                raise DevelopmentRequestError("role is invalid")
            card = {"request_hash": request_hash, "plan": dict(plan), "root": dict(root), "unit": unit_id,
                    "role": role, "allocation": allocated, "idempotency_key": _hash([request_hash, unit_id, role, allocated["attempt_id"]]),
                    "state": "PREPARED", "activation": "declarative-only"}
            cards.append(card)
    body["launch_cards"] = cards
    body["timeline"] = [
        "request-submitted", "resolution-resolved", "launch-prepared", "implementation", "test",
        "independent-review", "admission", "candidate-installation", "live-verification", "rollback",
        "operator-acceptance",
    ]
    return {**body, "lifecycle_hash": _hash(body)}
