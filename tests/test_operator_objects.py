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


_EXPECTED_WORKSTATION_VIEWS = [
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
            {"id": "alerts", "components": ["attention-banner"], "sections": []},
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
]


_EXPECTED_ACTION_MENUS_BASE = [
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
]


def _expected_action_menus(listing_default: str) -> list[dict]:
    menus = copy.deepcopy(_EXPECTED_ACTION_MENUS_BASE)
    defaults = {
        "listing-primary": listing_default,
        "listing-tools": "reidentify",
        "inventory-primary": "save-inventory",
        "inventory-more": "archive-item",
    }
    for menu in menus:
        menu["default_command_id"] = defaults[menu["id"]]
    return menus


_EXPECTED_DATA_NAVIGATION = [
    {"label": "Listing", "target": "listing-editor"},
    {"label": "Pricing & comps", "target": "pricing-data"},
    {"label": "eBay listing data", "target": "listing-provider"},
    {"label": "Offer data", "target": "offer-provider"},
    {"label": "Price history", "target": "price-history"},
]


_EXPECTED_BREADCRUMBS = [
    {"label": "Home", "href": "/form/home"},
    {"label": "Inventory", "href": "/form/items"},
]


_EXPECTED_CATEGORY_LOOKUP = {
    "search_endpoint": "/api/ebay/category-search?q={query}",
    "context_endpoint": (
        "/api/ebay/category-context/{value}?current_condition={current_condition}"
    ),
    "node_endpoint": "/api/ebay/category-node/{value}",
    "browse_endpoint": "/api/ebay/category-children?parent_id={parent_id}",
    "minimum_query_length": 2,
}


_EXPECTED_SECONDARY_CATEGORY_LOOKUP = {
    key: value
    for key, value in _EXPECTED_CATEGORY_LOOKUP.items()
    if key != "context_endpoint"
}


_EXPECTED_LISTING_EDITOR = {
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
}


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
        listing_default = {
            "ready": "list-item",
            "staged": "list-item",
            "published": "update-item",
            "held": "save-listing-draft",
            "in_progress": "save-listing-draft",
            "reconciliation_required": "save-listing-draft",
        }[row["expected"]["state"]]
        assert view["state"] == row["expected"]["state"]
        assert view["reasons"] == row["expected"]["reasons"]
        assert [command["id"] for command in view["commands"] if command["enabled"]] == row["expected"]["enabled_commands"]
        assert {command["id"]: command["authority_scope"] for command in view["commands"]} == row["expected"]["authority_scopes"]
        assert sorted(view["field_schema"]) == row["expected"]["field_schema_keys"]
        assert view["presentation"]["views"] == _EXPECTED_WORKSTATION_VIEWS
        assert view["presentation"]["action_menus"] == _expected_action_menus(
            listing_default
        )
        assert view["presentation"]["data_navigation"] == _EXPECTED_DATA_NAVIGATION
        assert view["presentation"]["breadcrumbs"] == _EXPECTED_BREADCRUMBS
        assert view["presentation"]["listing_editor"] == _EXPECTED_LISTING_EDITOR
        listing_field_order = list(view["field_schema"]["listing_fields"])
        assert listing_field_order.index("category_id") < listing_field_order.index(
            "price"
        )


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
            "title": "Thing",
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
        "item_condition_required": True,
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
        "aspects": [{"name": "Brand", "required": True, "allowed_values": []}],
        "category_groups": [
            {"value": "parts", "label": "Parts"},
            {"value": "books", "label": "Books"},
        ],
    }


def test_server_builder_publishes_complete_thin_client_contract():
    published = build_item_operator_object(
        item=_item(),
        workflow_card=_workflow_card(),
        category_context=_category_context(),
        media=[
            {
                "kind": "image",
                "name": "front view.jpg",
                "url": "/media/sku-1/front%20view.jpg",
                "position": 0,
                "primary": True,
            },
        ],
    )
    view = web_adapter_view(published)

    assert view["item"]["record"]["title"] == "Thing"
    assert view["listing"]["offer"]["offer_id"] == "offer-1"
    assert view["item"]["media"] == [
        {
            "kind": "image",
            "name": "front view.jpg",
            "url": "/media/sku-1/front%20view.jpg",
            "position": 0,
            "primary": True,
        },
    ]
    assert view["item"]["media_status"] == {"state": "ready", "reason": None}
    assert view["field_schema"]["condition"] == {
        "title": "eBay condition",
        "value": "USED_GOOD",
        "label": "Used - Good",
        "required": True,
        "valid": True,
        "policy_resolved": True,
        "item_condition_required": True,
        "control": "select",
        "options": [{"value": "USED_GOOD", "label": "Used - Good"}],
    }
    assert {command["id"]: command["enabled"] for command in view["commands"]} == {
        "save-inventory": True,
        "save-listing-draft": True,
        "list-item": True,
        "update-item": True,
        "reidentify": True,
        "reprice-item": True,
        "reorder-photos": False,
        "resync-photos": True,
        "sync-from-ebay": True,
        "reset-draft-from-live": False,
        "end-listing": False,
        "mark-sold": True,
        "archive-item": True,
        "delete-item": True,
    }
    command_semantics = {
        command["id"]: command["value_semantics"] for command in view["commands"]
    }
    assert command_semantics["save-inventory"] == "sparse-patch"
    assert command_semantics["save-listing-draft"] == "sparse-patch"
    assert command_semantics["list-item"] == "complete-value"
    assert command_semantics["update-item"] == "complete-value"
    assert command_semantics["reidentify"] == "complete-value"
    assert view["presentation"]["views"] == _EXPECTED_WORKSTATION_VIEWS
    assert view["presentation"]["action_menus"] == _expected_action_menus("list-item")
    assert view["presentation"]["data_navigation"] == _EXPECTED_DATA_NAVIGATION
    assert view["presentation"]["breadcrumbs"] == _EXPECTED_BREADCRUMBS
    assert view["presentation"]["listing_editor"] == _EXPECTED_LISTING_EDITOR
    assert view["presentation"]["pricing_context"]["id"] == "pricing-data"
    assert view["presentation"]["pricing_context"]["details_target"] == "pricing-data"
    category = view["field_schema"]["listing_fields"]["category_id"]
    assert category["control"] == "category-search"
    assert category["lookup"] == _EXPECTED_CATEGORY_LOOKUP
    assert view["field_schema"]["condition"]["options"] == [
        {"value": "USED_GOOD", "label": "Used - Good"},
    ]


