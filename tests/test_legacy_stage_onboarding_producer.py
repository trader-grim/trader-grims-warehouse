import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tgw.item_mutation import item_generation
from tgw.workflow.legacy_stage_onboarding import (
    PAYLOAD_SCHEMA,
    inventory_legacy_stage_onboarding,
    request_legacy_stage_onboarding,
)
from tgw.workflow.operator_authority import listing_content_identity
from tgw.workflow_kernel.scheduler import DispatchResult


def _fixture(tmp_path):
    sku = "SKU-LEGACY-1"
    path = tmp_path / sku / f"{sku}.json"
    path.parent.mkdir()
    item = {
        "sku": sku, "title": "Legacy item",
        "ebay_offer": {"offer_id": "OFF-1", "marketplace_id": "EBAY_US"},
    }
    path.write_text(json.dumps(item))
    config = {
        "itemdata_root": tmp_path,
        "workflow_migration": {
            "ebay_provider_identity": "ebay:account-1",
            "ebay_legacy_stage_onboarding_consumer": "workflow",
        },
    }
    return sku, path, item, config


def _bodies(monkeypatch):
    inventory = {"product": {"title": "Legacy item"}}
    offer = {"offerId": "OFF-1", "marketplaceId": "EBAY_US"}
    monkeypatch.setattr(
        "tgw.workflow.legacy_stage_onboarding._build_offer_bodies",
        lambda *args, **kwargs: (inventory, offer),
    )
    return inventory, offer


def test_producer_constructs_exact_generation_bound_payload_and_dedupe(
    tmp_path, monkeypatch,
):
    sku, path, item, config = _fixture(tmp_path)
    inventory, offer = _bodies(monkeypatch)
    calls = []

    def enqueue(**kwargs):
        calls.append(kwargs)
        return "job-1"

    result = request_legacy_stage_onboarding(
        path, sku=sku, config=config, enqueue_fn=enqueue,
    )
    assert result.enqueued is True
    assert len(calls) == 1
    call = calls[0]
    assert call["queue_name"] == "ebay_onboard_legacy_stage"
    assert call["entity_type"] == "item" and call["entity_id"] == sku
    payload = call["payload"]
    assert payload["payload_schema_id"] == PAYLOAD_SCHEMA
    assert payload["provider_identity"] == "ebay:account-1"
    assert payload["offer_id"] == "OFF-1"
    assert payload["object_generation"] == item_generation(item)
    assert payload["content_identity"] == listing_content_identity(item)
    assert payload["expected_inventory_item"] == inventory
    assert payload["expected_offer"] == offer
    assert payload["treatment_id"] == "ebay-onboard-legacy-stage"
    assert payload["treatment_version"] == "1"
    assert call["dedupe_key"] == (
        f"treatment:ebay_onboard_legacy_stage:item:{sku}:"
        f"{payload['object_generation']}:ebay-onboard-legacy-stage:1"
    )


def test_missing_canonical_marketplace_uses_exact_read_only_offer(
    tmp_path, monkeypatch,
):
    sku, path, item, config = _fixture(tmp_path)
    item["ebay_offer"].pop("marketplace_id")
    path.write_text(json.dumps(item))
    get = MagicMock(return_value={
        "offerId": "OFF-1", "sku": sku, "marketplaceId": "EBAY_MOTORS_US",
    })
    monkeypatch.setattr("tgw.workflow.legacy_stage_onboarding.ebay_get", get)
    build = MagicMock(return_value=(
        {"product": {"title": "Legacy item"}},
        {"offerId": "OFF-1", "marketplaceId": "EBAY_MOTORS_US"},
    ))
    monkeypatch.setattr(
        "tgw.workflow.legacy_stage_onboarding._build_offer_bodies", build,
    )
    calls = []
    request_legacy_stage_onboarding(
        path, sku=sku, config=config,
        enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-1",
    )
    get.assert_called_once_with(config, "/sell/inventory/v1/offer/OFF-1")
    assert build.call_args.kwargs["known_marketplace_id"] == "EBAY_MOTORS_US"
    assert calls[0]["payload"]["expected_offer"]["marketplaceId"] == (
        "EBAY_MOTORS_US"
    )


@pytest.mark.parametrize(
    "observed",
    [None, {}, {"offerId": "OTHER", "sku": "SKU-LEGACY-1",
                "marketplaceId": "EBAY_US"},
     {"offerId": "OFF-1", "sku": "OTHER", "marketplaceId": "EBAY_US"},
     {"offerId": "OFF-1", "sku": "SKU-LEGACY-1", "marketplaceId": ""}],
)
def test_marketplace_discovery_fails_closed_on_unbound_observation(
    tmp_path, monkeypatch, observed,
):
    sku, path, item, config = _fixture(tmp_path)
    item["ebay_offer"].pop("marketplace_id")
    path.write_text(json.dumps(item))
    monkeypatch.setattr(
        "tgw.workflow.legacy_stage_onboarding.ebay_get",
        MagicMock(return_value=observed),
    )
    enqueue = MagicMock()
    with pytest.raises(ValueError):
        request_legacy_stage_onboarding(
            path, sku=sku, config=config, enqueue_fn=enqueue,
        )
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "migration",
    [{}, {"ebay_provider_identity": ""},
     {"ebay_provider_identity": " ebay:account-1"},
     {"ebay_provider_identity": "ebay:other"}],
)
def test_provider_identity_is_config_derived_and_exact(
    tmp_path, monkeypatch, migration,
):
    sku, path, _, config = _fixture(tmp_path)
    config["workflow_migration"] = {
        **migration, "ebay_legacy_stage_onboarding_consumer": "workflow",
    }
    _bodies(monkeypatch)
    if migration.get("ebay_provider_identity") == "ebay:other":
        calls = []
        request_legacy_stage_onboarding(
            path, sku=sku, config=config,
            enqueue_fn=lambda **kwargs: calls.append(kwargs) or "job-1",
        )
        assert calls[0]["payload"]["provider_identity"] == "ebay:other"
    else:
        with pytest.raises(ValueError, match="configured provider identity"):
            request_legacy_stage_onboarding(path, sku=sku, config=config)


