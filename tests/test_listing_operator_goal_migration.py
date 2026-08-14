import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tgw import http_server
from tgw.workflow.listing_migration import (
    GoalRequestResult,
    _evaluator_authorized_scopes,
    approved_authority_scopes,
    authorize_and_dispatch_force_restage,
    authorize_and_dispatch_next_listing_effect,
    authorize_and_request_item_goal,
    request_item_goal,
)
from tgw.workflow.profiles import (
    TGW_EBAY_DRAFTED,
    TGW_EBAY_IDENTIFIED,
    TGW_EBAY_LISTABLE,
    TGW_EBAY_STAGED,
)
from tgw.workflow.scheduler import DispatchResult
from tgw.workflow.treatments import (
    AI_IDENTIFY,
    EBAY_DRAFT,
    EBAY_PUBLISH,
    EBAY_STAGE,
    TGW_TREATMENTS,
)


@pytest.mark.parametrize(
    ("treatment", "scope", "offer_id", "expected_force"),
    (
        (EBAY_STAGE, "stage", "offer-1", True),
        (EBAY_PUBLISH, "publish", "offer-1", False),
    ),
)
def test_next_listing_effect_dispatches_exact_governed_payload(
    tmp_path, monkeypatch, treatment, scope, offer_id, expected_force,
):
    _, path = _item(tmp_path, ebay_offer={"offer_id": offer_id})
    disposition = SimpleNamespace(
        treatment_id=treatment.identity, treatment_version=treatment.version,
    )
    graph = SimpleNamespace(
        object_id="SKU-1", object_generation="gen-1",
        eligible_treatments=(disposition,), ownership_conflicts=(),
        reconciliation_gates=(), graph_id="graph-1", condition_hash="condition-1",
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
    )
    admitted = GoalRequestResult(
        graph=graph, dispatched=None, held_external=(treatment.identity,),
        operator_gates=(f"provider_contract_required:{treatment.identity}",),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.authorize_and_request_item_goal",
        lambda *args, **kwargs: (admitted, "authority-1", True),
    )
    calls = []
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.dispatch_treatment",
        lambda **kwargs: calls.append(kwargs) or DispatchResult(
            treatment_id=treatment.identity, treatment_version=treatment.version,
            queue_name=treatment.identity.replace("-", "_"), entity_id="SKU-1",
            enqueued=True, job_id="job-1", outcome="dispatched",
        ),
    )
    authority = SimpleNamespace(
        scopes=("upload", "stage", "publish"), entity_id="SKU-1",
        object_generation="gen-1", pre_authority_condition_hash="pre-hash",
    )

    _, dispatched, authority_id, created = authorize_and_dispatch_next_listing_effect(
        path, operator_identity="authenticated:dave",
        surface="http:item-action:ebay-publish", provider_identity="ebay:account",
        authority_lookup=lambda value: authority,
    )

    assert dispatched and dispatched.treatment_id == treatment.identity
    assert authority_id == "authority-1" and created is True
    payload = calls[0]["payload_extra"]
    assert payload["operator_authority_id"] == "authority-1"
    assert payload["pre_authority_condition_hash"] == "pre-hash"
    assert (payload.get("force") is True) is expected_force
    assert scope in authority.scopes


def test_item_publish_workflow_never_enqueues_legacy_generic_jobs(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {
            "ebay_publish_provider_effect": "workflow",
            "ebay_provider_identity": "ebay:account",
        },
    })
    graph = SimpleNamespace(graph_id="graph-1", object_generation="gen-1")
    result = SimpleNamespace(graph=graph)
    dispatched = DispatchResult(
        treatment_id="ebay-stage", treatment_version="1", queue_name="ebay_stage",
        entity_id="SKU-1", enqueued=True, job_id="job-governed",
    )
    captured = {}
    monkeypatch.setattr(
        listing_migration, "authorize_and_dispatch_next_listing_effect",
        lambda path, **kwargs: captured.update({"path": path, **kwargs})
        or (result, dispatched, "authority-1", True),
    )
    monkeypatch.setattr(
        http_server.state_machine, "enqueue_job",
        lambda **kwargs: pytest.fail(f"legacy enqueue used: {kwargs}"),
    )

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ebay_publish"),
        operator_identity="operator:authenticated",
    )

    assert response["ok"] is True
    assert response["status"] == "workflow_dispatched"
    assert response["job_id"] == "job-governed"
    assert response["treatment_id"] == "ebay-stage"
    assert captured["operator_identity"] == "operator:authenticated"
    assert captured["provider_identity"] == "ebay:account"


