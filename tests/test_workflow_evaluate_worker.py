import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tgw.errors import TreatmentFailure
from tgw.item_mutation import item_generation
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
from tgw.workers.normalize_condition import handle_job
from tgw.workers.workflow_evaluate import (
    _listing_continuation_requested,
    _validate_listing_continuation,
    evaluate_event,
)
from tgw.workflow.treatments import LEGACY_STAGE_ONBOARDING_TREATMENTS


def _event(item_root, **changes):
    payload = {
        "entity_id": "SKU-1",
        "origin_job_id": "origin-1",
        "origin_receipt": {"outcome": "satisfied", "graph_id": "old-graph"},
        "goal_profile_id": "tgw.ebay_listable",
        "goal_profile_version": "1",
        "prior_graph_id": "old-graph",
        "prior_object_generation": "old-generation",
    }
    payload.update(changes)
    return {"job_id": "event-1", "entity_type": "item", "entity_id": "SKU-1",
            "payload_json": payload}, {"itemdata_root": item_root}


def _item(tmp_path):
    root = tmp_path / "items"
    path = root / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"sku": "SKU-1", "condition": "Used", "image": "a.jpg"}),
                    encoding="utf-8")
    return root


def _isolated_event(item_root):
    origin = {
        "receipt_schema_id": "treatment-receipt/v1",
        "treatment_id": "ebay-onboard-legacy-stage",
        "treatment_version": "1",
        "outcome": "satisfied",
        "goal_profile_id": "tgw.ebay_legacy_stage_onboarded",
        "goal_profile_version": "1",
        "entity_id": "SKU-1",
        "graph_id": "old-graph",
        "object_generation": "old-generation",
        "condition_hash": "condition-old",
    }
    job, config = _event(
        item_root,
        goal_profile_id="tgw.ebay_legacy_stage_onboarded",
        origin_receipt=origin,
    )
    config["workflow_migration"] = {"ebay_provider_identity": "ebay:account-1"}
    durable = {
        "job_id": "origin-1",
        "state": "succeeded",
        "queue_name": "ebay_onboard_legacy_stage",
        "entity_type": "item",
        "entity_id": "SKU-1",
        "payload_json": {
            "payload_schema_id": "ebay-onboard-legacy-stage/v1",
            "treatment_id": "ebay-onboard-legacy-stage",
            "treatment_version": "1",
            "goal_profile_id": "tgw.ebay_legacy_stage_onboarded",
            "goal_profile_version": "1",
            "entity_id": "SKU-1", "sku": "SKU-1",
            "graph_id": "old-graph",
            "object_generation": "old-generation",
            "condition_hash": "condition-old",
            "result": origin,
        },
    }
    return job, config, origin, durable


def test_isolated_profile_rejects_forged_ordinary_origin(tmp_path):
    job, config, _, _ = _isolated_event(_item(tmp_path))
    job["payload_json"]["origin_receipt"] = {
        "outcome": "satisfied", "graph_id": "old-graph",
        "treatment_id": "normalize-condition", "treatment_version": "1",
    }
    lookup = MagicMock()
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, config, enqueue_fn=MagicMock(), origin_lookup=lookup)
    assert caught.value.result["evidence"]["reason_code"] == "ISOLATED_ORIGIN_MISMATCH"
    lookup.assert_not_called()


def test_ordinary_profile_rejects_isolated_origin(tmp_path):
    job, config, _, _ = _isolated_event(_item(tmp_path))
    job["payload_json"]["goal_profile_id"] = "tgw.ebay_listable"
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, config, enqueue_fn=MagicMock(), origin_lookup=MagicMock())
    assert caught.value.result["evidence"]["reason_code"] == "ISOLATED_ORIGIN_MISMATCH"


@pytest.mark.parametrize(
    "change",
    [
        {"state": "failed"},
        {"queue_name": "workflow_evaluate"},
        {"entity_id": "OTHER"},
        {"payload_json": {"payload_schema_id": "unknown", "result": {}}},
    ],
)
def test_isolated_origin_requires_exact_durable_queue_row(tmp_path, change):
    job, config, _, durable = _isolated_event(_item(tmp_path))
    durable.update(change)
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, config, enqueue_fn=MagicMock(),
                       origin_lookup=lambda _job_id: durable)
    assert caught.value.result["evidence"]["reason_code"] == "UNTRUSTED_ISOLATED_ORIGIN"


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("payload_schema_id", "other/v1"),
        ("treatment_id", "normalize-condition"),
        ("treatment_version", "2"),
        ("goal_profile_id", "tgw.ebay_listable"),
        ("goal_profile_version", "2"),
        ("entity_id", "OTHER"),
        ("sku", "OTHER"),
        ("graph_id", "other-graph"),
        ("object_generation", "other-generation"),
        ("condition_hash", "other-condition"),
    ],
)
def test_isolated_origin_rejects_each_forged_durable_payload_binding(
    tmp_path, key, bad_value,
):
    job, config, _, durable = _isolated_event(_item(tmp_path))
    durable["payload_json"][key] = bad_value
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, config, enqueue_fn=MagicMock(),
                       origin_lookup=lambda _job_id: durable)
    assert caught.value.result["evidence"]["reason_code"] == "UNTRUSTED_ISOLATED_ORIGIN"


def test_isolated_origin_rejects_wrong_durable_job_id(tmp_path):
    job, config, _, durable = _isolated_event(_item(tmp_path))
    durable["job_id"] = "other-origin"
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, config, enqueue_fn=MagicMock(),
                       origin_lookup=lambda _job_id: durable)
    assert caught.value.result["evidence"]["reason_code"] == "UNTRUSTED_ISOLATED_ORIGIN"


