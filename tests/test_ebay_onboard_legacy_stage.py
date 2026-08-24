import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tgw.errors import HardFailure, TreatmentFailure
from tgw.item_mutation import item_generation
from tgw.legacy_stage_corroboration import LegacyStageRead
from tgw.workers.ebay_onboard_legacy_stage import EbayOnboardLegacyStageWorker
from tgw.workflow_kernel.contracts import FingerprintResult, GoalProfile
from tgw.workflow_kernel.evaluator import evaluate
from tgw.workflow.item_snapshot import build_item_snapshot
from tgw.workflow.listing_migration import _authoritative_stage_lookup
from tgw.workflow.operator_authority import listing_content_identity
from tgw.workflow.profiles import TGW_EBAY_LEGACY_STAGE_ONBOARDED
from tgw.workflow.treatments import LEGACY_STAGE_ONBOARDING_TREATMENTS


def _bodies():
    inventory = {
        "condition": "USED_EXCELLENT",
        "product": {"title": "Title", "description": "Description",
                    "aspects": {"Brand": ["Example"]},
                    "imageUrls": ["https://img/1"]},
        "availability": {"shipToLocationAvailability": {
            "quantity": 1, "availabilityDistributions": [
                {"merchantLocationKey": "warehouse", "quantity": 1},
            ],
        }},
    }
    offer = {
        "sku": "SKU-1", "offerId": "OFF-1", "status": "UNPUBLISHED",
        "marketplaceId": "EBAY_US", "format": "FIXED_PRICE",
        "availableQuantity": 1, "categoryId": "123",
        "listingDescription": "Description",
        "listingPolicies": {"fulfillmentPolicyId": "f", "paymentPolicyId": "p",
                            "returnPolicyId": "r"},
        "merchantLocationKey": "warehouse",
        "pricingSummary": {"price": {"currency": "USD", "value": "10.00"}},
        "shipToLocations": {"regionIncluded": [
            {"regionType": "COUNTRY", "regionName": "US"},
        ]},
    }
    return inventory, offer


def _worker(tmp_path):
    path = tmp_path / "items" / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sku": "SKU-1", "ebay_offer": {"offer_id": "OFF-1", "keep": "value"},
    }))
    worker = object.__new__(EbayOnboardLegacyStageWorker)
    worker.owner = "owner-1"
    worker.config = {
        "itemdata_root": tmp_path / "items", "data_root": tmp_path,
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "journal",
        "workflow_migration": {"ebay_provider_identity": "ebay:account-1"},
    }
    return worker, path


def _job(path):
    inventory, offer = _bodies()
    generation = item_generation(json.loads(path.read_text()))
    return {
        "job_id": "11111111-1111-4111-8111-111111111111",
        "lease_token": "22222222-2222-4222-8222-222222222222",
        "entity_type": "item", "entity_id": "SKU-1",
        "payload_json": {
            "payload_schema_id": "ebay-onboard-legacy-stage/v1",
            "sku": "SKU-1", "entity_id": "SKU-1", "offer_id": "OFF-1",
            "provider_identity": "ebay:account-1",
            "object_generation": generation, "graph_id": "graph-1",
            "condition_hash": "condition-1", "content_identity": "c" * 64,
            "goal_profile_id": "tgw.ebay_staged",
            "goal_profile_version": "1",
            "treatment_id": "ebay-onboard-legacy-stage",
            "treatment_version": "1", "expected_inventory_item": inventory,
            "expected_offer": offer,
        },
    }


@pytest.fixture(autouse=True)
def _repository_and_projection(monkeypatch):
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.record_provider_observation",
        lambda observation: observation,
    )
    monkeypatch.setattr(
        "tgw.legacy_stage_corroboration.record_provider_observation",
        lambda observation, **kwargs: observation,
    )
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})


