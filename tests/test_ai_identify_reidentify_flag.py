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

import tgw.apis.ebay.taxonomy as taxonomy_mod
import tgw.apis.lookup as lookup_mod
import tgw.image_hash as image_hash_mod
import tgw.workers.ai_identify as ai_identify_mod
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
    monkeypatch.setattr(ai_identify_mod.state_machine, "enqueue_job", lambda **k: "job-1")


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
