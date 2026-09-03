from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

import tgw.http_server as http_server
from tgw.operator_objects import build_item_operator_object


def _workflow_card() -> dict:
    return {
        "entity_id": "sku-1",
        "object_generation": "gen-1",
        "graph_id": "graph-1",
        "operator_projection": {
            "state": "held",
            "commands": {
                "save-draft": {"enabled": True, "reason": None},
                "list-item": {"enabled": False, "reason": "invalid condition"},
                "update-item": {"enabled": False, "reason": "not listed"},
            },
        },
    }


def _optional_category_object() -> dict:
    return build_item_operator_object(
        item={
            "sku": "sku-1",
            "condition": "Very Good",
            "draft_listing": {
                "category_id": "108857",
                "condition_enum": "USED_GOOD",
                "condition_label": "Good",
                "item_specifics": {},
            },
        },
        workflow_card=_workflow_card(),
        category_context={
            "item_condition_required": False,
            "conditions": [],
            "aspects": [],
        },
    )


def test_save_rejects_nonempty_condition_for_resolved_optional_category(
    tmp_path, monkeypatch
):
    published = _optional_category_object()
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: published,
    )
    monkeypatch.setattr(
        http_server,
        "ebay_category_context",
        lambda *args, **kwargs: {
            "item_condition_required": False,
            "conditions": [],
        },
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: pytest.fail("invalid condition must not be written"),
    )

    with pytest.raises(http_server.HTTPException) as exc_info:
        http_server.execute_item_operator_command(
            "sku-1",
            http_server.OperatorCommandBody(
                command_id="save-listing-draft",
                object_generation="gen-1",
                values={"draft_listing": {"condition_enum": "USED_GOOD"}},
            ),
            operator_identity="operator:test",
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "condition is not valid for the selected category"


def test_save_holds_cached_choices_without_requirement_flag(tmp_path, monkeypatch):
    published = _optional_category_object()
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: published,
    )
    monkeypatch.setattr(
        http_server,
        "ebay_category_context",
        lambda *args, **kwargs: {
            "item_condition_required": None,
            "conditions": [{"enum": "USED_GOOD", "label": "Good"}],
        },
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: pytest.fail("unresolved condition must not be written"),
    )

    with pytest.raises(http_server.HTTPException) as exc_info:
        http_server.execute_item_operator_command(
            "sku-1",
            http_server.OperatorCommandBody(
                command_id="save-listing-draft",
                object_generation="gen-1",
                values={"draft_listing": {"condition_enum": "USED_GOOD"}},
            ),
            operator_identity="operator:test",
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "condition policy is unresolved for the selected category"


def test_browser_projection_requires_boolean_requirement_flag():
    source = (
        Path(__file__).parents[1] / "src/tgw/static/operator_item.html"
    ).read_text(encoding="utf-8")

    assert 'policyResolved=typeof context.item_condition_required==="boolean"' in source
    assert "policy_resolved:policyResolved" in source
    assert 'policyHeld=condition.policy_resolved===false' in source
    assert (
        'typeof context.item_condition_required!=="boolean"?'
        '" · condition policy unresolved"' in source
    )
    effective_legacy = http_server._CATEGORY_CONTEXT_IIFE
    assert "var policyResolved=typeof d.item_condition_required==='boolean'" in effective_legacy
    assert "sel.disabled=!policyResolved" in effective_legacy
    assert "Condition policy unresolved — retry" in effective_legacy
    assert "condition auto-matched" not in effective_legacy


def test_operator_can_explicitly_clear_condition_for_optional_category(
    tmp_path, monkeypatch
):
    published = _optional_category_object()
    refreshed = deepcopy(published)
    refreshed["object_generation"] = "gen-2"
    writes = []
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if writes else published,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda path, sku, fields, **kwargs: (
            writes.append(fields)
            or SimpleNamespace(status="COMMITTED", resulting_generation="gen-2")
        ),
    )

    result = http_server.execute_item_operator_command(
        "sku-1",
        http_server.OperatorCommandBody(
            command_id="save-listing-draft",
            object_generation="gen-1",
            values={"draft_listing": {"condition_enum": ""}},
        ),
        operator_identity="operator:test",
    )

    assert result["ok"] is True
    assert writes == [{"draft_listing": {"condition_enum": ""}}]


def test_provider_preflight_never_silently_clears_or_remaps_condition(
    tmp_path, monkeypatch
):
    item_path = tmp_path / "sku-1.json"
    document = {
        "sku": "sku-1",
        "draft_listing": {
            "category_id": "108857",
            "condition_enum": "USED_GOOD",
        },
    }
    item_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(http_server, "_cfg", {})
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.allowed_conditions_for_category",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.item_condition_required_for_category",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.best_condition_for_enum",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_patch",
        lambda *args, **kwargs: pytest.fail("preflight must not mutate condition"),
    )

    with pytest.raises(http_server.HTTPException) as exc_info:
        http_server._normalize_draft_condition_for_provider(item_path)

    assert exc_info.value.status_code == 409
    assert "choose the blank option" in exc_info.value.detail
    assert json.loads(item_path.read_text(encoding="utf-8")) == document


def test_provider_preflight_holds_matching_choice_when_flag_is_missing(
    tmp_path, monkeypatch
):
    item_path = tmp_path / "sku-1.json"
    document = {
        "sku": "sku-1",
        "draft_listing": {
            "category_id": "1105",
            "condition_enum": "USED_GOOD",
        },
    }
    item_path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(http_server, "_cfg", {})
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.item_condition_required_for_category",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.allowed_conditions_for_category",
        lambda *args, **kwargs: [{"condition_enum": "USED_GOOD"}],
    )
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.best_condition_for_enum",
        lambda *args, **kwargs: pytest.fail("unresolved policy must not suggest a remap"),
    )

    with pytest.raises(http_server.HTTPException) as exc_info:
        http_server._normalize_draft_condition_for_provider(item_path)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "condition policy is unresolved for the selected category"
    assert json.loads(item_path.read_text(encoding="utf-8")) == document


def test_http_projection_uses_selected_tgw_group_union(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.inventory_conditions_for_categories",
        lambda cfg, categories: (
            seen.append(list(categories))
            or [
                {"value": "New", "label": "New"},
                {"value": "Very Good", "label": "Very Good"},
            ]
        ),
    )

    context = http_server._category_group_inventory_condition_context(
        {"catalog_root": "/unused"},
        {"category_group": "books"},
        groups={"books": {"ebay_categories": ["261186", "1105"]}},
    )

    assert seen == [["261186", "1105"]]
    assert context == {
        "inventory_condition_group": "books",
        "inventory_conditions": [
            {"value": "New", "label": "New"},
            {"value": "Very Good", "label": "Very Good"},
        ],
    }


def test_inventory_condition_projection_fails_closed_without_hiding_item(
    monkeypatch,
):
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.inventory_conditions_for_categories",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("cache offline")),
    )

    context = http_server._category_group_inventory_condition_context(
        {},
        {"category_group": "books"},
        groups={"books": {"ebay_categories": ["1105"]}},
    )

    assert context["inventory_condition_group"] == "books"
    assert context["inventory_conditions"] == []
    assert context["inventory_conditions_error"] == "cache offline"