def test_public_handle_defaults_off_before_provider_read(tmp_path):
    worker, path = _worker(tmp_path)
    with patch(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
    ) as read, pytest.raises(HardFailure, match="disabled"):
        worker.handle(_job(path))
    read.assert_not_called()


def test_public_handle_routes_only_exact_schema_and_workflow_mode(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    worker.config["workflow_migration"][
        "ebay_legacy_stage_onboarding_consumer"
    ] = "workflow"
    expected = {"outcome": "satisfied"}
    governed = MagicMock(return_value=expected)
    monkeypatch.setattr(worker, "_handle_governed", governed)
    job = _job(path)
    assert worker.handle(job) == expected
    governed.assert_called_once_with(job)


@pytest.mark.parametrize("schema", [None, "", "ebay-onboard-legacy-stage/v2"])
def test_public_handle_rejects_partial_or_mixed_schema(tmp_path, schema):
    worker, path = _worker(tmp_path)
    worker.config["workflow_migration"][
        "ebay_legacy_stage_onboarding_consumer"
    ] = "workflow"
    job = _job(path)
    if schema is None:
        job["payload_json"].pop("payload_schema_id")
    else:
        job["payload_json"]["payload_schema_id"] = schema
    with pytest.raises(HardFailure, match="schema"):
        worker.handle(job)


def test_public_handle_rejects_invalid_selector_before_get(tmp_path):
    worker, path = _worker(tmp_path)
    worker.config["workflow_migration"][
        "ebay_legacy_stage_onboarding_consumer"
    ] = "invalid"
    with patch(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
    ) as read, pytest.raises(HardFailure, match="selector"):
        worker.handle(_job(path))
    read.assert_not_called()


@pytest.mark.parametrize("key,value", [
    ("provider_effect_id", False),
    ("provider_effect_id", ""),
    ("legacy_stage_observation_id", "A" * 64),
    ("stage_content_identity", "short"),
])
def test_malformed_present_markers_fail_closed(tmp_path, key, value):
    worker, path = _worker(tmp_path)
    document = json.loads(path.read_text())
    document["ebay_offer"][key] = value
    path.write_text(json.dumps(document))
    payload = _job(path)["payload_json"]
    from tgw.item_mutation import operation_identity
    operation_payload = {
        "content_identity": payload["content_identity"],
        "comparison_fingerprint": "d" * 64,
        "observation_id": "a" * 64,
    }
    operation_id = operation_identity(
        sku=payload["sku"], kind="ebay-onboard-legacy-stage",
        expected_generation=payload["object_generation"], payload=operation_payload,
    )
    receipt = worker._apply_marker(
        payload, path, "a" * 64, operation_id, "d" * 64,
    )
    assert receipt.status == "FAILED"
    assert json.loads(path.read_text()) == document


def test_corroborated_observation_commits_distinct_markers(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    job = _job(path)
    inventory, offer = _bodies()
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: LegacyStageRead(
            "complete", "PROVIDER_READ_COMPLETE", offer, inventory,
            provider_identity="ebay:account-1",
        ),
    )
    checkpoints = []
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.state_machine.checkpoint_running_job",
        lambda *args: checkpoints.append(args[-1]) or args[-1],
    )
    receipt = worker._handle_governed(job)
    document = json.loads(path.read_text())
    assert receipt["outcome"] == "satisfied"
    assert receipt["established_conditions"] == ["staged_content_current"]
    assert document["ebay_offer"]["legacy_stage_observation_id"] == receipt["evidence"][
        "observation_id"
    ]
    assert document["ebay_offer"]["stage_content_identity"] == "c" * 64
    assert document["ebay_offer"]["offer_id"] == "OFF-1"
    assert document["ebay_offer"]["keep"] == "value"
    assert "provider_effect_id" not in json.dumps(document)
    assert checkpoints[0]["offer"] == offer
    assert checkpoints[0]["inventory_item"] == inventory


