"""audit#1143 #1269 (code-review follow-up on #1249): ai_identify.py had the
same raw[:200]-before-storage truncation bug already fixed in ebay_draft.py
-- the raw model response was cut to 200 chars before ever being examined,
so a "model returned non-JSON" failure was undiagnosable after the fact.
Raised to 2000 chars, matching the ebay_draft.py fix.

All external calls (LLM, image hashing) are mocked -- tests pass completely
offline with no billed API calls.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import tgw.apis.ebay.taxonomy as taxonomy_mod
import tgw.apis.lookup as lookup_mod
import tgw.image_hash as image_hash_mod
import tgw.workers.ai_identify as ai_identify_mod
from tgw.queue.worker_base import HardFailure


def _item(sku: str) -> Dict[str, Any]:
    return {"sku": sku, "title": "Old Title"}


def _worker(cfg: Dict[str, Any]) -> ai_identify_mod.AIIdentifyWorker:
    w = ai_identify_mod.AIIdentifyWorker.__new__(ai_identify_mod.AIIdentifyWorker)
    w.config = cfg
    return w


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def _mock_common(monkeypatch, tmp_path, sku, raw_response):
    fake_photo = tmp_path / sku / "photo.jpg"
    fake_photo.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-bytes")

    monkeypatch.setattr(ai_identify_mod, "_asset_ordered_photos", lambda item, sku_dir: [fake_photo])
    monkeypatch.setattr(ai_identify_mod, "get_task_model", lambda cfg, task: ("openrouter", "google/gemini-2.5-flash-lite"))
    monkeypatch.setattr(ai_identify_mod, "_encode_resized", lambda p, max_px=512: ("base64data", 10, 5))
    monkeypatch.setattr(ai_identify_mod, "call_model", lambda *a, **k: raw_response)
    monkeypatch.setattr(lookup_mod, "lookup_product", lambda item, cfg: None)
    monkeypatch.setattr(taxonomy_mod, "best_category", lambda cfg, title, category: (None, None))
    monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda p: "fakehash")
    monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda h, task: None)
    monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **k: None)


def test_nonjson_error_message_carries_2000_chars_not_200(monkeypatch, tmp_path):
    sku = "tgw20260101120000020"
    _write_item(tmp_path, sku, _item(sku))
    cfg = {"itemdata_root": tmp_path}

    raw_response = "not json at all — " + ("x" * 1900)
    _mock_common(monkeypatch, tmp_path, sku, raw_response)

    w = _worker(cfg)
    try:
        w.handle({"payload_json": {"sku": sku}})
        assert False, "expected HardFailure"
    except HardFailure as exc:
        msg = str(exc)
        assert len(msg) > 500, f"error message unexpectedly short: {len(msg)} chars"
        assert "x" * 500 in msg


def test_nonjson_error_message_capped_at_2000_chars_of_raw(monkeypatch, tmp_path):
    sku = "tgw20260101120000021"
    _write_item(tmp_path, sku, _item(sku))
    cfg = {"itemdata_root": tmp_path}

    raw_response = "y" * 5000
    _mock_common(monkeypatch, tmp_path, sku, raw_response)

    w = _worker(cfg)
    try:
        w.handle({"payload_json": {"sku": sku}})
        assert False, "expected HardFailure"
    except HardFailure as exc:
        msg = str(exc)
        assert "y" * 2001 not in msg
        assert "y" * 2000 in msg