def test_isolated_origin_rejects_cross_sku_durable_job(tmp_path):
    job, config, _, durable = _isolated_event(_item(tmp_path))
    durable["entity_id"] = "OTHER"
    durable["payload_json"]["entity_id"] = "OTHER"
    durable["payload_json"]["sku"] = "OTHER"
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, config, enqueue_fn=MagicMock(),
                       origin_lookup=lambda _job_id: durable)
    assert caught.value.result["evidence"]["reason_code"] == "UNTRUSTED_ISOLATED_ORIGIN"


def test_exact_isolated_origin_uses_only_isolated_treatments(tmp_path):
    item_root = _item(tmp_path)
    job, config, _, durable = _isolated_event(item_root)
    snapshot = SimpleNamespace(
        generation=item_generation(json.loads(
            (item_root / "SKU-1" / "SKU-1.json").read_text()
        )),
    )
    graph = SimpleNamespace(
        graph_id="new-graph", eligible_treatments=(), reconciliation_gates=(),
        ownership_conflicts=(),
    )
    with patch("tgw.workflow.listing_migration._authoritative_stage_lookup",
               return_value=MagicMock()) as stage_lookup, \
         patch("tgw.workers.workflow_evaluate.build_item_snapshot",
               return_value=snapshot) as build, \
         patch("tgw.workers.workflow_evaluate.evaluate", return_value=graph) as evaluator:
        receipt = evaluate_event(job, config, enqueue_fn=MagicMock(),
                                 origin_lookup=lambda _job_id: durable)
    assert receipt["evidence"]["dispatch"] == "none"
    stage_lookup.assert_called_once()
    assert build.call_args.kwargs["treatments"] is LEGACY_STAGE_ONBOARDING_TREATMENTS
    assert evaluator.call_args.kwargs["treatments"] is LEGACY_STAGE_ONBOARDING_TREATMENTS


def test_isolated_evaluation_rejects_marker_removed_between_reads(tmp_path):
    item_root = _item(tmp_path)
    item_path = item_root / "SKU-1" / "SKU-1.json"
    item = json.loads(item_path.read_text())
    item["ebay_offer"] = {"provider_effect_id": "effect-1"}
    item_path.write_text(json.dumps(item))
    job, config, _, durable = _isolated_event(item_root)

    from tgw.workers import workflow_evaluate as module
    original_build = module.build_item_snapshot

    def remove_marker_then_build(*args, **kwargs):
        current = json.loads(item_path.read_text())
        current["ebay_offer"].pop("provider_effect_id")
        item_path.write_text(json.dumps(current))
        return original_build(*args, **kwargs)

    enqueue = MagicMock()
    with patch("tgw.workflow.listing_migration._authoritative_stage_lookup",
               return_value=MagicMock()), \
         patch("tgw.workers.workflow_evaluate.build_item_snapshot",
               side_effect=remove_marker_then_build):
        with pytest.raises(TreatmentFailure) as caught:
            evaluate_event(job, config, enqueue_fn=enqueue,
                           origin_lookup=lambda _job_id: durable)
    assert caught.value.result["evidence"]["reason_code"] == (
        "CANONICAL_CHANGED_DURING_EVALUATION"
    )
    enqueue.assert_not_called()


def test_rebuilds_new_generation_and_dispatches_evaluator_selected_local_treatment(tmp_path):
    job, cfg = _event(_item(tmp_path))
    enqueue = MagicMock(return_value="next-job")
    receipt = evaluate_event(job, cfg, enqueue_fn=enqueue)
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["object_generation"] != "old-generation"
    assert receipt["evidence"]["dispatch"] == "enqueued"
    assert enqueue.call_count == 1
    assert enqueue.call_args.kwargs["payload"]["graph_id"] == receipt["evidence"]["graph_id"]


