import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from tgw import http_server
from tgw.config import bind_ebay_provider_identity
from tgw.item_mutation import item_generation
from tgw.workflow.listing_migration import (
    GoalRequestResult,
    _evaluator_authorized_scopes,
    _require_provider_target_environment,
    approved_authority_scopes,
    authorize_and_dispatch_force_restage,
    authorize_and_dispatch_next_listing_effect,
    authorize_and_execute_end_listing,
    authorize_and_request_item_goal,
    request_item_goal,
)
from tgw.workflow.profiles import (
    TGW_EBAY_DRAFTED,
    TGW_EBAY_IDENTIFIED,
    TGW_EBAY_LISTABLE,
    TGW_EBAY_PRICED,
    TGW_EBAY_WITHDRAWN,
)
from tgw.workflow.treatments import (
    AI_IDENTIFY,
    EBAY_DRAFT,
    EBAY_PUBLISH,
    EBAY_STAGE,
    TGW_TREATMENTS,
)
from tgw.workflow_kernel.scheduler import DispatchResult


@pytest.mark.parametrize(
    ("treatment", "scope", "offer_id", "expected_force"),
    (
        (EBAY_STAGE, "stage", "offer-1", True),
        (EBAY_PUBLISH, "publish", "offer-1", False),
    ),
)
def test_next_listing_effect_dispatches_exact_governed_payload(
    tmp_path,
    monkeypatch,
    treatment,
    scope,
    offer_id,
    expected_force,
):
    _, path = _item(tmp_path, ebay_offer={"offer_id": offer_id})
    generation = item_generation(json.loads(path.read_text(encoding="utf-8")))
    disposition = SimpleNamespace(
        treatment_id=treatment.identity,
        treatment_version=treatment.version,
    )
    graph = SimpleNamespace(
        object_id="SKU-1",
        object_generation=generation,
        eligible_treatments=(disposition,),
        ownership_conflicts=(),
        reconciliation_gates=(),
        graph_id="graph-1",
        condition_hash="condition-1",
        goal_profile_id="tgw.ebay_listable",
        goal_profile_version="1",
    )
    admitted = GoalRequestResult(
        graph=graph,
        dispatched=None,
        held_external=(treatment.identity,),
        operator_gates=(f"provider_contract_required:{treatment.identity}",),
    )
    admission = {}
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.authorize_and_request_item_goal",
        lambda *args, **kwargs: admission.update(kwargs) or (admitted, "authority-1", True),
    )
    calls = []
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.dispatch_treatment",
        lambda **kwargs: (
            calls.append(kwargs)
            or DispatchResult(
                treatment_id=treatment.identity,
                treatment_version=treatment.version,
                queue_name=treatment.identity.replace("-", "_"),
                entity_id="SKU-1",
                enqueued=True,
                job_id="job-1",
                outcome="dispatched",
            )
        ),
    )
    authority = SimpleNamespace(
        scopes=("upload", "stage", "publish", "force-restage"),
        entity_id="SKU-1",
        object_generation=generation,
        pre_authority_condition_hash="pre-hash",
    )

    _, dispatched, authority_id, created = authorize_and_dispatch_next_listing_effect(
        path,
        operator_identity="authenticated:dave",
        surface="http:item-action:ebay-publish",
        provider_identity="ebay:account",
        authority_lookup=lambda value: authority,
    )

    assert dispatched and dispatched.treatment_id == treatment.identity
    assert authority_id == "authority-1" and created is True
    payload = calls[0]["payload_extra"]
    assert payload["operator_authority_id"] == "authority-1"
    assert payload["pre_authority_condition_hash"] == "pre-hash"
    assert (payload.get("force") is True) is expected_force
    assert ("force-restage" if expected_force else scope) in authority.scopes
    assert admission["scopes"] == (
        "upload",
        "stage",
        "publish",
        "force-restage",
    )


def test_legacy_item_publish_action_is_rejected_before_dispatch(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "ebay_publish_provider_effect": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    graph = SimpleNamespace(graph_id="graph-1", object_generation="gen-1")
    result = SimpleNamespace(graph=graph)
    dispatched = DispatchResult(
        treatment_id="ebay-stage",
        treatment_version="1",
        queue_name="ebay_stage",
        entity_id="SKU-1",
        enqueued=True,
        job_id="job-governed",
    )
    captured = {}
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        lambda path, **kwargs: captured.update({"path": path, **kwargs}) or (result, dispatched, "authority-1", True),
    )
    monkeypatch.setattr(
        http_server.state_machine,
        "enqueue_job",
        lambda **kwargs: pytest.fail(f"legacy enqueue used: {kwargs}"),
    )

    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_publish"),
            operator_identity="operator:authenticated",
        )
    assert caught.value.status_code == 400
    assert captured == {}


