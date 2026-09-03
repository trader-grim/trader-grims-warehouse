from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tgw import http_server, inventory_record
from tgw.ebay.draft_specifics import get_ebay_aspects
from tgw.item_mutation import item_generation
from tgw.operator_objects import build_item_operator_object
from tgw.workflow import listing_migration

AUTH = {"Authorization": "Bearer operator-object-test"}


def _card():
    return {
        "entity_id": "sku-1",
        "object_generation": "gen-1",
        "graph_id": "graph-1",
        "fingerprints": [],
        "attempts": [],
        "active_attempts": [],
        "reconciliation_gates": [],
        "ownership_conflicts": [],
        "operator_gates": [],
        "legal_actions": [
            {
                "treatment_id": "ebay-publish",
                "treatment_version": "1",
                "effect_class": "external",
                "action": "held_external_contract",
                "reasons": [],
            },
        ],
        "operator_projection": {
            "state": "staged",
            "reasons": [],
            "commands": {
                "save-draft": {"enabled": True, "reason": None},
                "list-item": {"enabled": True, "reason": None},
                "update-item": {"enabled": True, "reason": None},
            },
        },
    }


def _published():
    return build_item_operator_object(
        item={
            "sku": "sku-1",
            "draft_listing": {
                "title": "Thing",
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "condition_label": "Used - Good",
                "item_specifics": {"Brand": "TGW"},
            },
            "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
        },
        workflow_card=_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )


def _published_for_document(document):
    card = _card()
    card["object_generation"] = item_generation(document)
    return build_item_operator_object(
        item=document,
        workflow_card=card,
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )


def _published_with_enabled_publication_commands(published):
    """Return the published operator object with list/update commands enabled.

    The builder disables listing commands while the item condition policy is
    unresolved (no category context supplied); tests that exercise the command
    endpoint past the enabled gate supply a resolved policy through
    ``ebay_category_context`` and enable the commands here so the dispatch
    path under test is reached.
    """
    for command in published["commands"]:
        if command["id"] in {"list-item", "update-item"}:
            command["enabled"] = True
            command["reason"] = None
    return published


def _resolved_category_context(category_id, current_condition):
    """Minimal resolved condition policy for the command endpoint path."""
    return {
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
        "item_condition_required": True,
    }


def _complete_listing_values(published, **overrides):
    fields = {
        name: json.loads(json.dumps(descriptor.get("value")))
        for name, descriptor in published["field_schema"]["listing_fields"].items()
    }
    condition = published["field_schema"].get("condition", {})
    if "condition_enum" in (
        next(
            command for command in published["commands"]
            if command["id"] in {"list-item", "update-item"}
        )["input_schema"]["properties"]["draft_listing"]["properties"]
    ):
        fields["condition_enum"] = condition.get("value")
    fields.update(overrides)
    return {"draft_listing": fields}


def _write_operator_item(root, document):
    sku = document["sku"]
    path = root / sku / f"{sku}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _operator_test_config(tmp_path):
    return {
        "itemdata_root": tmp_path / "items",
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "mutations",
        "sqlite_catalog_path": tmp_path / "catalog.sqlite",
        "location_tree_root": tmp_path / "locations",
        "pretty": True,
    }


def test_sparse_listing_patch_preserves_untouched_inventory_specifics():
    document = {
        "sku": "sku-1",
        "item_attributes": {"Color": "Blue", "Material": "Wood"},
        "item_attributes_history": [],
        "draft_listing": {
            "price": 10.0,
            "item_specifics": {"Color": "", "Material": "Wood"},
        },
    }

    patched = http_server._operator_patch_document(
        document,
        {"draft_listing": {"price": 12.0}},
        operator_identity="operator-test",
    )

    assert inventory_record.get_inventory_fields(patched) == {
        "Color": "Blue",
        "Material": "Wood",
    }
    assert patched["item_attributes"] == document["item_attributes"]
    assert patched["item_attributes_history"] == []
    assert get_ebay_aspects(patched) == {"Color": "", "Material": "Wood"}


def test_explicit_specific_clear_syncs_only_the_changed_inventory_field():
    document = {
        "sku": "sku-1",
        "item_attributes": {"Color": "Blue", "Material": "Wood"},
        "item_attributes_history": [],
        "draft_listing": {
            "item_specifics": {"Color": "Blue", "Material": "Wood"},
        },
    }

    patched = http_server._operator_patch_document(
        document,
        {"draft_listing": {"item_specifics": {"Color": ""}}},
        operator_identity="operator-test",
    )

    assert get_ebay_aspects(patched) == {"Color": "", "Material": "Wood"}
    assert inventory_record.get_inventory_fields(patched) == {
        "Color": "",
        "Material": "Wood",
    }
    assert [
        (entry["key"], entry["value"], entry["previous_value"])
        for entry in patched["item_attributes_history"]
    ] == [("Color", "", "Blue")]