@pytest.mark.parametrize(
    ("treatment_id", "queue_name"),
    [
        ("normalize-condition", "normalize_condition"),
        ("ebay-upload", "ebay_upload"),
        ("ebay-stage", "ebay_stage"),
    ],
)
def test_successful_governed_predecessor_durably_continues_listing(
    tmp_path, treatment_id, queue_name,
):
    item_root = _item(tmp_path)
    item_path = item_root / "SKU-1" / "SKU-1.json"
    item = json.loads(item_path.read_text())
    item.update({
        "product_lookup": {"title": "known"}, "ebay_category_id": "123",
        "draft_listing": {"title": "Ready", "category_id": "123", "price": 10},
        "ebay_photos": [{"url": "https://example.invalid/photo.jpg"}],
        "ebay_offer": {"offer_id": "offer-1", "provider_effect_id": "effect-1",
                       "stage_content_identity": "content-1"},
    })
    item_path.write_text(json.dumps(item), encoding="utf-8")
    resulting_generation = item_generation(item)
    origin = {
        "outcome": "satisfied", "graph_id": "old-graph",
        "treatment_id": treatment_id, "treatment_version": "1",
        "evidence": {"resulting_generation": resulting_generation},
    }
    job, config = _event(
        item_root, origin_receipt=origin,
        operator_authority_id="authority-1",
        operator_identity="operator:authenticated",
        operator_surface="http:item-action:ebay-publish",
        pre_authority_condition_hash="pre-condition",
    )
    config["workflow_migration"] = {"ebay_provider_identity": "ebay:account"}
    durable_payload = {
        "operator_authority_id": "authority-1",
        "operator_identity": "operator:authenticated",
        "operator_surface": "http:item-action:ebay-publish",
        "pre_authority_condition_hash": "pre-condition",
        "goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
        "graph_id": "old-graph", "object_generation": "old-generation",
        "treatment_id": treatment_id, "treatment_version": "1",
        "sku": "SKU-1",
        "result": origin,
    }
    durable = {
        "job_id": "origin-1", "state": "succeeded", "queue_name": queue_name,
        "entity_type": "item", "entity_id": "SKU-1", "payload_json": durable_payload,
    }
    now = datetime.now(UTC)
    authority = SimpleNamespace(
        authority_id="authority-1",
        operator_identity="operator:authenticated",
        surface="http:item-action:ebay-publish",
        entity_id="SKU-1", goal_profile_id="tgw.ebay_listable",
        goal_profile_version="1", object_generation="old-generation",
        pre_authority_condition_hash="pre-condition",
        content_identity="old-content", provider_identity="ebay:account",
        scopes=("upload", "stage", "publish"),
        issued_at=now - timedelta(seconds=5),
        expires_at=now + timedelta(minutes=5), superseded_at=None,
        superseded_by=None,
    )
    continued = SimpleNamespace(
        graph=SimpleNamespace(object_generation="new-generation", graph_id="new-graph"),
    )
    dispatched = SimpleNamespace(
        enqueued=True, outcome="dispatched", treatment_id="ebay-publish",
        job_id="publish-job",
    )
    enqueue = MagicMock()
    with patch("tgw.workers.workflow_evaluate.get_authority", return_value=authority), \
         patch("tgw.workflow.listing_migration.authorize_and_dispatch_next_listing_effect",
               return_value=(continued, dispatched, "authority-2", True)) as continuation:
        receipt = evaluate_event(
            job, config, enqueue_fn=enqueue, origin_lookup=lambda _job_id: durable,
        )

    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["continued_from"] == treatment_id
    assert receipt["evidence"]["next_treatment"] == "ebay-publish"
    assert receipt["evidence"]["next_job_id"] == "publish-job"
    assert receipt["evidence"]["successor_authority_id"] == "authority-2"
    assert continuation.call_args.kwargs["enqueue_fn"] is enqueue


def test_listing_continuation_reuses_exact_committed_successor_after_crash():
    now = datetime.now(UTC)
    original = SimpleNamespace(
        authority_id="authority-1", operator_identity="operator:authenticated",
        surface="http:item-action:ebay-publish", entity_id="SKU-1",
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
        object_generation="old-generation",
        pre_authority_condition_hash="old-condition", content_identity="old-content",
        provider_identity="ebay:account", scopes=("upload", "stage", "publish"),
        issued_at=now - timedelta(minutes=2), expires_at=now + timedelta(minutes=3),
        superseded_at=now - timedelta(seconds=1), superseded_by="authority-2",
    )
    item = {"sku": "SKU-1", "condition": "Used", "draft_listing": {"price": 10}}
    from tgw.workflow.operator_authority import listing_content_identity
    graph = SimpleNamespace(object_generation="new-generation", condition_hash="new-condition")
    successor = SimpleNamespace(
        authority_id="authority-2", operator_identity=original.operator_identity,
        surface=original.surface, entity_id="SKU-1",
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
        object_generation=graph.object_generation,
        pre_authority_condition_hash=graph.condition_hash,
        content_identity=listing_content_identity(item),
        provider_identity="ebay:account", scopes=("upload", "stage", "publish"),
        issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5),
        superseded_at=None, superseded_by=None,
    )
    origin = {
        "outcome": "satisfied", "graph_id": "old-graph",
        "treatment_id": "ebay-stage", "treatment_version": "1",
        "evidence": {"resulting_generation": "new-generation"},
    }
    payload = {
        "operator_authority_id": "authority-1",
        "operator_identity": original.operator_identity,
        "operator_surface": original.surface,
        "pre_authority_condition_hash": "old-condition",
        "prior_graph_id": "old-graph", "prior_object_generation": "old-generation",
    }
    durable_payload = {
        **payload, "graph_id": "old-graph", "object_generation": "old-generation",
        "goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
        "treatment_id": "ebay-stage", "treatment_version": "1", "sku": "SKU-1",
        "result": origin,
    }
    durable = {
        "job_id": "origin-1", "state": "succeeded", "queue_name": "ebay_stage",
        "entity_type": "item", "entity_id": "SKU-1", "payload_json": durable_payload,
    }
    with patch(
        "tgw.workers.workflow_evaluate.get_authority",
        side_effect=lambda authority_id: {
            "authority-1": original, "authority-2": successor,
        }.get(authority_id),
    ):
        selected = _validate_listing_continuation(
            payload=payload, origin=origin, origin_job_id="origin-1",
            entity_id="SKU-1", profile_id="tgw.ebay_listable", profile_version="1",
            graph=graph, item=item, provider_identity="ebay:account",
            origin_lookup=lambda _job_id: durable,
        )
    assert selected is successor