def test_server_publishes_status_attention_and_holds_invalid_listing_fields():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {
        "category_id": "999",
        "condition_enum": "USED_GOOD",
        "item_specifics": {"Brand": ""},
    }
    item["pipeline_error"] = {
        "code": "ebay_rejected",
        "detail": "Invalid value for categoryId.",
        "source": "ebay_stage",
        "field": "categoryId",
        "ts": "2026-08-23T12:00:00Z",
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "primary_category_node": None,
            "item_condition_required": True,
            "conditions": [
                {"enum": "USED_EXCELLENT", "label": "Used - Excellent"},
            ],
            "aspects": [
                {"name": "Brand", "required": True, "allowed_values": []},
            ],
        },
    )

    category_attention = published["field_schema"]["listing_fields"][
        "category_id"
    ]["attention"]
    assert category_attention["level"] == "error"
    assert category_attention["reasons"] == [
        {
            "code": "invalid-ebay-category",
            "message": (
                "Category 999 is not present in the current cached eBay taxonomy."
            ),
        },
        {
            "code": "provider-rejection",
            "message": "Invalid value for categoryId.",
        },
    ]
    assert category_attention["evidence"] == [
        "draft_listing.category_id=999",
        "provider-error:ebay_rejected",
        "source:ebay_stage",
        "observed-at:2026-08-23T12:00:00Z",
    ]
    assert category_attention["actions"][0] == {
        "kind": "edit-field",
        "field": "category_id",
        "label": "Search or browse for an assignable leaf eBay category.",
    }

    condition_attention = published["field_schema"]["condition"]["attention"]
    assert condition_attention["reasons"] == [
        {
            "code": "invalid-ebay-condition",
            "message": "Condition 'USED_GOOD' is not valid for eBay category 999.",
        }
    ]
    assert condition_attention["evidence"] == [
        "draft_listing.condition=USED_GOOD",
        "itemConditionRequired=true",
        "allowed-condition:USED_EXCELLENT=Used - Excellent",
    ]
    aspect_attention = published["field_schema"]["aspects"][0]["attention"]
    assert aspect_attention["reasons"] == [
        {
            "code": "missing-required-aspect",
            "message": "Required eBay item specific 'Brand' is blank.",
        }
    ]

    presentation = published["presentation"]
    assert presentation["status_summary"]["listing"] == {
        "label": "eBay",
        "state": "Failed",
        "tone": "error",
        "reasons": ["Invalid value for categoryId."],
        "evidence": [
            "offer-id:offer-1",
            "provider-error:ebay_rejected",
            "source:ebay_stage",
            "observed-at:2026-08-23T12:00:00Z",
        ],
    }
    listing_header_status = next(
        status
        for status in presentation["header"]["statuses"]
        if status["id"] == "listing"
    )
    assert listing_header_status["value"] == "Failed"
    assert listing_header_status["reasons"] == ["Invalid value for categoryId."]
    assert presentation["attention"]["level"] == "error"
    assert presentation["attention"]["title"] == "Listing requires correction"
    assert presentation["attention"]["expanded"] is False
    assert any(
        entry["field"] == "category_id"
        for entry in presentation["attention"]["items"]
    )

    commands = {command["id"]: command for command in published["commands"]}
    expected_reason = (
        "Publication held: Category 999 is not present in the current cached "
        "eBay taxonomy. Condition 'USED_GOOD' is not valid for eBay category "
        "999. Required aspects are missing: Brand"
    )
    assert commands["save-listing-draft"]["enabled"] is True
    assert commands["save-listing-draft"]["value_semantics"] == "sparse-patch"
    for command_id in ("list-item", "update-item"):
        assert commands[command_id]["enabled"] is False
        assert commands[command_id]["reason"] == expected_reason
        assert commands[command_id]["value_semantics"] == "complete-value"
    listing_menu = next(
        menu
        for menu in presentation["action_menus"]
        if menu["id"] == "listing-primary"
    )
    assert listing_menu["default_command_id"] == "save-listing-draft"


