from __future__ import annotations

import json
import shutil
import subprocess
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tgw import http_server, sqlite_catalog
from tgw.item_mutation import item_generation
from tgw.operator_objects import build_item_operator_object
from tgw.workflow import listing_migration

AUTH = {"Authorization": "Bearer operator-object-test"}


def _card(item):
    return {
        "entity_id": "sku-1",
        "object_generation": item_generation(item),
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
    item = {
        "sku": "sku-1",
        "draft_listing": {
            "category_id": "123",
            "condition_enum": "USED_GOOD",
            "condition_label": "Used - Good",
            "item_specifics": {},
        },
        "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
    }
    return build_item_operator_object(
        item=item,
        workflow_card=_card(item),
        category_context={
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )


def _published_from_item_path(item_path, category_context=None):
    item = json.loads(item_path.read_text(encoding="utf-8"))
    card = _card(item)
    return build_item_operator_object(
        item=item,
        workflow_card=card,
        category_context=category_context
        or {
            "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
            "aspects": [],
        },
    )


def _configure_real_item_command_storage(tmp_path, monkeypatch, item):
    itemdata_root = tmp_path / "ItemData"
    item_dir = itemdata_root / item["sku"]
    item_dir.mkdir(parents=True)
    item_path = item_dir / f"{item['sku']}.json"
    item_path.write_text(json.dumps(item), encoding="utf-8")
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": itemdata_root,
            "item_mutation_journal_root": tmp_path / "item-mutations",
            "pretty": False,
        },
    )
    monkeypatch.setattr(
        http_server,
        "_api_key",
        AUTH["Authorization"].removeprefix("Bearer "),
    )
    monkeypatch.setattr(
        sqlite_catalog,
        "upsert_catalog_row",
        lambda config, document: {"ok": True},
    )
    monkeypatch.setattr(
        http_server,
        "_workflow_provider_identity",
        lambda: (_ for _ in ()).throw(
            AssertionError("local save called a provider")
        ),
    )
    return item_path


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


def test_exact_route_fails_closed_on_a_record_with_b_action_card_generation(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import action_cards

    itemdata_root = tmp_path / "ItemData"
    item_dir = itemdata_root / "sku-1"
    item_dir.mkdir(parents=True)
    item_path = item_dir / "sku-1.json"
    item_a = {"sku": "sku-1", "title": "A"}
    item_b = {"sku": "sku-1", "title": "B"}
    item_path.write_text(json.dumps(item_a), encoding="utf-8")
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": itemdata_root,
            "item_mutation_journal_root": tmp_path / "item-mutations",
        },
    )
    monkeypatch.setattr(
        http_server,
        "_api_key",
        AUTH["Authorization"].removeprefix("Bearer "),
    )
    monkeypatch.setattr(http_server, "_workflow_attempt_rows", lambda sku: [])
    monkeypatch.setattr(
        http_server,
        "_workflow_reconciled_provider_effect_ids",
        lambda rows: frozenset(),
    )
    monkeypatch.setattr(http_server, "_workflow_provider_identity", lambda: "")
    real_build_action_card = action_cards.build_item_action_card

    def build_b_action_card(*args, **kwargs):
        # Exact reviewed interleaving: the route already holds record A in
        # memory, then an uncooperative writer publishes B before the action
        # card reopens the pathname and derives B's generation.
        item_path.write_text(json.dumps(item_b), encoding="utf-8")
        return real_build_action_card(*args, **kwargs)

    monkeypatch.setattr(
        action_cards,
        "build_item_action_card",
        build_b_action_card,
    )

    response = TestClient(http_server.app).get(
        "/api/operator/items/sku-1",
        headers=AUTH,
    )

    assert response.status_code == 503, response.text
    assert "object" not in response.json()
    assert "item generation" in response.json()["detail"]
    assert json.loads(item_path.read_text(encoding="utf-8")) == item_b