def test_listing_continuation_rejects_state_changed_after_predecessor():
    now = datetime.now(UTC)
    authority = SimpleNamespace(
        authority_id="authority-1", operator_identity="operator:authenticated",
        surface="http:item-action:ebay-publish", entity_id="SKU-1",
        goal_profile_id="tgw.ebay_listable", goal_profile_version="1",
        object_generation="old-generation",
        pre_authority_condition_hash="old-condition", content_identity="old-content",
        provider_identity="ebay:account", scopes=("upload", "stage", "publish"),
        issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=5),
        superseded_at=None, superseded_by=None,
    )
    origin = {
        "outcome": "satisfied", "graph_id": "old-graph",
        "treatment_id": "ebay-stage", "treatment_version": "1",
        "evidence": {"resulting_generation": "predecessor-output"},
    }
    payload = {
        "operator_authority_id": "authority-1",
        "operator_identity": authority.operator_identity,
        "operator_surface": authority.surface,
        "pre_authority_condition_hash": "old-condition",
        "prior_graph_id": "old-graph", "prior_object_generation": "old-generation",
    }
    durable_payload = {
        **payload, "graph_id": "old-graph", "object_generation": "old-generation",
        "goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
        "treatment_id": "ebay-stage", "treatment_version": "1", "sku": "SKU-1",
        "result": origin,
    }
    durable = {
        "job_id": "origin-1", "state": "succeeded", "queue_name": "ebay_stage",
        "entity_type": "item", "entity_id": "SKU-1", "payload_json": durable_payload,
    }
    with patch("tgw.workers.workflow_evaluate.get_authority", return_value=authority):
        with pytest.raises(TreatmentFailure) as caught:
            _validate_listing_continuation(
                payload=payload, origin=origin, origin_job_id="origin-1",
                entity_id="SKU-1", profile_id="tgw.ebay_listable",
                profile_version="1",
                graph=SimpleNamespace(
                    object_generation="unrelated-later-edit", condition_hash="new-condition",
                ),
                item={"sku": "SKU-1"}, provider_identity="ebay:account",
                origin_lookup=lambda _job_id: durable,
            )
    assert caught.value.result["evidence"]["reason_code"] == (
        "UNTRUSTED_OPERATOR_CONTINUATION"
    )


def test_listing_continuation_succeeds_when_no_further_dispatch_is_needed(tmp_path):
    item_root = _item(tmp_path)
    item_path = item_root / "SKU-1" / "SKU-1.json"
    item = json.loads(item_path.read_text())
    resulting_generation = item_generation(item)
    origin = {
        "outcome": "satisfied", "graph_id": "old-graph",
        "treatment_id": "ebay-stage", "treatment_version": "1",
        "evidence": {"resulting_generation": resulting_generation},
    }
    job, config = _event(
        item_root, origin_receipt=origin,
        operator_authority_id="authority-1",
        operator_identity="operator:authenticated",
        operator_surface="http:item-action:ebay-publish",
        pre_authority_condition_hash="pre-condition",
    )
    config["workflow_migration"] = {"ebay_provider_identity": "ebay:account"}
    authority = SimpleNamespace(
        operator_identity="operator:authenticated",
        surface="http:item-action:ebay-publish", provider_identity="ebay:account",
    )
    continued = SimpleNamespace(
        graph=SimpleNamespace(object_generation=resulting_generation, graph_id="new-graph"),
    )
    with patch(
        "tgw.workers.workflow_evaluate._validate_listing_continuation",
        return_value=authority,
    ), patch(
        "tgw.workflow.listing_migration.authorize_and_dispatch_next_listing_effect",
        return_value=(continued, None, "authority-2", False),
    ):
        receipt = evaluate_event(job, config, enqueue_fn=MagicMock())

    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["dispatch"] == "none"
    assert receipt["evidence"]["next_treatment"] is None
    assert receipt["evidence"]["successor_authority_id"] == "authority-2"


def test_update_item_stage_is_not_a_publish_continuation():
    assert not _listing_continuation_requested(
        {"operator_surface": "http:item-action:ebay-update"}, "ebay-stage"
    )
    assert not _listing_continuation_requested(
        {"operator_surface": "http:item-action:ebay-stage"}, "ebay-stage"
    )
    assert _listing_continuation_requested(
        {"operator_surface": "http:item-action:ebay-publish"}, "ebay-stage"
    )


def test_governed_stage_continuation_rejects_non_durable_origin(tmp_path):
    item_root = _item(tmp_path)
    origin = {
        "outcome": "satisfied", "graph_id": "old-graph",
        "treatment_id": "ebay-stage", "treatment_version": "1",
    }
    job, config = _event(
        item_root, origin_receipt=origin,
        operator_authority_id="authority-1",
        operator_identity="operator:authenticated",
        operator_surface="http:item-action:ebay-publish",
        pre_authority_condition_hash="pre-condition",
    )
    config["workflow_migration"] = {"ebay_provider_identity": "ebay:account"}
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(
            job, config, enqueue_fn=MagicMock(),
            origin_lookup=lambda _job_id: {"state": "failed"},
        )
    assert caught.value.result["evidence"]["reason_code"] == (
        "UNTRUSTED_OPERATOR_CONTINUATION"
    )


def test_unauthorized_external_candidates_are_not_reported_as_eligible(tmp_path):
    root = tmp_path / "items"
    path = root / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sku": "SKU-1", "condition": "Used", "image": "a.jpg",
        "product_lookup": {"title": "known"}, "ebay_category_id": "123",
        "draft_listing": {"title": "Ready", "category_id": "123", "price": 10},
        "ebay_offer": {"offer_id": "offer-1"},
    }), encoding="utf-8")
    job, cfg = _event(root)
    enqueue = MagicMock()
    receipt = evaluate_event(job, cfg, enqueue_fn=enqueue)
    assert receipt["evidence"]["dispatch"] == "none"
    assert "external_candidates" not in receipt["evidence"]
    enqueue.assert_not_called()


