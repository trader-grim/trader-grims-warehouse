import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tgw import http_server
from tgw.workflow.listing_migration import approved_authority_scopes, request_item_goal
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


def test_goal_scope_ceiling_defaults_and_blocks_escalation():
    assert approved_authority_scopes(TGW_EBAY_LISTABLE, ()) == (
        "upload", "stage", "publish",
    )
    assert approved_authority_scopes(TGW_EBAY_IDENTIFIED, ()) == ()
    with pytest.raises(ValueError):
        approved_authority_scopes(TGW_EBAY_IDENTIFIED, ("publish",))
    assert approved_authority_scopes(TGW_EBAY_LISTABLE, ("force-restage",)) == (
        "force-restage",
    )


def test_goal_uses_exact_canonical_stage_marker_not_latest_effect(tmp_path, monkeypatch):
    from tgw import provider_effects

    _, path = _item(
        tmp_path, condition="Used", product_lookup={"known": True},
        ebay_category_id="123", ebay_photos=["https://photo"],
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={"offer_id": "offer-1", "provider_effect_id": "effect-exact",
                    "stage_content_identity": "content-exact"},
    )
    calls = []
    monkeypatch.setattr(
        provider_effects, "lookup_authoritative_stage_receipt",
        lambda **kwargs: calls.append(kwargs) or {
            "receipt_id": "effect-exact", "content_identity": "different",
        },
    )
    result = request_item_goal(
        path, TGW_EBAY_LISTABLE, provider_identity="ebay:account",
        authority_lookup=lambda _: None,
    )
    assert calls[0] == {
        "sku": "SKU-1", "provider_effect_id": "effect-exact",
        "stage_content_identity": "content-exact", "offer_id": "offer-1",
        "expected_provider_identity": "ebay:account",
    }
    staged = next(fp for fp in result.graph.fingerprints
                  if fp.condition_id == "staged_content_current")
    assert staged.result.value == "stale"


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
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {"ebay_provider_identity": "ebay:account"},
    })
    calls = []
    result = request_item_goal(
        root / "SKU-1" / "SKU-1.json", TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-endpoint",
    )
    captured = {}
    monkeypatch.setattr(listing_migration, "authorize_and_request_item_goal",
                        lambda *args, **kwargs: captured.update(kwargs) or
                        (result, "authority-1", True))
    response = http_server.request_workflow_goal(
        "SKU-1", http_server.WorkflowGoalBody(goal_profile_id="tgw.ebay_identified"),
        operator_identity="operator:test",
    )
    assert response["dispatched"] is True
    assert response["job_id"] == "job-endpoint"
    assert len(response["object_generation"]) == 64
    assert calls[0]["queue_name"] == "ai_identify"
    assert response["authority_id"] == "authority-1"
    assert captured["operator_identity"] == "operator:test"
    assert captured["provider_identity"] == "ebay:account"


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


def test_force_reidentify_marker_makes_identified_item_dispatchable(tmp_path):
    _, path = _item(
        tmp_path, product_lookup={"brand": "Acme"}, ai_reidentify=True,
    )
    calls = []
    result = request_item_goal(
        path, TGW_EBAY_IDENTIFIED, treatments=(AI_IDENTIFY,),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-force",
    )
    assert result.dispatched is not None
    assert result.dispatched.job_id == "job-force"
    assert calls[0]["queue_name"] == "ai_identify"


def test_item_action_ai_identify_workflow_uses_authenticated_goal_not_direct_fanout(
    tmp_path, monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(tmp_path, product_lookup={"brand": "Acme"})
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {
            "item_ai_identify_fanout": "workflow",
            "ebay_provider_identity": "ebay:account",
        },
    })
    captured = {}
    dispatched = SimpleNamespace(job_id="job-workflow")
    goal_result = SimpleNamespace(dispatched=dispatched)
    monkeypatch.setattr(
        listing_migration, "request_item_goal",
        lambda *args, **kwargs: captured.update(kwargs) or goal_result,
    )
    direct = []
    monkeypatch.setattr(http_server.state_machine, "enqueue_job",
                        lambda **kwargs: direct.append(kwargs))

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:test",
    )

    assert response["job_id"] == "job-workflow"
    assert direct == []
    assert captured["origin"] == "operator"
    assert captured["operator_identity"] == "operator:test"
    assert captured["operator_surface"] == "http:item-action:ai-identify"
    assert json.loads(path.read_text())["ai_reidentify"] is True


