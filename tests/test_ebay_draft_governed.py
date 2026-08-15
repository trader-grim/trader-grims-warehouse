from __future__ import annotations

import json

import pytest

import tgw.workers.ebay_draft as draft_mod
from tgw.item_mutation import item_generation, operation_identity


def _job(sku: str, generation: str, fields: dict) -> dict:
    payload = {
        "sku": sku,
        "entity_id": sku,
        "object_id": sku,
        "treatment_id": "ebay-draft",
        "treatment_version": "1",
        "graph_id": "graph-1",
        "goal_profile_id": "tgw.ebay_identified",
        "goal_profile_version": "1",
        "object_generation": generation,
        "condition_hash": "condition-1",
    }
    mutation_payload = {
        "schema": "ebay-draft-observation/v1",
        "job_id": "11111111-1111-4111-8111-111111111111",
        "graph_id": "graph-1",
        "fields": fields,
    }
    payload["observation_checkpoint"] = {
        "schema": "ebay-draft-observation/v1",
        "sku": sku,
        "expected_generation": generation,
        "fields": fields,
        "operation_id": operation_identity(
            sku=sku, kind="ebay-draft", expected_generation=generation,
            payload=mutation_payload,
        ),
    }
    return {
        "job_id": mutation_payload["job_id"],
        "lease_token": "22222222-2222-4222-8222-222222222222",
        "entity_type": "item",
        "entity_id": sku,
        "payload_json": payload,
    }


def _worker(tmp_path):
    worker = draft_mod.EbayDraftWorker.__new__(draft_mod.EbayDraftWorker)
    worker.owner = "worker-1"
    worker.config = {
        "itemdata_root": tmp_path,
        "data_root": tmp_path,
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


def test_checkpoint_replay_skips_model_and_repairs_projection(tmp_path, monkeypatch):
    sku = "SKU-1"
    item = {"sku": sku, "title": "Identified item", "ai_identified": True}
    path = _write(tmp_path, sku, item)
    fields = {"draft_listing": {"title": "Draft", "category_id": "123"}}
    job = _job(sku, item_generation(item), fields)
    monkeypatch.setattr(
        draft_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(
        draft_mod, "call_model",
        lambda *a, **k: pytest.fail("checkpoint replay must not call model"),
    )
    calls = 0

    def project(_cfg, _document):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("projection interrupted")
        return {"ok": True}

    monkeypatch.setattr(draft_mod, "upsert_catalog_row", project)

    receipt = _worker(tmp_path).handle(job)

    assert json.loads(path.read_text())["draft_listing"] == fields["draft_listing"]
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["operation_id"] == job["payload_json"]["observation_checkpoint"]["operation_id"]
    assert calls == 2


def test_checkpoint_replay_refuses_newer_operator_generation(tmp_path, monkeypatch):
    sku = "SKU-2"
    original = {"sku": sku, "title": "Identified item", "ai_identified": True}
    path = _write(tmp_path, sku, original)
    fields = {"draft_listing": {"title": "Stale draft", "category_id": "123"}}
    job = _job(sku, item_generation(original), fields)
    newer = dict(original, operator_note="newer edit")
    path.write_text(json.dumps(newer), encoding="utf-8")
    monkeypatch.setattr(
        draft_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(draft_mod, "upsert_catalog_row", lambda *_: {"ok": True})

    with pytest.raises(Exception, match="mutation did not commit: CONFLICT"):
        _worker(tmp_path).handle(job)

    after = json.loads(path.read_text())
    assert after["operator_note"] == "newer edit"
    assert "draft_listing" not in after


def test_category_99_checkpoint_never_establishes_draft_generated(tmp_path, monkeypatch):
    sku = "SKU-3"
    item = {"sku": sku, "title": "Identified item", "ai_identified": True}
    _write(tmp_path, sku, item)
    fields = {"draft_listing": {"title": "Fallback", "category_id": "99"}}
    job = _job(sku, item_generation(item), fields)
    monkeypatch.setattr(
        draft_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(draft_mod, "upsert_catalog_row", lambda *_: {"ok": True})

    with pytest.raises(Exception) as caught:
        _worker(tmp_path).handle(job)

    receipt = caught.value.result
    assert receipt["outcome"] == "partial"
    assert receipt["established_conditions"] == []
    assert receipt["evidence"]["reason_code"] == "DRAFT_REQUIRES_OPERATOR_CATEGORY"


def test_corrupt_photo_finding_can_be_checkpointed_without_legacy_fence(
    tmp_path, monkeypatch,
):
    sku = "SKU-4"
    item = {"sku": sku, "title": "Identified item", "ai_identified": True}
    directory = tmp_path / sku
    directory.mkdir()
    bad_photo = directory / "bad.jpg"
    bad_photo.write_bytes(b"not-an-image")
    findings = []
    monkeypatch.setattr(draft_mod, "_asset_ordered_photos", lambda *_: [bad_photo])
    monkeypatch.setattr(
        draft_mod, "fence_patch_item",
        lambda *a, **k: pytest.fail("governed finding must not use legacy fence"),
    )

    photos = draft_mod._aspect_fill_photos(
        item, directory, "google_direct", sku=sku, config={},
        finding_sink=findings,
    )

    assert photos == []
    assert findings[0]["code"] == "photo_files_readable"
    fields = {
        "draft_listing": {"title": "Draft", "category_id": "123"},
        "pipeline_error": findings[0],
    }
    path = directory / f"{sku}.json"
    path.write_text(json.dumps(item), encoding="utf-8")
    job = _job(sku, item_generation(item), fields)
    monkeypatch.setattr(
        draft_mod.state_machine, "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(draft_mod, "upsert_catalog_row", lambda *_: {"ok": True})

    receipt = _worker(tmp_path).handle(job)

    assert receipt["outcome"] == "satisfied"
    assert json.loads(path.read_text())["pipeline_error"]["code"] == "photo_files_readable"
