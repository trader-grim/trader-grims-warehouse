"""Pure W18 revision-freshness and refresh-transition controller."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REVISIONS = ("plan", "code_graph", "workflow", "actor_contract")


class ProjectionRefreshError(ValueError):
    pass


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _exact_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise ProjectionRefreshError(f"{label} must be an exact sha256 hash")
    return value


def _exact_commit(value: Any, label: str) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise ProjectionRefreshError(f"{label} must be an exact commit")
    return value


def _mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ProjectionRefreshError(f"{label} fields are not exact")
    return dict(value)


def _revision(value: Any, label: str) -> dict[str, str]:
    item = _mapping(value, {"source", "materialization", "build", "health"}, label)
    _exact_hash(item["materialization"], f"{label} materialization")
    _exact_hash(item["build"], f"{label} build")
    if not isinstance(item["source"], str) or not item["source"]:
        raise ProjectionRefreshError(f"{label} source is invalid")
    if item["health"] not in {"READY", "STALE", "FAILED"}:
        raise ProjectionRefreshError(f"{label} health is invalid")
    return item


def compile_projection_refresh(*, request: Mapping[str, Any]) -> dict[str, Any]:
    """Compile a coalesced, non-activating W18 refresh disposition."""
    value = _mapping(request, {"schema", "lease", "desired", "observed", "actors", "refresh"}, "refresh request")
    if value["schema"] != "tgw-w18-projection-refresh-request/v1":
        raise ProjectionRefreshError("refresh request schema is invalid")
    lease = _mapping(value["lease"], {"id", "generation"}, "fleet lease")
    if not isinstance(lease["id"], str) or not lease["id"] or isinstance(lease["generation"], bool) or not isinstance(lease["generation"], int) or lease["generation"] < 0:
        raise ProjectionRefreshError("fleet lease is invalid")
    desired = _mapping(value["desired"], set(_REVISIONS), "desired revisions")
    observed = _mapping(value["observed"], set(_REVISIONS), "observed projections")
    desired_values = {name: _revision(desired[name], f"desired {name}") for name in _REVISIONS}
    observed_values = {name: _revision(observed[name], f"observed {name}") for name in _REVISIONS}
    actors = value["actors"]
    if not isinstance(actors, list) or not actors or not all(isinstance(item, Mapping) for item in actors):
        raise ProjectionRefreshError("actor contracts are invalid")
    actor_ids: set[str] = set()
    actor_issues: list[str] = []
    for raw in actors:
        actor = _mapping(raw, {"id", "generation", "status"}, "actor contract")
        if not isinstance(actor["id"], str) or not actor["id"] or actor["id"] in actor_ids:
            raise ProjectionRefreshError("actor identity is invalid")
        actor_ids.add(actor["id"])
        _exact_hash(actor["generation"], "actor contract generation")
        if actor["status"] != "READY":
            actor_issues.append(actor["id"])
    refresh = _mapping(value["refresh"], {"predecessor", "successor", "outcome"}, "refresh outcome")
    _exact_hash(refresh["predecessor"], "refresh predecessor")
    _exact_hash(refresh["successor"], "refresh successor")
    if refresh["outcome"] not in {"PENDING", "HEALTHY", "FAILED"}:
        raise ProjectionRefreshError("refresh outcome is invalid")
    stale = [name for name in _REVISIONS if observed_values[name] != desired_values[name]]
    reasons = [f"stale-projection:{name}" for name in stale]
    reasons.extend(f"quarantined-actor:{actor}" for actor in actor_issues)
    if refresh["outcome"] == "FAILED":
        status = "ROLLBACK_REQUIRED"
        reasons.append("refresh-health-failed")
    elif actor_issues:
        status = "QUARANTINED"
    elif stale:
        status = "HOLD"
    elif refresh["outcome"] == "PENDING":
        status = "PREPARED"
    else:
        status = "FRESH"
    unsigned = {
        "schema": "tgw-w18-projection-refresh-receipt/v1",
        "lease": lease,
        "desired": desired_values,
        "observed": observed_values,
        "actors": [dict(item) for item in actors],
        "refresh": refresh,
        "status": status,
        "reasons": sorted(reasons),
        "activation": "declarative-only",
    }
    return {**unsigned, "receipt_hash": _hash(unsigned)}