def test_item_action_ai_identify_defaults_to_exact_legacy_fanout(tmp_path, monkeypatch):
    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": root, "raw": {}})
    calls = []
    monkeypatch.setattr(
        http_server.state_machine, "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or "job-legacy",
    )

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:test",
    )

    assert response["job_id"] == "job-legacy"
    assert calls == [{
        "queue_name": "ai_identify",
        "payload": {"sku": "SKU-1", "origin": "operator"},
        "dedupe_key": "ai_identify:SKU-1", "max_attempts": 3,
    }]


def test_item_action_workflow_dispatch_failure_preserves_pending_intent(
    tmp_path, monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(tmp_path, product_lookup={"brand": "Acme"})
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {"item_ai_identify_fanout": "workflow"},
    })
    monkeypatch.setattr(
        listing_migration, "request_item_goal",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dispatch failed")),
    )
    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1", http_server.ActionBody(action="ai_identify"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 500
    assert json.loads(path.read_text())["ai_reidentify"] is True


def test_item_action_workflow_held_is_truthful_and_preserves_pending_intent(
    tmp_path, monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {"item_ai_identify_fanout": "workflow"},
    })
    held = SimpleNamespace(
        dispatched=None, held_external=("external",), operator_gates=("gate",),
        graph=SimpleNamespace(ownership_conflicts=("owner",),
                              reconciliation_gates=("reconcile",)),
    )
    monkeypatch.setattr(listing_migration, "request_item_goal",
                        lambda *args, **kwargs: held)
    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:test",
    )
    assert response["ok"] is False
    assert response["status"] == "held"
    assert response["operator_gates"] == ["gate"]
    assert json.loads(path.read_text())["ai_reidentify"] is True


def test_item_action_invalid_selector_preserves_503_and_does_not_mutate(
    tmp_path, monkeypatch,
):
    root, path = _item(tmp_path)
    before = path.read_text()
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {"item_ai_identify_fanout": "invalid"},
    })
    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1", http_server.ActionBody(action="ai_identify"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 503
    assert path.read_text() == before


def test_item_action_workflow_queue_generation_matches_durable_intent(
    tmp_path, monkeypatch,
):
    from tgw.workflow.item_snapshot import build_item_snapshot

    root, path = _item(tmp_path, product_lookup={"brand": "Acme"})
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {"item_ai_identify_fanout": "workflow"},
    })
    calls = []
    monkeypatch.setattr(
        http_server.state_machine, "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or "job-generation",
    )

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:authenticated",
    )

    snapshot = build_item_snapshot(path, TGW_EBAY_IDENTIFIED,
                                   treatments=(AI_IDENTIFY,))
    assert response["job_id"] == "job-generation"
    assert calls[0]["payload"]["object_generation"] == snapshot.generation
    assert calls[0]["payload"]["operator_identity"] == (
        "operator:authenticated"
    )
    assert calls[0]["payload"]["operator_surface"] == (
        "http:item-action:ai-identify"
    )


def test_workflow_goal_endpoint_reports_enqueue_failure(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {"ebay_provider_identity": "ebay:account"},
    })
    monkeypatch.setattr(
        listing_migration, "authorize_and_request_item_goal",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dispatch failed")),
    )
    with pytest.raises(HTTPException) as caught:
        http_server.request_workflow_goal(
            "SKU-1", http_server.WorkflowGoalBody(goal_profile_id="tgw.ebay_identified"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 503
