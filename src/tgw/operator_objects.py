"""Published operator objects and deliberately thin client views.

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


def validate_operator_command_values(
    published: Mapping[str, Any], command_id: str, values: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate client values solely against the server-published schema."""
    published = _mapping(published, "published")
    if published.get("schema") != OPERATOR_OBJECT_SCHEMA:
        raise OperatorObjectBindingError("unsupported published object schema")
    commands = published.get("commands")
    if not isinstance(commands, list):
        raise OperatorObjectBindingError("published.commands must be a list")
    command = next((item for item in commands if item.get("id") == command_id), None)
    if command is None:
        raise OperatorObjectBindingError("command is not published for this object")
    schema = _mapping(command.get("input_schema"), "command.input_schema")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise OperatorObjectBindingError("command input schema is not closed")
    if not isinstance(values, Mapping):
        raise OperatorObjectBindingError("command values must be an object")
    properties = _mapping(schema.get("properties", {}), "command.input_schema.properties")
    if not set(values) <= set(properties):
        raise OperatorObjectBindingError("command values contain an unpublished field")
    result: dict[str, Any] = {}
    for field, value in values.items():
        field_schema = _mapping(properties[field], f"command field {field}")
        if field_schema.get("type") == "string":
            if not isinstance(value, str):
                raise OperatorObjectBindingError(f"command field {field} must be a string")
            allowed = field_schema.get("enum")
            if allowed is not None and (not isinstance(allowed, list) or value not in allowed):
                raise OperatorObjectBindingError(f"command field {field} is not an allowed value")
            result[field] = value
        elif field_schema.get("type") == "object":
            if not isinstance(value, Mapping) or field_schema.get("additionalProperties") is not False:
                raise OperatorObjectBindingError(f"command field {field} must be a closed object")
            nested = _mapping(field_schema.get("properties", {}), f"command field {field} properties")
            if not set(value) <= set(nested):
                raise OperatorObjectBindingError(f"command field {field} contains an unpublished key")
            checked: dict[str, str] = {}
            for key, nested_value in value.items():
                nested_schema = _mapping(nested[key], f"command field {field}.{key}")
                if nested_schema.get("type") != "string" or not isinstance(nested_value, str):
                    raise OperatorObjectBindingError(f"command field {field}.{key} must be a string")
                allowed = nested_schema.get("enum")
                if allowed is not None and nested_value not in allowed:
                    raise OperatorObjectBindingError(f"command field {field}.{key} is not an allowed value")
                checked[key] = nested_value
            result[field] = checked
        else:
            raise OperatorObjectBindingError(f"command field {field} has an unsupported type")
    return result


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
            "details": deepcopy(dict(_mapping(workflow.get("details", {}), "workflow.details"))),
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
        "item": deepcopy(published.get("item", {})),
        "listing": deepcopy(published.get("listing", {})),
        "workflow": deepcopy(dict(workflow)),
        "field_schema": deepcopy(published.get("field_schema", {})),
        "commands": descriptors,
    }