def test_legacy_item_publish_cannot_mutate_condition_before_rejection(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(
        tmp_path,
        draft_listing={
            "category_id": "171175",
            "condition_id": "3000",
            "condition_label": "Used",
            "condition_enum": "USED_EXCELLENT",
        },
    )
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "ebay_publish_provider_effect": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.allowed_conditions_for_category",
        lambda cfg, category_id: [
            {
                "condition_id": "5000",
                "condition_label": "Good",
                "condition_enum": "USED_GOOD",
            }
        ],
    )
    monkeypatch.setattr(
        "tgw.apis.ebay.conditions.best_condition_for_enum",
        lambda cfg, category_id, current: {
            "condition_id": "5000",
            "condition_label": "Good",
            "condition_enum": "USED_GOOD",
        },
    )
    seen = {}
    dispatched = DispatchResult(
        treatment_id="ebay-stage",
        treatment_version="1",
        queue_name="ebay_stage",
        entity_id="SKU-1",
        enqueued=True,
        job_id="job-1",
    )
    graph = SimpleNamespace(graph_id="graph-1", object_generation="generation-1")

    def _authorize(observed_path, **kwargs):
        seen.update(json.loads(observed_path.read_text(encoding="utf-8")))
        return SimpleNamespace(graph=graph), dispatched, "authority-1", True

    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_next_listing_effect",
        _authorize,
    )

    before = path.read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_publish"),
            operator_identity="operator:authenticated",
        )
    assert caught.value.status_code == 400
    assert seen == {}
    assert path.read_text(encoding="utf-8") == before


def test_next_listing_effect_preserves_dispatched_local_remediation(monkeypatch):
    local = DispatchResult(
        treatment_id="normalize-condition",
        treatment_version="1",
        queue_name="normalize_condition",
        entity_id="SKU-1",
        enqueued=True,
        job_id="local-job",
    )
    admitted = GoalRequestResult(
        graph=SimpleNamespace(ownership_conflicts=(), reconciliation_gates=()),
        dispatched=local,
        held_external=(),
        operator_gates=(),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.authorize_and_request_item_goal",
        lambda *args, **kwargs: (admitted, "authority-1", True),
    )

    result, dispatched, authority_id, created = authorize_and_dispatch_next_listing_effect(
        "/unused/item.json",
        operator_identity="operator:test",
        surface="http:item-action:ebay-publish",
        provider_identity="ebay:account",
    )

    assert result is admitted
    assert dispatched is local
    assert authority_id == "authority-1" and created is True


def test_authorized_local_remediation_carries_continuation_identity(tmp_path, monkeypatch):
    _, path = _item(tmp_path, condition="pre-owned")
    captured = {}
    sentinel = GoalRequestResult(
        graph=SimpleNamespace(),
        dispatched=None,
        held_external=(),
        operator_gates=(),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.request_item_goal",
        lambda *args, **kwargs: captured.update(kwargs) or sentinel,
    )

    result, authority_id, created = authorize_and_request_item_goal(
        path,
        TGW_EBAY_LISTABLE,
        operator_identity="operator:authenticated",
        surface="http:item-action:ebay-publish",
        provider_identity="ebay:account",
        scopes=("upload", "stage", "publish"),
        issuer=lambda **kwargs: ("authority-1", True),
    )

    assert result is sentinel and authority_id == "authority-1" and created is True
    assert captured["operator_identity"] == "operator:authenticated"
    assert captured["operator_surface"] == "http:item-action:ebay-publish"


def test_force_restage_dispatch_is_exact_and_authority_bound(monkeypatch, tmp_path):
    _, path = _item(tmp_path)
    generation = item_generation(json.loads(path.read_text(encoding="utf-8")))
    disposition = SimpleNamespace(treatment_id="ebay-stage", treatment_version="1")
    graph = SimpleNamespace(
        object_id="SKU-1",
        object_generation=generation,
        eligible_treatments=(disposition,),
        ownership_conflicts=(),
        reconciliation_gates=(),
        graph_id="graph-1",
        condition_hash="condition-1",
        goal_profile_id="tgw.ebay_listable",
        goal_profile_version="1",
    )
    admitted = GoalRequestResult(
        graph=graph,
        dispatched=None,
        held_external=("ebay-stage",),
        operator_gates=("provider_contract_required:ebay-stage",),
    )
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.authorize_and_request_item_goal",
        lambda *args, **kwargs: (admitted, "authority-1", True),
    )
    calls = []
    monkeypatch.setattr(
        "tgw.workflow.listing_migration.dispatch_treatment",
        lambda **kwargs: (
            calls.append(kwargs)
            or DispatchResult(
                treatment_id="ebay-stage",
                treatment_version="1",
                queue_name="ebay_stage",
                entity_id="SKU-1",
                enqueued=True,
                job_id="job-1",
                outcome="dispatched",
            )
        ),
    )
    authority = SimpleNamespace(
        scopes=("force-restage",),
        entity_id="SKU-1",
        object_generation=generation,
        pre_authority_condition_hash="pre-hash",
    )
    _, dispatched, authority_id, created = authorize_and_dispatch_force_restage(
        path,
        operator_identity="authenticated:dave",
        surface="http:item-action:force-restage",
        provider_identity="ebay:account",
        authority_lookup=lambda value: authority,
    )
    assert dispatched.job_id == "job-1"
    assert authority_id == "authority-1" and created is True
    assert calls[0]["disposition"] is disposition
    assert calls[0]["entity_id"] == "SKU-1"
    assert calls[0]["payload_extra"] == {
        "origin": "operator",
        "force": True,
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
        path,
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
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
        path,
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-photo",
    )

    assert result.dispatched is not None
    assert result.dispatched.job_id == "job-photo"
    assert calls[0]["queue_name"] == "ai_identify"
    fingerprints = {item.condition_id: item.result.value for item in result.graph.fingerprints}
    assert fingerprints["item_has_photos"] == "true"