def test_generation_advance_after_get_conflicts_without_marker(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    job = _job(path)
    inventory, offer = _bodies()
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: LegacyStageRead(
            "complete", "PROVIDER_READ_COMPLETE", offer, inventory,
            provider_identity="ebay:account-1",
        ),
    )

    def checkpoint(*args):
        document = json.loads(path.read_text())
        document["operator_note"] = "newer"
        path.write_text(json.dumps(document))
        return args[-1]

    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.state_machine.checkpoint_running_job",
        checkpoint,
    )
    with pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed(job)
    assert caught.value.result["outcome"] == "conflict"
    assert "legacy_stage_observation_id" not in json.loads(path.read_text())["ebay_offer"]


def test_checkpoint_recovery_skips_second_get(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    job = _job(path)
    inventory, offer = _bodies()
    saved = []
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: LegacyStageRead(
            "complete", "PROVIDER_READ_COMPLETE", offer, inventory,
            provider_identity="ebay:account-1",
        ),
    )
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.state_machine.checkpoint_running_job",
        lambda *args: saved.append(args[-1]) or args[-1],
    )
    monkeypatch.setattr(
        worker, "_apply_marker",
        lambda *args: (_ for _ in ()).throw(RuntimeError("process crash")),
    )
    with pytest.raises(RuntimeError, match="process crash"):
        worker._handle_governed(job)
    retry = _job(path)
    retry["payload_json"]["object_generation"] = job["payload_json"][
        "object_generation"
    ]
    retry["payload_json"]["observation_checkpoint"] = saved[0]
    monkeypatch.undo()
    worker, _ = _worker_existing(tmp_path, path)
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.state_machine.checkpoint_running_job",
        lambda *args: args[-1],
    )
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("provider GET repeated")
        ),
    )
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.record_provider_observation",
        lambda observation: observation,
    )
    monkeypatch.setattr(
        "tgw.legacy_stage_corroboration.record_provider_observation",
        lambda observation, **kwargs: observation,
    )
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    assert worker._handle_governed(retry)["outcome"] == "satisfied"


def _worker_existing(tmp_path, path):
    worker = object.__new__(EbayOnboardLegacyStageWorker)
    worker.owner = "owner-1"
    worker.config = {
        "itemdata_root": tmp_path / "items", "data_root": tmp_path,
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "journal",
        "workflow_migration": {"ebay_provider_identity": "ebay:account-1"},
    }
    return worker, path


@pytest.mark.parametrize("outcome", ["contradicted", "indeterminate"])
def test_noncorroborated_read_records_evidence_without_marker(
    tmp_path, monkeypatch, outcome,
):
    worker, path = _worker(tmp_path)
    job = _job(path)
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: LegacyStageRead(outcome, "READ_NOT_CORROBORATED"),
    )
    with pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed(job)
    assert caught.value.result["outcome"] == outcome
    assert "legacy_stage_observation_id" not in json.loads(path.read_text())["ebay_offer"]


def test_checkpoint_cross_binding_is_rejected_without_get_or_marker(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    job = _job(path)
    inventory, offer = _bodies()
    saved = []
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: LegacyStageRead(
            "complete", "PROVIDER_READ_COMPLETE", offer, inventory,
            provider_identity="ebay:account-1",
        ),
    )
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.state_machine.checkpoint_running_job",
        lambda *args: saved.append(args[-1]) or args[-1],
    )
    monkeypatch.setattr(
        worker, "_apply_marker",
        lambda *args: (_ for _ in ()).throw(RuntimeError("stop after checkpoint")),
    )
    with pytest.raises(RuntimeError):
        worker._handle_governed(job)
    forged = _job(path)
    forged["payload_json"]["offer_id"] = "OFF-2"
    forged["payload_json"]["expected_offer"]["offerId"] = "OFF-2"
    forged["payload_json"]["observation_checkpoint"] = saved[0]
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GET repeated")),
    )
    with pytest.raises(HardFailure, match="checkpoint binding mismatch"):
        worker._handle_governed(forged)
    assert "legacy_stage_observation_id" not in json.loads(path.read_text())["ebay_offer"]


