"""Plan-bound development request resolution and authority projection.

This is the W14 product boundary.  A natural-language request is retained in
the same immutable PlanAuthority record that later carries the operator
decision and the typed launch effect.  Resolution is deliberately narrow: the
approved solved Plan is the default root, and a narrower root is held unless
the approved solution itself names that exact root.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from tgw.development_request import compile_request_lifecycle
from tgw.plan_authority import AuthorityRequest

SCHEMA = "tgw-development-console-request/v1"
_COMMIT = re.compile(r"[0-9a-f]{40}$")
_HASH = re.compile(r"sha256:[0-9a-f]{64}$")
_ROOT_KINDS = {"Plan", "PP", "Todo"}
# Durable lanes from SPEC-plan-capability-graph-v2.  These are intentionally
# not harness, vendor, account, or model names.  A qualified provider is chosen
# from the actor/provider registry only when a card is launched.
_ROLE_SEQUENCE = ("implementation", "controller-verification", "independent-review")


class DevelopmentConsoleError(ValueError):
    """The request cannot be bound to the approved development lifecycle."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise DevelopmentConsoleError(f"{label} must be a string list")
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise DevelopmentConsoleError(f"{label} contains duplicates")
    return normalized


def _request_id(original_request: str, requested_by: str, plan_commit: str) -> str:
    digest = hashlib.sha256(_canonical({
        "original_request": original_request,
        "requested_by": requested_by,
        "plan_commit": plan_commit,
    })).hexdigest()[:20]
    return f"development-{digest}"


def _solution_root(solution: Mapping[str, Any]) -> dict[str, str]:
    raw = solution.get("root")
    if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str) or not raw["id"]:
        raise DevelopmentConsoleError("approved solution has no exact execution root")
    identity = raw["id"]
    if identity.startswith("PP-"):
        kind = "PP"
    elif identity.isdecimal() or identity.startswith("TODO-"):
        kind = "Todo"
    else:
        kind = "Plan"
    return {"kind": kind, "id": identity}


def _closure(solution: Mapping[str, Any]) -> list[dict[str, Any]]:
    phases = solution.get("phase_order")
    if not isinstance(phases, list) or not phases:
        raise DevelopmentConsoleError("approved solution has no dependency-ordered phase closure")
    closure: list[dict[str, Any]] = []
    previous: list[str] = []
    seen: set[str] = set()
    for phase in phases:
        if not isinstance(phase, list) or not phase or not all(isinstance(item, str) and item for item in phase):
            raise DevelopmentConsoleError("approved solution phase closure is invalid")
        if set(phase) & seen:
            raise DevelopmentConsoleError("approved solution phase closure contains duplicates")
        for unit in phase:
            closure.append({"id": unit, "depends_on": list(previous), "roles": list(_ROLE_SEQUENCE)})
        seen.update(phase)
        previous.extend(phase)
    selected = solution.get("selected_providers")
    if not isinstance(selected, list) or set(selected) != seen:
        raise DevelopmentConsoleError("approved solution provider closure differs from phase order")
    return closure