def test_goal_scope_ceiling_defaults_and_blocks_escalation():
    assert approved_authority_scopes(TGW_EBAY_LISTABLE, ()) == (
        "upload",
        "stage",
        "publish",
    )
    assert approved_authority_scopes(TGW_EBAY_IDENTIFIED, ()) == ()
    with pytest.raises(ValueError):
        approved_authority_scopes(TGW_EBAY_IDENTIFIED, ("publish",))
    assert approved_authority_scopes(TGW_EBAY_LISTABLE, ("force-restage",)) == ("force-restage",)
    assert _evaluator_authorized_scopes(["force-restage"]) == (
        "force-restage",
        "stage",
    )
    assert _evaluator_authorized_scopes(["stage"]) == ("stage",)
    assert approved_authority_scopes(TGW_EBAY_WITHDRAWN, ()) == ("withdraw",)


@pytest.mark.parametrize("replay", [False, True], ids=("fresh", "succeeded-replay"))
def test_end_listing_uses_exact_withdraw_authority_and_reserved_effect(
    tmp_path,
    monkeypatch,
    replay,
):
    from datetime import UTC, datetime, timedelta

    from tgw.workflow import listing_migration
    from tgw.workflow.treatments import EBAY_WITHDRAW

    _, path = _item(
        tmp_path,
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={
            "offer_id": "offer-1",
            "status": "PUBLISHED",
            "ebay_environment": "sandbox",
        },
        ebay_listing={
            "listing_id": "listing-1",
            "status": "ACTIVE",
            "ebay_environment": "sandbox",
        },
    )
    generation = item_generation(json.loads(path.read_text(encoding="utf-8")))
    snapshot = SimpleNamespace(object_id="SKU-1", generation=generation)
    disposition = SimpleNamespace(
        treatment_id=EBAY_WITHDRAW.identity,
        treatment_version=EBAY_WITHDRAW.version,
    )
    base_graph = SimpleNamespace(
        waiting_treatments=(disposition,),
        condition_hash="pre-authority-condition",
    )
    admitted_graph = SimpleNamespace(
        eligible_treatments=(disposition,),
        ownership_conflicts=(),
        reconciliation_gates=(),
        graph_id="withdraw-graph",
        condition_hash="authorized-condition",
    )
    graphs = iter((base_graph, admitted_graph))
    monkeypatch.setattr(listing_migration, "build_item_snapshot", lambda *args, **kwargs: snapshot)
    monkeypatch.setattr(listing_migration, "evaluate", lambda **kwargs: next(graphs))

    issued = {}

    def issuer(**values):
        issued.update(values)
        return "authority-1", True

    now = datetime.now(UTC)
    authority = SimpleNamespace(
        authority_id="authority-1",
        superseded_at=None,
        entity_id="SKU-1",
        goal_profile_id=TGW_EBAY_WITHDRAWN.identity,
        goal_profile_version=TGW_EBAY_WITHDRAWN.version,
        object_generation=generation,
        pre_authority_condition_hash="pre-authority-condition",
        content_identity=listing_migration.listing_content_identity(json.loads(path.read_text())),
        provider_identity=bind_ebay_provider_identity(
            "ebay:sandbox-account", "sandbox",
        ),
        scopes=("withdraw",),
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=5),
    )
    reserved = {}

    def reserve(**values):
        reserved.update(values)
        if replay:
            return SimpleNamespace(
                effect_id="effect-1",
                state="succeeded",
                result={
                    "offer_id": "offer-1",
                    "listing_id": "listing-1",
                    "ended_at": "2026-08-22T12:00:00+00:00",
                    "ebay_environment": "sandbox",
                    "endpoint": "https://api.sandbox.ebay.com",
                    "provider_response": {"withdrawn": True},
                },
            )
        return SimpleNamespace(effect_id="effect-1", state="dispatched", result=None)

    finished = {}

    def finish(effect_id, **values):
        finished.update({"effect_id": effect_id, **values})
        return SimpleNamespace(effect_id=effect_id, state=values["state"], result=values.get("result"))

    provider_calls = []
    graph, effect, authority_id, created, projection = authorize_and_execute_end_listing(
        path,
        config={
            "ebay_environment": "sandbox",
            "itemdata_root": tmp_path,
            "archive_root": tmp_path / "archive",
            "item_mutation_journal_root": tmp_path / "mutations",
        },
        operator_identity="operator:dave",
        surface="http:operator-object:end-listing",
        provider_identity="ebay:sandbox-account",
        issuer=issuer,
        authority_lookup=lambda value: authority if value == "authority-1" else None,
        reserve_effect=reserve,
        finish_effect=finish,
        inventory_withdraw=lambda offer_id: provider_calls.append(offer_id) or {"withdrawn": True},
        project_item=lambda sku, document: {"ok": True},
    )

    assert graph is admitted_graph
    assert effect.state == "succeeded"
    assert authority_id == "authority-1" and created is True
    assert issued["scopes"] == ("withdraw",)
    assert reserved["authority_scope"] == "withdraw"
    assert reserved["operation"] == "withdraw-offer"
    assert reserved["request"]["offer_id"] == "offer-1"
    assert reserved["request"]["ebay_environment"] == "sandbox"
    assert reserved["request"]["endpoint"] == "https://api.sandbox.ebay.com"
    assert provider_calls == ([] if replay else ["offer-1"])
    if replay:
        assert finished == {}
    else:
        assert finished["state"] == "succeeded"
    assert projection.status == "COMMITTED"
    projected = json.loads(path.read_text(encoding="utf-8"))
    assert projected["ebay_listing"]["status"] == "Ended"
    assert projected["ebay_offer"]["status"] == "UNPUBLISHED"
    assert projected["ebay_listing"]["provider_effect_id"] == "effect-1"