@pytest.mark.parametrize(
    "detail,changed_field,new_value",
    [
        ("Invalid value for categoryId.", "category_id", "456"),
        ("Invalid value for conditionId.", "condition_enum", "USED_GOOD"),
    ],
)
def test_attributed_provider_rejection_clears_only_after_its_field_changes(
    detail,
    changed_field,
    new_value,
):
    document = {
        "sku": "sku-1",
        "pipeline_error": {
            "code": "ebay_rejected",
            "detail": detail,
            "source": "ebay_stage",
        },
        "draft_listing": {
            "category_id": "123",
            "condition_enum": "USED_ACCEPTABLE",
            "price": 10.0,
        },
    }
    unrelated = http_server._operator_patch_document(
        document,
        {"draft_listing": {"price": 12.0}},
        operator_identity="operator-test",
    )
    assert unrelated["pipeline_error"] == document["pipeline_error"]

    corrected = http_server._operator_patch_document(
        document,
        {"draft_listing": {changed_field: new_value}},
        operator_identity="operator-test",
    )
    assert "pipeline_error" not in corrected


def test_get_operator_object_mounts_real_http_contract_without_live_taxonomy(
    tmp_path,
    monkeypatch,
):
    item_dir = tmp_path / "sku-1"
    item_dir.mkdir()
    (item_dir / "sku-1.json").write_text(
        json.dumps(
            {
                "sku": "sku-1",
                "draft_listing": {
                    "category_id": "123",
                    "condition_enum": "USED_GOOD",
                    "condition_label": "Used - Good",
                    "item_specifics": {},
                },
                "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
            }
        ),
        encoding="utf-8",
    )
    (item_dir / "front view #1.jpg").write_bytes(b"jpeg-one")
    (item_dir / "rear.jpg").write_bytes(b"jpeg-two")
    (item_dir / "inspection clip.mp4").write_bytes(b"video-one")
    (item_dir / ".hidden.jpg").write_bytes(b"hidden")
    (item_dir / ".hidden.webm").write_bytes(b"hidden-video")
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_workflow_attempt_rows", lambda sku: [])
    monkeypatch.setattr(http_server, "_workflow_reconciled_provider_effect_ids", lambda rows: frozenset())
    monkeypatch.setattr(
        http_server,
        "ebay_category_context",
        lambda *args, **kwargs: {
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )
    from tgw.apis.ebay import taxonomy

    def reject_taxonomy_call(*args, **kwargs):
        raise AssertionError("item-object rendering must not call taxonomy providers")

    for provider_name in (
        "ebay_get",
        "get_category_tree_id",
        "get_category_suggestions",
        "_fetch_tree_live",
        "refresh_category_tree_cache",
        "search_categories_local",
        "get_category_node",
        "get_category_children",
    ):
        monkeypatch.setattr(taxonomy, provider_name, reject_taxonomy_call)

    response = TestClient(http_server.app).get("/api/operator/items/sku-1", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()["object"]
    assert payload["schema"] == "tgw-operator-object/v1"
    assert len(payload["object_generation"]) == 64
    assert [command["id"] for command in payload["commands"]] == [
        "save-inventory",
        "save-listing-draft",
        "list-item",
        "update-item",
        "reidentify",
        "reprice-item",
        "reorder-photos",
        "resync-photos",
        "sync-from-ebay",
        "reset-draft-from-live",
        "end-listing",
        "mark-sold",
        "archive-item",
        "delete-item",
    ]
    assert payload["item"]["media"] == [
        {
            "kind": "image",
            "name": "front view #1.jpg",
            "url": "/media/sku-1/front%20view%20%231.jpg",
            "position": 0,
            "primary": False,
        },
        {
            "kind": "image",
            "name": "rear.jpg",
            "url": "/media/sku-1/rear.jpg",
            "position": 1,
            "primary": False,
        },
        {
            "kind": "video",
            "name": "inspection clip.mp4",
            "url": "/media/sku-1/inspection%20clip.mp4",
            "position": 2,
            "primary": False,
        },
    ]
    assert payload["item"]["media_status"] == {"state": "ready", "reason": None}


def test_operator_object_media_uses_canonical_primary_and_survives_storage_error(
    tmp_path, monkeypatch,
):
    item_dir = tmp_path / "sku-1"
    item_dir.mkdir()
    (item_dir / "sku-1.json").write_text(
        json.dumps(
            {
                "sku": "sku-1",
                "image": "rear.jpg",
                "draft_listing": {
                    "category_id": "123",
                    "condition_enum": "USED_GOOD",
                    "item_specifics": {},
                },
            }
        ),
        encoding="utf-8",
    )
    (item_dir / "front.jpg").write_bytes(b"front")
    (item_dir / "rear.jpg").write_bytes(b"rear")
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_workflow_attempt_rows", lambda sku: [])
    monkeypatch.setattr(http_server, "_workflow_reconciled_provider_effect_ids", lambda rows: frozenset())
    monkeypatch.setattr(
        http_server,
        "ebay_category_context",
        lambda *args, **kwargs: {
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )
    client = TestClient(http_server.app)
    response = client.get("/api/operator/items/sku-1", headers=AUTH)
    assert response.status_code == 200
    media = response.json()["object"]["item"]["media"]
    assert [entry["position"] for entry in media] == [0, 1]
    assert [entry["name"] for entry in media if entry["primary"]] == ["rear.jpg"]

    import tgw.assets

    monkeypatch.setattr(
        tgw.assets,
        "primary_photo",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("denied")),
    )
    unavailable = client.get("/api/operator/items/sku-1", headers=AUTH)
    assert unavailable.status_code == 200
    item_view = unavailable.json()["object"]["item"]
    assert item_view["media"] == []
    assert item_view["media_status"] == {
        "state": "unavailable",
        "reason": "Media could not be read from item storage.",
    }


def test_thin_web_item_page_uses_only_published_object_and_command_contract(monkeypatch):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    client = TestClient(http_server.app)
    http_server._sessions["operator-object-browser"] = float("inf")
    client.cookies.set("tgw_session", "operator-object-browser")
    response = client.get("/form/operator/items/sku-1")
    assert response.status_code == 200
    assert "/api/operator/items/" in response.text
    assert "data-command" in response.text
    assert "mediaGallery(object)" in response.text
    assert 'data-media-url="${esc(entry.url)}"' in response.text
    assert '<video controls preload="metadata"' in response.text
    assert "saveValues(command)" in response.text
    assert "workflowValues(command)" in response.text
    assert 'data-view="${esc(view.id)}"' in response.text
    assert "tgw-item-presentation/v1" in response.text
    assert "const componentRegistry=" in response.text
    assert '"provider-media":{render:providerMedia}' in response.text
    assert '"attention-banner":{render:renderAttention}' in response.text
    assert "object.presentation?.attention" in response.text
    assert "normaliseRegions(view)" in response.text
    assert "view.components" not in response.text
    assert 'data-region="${esc(region.id)}"' in response.text
    assert 'data-layout="${esc(layout)}"' in response.text
    assert "object.presentation?.action_menus" in response.text
    assert "menu.command_ids" in response.text
    assert "menu.default_command_id" in response.text
    assert "menu.commands.find(command=>command.id===menu.default_command_id)" in response.text
    assert "<select data-action-selector" in response.text
    assert '<button type="button" class="action-execute ' in response.text
    assert "data-action-tone" in response.text
    assert ">Execute</button>" in response.text
    assert "action-selection-reason" in response.text
    assert 'document.querySelectorAll("[data-action-selector]")' in response.text
    assert 'select.addEventListener("change",sync)' in response.text
    assert 'execute.dataset.command=option?.value||""' in response.text
    assert '<details class="action-menu">' not in response.text
    assert "data-command-region" in response.text
    assert "object.presentation?.data_navigation" in response.text
    assert 'aria-label="Item data sections"' in response.text
    assert "pricing.details_target" in response.text
    assert "pricing_context?.details_sections" in response.text
    assert "pricing.research_links" in response.text
    assert "data-pricing-search" in response.text
    assert (
        "publishedCategoryFields="
        "object.presentation?.listing_editor?.category_fields"
        in response.text
    )
    assert "categoryEntries=categoryNames.map(name=>[name,fields[name]])" in response.text
    assert "categorySet=new Set(categoryEntries.map(([name])=>name))" in response.text
    assert "let categoryWorkbenchRendered=false" in response.text
    assert (
        '<div class="category-workbench">'
        '${categoryEntries.map(([name,spec])=>inputControl('
        '"draft_listing",name,spec)).join("")}</div>'
        in response.text
    )
    assert (
        "publishedPricingFields=object.presentation?.listing_editor?.pricing_fields"
        in response.text
    )
    assert (
        "publishedPricingFooterFields="
        "object.presentation?.listing_editor?.pricing_footer_fields"
        in response.text
    )
    assert "pricingEntries=pricingNames.map(name=>[name,fields[name]])" in response.text
    assert "pricingSet=new Set(pricingEntries.map(([name])=>name))" in response.text
    assert (
        "pricingFooterEntries=pricingFooterNames.map(name=>[name,fields[name]])"
        in response.text
    )
    assert (
        "pricingFooterSet=new Set(pricingFooterEntries.map(([name])=>name))"
        in response.text
    )
    assert "workbenchSet=new Set([...pricingSet,...pricingFooterSet])" in response.text
    assert "let workbenchRendered=false" in response.text
    assert (
        'if(workbenchRendered)return "";workbenchRendered=true;return workbenchHtml'
        in response.text
    )
    assert (
        'if(categorySet.has(name)){if(categoryWorkbenchRendered)return "";'
        "categoryWorkbenchRendered=true;return categoryWorkbenchHtml}"
        'if(workbenchSet.has(name)){if(workbenchRendered)return "";'
        "workbenchRendered=true;return workbenchHtml}"
        in response.text
    )
    assert (
        '<div class="price-workbench"><div class="pricing-workbench-fields">'
        "${pricingFieldHtml}</div>${pricingQuickTools(object,hintedSpec)}"
        '${pricingFooterHtml?`<div class="pricing-workbench-footer">'
        '${pricingFooterHtml}</div>`:""}</div>'
        in response.text
    )
    assert (
        'pricingEntries.find(([,spec])=>spec.control==="money"&&spec.hint)'
        in response.text
    )
    assert "name===hintedName?{...spec,hint:null}:spec" in response.text
    quick_tools_source = response.text[
        response.text.index("function pricingQuickTools"):
        response.text.index("function listingEditor")
    ]
    search_row = quick_tools_source.index('<div class="pricing-tool-row">')
    moved_hint = quick_tools_source.index(
        '<div class="pricing-moved-hint">${hintHtml}</div>'
    )
    research_links = quick_tools_source.index(
        '<div class="pricing-research-links">${linkHtml}${detailsHtml}</div>'
    )
    assert search_row < moved_hint < research_links
    listing_editor_source = response.text[
        response.text.index("function listingEditor"):
        response.text.index("function saveValues")
    ]
    for field_name in (
        "category_id",
        "secondary_category_id",
        "store_category_id",
        "secondary_store_category_id",
        "price",
        "quantity",
        "best_offer_enabled",
        "best_offer_auto_accept_price",
        "best_offer_auto_decline_price",
    ):
        assert f'"{field_name}"' not in listing_editor_source
    assert "function pricingValues" in response.text
    assert 'command.value_source==="media-order"' in response.text
    assert "function mediaOrderValues" in response.text
    assert "submitCommand(command,{order})" in response.text
    assert 'none:()=>({})' in response.text
    assert "confirmationMessage(command)" in response.text
    assert 'data-search-endpoint="${esc(lookup.search_endpoint' in response.text
    assert 'data-context-endpoint="${esc(lookup.context_endpoint' in response.text
    assert 'data-node-endpoint="${esc(lookup.node_endpoint' in response.text
    assert 'data-browse-endpoint="${esc(lookup.browse_endpoint' in response.text
    assert "function categoryEndpointValues" in response.text
    assert "current_condition:currentCondition" in response.text
    assert "resolveCategoryId(control,categoryId)" in response.text
    assert 'loadCategoryChildren(control,parentId="")' in response.text
    assert "data-category-browse-toggle" in response.text
    assert "data-category-browse-panel" in response.text
    category_control_source = response.text[
        response.text.index("function categorySearchControl"):
        response.text.index("function inputControl")
    ]
    assert 'function taxonomyPath(path,label="")' in response.text
    assert "function selectionSummary(selection={},fallbackValue=\"\",taxonomy=false)" in response.text
    assert "function updateSelectionDisplay" in response.text
    assert "data-selection-display" in response.text
    assert "data-selection-label" in response.text
    assert "data-selection-value" in response.text
    assert "data-selection-select" in response.text
    assert "updateCategorySelection(control,categoryId,label=\"\",path=\"\")" in response.text
    assert "selectCategory(control,categoryId,label=\"\",path=\"\")" in response.text
    assert "refreshCategoryContext(control,categoryId,label=\"\",path=\"\")" in response.text
    assert "data-category-path" in response.text
    assert "spec.selection" in category_control_source
    assert "selection.value" in category_control_source
    assert "selection.label" in category_control_source
    assert "selection.path" in category_control_source
    for field_name in (
        "category_id",
        "secondary_category_id",
        "store_category_id",
        "secondary_store_category_id",
    ):
        assert f'"{field_name}"' not in category_control_source
    assert "const normalised=normaliseOptions(options)" in response.text
    assert 'if(options.length||spec.control==="select")' in response.text
    assert "minimumQueryLength" in response.text
    assert "aspect.inventory_value" in response.text
    assert "aspect.live_value" in response.text
    assert "aspect.proposed_value" in response.text
    assert "aspect.custom" in response.text
    assert 'aspect.mode==="SELECTION_ONLY"' in response.text
    assert "spec?.attention" in response.text
    assert "fieldAttention(spec)" in response.text
    assert "header.statuses" in response.text
    assert 'command.value_semantics==="sparse-patch"' in response.text
    assert "data-initial-value" in response.text
    assert "sameValue(value,initialValue(element))" in response.text
    assert "sameValue(mapped,initialValue(editor))" in response.text
    assert "sameValue(element.value,initialValue(element))" in response.text
    assert "initial_value:initialValue(element)" in response.text
    assert "initial=priorControl?initialValue(priorControl)" in response.text
    assert '<details class="alert ' in response.text
    assert "<summary><strong>${esc(alert.title)}</strong>" in response.text
    assert "alert-expanded" in response.text
    assert 'anchor=section.id?` id="${esc(section.id)}"`' in response.text
    assert 'anchor=pricing.id?` id="${esc(pricing.id)}"`' in response.text
    assert "<details${anchor}" in response.text
    assert "<section${anchor}" in response.text
    assert "header.lead" in response.text
    assert "header.trail" in response.text
    assert "header.status" in response.text
    assert "publishedBreadcrumbs=Array.isArray(presentation.breadcrumbs)?presentation.breadcrumbs:[]" in response.text
    assert "breadcrumbs=publishedBreadcrumbs.length?publishedBreadcrumbs:" in response.text
    assert "breadcrumbs.map((entry,index)=>" in response.text
    assert "safeHref(href)" in response.text
    assert "object.presentation?.listing_editor?.id" in response.text
    assert 'editorId?` id="${esc(editorId)}"`' in response.text
    assert '<section${anchor} class="card editor-card listing-editor">' in response.text
    assert "${viewNav(object.presentation?.views||[])}" in response.text
    assert '<div class="item-chrome">${itemHeader(object,view)}</div>' in response.text
    static_chrome = response.text.rfind(".item-chrome{position:static")
    assert static_chrome >= 0
    assert ".item-chrome{position:sticky" not in response.text
    assert ".item-header{position:sticky" not in response.text
    navigation_row = (
        '<div class="header-row header-row-navigation"><div class="breadcrumbs">'
        '${breadcrumbHtml}</div>${viewNav(object.presentation?.views||[])}</div>'
    )
    summary_row = '<div class="header-row header-row-summary"><div class="header-lead">'
    assert navigation_row in response.text
    assert summary_row in response.text
    assert response.text.index(navigation_row) < response.text.index(summary_row)
    assert '<div class="header-trail">${trailHtml}</div>' in response.text
    assert "const contents=regions.map(region=>renderRegion(object,region,sections,view)).join(\"\")" in response.text
    assert "duplicateActionRegions" not in response.text
    assert "topActions" not in response.text
    assert "bottomActions" not in response.text
    assert "command.group" not in response.text
    assert "Listing capabilities" not in response.text
    assert 'view.id==="overview"' not in response.text
    assert 'view.id==="listing"' not in response.text
    assert 'selectedView==="listing"' not in response.text
    assert "winchestermysterykitchen" not in response.text
    assert "Listing actions" not in response.text
    assert "Listing tools" not in response.text
    assert "TGW item actions" not in response.text
    assert "More item actions" not in response.text
    assert "Pricing & comps" not in response.text
    assert "/api/ebay/category-search" not in response.text
    assert "/api/ebay/category-context" not in response.text
    assert "/api/ebay/category-node" not in response.text
    assert "/api/ebay/category-children" not in response.text
    for field_name in (
        "store_category_id",
        "shipping_profile",
        "return_policy_id",
    ):
        assert f'"{field_name}"' not in response.text
    assert 'fetch("/api/items/' not in response.text
    for command_id in (
        "save-inventory",
        "save-listing-draft",
        "list-item",
        "update-item",
        "reidentify",
        "reprice-item",
        "reorder-photos",
        "resync-photos",
        "sync-from-ebay",
        "reset-draft-from-live",
        "mark-sold",
        "archive-item",
        "delete-item",
        "end-listing",
    ):
        assert f'"{command_id}"' not in response.text
    assert "Provider state</h2><pre>" not in response.text
    assert "JSON.stringify({provider_state" not in response.text
    assert "ebay_publish" not in response.text
    assert "triggerAction" not in response.text
    for policy_text in (
        "Category 99",
        "assignable leaf",
        "not valid for eBay category",
        "Publication held",
    ):
        assert policy_text not in response.text


def test_command_rejects_stale_generation_before_dispatch(monkeypatch):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: _published())

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={"command_id": "list-item", "object_generation": "stale", "values": {}},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "generation_conflict"


def test_list_command_atomically_saves_full_editor_before_exact_dispatch(
    tmp_path,
    monkeypatch,
):
    config = _operator_test_config(tmp_path)
    path = _write_operator_item(
        config["itemdata_root"],
        {
            "sku": "sku-1",
            "title": "Old title",
            "draft_listing": {
                "title": "Old title",
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "condition_label": "Used - Good",
                "price": 10.0,
                "quantity": 1,
                "item_specifics": {"Brand": "TGW"},
            },
            "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
        },
    )
    monkeypatch.setattr(http_server, "_cfg", config)
    monkeypatch.setattr(
        http_server,
        "_api_key",
        AUTH["Authorization"].removeprefix("Bearer "),
    )
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: _published_with_enabled_publication_commands(
            _published_for_document(
                json.loads(path.read_text(encoding="utf-8"))
            )
        ),
    )
    monkeypatch.setattr(http_server, "ebay_category_context", _resolved_category_context)
    monkeypatch.setattr(http_server, "_workflow_provider_identity", lambda: "ebay:test")
    dispatched = {}

    def dispatch(bound_path, **kwargs):
        document = json.loads(bound_path.read_text(encoding="utf-8"))
        generation = item_generation(document)
        assert kwargs["expected_generation"] == generation
        assert document["draft_listing"]["title"] == "Edited title"
        assert document["draft_listing"]["price"] == 42.5
        assert document["draft_listing"]["quantity"] == 3
        assert document["draft_listing"]["category_id"] == "123"
        assert document["draft_listing"]["condition_enum"] == "USED_GOOD"
        assert get_ebay_aspects(document) == {"Brand": "TGW"}
        dispatched.update(kwargs)
        result = SimpleNamespace(
            graph=SimpleNamespace(
                graph_id="graph-edited",
                object_generation=generation,
            ),
            held_external=(),
            operator_gates=(),
        )
        return (
            result,
            SimpleNamespace(enqueued=True, job_id="job-edited"),
            "authority-edited",
            True,
        )

    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        dispatch,
    )
    initial_generation = item_generation(json.loads(path.read_text(encoding="utf-8")))
    published_editor = _published_for_document(
        json.loads(path.read_text(encoding="utf-8"))
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "list-item",
            "object_generation": initial_generation,
            "values": _complete_listing_values(
                published_editor,
                title="Edited title",
                price=42.5,
                quantity=3,
            ),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "job-edited"
    assert dispatched["surface"] == "http:operator-object:list-item"


def test_operator_command_cas_refuses_race_without_overwriting_winner(
    tmp_path,
    monkeypatch,
):
    config = _operator_test_config(tmp_path)
    path = _write_operator_item(
        config["itemdata_root"],
        {
            "sku": "sku-1",
            "title": "Original",
            "draft_listing": {
                "title": "Original",
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "item_specifics": {},
            },
        },
    )
    monkeypatch.setattr(http_server, "_cfg", config)
    monkeypatch.setattr(
        http_server,
        "_api_key",
        AUTH["Authorization"].removeprefix("Bearer "),
    )
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: _published_for_document(
            json.loads(path.read_text(encoding="utf-8"))
        ),
    )
    import tgw.operator_objects as operator_objects

    validate = operator_objects.validate_operator_command_values
    raced = False

    def race_after_validation(published, command_id, values):
        nonlocal raced
        checked = validate(published, command_id, values)
        if not raced:
            winner = json.loads(path.read_text(encoding="utf-8"))
            winner["concurrent_winner"] = True
            path.write_text(json.dumps(winner), encoding="utf-8")
            raced = True
        return checked

    monkeypatch.setattr(
        operator_objects,
        "validate_operator_command_values",
        race_after_validation,
    )

    initial_generation = item_generation(json.loads(path.read_text(encoding="utf-8")))
    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-inventory",
            "object_generation": initial_generation,
            "values": {"item_fields": {"title": "Stale overwrite"}},
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "generation_conflict"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["title"] == "Original"
    assert persisted["concurrent_winner"] is True