def test_lexically_earlier_external_does_not_block_later_local_treatment(tmp_path):
    root = tmp_path / "items"
    path = root / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sku": "SKU-1", "condition": "pre-owned", "image": "a.jpg",
        "product_lookup": {"title": "known"}, "ebay_category_id": "123",
        "draft_listing": {"title": "Ready", "category_id": "123", "price": 10},
        "ebay_offer": {"offer_id": "offer-1"},
    }), encoding="utf-8")
    job, cfg = _event(root)
    enqueue = MagicMock(return_value="normalize-job")
    receipt = evaluate_event(job, cfg, enqueue_fn=enqueue)
    assert receipt["evidence"]["eligible"] == ["normalize-condition"]
    assert receipt["evidence"]["next_treatment"] == "normalize-condition"
    assert receipt["evidence"]["dispatch"] == "enqueued"
    assert enqueue.call_args.kwargs["queue_name"] == "normalize_condition"


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"goal_profile_id": "coding.deployed"}, "PROFILE_MISMATCH"),
        ({"goal_profile_id": "not.registered"}, "UNKNOWN_PROFILE"),
        ({"origin_receipt": {"outcome": "failed"}}, "INVALID_ORIGIN_RECEIPT"),
        ({"prior_graph_id": "different"}, "ORIGIN_GRAPH_MISMATCH"),
    ],
)
def test_invalid_event_is_structured_hard_failure(tmp_path, changes, reason):
    job, cfg = _event(_item(tmp_path), **changes)
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, cfg, enqueue_fn=MagicMock())
    assert caught.value.result["outcome"] == "failed"
    assert caught.value.result["evidence"]["reason_code"] == reason


def test_worker_completion_uses_atomic_outbox_only_for_bound_success_receipt():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "normalize_condition"
    worker.owner = "owner"
    worker.config = {}
    receipt = {"treatment_id": "normalize-condition", "treatment_version": "1",
               "graph_id": "graph-1", "outcome": "satisfied",
               "goal_profile_id": "tgw.ebay_listable",
               "goal_profile_version": "1", "object_generation": "gen-1",
               "condition_hash": "condition-1", "entity_id": "SKU-1",
               "receipt_schema_id": "treatment-receipt/v1"}
    job = {"job_id": "job-1", "lease_token": LEASE_TOKEN,
           "entity_type": "item", "entity_id": "SKU-1",
           "payload_json": {"treatment_id": "normalize-condition", "treatment_version": "1",
                            "graph_id": "graph-1", "object_generation": "gen-1",
                            "condition_hash": "condition-1",
                            "goal_profile_id": "tgw.ebay_listable",
                            "goal_profile_version": "1", "entity_id": "SKU-1",
                            "object_id": "SKU-1"}}
    worker.handle = MagicMock(return_value=receipt)
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as atomic, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary:
        worker._process(job)
    atomic.assert_called_once_with("job-1", "owner", LEASE_TOKEN, receipt)
    ordinary.assert_not_called()


def test_unrecognized_dict_keeps_backward_compatible_completion():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "echo"
    worker.owner = "owner"
    worker.config = {}
    worker.handle = MagicMock(return_value={"ok": True})
    job = {"job_id": "job-1", "lease_token": LEASE_TOKEN,
           "entity_type": "system", "payload_json": {}}
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as atomic, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary:
        worker._process(job)
    atomic.assert_not_called()
    ordinary.assert_called_once_with(
        "job-1", "owner", LEASE_TOKEN, result={"ok": True},
    )


def test_waiting_receipt_uses_atomic_timer_boundary_not_success_outbox():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "ebay_upload"
    worker.owner = "owner"
    worker.config = {}
    payload = {
        "sku": "SKU-1", "treatment_id": "ebay-upload",
        "treatment_version": "1", "graph_id": "graph-1",
        "object_generation": "gen-1", "condition_hash": "condition-1",
    }
    receipt = {
        "receipt_schema_id": "treatment-wait-receipt/v1",
        "treatment_id": "ebay-upload", "treatment_version": "1",
        "graph_id": "graph-1", "outcome": "transient_backoff",
        "timer": {
            "queue_name": "ebay_upload", "not_before": 1234.0,
            "payload": {**payload, "quota_retries": 1},
            "dedupe_key": "workflow-timer:graph-1:ebay-upload:quota:1",
        },
    }
    job = {"job_id": "job-1", "lease_token": LEASE_TOKEN,
           "queue_name": "ebay_upload",
           "entity_type": "item", "entity_id": "SKU-1", "payload_json": payload}
    worker.handle = MagicMock(return_value=receipt)
    with patch("tgw.queue.worker_base.time.time", return_value=1000.0), \
         patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_schedule_timer",
               return_value="timer-1") as timer, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary, \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as outbox:
        worker._process(job)
    timer.assert_called_once_with("job-1", "owner", LEASE_TOKEN, receipt)
    ordinary.assert_not_called()
    outbox.assert_not_called()