def test_end_listing_lock_excludes_concurrent_provider_target_mutation(
    tmp_path,
    monkeypatch,
):
    """A competing target edit cannot land between admission and dispatch."""
    import threading
    from datetime import UTC, datetime, timedelta

    from tgw import item_mutation
    from tgw.item_mutation import mutate_item
    from tgw.workflow import listing_migration
    from tgw.workflow.treatments import EBAY_WITHDRAW

    _, path = _item(
        tmp_path,
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={
            "offer_id": "offer-1",
            "status": "PUBLISHED",
            "ebay_environment": "sandbox",
        },
        ebay_listing={
            "listing_id": "listing-1",
            "status": "ACTIVE",
            "ebay_environment": "sandbox",
        },
    )
    generation = item_generation(json.loads(path.read_text(encoding="utf-8")))
    snapshot = SimpleNamespace(object_id="SKU-1", generation=generation)
    disposition = SimpleNamespace(
        treatment_id=EBAY_WITHDRAW.identity,
        treatment_version=EBAY_WITHDRAW.version,
    )
    graphs = iter((
        SimpleNamespace(
            waiting_treatments=(disposition,),
            condition_hash="pre-authority-condition",
        ),
        SimpleNamespace(
            eligible_treatments=(disposition,),
            ownership_conflicts=(),
            reconciliation_gates=(),
            graph_id="withdraw-graph",
            condition_hash="authorized-condition",
        ),
    ))
    monkeypatch.setattr(
        listing_migration,
        "build_item_snapshot",
        lambda *args, **kwargs: snapshot,
    )
    monkeypatch.setattr(
        listing_migration,
        "evaluate",
        lambda **kwargs: next(graphs),
    )
    now = datetime.now(UTC)
    authority = SimpleNamespace(
        authority_id="authority-1",
        superseded_at=None,
        entity_id="SKU-1",
        goal_profile_id=TGW_EBAY_WITHDRAWN.identity,
        goal_profile_version=TGW_EBAY_WITHDRAWN.version,
        object_generation=generation,
        pre_authority_condition_hash="pre-authority-condition",
        content_identity=listing_migration.listing_content_identity(
            json.loads(path.read_text(encoding="utf-8"))
        ),
        provider_identity=bind_ebay_provider_identity(
            "ebay:sandbox-account", "sandbox",
        ),
        scopes=("withdraw",),
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=5),
    )
    config = {
        "ebay_environment": "sandbox",
        "itemdata_root": tmp_path,
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "mutations",
    }
    writer_lock_probe_done = threading.Event()
    writer_lock_probe_results = []
    writer_finished = threading.Event()
    writer_receipts = []
    writer_threads = []
    writer_thread_name = "concurrent-provider-target-writer"
    real_flock = item_mutation.fcntl.flock

    def observe_writer_lock(descriptor, operation):
        if (
            threading.current_thread().name != writer_thread_name
            or operation != item_mutation.fcntl.LOCK_EX
        ):
            return real_flock(descriptor, operation)
        try:
            real_flock(
                descriptor,
                item_mutation.fcntl.LOCK_EX | item_mutation.fcntl.LOCK_NB,
            )
        except BlockingIOError:
            writer_lock_probe_results.append("blocked")
            writer_lock_probe_done.set()
            return real_flock(descriptor, item_mutation.fcntl.LOCK_EX)
        writer_lock_probe_results.append("acquired")
        writer_lock_probe_done.set()
        return None

    monkeypatch.setattr(item_mutation.fcntl, "flock", observe_writer_lock)

    def race_target(document):
        updated = json.loads(json.dumps(document))
        updated["ebay_offer"]["offer_id"] = "offer-raced"
        return updated

    def concurrent_writer():
        writer_receipts.append(mutate_item(
            item_path=path,
            archive_root=config["archive_root"],
            journal_root=config["item_mutation_journal_root"],
            sku="SKU-1",
            kind="test:concurrent-provider-target",
            expected_generation=generation,
            payload={"offer_id": "offer-raced"},
            mutate=race_target,
            project=lambda sku, document: {"ok": True},
        ))
        writer_finished.set()

    def reserve(**values):
        thread = threading.Thread(
            target=concurrent_writer,
            name=writer_thread_name,
            daemon=True,
        )
        writer_threads.append(thread)
        thread.start()
        assert writer_lock_probe_done.wait(1), (
            "concurrent target writer did not reach the item-lock acquisition"
        )
        assert writer_lock_probe_results == ["blocked"], (
            "concurrent target writer acquired the item lock during effect admission"
        )
        assert not writer_finished.is_set()
        return SimpleNamespace(
            effect_id="effect-1", state="dispatched", result=None,
        )

    def finish(effect_id, **values):
        return SimpleNamespace(
            effect_id=effect_id,
            state=values["state"],
            result=values.get("result"),
        )

    provider_calls = []
    *_, projection = authorize_and_execute_end_listing(
        path,
        config=config,
        operator_identity="operator:dave",
        surface="http:operator-object:end-listing",
        provider_identity="ebay:sandbox-account",
        issuer=lambda **kwargs: ("authority-1", True),
        authority_lookup=lambda value: (
            authority if value == "authority-1" else None
        ),
        reserve_effect=reserve,
        finish_effect=finish,
        inventory_withdraw=lambda offer_id: (
            provider_calls.append(offer_id) or {"withdrawn": True}
        ),
        project_item=lambda sku, document: {"ok": True},
    )
    writer_threads[0].join(timeout=2)

    assert projection.status == "COMMITTED"
    assert provider_calls == ["offer-1"]
    assert writer_finished.is_set()
    assert writer_receipts[0].status == "CONFLICT"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["ebay_offer"]["offer_id"] == "offer-1"
    assert persisted["ebay_offer"]["status"] == "UNPUBLISHED"
    assert persisted["ebay_listing"]["status"] == "Ended"


