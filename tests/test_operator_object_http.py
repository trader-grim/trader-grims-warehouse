from __future__ import annotations

import json
import shutil
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tgw import http_server
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
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "condition_label": "Used - Good",
                "item_specifics": {},
            },
            "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
        },
        workflow_card=_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )


def test_get_operator_object_mounts_real_http_contract(tmp_path, monkeypatch):
    item_dir = tmp_path / "sku-1"
    item_dir.mkdir()
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(
        json.dumps({
            "condition_factors": {"New": 1.0, "Very Good": 0.8, "Good": 0.7},
            "attribute_vocabulary": {
                "Brand": {"type": "string"},
                "Color": {"type": "string"},
            },
            "groups": {
                "paper": {
                    "name": "Paper",
                    "size_class": "flat",
                    "ai_hint": "printed paper",
                    "ebay_categories": ["123"],
                },
            },
        }),
        encoding="utf-8",
    )
    (item_dir / "sku-1.json").write_text(
        json.dumps(
            {
                "sku": "sku-1",
                "condition": "very good",
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
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {"itemdata_root": tmp_path, "category_groups_path": str(groups_path)},
    )
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
    ]
    assert [command["label"] for command in payload["commands"][:2]] == [
        "Save Inventory",
        "Save Listing Draft",
    ]
    schema = payload["field_schema"]
    assert schema["item_fields"]["condition"]["value"] == "Very Good"
    assert "Color" in schema["record_attribute_vocabulary"]
    assert schema["aspects"] == []
    assert schema["item_fields"]["category_group"]["options"][0] == {
        "value": "paper",
        "label": "Paper",
        "size_class": "flat",
        "ai_hint": "printed paper",
        "ebay_categories": ["123"],
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
    assert "ebay_publish" not in response.text
    assert "triggerAction" not in response.text
    assert "id==='save-inventory'" in response.text
    assert "id==='save-listing-draft'" in response.text
    assert "saveValues('item_fields')" in response.text
    assert "saveValues('draft_listing')" in response.text
    assert "x.display_only?'disabled'" in response.text
    assert "saveBaseline={item_fields:{},draft_listing:{},aspects:{}}" in response.text
    assert "function resetSaveBaseline()" in response.text
    assert "JSON.stringify(value)!==saveBaseline[scope][name]" in response.text
    assert "resetSaveBaseline();" in response.text


def test_shipped_browser_command_generation_submits_only_dirty_controls(monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for the shipped browser contract test")
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    client = TestClient(http_server.app)
    http_server._sessions["operator-object-js"] = float("inf")
    client.cookies.set("tgw_session", "operator-object-js")
    response = client.get("/form/operator/items/sku-1")
    script = response.text.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
resetSaveBaseline();
const initialInventory=commandValues('save-inventory');
const initialListing=commandValues('save-listing-draft');
controls.find(x=>x.dataset.field==='title'&&x.dataset.scope==='item_fields').value='Renamed';
const titleInventory=commandValues('save-inventory');
resetSaveBaseline();
const secondInventory=commandValues('save-inventory');
controls.find(x=>x.dataset.field==='price').value='12.5';
const priceListing=commandValues('save-listing-draft');
resetSaveBaseline();
condition.value='';
const clearListing=commandValues('save-listing-draft');
condition.value='USED_GOOD';
resetSaveBaseline();
condition.value='NEW';
const remapListing=commandValues('save-listing-draft');
resetSaveBaseline();
controls.find(x=>x.dataset.field==='category_group').value='cameras';
const groupInventory=commandValues('save-inventory');
resetSaveBaseline();
aspects[0].value='Other';
const aspectListing=commandValues('save-listing-draft');
globalThis.__result={initialInventory,initialListing,titleInventory,secondInventory,priceListing,clearListing,remapListing,groupInventory,aspectListing};
"""
    runner = f"""
const vm=require('node:vm');
const controls=[
  {{value:'Thing',dataset:{{scope:'item_fields',field:'title',type:'string',nullable:'false'}}}},
  {{value:'books',dataset:{{scope:'item_fields',field:'category_group',type:'string',nullable:'false'}}}},
  {{value:'custom-size',dataset:{{scope:'item_fields',field:'size_class',type:'string',nullable:'false'}}}},
  {{value:'operator-specific hint',dataset:{{scope:'item_fields',field:'ai_hint',type:'string',nullable:'false'}}}},
  {{value:'Listing',dataset:{{scope:'draft_listing',field:'title',type:'string',nullable:'false'}}}},
  {{value:'10',dataset:{{scope:'draft_listing',field:'price',type:'number',nullable:'true'}}}},
  {{value:'USED_GOOD',dataset:{{scope:'draft_listing',field:'condition_enum',type:'string',nullable:'false'}}}}
];
const aspects=[{{value:'TGW',dataset:{{aspect:'Brand'}}}}];
const condition=controls.find(x=>x.dataset.field==='condition_enum');
const itemNode={{innerHTML:''}};
const document={{
  title:'',
  querySelectorAll(selector){{
    if(selector==='[data-aspect]')return aspects;
    if(selector==='[data-command]')return [];
    const match=selector.match(/^\\[data-scope="([^"]+)"\\]\\[data-field\\]$/);
    return match?controls.filter(x=>x.dataset.scope===match[1]):[];
  }},
  getElementById(id){{return id==='condition_enum'?condition:itemNode;}}
}};
const sandbox={{controls,aspects,condition,document,location:{{pathname:'/form/operator/items/sku-1'}},fetch:()=>new Promise(()=>{{}}),encodeURIComponent,decodeURIComponent}};
vm.runInNewContext({json.dumps(script + harness)},sandbox);
process.stdout.write(JSON.stringify(sandbox.__result));
"""

    completed = subprocess.run(
        [node, "-e", runner],
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)

    assert result == {
        "initialInventory": {"item_fields": {}},
        "initialListing": {"draft_listing": {}},
        "titleInventory": {"item_fields": {"title": "Renamed"}},
        "secondInventory": {"item_fields": {}},
        "priceListing": {"draft_listing": {"price": 12.5}},
        "clearListing": {"draft_listing": {"condition_enum": ""}},
        "remapListing": {"draft_listing": {"condition_enum": "NEW"}},
        "groupInventory": {"item_fields": {"category_group": "cameras"}},
        "aspectListing": {"draft_listing": {"item_specifics": {"Brand": "Other"}}},
    }


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
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: _published())
    monkeypatch.setattr(http_server, "_workflow_provider_identity", lambda: "ebay:test")
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
        json={"command_id": "update-item", "object_generation": "gen-1", "values": {}},
    )

    assert response.status_code == 200
    assert response.json()["authority_scope"] == "update-restage"
    assert seen["surface"] == "http:operator-object:update-item"


@pytest.mark.parametrize(
    ("command_id", "values", "expected_patch"),
    [
        (
            "save-inventory",
            {"item_fields": {"title": "Inventory title"}},
            {"title": "Inventory title"},
        ),
        (
            "save-listing-draft",
            {"draft_listing": {"price": 22.5}},
            {"draft_listing": {"price": 22.5}},
        ),
    ],
    ids=["save-inventory", "save-listing-draft"],
)
def test_sparse_inventory_and_listing_draft_http_round_trips_are_distinct(
    monkeypatch, tmp_path, command_id, values, expected_patch
):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    published = _published()
    refreshed = deepcopy(published)
    refreshed["object_generation"] = "gen-2"
    patches = []
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patches else published,
    )
    monkeypatch.setattr(
        http_server,
        "patch_item",
        lambda sku, body, request, operator: patches.append(dict(body.fields))
        or {"ok": True},
    )
    monkeypatch.setattr(
        http_server,
        "_workflow_provider_identity",
        lambda: (_ for _ in ()).throw(AssertionError("local save called a provider")),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local save dispatched publication")
        ),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_update_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("local save dispatched update")
        ),
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
    assert patches == [expected_patch]


@pytest.mark.parametrize(
    ("current_category", "expected_patch"),
    [
        (
            "",
            {
                "category_group": "books",
                "size_class": "small",
                "ai_hint": "printed book",
                "draft_listing": {"category_id": "261186"},
            },
        ),
        (
            "999999",
            {
                "category_group": "books",
                "size_class": "small",
                "ai_hint": "printed book",
            },
        ),
    ],
    ids=[
        "supply-first-category",
        "preserve-existing-category",
    ],
)
def test_group_selection_is_one_atomic_http_patch_and_preserves_later_category(
    monkeypatch, tmp_path, current_category, expected_patch
):
    item = {
        "sku": "sku-1",
        "title": "Thing",
        "draft_listing": {
            "category_id": current_category,
            "condition_enum": "USED_GOOD",
            "item_specifics": {},
        },
    }
    group = {
        "value": "books",
        "label": "Books",
        "size_class": "small",
        "ai_hint": "printed book",
        "ebay_categories": ["261186", "1105"],
    }
    published = build_item_operator_object(
        item=item,
        workflow_card=_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
            "category_groups": [group],
        },
    )
    refreshed = deepcopy(published)
    refreshed["object_generation"] = "gen-2"
    patches = []
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patches else published,
    )
    monkeypatch.setattr(
        http_server,
        "patch_item",
        lambda sku, body, request, operator: patches.append(dict(body.fields))
        or {"ok": True},
    )
    monkeypatch.setattr(
        http_server,
        "_workflow_provider_identity",
        lambda: (_ for _ in ()).throw(AssertionError("group save called a provider")),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-inventory",
            "object_generation": "gen-1",
            "values": {"item_fields": {"category_group": "books"}},
        },
    )

    assert response.status_code == 200, response.text
    assert patches == [expected_patch]


def test_unrelated_inventory_save_does_not_reapply_unchanged_group_defaults(
    monkeypatch, tmp_path
):
    item = {
        "sku": "sku-1",
        "title": "Thing",
        "category_group": "books",
        "size_class": "custom-size",
        "ai_hint": "operator-specific hint",
        "draft_listing": {
            "category_id": "261186",
            "condition_enum": "USED_GOOD",
            "item_specifics": {},
        },
    }
    published = build_item_operator_object(
        item=item,
        workflow_card=_card(),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
            "category_groups": [{
                "value": "books",
                "label": "Books",
                "size_class": "small",
                "ai_hint": "printed book",
                "ebay_categories": ["261186"],
            }],
        },
    )
    refreshed = deepcopy(published)
    refreshed["object_generation"] = "gen-2"
    patches = []
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patches else published,
    )
    monkeypatch.setattr(
        http_server,
        "patch_item",
        lambda sku, body, request, operator: patches.append(dict(body.fields))
        or {"ok": True},
    )
    monkeypatch.setattr(
        http_server,
        "_workflow_provider_identity",
        lambda: (_ for _ in ()).throw(AssertionError("inventory save called a provider")),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-inventory",
            "object_generation": "gen-1",
            # Defensive HTTP reproduction: even a stale client that resends
            # the unchanged group must not overwrite custom server fields.
            "values": {
                "item_fields": {
                    "title": "Renamed",
                    "category_group": "books",
                },
            },
        },
    )

    assert response.status_code == 200, response.text
    assert patches == [{"title": "Renamed", "category_group": "books"}]
    assert published["item"]["record"]["size_class"] == "custom-size"
    assert published["item"]["record"]["ai_hint"] == "operator-specific hint"


@pytest.mark.parametrize(
    ("draft_values", "expected_patch"),
    [
        ({"title": "Unrelated title"}, {"draft_listing": {"title": "Unrelated title"}}),
        ({"condition_enum": ""}, {"draft_listing": {"condition_enum": ""}}),
        ({"condition_enum": "NEW"}, {"draft_listing": {"condition_enum": "NEW"}}),
    ],
    ids=["unrelated-edit", "explicit-clear", "explicit-remap"],
)
def test_illegal_condition_listing_draft_http_remediation_paths(
    monkeypatch, tmp_path, draft_values, expected_patch
):
    item = {
        "sku": "sku-1",
        "title": "Thing",
        "draft_listing": {
            "category_id": "123",
            "condition_enum": "USED_GOOD",
            "condition_label": "Used - Good",
            "item_specifics": {},
        },
    }
    published = build_item_operator_object(
        item=item,
        workflow_card=_card(),
        category_context={
            "category_recognized": True,
            "item_condition_required": True,
            "required_flag_valid": True,
            "conditions": [{"enum": "NEW", "label": "New"}],
            "aspects": [],
        },
    )
    refreshed = deepcopy(published)
    refreshed["object_generation"] = "gen-2"
    patches = []
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: refreshed if patches else published,
    )
    monkeypatch.setattr(
        http_server,
        "patch_item",
        lambda sku, body, request, operator: patches.append(dict(body.fields))
        or {"ok": True},
    )
    monkeypatch.setattr(
        http_server,
        "_workflow_provider_identity",
        lambda: (_ for _ in ()).throw(AssertionError("draft save called a provider")),
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-listing-draft",
            "object_generation": "gen-1",
            "values": {"draft_listing": draft_values},
        },
    )

    assert response.status_code == 200, response.text
    assert patches == [expected_patch]


@pytest.mark.parametrize(
    ("command_id", "values"),
    [
        ("save-inventory", {"item_fields": {}}),
        ("save-listing-draft", {"draft_listing": {}}),
    ],
)
def test_second_sparse_save_is_an_idempotent_http_noop(
    monkeypatch, tmp_path, command_id, values
):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: _published())
    monkeypatch.setattr(
        http_server,
        "patch_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("an empty dirty-field domain must not patch")
        ),
    )
    monkeypatch.setattr(
        http_server,
        "_workflow_provider_identity",
        lambda: (_ for _ in ()).throw(AssertionError("local no-op called a provider")),
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
    assert response.json()["command_id"] == command_id
    assert response.json()["object_generation"] == "gen-1"