def test_live_shaped_lowercase_config_preserves_display_and_cached_policy(
    tmp_path,
    monkeypatch,
):
    from tgw.apis.ebay import conditions, specifics
    from tgw.ebay import pricing

    itemdata_root = tmp_path / "ItemData"
    item_dir = itemdata_root / "sku-1"
    item_dir.mkdir(parents=True)
    item_path = item_dir / "sku-1.json"
    item_path.write_text(json.dumps({
        "sku": "sku-1",
        "title": "Live-shaped item",
        "condition": "Very Good",
        "notes": "before",
        "draft_listing": {
            "category_id": "108857",
            "condition_enum": "",
            "item_specifics": {"Color": "Cerulean", "Format": "Poster"},
        },
    }), encoding="utf-8")
    conditions_configured = [
        "new",
        "like new",
        "very good",
        "good",
        "acceptable",
        "for parts",
    ]
    groups = {
        f"group-{index:02d}": {
            "name": f"Group {index:02d}",
            "size_class": "flat",
            "ai_hint": f"group {index:02d}",
            "ebay_categories": ["108857"] if index == 0 else [str(200000 + index)],
        }
        for index in range(25)
    }
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(json.dumps({
        "condition_factors": {
            value: 1.0 - index / 10
            for index, value in enumerate(conditions_configured)
        },
        "groups": groups,
    }), encoding="utf-8")
    (tmp_path / "ebay-condition-policies.json").write_text(json.dumps({
        "fetched_at": "2099-01-01T00:00:00+00:00",
        "policies": {"108857": []},
        "item_condition_required": {"108857": False},
    }), encoding="utf-8")
    provider_aspects = [
        {
            "name": "Color",
            "required": False,
            "mode": "FREE_TEXT",
            "allowed_values": [],
        },
        {
            "name": "Format",
            "required": False,
            "mode": "SELECTION_ONLY",
            "allowed_values": ["Poster", "Print", "Broadside"],
        },
    ]
    (tmp_path / "ebay-aspects-cache.json").write_text(json.dumps({
        "108857": {
            "_aspect_filter_revision": specifics._ASPECT_FILTER_REVISION,
            "aspects": provider_aspects,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(conditions, "_policies_mem_cache", None)
    monkeypatch.setattr(conditions, "_required_mem_cache", {})
    monkeypatch.setattr(specifics, "_aspects_mem_cache", {})
    monkeypatch.setattr(pricing, "_groups_cache", None)
    monkeypatch.setattr(pricing, "_groups_reverse", None)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": itemdata_root,
        "item_mutation_journal_root": tmp_path / "item-mutations",
        "category_groups_path": groups_path,
        "catalog_root": tmp_path,
        "pretty": False,
    })
    monkeypatch.setattr(
        http_server,
        "_api_key",
        AUTH["Authorization"].removeprefix("Bearer "),
    )
    monkeypatch.setattr(http_server, "_workflow_attempt_rows", lambda sku: [])
    monkeypatch.setattr(
        http_server,
        "_workflow_reconciled_provider_effect_ids",
        lambda rows: frozenset(),
    )
    monkeypatch.setattr(http_server, "_workflow_provider_identity", lambda: "")
    monkeypatch.setattr(
        sqlite_catalog,
        "upsert_catalog_row",
        lambda config, document: {"ok": True},
    )
    client = TestClient(http_server.app)

    fetched = client.get("/api/operator/items/sku-1", headers=AUTH)

    assert fetched.status_code == 200, fetched.text
    published = fetched.json()["object"]
    schema = published["field_schema"]
    assert schema["condition"] == {
        "value": "",
        "label": None,
        "required": False,
        "valid": True,
        "options": [{"value": "", "label": "No listing condition"}],
        "required_flag_valid": True,
    }
    assert schema["item_fields"]["condition"]["value"] == "Very Good"
    assert schema["record_condition_vocabulary"] == conditions_configured
    published_conditions = [
        option["value"]
        for option in schema["item_fields"]["condition"]["options"]
    ]
    assert [value.casefold() for value in published_conditions] == (
        conditions_configured
    )
    assert "Very Good" in published_conditions
    assert [option["value"] for option in schema["category_groups"]] == list(
        groups
    )
    assert "record_attribute_vocabulary" not in schema
    aspects = {aspect["name"]: aspect for aspect in schema["aspects"]}
    assert aspects["Color"]["mode"] == "FREE_TEXT"
    assert aspects["Color"]["allowed_values"] == []
    assert aspects["Format"]["mode"] == "SELECTION_ONLY"
    assert aspects["Format"]["allowed_values"] == [
        "Poster",
        "Print",
        "Broadside",
    ]

    saved = client.post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-inventory",
            "object_generation": published["object_generation"],
            "values": {"item_fields": {"notes": "after"}},
        },
    )

    assert saved.status_code == 200, saved.text
    stored = json.loads(item_path.read_text(encoding="utf-8"))
    assert stored["condition"] == "Very Good"
    assert stored["notes"] == "after"
    assert saved.json()["object_generation"] == item_generation(stored)


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
function capturedListing(){try{return{value:commandValues('save-listing-draft')}}catch(e){return{error:e.message}}}
const price=controls.find(x=>x.dataset.field==='price');
const quantity=controls.find(x=>x.dataset.field==='quantity');
resetSaveBaseline();
const initialInventory=commandValues('save-inventory');
const initialListing=commandValues('save-listing-draft');
controls.find(x=>x.dataset.field==='title'&&x.dataset.scope==='item_fields').value='Renamed';
const titleInventory=commandValues('save-inventory');
resetSaveBaseline();
const secondInventory=commandValues('save-inventory');
price.value='12.5';
const priceListing=commandValues('save-listing-draft');
price.value='';
const emptyNullable=capturedListing();
price.value='not-a-number';
const invalidNumber=capturedListing();
const beforeHeldFetch=fetchCount;
submitCommand('save-listing-draft');
const invalidHeld={message:itemNode.textContent,fetchUnchanged:fetchCount===beforeHeldFetch};
price.value='10';quantity.value='12items';
const partialInteger=capturedListing();
quantity.value='2';price.value='1e309';
const nonFiniteNumber=capturedListing();
price.value='10';
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
const numberControl=inputControl('draft_listing','price',{type:'number',label:'Price',nullable:true,value:10});
const integerControl=inputControl('draft_listing','quantity',{type:'integer',label:'Quantity',nullable:true,value:2});
const liveAspectHtml=fieldControls({field_schema:{
  item_fields:{},listing_fields:{},condition:{options:[]},
  aspects:[
    {name:'Color',required:false,mode:'FREE_TEXT',allowed_values:[],value:'Cerulean'},
    {name:'Format',required:false,mode:'SELECTION_ONLY',allowed_values:['Poster','Print','Broadside'],value:'Poster'}
  ]
}});
globalThis.__result={
  initialInventory,initialListing,titleInventory,secondInventory,priceListing,
  emptyNullable,invalidNumber,invalidHeld,partialInteger,nonFiniteNumber,
  clearListing,remapListing,groupInventory,aspectListing,
  numericTypes:{
    number:numberControl.includes('type="number"')&&numberControl.includes('step="any"'),
    integer:integerControl.includes('type="number"')&&integerControl.includes('step="1"')
  },
  liveAspects:{
    colorInput:liveAspectHtml.includes('<input id="aspect-0"')&&!liveAspectHtml.includes('<select id="aspect-0"'),
    formatSelect:liveAspectHtml.includes('<select id="aspect-1"'),
    allSelections:['Poster','Print','Broadside'].every(v=>liveAspectHtml.includes('value="'+v+'"'))
  }
};
"""
    runner = f"""