@pytest.mark.parametrize("mode", [None, "off", "invalid"])
def test_consumer_must_be_exactly_admitted_before_body_build_or_enqueue(
    tmp_path, monkeypatch, mode,
):
    sku, path, _, config = _fixture(tmp_path)
    if mode is None:
        config["workflow_migration"].pop("ebay_legacy_stage_onboarding_consumer")
    else:
        config["workflow_migration"]["ebay_legacy_stage_onboarding_consumer"] = mode
    bodies = MagicMock()
    monkeypatch.setattr("tgw.workflow.legacy_stage_onboarding._build_offer_bodies", bodies)
    enqueue = MagicMock()
    with pytest.raises(ValueError, match="consumer is not admitted"):
        request_legacy_stage_onboarding(path, sku=sku, config=config, enqueue_fn=enqueue)
    bodies.assert_not_called()
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "marker", ["provider_effect_id", "legacy_stage_observation_id",
               "stage_content_identity"],
)
def test_any_existing_authority_marker_blocks_producer(tmp_path, monkeypatch, marker):
    sku, path, item, config = _fixture(tmp_path)
    item["ebay_offer"][marker] = None
    path.write_text(json.dumps(item))
    _bodies(monkeypatch)
    with pytest.raises(ValueError, match="already has staged authority evidence"):
        request_legacy_stage_onboarding(path, sku=sku, config=config)


def test_no_eligible_or_waiting_disposition_fails_without_enqueue(tmp_path, monkeypatch):
    sku, path, _, config = _fixture(tmp_path)
    _bodies(monkeypatch)
    monkeypatch.setattr(
        "tgw.workflow.legacy_stage_onboarding.evaluate",
        lambda **kwargs: SimpleNamespace(
            object_generation=kwargs["snapshot"].generation,
            eligible_treatments=(), waiting_treatments=(),
        ),
    )
    enqueue = MagicMock()
    with pytest.raises(ValueError, match="one legal disposition"):
        request_legacy_stage_onboarding(
            path, sku=sku, config=config, enqueue_fn=enqueue,
        )
    enqueue.assert_not_called()


def test_item_change_after_evaluation_blocks_stale_dispatch(tmp_path, monkeypatch):
    sku, path, _, config = _fixture(tmp_path)
    _bodies(monkeypatch)
    from tgw.workflow import legacy_stage_onboarding as module
    original = module.evaluate

    def change_after_evaluate(**kwargs):
        graph = original(**kwargs)
        current = json.loads(path.read_text())
        current["operator_note"] = "newer"
        path.write_text(json.dumps(current))
        return graph

    monkeypatch.setattr(module, "evaluate", change_after_evaluate)
    enqueue = MagicMock()
    with pytest.raises(ValueError, match="changed before onboarding dispatch"):
        request_legacy_stage_onboarding(
            path, sku=sku, config=config, enqueue_fn=enqueue,
        )
    enqueue.assert_not_called()


def test_duplicate_disposition_is_truthful_already_dispatched(tmp_path, monkeypatch):
    sku, path, _, config = _fixture(tmp_path)
    _bodies(monkeypatch)
    monkeypatch.setattr(
        "tgw.workflow.legacy_stage_onboarding.dispatch_treatment",
        lambda **kwargs: DispatchResult(
            treatment_id="ebay-onboard-legacy-stage", treatment_version="1",
            queue_name="ebay_onboard_legacy_stage", entity_id=sku,
            enqueued=False, outcome="already_dispatched",
        ),
    )
    result = request_legacy_stage_onboarding(path, sku=sku, config=config)
    assert result.enqueued is False and result.outcome == "already_dispatched"


def test_inventory_is_select_only_schema_classified_and_privacy_safe():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = [
        ("cancelled", "ambiguous", 2), ("cancelled", "schema_v1", 1),
        ("queued", "schema_v1", 3), ("running", "ambiguous", 1),
    ]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    result = inventory_legacy_stage_onboarding(connection=connection)
    assert result == {
        "cancelled": {"ambiguous": 2, "schema_v1": 1},
        "queued": {"schema_v1": 3}, "running": {"ambiguous": 1},
    }
    sql, params = cursor.execute.call_args.args
    assert sql.lstrip().upper().startswith("SELECT")
    assert "payload_json->>'payload_schema_id'" in sql
    assert "cancelled" not in sql
    assert params == (PAYLOAD_SCHEMA, "ebay_onboard_legacy_stage")
    assert "payload_json" not in json.dumps(result)