def test_legacy_publication_and_bulk_list_paths_are_not_registered(monkeypatch):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    client = TestClient(http_server.app)
    direct = client.post(
        "/api/items/sku-1/action",
        headers=AUTH,
        json={"action": "ebay_publish"},
    )
    bulk = client.post(
        "/api/bulk/action",
        headers=AUTH,
        json={"action": "list_now", "skus": ["sku-1"]},
    )
    assert direct.status_code == 400
    assert bulk.status_code == 400
    assert "ebay_publish" not in http_server.PIPELINE_ACTIONS
    assert "list_now" not in http_server._BULK_VALID_ACTIONS


def test_update_command_uses_nonpublication_dispatcher(monkeypatch, tmp_path):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: _published_with_enabled_publication_commands(_published()),
    )
    monkeypatch.setattr(http_server, "_workflow_provider_identity", lambda: "ebay:test")
    monkeypatch.setattr(
        http_server,
        "ebay_category_context",
        _resolved_category_context,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: SimpleNamespace(
            status="COMMITTED", resulting_generation="gen-1",
        ),
    )
    seen = {}

    def update_dispatch(path, **kwargs):
        seen.update(kwargs)
        result = SimpleNamespace(
            graph=SimpleNamespace(graph_id="graph-2", object_generation="gen-1"),
            held_external=(),
            operator_gates=(),
        )
        dispatched = SimpleNamespace(enqueued=True, job_id="job-1")
        return result, dispatched, "authority-1", True

    monkeypatch.setattr(listing_migration, "authorize_and_dispatch_update_item", update_dispatch)
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publication dispatcher called")),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "update-item",
            "object_generation": "gen-1",
            "values": _complete_listing_values(_published()),
        },
    )

    assert response.status_code == 200
    assert response.json()["authority_scope"] == "update-restage"
    assert seen["surface"] == "http:operator-object:update-item"