@pytest.mark.parametrize(
    ("not_before", "reason"),
    [
        (True, "INVALID_TIMER_NOT_BEFORE"),
        (float("nan"), "INVALID_TIMER_NOT_BEFORE"),
        (float("inf"), "INVALID_TIMER_NOT_BEFORE"),
        (1000.0, "TIMER_NOT_IN_FUTURE"),
        (1000.5, "TIMER_NOT_IN_FUTURE"),
        (1000.0 + 7 * 24 * 3600 + 1, "TIMER_WINDOW_EXCEEDED"),
    ],
)
def test_waiting_receipt_rejects_unsafe_timer_window(not_before, reason):
    from tgw.queue.worker_base import _waiting_treatment_receipt_error

    payload = {
        "sku": "SKU-1", "treatment_id": "ebay-upload",
        "treatment_version": "1", "graph_id": "graph-1",
        "object_generation": "gen-1", "condition_hash": "condition-1",
    }
    receipt = {
        "receipt_schema_id": "treatment-wait-receipt/v1",
        "treatment_id": "ebay-upload", "treatment_version": "1",
        "graph_id": "graph-1", "outcome": "transient_backoff",
        "timer": {
            "queue_name": "ebay_upload", "not_before": not_before,
            "payload": payload, "dedupe_key": "timer-1",
        },
    }
    job = {"queue_name": "ebay_upload", "entity_id": "SKU-1",
           "payload_json": payload}
    with patch("tgw.queue.worker_base.time.time", return_value=1000.0):
        assert _waiting_treatment_receipt_error(receipt, job) == reason


@pytest.mark.parametrize("max_attempts", [True, 0, 11, 1.5, "3"])
def test_waiting_receipt_rejects_invalid_timer_attempt_budget(max_attempts):
    from tgw.queue.worker_base import _waiting_treatment_receipt_error

    payload = {
        "sku": "SKU-1", "treatment_id": "ebay-upload",
        "treatment_version": "1", "graph_id": "graph-1",
        "object_generation": "gen-1", "condition_hash": "condition-1",
    }
    receipt = {
        "receipt_schema_id": "treatment-wait-receipt/v1",
        "treatment_id": "ebay-upload", "treatment_version": "1",
        "graph_id": "graph-1", "outcome": "transient_backoff",
        "timer": {
            "queue_name": "ebay_upload", "not_before": 2000.0,
            "payload": payload, "dedupe_key": "timer-1",
            "max_attempts": max_attempts,
        },
    }
    job = {"queue_name": "ebay_upload", "entity_id": "SKU-1",
           "payload_json": payload}
    with patch("tgw.queue.worker_base.time.time", return_value=1000.0):
        assert _waiting_treatment_receipt_error(receipt, job) == (
            "INVALID_TIMER_MAX_ATTEMPTS"
        )


@pytest.mark.parametrize(
    ("key", "changed", "reason"),
    [
        ("graph_id", "graph-other", "TIMER_GRAPH_ID_MISMATCH"),
        ("object_generation", "gen-other", "TIMER_OBJECT_GENERATION_MISMATCH"),
        ("condition_hash", "condition-other", "TIMER_CONDITION_HASH_MISMATCH"),
        ("provider_effect_id", "effect-other", "TIMER_PROVIDER_EFFECT_ID_MISMATCH"),
        ("provider_identity", "ebay:other", "TIMER_PROVIDER_IDENTITY_MISMATCH"),
        ("expected_offer_id", "offer-other", "TIMER_EXPECTED_OFFER_ID_MISMATCH"),
        ("source_operation", "publish-offer", "TIMER_SOURCE_OPERATION_MISMATCH"),
    ],
)
def test_waiting_receipt_rejects_rebound_treatment_or_source_identity(
    key, changed, reason,
):
    from tgw.queue.worker_base import _waiting_treatment_receipt_error

    payload = {
        "sku": "SKU-1", "entity_id": "SKU-1", "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1", "graph_id": "graph-1",
        "goal_profile_id": "tgw.ebay_reconciled", "goal_profile_version": "1",
        "object_generation": "gen-1", "condition_hash": "condition-1",
        "provider_effect_id": "effect-1", "provider_identity": "ebay:account",
        "expected_offer_id": "offer-1", "source_operation": "stage-draft",
    }
    timer_payload = {**payload, key: changed, "sync_retry": 1}
    receipt = {
        "receipt_schema_id": "treatment-wait-receipt/v1",
        "treatment_id": payload["treatment_id"],
        "treatment_version": payload["treatment_version"],
        "graph_id": payload["graph_id"], "outcome": "transient_backoff",
        "timer": {"queue_name": "ebay_sync", "not_before": 1100.0,
                  "payload": timer_payload, "dedupe_key": "timer-1"},
    }
    job = {"queue_name": "ebay_sync", "entity_type": "item",
           "entity_id": "SKU-1", "payload_json": payload}
    with patch("tgw.queue.worker_base.time.time", return_value=1000.0):
        assert _waiting_treatment_receipt_error(receipt, job) == reason


def test_waiting_receipt_rejects_reserved_checkpoint_injection():
    from tgw.queue.worker_base import _waiting_treatment_receipt_error

    payload = {
        "sku": "SKU-1", "entity_id": "SKU-1", "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1", "graph_id": "graph-1",
        "object_generation": "gen-1", "condition_hash": "condition-1",
    }
    receipt = {
        "receipt_schema_id": "treatment-wait-receipt/v1",
        "treatment_id": "ebay-sync-targeted", "treatment_version": "1",
        "graph_id": "graph-1", "outcome": "transient_backoff",
        "timer": {"queue_name": "ebay_sync", "not_before": 1100.0,
                  "payload": {**payload, "observation_checkpoint": {"forged": True}},
                  "dedupe_key": "timer-1"},
    }
    job = {"queue_name": "ebay_sync", "entity_type": "item",
           "entity_id": "SKU-1", "payload_json": payload}
    with patch("tgw.queue.worker_base.time.time", return_value=1000.0):
        assert _waiting_treatment_receipt_error(receipt, job) == (
            "TIMER_RESERVED_CHECKPOINT"
        )