def test_next_listing_effect_preserves_dispatched_local_remediation(monkeypatch):
    local = DispatchResult(
        treatment_id="normalize-condition", treatment_version="1",
        queue_name="normalize_condition", entity_id="SKU-1", enqueued=True,
        job_id="local-job",
    )
    admitted = GoalRequestResult(
        graph=SimpleNamespace(ownership_conflicts=(), reconciliation_gates=()),
        dispatched=local, held_external=(), operator_gates=(),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.authorize_and_request_item_goal",
        lambda *args, **kwargs: (admitted, "authority-1", True),
    )

    result, dispatched, authority_id, created = (
        authorize_and_dispatch_next_listing_effect(
            "/unused/item.json", operator_identity="operator:test",
            surface="http:item-action:ebay-publish",
            provider_identity="ebay:account",
        )
    )

    assert result is admitted
    assert dispatched is local
    assert authority_id == "authority-1" and created is True


def test_authorized_local_remediation_carries_continuation_identity(tmp_path, monkeypatch):
    _, path = _item(tmp_path, condition="pre-owned")
    captured = {}
    sentinel = GoalRequestResult(
        graph=SimpleNamespace(), dispatched=None, held_external=(), operator_gates=(),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.request_item_goal",
        lambda *args, **kwargs: captured.update(kwargs) or sentinel,
    )

    result, authority_id, created = authorize_and_request_item_goal(
        path, TGW_EBAY_LISTABLE, operator_identity="operator:authenticated",
        surface="http:item-action:ebay-publish", provider_identity="ebay:account",
        scopes=("upload", "stage", "publish"),
        issuer=lambda **kwargs: ("authority-1", True),
    )

    assert result is sentinel and authority_id == "authority-1" and created is True
    assert captured["operator_identity"] == "operator:authenticated"
    assert captured["operator_surface"] == "http:item-action:ebay-publish"


def test_force_restage_dispatch_is_exact_and_authority_bound(monkeypatch):
    disposition = SimpleNamespace(treatment_id="ebay-stage", treatment_version="1")
    graph = SimpleNamespace(
        object_id="SKU-1", object_generation="gen-1",
        eligible_treatments=(disposition,), ownership_conflicts=(),
        reconciliation_gates=(), graph_id="graph-1", condition_hash="condition-1",
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
    )
    admitted = GoalRequestResult(
        graph=graph, dispatched=None, held_external=("ebay-stage",),
        operator_gates=("provider_contract_required:ebay-stage",),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.authorize_and_request_item_goal",
        lambda *args, **kwargs: (admitted, "authority-1", True),
    )
    calls = []
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.dispatch_treatment",
        lambda **kwargs: calls.append(kwargs) or DispatchResult(
            treatment_id="ebay-stage", treatment_version="1",
            queue_name="ebay_stage", entity_id="SKU-1", enqueued=True,
            job_id="job-1", outcome="dispatched",
        ),
    )
    authority = SimpleNamespace(
        scopes=("force-restage",), entity_id="SKU-1", object_generation="gen-1",
        pre_authority_condition_hash="pre-hash",
    )
    _, dispatched, authority_id, created = authorize_and_dispatch_force_restage(
        "/items/SKU-1.json", operator_identity="authenticated:dave",
        surface="http:item-action:force-restage", provider_identity="ebay:account",
        authority_lookup=lambda value: authority,
    )
    assert dispatched.job_id == "job-1"
    assert authority_id == "authority-1" and created is True
    assert calls[0]["disposition"] is disposition
    assert calls[0]["entity_id"] == "SKU-1"
    assert calls[0]["payload_extra"] == {
        "origin": "operator", "force": True,
        "operator_identity": "authenticated:dave",
        "operator_surface": "http:item-action:force-restage",
        "operator_authority_id": "authority-1",
        "pre_authority_condition_hash": "pre-hash",
    }


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


