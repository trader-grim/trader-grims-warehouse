from __future__ import annotations

import copy
import runpy
from pathlib import Path

import pytest

from tgw.operator_objects import (
    OperatorObjectBindingError,
    build_item_operator_object,
    publish_operator_object,
    validate_operator_command_values,
    web_adapter_view,
)

encoded_matrix = runpy.run_path(str(Path(__file__).with_name("operator_object_matrix.py")))["encoded_matrix"]


def test_shared_state_matrix_matches_web_adapter_contract():
    import json
    from pathlib import Path

    fixture_path = Path(__file__).parent / "fixtures/operator_object_state_matrix.json"
    fixture_bytes = fixture_path.read_text()
    assert fixture_bytes == encoded_matrix()
    matrix = json.loads(fixture_bytes)
    assert {row["expected"]["state"] for row in matrix} == {
        "ready", "staged", "published", "held", "in_progress", "reconciliation_required",
    }
    for row in matrix:
        view = web_adapter_view(row["object"])
        assert view["state"] == row["expected"]["state"]
        assert view["reasons"] == row["expected"]["reasons"]
        assert [command["id"] for command in view["commands"] if command["enabled"]] == row["expected"]["enabled_commands"]
        assert {command["id"]: command["authority_scope"] for command in view["commands"]} == row["expected"]["authority_scopes"]
        assert sorted(view["field_schema"]) == row["expected"]["field_schema_keys"]


def _published(*, state: str = "ready", generation: str = "gen-1"):
    return publish_operator_object(
        item={"entity_id": "sku-1", "object_generation": generation, "title": "Thing"},
        listing={"entity_id": "sku-1", "object_generation": generation, "provider_state": "draft"},
        workflow={"entity_id": "sku-1", "object_generation": generation, "state": state, "reasons": ["server evaluated"], "evidence": ["receipt-1"], "graph_id": "graph-1"},
        field_schema={"condition": {"options": [{"value": "used_good", "label": "Used - Good"}]}},
        commands=[
            {"id": "list-item", "enabled": state == "ready", "reason": None, "authority_scope": "publication"},
            {"id": "update-item", "enabled": state == "ready", "reason": None, "authority_scope": "update-restage"},
        ],
    )


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


def _workflow_card(*, reconciliation=(), active=(), state="staged"):
    blocked = bool(reconciliation)
    running = bool(active)
    projected_state = (
        "reconciliation_required" if blocked
        else "in_progress" if running
        else state
    )
    reason = (
        str(reconciliation[0]) if blocked
        else "The authoritative workflow is active." if running
        else None
    )
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
        "legal_actions": [] if blocked or running or state == "published" else [
            {
                "treatment_id": "ebay-publish",
                "treatment_version": "1",
                "effect_class": "external",
                "action": "held_external_contract",
                "reasons": [],
            },
        ],
        "operator_projection": {
            "state": projected_state,
            "reasons": [str(value) for value in reconciliation],
            "commands": {
                "save-draft": {
                    "enabled": not running,
                    "reason": "The authoritative workflow is active." if running else None,
                },
                "list-item": {
                    "enabled": not blocked and not running and state != "published",
                    "reason": reason or (
                        "The provider already reports this item as published."
                        if state == "published" else None
                    ),
                },
                "update-item": {
                    "enabled": not blocked and not running,
                    "reason": reason,
                },
            },
        },
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
        item=_item(),
        workflow_card=_workflow_card(),
        category_context=_category_context(),
    )
    view = web_adapter_view(published)

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
        "save-inventory": True,
        "save-listing-draft": True,
        "list-item": True,
        "update-item": True,
    }


def test_published_provider_state_disables_list_but_keeps_update_nonpublishing():
    published = build_item_operator_object(
        item=_item(published=True),
        workflow_card=_workflow_card(state="published"),
        category_context=_category_context(),
    )
    commands = {command["id"]: command for command in published["commands"]}

    assert published["workflow"]["state"] == "published"
    assert commands["list-item"]["enabled"] is False
    assert commands["list-item"]["authority_scope"] == "publication"
    assert commands["save-inventory"]["authority_scope"] == "local-item-mutation"
    assert commands["save-listing-draft"]["authority_scope"] == "local-item-mutation"
    assert commands["update-item"]["enabled"] is True
    assert commands["update-item"]["authority_scope"] == "update-restage"


def test_reconciliation_gate_holds_provider_commands_but_preserves_local_repair():
    published = build_item_operator_object(
        item=_item(),
        workflow_card=_workflow_card(reconciliation=("listing.stage",)),
        category_context=_category_context(),
    )

    assert published["workflow"]["state"] == "reconciliation_required"
    commands = {command["id"]: command for command in published["commands"]}
    assert commands["save-inventory"]["enabled"] is True
    assert commands["save-listing-draft"]["enabled"] is True
    assert commands["list-item"]["enabled"] is False
    assert commands["update-item"]["enabled"] is False


def test_command_values_are_validated_from_published_condition_and_aspect_schema():
    published = build_item_operator_object(
        item=_item(),
        workflow_card=_workflow_card(),
        category_context=_category_context(),
    )
    assert validate_operator_command_values(
        published,
        "update-item",
        {"condition_enum": "USED_GOOD", "item_specifics": {"Brand": "TGW"}},
    ) == {"condition_enum": "USED_GOOD", "item_specifics": {"Brand": "TGW"}}
    with pytest.raises(OperatorObjectBindingError, match="unpublished"):
        validate_operator_command_values(published, "update-item", {"price": "1.00"})
    with pytest.raises(OperatorObjectBindingError, match="allowed value"):
        validate_operator_command_values(published, "list-item", {"condition_enum": "invented"})