def test_end_listing_target_environment_is_fail_closed_with_prod_legacy_only():
    _require_provider_target_environment(
        {"offer_id": "legacy-production"},
        "production",
        "ebay_offer",
    )
    with pytest.raises(ValueError, match="production-only"):
        _require_provider_target_environment(
            {"offer_id": "untagged-sandbox"},
            "sandbox",
            "ebay_offer",
        )
    with pytest.raises(ValueError, match="does not match"):
        _require_provider_target_environment(
            {
                "offer_id": "wrong-environment",
                "ebay_environment": "production",
            },
            "sandbox",
            "ebay_offer",
        )
    with pytest.raises(ValueError, match="does not match"):
        _require_provider_target_environment(
            {
                "listing_id": "unknown-environment",
                "ebay_environment": "staging",
            },
            "production",
            "ebay_listing",
        )


def test_end_listing_rejects_untagged_sandbox_target_before_authority(tmp_path):
    _, path = _item(
        tmp_path,
        ebay_offer={"offer_id": "legacy-offer", "status": "PUBLISHED"},
    )
    authority_calls = []

    with pytest.raises(ValueError, match="production-only"):
        authorize_and_execute_end_listing(
            path,
            config={"ebay_environment": "sandbox"},
            operator_identity="operator:dave",
            surface="http:operator-object:end-listing",
            provider_identity="ebay:sandbox-account",
            issuer=lambda **kwargs: authority_calls.append(kwargs),
        )

    assert authority_calls == []


def test_update_dispatch_requires_fresh_stage_even_when_item_is_published(monkeypatch):
    from tgw.workflow import listing_migration

    captured = {}
    expected = SimpleNamespace(dispatched=SimpleNamespace(job_id="job-update"))

    def authorize(*args, **kwargs):
        captured.update(kwargs)
        return expected, "authority-1", True

    monkeypatch.setattr(
        listing_migration,
        "authorize_and_request_item_goal",
        authorize,
    )

    result, dispatched, authority_id, created = (
        listing_migration.authorize_and_dispatch_update_item(
            "/items/SKU-1.json",
            operator_identity="operator:test",
            surface="http:operator-object:update-item",
            provider_identity="ebay:account",
            item_document={"sku": "SKU-1"},
        )
    )

    assert result is expected
    assert dispatched.job_id == "job-update"
    assert authority_id == "authority-1"
    assert created is True
    assert captured["require_current_stage_when_published"] is True