def test_goal_request_observes_legacy_on_disk_photos_without_json_image_field(
    tmp_path,
):
    """Prepare Listing and the item UI must agree on real SKU photo assets."""
    _, path = _item(tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.pop("image")
    path.write_text(json.dumps(doc), encoding="utf-8")
    (path.parent / "front.jpg").write_bytes(b"photo")
    calls = []

    result = request_item_goal(
        path, TGW_EBAY_IDENTIFIED, treatments=(AI_IDENTIFY,),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-photo",
    )

    assert result.dispatched is not None
    assert result.dispatched.job_id == "job-photo"
    assert calls[0]["queue_name"] == "ai_identify"
    fingerprints = {item.condition_id: item.result.value for item in result.graph.fingerprints}
    assert fingerprints["item_has_photos"] == "true"


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
    assert _evaluator_authorized_scopes(["force-restage"]) == (
        "force-restage", "stage",
    )
    assert _evaluator_authorized_scopes(["stage"]) == ("stage",)


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


def test_force_reidentify_then_regenerates_existing_draft(tmp_path):
    _, path = _item(
        tmp_path,
        product_lookup={"brand": "Old"},
        ebay_category_id="123",
        draft_listing={"title": "Old draft", "category_id": "123"},
        ai_reidentify=True,
        ai_redraft_requested=True,
    )
    calls = []
    first = request_item_goal(
        path, TGW_EBAY_DRAFTED, treatments=(AI_IDENTIFY, EBAY_DRAFT),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-identify",
    )
    assert first.dispatched.treatment_id == "ai-identify"

    after_identify = json.loads(path.read_text())
    after_identify["ai_reidentify"] = None
    after_identify["product_lookup"] = {"brand": "Refreshed"}
    path.write_text(json.dumps(after_identify), encoding="utf-8")

    second = request_item_goal(
        path, TGW_EBAY_DRAFTED, treatments=(AI_IDENTIFY, EBAY_DRAFT),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-draft",
    )
    assert second.dispatched.treatment_id == "ebay-draft"
    assert calls[-1]["queue_name"] == "ebay_draft"


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
    captured_goal = []
    dispatched = SimpleNamespace(job_id="job-workflow")
    goal_result = SimpleNamespace(dispatched=dispatched)
    monkeypatch.setattr(
        listing_migration, "request_item_goal",
        lambda *args, **kwargs: (
            captured_goal.append(args[1]), captured.update(kwargs), goal_result
        )[-1],
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
    from tgw.workflow.profiles import TGW_EBAY_DRAFTED

    assert captured_goal == [TGW_EBAY_DRAFTED]
    written = json.loads(path.read_text())
    assert written["ai_reidentify"] is True
    assert written["ai_redraft_requested"] is True


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
        "entity_type": "item", "entity_id": "SKU-1",
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


def test_item_stage_workflow_uses_authorized_goal_without_direct_provider_fanout(
    tmp_path, monkeypatch,
):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {
            "item_ebay_stage_fanout": "workflow",
            "ebay_provider_identity": "ebay:account",
        },
    })
    captured = {}
    graph = SimpleNamespace(
        graph_id="graph-1", object_generation="generation-1",
        unmet_requirements=("staged",), explicit_requirements=(),
        ownership_conflicts=(),
        reconciliation_gates=(),
    )
    held = SimpleNamespace(
        graph=graph, dispatched=None, held_external=("ebay-stage",),
        operator_gates=("provider_contract_required:ebay-stage",),
    )
    monkeypatch.setattr(
        listing_migration, "authorize_and_request_item_goal",
        lambda path, goal, **kwargs: captured.update(
            {"path": path, "goal": goal, **kwargs}
        ) or (held, "authority-1", True),
    )
    direct = []
    monkeypatch.setattr(http_server.state_machine, "enqueue_job",
                        lambda **kwargs: direct.append(kwargs))

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ebay_stage"),
        operator_identity="operator:authenticated",
    )

    assert response["ok"] is False
    assert response["status"] == "held"
    assert response["authority_id"] == "authority-1"
    assert direct == []
    assert captured["goal"] is TGW_EBAY_STAGED
    assert captured["operator_identity"] == "operator:authenticated"
    assert captured["surface"] == "http:item-action:ebay-stage"
    assert captured["scopes"] == ("upload", "stage")


