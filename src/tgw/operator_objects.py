"""Published operator objects and deliberately thin client views.

This is a transport seam, not a workflow evaluator or command dispatcher.
Callers must pass the current server-owned item, listing, workflow, and field
views.  The assembler only verifies their shared identity/generation and
emits one stable object which every client can render.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, quote_plus, urlparse

from tgw import inventory_record
from tgw.ebay.draft_specifics import get_ebay_aspects
from tgw.item_mutation import item_generation

OPERATOR_OBJECT_SCHEMA = "tgw-operator-object/v1"
ADAPTER_VIEW_SCHEMA = "tgw-operator-adapter-view/v1"


def _observed_datetime(value: Any) -> datetime | None:
    """Parse one timezone-qualified observation without inventing ordering."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        observed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return observed if observed.tzinfo is not None else None


def _active_pipeline_error(item: Mapping[str, Any]) -> Any:
    """Return only a pipeline finding not superseded by provider success.

    A successful eBay stage is stronger, later evidence than an earlier
    ``ebay_stage`` rejection.  The raw rejection remains in the canonical item
    for audit/history, but it must not continue to publish red field attention
    or a failed provider state after that later success.  Missing, malformed,
    equal, or out-of-order timestamps fail closed and keep the finding active.
    """
    pipeline_error = item.get("pipeline_error")
    if not isinstance(pipeline_error, Mapping) or not pipeline_error:
        return pipeline_error
    is_rejection = pipeline_error.get("code") == "ebay_rejected" or bool(
        pipeline_error.get("error") and not pipeline_error.get("code")
    )
    source = str(
        pipeline_error.get("source") or pipeline_error.get("worker") or ""
    ).strip()
    if not is_rejection or source != "ebay_stage":
        return pipeline_error
    rejected_at = _observed_datetime(
        pipeline_error.get("ts") or pipeline_error.get("at")
    )
    if rejected_at is None:
        return pipeline_error
    successful_stage_times = [
        observed
        for observed in (
            _observed_datetime(
                (item.get("ebay_offer") or {}).get("staged_at")
                if isinstance(item.get("ebay_offer"), Mapping)
                else None
            ),
            _observed_datetime(
                (item.get("ebay_submitted") or {}).get("staged_at")
                if isinstance(item.get("ebay_submitted"), Mapping)
                else None
            ),
        )
        if observed is not None
    ]
    if successful_stage_times and max(successful_stage_times) > rejected_at:
        return None
    return pipeline_error

_COMMAND_SPECS: dict[str, dict[str, Any]] = {
    "save-inventory": {
        "label": "Save Inventory",
        "authority_scope": "local-item-mutation",
        "value_source": "editor",
        "value_semantics": "sparse-patch",
        "views": ["inventory"],
        "group": "inventory",
        "tone": "primary",
        "confirmation": None,
    },
    "save-listing-draft": {
        "label": "Save Listing Draft",
        "authority_scope": "local-item-mutation",
        "value_source": "editor",
        "value_semantics": "sparse-patch",
        "views": ["listing"],
        "group": "listing-draft",
        "tone": "primary",
        "confirmation": None,
    },
    "list-item": {
        "label": "List Item",
        "authority_scope": "publication",
        # Listing is an atomic "save the visible editor, then request the
        # governed provider workflow" operation.  Requiring a separate Save
        # Draft click made the primary button capable of publishing stale
        # values from fields that were still visibly edited in the client.
        "value_source": "editor",
        "views": ["listing"],
        "group": "ebay-listing",
        "tone": "primary",
        "confirmation": "Publish this item through the current governed listing workflow?",
    },
    "update-item": {
        "label": "Update Item",
        "authority_scope": "update-restage",
        "value_source": "editor",
        "views": ["listing"],
        "group": "ebay-listing",
        "tone": "primary",
        "confirmation": "Push the current draft to the existing eBay item?",
    },
    "reidentify": {
        "label": "Reidentify",
        "authority_scope": "local-workflow-request",
        "value_source": "none",
        "views": ["listing"],
        "group": "listing-preparation",
        "tone": "secondary",
        "confirmation": "Regenerate identification, the listing draft, and pricing guidance from the current photos?",
    },
    "resync-photos": {
        "label": "Resync Photos",
        "authority_scope": "update-restage",
        "value_source": "none",
        "views": ["listing"],
        "group": "ebay-listing",
        "tone": "secondary",
        "confirmation": "Synchronize the canonical local photo set to the provider?",
    },
    "sync-from-ebay": {
        "label": "Refresh from eBay",
        "authority_scope": "provider-observation",
        "value_source": "none",
        "views": ["listing", "evidence"],
        "group": "ebay-listing",
        "tone": "secondary",
        "confirmation": None,
    },
    "reset-draft-from-live": {
        "label": "Reset Draft from Live",
        "authority_scope": "local-item-mutation",
        "value_source": "none",
        "views": ["listing"],
        "group": "listing-preparation",
        "tone": "warning",
        "confirmation": "Replace the local listing draft with the current live eBay values?",
    },
    "reorder-photos": {
        "label": "Save Photo Order",
        "authority_scope": "local-item-mutation",
        "value_source": "media-order",
        "views": ["listing"],
        "group": "photos",
        "tone": "secondary",
        "confirmation": None,
    },
    "reprice-item": {
        "label": "Run AI Pricer",
        "authority_scope": "local-workflow-request",
        "value_source": "pricing",
        "views": ["listing"],
        "group": "pricing",
        "tone": "secondary",
        "confirmation": None,
    },
    "mark-sold": {
        "label": "Mark Sold",
        "authority_scope": "local-item-lifecycle",
        "value_source": "none",
        "views": ["inventory", "listing"],
        "group": "inventory-lifecycle",
        "tone": "warning",
        "confirmation": "Mark one unit sold in the local inventory record? This does not end a live provider listing.",
    },
    "archive-item": {
        "label": "Archive",
        "authority_scope": "local-item-lifecycle",
        "value_source": "none",
        "views": ["inventory", "listing"],
        "group": "inventory-lifecycle",
        "tone": "warning",
        "confirmation": "Archive this local item? This does not end a live provider listing.",
    },
    "delete-item": {
        "label": "Delete",
        "authority_scope": "local-item-lifecycle",
        "value_source": "none",
        "views": ["inventory", "listing"],
        "group": "inventory-lifecycle",
        "tone": "danger",
        "confirmation": "Soft-delete this local item? The item data remains recoverable, and this does not end a live provider listing.",
    },
    "end-listing": {
        "label": "End Listing",
        "authority_scope": "publication-withdrawal",
        "value_source": "none",
        "views": ["listing"],
        "group": "ebay-listing",
        "tone": "danger",
        "confirmation": "End the active provider listing? This is an external marketplace action.",
    },
}