def test_checkpoint_replay_reconciles_projection_failure_without_get(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    job = _job(path)
    inventory, offer = _bodies()
    saved = []
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: LegacyStageRead(
            "complete", "PROVIDER_READ_COMPLETE", offer, inventory,
            provider_identity="ebay:account-1",
        ),
    )
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.state_machine.checkpoint_running_job",
        lambda *args: saved.append(args[-1]) or args[-1],
    )
    monkeypatch.setattr(
        "tgw.sqlite_catalog.upsert_catalog_row",
        lambda *args: (_ for _ in ()).throw(OSError("catalog unavailable")),
    )
    with pytest.raises(TreatmentFailure) as first:
        worker._handle_governed(job)
    assert first.value.result["outcome"] == "repair_required"
    retry = _job(path)
    retry["payload_json"]["object_generation"] = job["payload_json"][
        "object_generation"
    ]
    retry["payload_json"]["observation_checkpoint"] = saved[0]
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    monkeypatch.setattr(
        "tgw.workers.ebay_onboard_legacy_stage.read_legacy_stage_observation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("GET repeated")),
    )
    assert worker._handle_governed(retry)["outcome"] == "satisfied"


def test_exact_existing_markers_are_semantic_noop_with_sqlite_verification(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    document = json.loads(path.read_text())
    document["ebay_offer"].update({
        "legacy_stage_observation_id": "a" * 64,
        "stage_content_identity": "c" * 64,
    })
    path.write_text(json.dumps(document))
    payload = _job(path)["payload_json"]
    from tgw.item_mutation import operation_identity
    comparison_fingerprint = "b" * 64
    operation_id = operation_identity(
        sku="SKU-1", kind="ebay-onboard-legacy-stage",
        expected_generation=payload["object_generation"],
        payload={"content_identity": "c" * 64,
                 "comparison_fingerprint": comparison_fingerprint,
                 "observation_id": "a" * 64},
    )
    projections = []
    monkeypatch.setattr(
        "tgw.sqlite_catalog.upsert_catalog_row",
        lambda *args: projections.append(args) or {"ok": True},
    )
    receipt = worker._apply_marker(
        payload, path, "a" * 64, operation_id, comparison_fingerprint,
    )
    assert receipt.status == "COMMITTED"
    assert receipt.changed is False
    assert len(projections) == 1


def test_existing_provider_effect_marker_rejects_legacy_marker(tmp_path):
    worker, path = _worker(tmp_path)
    document = json.loads(path.read_text())
    document["ebay_offer"]["provider_effect_id"] = "f" * 64
    path.write_text(json.dumps(document))
    payload = _job(path)["payload_json"]
    from tgw.item_mutation import operation_identity
    operation_id = operation_identity(
        sku="SKU-1", kind="ebay-onboard-legacy-stage",
        expected_generation=payload["object_generation"],
        payload={"content_identity": "c" * 64,
                 "comparison_fingerprint": "b" * 64,
                 "observation_id": "a" * 64},
    )
    result = worker._apply_marker(
        payload, path, "a" * 64, operation_id, "b" * 64,
    )
    assert result.status == "FAILED"
    current = json.loads(path.read_text())["ebay_offer"]
    assert current["provider_effect_id"] == "f" * 64
    assert "legacy_stage_observation_id" not in current


def _stage_goal():
    return GoalProfile(
        identity="test.stage", version="1",
        required=("staged_content_current",),
    )


def test_legacy_marker_authoritatively_makes_snapshot_current(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    document = json.loads(path.read_text())
    content_identity = listing_content_identity(document)
    document["ebay_offer"].update({
        "legacy_stage_observation_id": "a" * 64,
        "stage_content_identity": content_identity,
    })
    path.write_text(json.dumps(document))
    monkeypatch.setattr(
        "tgw.provider_observations.lookup_authoritative_legacy_stage_receipt",
        lambda **kwargs: {
            "receipt_id": kwargs["observation_id"],
            "content_identity": kwargs["content_identity"],
            "offer_id": kwargs["offer_id"],
        },
    )
    lookup = _authoritative_stage_lookup(document, "ebay:account-1")
    snapshot = build_item_snapshot(path, _stage_goal(), stage_receipt_lookup=lookup)
    assertion = next(item for item in snapshot.assertions
                     if item.condition_id == "staged_content_current")
    assert assertion.result is FingerprintResult.TRUE


def test_isolated_evaluator_moves_from_eligible_to_satisfied(tmp_path, monkeypatch):
    _, path = _worker(tmp_path)
    before = build_item_snapshot(path, TGW_EBAY_LEGACY_STAGE_ONBOARDED)
    initial = evaluate(
        snapshot=before, goal=TGW_EBAY_LEGACY_STAGE_ONBOARDED,
        treatments=LEGACY_STAGE_ONBOARDING_TREATMENTS,
        evaluator_version="legacy-stage-onboarding-test/v1",
    )
    assert [item.treatment_id for item in initial.eligible_treatments] == [
        "ebay-onboard-legacy-stage",
    ]

    document = json.loads(path.read_text())
    content_identity = listing_content_identity(document)
    document["ebay_offer"].update({
        "legacy_stage_observation_id": "a" * 64,
        "stage_content_identity": content_identity,
    })
    path.write_text(json.dumps(document))
    monkeypatch.setattr(
        "tgw.provider_observations.lookup_authoritative_legacy_stage_receipt",
        lambda **kwargs: {
            "receipt_id": kwargs["observation_id"],
            "content_identity": kwargs["content_identity"],
        },
    )
    lookup = _authoritative_stage_lookup(document, "ebay:account-1")
    after = build_item_snapshot(
        path, TGW_EBAY_LEGACY_STAGE_ONBOARDED,
        stage_receipt_lookup=lookup,
    )
    completed = evaluate(
        snapshot=after, goal=TGW_EBAY_LEGACY_STAGE_ONBOARDED,
        treatments=LEGACY_STAGE_ONBOARDING_TREATMENTS,
        evaluator_version="legacy-stage-onboarding-test/v1",
    )
    assert completed.satisfied_requirements == ("staged", "staged_content_current")
    assert completed.eligible_treatments == ()


@pytest.mark.parametrize("dual", [False, True])
def test_mismatched_or_dual_legacy_marker_fails_snapshot_closed(
    tmp_path, monkeypatch, dual,
):
    worker, path = _worker(tmp_path)
    document = json.loads(path.read_text())
    document["ebay_offer"].update({
        "legacy_stage_observation_id": "a" * 64,
        "stage_content_identity": "stale-content",
    })
    if dual:
        document["ebay_offer"]["provider_effect_id"] = "f" * 64
    path.write_text(json.dumps(document))
    monkeypatch.setattr(
        "tgw.provider_observations.lookup_authoritative_legacy_stage_receipt",
        lambda **kwargs: None,
    )
    lookup = _authoritative_stage_lookup(document, "ebay:account-1")
    snapshot = build_item_snapshot(path, _stage_goal(), stage_receipt_lookup=lookup)
    assertion = next(item for item in snapshot.assertions
                     if item.condition_id == "staged_content_current")
    assert assertion.result in {FingerprintResult.UNKNOWN, FingerprintResult.STALE}


def test_static_worker_has_no_provider_write_or_authority_or_publish_calls():
    source = Path(__file__).parents[1].joinpath(
        "src/tgw/workers/ebay_onboard_legacy_stage.py"
    ).read_text()
    for forbidden in (
        "ebay_post", "ebay_put", "ebay_delete", "reserve_provider_effect",
        "reserve_and_begin_authorized_effect",
        "operator_authority", "publish_offer", "stage_draft",
    ):
        assert forbidden not in source
