import json

import pytest
from fastapi import HTTPException

from tgw import http_server
from tgw.workflow.listing_migration import request_item_goal
from tgw.workflow.profiles import TGW_EBAY_IDENTIFIED, TGW_EBAY_LISTABLE
from tgw.workflow.treatments import AI_IDENTIFY, TGW_TREATMENTS


def _item(tmp_path, **fields):
    root = tmp_path / "items"
    path = root / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    doc = {"sku": "SKU-1", "image": "front.jpg", **fields}
    path.write_text(json.dumps(doc), encoding="utf-8")
    return root, path


def test_goal_request_is_generation_bound_and_dispatches_one_local(tmp_path):
    _, path = _item(tmp_path)
    calls = []
    result = request_item_goal(
        path, TGW_EBAY_IDENTIFIED, treatments=(AI_IDENTIFY,),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-1",
    )
    assert result.dispatched and result.dispatched.enqueued
    assert len(calls) == 1
    payload = calls[0]["payload"]
    assert payload["graph_id"] == result.graph.graph_id
    assert payload["object_generation"] == result.graph.object_generation
    assert payload["condition_hash"] == result.graph.condition_hash
    assert payload["origin"] == "operator"


def test_external_only_goal_is_held_with_provider_gate(tmp_path):
    _, path = _item(
        tmp_path, condition="Used", product_lookup={"known": True},
        ebay_category_id="123", ebay_photos=["https://photo"],
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={"offer_id": "offer-1"},
    )
    calls = []
    result = request_item_goal(
        path, TGW_EBAY_LISTABLE, treatments=TGW_TREATMENTS,
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "never",
    )
    assert calls == []
    assert "ebay-publish" in result.held_external
    assert "provider_contract_required:ebay-publish" in result.operator_gates


def test_local_remediation_dispatches_even_when_external_is_also_eligible(tmp_path):
    _, path = _item(
        tmp_path, condition="pre-owned", product_lookup={"known": True},
        ebay_category_id="123", ebay_photos=["https://photo"],
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={"offer_id": "offer-1"},
    )
    calls = []
    result = request_item_goal(
        path, TGW_EBAY_LISTABLE,
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "normalize-job",
    )
    assert calls[0]["queue_name"] == "normalize_condition"
    assert result.dispatched.treatment_id == "normalize-condition"
    assert "ebay-publish" in result.held_external


def test_workflow_goal_endpoint_dispatches_local_and_returns_generation(tmp_path, monkeypatch):
    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": root})
    calls = []
    monkeypatch.setattr(
        http_server.state_machine, "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or "job-endpoint",
    )
    response = http_server.request_workflow_goal(
        "SKU-1", http_server.WorkflowGoalBody(goal_profile_id="tgw.ebay_identified"),
    )
    assert response["dispatched"] is True
    assert response["job_id"] == "job-endpoint"
    assert len(response["object_generation"]) == 64
    assert calls[0]["queue_name"] == "ai_identify"


def test_goal_request_raises_when_enqueue_actually_fails(tmp_path):
    _, path = _item(tmp_path)

    def fail(**kwargs):
        raise RuntimeError("database unavailable")

    try:
        request_item_goal(
            path, TGW_EBAY_IDENTIFIED, treatments=(AI_IDENTIFY,), enqueue_fn=fail,
        )
    except RuntimeError as exc:
        assert "failed to dispatch workflow treatment ai-identify" in str(exc)
    else:
        raise AssertionError("actual enqueue failure must not look successful")


def test_goal_request_allows_idempotent_already_dispatched(tmp_path):
    _, path = _item(tmp_path)

    class Duplicate(Exception):
        pgcode = "23505"

    def duplicate(**kwargs):
        raise Duplicate()

    result = request_item_goal(
        path, TGW_EBAY_IDENTIFIED, treatments=(AI_IDENTIFY,), enqueue_fn=duplicate,
    )
    assert result.dispatched is not None
    assert result.dispatched.enqueued is False
    assert result.dispatched.outcome == "already_dispatched"


def test_workflow_goal_endpoint_reports_enqueue_failure(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": root})
    monkeypatch.setattr(
        listing_migration, "request_item_goal",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dispatch failed")),
    )
    with pytest.raises(HTTPException) as caught:
        http_server.request_workflow_goal(
            "SKU-1", http_server.WorkflowGoalBody(goal_profile_id="tgw.ebay_identified"),
        )
    assert caught.value.status_code == 503