def test_item_stage_no_dispatch_satisfied_graph_is_not_reported_held(
    tmp_path, monkeypatch,
):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {
            "item_ebay_stage_fanout": "workflow",
            "ebay_provider_identity": "ebay:account",
        },
    })
    graph = SimpleNamespace(
        graph_id="graph-satisfied", object_generation="generation-1",
        unmet_requirements=(), explicit_requirements=(), ownership_conflicts=(),
        reconciliation_gates=(),
    )
    satisfied = SimpleNamespace(
        graph=graph, dispatched=None, held_external=(), operator_gates=(),
    )
    monkeypatch.setattr(
        listing_migration, "authorize_and_request_item_goal",
        lambda *args, **kwargs: (satisfied, "authority-1", False),
    )

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ebay_stage"),
        operator_identity="operator:authenticated",
    )

    assert response["ok"] is True
    assert response["status"] == "already_satisfied"
    assert response["authority_id"] == "authority-1"


def test_item_stage_explicit_requirement_is_reported_held(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {
        "itemdata_root": root,
        "workflow_migration": {
            "item_ebay_stage_fanout": "workflow",
            "ebay_provider_identity": "ebay:account",
        },
    })
    graph = SimpleNamespace(
        graph_id="graph-explicit", object_generation="generation-1",
        unmet_requirements=(), explicit_requirements=(("staged", "UNKNOWN"),),
        ownership_conflicts=(), reconciliation_gates=(),
    )
    held = SimpleNamespace(
        graph=graph, dispatched=None, held_external=(), operator_gates=(),
    )
    monkeypatch.setattr(
        listing_migration, "authorize_and_request_item_goal",
        lambda *args, **kwargs: (held, "authority-1", False),
    )

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ebay_stage"),
        operator_identity="operator:authenticated",
    )

    assert response["ok"] is False
    assert response["status"] == "held"


def test_item_stage_defaults_to_exact_legacy_upload_stage_fanout(
    tmp_path, monkeypatch,
):
    root, _ = _item(tmp_path, draft_listing={"title": "A"})
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": root, "raw": {}})
    calls = []
    monkeypatch.setattr(
        http_server.state_machine, "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or f"job-{len(calls)}",
    )

    response = http_server.item_action(
        "SKU-1", http_server.ActionBody(action="ebay_stage"),
        operator_identity="operator:authenticated",
    )

    assert response == {
        "ok": True, "sku": "SKU-1", "action": "ebay_stage", "job_id": "job-2",
    }
    assert calls == [
        {"queue_name": "ebay_upload",
         "payload": {"sku": "SKU-1", "origin": "operator"},
         "entity_type": "item", "entity_id": "SKU-1",
         "dedupe_key": "ebay_upload:SKU-1", "max_attempts": 5},
        {"queue_name": "ebay_stage",
         "payload": {"sku": "SKU-1", "origin": "operator"},
         "entity_type": "item", "entity_id": "SKU-1",
         "dedupe_key": "ebay_stage:SKU-1", "max_attempts": 5},
    ]


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