def resolve_request(
    *,
    body: Mapping[str, Any],
    solution: Mapping[str, Any],
    plan_commit: str,
    requested_by: str,
    source_commit: str,
    freshness: Mapping[str, Any],
    provider_registry: Mapping[str, Any],
) -> tuple[dict[str, Any], AuthorityRequest]:
    """Resolve and compile one immutable development authority request."""
    if not isinstance(plan_commit, str) or _COMMIT.fullmatch(plan_commit) is None:
        raise DevelopmentConsoleError("approved Plan commit is invalid")
    if not isinstance(source_commit, str) or _COMMIT.fullmatch(source_commit) is None:
        raise DevelopmentConsoleError("approved source commit is invalid")
    if solution.get("plan_commit") != plan_commit or solution.get("dispatchable") is not True:
        raise DevelopmentConsoleError("approved Plan solution is not dispatchable at this revision")
    solution_hash = solution.get("solution_hash")
    closure_hash = solution.get("closure_hash")
    if not isinstance(solution_hash, str) or _HASH.fullmatch(solution_hash) is None or not isinstance(closure_hash, str) or _HASH.fullmatch(closure_hash) is None:
        raise DevelopmentConsoleError("approved solution hashes are invalid")
    if freshness.get("status") != "FRESH":
        raise DevelopmentConsoleError("development launch is held: required projections are not fresh")
    if provider_registry.get("schema") != "tgw-harness-provider-registry/v1" or not isinstance(provider_registry.get("providers"), list):
        raise DevelopmentConsoleError("harness provider registry is unavailable")
    provider_registry_hash = _hash(provider_registry)

    allowed = {"schema", "original_request", "scope", "constraints", "effect_limits", "root"}
    if set(body) - allowed or body.get("schema") not in {None, SCHEMA}:
        raise DevelopmentConsoleError("development request fields are not exact")
    original = body.get("original_request")
    scope = body.get("scope")
    if not isinstance(original, str) or not original.strip() or not isinstance(scope, str) or not scope.strip():
        raise DevelopmentConsoleError("original_request and scope are required")
    constraints = _strings(body.get("constraints", []), "constraints")
    effect_limits = _strings(body.get("effect_limits", []), "effect_limits")
    requested_root = body.get("root")
    selected_root = _solution_root(solution)
    alternatives = [f"{selected_root['kind']}:{selected_root['id']}"]
    if requested_root is not None:
        if (
            not isinstance(requested_root, Mapping)
            or set(requested_root) != {"kind", "id"}
            or requested_root.get("kind") not in _ROOT_KINDS
            or not isinstance(requested_root.get("id"), str)
            or not requested_root["id"]
        ):
            raise DevelopmentConsoleError("requested root is invalid")
        alternatives.append(f"{requested_root['kind']}:{requested_root['id']}")

    request_id = _request_id(original.strip(), requested_by, plan_commit)
    request = {
        "request_id": request_id,
        "original_request": original.strip(),
        "scope": scope.strip(),
        "constraints": constraints,
        "effect_limits": effect_limits,
    }
    root_matches = requested_root is None or dict(requested_root) == selected_root
    if not root_matches:
        resolution: dict[str, Any] = {
            "status": "CLARIFICATION_REQUIRED",
            "alternatives": sorted(set(alternatives)),
            "confidence": 1.0,
            "explanation": "The approved solution does not contain the requested narrower root.",
            "clarification": "Approve a solution for that exact PP/Todo root or use the approved Plan root.",
        }
    else:
        resolution = {
            "status": "RESOLVED",
            "alternatives": alternatives,
            "confidence": 1.0,
            "explanation": "The request uses the exact root and dependency phases of the approved solved Plan.",
            "plan": {"commit": plan_commit, "solution_hash": solution_hash},
            "root": selected_root,
            "closure": _closure(solution),
        }
    attempt_id = "attempt-" + request_id.removeprefix("development-")[:12]
    allocation = {
        "attempt_id": attempt_id,
        "worktree": f"/opt/TGW/w/attempts/{request_id}/{attempt_id}/worktree",
        "attempt_root": f"/var/cache/tgw/attempts/{request_id}/{attempt_id}",
    }
    lifecycle = compile_request_lifecycle(request=request, resolution=resolution, allocation=allocation)
    if lifecycle["launch_cards"]:
        for card in lifecycle["launch_cards"]:
            card["provider_selection"] = {
                "mode": "launch-time-qualified-provider",
                "registry_id": provider_registry.get("id"),
                "registry_hash": provider_registry_hash,
                "selected_provider": None,
            }
        unsigned = dict(lifecycle)
        unsigned.pop("lifecycle_hash", None)
        lifecycle = {**unsigned, "lifecycle_hash": _hash(unsigned)}
    parameters = {
        "schema": "tgw-development-launch/v1",
        "lifecycle": lifecycle,
        "source_commit": source_commit,
        "freshness": dict(freshness),
        "provider_registry_hash": provider_registry_hash,
    }
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    authority = AuthorityRequest.create(
        {
            "solution_hash": solution_hash,
            "graph_id": lifecycle["lifecycle_hash"],
            "object_generation": source_commit,
            "summary": original.strip(),
            "evidence": [lifecycle["lifecycle_hash"], freshness["receipt_hash"]],
            "effect": {
                "kind": "development-launch",
                "generation": lifecycle["lifecycle_hash"],
                "parameters": parameters,
            },
            "requested_by": requested_by,
            "expires_at": expires.isoformat(),
        },
        solution=solution,
        current_plan_commit=plan_commit,
    )
    return lifecycle, authority


def project_development_request(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Project a PlanAuthority row back into the development timeline."""
    if row.get("effect_kind") != "development-launch":
        return None
    parameters = row.get("effect_parameters")
    if not isinstance(parameters, Mapping) or parameters.get("schema") != "tgw-development-launch/v1":
        return None
    lifecycle = parameters.get("lifecycle")
    if not isinstance(lifecycle, Mapping):
        return None
    return {
        "request_id": row.get("request_id"),
        "authority_status": row.get("outcome") or row.get("decision_kind") or "pending",
        "original_request": lifecycle.get("request", {}).get("original_request"),
        "scope": lifecycle.get("request", {}).get("scope"),
        "resolution": lifecycle.get("resolution"),
        "launch_cards": lifecycle.get("launch_cards", []),
        "timeline": lifecycle.get("timeline", []),
        "lifecycle_hash": lifecycle.get("lifecycle_hash"),
        "source_commit": parameters.get("source_commit"),
        "freshness": parameters.get("freshness"),
        "requested_at": row.get("requested_at"),
        "decision": row.get("decision_kind"),
        "execution": {
            "receipt_id": row.get("receipt_id"),
            "outcome": row.get("outcome"),
            "evidence": list(row.get("execution_evidence") or ()),
            "detail": row.get("detail") or "",
        } if row.get("receipt_id") else None,
    }
