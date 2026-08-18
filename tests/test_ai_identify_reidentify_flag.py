"""audit#1143 #1167 — ai_identify.py's force-reidentify flag must actually
persist as cleared, not just clear the in-memory copy.

Bug: handle() did item.pop("ai_reidentify", None) on the in-memory item
dict, but the final write goes through fence_patch_item(self.config, sku,
fence_fields) — a curated allow-list dict that never included this key. The
persisted ai_reidentify=True flag never actually cleared, so every
subsequent ai_identify run for the SKU still saw force_reidentify=True and
re-triggered a billed vision-AI call forever.

All external calls (LLM, product lookup, taxonomy, image hashing) are
mocked — tests pass completely offline with no billed API calls.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

import tgw.apis.ebay.taxonomy as taxonomy_mod
import tgw.apis.lookup as lookup_mod
import tgw.image_hash as image_hash_mod
import tgw.workers.ai_identify as ai_identify_mod
from tgw.item_mutation import item_generation, operation_identity
from tgw.workers.ai_identify import AIIdentifyWorker


def _item(sku: str, ai_reidentify: bool = True) -> Dict[str, Any]:
    item = {
        "sku": sku,
        "title": "Old Title",
        "ai_identified": True,
    }
    if ai_reidentify:
        item["ai_reidentify"] = True
    return item


def _worker(cfg: Dict[str, Any]) -> AIIdentifyWorker:
    w = AIIdentifyWorker.__new__(AIIdentifyWorker)
    w.config = cfg
    return w


def _cfg(tmp_path) -> Dict[str, Any]:
    return {"itemdata_root": tmp_path}


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def _mock_common(monkeypatch, tmp_path, sku):
    fake_photo = tmp_path / sku / "photo.jpg"
    fake_photo.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    monkeypatch.setattr(ai_identify_mod, "_asset_ordered_photos", lambda item, sku_dir: [fake_photo])
    monkeypatch.setattr(ai_identify_mod, "get_task_model", lambda cfg, task: ("openrouter", "google/gemini-2.5-flash-lite"))
    monkeypatch.setattr(ai_identify_mod, "_encode_resized", lambda p, max_px=512: ("base64data", 10, 5))
    monkeypatch.setattr(ai_identify_mod, "call_model", lambda *a, **k: '{"title": "New Title", "category": "Widgets"}')
    monkeypatch.setattr(ai_identify_mod, "extract_json", lambda raw: json.loads(raw))
    monkeypatch.setattr(lookup_mod, "lookup_product", lambda item, cfg: None)
    monkeypatch.setattr(taxonomy_mod, "best_category", lambda cfg, title, category: (None, None))
    monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda p: "fakehash")
    monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda h, task: None)
    monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **k: None)


def test_force_reidentify_flag_is_actually_persisted_as_cleared(tmp_path, monkeypatch):
    sku = "tgw1"
    _write_item(tmp_path, sku, _item(sku, ai_reidentify=True))
    _mock_common(monkeypatch, tmp_path, sku)

    patched = {}
    monkeypatch.setattr(ai_identify_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker.handle({"payload_json": {"sku": sku}})

    assert patched.get("ai_reidentify") is None
    assert "ai_reidentify" in patched


def test_no_reidentify_flag_means_no_clearing_write(tmp_path, monkeypatch):
    # When force_reidentify was never set, there's nothing to clear — the
    # fence write shouldn't carry a no-op ai_reidentify key.
    sku = "tgw2"
    _write_item(tmp_path, sku, _item(sku, ai_reidentify=False))
    # already_identified=True and force_reidentify=False means handle()
    # would normally skip — flip ai_identified off so the call proceeds.
    doc = json.loads((tmp_path / sku / f"{sku}.json").read_text())
    doc["ai_identified"] = False
    (tmp_path / sku / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")
    _mock_common(monkeypatch, tmp_path, sku)

    patched = {}
    monkeypatch.setattr(ai_identify_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker.handle({"payload_json": {"sku": sku}})

    assert "ai_reidentify" not in patched


def _governed_job(sku: str, generation: str) -> Dict[str, Any]:
    return {
        "job_id": "11111111-1111-4111-8111-111111111111",
        "lease_token": "22222222-2222-4222-8222-222222222222",
        "entity_type": "item",
        "entity_id": sku,
        "payload_json": {
            "sku": sku,
            "entity_id": sku,
            "object_id": sku,
            "treatment_id": "ai-identify",
            "treatment_version": "1",
            "graph_id": "graph-1",
            "goal_profile_id": "tgw.ebay_identified",
            "goal_profile_version": "1",
            "object_generation": generation,
            "condition_hash": "condition-1",
        },
    }


def test_governed_already_identified_returns_fully_bound_receipt(tmp_path):
    sku = "tgw-governed"
    item = _item(sku, ai_reidentify=False)
    _write_item(tmp_path, sku, item)

    receipt = _worker(_cfg(tmp_path)).handle(
        _governed_job(sku, item_generation(item))
    )

    assert receipt["receipt_schema_id"] == "treatment-receipt/v1"
    assert receipt["entity_id"] == sku
    assert receipt["graph_id"] == "graph-1"
    assert receipt["object_generation"] == item_generation(item)
    assert receipt["evidence"]["changed"] is False


def test_governed_generation_mismatch_stops_before_ai_effect(tmp_path):
    sku = "tgw-stale"
    _write_item(tmp_path, sku, _item(sku, ai_reidentify=False))

    with pytest.raises(Exception, match="object generation mismatch"):
        _worker(_cfg(tmp_path)).handle(_governed_job(sku, "0" * 64))


def test_governed_operator_edit_during_model_call_conflicts_without_stale_write(
    tmp_path, monkeypatch,
):
    sku = "tgw-concurrent"
    item = _item(sku, ai_reidentify=True)
    _write_item(tmp_path, sku, item)
    _mock_common(monkeypatch, tmp_path, sku)
    job = _governed_job(sku, item_generation(item))
    path = tmp_path / sku / f"{sku}.json"

    def checkpoint(_job_id, _owner, _token, value):
        current = json.loads(path.read_text(encoding="utf-8"))
        current["operator_note"] = "newer edit"
        path.write_text(json.dumps(current), encoding="utf-8")
        return value

    monkeypatch.setattr(ai_identify_mod.state_machine, "checkpoint_running_job", checkpoint)
    monkeypatch.setattr(
        ai_identify_mod,
        "fence_patch_item",
        lambda *a, **k: pytest.fail("governed path must not use unfenced patch"),
    )
    worker = _worker({
        **_cfg(tmp_path),
        "data_root": tmp_path,
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "journal",
    })
    worker.owner = "worker-1"

    receipt = worker.handle(job)

    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["reason_code"] == "MUTATION_CONFLICT_REEVALUATE"

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["title"] == "Old Title"
    assert after["operator_note"] == "newer edit"


def test_governed_checkpoint_replay_skips_model_and_commits_exact_fields(
    tmp_path, monkeypatch,
):
    sku = "tgw-recovery"
    item = _item(sku, ai_reidentify=True)
    _write_item(tmp_path, sku, item)
    job = _governed_job(sku, item_generation(item))
    fields = {"title": "Checkpoint Title", "ai_identified": True, "ai_reidentify": None}
    mutation_payload = {
        "schema": "ai-identify-observation/v1",
        "job_id": job["job_id"],
        "graph_id": job["payload_json"]["graph_id"],
        "fields": fields,
    }
    checkpoint = {
        "schema": "ai-identify-observation/v1",
        "sku": sku,
        "expected_generation": job["payload_json"]["object_generation"],
        "fields": fields,
        "operation_id": operation_identity(
            sku=sku,
            kind="ai-identify",
            expected_generation=job["payload_json"]["object_generation"],
            payload=mutation_payload,
        ),
    }
    job["payload_json"]["observation_checkpoint"] = checkpoint
    monkeypatch.setattr(
        ai_identify_mod.state_machine,
        "checkpoint_running_job",
        lambda _job_id, _owner, _token, value: value,
    )
    monkeypatch.setattr(
        ai_identify_mod,
        "call_model",
        lambda *a, **k: pytest.fail("checkpoint recovery must not call model"),
    )
    projection_calls = 0

    def project(_cfg, _doc):
        nonlocal projection_calls
        projection_calls += 1
        if projection_calls == 1:
            raise RuntimeError("simulated projection interruption")
        return {"ok": True}

    monkeypatch.setattr(ai_identify_mod, "upsert_catalog_row", project)
    worker = _worker({
        **_cfg(tmp_path),
        "data_root": tmp_path,
        "archive_root": tmp_path / "archive",
        "item_mutation_journal_root": tmp_path / "journal",
    })
    worker.owner = "worker-1"

    receipt = worker.handle(job)

    after = json.loads((tmp_path / sku / f"{sku}.json").read_text(encoding="utf-8"))
    assert after["title"] == "Checkpoint Title"
    assert "ai_reidentify" not in after
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["operation_id"] == checkpoint["operation_id"]
    assert projection_calls == 2
