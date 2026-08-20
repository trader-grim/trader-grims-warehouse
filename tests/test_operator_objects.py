from __future__ import annotations

import copy

import pytest

from tgw.operator_objects import (
    ADAPTER_VIEW_SCHEMA,
    OPERATOR_OBJECT_SCHEMA,
    OperatorObjectBindingError,
    flutter_adapter_view,
    publish_operator_object,
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