def test_atomic_wait_completion_and_future_job_are_one_transaction():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [("item", "SKU-1"), ("timer-job",)]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    receipt = {
        "outcome": "transient_backoff",
        "timer": {
            "queue_name": "ebay_upload", "not_before": 1234.0,
            "payload": {"sku": "SKU-1", "graph_id": "graph-1"},
            "dedupe_key": "workflow-timer:graph-1:ebay-upload:quota:1",
            "max_attempts": 3,
        },
    }
    with patch.object(state_machine, "_conn", return_value=connection), \
         patch.object(state_machine.time, "time", return_value=1000.0):
        timer_id = state_machine.complete_treatment_and_schedule_timer(
            "origin-job", "owner", LEASE_TOKEN, receipt,
        )
    assert timer_id == "timer-job"
    assert cursor.execute.call_count == 2
    insert_args = cursor.execute.call_args_list[1].args[1]
    assert insert_args[0] == "ebay_upload"
    assert insert_args[2] == "SKU-1"
    assert json.loads(insert_args[5])["graph_id"] == "graph-1"


def test_wait_timer_fails_closed_when_running_lease_is_lost():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    receipt = {"timer": {"not_before": 1234.0}}
    with patch.object(state_machine, "_conn", return_value=connection), \
         patch.object(state_machine.time, "time", return_value=1000.0):
        with pytest.raises(RuntimeError, match="lost running lease"):
            state_machine.complete_treatment_and_schedule_timer(
                    "origin-job", "wrong-owner", LEASE_TOKEN, receipt,
            )
    assert cursor.execute.call_count == 1


def test_wait_timer_insert_conflict_rolls_back_receipt_completion():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = ("item", "SKU-1")
    cursor.execute.side_effect = [None, RuntimeError("timer identity conflict")]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    receipt = {
        "timer": {
            "queue_name": "ebay_upload", "not_before": 1234.0,
            "payload": {"sku": "SKU-1"}, "dedupe_key": "timer-key",
        },
    }
    with patch.object(state_machine, "_conn", return_value=connection), \
         patch.object(state_machine.time, "time", return_value=1000.0):
        with pytest.raises(RuntimeError, match="timer identity conflict"):
            state_machine.complete_treatment_and_schedule_timer(
                "origin-job", "owner", LEASE_TOKEN, receipt,
            )
    assert connection.__exit__.call_args.args[0] is RuntimeError


def test_claim_sql_sources_contractually_gate_future_not_before():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(state_machine, "_conn", return_value=connection):
        assert state_machine.claim_queue_jobs(
            "ebay_upload", "fresh-process", limit=1,
        ) == []
    assert cursor.execute.call_args.args[0] == (
        "SELECT * FROM claim_queue_jobs(%s, %s, %s, %s)"
    )
    queue_dir = Path(__file__).parents[1] / "src/tgw/queue"
    for schema_name in ("schema.sql", "live_schema.sql"):
        schema = (queue_dir / schema_name).read_text()
        assert "not_before IS NULL OR q.not_before <= NOW()" in schema


def test_atomic_completion_writes_receipt_then_durable_event():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [
        ("SKU-1", {"goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
                   "graph_id": "graph-1", "object_generation": "gen-1",
                   "operator_authority_id": "authority-1",
                   "operator_identity": "operator:authenticated",
                   "operator_surface": "http:item-action:ebay-publish",
                   "pre_authority_condition_hash": "condition-0"}),
        ("event-job",),
    ]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    receipt = {"outcome": "satisfied", "graph_id": "graph-1"}
    with patch.object(state_machine, "_conn", return_value=connection):
        event_id = state_machine.complete_treatment_and_enqueue_evaluation(
            "origin-job", "owner", LEASE_TOKEN, receipt,
        )
    assert event_id == "event-job"
    assert cursor.execute.call_count == 2
    inserted = json.loads(cursor.execute.call_args_list[1].args[1][2])
    assert inserted["origin_receipt"] == receipt
    assert inserted["prior_object_generation"] == "gen-1"
    assert inserted["operator_authority_id"] == "authority-1"
    assert inserted["operator_identity"] == "operator:authenticated"
    assert inserted["operator_surface"] == "http:item-action:ebay-publish"
    assert inserted["pre_authority_condition_hash"] == "condition-0"


def test_satisfied_no_change_receipt_is_persisted_without_evaluation_event():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (
        "SKU-1", {"goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
                  "graph_id": "graph-1", "object_generation": "gen-1"},
    )
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    receipt = {"outcome": "satisfied", "graph_id": "graph-1",
               "evidence": {"changed": False}}
    with patch.object(state_machine, "_conn", return_value=connection):
        result = state_machine.complete_treatment_and_enqueue_evaluation(
            "origin-job", "owner", LEASE_TOKEN, receipt,
        )
    assert result == state_machine.EVALUATION_EVENT_NOT_REQUIRED
    assert cursor.execute.call_count == 1
    persisted = json.loads(cursor.execute.call_args.args[1][0])
    assert persisted["evidence"]["changed"] is False


