"""audit#COHESION-2026-07 #1288 — ai_identify.py's pHash cache must be keyed
on (phash, task, prompt-context), not just (phash, task).

Bug: lookup_hash(img_hash, "ai_identify") / store_hash(img_hash, sku,
"ai_identify", result) keyed the cache on the photo hash alone. If a SKU is
re-identified later with a newly available ai_hint or product_context that
wasn't present on the first scan, the cache still returned the FIRST
result for that photo, silently ignoring the new context.

Fix: fold the prompt context (product_context or hint) into the cache key
string via a sha256-derived signature, computed alongside img_hash. img_hash
itself keeps being used everywhere else (log_event, vision_record).

All external calls (LLM, product lookup, taxonomy, image hashing) are
mocked — tests pass completely offline with no billed API calls.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

import tgw.apis.ebay.taxonomy as taxonomy_mod
import tgw.apis.lookup as lookup_mod
import tgw.image_hash as image_hash_mod
import tgw.workers.ai_identify as ai_identify_mod
from tgw.workers.ai_identify import AIIdentifyWorker


def _item(sku: str, title: str = "") -> Dict[str, Any]:
    return {
        "sku": sku,
        "title": title,
        "ai_identified": False,
    }


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


def _mock_common(monkeypatch, tmp_path, sku, hint=None, lookup_result=None):
    fake_photo = tmp_path / sku / "photo.jpg"
    fake_photo.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    if hint:
        doc = json.loads((tmp_path / sku / f"{sku}.json").read_text())
        doc["ai_hint"] = hint
        (tmp_path / sku / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")

    monkeypatch.setattr(ai_identify_mod, "_asset_ordered_photos", lambda item, sku_dir: [fake_photo])
    monkeypatch.setattr(ai_identify_mod, "get_task_model", lambda cfg, task: ("openrouter", "google/gemini-2.5-flash-lite"))
    monkeypatch.setattr(ai_identify_mod, "_encode_resized", lambda p, max_px=512: ("base64data", 10, 5))
    monkeypatch.setattr(ai_identify_mod, "call_model", lambda *a, **k: '{"title": "New Title", "category": "Widgets"}')
    monkeypatch.setattr(ai_identify_mod, "extract_json", lambda raw: json.loads(raw))
    monkeypatch.setattr(lookup_mod, "lookup_product", lambda item, cfg: lookup_result)
    monkeypatch.setattr(taxonomy_mod, "best_category", lambda cfg, title, category: (None, None))
    monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda p: "fakehash")
    monkeypatch.setattr(ai_identify_mod, "fence_patch_item", lambda cfg, sku, fields: {"ok": True})


def _context_sig(context: str) -> str:
    return hashlib.sha256(context.encode("utf-8")).hexdigest()[:16]


def test_cache_key_differs_for_different_hints(monkeypatch, tmp_path):
    """Same img_hash, different hint -> different computed cache_key."""
    calls = []
    monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda key, task: calls.append(key) or None)
    monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **k: None)

    sku_a = "tgwA"
    _write_item(tmp_path, sku_a, _item(sku_a))
    _mock_common(monkeypatch, tmp_path, sku_a, hint="Nike shoe")
    _worker(_cfg(tmp_path)).handle({"payload_json": {"sku": sku_a}})

    sku_b = "tgwB"
    _write_item(tmp_path, sku_b, _item(sku_b))
    _mock_common(monkeypatch, tmp_path, sku_b, hint="Adidas shoe")
    _worker(_cfg(tmp_path)).handle({"payload_json": {"sku": sku_b}})

    assert len(calls) == 2
    assert calls[0] != calls[1]
    assert calls[0] == f"fakehash:{_context_sig('Nike shoe')}"
    assert calls[1] == f"fakehash:{_context_sig('Adidas shoe')}"


def test_cache_key_identical_when_no_context(monkeypatch, tmp_path):
    """Same img_hash, both calls with no hint/product_context -> identical
    cache_key ('no_context' suffix) — preserves today's correct behavior."""
    calls = []
    monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda key, task: calls.append(key) or None)
    monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **k: None)

    sku_a = "tgwC"
    _write_item(tmp_path, sku_a, _item(sku_a))
    _mock_common(monkeypatch, tmp_path, sku_a)
    _worker(_cfg(tmp_path)).handle({"payload_json": {"sku": sku_a}})

    sku_b = "tgwD"
    _write_item(tmp_path, sku_b, _item(sku_b))
    _mock_common(monkeypatch, tmp_path, sku_b)
    _worker(_cfg(tmp_path)).handle({"payload_json": {"sku": sku_b}})

    assert len(calls) == 2
    assert calls[0] == calls[1] == "fakehash:no_context"


def test_new_context_does_not_hit_stale_no_context_cache_entry(monkeypatch, tmp_path):
    """First call (no context) stores under key A ('fakehash:no_context').
    Second call for the same photo but with a real hint now present must
    NOT hit key A's cached result — confirms the bug scenario is fixed."""
    store = {}

    def fake_lookup(key, task):
        return store.get(key)

    def fake_store(key, sku, task, result):
        store[key] = result

    monkeypatch.setattr(image_hash_mod, "lookup_hash", fake_lookup)
    monkeypatch.setattr(image_hash_mod, "store_hash", fake_store)

    sku = "tgwE"
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path, sku)
    _worker(_cfg(tmp_path)).handle({"payload_json": {"sku": sku}})

    assert "fakehash:no_context" in store
    first_result = store["fakehash:no_context"]

    # Second call, same photo hash, but with a hint present now.
    monkeypatch.setattr(
        ai_identify_mod, "call_model", lambda *a, **k: '{"title": "Different Title", "category": "Other"}'
    )
    doc = json.loads((tmp_path / sku / f"{sku}.json").read_text())
    doc["ai_identified"] = False
    doc["ai_hint"] = "a real hint now present"
    (tmp_path / sku / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")

    calls_before = dict(store)
    _worker(_cfg(tmp_path)).handle({"payload_json": {"sku": sku}})

    new_key = f"fakehash:{_context_sig('a real hint now present')}"
    assert new_key in store
    assert new_key != "fakehash:no_context"
    assert store[new_key] != first_result
    # the stale no_context entry is untouched, not overwritten or reused
    assert store["fakehash:no_context"] == calls_before["fakehash:no_context"]