def test_later_successful_stage_suppresses_older_stage_rejection_projection():
    item = _item()
    item["pipeline_error"] = {
        "code": "ebay_rejected",
        "detail": "Invalid value for categoryId.",
        "source": "ebay_stage",
        "field": "categoryId",
        "ts": "2026-08-21T23:30:43.862961+00:00",
    }
    item["ebay_offer"]["staged_at"] = "2026-08-24T14:56:43.950419+00:00"
    item["ebay_submitted"] = {
        "staged_at": "2026-08-24T14:56:43.950419+00:00",
        "inventory_item": {"condition": "USED_GOOD"},
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            **_category_context(),
            "primary_category_node": {"leaf": True},
        },
    )

    # Projection reconciliation never erases the canonical historical finding.
    assert item["pipeline_error"]["detail"] == "Invalid value for categoryId."
    assert not any(
        alert["title"] == "eBay rejected ebay_stage"
        for alert in published["presentation"]["alerts"]
    )
    assert published["presentation"]["status_summary"]["listing"]["state"] == (
        "UNPUBLISHED"
    )
    category_attention = published["field_schema"]["listing_fields"][
        "category_id"
    ].get("attention")
    assert not category_attention or all(
        reason["code"] != "provider-rejection"
        for reason in category_attention["reasons"]
    )


def test_newer_stage_rejection_remains_active_after_older_stage_success():
    item = _item()
    item["ebay_offer"]["staged_at"] = "2026-08-21T23:00:00+00:00"
    item["pipeline_error"] = {
        "code": "ebay_rejected",
        "detail": "Invalid value for categoryId.",
        "source": "ebay_stage",
        "field": "categoryId",
        "ts": "2026-08-21T23:30:43+00:00",
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            **_category_context(),
            "primary_category_node": {"leaf": True},
        },
    )

    assert any(
        alert["title"] == "eBay rejected ebay_stage"
        for alert in published["presentation"]["alerts"]
    )
    assert published["presentation"]["status_summary"]["listing"]["state"] == (
        "Failed"
    )
    category_attention = published["field_schema"]["listing_fields"][
        "category_id"
    ]["attention"]
    assert any(
        reason["code"] == "provider-rejection"
        for reason in category_attention["reasons"]
    )


def test_optional_and_unresolved_condition_policy_are_distinct():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {"condition_enum": ""}

    optional = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "item_condition_required": False,
            "conditions": [],
            "aspects": _category_context()["aspects"],
        },
    )
    assert optional["field_schema"]["condition"]["valid"] is True
    assert optional["field_schema"]["condition"]["required"] is False
    assert "attention" not in optional["field_schema"]["condition"]
    assert next(
        command for command in optional["commands"] if command["id"] == "list-item"
    )["enabled"] is True

    unresolved = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={"conditions": [], "aspects": _category_context()["aspects"]},
    )
    unresolved_condition = unresolved["field_schema"]["condition"]
    assert unresolved_condition["policy_resolved"] is False
    assert unresolved_condition["attention"]["reasons"] == [
        {
            "code": "invalid-ebay-condition",
            "message": (
                "The eBay condition policy for category 123 is unresolved, so "
                "publication cannot verify the selected condition."
            ),
        }
    ]


def test_tgw_group_inventory_conditions_are_distinct_from_ebay_listing_policy():
    item = _item()
    item["category_group"] = "books"
    item["condition"] = "Very Good"
    item["draft_listing"] = item["draft_listing"] | {
        "category_id": "1105",
        "condition_enum": "USED_VERY_GOOD",
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "inventory_condition_group": "books",
            "inventory_conditions": [
                {"value": "New", "label": "New"},
                {"value": "Very Good", "label": "Very Good"},
                {"value": "Good", "label": "Good"},
                {"value": "Acceptable", "label": "Acceptable"},
            ],
            "item_condition_required": True,
            "conditions": [
                {"enum": "USED_VERY_GOOD", "label": "Very Good"},
                {"enum": "USED_GOOD", "label": "Good"},
            ],
            "aspects": [],
        },
    )

    inventory = published["field_schema"]["item_fields"]["condition"]
    listing = published["field_schema"]["condition"]
    assert inventory["control"] == "select"
    assert [option["value"] for option in inventory["options"]] == [
        "New",
        "Very Good",
        "Good",
        "Acceptable",
    ]
    assert [option["value"] for option in listing["options"]] == [
        "USED_VERY_GOOD",
        "USED_GOOD",
    ]
    save_inventory = next(
        command
        for command in published["commands"]
        if command["id"] == "save-inventory"
    )
    assert save_inventory["input_schema"]["properties"]["item_fields"][
        "properties"
    ]["condition"]["enum"] == [
        "",
        "New",
        "Very Good",
        "Good",
        "Acceptable",
    ]


def test_tgw_group_condition_outside_vocabulary_is_display_only():
    item = _item()
    item["category_group"] = "books"
    item["condition"] = "Legacy Grade"

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "inventory_condition_group": "books",
            "inventory_conditions": [
                {"value": "Very Good", "label": "Very Good"},
                {"value": "Good", "label": "Good"},
            ],
            "item_condition_required": True,
            "conditions": _category_context()["conditions"],
            "aspects": [],
        },
    )
    inventory = published["field_schema"]["item_fields"]["condition"]

    assert inventory["options"] == [
        {"value": "Very Good", "label": "Very Good"},
        {"value": "Good", "label": "Good"},
    ]
    assert inventory["invalid_current"] == {
        "value": "Legacy Grade",
        "label": "Legacy Grade",
    }


