from __future__ import annotations

import copy

import pytest

from tgw.operator_objects import (
    ADAPTER_VIEW_SCHEMA,
    OPERATOR_OBJECT_SCHEMA,
    OperatorObjectBindingError,
    build_item_operator_object,
    flutter_adapter_view,
    publish_operator_object,
    validate_operator_command_values,
    web_adapter_view,
)


def _published(*, state: str = "ready", generation: str = "gen-1"):
    return publish_operator_object(
        item={"entity_id": "sku-1", "object_generation": generation, "title": "Thing"},
        listing={"entity_id": "sku-1", "object_generation": generation, "provider_state": "draft"},
        workflow={"entity_id": "sku-1", "object_generation": generation, "state": state,
                  "reasons": ["server evaluated"], "evidence": ["receipt-1"], "graph_id": "graph-1"},
        field_schema={"condition": {"options": [{"value": "used_good", "label": "Used - Good"}]}},
        commands=[
            {"id": "list-item", "enabled": state == "ready", "reason": None, "authority_scope": "publication"},
            {"id": "update-item", "enabled": state == "ready", "reason": None, "authority_scope": "update-restage"},
        ],
    )


@pytest.mark.parametrize("state", ["ready", "reconciliation_required", "generation_conflict"])
def test_state_matrix_parity_across_web_and_flutter(state):
    published = _published(state=state)

    web = web_adapter_view(published)
    flutter = flutter_adapter_view(published)

    assert published["schema"] == OPERATOR_OBJECT_SCHEMA
    assert web == flutter
    assert web["schema"] == ADAPTER_VIEW_SCHEMA
    assert web["state"] == state
    assert web["commands"] == published["commands"]
    assert web["field_schema"] == published["field_schema"]


def test_only_list_item_carries_publication_scope():
    commands = _published()["commands"]
    assert {command["id"]: command["authority_scope"] for command in commands} == {
        "list-item": "publication",
        "update-item": "update-restage",
    }


@pytest.mark.parametrize("component", ["listing", "workflow"])
def test_rejects_component_with_mismatched_generation(component):
    values = {
        "item": {"entity_id": "sku-1", "object_generation": "gen-1"},
        "listing": {"entity_id": "sku-1", "object_generation": "gen-1"},
        "workflow": {"entity_id": "sku-1", "object_generation": "gen-1", "state": "ready", "evidence": []},
        "field_schema": {},
        "commands": [],
    }
    values[component]["object_generation"] = "gen-2"
    with pytest.raises(OperatorObjectBindingError, match="bindings must match"):
        publish_operator_object(**values)


def test_rejects_forged_update_publication_scope_even_in_handbuilt_adapter_object():
    forged = _published()
    forged["commands"][1]["authority_scope"] = "publication"
    with pytest.raises(OperatorObjectBindingError, match="scope"):
        web_adapter_view(forged)


def test_adapter_view_is_detached_from_caller_mutation():
    published = _published()
    rendered = web_adapter_view(published)
    before = copy.deepcopy(rendered)
    published["field_schema"]["condition"]["options"][0]["label"] = "forged"
    assert rendered == before


def _workflow_card(*, reconciliation=(), active=()):
    return {
        "entity_id": "sku-1",
        "object_generation": "gen-1",
        "graph_id": "graph-1",
        "fingerprints": [
            {"condition_id": "valid_category", "result": "true", "reasons": [], "evidence": []},
        ],
        "attempts": [],
        "active_attempts": list(active),
        "reconciliation_gates": list(reconciliation),
        "ownership_conflicts": [],
        "operator_gates": [],
    }


def _item(*, published=False):
    return {
        "sku": "sku-1",
        "title": "Thing",
        "draft_listing": {
            "category_id": "123",
            "condition_enum": "USED_GOOD",
            "condition_label": "Used - Good",
            "item_specifics": {"Brand": "TGW"},
        },
        "ebay_offer": {"offer_id": "offer-1", "status": "PUBLISHED" if published else "UNPUBLISHED"},
        "ebay_listing": {"listing_id": "listing-1", "status": "Active"} if published else {},
    }


def _category_context():
    return {
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
        "aspects": [{"name": "Brand", "required": True, "allowed_values": []}],
    }


def test_server_builder_publishes_complete_thin_client_contract():
    published = build_item_operator_object(
        item=_item(), workflow_card=_workflow_card(), category_context=_category_context(),
    )
    view = flutter_adapter_view(published)

    assert view["item"]["record"]["title"] == "Thing"
    assert view["listing"]["offer"]["offer_id"] == "offer-1"
    assert view["field_schema"]["condition"] == {
        "value": "USED_GOOD",
        "label": "Used - Good",
        "required": True,
        "valid": True,
        "options": [{"value": "USED_GOOD", "label": "Used - Good"}],
    }
    assert {command["id"]: command["enabled"] for command in view["commands"]} == {
        "list-item": True,
        "update-item": True,
    }


def test_published_provider_state_disables_list_but_keeps_update_nonpublishing():
    published = build_item_operator_object(
        item=_item(published=True), workflow_card=_workflow_card(),
        category_context=_category_context(),
    )
    commands = {command["id"]: command for command in published["commands"]}

    assert published["workflow"]["state"] == "published"
    assert commands["list-item"]["enabled"] is False
    assert commands["list-item"]["authority_scope"] == "publication"
    assert commands["update-item"]["enabled"] is True
    assert commands["update-item"]["authority_scope"] == "update-restage"


def test_reconciliation_gate_holds_every_mutating_command():
    published = build_item_operator_object(
        item=_item(), workflow_card=_workflow_card(reconciliation=("listing.stage",)),
        category_context=_category_context(),
    )

    assert published["workflow"]["state"] == "reconciliation_required"
    assert all(command["enabled"] is False for command in published["commands"])


def test_command_values_are_validated_from_published_condition_and_aspect_schema():
    published = build_item_operator_object(
        item=_item(), workflow_card=_workflow_card(), category_context=_category_context(),
    )
    assert validate_operator_command_values(
        published, "update-item", {"condition_enum": "USED_GOOD", "item_specifics": {"Brand": "TGW"}},
    ) == {"condition_enum": "USED_GOOD", "item_specifics": {"Brand": "TGW"}}
    with pytest.raises(OperatorObjectBindingError, match="unpublished"):
        validate_operator_command_values(published, "update-item", {"price": "1.00"})
    with pytest.raises(OperatorObjectBindingError, match="allowed value"):
        validate_operator_command_values(published, "list-item", {"condition_enum": "invented"})