def test_goal_uses_exact_canonical_stage_marker_not_latest_effect(tmp_path, monkeypatch):
    from tgw import provider_effects

    _, path = _item(
        tmp_path,
        condition="Used",
        product_lookup={"known": True},
        ebay_category_id="123",
        ebay_photos=["https://photo"],
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={"offer_id": "offer-1", "provider_effect_id": "effect-exact", "stage_content_identity": "content-exact"},
    )
    calls = []
    monkeypatch.setattr(
        provider_effects,
        "lookup_authoritative_stage_receipt",
        lambda **kwargs: (
            calls.append(kwargs)
            or {
                "receipt_id": "effect-exact",
                "content_identity": "different",
            }
        ),
    )
    result = request_item_goal(
        path,
        TGW_EBAY_LISTABLE,
        provider_identity="ebay:account",
        authority_lookup=lambda _: None,
    )
    assert calls[0] == {
        "sku": "SKU-1",
        "provider_effect_id": "effect-exact",
        "stage_content_identity": "content-exact",
        "offer_id": "offer-1",
        "expected_provider_identity": "ebay:account",
    }
    staged = next(fp for fp in result.graph.fingerprints if fp.condition_id == "staged_content_current")
    assert staged.result.value == "stale"


def test_external_only_goal_is_held_with_provider_gate(tmp_path):
    _, path = _item(
        tmp_path,
        condition="Used",
        product_lookup={"known": True},
        ebay_category_id="123",
        ebay_photos=["https://photo"],
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={"offer_id": "offer-1"},
    )
    calls = []
    result = request_item_goal(
        path,
        TGW_EBAY_LISTABLE,
        treatments=TGW_TREATMENTS,
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "never",
    )
    assert calls == []
    assert "ebay-publish" in result.held_external
    assert "provider_contract_required:ebay-publish" in result.operator_gates


def test_local_remediation_dispatches_even_when_external_is_also_eligible(tmp_path):
    _, path = _item(
        tmp_path,
        condition="pre-owned",
        product_lookup={"known": True},
        ebay_category_id="123",
        ebay_photos=["https://photo"],
        draft_listing={"title": "Ready", "category_id": "123", "price": 10},
        ebay_offer={"offer_id": "offer-1"},
    )
    calls = []
    result = request_item_goal(
        path,
        TGW_EBAY_LISTABLE,
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "normalize-job",
    )
    assert calls[0]["queue_name"] == "normalize_condition"
    assert result.dispatched.treatment_id == "normalize-condition"
    assert "ebay-publish" in result.held_external


def test_workflow_goal_endpoint_dispatches_local_and_returns_generation(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"ebay_provider_identity": "ebay:account"},
        },
    )
    calls = []
    result = request_item_goal(
        root / "SKU-1" / "SKU-1.json",
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-endpoint",
    )
    captured = {}
    monkeypatch.setattr(listing_migration, "authorize_and_request_item_goal", lambda *args, **kwargs: captured.update(kwargs) or (result, "authority-1", True))
    response = http_server.request_workflow_goal(
        "SKU-1",
        http_server.WorkflowGoalBody(goal_profile_id="tgw.ebay_identified"),
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
            path,
            TGW_EBAY_IDENTIFIED,
            treatments=(AI_IDENTIFY,),
            enqueue_fn=fail,
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
        path,
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
        enqueue_fn=duplicate,
    )
    assert result.dispatched is not None
    assert result.dispatched.enqueued is False
    assert result.dispatched.outcome == "already_dispatched"


def test_force_reidentify_marker_makes_identified_item_dispatchable(tmp_path):
    _, path = _item(
        tmp_path,
        product_lookup={"brand": "Acme"},
        ai_reidentify=True,
    )
    calls = []
    result = request_item_goal(
        path,
        TGW_EBAY_IDENTIFIED,
        treatments=(AI_IDENTIFY,),
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
        path,
        TGW_EBAY_DRAFTED,
        treatments=(AI_IDENTIFY, EBAY_DRAFT),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-identify",
    )
    assert first.dispatched.treatment_id == "ai-identify"

    after_identify = json.loads(path.read_text())
    after_identify["ai_reidentify"] = None
    after_identify["product_lookup"] = {"brand": "Refreshed"}
    path.write_text(json.dumps(after_identify), encoding="utf-8")

    second = request_item_goal(
        path,
        TGW_EBAY_DRAFTED,
        treatments=(AI_IDENTIFY, EBAY_DRAFT),
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-draft",
    )
    assert second.dispatched.treatment_id == "ebay-draft"
    assert calls[-1]["queue_name"] == "ebay_draft"