def test_category_change_publishes_invalid_condition_display_and_explicit_suggestion():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {
        "condition_enum": "USED_EXCELLENT",
        "condition_label": "Used",
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "item_condition_required": True,
            "conditions": [{"enum": "USED_GOOD", "label": "Good"}],
            "condition_remap": {"enum": "USED_GOOD", "label": "Good"},
            "aspects": [],
        },
    )
    condition = published["field_schema"]["condition"]

    assert condition["options"] == [{"value": "USED_GOOD", "label": "Good"}]
    assert condition["invalid_current"] == {
        "value": "USED_EXCELLENT",
        "label": "Used",
    }
    assert condition["suggested_replacement"] == {
        "value": "USED_GOOD",
        "label": "Good",
    }
    assert "Choose 'Good'" in condition["hint"]


def test_optional_category_requires_explicit_clear_of_prior_condition():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {
        "category_id": "108857",
        "condition_enum": "Very Good",
        "condition_label": "Very Good",
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "item_condition_required": False,
            "conditions": [],
            "aspects": [],
        },
    )
    condition = published["field_schema"]["condition"]

    assert condition["required"] is False
    assert condition["valid"] is False
    assert condition["options"] == []
    assert condition["invalid_current"]["value"] == "Very Good"
    assert condition["empty_option_label"].startswith("No condition")
    assert "Choose the blank option" in condition["hint"]


def test_cached_choices_without_requirement_flag_remain_unresolved():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {
        "condition_enum": "USED_GOOD",
        "condition_label": "Good",
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Good"}],
            "aspects": [],
        },
    )
    condition = published["field_schema"]["condition"]

    assert condition["policy_resolved"] is False
    assert condition["valid"] is False
    assert condition["invalid_current"] == {
        "value": "USED_GOOD",
        "label": "Good",
    }
    assert "policy" in condition["attention"]["reasons"][0]["message"].lower()


def test_listing_selection_controls_keep_invalid_current_display_only():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {
        "category_name": "Current category",
        "condition_enum": "CURRENT_CONDITION",
        "condition_label": "Current condition",
        "store_category_id": "STORE-CURRENT",
        "shipping_profile": "SHIP-CURRENT",
        "return_policy_id": "RETURN-CURRENT",
    }
    context = {
        "category_name": "Current category",
        "item_condition_required": True,
        "conditions": [
            {"enum": "OTHER_CONDITION", "label": "Other condition"},
        ],
        "aspects": [],
        "store_categories": [
            {"value": "STORE-OTHER", "label": "Other store category"},
        ],
        "fulfillment_policies": [
            {"value": "SHIP-OTHER", "label": "Other shipping policy"},
        ],
        "return_policies": [
            {"value": "RETURN-OTHER", "label": "Other return policy"},
        ],
    }

    view = web_adapter_view(
        build_item_operator_object(
            item=item,
            workflow_card=_workflow_card(),
            category_context=context,
        )
    )
    listing_fields = view["field_schema"]["listing_fields"]

    assert listing_fields["category_id"] == {
        "type": "string",
        "label": "eBay category",
        "value": "123",
        "display_value": "Current category",
        "display_path": "",
        "control": "category-search",
        "lookup": _EXPECTED_CATEGORY_LOOKUP,
        "selection": {
            "label": "Current category",
            "path": "",
            "value": "123",
        },
    }
    expected_selectors = {
        "store_category_id": (
            "STORE-CURRENT",
            [
                {"value": "STORE-CURRENT", "label": "STORE-CURRENT"},
                {"value": "STORE-OTHER", "label": "Other store category"},
            ],
        ),
        "shipping_profile": (
            "SHIP-CURRENT",
            [
                {"value": "SHIP-CURRENT", "label": "SHIP-CURRENT"},
                {"value": "SHIP-OTHER", "label": "Other shipping policy"},
            ],
        ),
        "return_policy_id": (
            "RETURN-CURRENT",
            [
                {"value": "RETURN-CURRENT", "label": "RETURN-CURRENT"},
                {"value": "RETURN-OTHER", "label": "Other return policy"},
            ],
        ),
    }
    for field_name, (current_value, options) in expected_selectors.items():
        field = listing_fields[field_name]
        assert field["control"] == "select"
        assert field["value"] == current_value
        assert field["options"] == options
        assert field["options"][0]["value"] == current_value

    condition = view["field_schema"]["condition"]
    assert {
        key: condition[key]
        for key in (
            "title",
            "value",
            "label",
            "required",
            "valid",
            "policy_resolved",
            "item_condition_required",
            "control",
            "options",
        )
    } == {
        "title": "eBay condition",
        "value": "CURRENT_CONDITION",
        "label": "Current condition",
        "required": True,
        "valid": False,
        "policy_resolved": True,
        "item_condition_required": True,
        "control": "select",
        "options": [
            {"value": "OTHER_CONDITION", "label": "Other condition"},
        ],
    }
    assert condition["invalid_current"] == {
        "value": "CURRENT_CONDITION",
        "label": "Current condition",
    }
    assert condition["attention"]["reasons"] == [
        {
            "code": "invalid-ebay-condition",
            "message": "Condition 'CURRENT_CONDITION' is not valid for eBay category 123.",
        }
    ]


