"""todo #1287 / PP-COHESION-001 — ai_identify.py's local variable `model`
(the LLM provider model id) was silently clobbered by the AI-extracted
item's product-`model` field (`model = _str("model")`) before being read
into `identification_history`/`vision_results` provenance — corrupting
which LLM actually produced the identification.

Fix: the extracted-item variable is renamed to `item_model`, leaving the
provider `model` variable (set via `get_task_model()`) untouched through to
the provenance writes.

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


def _item(sku: str) -> Dict[str, Any]:
    return {"sku": sku, "title": "Old Title", "ai_identified": False}


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
    # provider-model id — must be preserved distinctly from the extracted
    # product-model field below.
    monkeypatch.setattr(ai_identify_mod, "get_task_model", lambda cfg, task: ("openrouter", "anthropic/claude-4.5-vision"))
    monkeypatch.setattr(ai_identify_mod, "_encode_resized", lambda p, max_px=512: ("base64data", 10, 5))
    monkeypatch.setattr(
        ai_identify_mod,
        "call_model",
        lambda *a, **k: json.dumps({"title": "New Title", "category": "Widgets", "model": "PS5-CFI-1215A"}),
    )
    monkeypatch.setattr(ai_identify_mod, "extract_json", lambda raw: json.loads(raw))
    monkeypatch.setattr(lookup_mod, "lookup_product", lambda item, cfg: None)
    monkeypatch.setattr(taxonomy_mod, "best_category", lambda cfg, title, category: (None, None))
    monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda p: "fakehash")
    monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda h, task: None)
    monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **k: None)


def test_provenance_records_llm_model_not_extracted_product_model(tmp_path, monkeypatch):
    sku = "tgw1"
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path, sku)

    patched = {}
    monkeypatch.setattr(
        ai_identify_mod, "fence_patch_item",
        lambda cfg, sku, fields: patched.update(fields) or {"ok": True},
    )

    worker = _worker(_cfg(tmp_path))
    worker.handle({"payload_json": {"sku": sku}})

    expected_provenance = "openrouter/anthropic/claude-4.5-vision"

    # identification_history entry's "model" field is LLM provenance, NOT
    # the extracted product model ("openrouter/PS5-CFI-1215A" would be the
    # bug's corrupted value).
    history = patched["identification_history"]
    assert history[-1]["model"] == expected_provenance
    assert history[-1]["model"] != "openrouter/PS5-CFI-1215A"

    # vision_results entry: top-level "model" is the same LLM provenance,
    # while the nested extracted.model still correctly holds the product
    # model value — not lost, just correctly separated.
    vision_entry = patched["vision_results"][-1]
    assert vision_entry["model"] == expected_provenance
    assert vision_entry["extracted"]["model"] == "PS5-CFI-1215A"

    # canonical item["model"] field (written via the _field/_val loop) is
    # still populated with the extracted product model, unaffected by the
    # rename.
    assert patched["model"] == "PS5-CFI-1215A"