def build_item_operator_object(
    *,
    item: Mapping[str, Any],
    workflow_card: Mapping[str, Any],
    category_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical item/listing/workflow view from server projections.

    This function is deliberately pure.  The HTTP host owns all reads and
    passes the current item, evaluator Action Card, and category projection.
    Clients receive the resulting policy; they never recreate it.
    """
    item = _mapping(item, "item")
    workflow_card = _mapping(workflow_card, "workflow_card")
    context = _mapping(category_context or {}, "category_context")
    entity_id = workflow_card.get("entity_id")
    generation = workflow_card.get("object_generation")
    if not isinstance(entity_id, str) or not entity_id:
        raise OperatorObjectBindingError("workflow_card.entity_id is required")
    if not isinstance(generation, str) or not generation:
        raise OperatorObjectBindingError("workflow_card.object_generation is required")
    if item.get("sku") != entity_id:
        raise OperatorObjectBindingError("item and workflow identities must match")

    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), Mapping) else {}
    provider_listing = item.get("ebay_listing") if isinstance(item.get("ebay_listing"), Mapping) else {}
    draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), Mapping) else {}
    listing_status = str(provider_listing.get("status") or "")
    offer_status = str(offer.get("status") or "")
    is_published = listing_status.lower() == "active" or offer_status.upper() == "PUBLISHED"

    conditions = []
    for condition in context.get("conditions", ()):
        if not isinstance(condition, Mapping):
            continue
        value = condition.get("enum") or condition.get("condition_enum")
        label = condition.get("label") or condition.get("condition_label")
        if isinstance(value, str) and value and isinstance(label, str) and label:
            conditions.append({"value": value, "label": label})
    current_condition = str(draft.get("condition_enum") or draft.get("condition") or "")
    valid_condition = not conditions or any(option["value"] == current_condition for option in conditions)

    aspects = []
    missing_aspects = []
    specifics = draft.get("item_specifics") if isinstance(draft.get("item_specifics"), Mapping) else {}
    for aspect in context.get("aspects", ()):
        if not isinstance(aspect, Mapping) or not isinstance(aspect.get("name"), str):
            continue
        descriptor = {
            "name": aspect["name"],
            "required": bool(aspect.get("required")),
            "mode": aspect.get("mode"),
            "allowed_values": list(aspect.get("allowed_values") or ()),
            "value": specifics.get(aspect["name"]),
        }
        aspects.append(descriptor)
        if descriptor["required"] and not descriptor["value"]:
            missing_aspects.append(descriptor["name"])

    validation_messages = []
    category_id = str(draft.get("category_id") or item.get("ebay_category_id") or "").strip()
    if not category_id or category_id == "99":
        validation_messages.append("A valid eBay category is required.")
    if current_condition and not valid_condition:
        validation_messages.append("The selected condition is not valid for this category.")
    if not current_condition:
        validation_messages.append("A category-valid displayed condition is required.")
    if missing_aspects:
        validation_messages.append("Required aspects are missing: " + ", ".join(sorted(missing_aspects)))

    reconciliation = list(workflow_card.get("reconciliation_gates") or ())
    conflicts = list(workflow_card.get("ownership_conflicts") or ())
    active = list(workflow_card.get("active_attempts") or ())
    blockers = [str(value) for value in reconciliation]
    blockers.extend("ownership conflict: " + ", ".join(map(str, value)) for value in conflicts)
    blockers.extend(validation_messages)

    if reconciliation or conflicts:
        state = "reconciliation_required"
    elif active:
        state = "in_progress"
    elif is_published:
        state = "published"
    elif offer.get("offer_id"):
        state = "staged"
    elif validation_messages:
        state = "held"
    else:
        state = "ready"

    list_enabled = not blockers and not active and not is_published
    update_enabled = bool(draft and (offer.get("offer_id") or provider_listing.get("listing_id"))) and not blockers and not active
    list_reason = None
    if is_published:
        list_reason = "The provider already reports this item as published."
    elif active:
        list_reason = "The authoritative workflow is already running."
    elif blockers:
        list_reason = blockers[0]
    update_reason = None
    if active:
        update_reason = "The authoritative workflow is already running."
    elif blockers:
        update_reason = blockers[0]
    elif not draft:
        update_reason = "No listing draft exists."
    elif not (offer.get("offer_id") or provider_listing.get("listing_id")):
        update_reason = "The item has not been staged at the provider."

    evidence = []
    for fingerprint in workflow_card.get("fingerprints", ()):
        if isinstance(fingerprint, Mapping):
            evidence.append(
                f"condition:{fingerprint.get('condition_id')}:{fingerprint.get('result')}"
            )
    for attempt in workflow_card.get("attempts", ()):
        if isinstance(attempt, Mapping) and attempt.get("job_id"):
            evidence.append(f"attempt:{attempt['job_id']}:{attempt.get('state')}")
    if provider_listing.get("listing_id"):
        evidence.append(f"provider-listing:{provider_listing['listing_id']}:{listing_status}")

    item_view = {
        "entity_id": entity_id,
        "object_generation": generation,
        "record": deepcopy(dict(item)),
    }
    listing_view = {
        "entity_id": entity_id,
        "object_generation": generation,
        "provider_state": listing_status or offer_status or "not-staged",
        "offer": deepcopy(dict(offer)),
        "listing": deepcopy(dict(provider_listing)),
    }
    workflow_view = {
        "entity_id": entity_id,
        "object_generation": generation,
        "state": state,
        "reasons": blockers,
        "evidence": sorted(set(evidence)),
        "graph_id": workflow_card.get("graph_id"),
        "details": deepcopy(dict(workflow_card)),
    }
    field_schema = {
        "category": {
            "value": category_id,
            "label": context.get("category_name") or draft.get("category_name") or item.get("ebay_category_name"),
            "required": True,
        },
        "condition": {
            "value": current_condition,
            "label": draft.get("condition_label") or draft.get("condition_description"),
            "required": True,
            "valid": valid_condition and bool(current_condition),
            "options": conditions,
        },
        "aspects": aspects,
        "defaults": {
            "fulfillment_policy_id": context.get("fulfillment_policy_id"),
            "store_category": deepcopy(context.get("store_category")),
        },
        "validation_messages": validation_messages,
    }
    command_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "condition_enum": {
                "type": "string",
                "enum": [option["value"] for option in conditions],
            },
            "item_specifics": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    aspect["name"]: {
                        "type": "string",
                        **({"enum": aspect["allowed_values"]} if aspect["allowed_values"] else {}),
                    }
                    for aspect in aspects
                },
            },
        },
    }
    return publish_operator_object(
        item=item_view,
        listing=listing_view,
        workflow=workflow_view,
        field_schema=field_schema,
        commands=(
            {
                "id": "list-item",
                "enabled": list_enabled,
                "reason": list_reason,
                "authority_scope": "publication",
                "input_schema": command_input_schema,
            },
            {
                "id": "update-item",
                "enabled": update_enabled,
                "reason": update_reason,
                "authority_scope": "update-restage",
                "input_schema": command_input_schema,
            },
        ),
    )


def web_adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Web adapter: render and submit only the shared API view."""
    return adapter_view(published)


def flutter_adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Flutter adapter: render and submit only the shared API view."""
    return adapter_view(published)