def test_category_descriptors_publish_shared_lookup_and_human_selections():
    item = _item()
    item["draft_listing"] = item["draft_listing"] | {
        "category_name": "Primary category",
        "category_path": "Collectibles > Primary category",
        "secondary_category_id": "456",
        "secondary_category_name": "Secondary category",
        "secondary_category_path": "Collectibles > Secondary category",
        "store_category_id": "STORE-PRIMARY",
        "secondary_store_category_id": "STORE-SECONDARY",
    }
    context = {
        "category_name": "Primary category",
        "category_path": "Collectibles > Primary category",
        "item_condition_required": True,
        "conditions": _category_context()["conditions"],
        "aspects": [],
        "store_categories": [
            {"value": "STORE-PRIMARY", "label": "Primary store category"},
            {
                "value": "STORE-SECONDARY",
                "label": "Secondary store category",
            },
        ],
    }

    view = web_adapter_view(
        build_item_operator_object(
            item=item,
            workflow_card=_workflow_card(),
            category_context=context,
        )
    )
    fields = view["field_schema"]["listing_fields"]

    assert fields["category_id"]["control"] == "category-search"
    assert fields["secondary_category_id"]["control"] == "category-search"
    assert fields["category_id"]["lookup"] == _EXPECTED_CATEGORY_LOOKUP
    assert (
        fields["secondary_category_id"]["lookup"]
        == _EXPECTED_SECONDARY_CATEGORY_LOOKUP
    )
    assert fields["category_id"]["selection"] == {
        "label": "Primary category",
        "path": "Collectibles > Primary category",
        "value": "123",
    }
    assert fields["secondary_category_id"]["selection"] == {
        "label": "Secondary category",
        "path": "Collectibles > Secondary category",
        "value": "456",
    }
    assert fields["store_category_id"]["selection"] == {
        "label": "Primary store category",
        "value": "STORE-PRIMARY",
    }
    assert fields["secondary_store_category_id"]["selection"] == {
        "label": "Secondary store category",
        "value": "STORE-SECONDARY",
    }


@pytest.mark.parametrize(
    ("published_value", "expected"),
    [("missing", True), (None, True), (False, False)],
)
def test_best_offer_defaults_enabled_without_overriding_explicit_false(
    published_value,
    expected,
):
    item = _item()
    if published_value != "missing":
        item["draft_listing"]["best_offer_enabled"] = published_value

    view = web_adapter_view(
        build_item_operator_object(
            item=item,
            workflow_card=_workflow_card(),
            category_context=_category_context(),
        )
    )

    assert view["field_schema"]["listing_fields"]["best_offer_enabled"] == {
        "type": "boolean",
        "nullable": True,
        "label": "Best Offer",
        "value": expected,
        "default_value": True,
        "default_applied": published_value == "missing" or published_value is None,
    }


