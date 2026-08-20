import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tgw.provider_effects import ProviderEffectConflict
from tgw.workflow.post_push_sync import dispatch_targeted_sync


def _item(tmp_path, *, marker="effect-1"):
    path = tmp_path / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sku": "SKU-1",
        "ebay_offer": {"offer_id": "OFF-1", "provider_effect_id": marker},
    }))
    return path


def test_dispatch_is_constructed_from_exact_ledger_and_canonical_marker(tmp_path):
    calls = []
    source = SimpleNamespace(operation="stage-draft")
    with patch("tgw.workflow.post_push_sync.resolve_succeeded_provider_effect",
               return_value=(source, "OFF-1")) as resolve:
        result = dispatch_targeted_sync(
            _item(tmp_path), source_provider_effect_id="effect-1",
            provider_identity="ebay:account",
            enqueue_fn=lambda **kwargs: calls.append(kwargs) or "sync-job",
        )
    assert result.enqueued is True
    resolve.assert_called_once_with(
        provider_effect_id="effect-1", sku="SKU-1",
        provider_identity="ebay:account",
    )
    payload = calls[0]["payload"]
    assert payload["provider_effect_id"] == "effect-1"
    assert payload["expected_offer_id"] == "OFF-1"
    assert payload["object_generation"]
    assert calls[0]["dedupe_key"] == (
        f"treatment:ebay_sync:item:SKU-1:{payload['object_generation']}:"
        "ebay-sync-targeted:1"
    )
    assert calls[0]["queue_name"] == "ebay_sync"


def test_dispatch_rejects_missing_or_forged_canonical_marker(tmp_path):
    source = SimpleNamespace(operation="stage-draft")
    with patch("tgw.workflow.post_push_sync.resolve_succeeded_provider_effect",
               return_value=(source, "OFF-1")), pytest.raises(ProviderEffectConflict):
        dispatch_targeted_sync(
            _item(tmp_path, marker="forged"),
            source_provider_effect_id="effect-1", provider_identity="ebay:account",
            enqueue_fn=lambda **kwargs: pytest.fail("must not enqueue"),
        )


@pytest.mark.parametrize("document_sku", [None, "OTHER"])
def test_dispatch_rejects_missing_or_path_mismatched_sku_before_ledger(
    tmp_path, document_sku,
):
    path = tmp_path / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sku": document_sku,
        "ebay_offer": {"offer_id": "OFF-1", "provider_effect_id": "effect-1"},
    }))
    with patch("tgw.workflow.post_push_sync.resolve_succeeded_provider_effect") as ledger, \
         pytest.raises(ValueError):
        dispatch_targeted_sync(
            path, source_provider_effect_id="effect-1",
            provider_identity="ebay:account",
            enqueue_fn=lambda **kwargs: pytest.fail("must not enqueue"),
        )
    ledger.assert_not_called()


def test_dispatch_duplicate_is_truthful_already_dispatched(tmp_path):
    class Duplicate(Exception):
        pgcode = "23505"

    source = SimpleNamespace(operation="stage-draft")
    with patch("tgw.workflow.post_push_sync.resolve_succeeded_provider_effect",
               return_value=(source, "OFF-1")):
        result = dispatch_targeted_sync(
            _item(tmp_path), source_provider_effect_id="effect-1",
            provider_identity="ebay:account",
            enqueue_fn=lambda **kwargs: (_ for _ in ()).throw(Duplicate()),
        )
    assert result.enqueued is False
    assert result.outcome == "already_dispatched"