const vm=require('node:vm');
const controls=[
  {{value:'Thing',dataset:{{scope:'item_fields',field:'title',type:'string',nullable:'false'}}}},
  {{value:'books',dataset:{{scope:'item_fields',field:'category_group',type:'string',nullable:'false'}}}},
  {{value:'custom-size',dataset:{{scope:'item_fields',field:'size_class',type:'string',nullable:'false'}}}},
  {{value:'operator-specific hint',dataset:{{scope:'item_fields',field:'ai_hint',type:'string',nullable:'false'}}}},
  {{value:'Listing',dataset:{{scope:'draft_listing',field:'title',type:'string',nullable:'false'}}}},
  {{value:'10',validity:{{badInput:false}},dataset:{{scope:'draft_listing',field:'price',type:'number',nullable:'true'}}}},
  {{value:'2',validity:{{badInput:false}},dataset:{{scope:'draft_listing',field:'quantity',type:'integer',nullable:'true'}}}},
  {{value:'USED_GOOD',dataset:{{scope:'draft_listing',field:'condition_enum',type:'string',nullable:'false'}}}}
];
const aspects=[{{value:'TGW',dataset:{{aspect:'Brand'}}}}];
const condition=controls.find(x=>x.dataset.field==='condition_enum');
const itemNode={{innerHTML:'',textContent:''}};
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
const sandbox={{
  controls,aspects,condition,itemNode,document,
  location:{{pathname:'/form/operator/items/sku-1'}},
  fetchCount:0,
  fetch:()=>{{sandbox.fetchCount++;return new Promise(()=>{{}})}},
  encodeURIComponent,decodeURIComponent
}};
vm.runInNewContext({json.dumps(script + harness)},sandbox);
process.stdout.write(JSON.stringify(sandbox.__result));
"""

    completed = subprocess.run(
        [node, "-e", runner],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)

    assert result == {
        "initialInventory": {"item_fields": {}},
        "initialListing": {"draft_listing": {}},
        "titleInventory": {"item_fields": {"title": "Renamed"}},
        "secondInventory": {"item_fields": {}},
        "priceListing": {"draft_listing": {"price": 12.5}},
        "emptyNullable": {"value": {"draft_listing": {"price": None}}},
        "invalidNumber": {"error": "price must be a finite number"},
        "invalidHeld": {
            "message": "Held: price must be a finite number",
            "fetchUnchanged": True,
        },
        "partialInteger": {"error": "quantity must be a whole finite integer"},
        "nonFiniteNumber": {"error": "price must be a finite number"},
        "clearListing": {"draft_listing": {"condition_enum": ""}},
        "remapListing": {"draft_listing": {"condition_enum": "NEW"}},
        "groupInventory": {"item_fields": {"category_group": "cameras"}},
        "aspectListing": {"draft_listing": {"item_specifics": {"Brand": "Other"}}},
        "numericTypes": {"number": True, "integer": True},
        "liveAspects": {
            "colorInput": True,
            "formatSelect": True,
            "allSelections": True,
        },
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


@pytest.mark.parametrize(
    "draft_fragment",
    [
        '"price":NaN',
        '"price":Infinity',
        '"price":"not-a-number"',
        '"quantity":"12items"',
        '"quantity":12.5',
        '"quantity":true',
    ],
)
def test_operator_save_http_rejects_nonfinite_and_type_confused_numeric_payloads(
    monkeypatch, draft_fragment
):
    published = _published()
    monkeypatch.setattr(
        http_server,
        "_api_key",
        AUTH["Authorization"].removeprefix("Bearer "),
    )
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: published,
    )
    monkeypatch.setattr(
        http_server,
        "patch_item",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("invalid numeric payload reached item storage")
        ),
    )
    payload = (
        '{"command_id":"save-listing-draft",'
        f'"object_generation":"{published["object_generation"]}",'
        '"values":{"draft_listing":{'
        f"{draft_fragment}"
        "}}}"
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers={**AUTH, "Content-Type": "application/json"},
        content=payload,
    )

    assert response.status_code == 422, response.text


def test_operator_save_and_actual_ebay_write_share_the_real_item_lock(
    tmp_path, monkeypatch
):
    item_path = _configure_real_item_command_storage(
        tmp_path,
        monkeypatch,
        {
            "sku": "sku-1",
            "title": "Thing",
            "notes": "initial value",
            "draft_listing": {
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "item_specifics": {},
            },
        },
    )
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda _sku: _published_from_item_path(item_path),
    )
    operator_inside_lock = threading.Event()
    release_operator = threading.Event()
    ebay_path_entered = threading.Event()
    real_apply_patch_locked = http_server._apply_patch_locked
    real_apply_ebay_write = http_server._apply_ebay_write

    def paused_apply_patch_locked(json_path, fields, *args, **kwargs):
        if fields.get("notes") == "operator edit":
            operator_inside_lock.set()
            assert release_operator.wait(timeout=5)
        return real_apply_patch_locked(json_path, fields, *args, **kwargs)

    def observed_ebay_write(*args, **kwargs):
        ebay_path_entered.set()
        return real_apply_ebay_write(*args, **kwargs)

    monkeypatch.setattr(
        http_server,
        "_apply_patch_locked",
        paused_apply_patch_locked,
    )
    monkeypatch.setattr(http_server, "_apply_ebay_write", observed_ebay_write)
    initial_generation = item_generation(
        json.loads(item_path.read_text(encoding="utf-8"))
    )
    responses = {}

    def submit_operator_save():
        responses["operator"] = TestClient(http_server.app).post(
            "/api/operator/items/sku-1/commands",
            headers=AUTH,
            json={
                "command_id": "save-inventory",
                "object_generation": initial_generation,
                "values": {"item_fields": {"notes": "operator edit"}},
            },
        )

    def submit_ebay_write():
        responses["ebay"] = TestClient(http_server.app).post(
            "/api/items/sku-1/ebay-write",
            headers=AUTH,
            json={"ebay_offer": {"offer_id": "offer-1"}},
        )

    operator_thread = threading.Thread(target=submit_operator_save)
    ebay_thread = threading.Thread(target=submit_ebay_write)
    operator_thread.start()
    assert operator_inside_lock.wait(timeout=5)
    ebay_thread.start()
    assert ebay_path_entered.wait(timeout=5)
    assert ebay_thread.is_alive()
    release_operator.set()
    operator_thread.join(timeout=5)
    ebay_thread.join(timeout=5)

    assert not operator_thread.is_alive()
    assert not ebay_thread.is_alive()
    assert responses["operator"].status_code == 200, responses["operator"].text
    assert responses["ebay"].status_code == 200, responses["ebay"].text
    final_document = json.loads(item_path.read_text(encoding="utf-8"))
    assert final_document["notes"] == "operator edit"
    assert final_document["ebay_offer"]["offer_id"] == "offer-1"


def test_operator_location_save_propagates_post_commit_repair_required(
    tmp_path,
    monkeypatch,
):
    item_path = _configure_real_item_command_storage(
        tmp_path,
        monkeypatch,
        {
            "sku": "sku-1",
            "title": "Thing",
            "location": "old-bin",
            "draft_listing": {
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "item_specifics": {},
            },
        },
    )
    http_server._cfg["location_tree_root"] = tmp_path / "by-location"
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda _sku: _published_from_item_path(item_path),
    )
    monkeypatch.setattr(
        http_server,
        "sync_location_tree",
        lambda *args, **kwargs: {
            "ok": False,
            "sku": "sku-1",
            "error": "injected location projection failure",
        },
    )
    generation = item_generation(
        json.loads(item_path.read_text(encoding="utf-8"))
    )

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-inventory",
            "object_generation": generation,
            "values": {"item_fields": {"location": "new-bin"}},
        },
    )

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    stored = json.loads(item_path.read_text(encoding="utf-8"))
    assert detail["ok"] is False
    assert detail["code"] == "location_projection_repair_required"
    assert detail["resulting_generation"] == item_generation(stored)
    assert stored["location"] == "new-bin"
    assert stored["pipeline_error"]["code"] == "location_update_failed"


@pytest.mark.parametrize(
    ("command_id", "initial_extra", "newer_values", "stale_values"),
    [
        (
            "save-inventory",
            {"location": "old-bin"},
            {"item_fields": {"location": "newer-bin"}},
            {"item_fields": {"location": "stale-bin"}},
        ),
        (
            "save-listing-draft",
            {
                "price_history": [{
                    "ts": "2026-01-01T00:00:00+00:00",
                    "price": 10.0,
                    "previous_price": None,
                    "stage": None,
                    "label": "initial",
                    "source": "test",
                }],
            },
            {"draft_listing": {"price": 20.0}},
            {"draft_listing": {"price": 12.0}},
        ),
    ],
    ids=["location", "price-history"],
)
def test_exact_operator_route_rejects_stale_save_without_overwriting_newer_state(
    tmp_path,
    monkeypatch,
    command_id,
    initial_extra,
    newer_values,
    stale_values,
):
    item = {
        "sku": "sku-1",
        "title": "Thing",
        "draft_listing": {
            "category_id": "123",
            "condition_enum": "USED_GOOD",
            "price": 10.0,
            "item_specifics": {},
        },
        **initial_extra,
    }
    item_path = _configure_real_item_command_storage(
        tmp_path,
        monkeypatch,
        item,
    )
    http_server._cfg["location_tree_root"] = tmp_path / "by-location"
    initial_generation = item_generation(item)
    stale_published = threading.Event()
    release_stale = threading.Event()
    current_calls = 0
    calls_lock = threading.Lock()

    def interleaved_current(_sku):
        nonlocal current_calls
        published = _published_from_item_path(item_path)
        with calls_lock:
            current_calls += 1
            call_number = current_calls
        if call_number == 1:
            stale_published.set()
            assert release_stale.wait(timeout=5)
        return published

    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        interleaved_current,
    )
    responses = {}

    def submit_stale():
        responses["stale"] = TestClient(http_server.app).post(
            "/api/operator/items/sku-1/commands",
            headers=AUTH,
            json={
                "command_id": command_id,
                "object_generation": initial_generation,
                "values": stale_values,
            },
        )

    stale_thread = threading.Thread(
        target=submit_stale,
        name="stale-operator-save",
    )
    stale_thread.start()
    assert stale_published.wait(timeout=5)
    newer = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": command_id,
            "object_generation": initial_generation,
            "values": newer_values,
        },
    )
    assert newer.status_code == 200, newer.text
    newer_document = json.loads(item_path.read_text(encoding="utf-8"))
    newer_generation = item_generation(newer_document)
    assert newer.json()["object_generation"] == newer_generation

    release_stale.set()
    stale_thread.join(timeout=5)
    assert not stale_thread.is_alive()
    assert responses["stale"].status_code == 409, responses["stale"].text
    assert responses["stale"].json()["detail"] == {
        "code": "generation_conflict",
        "expected": newer_generation,
        "received": initial_generation,
        "refresh": "/api/operator/items/sku-1",
    }
    final_document = json.loads(item_path.read_text(encoding="utf-8"))
    assert final_document == newer_document

    if command_id == "save-inventory":
        assert final_document["location"] == "newer-bin"
        assert (tmp_path / "by-location" / "newer-bin" / "sku-1").is_symlink()
        assert not (tmp_path / "by-location" / "stale-bin" / "sku-1").exists()
    else:
        assert final_document["draft_listing"]["price"] == 20.0
        assert [entry["price"] for entry in final_document["price_history"]] == [
            10.0,
            20.0,
        ]
        assert final_document["price_history"][-1]["previous_price"] == 10.0


def test_inventory_condition_http_enforces_published_tgw_vocabulary_without_mutation(
    tmp_path, monkeypatch
):
    item_path = _configure_real_item_command_storage(
        tmp_path,
        monkeypatch,
        {
            "sku": "sku-1",
            "title": "Thing",
            "condition": "Good",
            "draft_listing": {
                "category_id": "123",
                "condition_enum": "USED_GOOD",
                "item_specifics": {},
            },
        },
    )
    category_context = {
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}],
        "aspects": [],
        "record_condition_vocabulary": ["New", "Good"],
    }
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda _sku: _published_from_item_path(item_path, category_context),
    )
    client = TestClient(http_server.app)

    for supplied, configured in (("New", "New"), ("good", "Good")):
        generation = _published_from_item_path(
            item_path, category_context
        )["object_generation"]
        response = client.post(
            "/api/operator/items/sku-1/commands",
            headers=AUTH,
            json={
                "command_id": "save-inventory",
                "object_generation": generation,
                "values": {"item_fields": {"condition": supplied}},
            },
        )
        assert response.status_code == 200, response.text
        assert json.loads(item_path.read_text(encoding="utf-8"))[
            "condition"
        ] == configured

    before_fabricated = item_path.read_bytes()
    current_generation = _published_from_item_path(
        item_path, category_context
    )["object_generation"]
    fabricated = client.post(
        "/api/operator/items/sku-1/commands",
        headers=AUTH,
        json={
            "command_id": "save-inventory",
            "object_generation": current_generation,
            "values": {"item_fields": {"condition": "FABRICATED"}},
        },
    )

    assert fabricated.status_code == 422, fabricated.text
    assert "not an allowed value" in fabricated.json()["detail"]
    assert item_path.read_bytes() == before_fabricated


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
    published = _published()
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: published,
    )
    monkeypatch.setattr(http_server, "_workflow_provider_identity", lambda: "ebay:test")
    seen = {}

    def update_dispatch(path, **kwargs):
        seen.update(kwargs)
        result = SimpleNamespace(
            graph=SimpleNamespace(
                graph_id="graph-2",
                object_generation=published["object_generation"],
            ),
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
            "object_generation": published["object_generation"],
            "values": {},
        },
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
            "object_generation": published["object_generation"],
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
        workflow_card=_card(item),
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
            "object_generation": published["object_generation"],
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
        workflow_card=_card(item),
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
            "object_generation": published["object_generation"],
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
        workflow_card=_card(item),
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
            "object_generation": published["object_generation"],
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
    published = _published()
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(
        http_server,
        "_current_item_operator_object",
        lambda sku: published,
    )
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
            "object_generation": published["object_generation"],
            "values": values,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["command_id"] == command_id
    assert response.json()["object_generation"] == published["object_generation"]