def test_dense_item_publishes_human_sections_and_server_owned_view_placement():
    item = _item(published=True) | {
        "status": "Listed",
        "location": "A1-02",
        "description": "A useful inventory description.",
        "search_terms": "bob drake catalog",
        "item_attributes": {"Country of Origin": "US", "Brand": "TGW"},
        "draft_listing_state": "ready",
        "price_history": [
            {
                "ts": "2026-08-20T10:00:00Z",
                "price": 42.0,
                "previous_price": 48.0,
                "stage": "manual",
                "label": "Operator price",
            },
        ],
        "reprice_schedule": [
            {"stage": "week-1", "price": 39.0, "due_at": "2026-08-27T10:00:00Z"},
        ],
        "ebay_live": {"offer": {"status": "PUBLISHED"}},
        "ebay_submitted": {"inventory_item": {"condition": "USED_GOOD"}},
        "alt_text_results": [{"photo": "front.jpg", "model": "vision-model"}],
        "pipeline_error": {
            "code": "ebay_rejected",
            "detail": "Invalid value for categoryId.",
            "source": "ebay_stage",
            "field": "category_id",
            "ts": "2026-08-20T11:00:00Z",
        },
    }
    item["draft_listing"] = item["draft_listing"] | {
        "description": "Human-readable listing description.",
        "price": 42.0,
        "quantity": 1,
        "quality": {"score": 88, "flags": ["verify measurements"]},
    }
    item["ebay_offer"] = item["ebay_offer"] | {
        "price": 42.0,
        "suggested_price": 40.0,
        "quantity": 1,
        "price_comps": {
            "median": 44.0,
            "p75": 51.0,
            "query": "actual winning query",
            "source": "browse:full_title",
            "queried_at": "2026-08-20T09:00:00Z",
            "items": [
                {
                    "title": "Comparable",
                    "price": 44.0,
                    "condition": "Used",
                    "item_id": "item-1",
                    "url": "https://www.ebay.com/itm/1",
                    "outlier": False,
                    "llm_dropped": False,
                    "llm_reason": "",
                },
            ],
        },
    }
    item["ebay_listing"] = item["ebay_listing"] | {
        "listing_url": "https://www.ebay.com/itm/1",
        "live_price": 42.0,
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(state="published"),
        category_context=_category_context(),
    )
    presentation = published["presentation"]
    sections = {section["id"]: section for section in presentation["sections"]}
    views = {view["id"]: view for view in presentation["views"]}
    commands = {command["id"]: command for command in published["commands"]}

    assert presentation["schema"] == "tgw-item-presentation/v1"
    assert presentation["views"] == _EXPECTED_WORKSTATION_VIEWS
    assert presentation["action_menus"] == _expected_action_menus("update-item")
    assert presentation["data_navigation"] == _EXPECTED_DATA_NAVIGATION
    assert presentation["breadcrumbs"] == _EXPECTED_BREADCRUMBS
    assert presentation["listing_editor"] == _EXPECTED_LISTING_EDITOR
    assert presentation["header"]["facts"] == [
        {"id": "sku", "label": "SKU", "value": "sku-1", "format": "code"},
        {"id": "title", "label": "Title", "value": "Thing", "format": "text"},
        {"id": "location", "label": "Location", "value": "A1-02", "format": "text"},
        {"id": "quantity", "label": "Qty", "value": 1, "format": "text"},
        {
            "id": "status",
            "label": "Status",
            "value": "Listed",
            "format": "text",
            "tone": "accent",
        },
    ]
    assert presentation["header"]["lead"] == {
        "id": "title",
        "label": "Title",
        "value": "Thing",
        "format": "text",
    }
    assert presentation["header"]["trail"] == [
        {"id": "sku", "label": "SKU", "value": "sku-1", "format": "code"},
        {"id": "location", "label": "Location", "value": "A1-02", "format": "text"},
        {"id": "quantity", "label": "Qty", "value": 1, "format": "text"},
    ]
    assert presentation["header"]["status"] == {
        "id": "status",
        "label": "Status",
        "value": "Listed",
        "format": "text",
        "tone": "accent",
    }
    assert list(views) == ["inventory", "listing", "evidence"]
    assert views["listing"]["default"] is True
    listing_data = next(
        region
        for region in views["listing"]["regions"]
        if region["id"] == "listing-data"
    )
    assert listing_data["components"] == ["pricing-context"]
    assert presentation["pricing_context"]["id"] == "pricing-data"
    assert presentation["pricing_context"]["details_target"] == "pricing-data"
    assert presentation["pricing_context"]["details_sections"] == [
        "pricing-comps",
        "pricing-comparable-listings",
    ]
    assert presentation["pricing_context"]["search_terms"] == "actual winning query"
    assert presentation["pricing_context"]["search_terms_source"] == "pricing-observation"
    assert presentation["pricing_context"]["requested_search_terms"] == "bob drake catalog"
    assert presentation["pricing_context"]["last_successful_search_terms"] == "actual winning query"
    assert presentation["pricing_context"]["suggested_price"] == 40.0
    assert presentation["pricing_context"]["command_id"] == "reprice-item"
    assert presentation["pricing_context"]["research_links"] == [
        {
            "id": "ebay-sold",
            "label": "eBay Sold",
            "href": "https://www.ebay.com/sch/i.html?_nkw=actual+winning+query&LH_Complete=1&LH_Sold=1",
            "external": True,
        },
        {
            "id": "ebay-active",
            "label": "eBay Active",
            "href": "https://www.ebay.com/sch/i.html?_nkw=actual+winning+query",
            "external": True,
        },
        {
            "id": "terapeak",
            "label": "Terapeak",
            "href": "https://www.ebay.com/sh/research?marketplace=EBAY-US&keywords=actual+winning+query",
            "external": True,
        },
    ]
    assert published["field_schema"]["listing_fields"]["price"]["hint"]["median"] == 44.0
    assert sections["price-history"]["kind"] == "table"
    assert sections["pricing-comparable-listings"]["kind"] == "table"
    assert sections["pricing-comparable-listings"]["collapsed"] is True
    assert sections["workflow-summary"]["kind"] == "properties"
    assert sections["provider-live-snapshot"] == {
        "id": "provider-live-snapshot",
        "title": "Provider live snapshot",
        "kind": "tree",
        "value": {"offer": {"status": "PUBLISHED"}},
        "collapsed": True,
    }
    assert sections["listing-draft-summary"]["kind"] == "properties"
    assert commands["save-inventory"]["views"] == ["inventory"]
    assert commands["save-inventory"]["value_source"] == "editor"
    assert commands["save-listing-draft"]["views"] == ["listing"]
    assert commands["save-listing-draft"]["value_source"] == "editor"
    assert commands["reprice-item"]["views"] == ["listing"]
    assert commands["reprice-item"]["value_source"] == "pricing"
    assert commands["reprice-item"]["input_schema"]["properties"] == {
        "search_terms": {"type": "string"},
    }
    assert commands["list-item"]["views"] == ["listing"]
    assert commands["list-item"]["value_source"] == "editor"
    assert commands["list-item"]["input_schema"] == commands[
        "save-listing-draft"
    ]["input_schema"]
    assert any(alert["title"] == "Listing quality needs attention" for alert in presentation["alerts"])
    pipeline_alert = next(alert for alert in presentation["alerts"] if alert["title"] == "eBay rejected ebay_stage")
    assert pipeline_alert["message"] == "Invalid value for categoryId."
    assert {row["label"]: row["value"] for row in pipeline_alert["details"]} == {
        "Code": "ebay_rejected",
        "Field": "category_id",
        "Source": "ebay_stage",
        "When": "2026-08-20T11:00:00Z",
    }


