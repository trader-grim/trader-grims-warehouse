"""Read-only published operator objects and deliberately thin client views.

This is a transport seam, not a workflow evaluator or command dispatcher.
Callers must pass the current server-owned item, listing, workflow, and field
views.  The assembler only verifies their shared identity/generation and
emits one stable object which every client can render.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

OPERATOR_OBJECT_SCHEMA = "tgw-operator-object/v1"
ADAPTER_VIEW_SCHEMA = "tgw-operator-adapter-view/v1"


class OperatorObjectBindingError(ValueError):
    """A supplied component is not a coherent canonical object view."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OperatorObjectBindingError(f"{name} must be an object")
    return value


def _identity(view: Mapping[str, Any], name: str) -> tuple[str, str]:
    entity_id = view.get("entity_id")
    generation = view.get("object_generation")
    if not isinstance(entity_id, str) or not entity_id:
        raise OperatorObjectBindingError(f"{name}.entity_id is required")
    if not isinstance(generation, str) or not generation:
        raise OperatorObjectBindingError(f"{name}.object_generation is required")
    return entity_id, generation


def _string_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise OperatorObjectBindingError(f"{name} must be a list")
    if not all(isinstance(item, str) and item for item in value):
        raise OperatorObjectBindingError(f"{name} must contain non-empty strings")
    return list(value)


def _command_descriptor(command: Mapping[str, Any]) -> dict[str, Any]:
    command_id = command.get("id")
    if command_id not in {"list-item", "update-item"}:
        raise OperatorObjectBindingError("unrecognized operator command")
    enabled = command.get("enabled")
    if not isinstance(enabled, bool):
        raise OperatorObjectBindingError("command.enabled must be boolean")
    reason = command.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise OperatorObjectBindingError("command.reason must be a non-empty string when present")

    # This is declarative metadata only.  Issuing the grant remains in the
    # server command handler; a browser or Flutter client cannot replace it.
    expected_scope = "publication" if command_id == "list-item" else "update-restage"
    if command.get("authority_scope") != expected_scope:
        raise OperatorObjectBindingError("command authority scope does not match command id")
    return {
        "id": command_id,
        "label": "List Item" if command_id == "list-item" else "Update Item",
        "enabled": enabled,
        "reason": reason,
        "authority_scope": expected_scope,
        "input_schema": deepcopy(command.get("input_schema", {})),
        "refresh_target": "current-object",
    }


def publish_operator_object(
    *,
    item: Mapping[str, Any],
    listing: Mapping[str, Any],
    workflow: Mapping[str, Any],
    field_schema: Mapping[str, Any],
    commands: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Publish one coherent read-only view from canonical server projections.

    ``workflow`` is not recomputed here.  It must be the result of the
    authoritative evaluator/action-card projection for this exact generation.
    """
    item = _mapping(item, "item")
    listing = _mapping(listing, "listing")
    workflow = _mapping(workflow, "workflow")
    field_schema = _mapping(field_schema, "field_schema")
    binding = _identity(item, "item")
    if _identity(listing, "listing") != binding or _identity(workflow, "workflow") != binding:
        raise OperatorObjectBindingError("item, listing, and workflow bindings must match")

    state = workflow.get("state")
    if not isinstance(state, str) or not state:
        raise OperatorObjectBindingError("workflow.state is required")
    evidence = _string_list(workflow.get("evidence", ()), "workflow.evidence")
    descriptors = [_command_descriptor(_mapping(command, "command")) for command in commands]
    command_ids = [command["id"] for command in descriptors]
    if len(command_ids) != len(set(command_ids)):
        raise OperatorObjectBindingError("command ids must be unique")

    entity_id, generation = binding
    return {
        "schema": OPERATOR_OBJECT_SCHEMA,
        "entity_id": entity_id,
        "object_generation": generation,
        "item": deepcopy(dict(item)),
        "listing": deepcopy(dict(listing)),
        "workflow": {
            "state": state,
            "reasons": _string_list(workflow.get("reasons", ()), "workflow.reasons"),
            "evidence": evidence,
            "graph_id": workflow.get("graph_id"),
        },
        "field_schema": deepcopy(dict(field_schema)),
        "commands": descriptors,
    }


def adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Return the entire render/submit contract without client-side policy."""
    published = _mapping(published, "published")
    if published.get("schema") != OPERATOR_OBJECT_SCHEMA:
        raise OperatorObjectBindingError("unsupported published object schema")
    entity_id, generation = _identity(published, "published")
    workflow = _mapping(published.get("workflow"), "published.workflow")
    commands = published.get("commands")
    if not isinstance(commands, list):
        raise OperatorObjectBindingError("published.commands must be a list")
    # Revalidate, so a hand-built object cannot smuggle a publish-capable
    # update command into a thin adapter.
    descriptors = [_command_descriptor(_mapping(command, "published.command")) for command in commands]
    return {
        "schema": ADAPTER_VIEW_SCHEMA,
        "entity_id": entity_id,
        "object_generation": generation,
        "state": workflow.get("state"),
        "reasons": deepcopy(workflow.get("reasons", [])),
        "evidence": deepcopy(workflow.get("evidence", [])),
        "field_schema": deepcopy(published.get("field_schema", {})),
        "commands": descriptors,
    }


def web_adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Web adapter: render and submit only the shared API view."""
    return adapter_view(published)


def flutter_adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Flutter adapter: render and submit only the shared API view."""
    return adapter_view(published)