class OperatorObjectBindingError(ValueError):
    """A supplied component is not a coherent canonical object view."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OperatorObjectBindingError(f"{name} must be an object")
    return value


def _first_present(
    value: Mapping[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    """Return the first present field, preserving an intentional blank."""
    for key in keys:
        if key in value:
            return value[key]
    return default


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
    if command_id not in _COMMAND_SPECS:
        raise OperatorObjectBindingError("unrecognized operator command")
    spec = _COMMAND_SPECS[command_id]
    enabled = command.get("enabled")
    if not isinstance(enabled, bool):
        raise OperatorObjectBindingError("command.enabled must be boolean")
    reason = command.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason):
        raise OperatorObjectBindingError("command.reason must be a non-empty string when present")

    # This is declarative metadata only.  Issuing the grant remains in the
    # server command handler; a browser or Flutter client cannot replace it.
    expected_scope = spec["authority_scope"]
    if command.get("authority_scope") != expected_scope:
        raise OperatorObjectBindingError("command authority scope does not match command id")
    expected_value_source = spec["value_source"]
    if command.get("value_source", expected_value_source) != expected_value_source:
        raise OperatorObjectBindingError("command value source does not match command id")
    command_views = command.get("views")
    if command_views is None:
        command_views = spec["views"]
    if (
        not isinstance(command_views, Sequence)
        or isinstance(command_views, (str, bytes))
        or not command_views
        or not all(view in {"inventory", "listing", "evidence"} for view in command_views)
    ):
        raise OperatorObjectBindingError("command views are invalid")
    return {
        "id": command_id,
        "label": spec["label"],
        "enabled": enabled,
        "reason": reason,
        "authority_scope": expected_scope,
        "input_schema": deepcopy(command.get("input_schema", {})),
        "refresh_target": "current-object",
        "value_source": expected_value_source,
        "value_semantics": spec.get("value_semantics", "complete-value"),
        "views": list(command_views),
        "group": spec["group"],
        "tone": spec["tone"],
        "confirmation": spec["confirmation"],
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
    if field_type == "string-list":
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or not all(isinstance(item, str) and item for item in value)
            or len(value) != len(set(value))
        ):
            raise OperatorObjectBindingError(f"{label} must contain unique non-empty strings")
        return list(value)
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
    presentation: Mapping[str, Any] | None = None,
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
        "presentation": deepcopy(dict(presentation or {})),
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
        "presentation": deepcopy(published.get("presentation", {})),
    }


def _price_query_from_comp_urls(price_comps: Mapping[str, Any]) -> str:
    """Recover one unambiguous legacy Browse query from stored comp URLs.

    New observations persist ``price_comps.query`` directly.  Older comp
    records often retain eBay's ``_skw``/``_nkw`` query in every result URL;
    use it only when all available URL evidence agrees.  This is evidence
    recovery, never a title-based pricing guess.
    """
    items = price_comps.get("items")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        return ""
    queries: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            query_params = parse_qs(urlparse(url).query)
        except ValueError:
            continue
        for parameter in ("_skw", "_nkw"):
            values = query_params.get(parameter, ())
            if values and str(values[0]).strip():
                queries.add(str(values[0]).strip())
                break
    return next(iter(queries)) if len(queries) == 1 else ""


def _item_presentation(
    *,
    item: Mapping[str, Any],
    category_context: Mapping[str, Any],
    workflow_state: str,
    workflow_reasons: Sequence[str],
    workflow_details: Mapping[str, Any],
    workflow_evidence: Sequence[str],
    validation_messages: Sequence[str],
    field_attention: Sequence[Mapping[str, Any]],
    general_attention: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Publish a human-oriented item view without putting policy in clients."""

    draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), Mapping) else {}
    offer = item.get("ebay_offer") if isinstance(item.get("ebay_offer"), Mapping) else {}
    listing = item.get("ebay_listing") if isinstance(item.get("ebay_listing"), Mapping) else {}

    def present(value: Any) -> bool:
        return value is not None and value != "" and value != [] and value != {}

    def row(
        label: str,
        value: Any,
        *,
        value_format: str = "text",
        href: str | None = None,
        tone: str | None = None,
    ) -> dict[str, Any] | None:
        if not present(value):
            return None
        result: dict[str, Any] = {"label": label, "value": deepcopy(value), "format": value_format}
        if href:
            result["href"] = href
        if tone:
            result["tone"] = tone
        return result

    def rows(*values: dict[str, Any] | None) -> list[dict[str, Any]]:
        return [value for value in values if value is not None]

    def property_section(
        section_id: str,
        title: str,
        section_rows: Sequence[dict[str, Any] | None],
        *,
        description: str | None = None,
        collapsed: bool = False,
    ) -> dict[str, Any] | None:
        visible = [entry for entry in section_rows if entry is not None]
        if not visible:
            return None
        section: dict[str, Any] = {
            "id": section_id,
            "title": title,
            "kind": "properties",
            "rows": visible,
            "collapsed": collapsed,
        }
        if description:
            section["description"] = description
        return section

    def mapping_rows(value: Any) -> list[dict[str, Any] | None]:
        if not isinstance(value, Mapping):
            return []
        return [row(str(key), nested) for key, nested in sorted(value.items(), key=lambda pair: str(pair[0]).casefold())]

    def table_section(
        section_id: str,
        title: str,
        value: Any,
        columns: Sequence[tuple[str, str, str]],
        *,
        description: str | None = None,
        collapsed: bool = False,
    ) -> dict[str, Any] | None:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not value:
            return None
        table_rows = [deepcopy(dict(entry)) for entry in value if isinstance(entry, Mapping)]
        if not table_rows:
            return None
        section: dict[str, Any] = {
            "id": section_id,
            "title": title,
            "kind": "table",
            "columns": [
                {"key": key, "label": label, "format": value_format}
                for key, label, value_format in columns
            ],
            "rows": table_rows,
            "collapsed": collapsed,
        }
        if description:
            section["description"] = description
        return section

    def tree_section(section_id: str, title: str, value: Any) -> dict[str, Any] | None:
        if not present(value):
            return None
        return {
            "id": section_id,
            "title": title,
            "kind": "tree",
            "value": deepcopy(value),
            "collapsed": True,
        }

    price = offer.get("price")
    if price is None:
        price = draft.get("price")
    if price is None:
        price = item.get("price")
    quantity = item.get("quantity") if item.get("quantity") is not None else item.get("qty")
    if quantity is None:
        quantity = draft.get("quantity")
    inventory_status = item.get("status") or item.get("#STATUS")

    badges = []
    for label, value, tone in (
        ("Inventory", item.get("status"), "neutral"),
        ("Workflow", workflow_state, "accent"),
        ("Draft", item.get("draft_listing_state"), "neutral"),
        ("Listing", listing.get("status") or listing.get("listing_status"), "success"),
        ("Offer", offer.get("status"), "neutral"),
    ):
        if present(value):
            badges.append({"label": label, "value": value, "tone": tone})

    alerts = []
    pipeline_error = _active_pipeline_error(item)
    if isinstance(pipeline_error, Mapping) and pipeline_error:
        legacy_rejection = pipeline_error.get("error")
        code = pipeline_error.get("code")
        source = pipeline_error.get("source") or pipeline_error.get("worker") or "pipeline"
        detail = pipeline_error.get("detail") or legacy_rejection or code or "The item pipeline stopped."
        alerts.append({
            "level": "error",
            "title": (
                f"eBay rejected {source}"
                if legacy_rejection or code == "ebay_rejected"
                else f"{source} stopped"
            ),
            "message": str(detail),
            "details": rows(
                row("Code", code, value_format="code"),
                row("Field", pipeline_error.get("field"), value_format="code"),
                row("Source", source, value_format="code"),
                row("When", pipeline_error.get("ts") or pipeline_error.get("at"), value_format="datetime"),
            ),
        })
    elif pipeline_error:
        alerts.append({
            "level": "error",
            "title": "Item pipeline stopped",
            "message": str(pipeline_error),
            "details": [],
        })
    review_block = item.get("review_block")
    if isinstance(review_block, Mapping) and not review_block.get("ready"):
        alerts.append({
            "level": "error",
            "title": "Blocked in review",
            "message": review_block.get("error") or review_block.get("reason_code") or "Operator review is required.",
            "details": rows(
                row("Stage", review_block.get("stage")),
                row("Reason", review_block.get("reason_code")),
                row("Suggestion", review_block.get("suggestion")),
                row("Flagged", review_block.get("flagged_at"), value_format="datetime"),
            ),
        })
    if draft.get("offline_draft") is True:
        alerts.append({
            "level": "warning",
            "title": "Offline draft",
            "message": "This draft was prepared without live taxonomy confirmation.",
            "details": [],
        })
    quality = draft.get("quality") if isinstance(draft.get("quality"), Mapping) else {}
    flags = quality.get("flags") if isinstance(quality.get("flags"), list) else []
    if flags:
        alerts.append({
            "level": "warning",
            "title": "Listing quality needs attention",
            "message": "; ".join(str(flag) for flag in flags),
            "details": rows(row("Quality score", quality.get("score"))),
        })
    if validation_messages:
        alerts.append({
            "level": "warning",
            "title": "Listing validation",
            "message": "; ".join(validation_messages),
            "details": [],
        })
    if workflow_reasons:
        alerts.append({
            "level": "info",
            "title": "Current workflow disposition",
            "message": "; ".join(workflow_reasons),
            "details": [],
        })
    for alert in alerts:
        alert["domain"] = "listing-provider"
        alert["views"] = ["listing"]

    links = []
    listing_url = listing.get("listing_url")
    if isinstance(listing_url, str) and listing_url:
        links.append({"label": "View on eBay", "href": listing_url, "external": True})
    listing_id = listing.get("listing_id")
    if isinstance(listing_id, str) and listing_id:
        links.append({
            "label": "Seller Hub",
            "href": f"https://www.ebay.com/sh/lst/active?keyword={listing_id}",
            "external": True,
        })

    sections: list[dict[str, Any]] = []
    price_comps = offer.get("price_comps") if isinstance(offer.get("price_comps"), Mapping) else {}
    fingerprint_rows = [
        {
            "condition": fingerprint.get("condition_id"),
            "result": fingerprint.get("result"),
            "reasons": list(fingerprint.get("reasons") or []),
            "evidence_count": len(fingerprint.get("evidence") or []),
        }
        for fingerprint in workflow_details.get("fingerprints") or []
        if isinstance(fingerprint, Mapping)
    ]
    attempt_rows = [
        {
            "treatment": attempt.get("treatment_id") or attempt.get("queue_name"),
            "state": attempt.get("state"),
            "job_id": attempt.get("job_id"),
            "not_before": attempt.get("not_before"),
        }
        for attempt in workflow_details.get("attempts") or []
        if isinstance(attempt, Mapping)
    ]
    action_rows = [
        {
            "treatment": action.get("treatment_id"),
            "action": action.get("action"),
            "effect_class": action.get("effect_class"),
            "reasons": list(action.get("reasons") or []),
        }
        for action in workflow_details.get("legal_actions") or []
        if isinstance(action, Mapping)
    ]
    wait_rows = [
        {
            "treatment": wait.get("treatment_id"),
            "reasons": list(wait.get("reasons") or []),
        }
        for wait in workflow_details.get("waiting_treatments") or []
        if isinstance(wait, Mapping)
    ]
    candidates = [
        property_section(
            "inventory-overview",
            "Inventory overview",
            rows(
                row("SKU", item.get("sku"), value_format="code"),
                row("Status", item.get("status")),
                row("Location", item.get("location")),
                row("Quantity", quantity),
                row("Category", item.get("category")),
                row("Condition", item.get("condition")),
                row("Country of manufacture", item.get("country_of_manufacture")),
                row("Template", item.get("TEMPLATE"), value_format="code"),
                row("Baseline", item.get("baseline_at"), value_format="datetime"),
                row("Description", item.get("description"), value_format="multiline"),
            ),
        ),
        property_section(
            "inventory-attributes",
            "Inventory attributes",
            mapping_rows(inventory_record.get_inventory_fields(dict(item))),
        ),
        property_section(
            "listing-draft-summary",
            "Listing draft",
            rows(
                row("Title", draft.get("title")),
                row(
                    "Description",
                    _first_present(draft, "description", "listing_description"),
                    value_format="multiline",
                ),
                row("Price", draft.get("price"), value_format="money"),
                row("Quantity", draft.get("quantity")),
                row("Format", draft.get("format")),
                row("Category", draft.get("category_name")),
                row("Category ID", draft.get("category_id"), value_format="code"),
                row("Category agreement", draft.get("category_agreement")),
                row(
                    "Condition",
                    _first_present(draft, "condition_label", "condition"),
                ),
                row("Condition description", draft.get("condition_description"), value_format="multiline"),
                row("Store category", draft.get("store_category_name")),
                row("Store category ID", draft.get("store_category_id"), value_format="code"),
                row("Shipping policy", draft.get("shipping_profile"), value_format="code"),
                row("Return policy", draft.get("return_policy_id"), value_format="code"),
                row("Required aspects", f"{draft.get('aspects_required_filled', 0)} / {draft.get('aspects_required_total', 0)}"),
                row("Recommended aspects", f"{draft.get('aspects_recommended_filled', 0)} / {draft.get('aspects_recommended_total', 0)}"),
                row("SEO caption", draft.get("seo_caption"), value_format="multiline"),
                row("Title flags", draft.get("title_flags"), value_format="list"),
                row("Alt text", draft.get("alt_text"), value_format="list"),
            ),
        ),
        property_section(
            "item-specifics",
            "Item specifics",
            mapping_rows(get_ebay_aspects(dict(item))),
        ),
        property_section(
            "listing-provider",
            "eBay listing",
            rows(
                row("Listing ID", listing.get("listing_id"), value_format="code"),
                row("Status", listing.get("status") or listing.get("listing_status")),
                row("Live price", listing.get("live_price"), value_format="money"),
                row("Published", listing.get("published_at"), value_format="datetime"),
                row("Ended", listing.get("ended_at"), value_format="datetime"),
                row("Last synchronized", listing.get("synced_at"), value_format="datetime"),
                row("API", listing.get("api")),
                row("Photo verification", listing.get("photo_verify")),
            ),
        ),
        property_section(
            "offer-provider",
            "eBay offer",
            rows(
                row("Offer ID", offer.get("offer_id"), value_format="code"),
                row("Status", offer.get("status")),
                row("Price", offer.get("price"), value_format="money"),
                row("Staged price", offer.get("staged_price"), value_format="money"),
                row("Target price", offer.get("target_price"), value_format="money"),
                row("Quantity", offer.get("quantity")),
                row("Category ID", offer.get("category_id"), value_format="code"),
                row("Fulfillment policy", offer.get("fulfillment_policy_id"), value_format="code"),
                row("Price source", offer.get("price_source")),
                row("Priced", offer.get("priced_at"), value_format="datetime"),
                row("Staged", offer.get("staged_at"), value_format="datetime"),
                row("Published", offer.get("published_at"), value_format="datetime"),
            ),
        ),
        property_section(
            "pricing-comps",
            "Pricing context",
            rows(
                row("Confidence", price_comps.get("confidence")),
                row("Comparable listings", price_comps.get("count")),
                row("Minimum", price_comps.get("min"), value_format="money"),
                row("25th percentile", price_comps.get("p25"), value_format="money"),
                row("Median", price_comps.get("median"), value_format="money"),
                row("75th percentile", price_comps.get("p75"), value_format="money"),
                row("Maximum", price_comps.get("max"), value_format="money"),
                row("Outliers", price_comps.get("outlier_count")),
            ),
            description="Comparable-listing statistics used by the server pricing projection.",
        ),
        table_section(
            "pricing-comparable-listings",
            "Comparable listings",
            price_comps.get("items"),
            (
                ("title", "Title", "text"),
                ("price", "Price", "money"),
                ("condition", "Condition", "text"),
                ("item_id", "Item ID", "code"),
                ("url", "Source", "url"),
                ("outlier", "Outlier", "boolean"),
                ("llm_dropped", "Excluded", "boolean"),
                ("llm_reason", "Reason", "text"),
            ),
            collapsed=True,
        ),
        table_section(
            "price-history",
            "Price history",
            item.get("price_history"),
            (
                ("ts", "When", "datetime"),
                ("price", "Price", "money"),
                ("previous_price", "Previous", "money"),
                ("stage", "Stage", "text"),
                ("label", "Label", "text"),
                ("source", "Source", "text"),
            ),
        ),
        table_section(
            "reprice-schedule",
            "Reprice schedule",
            item.get("reprice_schedule"),
            (
                ("stage", "Stage", "text"),
                ("price", "Price", "money"),
                ("due_at", "Due", "datetime"),
                ("done_at", "Completed", "datetime"),
                ("status", "Status", "text"),
            ),
        ),
        property_section(
            "product-lookup",
            "Product lookup",
            mapping_rows(item.get("product_lookup")),
        ),
        table_section(
            "identification-history",
            "Identification history",
            item.get("identification_history"),
            (
                ("ts", "When", "datetime"),
                ("source", "Source", "text"),
                ("model", "Model", "text"),
                ("title", "Title", "text"),
                ("category", "Category", "text"),
                ("confidence", "Confidence", "text"),
            ),
            collapsed=True,
        ),
        property_section(
            "workflow-summary",
            "Workflow",
            rows(
                row("State", workflow_state),
                row("Reasons", list(workflow_reasons), value_format="list"),
                row("Evidence records", len(workflow_evidence)),
                row("Graph", workflow_details.get("graph_id"), value_format="code"),
                row("Attempts", len(workflow_details.get("attempts") or [])),
                row("Active attempts", len(workflow_details.get("active_attempts") or [])),
            ),
        ),
        table_section(
            "workflow-fingerprints",
            "Workflow conditions",
            fingerprint_rows,
            (
                ("condition", "Condition", "code"),
                ("result", "Result", "text"),
                ("reasons", "Reasons", "list"),
                ("evidence_count", "Evidence", "text"),
            ),
            collapsed=True,
        ),
        table_section(
            "workflow-actions",
            "Legal workflow actions",
            action_rows,
            (
                ("treatment", "Treatment", "code"),
                ("action", "Action", "text"),
                ("effect_class", "Effect", "text"),
                ("reasons", "Reasons", "list"),
            ),
            collapsed=True,
        ),
        table_section(
            "workflow-waits",
            "Workflow waits",
            wait_rows,
            (
                ("treatment", "Treatment", "code"),
                ("reasons", "Reasons", "list"),
            ),
            collapsed=True,
        ),
        table_section(
            "workflow-attempts",
            "Attempt history",
            attempt_rows,
            (
                ("treatment", "Treatment", "code"),
                ("state", "State", "text"),
                ("job_id", "Job", "code"),
                ("not_before", "Not before", "datetime"),
            ),
            collapsed=True,
        ),
        tree_section("alt-text-evidence", "Alt-text evidence", item.get("alt_text_results")),
        tree_section("provider-live-snapshot", "Provider live snapshot", item.get("ebay_live")),
        tree_section("provider-submitted-snapshot", "Provider submitted snapshot", item.get("ebay_submitted")),
    ]
    sections.extend(section for section in candidates if section is not None)

    category_pricing = (
        category_context.get("pricing")
        if isinstance(category_context.get("pricing"), Mapping)
        else {}
    )
    pricing_context_rows = rows(
        row("Current price", price, value_format="money"),
        row("AI suggestion", offer.get("suggested_price"), value_format="money"),
        row("Target price", offer.get("target_price"), value_format="money"),
        row("Category floor", category_pricing.get("floor"), value_format="money"),
        row("Typical used", category_pricing.get("typical_used"), value_format="money"),
        row("Typical new", category_pricing.get("typical_new"), value_format="money"),
        row("Comparable median", price_comps.get("median"), value_format="money"),
        row(
            "Comparable range",
            (
                f"{price_comps.get('p25')} – {price_comps.get('p75')}"
                if present(price_comps.get("p25")) and present(price_comps.get("p75"))
                else None
            ),
        ),
        row("Confidence", price_comps.get("confidence")),
        row("Price source", offer.get("price_source")),
        row("Priced", offer.get("priced_at"), value_format="datetime"),
    )
    requested_search_terms = str(item.get("search_terms") or "").strip()
    recorded_search_terms = str(price_comps.get("query") or "").strip()
    recovered_search_terms = (
        _price_query_from_comp_urls(price_comps)
        if not recorded_search_terms
        else ""
    )
    observed_search_terms = recorded_search_terms or recovered_search_terms
    pricing_request_pending = item.get("ai_reprice_requested") is True
    search_terms = (
        requested_search_terms
        if pricing_request_pending and requested_search_terms
        else observed_search_terms or requested_search_terms
    )
    encoded_terms = quote_plus(search_terms)
    research_links = [
        {
            "id": "ebay-sold",
            "label": "eBay Sold",
            "href": f"https://www.ebay.com/sch/i.html?_nkw={encoded_terms}&LH_Complete=1&LH_Sold=1",
            "external": True,
        },
        {
            "id": "ebay-active",
            "label": "eBay Active",
            "href": f"https://www.ebay.com/sch/i.html?_nkw={encoded_terms}",
            "external": True,
        },
        {
            "id": "terapeak",
            "label": "Terapeak",
            "href": f"https://www.ebay.com/sh/research?marketplace=EBAY-US&keywords={encoded_terms}",
            "external": True,
        },
    ]

    pipeline_reason = ""
    pipeline_status_evidence: list[str] = []
    if isinstance(pipeline_error, Mapping) and pipeline_error:
        pipeline_reason = str(
            pipeline_error.get("detail")
            or pipeline_error.get("error")
            or pipeline_error.get("code")
            or "The item pipeline stopped."
        )
        pipeline_status_evidence = [
            entry
            for entry in (
                (
                    f"provider-error:{pipeline_error.get('code') or pipeline_error.get('error')}"
                    if pipeline_error.get("code") or pipeline_error.get("error")
                    else ""
                ),
                (
                    f"source:{pipeline_error.get('source') or pipeline_error.get('worker')}"
                    if pipeline_error.get("source") or pipeline_error.get("worker")
                    else ""
                ),
                (
                    f"observed-at:{pipeline_error.get('ts') or pipeline_error.get('at')}"
                    if pipeline_error.get("ts") or pipeline_error.get("at")
                    else ""
                ),
            )
            if entry
        ]
    elif pipeline_error:
        pipeline_reason = str(pipeline_error)
    provider_state = (
        "Failed"
        if pipeline_reason
        else str(
            listing.get("status")
            or listing.get("listing_status")
            or offer.get("status")
            or "Not staged"
        )
    )
    workflow_tone = (
        "error"
        if workflow_state in {"held", "reconciliation_required"}
        else "warning"
        if workflow_state in {"ready", "staged"}
        else "success"
        if workflow_state == "published"
        else "accent"
    )
    provider_tone = (
        "error"
        if pipeline_reason
        else "success"
        if provider_state.strip().upper() in {"ACTIVE", "PUBLISHED"}
        else "warning"
        if provider_state.strip().upper() in {"ENDED", "WITHDRAWN", "INACTIVE"}
        else "neutral"
    )
    status_summary = {
        "inventory": {
            "label": "TGW",
            "state": inventory_status or "Unknown",
            "tone": "accent",
            "reasons": [],
            "evidence": [],
        },
        "workflow": {
            "label": "Workflow",
            "state": workflow_state,
            "tone": workflow_tone,
            "reasons": list(workflow_reasons),
            "evidence": list(workflow_evidence),
        },
        "listing": {
            "label": "eBay",
            "state": provider_state,
            "tone": provider_tone,
            "reasons": [pipeline_reason] if pipeline_reason else [],
            "evidence": [
                entry
                for entry in (
                    f"listing-id:{listing.get('listing_id')}"
                    if listing.get("listing_id")
                    else "",
                    f"offer-id:{offer.get('offer_id')}"
                    if offer.get("offer_id")
                    else "",
                    *pipeline_status_evidence,
                )
                if entry
            ],
        },
    }
    attention_items = [
        deepcopy(dict(entry)) for entry in (*field_attention, *general_attention)
    ]
    existing_attention_summaries = {
        str(entry.get("summary"))
        for entry in attention_items
        if entry.get("summary")
    }
    for alert in alerts:
        message = str(alert.get("message") or "")
        if not message or message in existing_attention_summaries:
            continue
        attention_items.append(
            {
                "field": None,
                "label": str(alert.get("title") or "Listing attention"),
                "level": str(alert.get("level") or "info"),
                "summary": message,
                "reasons": [{"code": "published-alert", "message": message}],
                "evidence": [
                    f"{detail.get('label')}: {detail.get('value')}"
                    for detail in alert.get("details", ())
                    if isinstance(detail, Mapping) and present(detail.get("value"))
                ],
                "actions": [],
            }
        )
    if workflow_reasons:
        attention_items.append(
            {
                "field": None,
                "label": "Workflow",
                "level": (
                    "error"
                    if workflow_state in {"held", "reconciliation_required"}
                    else "info"
                ),
                "summary": "; ".join(workflow_reasons),
                "reasons": [
                    {"code": "workflow-disposition", "message": reason}
                    for reason in workflow_reasons
                ],
                "evidence": list(workflow_evidence),
                "actions": [],
            }
        )
    level_rank = {"info": 0, "warning": 1, "error": 2}
    attention_level = max(
        (str(entry.get("level") or "info") for entry in attention_items),
        key=lambda level: level_rank.get(level, 0),
        default="info",
    )
    attention_evidence: list[str] = []
    attention_actions: list[dict[str, Any]] = []
    for entry in attention_items:
        for evidence_entry in entry.get("evidence", ()):
            if evidence_entry and evidence_entry not in attention_evidence:
                attention_evidence.append(str(evidence_entry))
        for action in entry.get("actions", ()):
            copied = deepcopy(dict(action))
            if copied not in attention_actions:
                attention_actions.append(copied)
    attention = {
        "level": attention_level,
        "title": (
            "Listing requires correction"
            if attention_level == "error"
            else "Listing needs attention"
            if attention_level == "warning"
            else "Current listing workflow"
        ),
        "summary": "; ".join(
            str(entry.get("summary"))
            for entry in attention_items
            if entry.get("summary")
        ),
        "items": attention_items,
        "evidence": attention_evidence,
        "actions": attention_actions,
        "expanded": False,
    } if attention_items else None

    return {
        "schema": "tgw-item-presentation/v1",
        "title": item.get("title") or item.get("sku"),
        "subtitle": item.get("sku"),
        "breadcrumbs": [
            {"label": "Home", "href": "/form/home"},
            {"label": "Inventory", "href": "/form/items"},
        ],
        "price": price,
        "price_format": "money",
        "badges": badges,
        "status_summary": status_summary,
        "attention": attention,
        "alerts": alerts,
        "links": links,
        "sections": sections,
        "header": {
            "lead": {
                "id": "title",
                "label": "Title",
                "value": item.get("title") or draft.get("title"),
                "format": "text",
            },
            "trail": [
                {"id": "sku", "label": "SKU", "value": item.get("sku"), "format": "code"},
                {"id": "location", "label": "Location", "value": item.get("location"), "format": "text"},
                {"id": "quantity", "label": "Qty", "value": quantity, "format": "text"},
            ],
            "status": {
                "id": "status",
                "label": "Status",
                "value": inventory_status,
                "format": "text",
                "tone": "accent",
            },
            "statuses": [
                {
                    "id": key,
                    "label": value["label"],
                    "value": value["state"],
                    "format": "text",
                    "tone": value["tone"],
                    "reasons": deepcopy(value["reasons"]),
                    "evidence": deepcopy(value["evidence"]),
                }
                for key, value in status_summary.items()
            ],
            "links": deepcopy(links),
            "facts": [
                {"id": "sku", "label": "SKU", "value": item.get("sku"), "format": "code"},
                {"id": "title", "label": "Title", "value": item.get("title") or draft.get("title"), "format": "text"},
                {"id": "location", "label": "Location", "value": item.get("location"), "format": "text"},
                {"id": "quantity", "label": "Qty", "value": quantity, "format": "text"},
                {"id": "status", "label": "Status", "value": inventory_status, "format": "text", "tone": "accent"},
            ],
        },
        "pricing_context": {
            "id": "pricing-data",
            "title": "Pricing",
            "rows": pricing_context_rows,
            "details_sections": ["pricing-comps", "pricing-comparable-listings"],
            "details_target": "pricing-data",
            "search_terms": search_terms,
            "search_terms_source": (
                "pending-operator-request"
                if pricing_request_pending and requested_search_terms
                else "pricing-observation"
                if recorded_search_terms
                else "legacy-comp-url"
                if recovered_search_terms
                else "operator-request"
                if requested_search_terms
                else None
            ),
            "requested_search_terms": requested_search_terms,
            "last_successful_search_terms": observed_search_terms,
            "suggested_price": offer.get("suggested_price"),
            "command_id": "reprice-item",
            "research_links": research_links,
            "research_note": "Links open production eBay market research; Sandbox results are not representative.",
        },
        "listing_editor": {
            "id": "listing-editor",
            "category_fields": [
                "category_id",
                "secondary_category_id",
                "store_category_id",
                "secondary_store_category_id",
            ],
            "pricing_fields": ["price", "quantity"],
            "pricing_footer_fields": [
                "best_offer_enabled",
                "best_offer_auto_accept_price",
                "best_offer_auto_decline_price",
            ],
        },
        "action_menus": [
            {
                "id": "listing-primary",
                "label": "Listing actions",
                "views": ["listing"],
                "command_ids": [
                    "save-listing-draft",
                    "list-item",
                    "update-item",
                    "end-listing",
                ],
            },
            {
                "id": "listing-tools",
                "label": "Listing tools",
                "views": ["listing"],
                "command_ids": [
                    "reidentify",
                    "resync-photos",
                    "sync-from-ebay",
                    "reset-draft-from-live",
                    "mark-sold",
                    "archive-item",
                    "delete-item",
                ],
            },
            {
                "id": "inventory-primary",
                "label": "TGW item actions",
                "views": ["inventory"],
                "command_ids": ["save-inventory", "mark-sold"],
            },
            {
                "id": "inventory-more",
                "label": "More item actions",
                "views": ["inventory"],
                "command_ids": ["archive-item", "delete-item"],
            },
        ],
        "data_navigation": [
            {"label": "Listing", "target": "listing-editor"},
            {"label": "Pricing & comps", "target": "pricing-data"},
            {"label": "eBay listing data", "target": "listing-provider"},
            {"label": "Offer data", "target": "offer-provider"},
            {"label": "Price history", "target": "price-history"},
        ],
        "views": [
            {
                "id": "inventory",
                "label": "TGW",
                "layout": "editor",
                "regions": [
                    {"id": "actions-top", "components": ["commands"], "sections": []},
                    {"id": "editor", "components": ["inventory-editor"], "sections": []},
                ],
            },
            {
                "id": "listing",
                "label": "winchestermysterykitchen",
                "layout": "workstation",
                "default": True,
                "regions": [
                    {
                        "id": "alerts",
                        "components": ["attention-banner"],
                        "sections": [],
                    },
                    {"id": "actions-top", "components": ["commands"], "sections": []},
                    {
                        "id": "reference",
                        "components": ["media", "provider-media"],
                        "sections": [],
                    },
                    {
                        "id": "editor",
                        "components": ["data-navigation", "listing-editor"],
                        "sections": [],
                    },
                    {
                        "id": "listing-data",
                        "components": ["pricing-context"],
                        "sections": [
                            "listing-provider",
                            "offer-provider",
                            "price-history",
                            "reprice-schedule",
                        ],
                    },
                    {"id": "actions-bottom", "components": ["commands"], "sections": []},
                ],
            },
            {
                "id": "evidence",
                "label": "Evidence",
                "layout": "document",
                "regions": [
                    {
                        "id": "main",
                        "components": [],
                        "sections": [
                            "workflow-summary",
                            "product-lookup",
                            "identification-history",
                            "alt-text-evidence",
                            "workflow-fingerprints",
                            "workflow-actions",
                            "workflow-waits",
                            "workflow-attempts",
                            "provider-live-snapshot",
                            "provider-submitted-snapshot",
                        ],
                    },
                ],
            },
        ],
    }