def test_pricing_context_never_synthesizes_search_terms_from_title():
    published = build_item_operator_object(
        item={
            "sku": "sku-1",
            "title": "A title that was never queried",
            "draft_listing": {
                "title": "A different unqueried draft title",
                "category_id": "123",
            },
        },
        workflow_card=_workflow_card(),
        category_context=_category_context(),
    )

    pricing_context = published["presentation"]["pricing_context"]
    assert pricing_context["search_terms"] == ""
    assert pricing_context["search_terms_source"] is None
    assert pricing_context["last_successful_search_terms"] == ""


def test_pricing_context_recovers_unambiguous_legacy_query_from_comp_evidence():
    published = build_item_operator_object(
        item={
            "sku": "sku-1",
            "title": "Current title is not the old query",
            "draft_listing": {"title": "Current title", "category_id": "123"},
            "ebay_offer": {
                "price_comps": {
                    "count": 2,
                    "items": [
                        {"url": "https://www.ebay.com/itm/1?_skw=Exact+Old+Query"},
                        {"url": "https://www.ebay.com/itm/2?_skw=Exact+Old+Query"},
                    ],
                },
            },
        },
        workflow_card=_workflow_card(),
        category_context=_category_context(),
    )

    pricing_context = published["presentation"]["pricing_context"]
    assert pricing_context["search_terms"] == "Exact Old Query"
    assert pricing_context["search_terms_source"] == "legacy-comp-url"
    assert pricing_context["last_successful_search_terms"] == "Exact Old Query"


def test_sparse_held_item_keeps_listing_save_schema_usable_without_taxonomy_choices():
    published = build_item_operator_object(
        item={
            "sku": "sku-1",
            "draft_listing": {"category_id": "99", "item_specifics": {}},
        },
        workflow_card=_workflow_card(state="held"),
        category_context={},
    )
    commands = {command["id"]: command for command in published["commands"]}
    save_properties = commands["save-listing-draft"]["input_schema"]["properties"]["draft_listing"]["properties"]

    assert commands["save-listing-draft"]["enabled"] is True
    assert "condition_enum" not in save_properties
    assert "condition_enum" not in commands["list-item"]["input_schema"]["properties"]
    assert validate_operator_command_values(
        published,
        "save-listing-draft",
        {"draft_listing": {"category_id": "123", "price": 12.5}},
    ) == {"draft_listing": {"category_id": "123", "price": 12.5}}


def test_inventory_schema_restores_fields_but_excludes_read_only_values_from_save():
    item = _item() | {
        "location": "A1-02",
        "qty": 2,
        "condition": "good",
        "brand": "TGW",
        "model": "M1",
        "manufacturer": "TGW Works",
        "model_number": "M-001",
        "country_of_manufacture": "US",
        "weight_oz": 12.5,
        "cost": 5.0,
        "floor_price": 9.99,
        "ai_hint": "blue label",
        "description": "Inventory description",
        "notes": "Shelf note",
        "status": "In Stock",
        "category_group": "parts",
        "barcode": "012345",
        "size_class": "small",
        "verified": True,
        "sku_old": "old-sku",
        "upc": "000111222333",
        "isbn": "9780000000000",
        "part_number": "P-1",
    }
    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context=_category_context(),
    )

    item_fields = published["field_schema"]["item_fields"]
    assert set(item_fields) == {
        "title", "location", "qty", "condition", "brand", "model",
        "manufacturer", "model_number", "country_of_manufacture", "weight_oz",
        "cost", "floor_price", "ai_hint", "description", "notes",
        "item_attributes", "status", "category_group", "barcode", "size_class",
        "verified", "sku_old", "upc", "isbn", "part_number",
    }
    read_only = {
        name for name, descriptor in item_fields.items()
        if descriptor.get("readonly") is True
    }
    assert read_only == {
        "status", "barcode", "size_class", "verified",
        "sku_old", "upc", "isbn", "part_number",
    }
    assert item_fields["category_group"]["control"] == "select"
    assert item_fields["category_group"]["options"] == [
        {"value": "parts", "label": "Parts"},
        {"value": "books", "label": "Books"},
    ]
    inventory_command = next(
        command for command in published["commands"]
        if command["id"] == "save-inventory"
    )
    save_fields = set(
        inventory_command["input_schema"]["properties"]["item_fields"]["properties"]
    )
    assert save_fields == set(item_fields) - read_only
    assert "price" not in item_fields
    with pytest.raises(OperatorObjectBindingError, match="unpublished"):
        validate_operator_command_values(
            published,
            "save-inventory",
            {"item_fields": {"status": "Sold"}},
        )


