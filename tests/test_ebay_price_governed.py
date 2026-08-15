from __future__ import annotations

import json

import pytest

import tgw.workers.ebay_price as price_mod
from tgw.item_mutation import item_generation, operation_identity


def _job(sku: str, generation: str, fields: dict) -> dict:
    job_id = "11111111-1111-4111-8111-111111111111"
    payload = {
        "sku": sku, "entity_id": sku, "object_id": sku,
        "treatment_id": "ebay-price", "treatment_version": "1",
        "graph_id": "graph-1", "goal_profile_id": "tgw.ebay_identified",
        "goal_profile_version": "1", "object_generation": generation,
        "condition_hash": "condition-1",
    }
    binding = {
        "schema": "ebay-price-observation/v1", "job_id": job_id,
        "graph_id": "graph-1", "fields": fields,
    }
    payload["observation_checkpoint"] = {
        "schema": "ebay-price-observation/v1", "sku": sku,
        "expected_generation": generation, "fields": fields,
        "operation_id": operation_identity(
            sku=sku, kind="ebay-price", expected_generation=generation,
            payload=binding,
        ),
    }
    return {
        "job_id": job_id,
        "lease_token": "22222222-2222-4222-8222-222222222222",
        "entity_type": "item", "entity_id": sku, "payload_json": payload,
    }


def _worker(tmp_path):
    worker = price_mod.EbayPriceWorker.__new__(price_mod.EbayPriceWorker)
    worker.owner = "worker-1"
    worker.config = {
        "itemdata_root": tmp_path, "data_root": tmp_path,
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "journal",
    }
    return worker


def _write(tmp_path, sku, item):
    directory = tmp_path / sku
    directory.mkdir()
    path = directory / f"{sku}.json"
    path.write_text(json.dumps(item), encoding="utf-8")
    return path


def test_checkpoint_replay_skips_comps_and_repairs_projection(tmp_path, monkeypatch):
    sku = "SKU-1"
    item = {"sku": sku, "title": "Item", "draft_listing": {"title": "Item"}}
    path = _write(tmp_path, sku, item)
    fields = {
        "ebay_offer": {"price": 19.99, "target_price": 9.99},
        "draft_listing": {"title": "Item", "price": 19.99},
    }
    job = _job(sku, item_generation(item), fields)
    monkeypatch.setattr(
        price_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(
        price_mod, "suggest_price",
        lambda *a, **k: pytest.fail("checkpoint replay must not query comps"),
    )
    calls = 0

    def project(_cfg, _document):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("projection interrupted")
        return {"ok": True}

    monkeypatch.setattr(price_mod, "upsert_catalog_row", project)

    receipt = _worker(tmp_path).handle(job)

    after = json.loads(path.read_text())
    assert after["draft_listing"]["price"] == 19.99
    assert receipt["outcome"] == "satisfied"
    assert receipt["established_conditions"] == ["priced"]
    assert calls == 2


def test_checkpoint_replay_refuses_newer_operator_edit(tmp_path, monkeypatch):
    sku = "SKU-2"
    original = {"sku": sku, "title": "Item", "draft_listing": {"title": "Item"}}
    path = _write(tmp_path, sku, original)
    fields = {
        "ebay_offer": {"price": 19.99},
        "draft_listing": {"title": "Item", "price": 19.99},
    }
    job = _job(sku, item_generation(original), fields)
    path.write_text(json.dumps(dict(original, operator_note="newer")))
    monkeypatch.setattr(
        price_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(price_mod, "upsert_catalog_row", lambda *_: {"ok": True})

    with pytest.raises(Exception, match="mutation did not commit: CONFLICT"):
        _worker(tmp_path).handle(job)

    after = json.loads(path.read_text())
    assert after["operator_note"] == "newer"
    assert "ebay_offer" not in after


def test_no_price_commit_is_partial_and_establishes_nothing(tmp_path, monkeypatch):
    sku = "SKU-3"
    item = {"sku": sku, "title": "Item", "draft_listing": {"title": "Item"}}
    _write(tmp_path, sku, item)
    fields = {
        "ebay_offer": {"price": None},
        "draft_listing": {"title": "Item", "price": None},
    }
    job = _job(sku, item_generation(item), fields)
    monkeypatch.setattr(
        price_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(price_mod, "upsert_catalog_row", lambda *_: {"ok": True})

    with pytest.raises(Exception) as caught:
        _worker(tmp_path).handle(job)

    assert caught.value.result["outcome"] == "partial"
    assert caught.value.result["established_conditions"] == []
    assert caught.value.result["evidence"]["reason_code"] == "PRICE_REQUIRES_OPERATOR_INPUT"


def test_fresh_governed_price_checkpoints_before_single_cas_write(tmp_path, monkeypatch):
    sku = "SKU-4"
    item = {
        "sku": sku, "title": "Item", "condition": "New",
        "draft_listing": {"title": "Item", "category_id": "123"},
    }
    path = _write(tmp_path, sku, item)
    job = _job(sku, item_generation(item), {})
    job["payload_json"].pop("observation_checkpoint")
    checkpoints = []

    def checkpoint(_job_id, _owner, _token, value):
        checkpoints.append(value)
        return value

    monkeypatch.setattr(price_mod.state_machine, "checkpoint_running_job", checkpoint)
    monkeypatch.setattr(price_mod, "suggest_price", lambda *a, **k: {
        "price": 10.0,
        "source": "test-comps",
        "comps": {"count": 3, "min": 8.0, "p25": 10.0,
                  "median": 12.0, "p75": 14.0, "max": 15.0},
        "comp_items": [],
        "queried_at": "2026-08-10T00:00:00+00:00",
        "price_confidence": "medium",
    })
    monkeypatch.setattr(price_mod, "upsert_catalog_row", lambda *_: {"ok": True})
    monkeypatch.setattr(
        price_mod, "fence_ebay_write",
        lambda *a, **k: pytest.fail("governed price must not use legacy eBay fence"),
    )
    monkeypatch.setattr(
        price_mod, "fence_patch_item",
        lambda *a, **k: pytest.fail("governed price must not use legacy item fence"),
    )

    receipt = _worker(tmp_path).handle(job)

    after = json.loads(path.read_text())
    assert checkpoints and checkpoints[0]["schema"] == "ebay-price-observation/v1"
    assert after["draft_listing"]["price"] == 16.99
    assert after["ebay_offer"]["target_price"] == 10.0
    assert receipt["outcome"] == "satisfied"