@pytest.mark.parametrize("command_id", ["list-item", "update-item"])
def test_listing_effect_commands_reject_omitted_editor_before_dispatch(
    monkeypatch, tmp_path, command_id,
):
    monkeypatch.setattr(
        http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer ")
    )
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: _published_with_enabled_publication_commands(_published()),
    )
    def dispatch(*args, **kwargs):
        pytest.fail("must not dispatch an omitted editor")
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        dispatch,
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_update_item",
        dispatch,
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": command_id,
            "object_generation": "gen-1",
            "values": {},
        },
    )

    assert response.status_code == 422


@pytest.mark.parametrize("command_id", ["save-inventory", "save-listing-draft"])
def test_sparse_save_noop_succeeds_without_mutation_or_provider(
    monkeypatch,
    command_id,
):
    monkeypatch.setattr(
        http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer ")
    )
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: _published(),
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: pytest.fail("a no-op save must not write"),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda *args, **kwargs: pytest.fail("a save must not call the provider"),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": command_id,
            "object_generation": "gen-1",
            "values": {},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["command_id"] == command_id
    assert response.json()["object_generation"] == "gen-1"


@pytest.mark.parametrize("command_id", ["list-item", "update-item"])
def test_invalid_published_listing_command_is_held_before_write_or_dispatch(
    monkeypatch,
    command_id,
):
    invalid_item = {
        "sku": "sku-1",
        "draft_listing": {
            "title": "Thing",
            "category_id": "999",
            "condition_enum": "USED_GOOD",
            "item_specifics": {},
        },
        "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
    }
    invalid = build_item_operator_object(
        item=invalid_item,
        workflow_card=_card(),
        category_context={
            "primary_category_node": None,
            "conditions": [
                {"enum": "USED_EXCELLENT", "label": "Used - Excellent"},
            ],
            "aspects": [],
        },
    )
    command = next(
        entry for entry in invalid["commands"] if entry["id"] == command_id
    )
    assert command["enabled"] is False
    monkeypatch.setattr(
        http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer ")
    )
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: invalid,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: pytest.fail("held publication must not write"),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda *args, **kwargs: pytest.fail("held publication must not dispatch"),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_update_item",
        lambda *args, **kwargs: pytest.fail("held update must not dispatch"),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": command_id,
            "object_generation": "gen-1",
            "values": _complete_listing_values(invalid),
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "command_held",
        "reason": command["reason"],
    }


