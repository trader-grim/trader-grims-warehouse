from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tgw.ebay.sync import classify_targeted_sync_payload, enqueue_post_push_sync
from tgw.workflow.sync_queue_inventory import targeted_sync_queue_inventory


def test_payload_classifier_never_reinterprets_partial_or_unknown_schema():
    assert classify_targeted_sync_payload({"sku": "SKU-1"}) == "legacy"
    assert classify_targeted_sync_payload({
        "payload_schema_id": "ebay-sync-targeted/v1", "sku": "SKU-1",
    }) == "governed"
    assert classify_targeted_sync_payload({"sku": "SKU-1", "graph_id": "g"}) == "ambiguous"
    assert classify_targeted_sync_payload({
        "payload_schema_id": "ebay-sync-targeted/v2", "sku": "SKU-1",
    }) == "ambiguous"
    assert classify_targeted_sync_payload({"reason": "startup"}) == "periodic"
    assert classify_targeted_sync_payload({"reason": "scheduled"}) == "periodic"
    assert classify_targeted_sync_payload({}) == "ambiguous"
    assert classify_targeted_sync_payload({
        "payload_schema_id": "ebay-sync-targeted/v1",
    }) == "governed"
    for reason in ("manual", "revision_apply"):
        assert classify_targeted_sync_payload({
            "sku": "SKU-1", "reason": reason, "origin": "operator",
        }) == "legacy"
        assert classify_targeted_sync_payload({
            "sku": "SKU-1", "reason": reason, "origin": "workflow",
        }) == "ambiguous"
        assert classify_targeted_sync_payload({
            "sku": "SKU-1", "reason": reason, "origin": "operator", "extra": True,
        }) == "ambiguous"


def test_producer_defaults_legacy_and_workflow_never_falls_back():
    with patch("tgw.ebay.sync.state_machine.enqueue_job", return_value="legacy") as enqueue:
        assert enqueue_post_push_sync("SKU-1", config={}) is True
    assert enqueue.call_args.kwargs["payload"] == {"sku": "SKU-1", "reason": "post_push"}
    config = {"itemdata_root": "/items", "workflow_migration": {
        "ebay_post_push_sync_producer": "workflow",
        "ebay_provider_identity": "ebay:account",
    }}
    with patch("tgw.config.sku_json", return_value="/items/SKU-1/SKU-1.json"), \
         patch("tgw.workflow.post_push_sync.dispatch_targeted_sync",
               return_value=SimpleNamespace(enqueued=True, outcome="dispatched")) as dispatch, \
         patch("tgw.ebay.sync.state_machine.enqueue_job") as legacy:
        assert enqueue_post_push_sync(
            "SKU-1", config=config, source_provider_effect_id="effect-1",
        ) is True
    dispatch.assert_called_once()
    legacy.assert_not_called()


def test_governed_enqueue_failure_propagates_without_legacy_fallback():
    config = {"workflow_migration": {
        "ebay_post_push_sync_producer": "workflow",
        "ebay_provider_identity": "ebay:account",
    }}
    with patch("tgw.config.sku_json", return_value="/items/SKU-1/SKU-1.json"), \
         patch("tgw.workflow.post_push_sync.dispatch_targeted_sync",
               side_effect=RuntimeError("queue unavailable")), \
         patch("tgw.ebay.sync.state_machine.enqueue_job") as legacy, \
         pytest.raises(RuntimeError):
        enqueue_post_push_sync(
            "SKU-1", config=config, source_provider_effect_id="effect-1",
        )
    legacy.assert_not_called()


def test_read_only_inventory_counts_mixed_shapes():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchall.return_value = [
        ("0", "cancelled", {"reason": "scheduled"}),
        ("1", "queued", {"sku": "A"}),
        ("1b", "queued", {"sku": "A", "reason": "manual", "origin": "operator"}),
        ("2", "running", {"payload_schema_id": "ebay-sync-targeted/v1", "sku": "B"}),
        ("3", "failed", {"sku": "C", "graph_id": "partial"}),
    ]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    result = targeted_sync_queue_inventory(connection=connection)
    assert result["counts"] == {
        "periodic": 1, "legacy": 2, "governed": 1, "ambiguous": 1,
    }
    assert all("payload" not in row for row in result["jobs"])
    assert cursor.execute.call_count == 1


@pytest.mark.parametrize("payload", [
    {}, {"graph_id": "partial"},
    {"payload_schema_id": "unknown/v1"},
    {"payload_schema_id": "ebay-sync-targeted/v1"},
])
def test_nonperiodic_missing_sku_never_reaches_bulk(payload, tmp_path):
    from tgw.queue.worker_base import HardFailure
    from tgw.workers.ebay_sync import EbaySyncWorker

    worker = object.__new__(EbaySyncWorker)
    worker.config = {"itemdata_root": tmp_path}
    with patch("tgw.workers.ebay_sync.fetch_all_offers") as bulk, \
         pytest.raises(HardFailure):
        worker.handle({"payload_json": payload})
    bulk.assert_not_called()


@pytest.mark.parametrize("reason", ["startup", "scheduled"])
def test_exact_periodic_shapes_reach_bulk_path(reason, tmp_path):
    from tgw.workers.ebay_sync import EbaySyncWorker

    worker = object.__new__(EbaySyncWorker)
    worker.config = {"itemdata_root": tmp_path}
    worker._record_fallback_state = MagicMock()
    worker._reschedule = MagicMock()
    worker._aspects_warmup_due = MagicMock(return_value=False)
    with patch("tgw.workers.ebay_sync.fetch_all_offers", return_value=[]) as bulk:
        assert worker.handle({"payload_json": {"reason": reason}}) is None
    bulk.assert_called_once()
    worker._reschedule.assert_called_once()


@pytest.mark.parametrize("reason", ["manual", "revision_apply"])
def test_exact_operator_legacy_shapes_reach_targeted_not_bulk(reason, tmp_path):
    from tgw.workers.ebay_sync import EbaySyncWorker

    worker = object.__new__(EbaySyncWorker)
    worker.config = {"itemdata_root": tmp_path}
    worker._sync_one = MagicMock(return_value=0)
    with patch("tgw.ebay.sync._find_offer", return_value=None) as targeted, \
         patch("tgw.workers.ebay_sync.fetch_all_offers") as bulk:
        assert worker.handle({"payload_json": {
            "sku": "SKU-1", "reason": reason, "origin": "operator",
        }}) is None
    targeted.assert_called_once()
    bulk.assert_not_called()