def _policy_context(**overrides):
    context = {
        "category_recognized": True,
        "item_condition_required": True,
        "required_flag_valid": True,
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
        "aspects": [],
    }
    context.update(overrides)
    return context


def test_optional_108857_keeps_independent_record_condition_and_global_vocabularies():
    item = _item()
    item["condition"] = "very good"
    item["draft_listing"] = {
        "category_id": "108857",
        "condition_enum": "",
        "item_specifics": {},
    }
    groups = [{
        "value": "paper",
        "label": "Paper",
        "size_class": "flat",
        "ai_hint": "printed paper",
        "ebay_categories": ["108857"],
    }]

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context=_policy_context(
            item_condition_required=False,
            conditions=[],
            category_groups=groups,
            record_condition_vocabulary=["New", "Very Good", "Good"],
            record_attribute_vocabulary={
                "Brand": {"type": "string"},
                "Color": {"type": "string"},
            },
        ),
    )

    schema = published["field_schema"]
    commands = {command["id"]: command for command in published["commands"]}
    assert schema["condition"] == {
        "value": "",
        "label": None,
        "required": False,
        "valid": True,
        "options": [{"value": "", "label": "No listing condition"}],
        "required_flag_valid": True,
    }
    assert schema["item_fields"]["condition"]["value"] == "Very Good"
    assert [
        option["value"]
        for option in schema["item_fields"]["condition"]["options"]
    ] == ["New", "Very Good", "Good"]
    assert schema["item_fields"]["category_group"]["options"] == groups
    assert "Color" in schema["record_attribute_vocabulary"]
    assert schema["aspects"] == []
    assert commands["save-inventory"]["enabled"] is True
    assert commands["save-listing-draft"]["enabled"] is True
    assert commands["list-item"]["enabled"] is True
    assert commands["update-item"]["enabled"] is True


@pytest.mark.parametrize(
    ("context", "condition_enum", "held_reason"),
    [
        (
            _policy_context(conditions=[]),
            "USED_GOOD",
            "selected condition is not valid",
        ),
        (
            _policy_context(),
            "",
            "category-valid displayed condition is required",
        ),
        (
            _policy_context(
                item_condition_required=None,
                required_flag_valid=False,
            ),
            "USED_GOOD",
            "itemConditionRequired policy flag is unresolved",
        ),
    ],
)
def test_condition_policy_holds_only_provider_commands(
    context, condition_enum, held_reason
):
    item = _item()
    item["draft_listing"]["condition_enum"] = condition_enum
    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context=context,
    )
    commands = {command["id"]: command for command in published["commands"]}

    assert commands["save-inventory"]["enabled"] is True
    assert set(commands["save-inventory"]["input_schema"]["properties"]) == {
        "item_fields",
    }
    assert commands["save-listing-draft"]["enabled"] is True
    assert set(
        commands["save-listing-draft"]["input_schema"]["properties"]
    ) == {"draft_listing"}
    assert commands["list-item"]["enabled"] is False
    assert commands["update-item"]["enabled"] is False
    assert held_reason in commands["list-item"]["reason"]


def test_illegal_condition_is_display_only_until_explicit_remap_or_clear():
    item = _item()
    item["condition"] = "Good"
    item["draft_listing"]["condition_enum"] = "USED_GOOD"
    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context=_policy_context(
            conditions=[{"enum": "NEW", "label": "New"}],
            condition_remap={"enum": "NEW", "label": "New"},
        ),
    )

    condition = published["field_schema"]["condition"]
    commands = {command["id"]: command for command in published["commands"]}
    assert condition["value"] == "USED_GOOD"
    assert condition["options"][0] == {
        "value": "USED_GOOD",
        "label": "Used - Good — not allowed; remap or clear",
        "display_only": True,
    }
    assert condition["options"][1] == {
        "value": "",
        "label": "Clear listing condition",
    }
    assert published["item"]["record"]["condition"] == "Good"
    assert commands["save-inventory"]["enabled"] is True
    assert commands["save-listing-draft"]["enabled"] is True
    assert commands["list-item"]["enabled"] is False
    assert commands["update-item"]["enabled"] is False
    assert validate_operator_command_values(
        published,
        "save-listing-draft",
        {"draft_listing": {"condition_enum": ""}},
    ) == {"draft_listing": {"condition_enum": ""}}
    with pytest.raises(OperatorObjectBindingError, match="allowed value"):
        validate_operator_command_values(
            published,
            "save-listing-draft",
            {"draft_listing": {"condition_enum": "USED_GOOD"}},
        )
    assert validate_operator_command_values(
        published,
        "save-listing-draft",
        {"draft_listing": {"condition_enum": "NEW"}},
    ) == {"draft_listing": {"condition_enum": "NEW"}}


def test_every_nonempty_provider_aspect_value_remains_a_selection_choice():
    values = ["Red", "Blue", "Custom provider value"]
    published = build_item_operator_object(
        item=_item(),
        workflow_card=_workflow_card(),
        category_context=_policy_context(
            aspects=[{
                "name": "Color",
                "required": False,
                "mode": "FREE_TEXT",
                "allowed_values": values,
            }],
        ),
    )

    assert published["field_schema"]["aspects"][0]["allowed_values"] == values
    command = next(
        command for command in published["commands"] if command["id"] == "list-item"
    )
    assert command["input_schema"]["properties"]["item_specifics"][
        "properties"
    ]["Color"]["enum"] == values
