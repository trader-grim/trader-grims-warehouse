from __future__ import annotations

import json
from typing import Any, Dict

import tgw.apis.ebay.taxonomy as taxonomy_mod
import tgw.apis.lookup as lookup_mod
import tgw.image_hash as image_hash_mod
import tgw.workers.ai_identify as ai_identify_mod
from tgw.apis.lookup.base import LookupResult
from tgw.workers.ai_identify import AIIdentifyWorker


def _worker(cfg: Dict[str, Any]) -> AIIdentifyWorker:
    worker = AIIdentifyWorker.__new__(AIIdentifyWorker)
    worker.config = cfg
    return worker


def test_open_library_prompt_requests_book_aspects_without_category_group():
    item = {
        "product_lookup": {
            "source": "open_library",
            "title": "What Neat Feet!",
            "brand": "Hana Machotka",
            "isbn": "0688094740",
        }
    }

    prompt = ai_identify_mod._prompt_for_item(
        item,
        {},
        hint="",
        product_context="Hana Machotka, What Neat Feet!",
    )

    assert "Source-specific target aspects" in prompt
    for aspect in ("Author", "Book Title", "Format", "Language", "ISBN"):
        assert aspect in prompt


def test_open_library_author_title_and_isbn_project_into_inventory_aspects(
    tmp_path, monkeypatch,
):
    sku = "tgw-book-aspects"
    sku_dir = tmp_path / sku
    sku_dir.mkdir()
    (sku_dir / f"{sku}.json").write_text(
        json.dumps({"sku": sku, "title": "Old", "ai_identified": False}),
        encoding="utf-8",
    )
    photo = sku_dir / "photo.jpg"
    photo.write_bytes(b"fake-jpeg")

    lookup = LookupResult(
        source="open_library",
        fetched_at="2026-08-15T00:00:00+00:00",
        title="What Neat Feet!",
        brand="Hana Machotka",
        isbn="0688094740",
    )
    monkeypatch.setattr(lookup_mod, "lookup_product", lambda item, cfg: lookup)
    monkeypatch.setattr(
        ai_identify_mod, "_asset_ordered_photos", lambda item, path: [photo]
    )
    monkeypatch.setattr(
        ai_identify_mod, "get_task_model", lambda cfg, task: ("openrouter", "vision")
    )
    monkeypatch.setattr(
        ai_identify_mod,
        "call_model",
        lambda *a, **k: json.dumps({
            "title": "What Neat Feet! by Hana Machotka",
            "category": "Books",
            "condition": "Good",
            "item_specifics": {"Format": "Hardcover", "Language": "English"},
        }),
    )
    monkeypatch.setattr(ai_identify_mod, "extract_json", json.loads)
    monkeypatch.setattr(
        taxonomy_mod, "best_category", lambda cfg, title, category: ("261186", "Books")
    )
    monkeypatch.setattr(image_hash_mod, "compute_dhash", lambda path: "hash")
    monkeypatch.setattr(image_hash_mod, "lookup_hash", lambda value, task: None)
    monkeypatch.setattr(image_hash_mod, "store_hash", lambda *a, **k: None)
    monkeypatch.setattr(
        ai_identify_mod, "_encode_resized", lambda path, max_px=512: ("b64", 1, 1)
    )
    patched: Dict[str, Any] = {}
    monkeypatch.setattr(
        ai_identify_mod,
        "fence_patch_item",
        lambda cfg, item_sku, fields: patched.update(fields) or {"ok": True},
    )

    _worker({"itemdata_root": tmp_path}).handle({"payload_json": {"sku": sku}})

    fields = patched["item_attributes"]["fields"]
    assert fields["Author"] == "Hana Machotka"
    assert fields["Book Title"] == "What Neat Feet!"
    assert fields["ISBN"] == "0688094740"
    assert fields["Format"] == "Hardcover"
    assert fields["Language"] == "English"