def test_item_action_ai_identify_workflow_uses_authenticated_goal_not_direct_fanout(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(tmp_path, product_lookup={"brand": "Acme"})
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "item_ai_identify_fanout": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    captured = {}
    captured_goal = []
    dispatched = SimpleNamespace(job_id="job-workflow")
    goal_result = SimpleNamespace(dispatched=dispatched)
    monkeypatch.setattr(
        listing_migration,
        "request_item_goal",
        lambda *args, **kwargs: (captured_goal.append(args[1]), captured.update(kwargs), goal_result)[-1],
    )
    direct = []
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda **kwargs: direct.append(kwargs))

    response = http_server.item_action(
        "SKU-1",
        http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:test",
    )

    assert response["job_id"] == "job-workflow"
    assert direct == []
    assert captured["origin"] == "operator"
    assert captured["operator_identity"] == "operator:test"
    assert captured["operator_surface"] == "http:item-action:ai-identify"
    assert captured_goal == [TGW_EBAY_PRICED]
    written = json.loads(path.read_text())
    assert written["ai_reidentify"] is True
    assert written["ai_redraft_requested"] is True
    assert written["ai_reprice_requested"] is True


def test_item_action_ai_identify_defaults_to_exact_governed_fanout(tmp_path, monkeypatch):
    root, _ = _item(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": root, "raw": {}})
    calls = []
    monkeypatch.setattr(
        http_server.state_machine,
        "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or "job-legacy",
    )

    response = http_server.item_action(
        "SKU-1",
        http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:test",
    )

    assert response["job_id"] == "job-legacy"
    assert len(calls) == 1
    call = calls[0]
    assert call["queue_name"] == call["handler_family"] == "ai_identify"
    assert call["entity_type"] == "item" and call["entity_id"] == "SKU-1"
    assert call["payload"]["sku"] == call["payload"]["entity_id"] == "SKU-1"
    assert call["payload"]["treatment_id"] == "ai-identify"
    assert call["payload"]["origin"] == "operator"
    assert call["payload"]["operator_identity"] == "operator:test"
    assert call["payload"]["operator_surface"] == "http:item-action:ai-identify"
    assert call["payload"]["graph_id"]
    assert call["payload"]["object_generation"]
    assert call["payload"]["condition_hash"]


@pytest.mark.parametrize(
    ("action", "goal", "surface", "marker"),
    (
        ("ebay_draft", TGW_EBAY_DRAFTED, "http:item-action:ebay-draft", "ai_redraft_requested"),
        ("ebay_price", TGW_EBAY_PRICED, "http:item-action:ebay-price", None),
    ),
)
def test_legacy_manual_local_pipeline_actions_are_rejected(
    tmp_path,
    monkeypatch,
    action,
    goal,
    surface,
    marker,
):
    from tgw.workflow import listing_migration

    root, path = _item(
        tmp_path,
        draft_listing={"title": "Draft", "price": 12.99},
        ebay_offer={"price": 12.99},
    )
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"bundle_downstream": "workflow"},
        },
    )
    captured = {}
    dispatched = SimpleNamespace(job_id=f"job-{action}")
    monkeypatch.setattr(
        listing_migration,
        "request_item_goal",
        lambda *args, **kwargs: captured.update({"path": args[0], "goal": args[1], **kwargs}) or SimpleNamespace(dispatched=dispatched),
    )

    before = path.read_text()
    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action=action),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 400
    assert captured == {}
    assert path.read_text() == before


def test_legacy_item_update_is_rejected_before_force_restage(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, path = _item(
        tmp_path,
        draft_listing={"title": "Draft", "price": 12.99},
        ebay_offer={"offer_id": "offer-1", "price": 12.99},
    )
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "item_ebay_stage_fanout": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    captured = {}
    graph = SimpleNamespace(graph_id="graph-1", object_generation="generation-1")
    dispatched = SimpleNamespace(
        job_id="job-stage",
        enqueued=True,
        treatment_id="ebay-stage",
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_dispatch_force_restage",
        lambda *args, **kwargs: captured.update({"path": args[0], **kwargs}) or (SimpleNamespace(graph=graph), dispatched, "authority-1", True),
    )

    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_update"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 400
    assert captured == {}


def test_item_action_workflow_dispatch_failure_preserves_pending_intent(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(tmp_path, product_lookup={"brand": "Acme"})
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"item_ai_identify_fanout": "workflow"},
        },
    )
    monkeypatch.setattr(
        listing_migration,
        "request_item_goal",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dispatch failed")),
    )
    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ai_identify"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 500
    assert json.loads(path.read_text())["ai_reidentify"] is True


def test_item_action_workflow_held_is_truthful_and_preserves_pending_intent(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, path = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"item_ai_identify_fanout": "workflow"},
        },
    )
    held = SimpleNamespace(
        dispatched=None,
        held_external=("external",),
        operator_gates=("gate",),
        graph=SimpleNamespace(ownership_conflicts=("owner",), reconciliation_gates=("reconcile",)),
    )
    monkeypatch.setattr(listing_migration, "request_item_goal", lambda *args, **kwargs: held)
    response = http_server.item_action(
        "SKU-1",
        http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:test",
    )
    assert response["ok"] is False
    assert response["status"] == "held"
    assert response["operator_gates"] == ["gate"]
    assert json.loads(path.read_text())["ai_reidentify"] is True


def test_item_action_invalid_selector_preserves_503_and_does_not_mutate(
    tmp_path,
    monkeypatch,
):
    root, path = _item(tmp_path)
    before = path.read_text()
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"item_ai_identify_fanout": "invalid"},
        },
    )
    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ai_identify"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 503
    assert path.read_text() == before


