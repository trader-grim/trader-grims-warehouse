from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from tgw import http_server
from tgw.operator_objects import build_item_operator_object
from tgw.workflow import action_cards, listing_migration

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
    }


def _published():
    return build_item_operator_object(
        item={
            "sku": "sku-1",
            "draft_listing": {
                "category_id": "123", "condition_enum": "USED_GOOD",
                "condition_label": "Used - Good", "item_specifics": {},
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
    (item_dir / "sku-1.json").write_text(json.dumps({
        "sku": "sku-1",
        "draft_listing": {
            "category_id": "123", "condition_enum": "USED_GOOD",
            "condition_label": "Used - Good", "item_specifics": {},
        },
        "ebay_offer": {"offer_id": "offer-1", "status": "UNPUBLISHED"},
    }), encoding="utf-8")
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": tmp_path})
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_workflow_attempt_rows", lambda sku: [])
    monkeypatch.setattr(http_server, "_workflow_reconciled_provider_effect_ids", lambda rows: frozenset())
    monkeypatch.setattr(action_cards, "build_item_action_card", lambda *args, **kwargs: _card())
    monkeypatch.setattr(http_server, "ebay_category_context", lambda *args, **kwargs: {
        "conditions": [{"enum": "USED_GOOD", "label": "Used - Good"}], "aspects": [],
    })

    response = TestClient(http_server.app).get("/api/operator/items/sku-1", headers=AUTH)

    assert response.status_code == 200
    payload = response.json()["object"]
    assert payload["schema"] == "tgw-operator-object/v1"
    assert payload["object_generation"] == "gen-1"
    assert payload["commands"][0]["id"] == "list-item"


def test_command_rejects_stale_generation_before_dispatch(monkeypatch):
    monkeypatch.setattr(http_server, "_api_key", AUTH["Authorization"].removeprefix("Bearer "))
    monkeypatch.setattr(http_server, "_current_item_operator_object", lambda sku: _published())

    response = TestClient(http_server.app).post(
        "/api/operator/items/sku-1/commands", headers=AUTH,
        json={"command_id": "list-item", "object_generation": "stale", "values": {}},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "generation_conflict"


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
            held_external=(), operator_gates=(),
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
        "/api/operator/items/sku-1/commands", headers=AUTH,
        json={"command_id": "update-item", "object_generation": "gen-1", "values": {}},
    )

    assert response.status_code == 200
    assert response.json()["authority_scope"] == "update-restage"
    assert seen["surface"] == "http:operator-object:update-item"
