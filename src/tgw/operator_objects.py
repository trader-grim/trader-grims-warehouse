"""Published operator objects and deliberately thin client views.

This is a transport seam, not a workflow evaluator or command dispatcher.
Callers must pass the current server-owned item, listing, workflow, and field
views.  The assembler only verifies their shared identity/generation and
emits one stable object which every client can render.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence

from tgw import inventory_record
from tgw.ebay.draft_specifics import get_ebay_aspects
from tgw.item_mutation import item_generation

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
    if command_id not in {
        "save-inventory", "save-listing-draft", "list-item", "update-item",
    }:
        raise OperatorObjectBindingError("unrecognized operator command")
    enabled = command.get("enabled")
    if not isinstance(enabled, bool):
        raise OperatorObjectBindingError("command.enabled must be boolean")
    reason = command.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise OperatorObjectBindingError("command.reason must be a non-empty string when present")

    # This is declarative metadata only.  Issuing the grant remains in the
    # server command handler; a browser or Flutter client cannot replace it.
    expected_scope = {
        "save-inventory": "local-item-mutation",
        "save-listing-draft": "local-item-mutation",
        "list-item": "publication",
        "update-item": "update-restage",
    }[command_id]
    if command.get("authority_scope") != expected_scope:
        raise OperatorObjectBindingError("command authority scope does not match command id")
    return {
        "id": command_id,
        "label": {
            "save-inventory": "Save Inventory",
            "save-listing-draft": "Save Listing Draft",
            "list-item": "List Item",
            "update-item": "Update Item",
        }[command_id],
        "enabled": enabled,
        "reason": reason,
        "authority_scope": expected_scope,
        "input_schema": deepcopy(command.get("input_schema", {})),
        "refresh_target": "current-object",
    }


def _validate_schema_value(schema: Mapping[str, Any], value: Any, label: str) -> Any:
    field_type = schema.get("type")
    if value is None and schema.get("nullable") is True:
        return None
    if field_type == "string":
        if not isinstance(value, str):
            raise OperatorObjectBindingError(f"{label} must be a string")
        allowed = schema.get("enum")
        if allowed is not None:
            if not isinstance(allowed, list):
                raise OperatorObjectBindingError(f"{label} has an invalid value schema")
            if schema.get("case_insensitive_enum") is True:
                matches = [
                    candidate
                    for candidate in allowed
                    if isinstance(candidate, str)
                    and candidate.casefold() == value.casefold()
                ]
                if len(matches) > 1:
                    raise OperatorObjectBindingError(
                        f"{label} has an ambiguous value vocabulary"
                    )
                if len(matches) == 1:
                    return matches[0]
            if value not in allowed:
                raise OperatorObjectBindingError(f"{label} is not an allowed value")
        return value
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OperatorObjectBindingError(f"{label} must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise OperatorObjectBindingError(f"{label} must be a finite number")
        return value
    if field_type == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise OperatorObjectBindingError(f"{label} must be an integer")
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise OperatorObjectBindingError(f"{label} must be boolean")
        return value
    if field_type == "string-map":
        if not isinstance(value, Mapping):
            raise OperatorObjectBindingError(f"{label} must be an object")
        if not all(isinstance(key, str) and key and isinstance(item, str) for key, item in value.items()):
            raise OperatorObjectBindingError(f"{label} must contain string fields")
        return dict(value)
    if field_type == "object":
        if not isinstance(value, Mapping) or schema.get("additionalProperties") is not False:
            raise OperatorObjectBindingError(f"{label} must be a closed object")
        nested = _mapping(schema.get("properties", {}), f"{label} properties")
        if not set(value) <= set(nested):
            raise OperatorObjectBindingError(f"{label} contains an unpublished key")
        return {key: _validate_schema_value(_mapping(nested[key], f"{label}.{key} schema"), nested_value, f"{label}.{key}") for key, nested_value in value.items()}
    raise OperatorObjectBindingError(f"{label} has an unsupported type")


def validate_operator_command_values(
    published: Mapping[str, Any],
    command_id: str,
    values: Mapping[str, Any],
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
    return {
        field: _validate_schema_value(
            _mapping(properties[field], f"command field {field}"),
            value,
            f"command field {field}",
        )
        for field, value in values.items()
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
    if item_generation(item) != generation:
        raise OperatorObjectBindingError(
            "item generation and workflow_card.object_generation must match"
        )

    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), Mapping) else {}
    provider_listing = item.get("ebay_listing") if isinstance(item.get("ebay_listing"), Mapping) else {}
    draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), Mapping) else {}
    listing_status = str(provider_listing.get("status") or "")
    offer_status = str(offer.get("status") or "")
    conditions = []
    for condition in context.get("conditions", ()):
        if not isinstance(condition, Mapping):
            continue
        value = condition.get("enum") or condition.get("condition_enum")
        label = condition.get("label") or condition.get("condition_label")
        if isinstance(value, str) and value and isinstance(label, str) and label:
            conditions.append({"value": value, "label": label})
    current_condition = str(draft.get("condition_enum") or draft.get("condition") or "")
    category_id = str(draft.get("category_id") or item.get("ebay_category_id") or "").strip()
    policy_fields_present = "category_recognized" in context or "required_flag_valid" in context
    valid_condition = (
        any(option["value"] == current_condition for option in conditions)
        if policy_fields_present
        else not conditions or any(
            option["value"] == current_condition for option in conditions
        )
    )
    category_recognized = context.get("category_recognized") is True if policy_fields_present else bool(category_id)
    required_flag_valid = context.get("required_flag_valid") is True if policy_fields_present else True
    listing_condition_required = context.get("item_condition_required") is True if policy_fields_present else True
    display_conditions = list(conditions)
    if policy_fields_present or not listing_condition_required:
        display_conditions.insert(
            0,
            {
                "value": "",
                "label": (
                    "Clear listing condition"
                    if listing_condition_required
                    else "No listing condition"
                ),
            },
        )
    if current_condition and not valid_condition:
        display_conditions.insert(0, {"value": current_condition, "label": f"{draft.get('condition_label') or current_condition} — not allowed; remap or clear", "display_only": True})

    aspects = []
    missing_aspects = []
    specifics = get_ebay_aspects(item)
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
    if not category_id or category_id == "99" or not category_recognized:
        validation_messages.append("A valid eBay category is required.")
    if category_id and not required_flag_valid:
        validation_messages.append("The eBay itemConditionRequired policy flag is unresolved.")
    if current_condition and not valid_condition:
        validation_messages.append("The selected condition is not valid for this category.")
    if not current_condition and listing_condition_required:
        validation_messages.append("A category-valid displayed condition is required.")
    if missing_aspects:
        validation_messages.append("Required aspects are missing: " + ", ".join(sorted(missing_aspects)))

    operator_projection = _mapping(
        workflow_card.get("operator_projection"),
        "workflow_card.operator_projection",
    )
    state = operator_projection.get("state")
    if state not in {
        "reconciliation_required", "in_progress", "published", "staged",
        "ready", "held",
    }:
        raise OperatorObjectBindingError("operator projection state is invalid")
    projection_reasons = _string_list(
        operator_projection.get("reasons", ()),
        "workflow_card.operator_projection.reasons",
    )
    projected_commands = _mapping(
        operator_projection.get("commands"),
        "workflow_card.operator_projection.commands",
    )
    if set(projected_commands) != {"save-draft", "list-item", "update-item"}:
        raise OperatorObjectBindingError("operator projection commands are incomplete")

    def projected_command(command_id: str) -> tuple[bool, str | None]:
        value = _mapping(
            projected_commands[command_id],
            f"workflow_card.operator_projection.commands.{command_id}",
        )
        if set(value) != {"enabled", "reason"} or not isinstance(value["enabled"], bool):
            raise OperatorObjectBindingError("operator projection command is invalid")
        reason = value["reason"]
        if reason is not None and (not isinstance(reason, str) or not reason):
            raise OperatorObjectBindingError("operator projection command reason is invalid")
        return value["enabled"], reason

    evidence = []
    for fingerprint in workflow_card.get("fingerprints", ()):
        if isinstance(fingerprint, Mapping):
            evidence.append(f"condition:{fingerprint.get('condition_id')}:{fingerprint.get('result')}")
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
        "reasons": projection_reasons,
        "evidence": sorted(set(evidence)),
        "graph_id": workflow_card.get("graph_id"),
        "details": deepcopy(dict(workflow_card)),
    }
    configured_record_conditions = [
        str(value)
        for value in context.get("record_condition_vocabulary", ())
        if isinstance(value, str) and value
    ]
    stored_record_condition = str(item.get("condition") or "")
    conditions_by_identity: dict[str, list[str]] = {}
    for value in configured_record_conditions:
        conditions_by_identity.setdefault(value.casefold(), []).append(value)
    condition_collisions = [
        {
            "identity": identity,
            "values": sorted(values, key=lambda value: (value.casefold(), value)),
        }
        for identity, values in sorted(conditions_by_identity.items())
        if len(values) > 1
    ]
    record_conditions = []
    for configured in configured_record_conditions:
        if len(conditions_by_identity[configured.casefold()]) != 1:
            continue
        # Production condition_factors uses lowercase identity labels.  Keep a
        # more intentional existing display spelling for that same identity;
        # otherwise the configured spelling remains canonical.
        if (
            stored_record_condition
            and configured.casefold() == stored_record_condition.casefold()
            and configured == configured.casefold()
            and stored_record_condition != stored_record_condition.casefold()
        ):
            record_conditions.append(stored_record_condition)
        else:
            record_conditions.append(configured)
    record_condition_options = [
        {"value": value, "label": value}
        for value in record_conditions
    ]
    published_record_condition = next(
        (
            value
            for value in record_conditions
            if value.casefold() == stored_record_condition.casefold()
        ),
        stored_record_condition,
    )
    if stored_record_condition and not any(
        option["value"].casefold() == stored_record_condition.casefold()
        for option in record_condition_options
    ):
        record_condition_options.insert(0, {
            "value": stored_record_condition,
            "label": (
                f"{stored_record_condition} — unavailable until vocabulary is repaired"
            ),
            "display_only": True,
        })
    group_options = list(context.get("category_groups") or ())
    record_condition_projection = (
        "record_condition_vocabulary" in context or bool(group_options)
    )
    record_condition_drift = {
        "empty": not configured_record_conditions,
        "casefold_collisions": condition_collisions,
    }
    field_schema = {
        "item_fields": {
            "title": {"type": "string", "label": "Inventory title", "value": item.get("title") or ""},
            "location": {"type": "string", "label": "Location", "value": item.get("location") or ""},
            "notes": {"type": "string", "label": "Notes", "value": item.get("notes") or ""},
            "item_attributes": {"type": "string-map", "label": "Inventory attributes", "value": deepcopy(inventory_record.get_inventory_fields(item))},
        },
        "listing_fields": {
            "title": {"type": "string", "label": "Listing title", "value": draft.get("title") or item.get("title") or ""},
            "description": {"type": "string", "label": "Description", "value": draft.get("description") or draft.get("listing_description") or ""},
            "price": {"type": "number", "nullable": True, "label": "Price", "value": draft.get("price")},
            "quantity": {"type": "integer", "nullable": True, "label": "Quantity", "value": draft.get("quantity")},
            "category_id": {"type": "string", "label": "eBay category ID", "value": category_id},
            "secondary_category_id": {"type": "string", "nullable": True, "label": "Secondary category ID", "value": draft.get("secondary_category_id")},
            "condition_description": {"type": "string", "label": "Condition description", "value": draft.get("condition_description") or ""},
            "shipping_profile": {"type": "string", "label": "Shipping policy", "value": draft.get("shipping_profile") or "", "options": deepcopy(context.get("fulfillment_policies") or [])},
            "return_policy_id": {"type": "string", "label": "Return policy", "value": draft.get("return_policy_id") or "", "options": deepcopy(context.get("return_policies") or [])},
            "store_category_id": {"type": "string", "label": "Store category", "value": draft.get("store_category_id") or "", "options": deepcopy(context.get("store_categories") or [])},
            "secondary_store_category_id": {
                "type": "string",
                "nullable": True,
                "label": "Secondary store category",
                "value": draft.get("secondary_store_category_id"),
                "options": deepcopy(context.get("store_categories") or []),
            },
            "best_offer_enabled": {"type": "boolean", "nullable": True, "label": "Best Offer", "value": draft.get("best_offer_enabled")},
            "best_offer_auto_accept_price": {"type": "number", "nullable": True, "label": "Best Offer auto-accept", "value": draft.get("best_offer_auto_accept_price")},
            "best_offer_auto_decline_price": {"type": "number", "nullable": True, "label": "Best Offer auto-decline", "value": draft.get("best_offer_auto_decline_price")},
            "item_specifics": {"type": "string-map", "label": "Item specifics", "value": deepcopy(specifics)},
        },
        "category": {
            "value": category_id,
            "label": context.get("category_name") or draft.get("category_name") or item.get("ebay_category_name"),
            "required": True,
        },
        "condition": {
            "value": current_condition,
            "label": draft.get("condition_label") or draft.get("condition_description"),
            "required": listing_condition_required,
            "valid": required_flag_valid and (valid_condition if current_condition else not listing_condition_required),
            "options": display_conditions,
        },
        "aspects": aspects,
        "defaults": {
            "fulfillment_policy_id": context.get("fulfillment_policy_id"),
            "store_category": deepcopy(context.get("store_category")),
        },
        "validation_messages": validation_messages,
    }
    if policy_fields_present:
        field_schema["condition"]["required_flag_valid"] = required_flag_valid
    if record_condition_projection:
        field_schema["item_fields"].update({
            "condition": {
                "type": "string",
                "label": "Inventory condition",
                "value": published_record_condition,
                "options": record_condition_options,
                "enum": record_conditions,
                "case_insensitive_enum": True,
                "case_insensitive_options": True,
            },
            "category_group": {"type": "string", "label": "TGW category group", "value": item.get("category_group") or "", "options": group_options},
            "size_class": {"type": "string", "label": "Size class", "value": item.get("size_class") or ""},
            "ai_hint": {"type": "string", "label": "AI hint", "value": item.get("ai_hint") or ""},
        })
        field_schema["category_groups"] = deepcopy(group_options)
        field_schema["record_condition_vocabulary"] = (
            configured_record_conditions
        )
        field_schema["record_condition_vocabulary_drift"] = (
            record_condition_drift
        )
    if context.get("record_attribute_vocabulary"):
        field_schema["record_attribute_vocabulary"] = deepcopy(context["record_attribute_vocabulary"])
    command_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "condition_enum": {
                "type": "string",
                "enum": ([""] if not listing_condition_required else []) + [option["value"] for option in conditions],
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
    save_inventory_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item_fields": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    name: {
                        key: value
                        for key, value in descriptor.items()
                        if key
                        in {
                            "type",
                            "nullable",
                            "enum",
                            "case_insensitive_enum",
                        }
                    }
                    for name, descriptor in field_schema["item_fields"].items()
                },
            },
        },
    }
    save_listing_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "draft_listing": {
                "type": "object",
                "additionalProperties": False,
                "properties": {name: {key: value for key, value in descriptor.items() if key in {"type", "nullable"}} for name, descriptor in field_schema["listing_fields"].items()}
                | {
                    # Local editing may explicitly clear a stale/illegal
                    # condition. Provider commands retain the narrower enum
                    # above and remain held until a required value is remapped.
                    "condition_enum": {"type": "string", "enum": ([""] if policy_fields_present else []) + [option["value"] for option in conditions]},
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
                "id": "save-inventory",
                "enabled": projected_command("save-draft")[0],
                "reason": projected_command("save-draft")[1],
                "authority_scope": "local-item-mutation",
                "input_schema": save_inventory_input_schema,
            },
            {
                "id": "save-listing-draft",
                "enabled": projected_command("save-draft")[0],
                "reason": projected_command("save-draft")[1],
                "authority_scope": "local-item-mutation",
                "input_schema": save_listing_input_schema,
            },
            {
                "id": "list-item",
                "enabled": projected_command("list-item")[0] and not (policy_fields_present and validation_messages),
                "reason": (" ".join(validation_messages) if projected_command("list-item")[0] and policy_fields_present and validation_messages else projected_command("list-item")[1]),
                "authority_scope": "publication",
                "input_schema": command_input_schema,
            },
            {
                "id": "update-item",
                "enabled": projected_command("update-item")[0] and not (policy_fields_present and validation_messages),
                "reason": (" ".join(validation_messages) if projected_command("update-item")[0] and policy_fields_present and validation_messages else projected_command("update-item")[1]),
                "authority_scope": "update-restage",
                "input_schema": command_input_schema,
            },
        ),
    )


def web_adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Web adapter: render and submit only the shared API view."""
    return adapter_view(published)