@pytest.mark.parametrize(
    "command_id,values,expected_patch",
    [
        (
            "save-inventory",
            {"item_fields": {"title": "Revised"}},
            {"title": "Revised"},
        ),
        (
            "save-listing-draft",
            {"draft_listing": {"price": 12.5}},
            {"draft_listing": {"price": 12.5}},
        ),
    ],
)
def test_split_save_commands_use_published_schema_and_never_call_provider(
    monkeypatch, tmp_path, command_id, values, expected_patch,
):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    published = _published()
    after = json.loads(json.dumps(published))
    after["object_generation"] = "gen-2"
    seen = []
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: after if seen else published)
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda path, sku, fields, **kwargs: (
            seen.append(dict(fields))
            or SimpleNamespace(status="COMMITTED", resulting_generation="gen-2")
        ),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("publication dispatcher called")),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": command_id,
            "object_generation": "gen-1",
            "values": values,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["authority_scope"] == "local-item-mutation"
    assert seen == [expected_patch]


def test_category_group_template_applies_atomically_without_changing_listing_category(
    monkeypatch, tmp_path,
):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps({
            "groups": {
                "music-memorabilia": {
                    "name": "Music Memorabilia",
                    "size_class": "small",
                    "ai_hint": "music memorabilia",
                    "ebay_categories": ["2329"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {"itemdata_root": tmp_path / "items", "category_groups_path": str(groups_path)},
    )
    document = {
        "sku": "sku-1",
        "title": "Program",
        "ai_hint": "operator note",
        "ebay_category_id": "108857",
        "draft_listing": {
            "title": "Program",
            "category_id": "108857",
            "condition_enum": "USED_GOOD",
            "item_specifics": {},
        },
        "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
    }
    published = build_item_operator_object(
        item=document,
        workflow_card=_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
            "category_groups": http_server._category_group_choices(http_server._cfg),
        },
    )
    refreshed = json.loads(json.dumps(published))
    refreshed["object_generation"] = "gen-2"
    patched = []
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patched else published,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda path, sku, fields, **kwargs: (
            patched.append(dict(fields))
            or SimpleNamespace(status="COMMITTED", resulting_generation="gen-2")
        ),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda *args, **kwargs: pytest.fail("template selection must not dispatch"),
    )

    response = http_server.execute_item_operator_command(
        "sku-1",
        http_server.OperatorCommandBody(
            command_id="save-inventory",
            object_generation="gen-1",
            values={
                "item_fields": {
                    "category_group": "music-memorabilia",
                    "ai_hint": "operator note",
                },
            },
        ),
        operator_identity="operator:test",
    )

    assert response["ok"] is True
    assert patched == [{
        "category_group": "music-memorabilia",
        "ai_hint": "music memorabilia; operator note",
        "size_class": "small",
    }]
    assert document["ebay_category_id"] == "108857"
    assert document["draft_listing"]["category_id"] == "108857"


def test_category_group_template_rejects_unpublished_choice_without_writing(
    monkeypatch, tmp_path,
):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps({"groups": {"books": {"name": "Books", "ebay_categories": []}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {"itemdata_root": tmp_path / "items", "category_groups_path": str(groups_path)},
    )
    published = build_item_operator_object(
        item={"sku": "sku-1", "draft_listing": {"category_id": "108857"}},
        workflow_card=_card(),
        category_context={
            "conditions": [],
            "aspects": [],
            "category_groups": http_server._category_group_choices(http_server._cfg),
        },
    )
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: published)
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: pytest.fail("invalid template must not write"),
    )

    with pytest.raises(http_server.HTTPException) as exc_info:
        http_server.execute_item_operator_command(
            "sku-1",
            http_server.OperatorCommandBody(
                command_id="save-inventory",
                object_generation="gen-1",
                values={"item_fields": {"category_group": "not-published"}},
            ),
            operator_identity="operator:test",
        )

    assert exc_info.value.status_code == 422
    assert "published template" in exc_info.value.detail


def test_category_group_template_rejects_config_changed_after_publish(
    monkeypatch, tmp_path,
):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps({
            "groups": {
                "books": {
                    "name": "Books",
                    "ai_hint": "printed book",
                    "ebay_categories": ["261186"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {"itemdata_root": tmp_path / "items", "category_groups_path": str(groups_path)},
    )
    published = build_item_operator_object(
        item={"sku": "sku-1", "draft_listing": {"category_id": "108857"}},
        workflow_card=_card(),
        category_context={
            "conditions": [],
            "aspects": [],
            "category_groups": http_server._category_group_choices(http_server._cfg),
        },
    )
    groups_path.write_text(
        json.dumps({
            "groups": {
                "books": {
                    "name": "Books",
                    "ai_hint": "changed template",
                    "ebay_categories": ["261186"],
                }
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: published)
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda *args, **kwargs: pytest.fail("changed template must not write"),
    )

    with pytest.raises(http_server.HTTPException) as exc_info:
        http_server.execute_item_operator_command(
            "sku-1",
            http_server.OperatorCommandBody(
                command_id="save-inventory",
                object_generation="gen-1",
                values={"item_fields": {"category_group": "books"}},
            ),
            operator_identity="operator:test",
        )

    assert exc_info.value.status_code == 409
    assert "templates changed" in exc_info.value.detail


def test_listing_save_derives_store_label_and_marks_changed_choice_operator_owned(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    published = build_item_operator_object(
        item={
            "sku": "sku-1",
            "draft_listing": {
                "title": "Thing",
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "store_category_id": "STORE-1",
                "store_category_name": "Old Store category",
                "store_category_source": "category_group",
                "item_specifics": {},
            },
            "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
        },
        workflow_card=_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
            "store_categories": [
                {"value": "STORE-1", "label": "Old Store category"},
                {"value": "STORE-2", "label": "Selected Store category"},
            ],
        },
    )
    refreshed = json.loads(json.dumps(published))
    refreshed["object_generation"] = "gen-2"
    patched = []
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patched else published,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda path, sku, fields, **kwargs: (
            patched.append(dict(fields))
            or SimpleNamespace(status="COMMITTED", resulting_generation="gen-2")
        ),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-listing-draft",
            "object_generation": "gen-1",
            "values": {"draft_listing": {"store_category_id": "STORE-2"}},
        },
    )

    assert response.status_code == 200, response.text
    assert patched == [{
        "draft_listing": {
            "store_category_id": "STORE-2",
            "store_category_name": "Selected Store category",
            "store_category_source": "operator",
        }
    }]


def test_sparse_item_listing_save_round_trip_does_not_require_unpublished_condition_choices(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    card = _card()
    card["operator_projection"] = {
        "state": "held",
        "reasons": ["A valid category is required."],
        "commands": {
            "save-draft": {"enabled": True, "reason": None},
            "list-item": {"enabled": False, "reason": "A valid category is required."},
            "update-item": {"enabled": False, "reason": "No provider listing exists."},
        },
    }
    published = build_item_operator_object(
        item={
            "sku": "sku-1",
            "draft_listing": {"category_id": "99", "item_specifics": {}},
        },
        workflow_card=card,
        category_context={},
    )
    refreshed = json.loads(json.dumps(published))
    refreshed["object_generation"] = "gen-2"
    patched = []
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patched else published,
    )
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda path, sku, fields, **kwargs: (
            patched.append(dict(fields))
            or SimpleNamespace(status="COMMITTED", resulting_generation="gen-2")
        ),
    )
    from tgw.apis.ebay import taxonomy

    monkeypatch.setattr(
        taxonomy,
        "get_cached_category_node",
        lambda cfg, category_id: {
            "id": category_id,
            "name": "Selected category",
            "path": "Collectibles > Selected category",
            "leaf": True,
            "marketplace_id": "EBAY_US",
            "source": "taxonomy-snapshot",
        },
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-listing-draft",
            "object_generation": "gen-1",
            "values": {"draft_listing": {"category_id": "123", "price": 12.5}},
        },
    )

    assert response.status_code == 200
    assert response.json()["authority_scope"] == "local-item-mutation"
    assert patched == [
        {
            "draft_listing": {
                "category_id": "123",
                "category_name": "Selected category",
                "category_path": "Collectibles > Selected category",
                "price": 12.5,
            }
        }
    ]


def test_reprice_command_sets_exact_marker_and_dispatches_governed_price(
    monkeypatch, tmp_path,
):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: _published())
    patches = []
    monkeypatch.setattr(
        http_server,
        "_apply_operator_patch_cas",
        lambda path, sku, fields, **kwargs: (
            patches.append((path, dict(fields)))
            or SimpleNamespace(status="COMMITTED", resulting_generation="gen-2")
        ),
    )
    monkeypatch.setattr(
        http_server,
        "load_item_doc",
        lambda path: {"sku": "sku-1", "search_terms": "vintage catalog"},
    )
    seen = {}

    def request_goal(path, goal, **kwargs):
        seen.update({"path": path, "goal": goal, **kwargs})
        return SimpleNamespace(
            graph=SimpleNamespace(graph_id="price-graph", object_generation="gen-2"),
            dispatched=SimpleNamespace(
                treatment_id="ebay-price",
                enqueued=True,
                job_id="price-job-1",
            ),
            held_external=(),
            operator_gates=(),
        )

    monkeypatch.setattr(listing_migration, "request_item_goal", request_goal)

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "reprice-item",
            "object_generation": "gen-1",
            "values": {"search_terms": " vintage catalog "},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "command_id": "reprice-item",
        "authority_scope": "local-workflow-request",
        "graph_id": "price-graph",
        "object_generation": "gen-2",
        "dispatched": True,
        "job_id": "price-job-1",
        "held_external": [],
        "operator_gates": [],
        "refresh": "/api/operator/items/sku-1",
    }
    assert patches == [
        (
            tmp_path / "sku-1" / "sku-1.json",
            {"search_terms": "vintage catalog", "ai_reprice_requested": True},
        ),
    ]
    assert seen["goal"].identity == "tgw.ebay_priced"
    assert tuple(treatment.identity for treatment in seen["treatments"]) == ("ebay-price",)
    assert seen["origin"] == "operator"
    assert seen["operator_surface"] == "http:operator-object:reprice-item"
