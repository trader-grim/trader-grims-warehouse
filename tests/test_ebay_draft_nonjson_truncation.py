"""audit#1143 #1249: diagnosing the 2771 dead-lettered ebay_draft jobs found
95 "model returned non-JSON" HardFailures that were undiagnosable after the
fact -- the raw model response was truncated to 200 chars before being
stored in error_detail, so every failure sample looked identically
"cut off mid-JSON" regardless of the real cause. Truncation raised to 2000
chars so future recurrences carry enough context to actually diagnose.

All external calls (LLM, eBay, product lookup) are mocked -- tests pass
completely offline.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import tgw.workers.ebay_draft as ebay_draft_mod
from tgw.queue.worker_base import HardFailure


def _item(sku: str) -> Dict[str, Any]:
    return {
        "sku": sku,
        "title": "Real Title",
        "ebay_category_id": "12345",
        "ebay_category_name": "Widgets",
    }


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def _worker(cfg) -> ebay_draft_mod.EbayDraftWorker:
    w = ebay_draft_mod.EbayDraftWorker.__new__(ebay_draft_mod.EbayDraftWorker)
    w.config = cfg
    return w


def _mock_common(monkeypatch, tmp_path, sku, raw_response):
    aspects = [{"name": "Color", "mode": "FREE_TEXT", "allowed_values": [], "required": False}]
    monkeypatch.setattr(ebay_draft_mod, "get_aspects", lambda cfg, cat_id: aspects)
    monkeypatch.setattr(ebay_draft_mod, "_fetch_browse_aspect_hints", lambda *a, **k: {})
    monkeypatch.setattr(ebay_draft_mod, "get_task_model",
                        lambda cfg, task: ("openrouter", "google/gemini-2.5-flash-lite"))
    monkeypatch.setattr(ebay_draft_mod, "_aspect_fill_photos",
                        lambda item, sku_dir, provider, **kw: [])
    monkeypatch.setattr(ebay_draft_mod, "_encode_resized", lambda p, max_px=512: ("b64", 1, 1))
    monkeypatch.setattr(ebay_draft_mod, "call_model", lambda *a, **k: raw_response)


def test_nonjson_error_message_carries_2000_chars_not_200(monkeypatch, tmp_path):
    sku = "tgw20260101120000010"
    _write_item(tmp_path, sku, _item(sku))
    cfg = {"itemdata_root": tmp_path}

    # A response that is NOT valid JSON at any truncation length, long enough
    # to prove the message is not still being cut at 200 chars.
    raw_response = "not json at all — " + ("x" * 1900)
    _mock_common(monkeypatch, tmp_path, sku, raw_response)

    w = _worker(cfg)
    try:
        w.handle({"payload_json": {"sku": sku}})
        assert False, "expected HardFailure"
    except HardFailure as exc:
        msg = str(exc)
        assert len(msg) > 500, f"error message unexpectedly short: {len(msg)} chars"
        # the truncated raw text embedded in the message should extend well
        # past where the old 200-char cutoff would have ended
        assert "x" * 500 in msg


def test_nonjson_error_message_capped_at_2000_chars_of_raw(monkeypatch, tmp_path):
    sku = "tgw20260101120000011"
    _write_item(tmp_path, sku, _item(sku))
    cfg = {"itemdata_root": tmp_path}

    raw_response = "y" * 5000  # not valid JSON, well past the 2000-char cap
    _mock_common(monkeypatch, tmp_path, sku, raw_response)

    w = _worker(cfg)
    try:
        w.handle({"payload_json": {"sku": sku}})
        assert False, "expected HardFailure"
    except HardFailure as exc:
        msg = str(exc)
        # raw is capped at 2000 chars, not the full 5000
        assert "y" * 2001 not in msg
        assert "y" * 2000 in msg