def test_normalize_noop_receipt_suppresses_false_evidence_event():
    receipt = handle_job(
        {"job_id": "origin-job", "entity_id": "SKU-1", "payload_json": {
            "sku": "SKU-1", "entity_id": "SKU-1", "object_generation": "gen-1",
            "graph_id": "graph-1", "treatment_id": "normalize-condition",
            "treatment_version": "1",
        }},
        {},
        mutation_fn=lambda **kwargs: {
            "status": "COMMITTED", "operation_id": "operation-1",
            "resulting_generation": "gen-1", "changed": False,
        },
    )
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (
        "SKU-1", {"goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
                  "graph_id": "graph-1", "object_generation": "gen-1"},
    )
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(state_machine, "_conn", return_value=connection):
        result = state_machine.complete_treatment_and_enqueue_evaluation(
            "origin-job", "owner", LEASE_TOKEN, receipt,
        )
    assert result == state_machine.EVALUATION_EVENT_NOT_REQUIRED
    assert cursor.execute.call_count == 1
    assert receipt["evidence"]["changed"] is False


def test_atomic_completion_raises_when_lease_guard_loses():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(state_machine, "_conn", return_value=connection):
        with pytest.raises(RuntimeError, match="lost running lease"):
            state_machine.complete_treatment_and_enqueue_evaluation(
                "origin-job", "wrong-owner", LEASE_TOKEN,
                {"outcome": "satisfied"},
            )
    assert cursor.execute.call_count == 1


@pytest.mark.parametrize(
    ("receipt_change", "payload_change", "job_change", "reason"),
    [
        ({"graph_id": ""}, {}, {}, "INVALID_RECEIPT_IDENTITY"),
        ({"graph_id": "wrong"}, {}, {}, "GRAPH_ID_MISMATCH"),
        ({"goal_profile_id": "other"}, {}, {}, "GOAL_PROFILE_ID_MISMATCH"),
        ({"goal_profile_version": "2"}, {}, {}, "GOAL_PROFILE_VERSION_MISMATCH"),
        ({"object_generation": "gen-2"}, {}, {}, "OBJECT_GENERATION_MISMATCH"),
        ({"condition_hash": "condition-2"}, {}, {}, "CONDITION_HASH_MISMATCH"),
        ({"entity_id": "OTHER"}, {}, {}, "RECEIPT_ENTITY_ID_MISMATCH"),
        ({}, {"object_generation": ""}, {}, "INVALID_OBJECT_GENERATION"),
        ({}, {"goal_profile_version": ""}, {}, "INVALID_GOAL_PROFILE_VERSION"),
        ({}, {"entity_id": "OTHER"}, {}, "ENTITY_ID_MISMATCH"),
        ({}, {"object_id": "OTHER"}, {}, "OBJECT_ID_MISMATCH"),
        ({}, {}, {"entity_type": "system"}, "INVALID_ENTITY_TYPE"),
    ],
)
def test_candidate_treatment_receipt_with_bad_binding_is_terminal_not_success(
    receipt_change, payload_change, job_change, reason,
):
    worker = object.__new__(QueueWorker)
    worker.queue_name = "normalize_condition"
    worker.owner = "owner"
    worker.config = {}
    receipt = {"treatment_id": "normalize-condition", "treatment_version": "1",
               "graph_id": "graph-1", "outcome": "satisfied",
               "goal_profile_id": "tgw.ebay_listable",
               "goal_profile_version": "1", "object_generation": "gen-1",
               "condition_hash": "condition-1", "entity_id": "SKU-1",
               "receipt_schema_id": "treatment-receipt/v1"}
    receipt.update(receipt_change)
    payload = {"treatment_id": "normalize-condition", "treatment_version": "1",
               "graph_id": "graph-1", "object_generation": "gen-1",
               "condition_hash": "condition-1",
               "goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
               "entity_id": "SKU-1", "object_id": "SKU-1"}
    payload.update(payload_change)
    job = {"job_id": "job-1", "lease_token": LEASE_TOKEN,
           "entity_type": "item", "entity_id": "SKU-1",
           "payload_json": payload}
    job.update(job_change)
    worker.handle = MagicMock(return_value=receipt)
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.mark_dead_letter") as dead, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary, \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as atomic:
        worker._process(job)
    ordinary.assert_not_called()
    atomic.assert_not_called()
    result = dead.call_args.kwargs["result"]
    assert result["outcome"] == "failed"
    assert result["evidence"]["reason_code"] == reason


def test_atomic_insert_exception_propagates_to_transaction_context():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (
        "SKU-1", {"goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
                  "graph_id": "graph-1", "object_generation": "gen-1"},
    )
    cursor.execute.side_effect = [None, RuntimeError("insert failed")]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(state_machine, "_conn", return_value=connection):
        with pytest.raises(RuntimeError, match="insert failed"):
            state_machine.complete_treatment_and_enqueue_evaluation(
                "origin-job", "owner", LEASE_TOKEN, {"outcome": "satisfied"},
            )
    # The exception leaves the `_conn` transaction context; psycopg rolls it back.
    assert connection.__exit__.call_args.args[0] is RuntimeError


def test_treatment_failure_persists_structured_result_as_dead_letter():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "normalize_condition"
    worker.owner = "owner"
    worker.config = {}
    failed = {"outcome": "failed", "evidence": {"reason_code": "CONFLICT"}}
    worker.handle = MagicMock(side_effect=TreatmentFailure("conflict", failed))
    job = {"job_id": "job-1", "lease_token": LEASE_TOKEN,
           "entity_type": "item", "entity_id": "SKU-1",
           "payload_json": {}}
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.mark_dead_letter") as dead, \
         patch("tgw.notify.notify"):
        worker._process(job)
    dead.assert_called_once_with(
        "job-1", "owner", LEASE_TOKEN,
        "TreatmentFailure('conflict')", result=failed,
    )
LEASE_TOKEN = "11111111-1111-4111-8111-111111111111"