def test_category_group_is_read_only_when_template_choices_are_unavailable():
    published = build_item_operator_object(
        item=_item(),
        workflow_card=_workflow_card(),
        category_context={
            "item_condition_required": True,
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )

    descriptor = published["field_schema"]["item_fields"]["category_group"]
    save_command = next(
        command for command in published["commands"]
        if command["id"] == "save-inventory"
    )
    save_fields = save_command["input_schema"]["properties"]["item_fields"]["properties"]

    assert descriptor["readonly"] is True
    assert descriptor["options"] == []
    assert descriptor["hint"] == "No category group templates are published."
    assert "category_group" not in save_fields


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
    assert {
        command_id: commands[command_id]["enabled"]
        for command_id in (
            "update-item",
            "end-listing",
            "reidentify",
            "archive-item",
            "delete-item",
        )
    } == {
        "update-item": True,
        "end-listing": True,
        "reidentify": True,
        "archive-item": True,
        "delete-item": True,
    }
    assert commands["end-listing"]["authority_scope"] == "publication-withdrawal"


def test_local_and_provider_media_are_published_as_distinct_ordered_sets():
    local_media = [
        {
            "kind": "image",
            "name": f"local-{position}.jpg",
            "url": f"/media/sku-1/local-{position}.jpg",
            "position": position,
            "primary": position == 0,
        }
        for position in range(6)
    ]
    provider_urls = [
        f"https://i.ebayimg.com/images/g/provider-{position}/s-l1600.jpg"
        for position in range(5)
    ]
    item = _item() | {
        "ebay_live": {
            "inventory_item": {"product": {"imageUrls": provider_urls}},
        },
        # Lower-precedence evidence must not replace the authoritative live order.
        "ebay_photos": ["https://example.invalid/stale.jpg"],
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context=_category_context(),
        media=local_media,
    )

    assert len(published["item"]["media"]) == 6
    provider = published["item"]["provider_media"]
    assert provider["source"] == "ebay-live"
    assert provider["status"] == "ready"
    assert provider["count"] == 5
    assert [entry["url"] for entry in provider["items"]] == provider_urls
    assert [entry["position"] for entry in provider["items"]] == list(range(5))


def test_aspects_keep_custom_empty_values_and_publish_comparison_context():
    item = _item() | {
        "item_attributes": {
            "Brand": "Inventory Brand",
            "Inventory only": "Warehouse value",
        },
        "ebay_live": {
            "inventory_item": {
                "product": {
                    "aspects": {
                        "Brand": ["Live Brand"],
                        "Legacy Custom": ["Live custom value"],
                    },
                },
            },
        },
        "revision_draft": {
            "delta": {
                "item_specifics": {
                    "Brand": "Proposed Brand",
                    "Legacy Custom": "",
                },
            },
        },
    }
    item["draft_listing"] = item["draft_listing"] | {
        "item_specifics": {
            "Brand": "Draft Brand",
            # An empty stored value is an editable clear, not a missing field.
            "Legacy Custom": "",
        },
    }

    published = build_item_operator_object(
        item=item,
        workflow_card=_workflow_card(),
        category_context=_category_context(),
    )
    aspects = {aspect["name"]: aspect for aspect in published["field_schema"]["aspects"]}

    assert aspects["Brand"] == {
        "name": "Brand",
        "required": True,
        "mode": "FREE_TEXT",
        "allowed_values": [],
        "value": "Draft Brand",
        "inventory_value": "Inventory Brand",
        "live_value": "Live Brand",
        "proposed_value": "Proposed Brand",
        "category_defined": True,
        "custom": False,
    }
    assert aspects["Legacy Custom"] == {
        "name": "Legacy Custom",
        "required": False,
        "mode": "FREE_TEXT",
        "allowed_values": [],
        "value": "",
        "inventory_value": None,
        "live_value": "Live custom value",
        "proposed_value": "",
        "category_defined": False,
        "custom": True,
    }
    assert validate_operator_command_values(
        published,
        "update-item",
        {"draft_listing": {"item_specifics": {"Legacy Custom": ""}}},
    ) == {"draft_listing": {"item_specifics": {"Legacy Custom": ""}}}


def test_photo_reorder_is_a_unique_string_list_command():
    media = [
        {
            "kind": "image",
            "name": name,
            "url": f"/media/sku-1/{name}",
            "position": position,
            "primary": position == 0,
        }
        for position, name in enumerate(("front.jpg", "side.jpg", "label.jpg"))
    ]
    published = build_item_operator_object(
        item=_item(),
        workflow_card=_workflow_card(),
        category_context=_category_context(),
        media=media,
    )
    command = next(
        command for command in published["commands"] if command["id"] == "reorder-photos"
    )

    assert command["enabled"] is True
    assert command["value_source"] == "media-order"
    assert command["input_schema"]["properties"]["order"] == {"type": "string-list"}
    assert validate_operator_command_values(
        published,
        "reorder-photos",
        {"order": ["label.jpg", "front.jpg", "side.jpg"]},
    ) == {"order": ["label.jpg", "front.jpg", "side.jpg"]}
    with pytest.raises(OperatorObjectBindingError, match="unique non-empty strings"):
        validate_operator_command_values(
            published,
            "reorder-photos",
            {"order": ["front.jpg", "front.jpg"]},
        )


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
        {
            "draft_listing": {
                "condition_enum": "USED_GOOD",
                "item_specifics": {"Brand": "TGW"},
                "price": 1.0,
            }
        },
    ) == {
        "draft_listing": {
            "condition_enum": "USED_GOOD",
            "item_specifics": {"Brand": "TGW"},
            "price": 1.0,
        }
    }
    with pytest.raises(OperatorObjectBindingError, match="unpublished"):
        validate_operator_command_values(published, "update-item", {"price": "1.00"})
    with pytest.raises(OperatorObjectBindingError, match="allowed value"):
        validate_operator_command_values(
            published,
            "list-item",
            {"draft_listing": {"condition_enum": "invented"}},
        )
