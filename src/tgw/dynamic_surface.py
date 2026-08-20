"""Closed, data-only W14/W17 operator-surface boundary.

Untrusted providers may propose a document in this schema.  They cannot add
HTML, scripts, URLs, endpoints, shell commands, or an unregistered effect.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[a-z][a-z0-9:._-]{0,255}\Z")
_URL = re.compile(r"(?:https?|ftp|file|data|javascript):", re.IGNORECASE)
_COMPONENT_FIELDS = {
    "heading": {"type", "id", "text"},
    "text": {"type", "id", "text"},
    "evidence": {"type", "id", "items"},
    "input": {"type", "id", "label", "input"},
}


class DynamicSurfaceError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise DynamicSurfaceError(f"{label} fields are not exact")
    return dict(value)


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise DynamicSurfaceError(f"{label} is invalid")
    return value


def _exact_hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise DynamicSurfaceError(f"{label} must be an exact sha256 hash")
    return value


def _utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise DynamicSurfaceError(f"{label} is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DynamicSurfaceError(f"{label} is invalid") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DynamicSurfaceError(f"{label} must include timezone")
    return result.astimezone(timezone.utc)


def _safe_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or _URL.search(value):
        raise DynamicSurfaceError(f"{label} is invalid or contains a remote resource")
    return value


def _validate_input(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("kind") not in {"string", "boolean", "choice"}:
        raise DynamicSurfaceError(f"{label} is invalid")
    expected = {"kind", "required", "choices"} if value["kind"] == "choice" else {"kind", "required"}
    item = _mapping(value, expected, label)
    if not isinstance(item["required"], bool):
        raise DynamicSurfaceError(f"{label} required is invalid")
    if item["kind"] == "choice":
        choices = item["choices"]
        if not isinstance(choices, list) or not choices or len(choices) > 32:
            raise DynamicSurfaceError(f"{label} choices are invalid")
        item["choices"] = [_safe_text(choice, f"{label} choice") for choice in choices]
        if len(set(item["choices"])) != len(item["choices"]):
            raise DynamicSurfaceError(f"{label} choices are duplicated")
    return item


def compile_dynamic_surface(
    *, proposal: Mapping[str, Any], handler_registry: Mapping[str, Mapping[str, Any]],
    renderer_version: str, observed_at: str,
) -> dict[str, Any]:
    """Validate and deterministically render one short-lived data document."""
    value = _mapping(proposal, {
        "schema", "surface_id", "request_id", "plan_commit", "solution_hash",
        "card_hash", "authority_hash", "expiry", "audience", "title",
        "components", "actions", "state",
    }, "surface proposal")
    if value["schema"] != "tgw-dynamic-surface-proposal/v1" or value["state"] != "LIVE":
        raise DynamicSurfaceError("surface proposal is not live schema v1")
    for field in ("surface_id", "request_id", "audience"):
        _identity(value[field], field.replace("_", " "))
    if not isinstance(value["plan_commit"], str) or _COMMIT.fullmatch(value["plan_commit"]) is None:
        raise DynamicSurfaceError("Plan commit must be exact")
    for field in ("solution_hash", "card_hash", "authority_hash"):
        _exact_hash(value[field], field.replace("_", " "))
    observed, expiry = _utc(observed_at, "observation"), _utc(value["expiry"], "expiry")
    if expiry <= observed:
        raise DynamicSurfaceError("surface is expired")
    _safe_text(value["title"], "surface title")

    components = value["components"]
    if not isinstance(components, list) or not components or len(components) > 64:
        raise DynamicSurfaceError("surface components are invalid")
    component_ids: set[str] = set()
    inputs: dict[str, dict[str, Any]] = {}
    rendered_components: list[dict[str, Any]] = []
    for raw in components:
        if not isinstance(raw, Mapping) or raw.get("type") not in _COMPONENT_FIELDS:
            raise DynamicSurfaceError("component type is not allowlisted")
        component = _mapping(raw, _COMPONENT_FIELDS[raw["type"]], "component")
        component_id = _identity(component["id"], "component id")
        if component_id in component_ids:
            raise DynamicSurfaceError("component id is duplicated")
        component_ids.add(component_id)
        if component["type"] in {"heading", "text"}:
            _safe_text(component["text"], "component text")
        elif component["type"] == "evidence":
            if not isinstance(component["items"], list) or not component["items"]:
                raise DynamicSurfaceError("evidence component is empty")
            component["items"] = [_exact_hash(item, "evidence identity") for item in component["items"]]
        else:
            _safe_text(component["label"], "input label")
            component["input"] = _validate_input(component["input"], f"input {component_id}")
            inputs[component_id] = component["input"]
        rendered_components.append(component)

    actions = value["actions"]
    if not isinstance(actions, list) or not actions or len(actions) > 16:
        raise DynamicSurfaceError("surface actions are invalid")
    action_ids: set[str] = set()
    rendered_actions: list[dict[str, Any]] = []
    for raw in actions:
        action = _mapping(raw, {"id", "label", "decision", "handler_id", "field_ids"}, "surface action")
        action_id = _identity(action["id"], "action id")
        handler_id = _identity(action["handler_id"], "handler id")
        decision = _identity(action["decision"], "decision")
        if action_id in action_ids:
            raise DynamicSurfaceError("action id is duplicated")
        action_ids.add(action_id)
        _safe_text(action["label"], "action label")
        handler = handler_registry.get(handler_id)
        if not isinstance(handler, Mapping) or set(handler) != {"decisions"}:
            raise DynamicSurfaceError("action handler is not registered")
        decisions = handler["decisions"]
        if not isinstance(decisions, list) or decision not in decisions:
            raise DynamicSurfaceError("decision is not legal for action handler")
        if not isinstance(action["field_ids"], list) or len(action["field_ids"]) != len(set(action["field_ids"])):
            raise DynamicSurfaceError("action field bindings are invalid")
        if not set(action["field_ids"]) <= set(inputs):
            raise DynamicSurfaceError("action references an unknown input")
        rendered_actions.append(action)

    source = dict(value)
    source["components"], source["actions"] = rendered_components, rendered_actions
    snapshot = {
        "title": source["title"], "components": rendered_components,
        "actions": rendered_actions, "redaction": "hash-identities-and-operator-visible-text-only",
    }
    rendered = {"title": source["title"], "components": rendered_components, "actions": rendered_actions}
    unsigned = {
        "schema": "tgw-dynamic-surface/v1", "status": "LIVE", "source": source,
        "source_schema_hash": _hash({"schema": source["schema"], "component_types": sorted(_COMPONENT_FIELDS)}),
        "presentation_snapshot": snapshot, "presentation_hash": _hash(snapshot),
        "renderer": {"version": _exact_hash(renderer_version, "renderer version"), "mode": "data-only-local"},
        "rendered": rendered, "render_hash": _hash(rendered),
    }
    return {**unsigned, "surface_hash": _hash(unsigned)}


def _typed_values(action: Mapping[str, Any], surface: Mapping[str, Any], values: Any) -> dict[str, Any]:
    if not isinstance(values, Mapping) or set(values) != set(action["field_ids"]):
        raise DynamicSurfaceError("submitted fields do not match the bound action")
    components = {item["id"]: item for item in surface["source"]["components"] if item["type"] == "input"}
    result: dict[str, Any] = {}
    for field_id in action["field_ids"]:
        spec, value = components[field_id]["input"], values[field_id]
        if value is None and not spec["required"]:
            result[field_id] = None
        elif spec["kind"] == "boolean" and isinstance(value, bool):
            result[field_id] = value
        elif spec["kind"] == "string" and isinstance(value, str) and value.strip() and len(value) <= 4096 and not _URL.search(value):
            result[field_id] = value
        elif spec["kind"] == "choice" and value in spec["choices"]:
            result[field_id] = value
        else:
            raise DynamicSurfaceError(f"submitted value for {field_id} is invalid")
    return result


def submit_dynamic_surface(
    *, surface: Mapping[str, Any], submission: Mapping[str, Any],
    current_card_hash: str, current_authority_hash: str,
    handlers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]],
    persist_receipt: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    claim_submission: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    """Invoke only the registered typed decision bound by a still-live surface."""
    value = _mapping(submission, {"schema", "surface_hash", "action_id", "values", "operator", "submitted_at"}, "surface submission")
    if value["schema"] != "tgw-dynamic-surface-submission/v1":
        raise DynamicSurfaceError("surface submission schema is invalid")
    unsigned_surface = dict(surface)
    claimed_surface_hash = unsigned_surface.pop("surface_hash", None)
    if claimed_surface_hash != _hash(unsigned_surface) or value["surface_hash"] != claimed_surface_hash:
        raise DynamicSurfaceError("surface hash mismatch")
    if surface.get("status") != "LIVE":
        raise DynamicSurfaceError("surface is inert")
    source = surface["source"]
    if source["card_hash"] != _exact_hash(current_card_hash, "current card") or source["authority_hash"] != _exact_hash(current_authority_hash, "current authority"):
        raise DynamicSurfaceError("surface authority is stale or superseded")
    if _utc(value["submitted_at"], "submission") >= _utc(source["expiry"], "expiry"):
        raise DynamicSurfaceError("surface is expired")
    operator = _identity(value["operator"], "operator")
    action = next((item for item in source["actions"] if item["id"] == value["action_id"]), None)
    if action is None:
        raise DynamicSurfaceError("action is not legal for this surface")
    handler = handlers.get(action["handler_id"])
    if not callable(handler):
        raise DynamicSurfaceError("typed handler is unavailable")
    typed_values = _typed_values(action, surface, value["values"])
    invocation = {
        "request_id": source["request_id"], "surface_id": source["surface_id"],
        "surface_hash": claimed_surface_hash, "card_hash": source["card_hash"],
        "authority_hash": source["authority_hash"], "operator": operator,
        "decision": action["decision"], "values": typed_values,
    }
    if not callable(claim_submission):
        raise DynamicSurfaceError("submission claim boundary is unavailable")
    claimed = claim_submission(invocation)
    if (
        not isinstance(claimed, Mapping)
        or claimed.get("status") != "CLAIMED"
        or not isinstance(claimed.get("claim_hash"), str)
        or _HASH.fullmatch(claimed["claim_hash"]) is None
    ):
        raise DynamicSurfaceError("submission claim was refused")
    claim = dict(claimed)
    pending = {
        "schema": "tgw-dynamic-surface-decision-receipt/v1", **invocation,
        "handler_id": action["handler_id"], "submitted_at": value["submitted_at"],
        "presentation_hash": surface["presentation_hash"], "render_hash": surface["render_hash"],
        "claim": claim, "status": "PENDING",
    }
    pending["receipt_hash"] = _hash(pending)
    pending_sink = persist_receipt(pending)
    if not isinstance(pending_sink, Mapping) or not pending_sink:
        raise DynamicSurfaceError("immutable receipt sink refused the pending decision")
    outcome = dict(handler(invocation))
    receipt = {
        "schema": "tgw-dynamic-surface-decision-receipt/v1", **invocation,
        "handler_id": action["handler_id"], "submitted_at": value["submitted_at"],
        "presentation_hash": surface["presentation_hash"], "render_hash": surface["render_hash"],
        "claim": claim, "status": "FINALIZED", "pending_receipt_hash": pending["receipt_hash"],
        "pending_sink": dict(pending_sink), "outcome": outcome,
    }
    receipt["receipt_hash"] = _hash(receipt)
    sink = persist_receipt(receipt)
    if not isinstance(sink, Mapping) or not sink:
        raise DynamicSurfaceError("immutable receipt sink refused the decision")
    return {**receipt, "sink": dict(sink)}