def test_item_action_workflow_queue_generation_matches_durable_intent(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow.item_snapshot import build_item_snapshot

    root, path = _item(tmp_path, product_lookup={"brand": "Acme"})
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"item_ai_identify_fanout": "workflow"},
        },
    )
    calls = []
    monkeypatch.setattr(
        http_server.state_machine,
        "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or "job-generation",
    )

    response = http_server.item_action(
        "SKU-1",
        http_server.ActionBody(action="ai_identify"),
        operator_identity="operator:authenticated",
    )

    snapshot = build_item_snapshot(path, TGW_EBAY_IDENTIFIED, treatments=(AI_IDENTIFY,))
    assert response["job_id"] == "job-generation"
    assert calls[0]["payload"]["object_generation"] == snapshot.generation
    assert calls[0]["payload"]["operator_identity"] == ("operator:authenticated")
    assert calls[0]["payload"]["operator_surface"] == ("http:item-action:ai-identify")


def test_legacy_item_stage_is_rejected_before_authorized_goal(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "item_ebay_stage_fanout": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    captured = {}
    graph = SimpleNamespace(
        graph_id="graph-1",
        object_generation="generation-1",
        unmet_requirements=("staged",),
        explicit_requirements=(),
        ownership_conflicts=(),
        reconciliation_gates=(),
    )
    held = SimpleNamespace(
        graph=graph,
        dispatched=None,
        held_external=("ebay-stage",),
        operator_gates=("provider_contract_required:ebay-stage",),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_request_item_goal",
        lambda path, goal, **kwargs: captured.update({"path": path, "goal": goal, **kwargs}) or (held, "authority-1", True),
    )
    direct = []
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda **kwargs: direct.append(kwargs))

    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_stage"),
            operator_identity="operator:authenticated",
        )
    assert caught.value.status_code == 400
    assert direct == []
    assert captured == {}


def test_legacy_item_stage_satisfied_graph_still_cannot_bypass_operator_object(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "item_ebay_stage_fanout": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    graph = SimpleNamespace(
        graph_id="graph-satisfied",
        object_generation="generation-1",
        unmet_requirements=(),
        explicit_requirements=(),
        ownership_conflicts=(),
        reconciliation_gates=(),
    )
    satisfied = SimpleNamespace(
        graph=graph,
        dispatched=None,
        held_external=(),
        operator_gates=(),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_request_item_goal",
        lambda *args, **kwargs: (satisfied, "authority-1", False),
    )

    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_stage"),
            operator_identity="operator:authenticated",
        )
    assert caught.value.status_code == 400


def test_legacy_item_stage_explicit_requirement_cannot_bypass_operator_object(
    tmp_path,
    monkeypatch,
):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {
                "item_ebay_stage_fanout": "workflow",
                "ebay_provider_identity": "ebay:account",
            },
        },
    )
    graph = SimpleNamespace(
        graph_id="graph-explicit",
        object_generation="generation-1",
        unmet_requirements=(),
        explicit_requirements=(("staged", "UNKNOWN"),),
        ownership_conflicts=(),
        reconciliation_gates=(),
    )
    held = SimpleNamespace(
        graph=graph,
        dispatched=None,
        held_external=(),
        operator_gates=(),
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_request_item_goal",
        lambda *args, **kwargs: (held, "authority-1", False),
    )

    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_stage"),
            operator_identity="operator:authenticated",
        )
    assert caught.value.status_code == 400


def test_legacy_item_stage_has_no_default_direct_fanout(
    tmp_path,
    monkeypatch,
):
    root, _ = _item(tmp_path, draft_listing={"title": "A"})
    monkeypatch.setattr(http_server, "_cfg", {"itemdata_root": root, "raw": {}})
    calls = []
    monkeypatch.setattr(
        http_server.state_machine,
        "enqueue_job",
        lambda **kwargs: calls.append(kwargs) or f"job-{len(calls)}",
    )

    with pytest.raises(HTTPException) as caught:
        http_server.item_action(
            "SKU-1",
            http_server.ActionBody(action="ebay_stage"),
            operator_identity="operator:authenticated",
        )
    assert caught.value.status_code == 400
    assert calls == []


def test_workflow_goal_endpoint_reports_enqueue_failure(tmp_path, monkeypatch):
    from tgw.workflow import listing_migration

    root, _ = _item(tmp_path)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": root,
            "workflow_migration": {"ebay_provider_identity": "ebay:account"},
        },
    )
    monkeypatch.setattr(
        listing_migration,
        "authorize_and_request_item_goal",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dispatch failed")),
    )
    with pytest.raises(HTTPException) as caught:
        http_server.request_workflow_goal(
            "SKU-1",
            http_server.WorkflowGoalBody(goal_profile_id="tgw.ebay_identified"),
            operator_identity="operator:test",
        )
    assert caught.value.status_code == 503