def _nested_mapping(value: Mapping[str, Any], *path: str) -> Mapping[str, Any]:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return {}
        current = current.get(key)
    return current if isinstance(current, Mapping) else {}


def _aspect_values(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            rendered = ", ".join(str(entry) for entry in raw if entry not in (None, ""))
        else:
            rendered = "" if raw is None else str(raw)
        result[key] = rendered
    return result


def _live_aspects(item: Mapping[str, Any]) -> dict[str, str]:
    live = item.get("ebay_live") if isinstance(item.get("ebay_live"), Mapping) else {}
    for candidate in (
        _nested_mapping(live, "inventory_item", "product").get("aspects"),
        _nested_mapping(live, "inventoryItem", "product").get("aspects"),
        _nested_mapping(live, "offer", "listing").get("itemSpecifics"),
    ):
        values = _aspect_values(candidate)
        if values:
            return values
    return {}


def _proposed_aspects(item: Mapping[str, Any]) -> dict[str, str]:
    revision = item.get("revision_draft") if isinstance(item.get("revision_draft"), Mapping) else {}
    delta = revision.get("delta") if isinstance(revision.get("delta"), Mapping) else {}
    return _aspect_values(delta.get("item_specifics"))


def _provider_media(item: Mapping[str, Any]) -> dict[str, Any]:
    """Publish the provider's ordered image set without asking clients to mine snapshots."""
    live = item.get("ebay_live") if isinstance(item.get("ebay_live"), Mapping) else {}
    draft = item.get("draft_listing") if isinstance(item.get("draft_listing"), Mapping) else {}
    candidates: tuple[tuple[str, Any], ...] = (
        ("ebay-live", _nested_mapping(live, "inventory_item", "product").get("imageUrls")),
        ("ebay-live", _nested_mapping(live, "inventoryItem", "product").get("imageUrls")),
        ("ebay-live", _nested_mapping(live, "offer", "listing").get("imageUrls")),
        ("listing-draft", draft.get("imageUrls")),
        ("eps", item.get("ebay_photos")),
    )
    source = "none"
    urls: list[str] = []
    for candidate_source, raw in candidates:
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            continue
        candidate_urls = []
        for entry in raw:
            url = entry.get("url") if isinstance(entry, Mapping) else entry
            if isinstance(url, str) and url.strip() and url not in candidate_urls:
                candidate_urls.append(url)
        if candidate_urls:
            source = candidate_source
            urls = candidate_urls
            break
    items = [
        {
            "kind": "image",
            "name": url.rsplit("/", 1)[-1].split("?", 1)[0] or f"eBay photo {position + 1}",
            "url": url,
            "position": position,
            "primary": False,
        }
        for position, url in enumerate(urls)
    ]
    return {
        "source": source,
        "status": "ready" if items else "empty",
        "count": len(items),
        "items": items,
    }


def _selector_options(
    raw_options: Any,
    *,
    current_value: Any = None,
    current_label: Any = None,
) -> list[dict[str, str]]:
    """Publish stable select choices, preserving an out-of-snapshot current value."""
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(raw_options, Sequence) and not isinstance(raw_options, (str, bytes)):
        for raw in raw_options:
            if isinstance(raw, Mapping):
                value = raw.get("value") or raw.get("id") or raw.get("enum") or raw.get("name")
                label = raw.get("label") or raw.get("name") or value
            else:
                value = raw
                label = raw
            if value is None:
                continue
            value_text = str(value).strip()
            label_text = str(label).strip() if label is not None else value_text
            if not value_text or value_text in seen:
                continue
            seen.add(value_text)
            options.append({"value": value_text, "label": label_text or value_text})

    if current_value is not None:
        value_text = str(current_value).strip()
        if value_text and value_text not in seen:
            label_text = str(current_label).strip() if current_label is not None else ""
            options.insert(0, {"value": value_text, "label": label_text or value_text})
    return options


def _selected_option(
    options: Sequence[Mapping[str, Any]],
    value: Any,
    *,
    fallback_label: Any = None,
) -> dict[str, str]:
    value_text = str(value).strip() if value is not None else ""
    label_text = str(fallback_label).strip() if fallback_label is not None else ""
    for option in options:
        if str(option.get("value") or "").strip() == value_text:
            label_text = str(option.get("label") or "").strip() or label_text
            break
    return {"label": label_text or value_text, "value": value_text}


def _category_selection(
    value: Any,
    *,
    node: Any = None,
    fallback_label: Any = None,
    fallback_path: Any = None,
) -> dict[str, str]:
    value_text = str(value).strip() if value is not None else ""
    category_node = node if isinstance(node, Mapping) else {}
    label = category_node.get("name") or category_node.get("label") or fallback_label
    path = category_node.get("path") or fallback_path
    return {
        "label": str(label).strip() if label is not None else "",
        "path": str(path).strip() if path is not None else "",
        "value": value_text,
    }


def build_item_operator_object(
    *,
    item: Mapping[str, Any],
    workflow_card: Mapping[str, Any],
    category_context: Mapping[str, Any] | None = None,
    media: Sequence[Mapping[str, Any]] = (),
    media_status: Mapping[str, Any] | None = None,
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
    conditions = []
    for condition in context.get("conditions", ()):
        if not isinstance(condition, Mapping):
            continue
        value = condition.get("enum") or condition.get("condition_enum")
        label = condition.get("label") or condition.get("condition_label")
        if isinstance(value, str) and value and isinstance(label, str) and label:
            conditions.append({"value": value, "label": label})
    current_condition = str(
        _first_present(draft, "condition_enum", "condition", default="") or ""
    )
    raw_condition_required = context.get("item_condition_required")
    condition_required = (
        raw_condition_required
        if isinstance(raw_condition_required, bool)
        else None
    )
    condition_policy_resolved = condition_required is not None
    valid_condition = condition_policy_resolved and (
        not current_condition
        if condition_required is False
        else bool(current_condition)
        and any(option["value"] == current_condition for option in conditions)
    )
    condition_label = draft.get("condition_label") or draft.get("condition_description")
    condition_options = deepcopy(conditions)
    invalid_current_condition = (
        {
            "value": current_condition,
            "label": str(condition_label or current_condition),
        }
        if current_condition and not valid_condition
        else None
    )
    raw_condition_remap = context.get("condition_remap")
    condition_remap = (
        {
            "value": str(
                raw_condition_remap.get("enum")
                or raw_condition_remap.get("condition_enum")
                or ""
            ),
            "label": str(
                raw_condition_remap.get("label")
                or raw_condition_remap.get("condition_label")
                or ""
            ),
        }
        if isinstance(raw_condition_remap, Mapping)
        else None
    )
    if condition_remap and not condition_remap["value"]:
        condition_remap = None
    inventory_condition_options = []
    for option in context.get("inventory_conditions", ()):
        if not isinstance(option, Mapping):
            continue
        value = option.get("value")
        label = option.get("label")
        if isinstance(value, str) and value and isinstance(label, str) and label:
            inventory_condition_options.append({"value": value, "label": label})
    inventory_condition_group = str(
        context.get("inventory_condition_group") or ""
    )
    shipping_profile = draft.get("shipping_profile") or ""
    return_policy_id = draft.get("return_policy_id") or ""
    store_category_id = draft.get("store_category_id") or ""
    secondary_store_category_id = draft.get("secondary_store_category_id")
    raw_best_offer_enabled = draft.get("best_offer_enabled")
    best_offer_enabled = True if raw_best_offer_enabled is None else raw_best_offer_enabled
    fulfillment_policy_options = _selector_options(
        context.get("fulfillment_policies"),
        current_value=shipping_profile,
    )
    return_policy_options = _selector_options(
        context.get("return_policies"),
        current_value=return_policy_id,
    )
    store_category_options = _selector_options(
        context.get("store_categories"),
        current_value=store_category_id,
        current_label=draft.get("store_category_name") or draft.get("store_category"),
    )
    secondary_store_category_options = _selector_options(
        context.get("store_categories"),
        current_value=secondary_store_category_id,
        current_label=draft.get("secondary_store_category_name"),
    )

    aspects = []
    missing_aspects = []
    specifics = _aspect_values(get_ebay_aspects(item))
    inventory_specifics = _aspect_values(inventory_record.get_inventory_fields(item))
    live_specifics = _live_aspects(item)
    proposed_specifics = _proposed_aspects(item)
    category_aspects: dict[str, Mapping[str, Any]] = {}
    category_order: list[str] = []
    for aspect in context.get("aspects", ()):
        if not isinstance(aspect, Mapping) or not isinstance(aspect.get("name"), str):
            continue
        name = aspect["name"]
        if not name or name in category_aspects:
            continue
        category_aspects[name] = aspect
        category_order.append(name)
    custom_names = sorted(
        (
            set(specifics)
            | set(inventory_specifics)
            | set(live_specifics)
            | set(proposed_specifics)
        )
        - set(category_aspects),
        key=str.casefold,
    )
    for name in category_order + custom_names:
        aspect = category_aspects.get(name, {})
        descriptor = {
            "name": name,
            "required": bool(aspect.get("required")),
            "mode": aspect.get("mode") or "FREE_TEXT",
            "allowed_values": list(aspect.get("allowed_values") or ()),
            "value": specifics.get(name, ""),
            "inventory_value": inventory_specifics.get(name),
            "live_value": live_specifics.get(name),
            "proposed_value": proposed_specifics.get(name),
            "category_defined": name in category_aspects,
            "custom": name not in category_aspects,
        }
        aspects.append(descriptor)
        if descriptor["required"] and not descriptor["value"]:
            missing_aspects.append(descriptor["name"])

    validation_messages: list[str] = []
    category_id = str(
        _first_present(
            draft,
            "category_id",
            default=item.get("ebay_category_id") or "",
        )
        or ""
    ).strip()
    secondary_category_id = str(draft.get("secondary_category_id") or "").strip()
    category_lookup = {
        "search_endpoint": "/api/ebay/category-search?q={query}",
        "context_endpoint": "/api/ebay/category-context/{value}?current_condition={current_condition}",
        "node_endpoint": "/api/ebay/category-node/{value}",
        "browse_endpoint": "/api/ebay/category-children?parent_id={parent_id}",
        "minimum_query_length": 2,
    }
    primary_category_selection = _category_selection(
        category_id,
        node=context.get("primary_category_node"),
        fallback_label=(
            context.get("category_name")
            or draft.get("category_name")
            or item.get("ebay_category_name")
        ),
        fallback_path=context.get("category_path") or draft.get("category_path"),
    )
    secondary_category_selection = _category_selection(
        secondary_category_id,
        node=context.get("secondary_category_node"),
        fallback_label=(
            context.get("secondary_category_name")
            or draft.get("secondary_category_name")
        ),
        fallback_path=(
            context.get("secondary_category_path")
            or draft.get("secondary_category_path")
        ),
    )
    store_category_selection = _selected_option(
        store_category_options,
        store_category_id,
        fallback_label=draft.get("store_category_name") or draft.get("store_category"),
    )
    secondary_store_category_selection = _selected_option(
        secondary_store_category_options,
        secondary_store_category_id,
        fallback_label=draft.get("secondary_store_category_name"),
    )
    field_attention: dict[str, dict[str, Any]] = {}
    general_attention: list[dict[str, Any]] = []
    attention_rank = {"info": 0, "warning": 1, "error": 2}

    def add_field_attention(
        field: str,
        *,
        label: str,
        level: str,
        code: str,
        reason: str,
        evidence: Sequence[str] = (),
        actions: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        current = field_attention.setdefault(
            field,
            {
                "field": field,
                "label": label,
                "level": level,
                "summary": reason,
                "reasons": [],
                "evidence": [],
                "actions": [],
            },
        )
        if attention_rank[level] > attention_rank[current["level"]]:
            current["level"] = level
        reason_entry = {"code": code, "message": reason}
        if reason_entry not in current["reasons"]:
            current["reasons"].append(reason_entry)
        current["summary"] = "; ".join(
            entry["message"] for entry in current["reasons"]
        )
        for entry in evidence:
            if entry and entry not in current["evidence"]:
                current["evidence"].append(entry)
        for action in actions:
            copied = deepcopy(dict(action))
            if copied not in current["actions"]:
                current["actions"].append(copied)

    def edit_action(field: str, label: str) -> dict[str, str]:
        return {
            "kind": "edit-field",
            "field": field,
            "label": label,
        }

    primary_category_node_supplied = "primary_category_node" in context
    primary_category_node = context.get("primary_category_node")
    category_valid = bool(category_id and category_id != "99")
    if not category_id:
        category_reason = "No eBay category is selected."
    elif category_id == "99":
        category_reason = (
            "Category 99 is a placeholder, not an assignable eBay leaf category."
        )
    elif primary_category_node_supplied and not isinstance(
        primary_category_node, Mapping
    ):
        category_valid = False
        category_reason = (
            f"Category {category_id} is not present in the current cached eBay taxonomy."
        )
    elif (
        primary_category_node_supplied
        and isinstance(primary_category_node, Mapping)
        and primary_category_node.get("leaf") is not True
    ):
        category_valid = False
        category_reason = f"Category {category_id} is not an assignable leaf category."
    else:
        category_reason = ""
    if not category_valid:
        validation_messages.append(category_reason)
        add_field_attention(
            "category_id",
            label="eBay category",
            level="error",
            code="invalid-ebay-category",
            reason=category_reason,
            evidence=(f"draft_listing.category_id={category_id or '<empty>'}",),
            actions=(
                edit_action(
                    "category_id",
                    "Search or browse for an assignable leaf eBay category.",
                ),
            ),
        )

    category_agreement = str(draft.get("category_agreement") or "").strip().lower()
    if category_agreement in {"mismatch", "unavailable"}:
        agreement_reason = (
            "The draft category conflicts with the product-lookup category evidence."
            if category_agreement == "mismatch"
            else "The draft category has not been confirmed against product-lookup evidence."
        )
        add_field_attention(
            "category_id",
            label="eBay category",
            level="warning",
            code=f"category-agreement-{category_agreement}",
            reason=agreement_reason,
            evidence=(f"draft_listing.category_agreement={category_agreement}",),
            actions=(
                edit_action(
                    "category_id",
                    "Confirm the displayed taxonomy path or select a different category.",
                ),
            ),
        )

    if not condition_policy_resolved:
        condition_reason = (
            f"The eBay condition policy for category {category_id or '<empty>'} "
            "is unresolved, so publication cannot verify the selected condition."
        )
    elif not current_condition and condition_required is True:
        condition_reason = "This eBay category requires a condition, but none is selected."
    elif not current_condition and condition_required is False:
        condition_reason = ""
    elif not current_condition:
        condition_reason = (
            "No eBay condition is selected and itemConditionRequired is "
            "unresolved for this category."
        )
    elif not conditions:
        condition_reason = (
            f"Condition {current_condition!r} is not accepted by the published "
            f"condition policy for eBay category {category_id or '<empty>'}."
        )
    elif not valid_condition:
        condition_reason = (
            f"Condition {current_condition!r} is not valid for eBay category "
            f"{category_id or '<empty>'}."
        )
    else:
        condition_reason = ""
    condition_valid = valid_condition
    if not condition_valid:
        validation_messages.append(condition_reason)
        allowed_condition_evidence = tuple(
            f"allowed-condition:{option['value']}={option['label']}"
            for option in conditions
        )
        add_field_attention(
            "condition_enum",
            label="eBay condition",
            level="error",
            code="invalid-ebay-condition",
            reason=condition_reason,
            evidence=(
                f"draft_listing.condition={current_condition or '<empty>'}",
                (
                    f"itemConditionRequired={str(condition_required).lower()}"
                    if condition_required is not None
                    else "itemConditionRequired=<unresolved>"
                ),
                *allowed_condition_evidence,
            ),
            actions=(
                edit_action(
                    "condition_enum",
                    "Select one of the conditions published for this category.",
                ),
            ),
        )

    for aspect_name in sorted(missing_aspects, key=str.casefold):
        aspect_reason = f"Required eBay item specific {aspect_name!r} is blank."
        add_field_attention(
            f"item_specifics.{aspect_name}",
            label=aspect_name,
            level="error",
            code="missing-required-aspect",
            reason=aspect_reason,
            evidence=(f"required-aspect:{aspect_name}",),
            actions=(
                edit_action(
                    f"item_specifics.{aspect_name}",
                    f"Enter {aspect_name} before publishing.",
                ),
            ),
        )
    if missing_aspects:
        validation_messages.append(
            "Required aspects are missing: " + ", ".join(sorted(missing_aspects))
        )

    pipeline_error = _active_pipeline_error(item)
    if isinstance(pipeline_error, Mapping) and pipeline_error:
        pipeline_detail = str(
            pipeline_error.get("detail")
            or pipeline_error.get("error")
            or pipeline_error.get("code")
            or "The provider rejected the listing draft."
        )
        raw_error_field = str(pipeline_error.get("field") or "").strip()
        detail_lower = pipeline_detail.casefold()
        if raw_error_field in {"categoryId", "category_id"} or "categoryid" in detail_lower:
            error_field = "category_id"
            error_label = "eBay category"
        elif raw_error_field in {
            "condition",
            "conditionId",
            "condition_id",
            "condition_enum",
        } or "conditionid" in detail_lower:
            error_field = "condition_enum"
            error_label = "eBay condition"
        else:
            error_field = ""
            error_label = "Provider listing"
        pipeline_evidence = tuple(
            value
            for value in (
                f"provider-error:{pipeline_error.get('code') or pipeline_error.get('error')}" if pipeline_error.get("code") or pipeline_error.get("error") else "",
                f"source:{pipeline_error.get('source') or pipeline_error.get('worker')}" if pipeline_error.get("source") or pipeline_error.get("worker") else "",
                f"observed-at:{pipeline_error.get('ts') or pipeline_error.get('at')}" if pipeline_error.get("ts") or pipeline_error.get("at") else "",
            )
            if value
        )
        if error_field:
            add_field_attention(
                error_field,
                label=error_label,
                level="error",
                code="provider-rejection",
                reason=pipeline_detail,
                evidence=pipeline_evidence,
                actions=(
                    edit_action(
                        error_field,
                        f"Correct {error_label.lower()} and save the listing draft.",
                    ),
                ),
            )
        else:
            general_attention.append(
                {
                    "field": None,
                    "label": error_label,
                    "level": "error",
                    "summary": pipeline_detail,
                    "reasons": [
                        {"code": "provider-rejection", "message": pipeline_detail}
                    ],
                    "evidence": list(pipeline_evidence),
                    "actions": [
                        {
                            "kind": "command",
                            "command_id": "save-listing-draft",
                            "label": "Correct the listing draft and save it.",
                        }
                    ],
                }
            )

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
    if not {"save-draft", "list-item", "update-item"}.issubset(projected_commands):
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
        "media": [deepcopy(dict(_mapping(entry, "media entry"))) for entry in media],
        "provider_media": _provider_media(item),
        "media_status": deepcopy(
            dict(
                _mapping(
                    media_status
                    if media_status is not None
                    else {"state": "ready", "reason": None},
                    "media status",
                )
            )
        ),
    }
    listing_view = {
        "entity_id": entity_id,
        "object_generation": generation,
        "provider_state": listing_status or offer_status or "not-staged",
        "offer": deepcopy(dict(offer)),
        "listing": deepcopy(dict(provider_listing)),
    }
    inventory_condition_field: dict[str, Any] = {
        "type": "string",
        "label": "Inventory condition",
        "value": item.get("condition") or "",
    }
    if inventory_condition_group:
        inventory_condition_value = str(item.get("condition") or "")
        inventory_condition_valid = any(
            option["value"] == inventory_condition_value
            for option in inventory_condition_options
        )
        inventory_condition_field.update(
            {
                "control": "select",
                "options": deepcopy(inventory_condition_options),
                "readonly": not bool(inventory_condition_options),
                "hint": (
                    "TGW category-group condition choices are temporarily "
                    "unavailable; refresh after the condition-policy cache is restored."
                    if context.get("inventory_conditions_error")
                    else (
                        f"All conditions published for TGW category group "
                        f"{inventory_condition_group!r}; the eBay listing selector "
                        "is narrowed separately by its selected category."
                    )
                ),
            }
        )
        if inventory_condition_value and not inventory_condition_valid:
            inventory_condition_field["invalid_current"] = {
                "value": inventory_condition_value,
                "label": inventory_condition_value,
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
            "qty": {
                "type": "integer",
                "nullable": True,
                "label": "Quantity",
                "value": item.get("qty") if item.get("qty") is not None else item.get("quantity"),
            },
            "condition": inventory_condition_field,
            "brand": {"type": "string", "label": "Brand", "value": item.get("brand") or ""},
            "model": {"type": "string", "label": "Model", "value": item.get("model") or ""},
            "manufacturer": {"type": "string", "label": "Manufacturer", "value": item.get("manufacturer") or ""},
            "model_number": {"type": "string", "label": "Model number", "value": item.get("model_number") or ""},
            "country_of_manufacture": {"type": "string", "label": "Country of manufacture", "value": item.get("country_of_manufacture") or ""},
            "weight_oz": {"type": "number", "nullable": True, "label": "Weight (oz)", "value": item.get("weight_oz")},
            "cost": {"type": "number", "nullable": True, "label": "Cost", "value": item.get("cost"), "control": "money"},
            "floor_price": {"type": "number", "nullable": True, "label": "Floor price", "value": item.get("floor_price"), "control": "money"},
            "ai_hint": {"type": "string", "label": "AI identification hint", "value": item.get("ai_hint") or "", "control": "textarea"},
            "description": {"type": "string", "label": "Inventory description", "value": item.get("description") or "", "control": "textarea"},
            "notes": {"type": "string", "label": "Notes", "value": item.get("notes") or "", "control": "textarea"},
            "item_attributes": {"type": "string-map", "label": "Inventory attributes", "value": deepcopy(inventory_record.get_inventory_fields(item))},
            "status": {"type": "string", "label": "Status", "value": item.get("status") or item.get("#STATUS") or "", "readonly": True},
            "category_group": {
                "type": "string",
                "label": "Category group template",
                "value": item.get("category_group") or "",
                "control": "select",
                "options": deepcopy(context.get("category_groups") or []),
                "readonly": not bool(context.get("category_groups")),
                "hint": (
                    "Applies the template's size class and AI hint; its first "
                    "eBay category is used only when the inventory item has none."
                    if context.get("category_groups")
                    else (
                        "Category group templates are temporarily unavailable; "
                        "refresh after the server configuration is restored."
                        if context.get("category_groups_error")
                        else "No category group templates are published."
                    )
                ),
            },
            "barcode": {"type": "string", "label": "Barcode", "value": item.get("barcode") or "", "readonly": True},
            "size_class": {"type": "string", "label": "Size class", "value": item.get("size_class") or "", "readonly": True},
            "verified": {"type": "boolean", "nullable": True, "label": "Verified", "value": item.get("verified"), "readonly": True},
            "sku_old": {"type": "string", "label": "Previous SKU", "value": item.get("sku_old") or "", "readonly": True},
            "upc": {"type": "string", "label": "UPC", "value": item.get("upc") or "", "readonly": True},
            "isbn": {"type": "string", "label": "ISBN", "value": item.get("isbn") or "", "readonly": True},
            "part_number": {"type": "string", "label": "Part number", "value": item.get("part_number") or "", "readonly": True},
        },
        "listing_fields": {
            "title": {
                "type": "string",
                "label": "Listing title",
                "value": _first_present(
                    draft,
                    "title",
                    default=item.get("title") or "",
                ),
                "control": "wide",
            },
            "description": {
                "type": "string",
                "label": "Description",
                "value": _first_present(
                    draft,
                    "description",
                    "listing_description",
                    default="",
                ),
                "control": "textarea",
            },
            "category_id": {
                "type": "string",
                "label": "eBay category",
                "value": category_id,
                "display_value": primary_category_selection["label"],
                "display_path": primary_category_selection["path"],
                "control": "category-search",
                "lookup": deepcopy(category_lookup),
                "selection": primary_category_selection,
            },
            "secondary_category_id": {
                "type": "string",
                "nullable": True,
                "label": "Secondary eBay category",
                "value": secondary_category_id,
                "display_value": secondary_category_selection["label"],
                "display_path": secondary_category_selection["path"],
                "control": "category-search",
                "lookup": {
                    key: deepcopy(value)
                    for key, value in category_lookup.items()
                    if key != "context_endpoint"
                },
                "selection": secondary_category_selection,
            },
            "store_category_id": {
                "type": "string",
                "label": "Store category",
                "value": store_category_id,
                "control": "select",
                "options": store_category_options,
                "selection": store_category_selection,
            },
            "secondary_store_category_id": {
                "type": "string",
                "nullable": True,
                "label": "Secondary store category",
                "value": secondary_store_category_id,
                "control": "select",
                "options": secondary_store_category_options,
                "selection": secondary_store_category_selection,
            },
            "price": {
                "type": "number",
                "nullable": True,
                "label": "Price",
                "value": draft.get("price"),
                "control": "money",
                "hint": {
                    "target": offer.get("target_price"),
                    "median": (offer.get("price_comps") or {}).get("median") if isinstance(offer.get("price_comps"), Mapping) else None,
                    "p25": (offer.get("price_comps") or {}).get("p25") if isinstance(offer.get("price_comps"), Mapping) else None,
                    "p75": (offer.get("price_comps") or {}).get("p75") if isinstance(offer.get("price_comps"), Mapping) else None,
                    "category": deepcopy(context.get("pricing") or {}),
                },
            },
            "quantity": {"type": "integer", "nullable": True, "label": "Quantity", "value": draft.get("quantity")},
            "condition_description": {"type": "string", "label": "Condition description", "value": draft.get("condition_description") or ""},
            "shipping_profile": {"type": "string", "label": "Shipping policy", "value": shipping_profile, "control": "select", "options": fulfillment_policy_options},
            "return_policy_id": {"type": "string", "label": "Return policy", "value": return_policy_id, "control": "select", "options": return_policy_options},
            "best_offer_enabled": {
                "type": "boolean",
                "nullable": True,
                "label": "Best Offer",
                "value": best_offer_enabled,
                "default_value": True,
                "default_applied": raw_best_offer_enabled is None,
            },
            "best_offer_auto_accept_price": {"type": "number", "nullable": True, "label": "Best Offer auto-accept", "value": draft.get("best_offer_auto_accept_price"), "control": "money"},
            "best_offer_auto_decline_price": {"type": "number", "nullable": True, "label": "Best Offer auto-decline", "value": draft.get("best_offer_auto_decline_price"), "control": "money"},
            "item_specifics": {"type": "string-map", "label": "Item specifics", "value": deepcopy(specifics)},
        },
        "category": {
            "value": category_id,
            "label": primary_category_selection["label"],
            "path": primary_category_selection["path"],
            "required": True,
        },
        "condition": {
            "title": "eBay condition",
            "value": current_condition,
            "label": condition_label,
            "required": condition_required is not False,
            "valid": valid_condition,
            "policy_resolved": condition_policy_resolved,
            "item_condition_required": condition_required,
            "control": "select",
            "options": condition_options,
        },
        "aspects": aspects,
        "pricing": {
            "current": draft.get("price"),
            "target": offer.get("target_price"),
            "source": offer.get("price_source"),
            "priced_at": offer.get("priced_at"),
            "category_hint": deepcopy(context.get("pricing") or {}),
            "comps": deepcopy(offer.get("price_comps") or {}),
        },
        "defaults": {
            "fulfillment_policy_id": context.get("fulfillment_policy_id"),
            "store_category": deepcopy(context.get("store_category")),
        },
        "validation_messages": validation_messages,
    }
    if invalid_current_condition:
        field_schema["condition"]["invalid_current"] = deepcopy(
            invalid_current_condition
        )
    if condition_remap:
        field_schema["condition"]["suggested_replacement"] = deepcopy(
            condition_remap
        )
    if condition_required is False:
        field_schema["condition"]["empty_option_label"] = (
            "No condition — not required for this category"
        )
    if invalid_current_condition and condition_remap:
        field_schema["condition"]["hint"] = (
            f"Current value is not valid for this category. Choose "
            f"{condition_remap['label']!r} to use the nearest same-or-worse "
            "condition, or select another legal value."
        )
    elif invalid_current_condition and condition_required is False:
        field_schema["condition"]["hint"] = (
            "This category does not require a condition. Choose the blank "
            "option to remove the prior listing condition."
        )
    category_attention = field_attention.get("category_id")
    if category_attention:
        field_schema["listing_fields"]["category_id"]["attention"] = deepcopy(
            category_attention
        )
        field_schema["category"]["attention"] = deepcopy(category_attention)
    condition_attention = field_attention.get("condition_enum")
    if condition_attention:
        field_schema["condition"]["attention"] = deepcopy(condition_attention)
    for aspect in field_schema["aspects"]:
        aspect_attention = field_attention.get(
            f"item_specifics.{aspect.get('name')}"
        )
        if aspect_attention:
            aspect["attention"] = deepcopy(aspect_attention)

    def inventory_input_descriptor(
        name: str,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        result = {
            key: value
            for key, value in descriptor.items()
            if key in {"type", "nullable"}
        }
        if name == "condition" and descriptor.get("control") == "select":
            allowed = [""]
            for option in descriptor.get("options", ()):
                if not isinstance(option, Mapping):
                    continue
                value = str(option.get("value") or "").strip()
                if value and value not in allowed:
                    allowed.append(value)
            result["enum"] = allowed
        return result

    inventory_save_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item_fields": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    name: inventory_input_descriptor(name, descriptor)
                    for name, descriptor in field_schema["item_fields"].items()
                    if descriptor.get("readonly") is not True
                },
            }
        },
    }

    def listing_input_descriptor(
        name: str,
        descriptor: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Publish validation rules for exactly the controls the API rendered."""
        result = {
            key: value
            for key, value in descriptor.items()
            if key in {"type", "nullable"}
        }
        if descriptor.get("control") == "select":
            allowed = [""]
            for option in descriptor.get("options", ()):
                if not isinstance(option, Mapping):
                    continue
                value = str(option.get("value") or "").strip()
                if value and value not in allowed:
                    allowed.append(value)
            if len(allowed) > 1:
                result["enum"] = allowed
        return result

    listing_save_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "draft_listing": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    name: listing_input_descriptor(name, descriptor)
                    for name, descriptor in field_schema["listing_fields"].items()
                }
                | (
                    {
                        "condition_enum": {
                            "type": "string",
                            **(
                                {
                                    "enum": [
                                        "",
                                        *[option["value"] for option in conditions],
                                    ]
                                }
                                if conditions
                                else {}
                            ),
                        }
                    }
                    if current_condition or conditions
                    else {}
                ),
            },
        },
    }
    presentation = _item_presentation(
        item=item,
        category_context=context,
        workflow_state=state,
        workflow_reasons=projection_reasons,
        workflow_details=workflow_card,
        workflow_evidence=evidence,
        validation_messages=validation_messages,
        field_attention=[
            deepcopy(field_attention[key])
            for key in sorted(field_attention, key=str.casefold)
        ],
        general_attention=deepcopy(general_attention),
    )
    empty_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    reorder_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"order": {"type": "string-list"}},
    }
    pricing_input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"search_terms": {"type": "string"}},
    }
    active = bool(workflow_card.get("active_attempts"))
    status = str(item.get("status") or item.get("#STATUS") or "").strip().lower()
    listing_states = {
        str(provider_listing.get(key) or "").strip().upper()
        for key in ("status", "listing_status", "listingStatus")
    }
    provider_active = bool(
        {"ACTIVE", "PUBLISHED"}.intersection(listing_states)
        or str(offer.get("status") or "").strip().upper() == "PUBLISHED"
    )
    provider_bound = bool(provider_listing.get("listing_id") or offer.get("offer_id"))
    draft_ready_for_pricing = bool(
        isinstance(draft.get("title"), str)
        and draft.get("title", "").strip()
        and str(draft.get("category_id", "99")) != "99"
    )
    image_count = sum(
        1 for entry in media
        if isinstance(entry, Mapping) and entry.get("kind") != "video"
    )

    def lifecycle_command(
        command_id: str,
        *,
        enabled: bool,
        reason: str | None,
        input_schema: Mapping[str, Any] = empty_input_schema,
    ) -> dict[str, Any]:
        spec = _COMMAND_SPECS[command_id]
        return {
            "id": command_id,
            "enabled": enabled,
            "reason": None if enabled else reason,
            "authority_scope": spec["authority_scope"],
            "input_schema": deepcopy(dict(input_schema)),
            "value_source": spec["value_source"],
            "views": list(spec["views"]),
        }

    publication_validation_reason = (
        "Publication held: " + " ".join(validation_messages)
        if validation_messages
        else None
    )

    def publication_command(command_id: str) -> dict[str, Any]:
        enabled, reason = projected_command(command_id)
        if enabled and publication_validation_reason:
            enabled = False
            reason = publication_validation_reason
        return lifecycle_command(
            command_id,
            enabled=enabled,
            reason=reason,
            input_schema=listing_save_input_schema,
        )

    commands = [
        lifecycle_command(
            "save-inventory",
            enabled=projected_command("save-draft")[0],
            reason=projected_command("save-draft")[1],
            input_schema=inventory_save_input_schema,
        ),
        lifecycle_command(
            "save-listing-draft",
            enabled=projected_command("save-draft")[0],
            reason=projected_command("save-draft")[1],
            input_schema=listing_save_input_schema,
        ),
        publication_command("list-item"),
        publication_command("update-item"),
        lifecycle_command(
            "reidentify",
            enabled=not active,
            reason="The authoritative workflow is active.",
        ),
        lifecycle_command(
            "reprice-item",
            enabled=not active and draft_ready_for_pricing,
            reason=(
                "The authoritative workflow is active."
                if active
                else "A titled listing draft with a valid category is required."
            ),
            input_schema=pricing_input_schema,
        ),
        lifecycle_command(
            "reorder-photos",
            enabled=not active and image_count > 1,
            reason=(
                "The authoritative workflow is active."
                if active else "At least two local photographs are required."
            ),
            input_schema=reorder_input_schema,
        ),
        lifecycle_command(
            "resync-photos",
            enabled=not active and image_count > 0,
            reason=(
                "The authoritative workflow is active."
                if active else "No local photographs are available to synchronize."
            ),
        ),
        lifecycle_command(
            "sync-from-ebay",
            enabled=provider_bound,
            reason="No provider offer or listing is bound to this item.",
        ),
        lifecycle_command(
            "reset-draft-from-live",
            enabled=not active and isinstance(item.get("ebay_live"), Mapping) and bool(item.get("ebay_live")),
            reason=(
                "The authoritative workflow is active."
                if active else "No live provider snapshot is available."
            ),
        ),
        lifecycle_command(
            "end-listing",
            enabled=not active and provider_active,
            reason=(
                "The authoritative workflow is active."
                if active else "No active provider listing is present."
            ),
        ),
        lifecycle_command(
            "mark-sold",
            enabled=not active and status not in {"sold", "disposed", "archived", "deleted"},
            reason=(
                "The authoritative workflow is active."
                if active else "The item is already in a terminal inventory state."
            ),
        ),
        lifecycle_command(
            "archive-item",
            enabled=not active and status != "archived",
            reason=(
                "The authoritative workflow is active."
                if active else "The item is already archived."
            ),
        ),
        lifecycle_command(
            "delete-item",
            enabled=not active and status != "deleted",
            reason=(
                "The authoritative workflow is active."
                if active else "The item is already deleted."
            ),
        ),
    ]
    enabled_command_ids = {
        command["id"] for command in commands if command["enabled"]
    }
    listing_primary_default = (
        "update-item"
        if provider_active and "update-item" in enabled_command_ids
        else (
            "list-item"
            if "list-item" in enabled_command_ids
            else "save-listing-draft"
        )
    )
    menu_priorities = {
        "listing-tools": [
            "reidentify",
            "resync-photos",
            "sync-from-ebay",
            "reset-draft-from-live",
            "mark-sold",
            "archive-item",
            "delete-item",
        ],
        "inventory-primary": ["save-inventory", "mark-sold"],
        "inventory-more": ["archive-item", "delete-item"],
    }
    for menu in presentation["action_menus"]:
        if menu["id"] == "listing-primary":
            menu["default_command_id"] = listing_primary_default
            continue
        priority = menu_priorities[menu["id"]]
        menu["default_command_id"] = next(
            (command_id for command_id in priority if command_id in enabled_command_ids),
            priority[0],
        )
    return publish_operator_object(
        item=item_view,
        listing=listing_view,
        workflow=workflow_view,
        field_schema=field_schema,
        presentation=presentation,
        commands=commands,
    )


def web_adapter_view(published: Mapping[str, Any]) -> dict[str, Any]:
    """Web adapter: render and submit only the shared API view."""
    return adapter_view(published)
