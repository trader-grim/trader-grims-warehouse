"""
tgw.workers.ai_identify — Vision-model item identification worker.

Provider and model are configured in tgw-models.json under the "ai_identify" key.
Defaults: openrouter / google/gemini-2.5-flash-lite (fast, cheap).
Ollama fallback: qwen2.5vl:7b (CPU-only, slow).

Results are written only if the item still has an empty ai_identified flag — safe
to re-run; will not overwrite unless ai_reidentify is set.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors

import tgw.logging as tgw_logging
from tgw import quota
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.apis.llm import CLOUD_PROVIDERS, call_model, get_task_model
from tgw.apis.ollama import extract_json, is_available
from tgw.assets import ordered_photos as _asset_ordered_photos
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.config import sku_dir as _cfg_sku_dir
from tgw.items import append_history_event
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = "ai_identify"
_OLLAMA_FALLBACK_MODEL = "qwen2.5vl:7b"

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
_VISION_MAX_PX = 512  # Ollama CPU path — keep small
_VISION_MAX_PX_CLOUD = 1024  # OpenRouter/Gemini — higher res, no cold-start cost
_MAX_PHOTOS_CLOUD = 6  # max images per call for cloud providers

_SYSTEM_PROMPT = """\
You are an inventory cataloguing assistant. You will be shown a photo of an item.
Extract every field you can observe or reasonably infer. Use null for fields you cannot determine.
Respond with valid JSON only — no prose, no markdown fences.
"""

_ITEM_FIELDS_SCHEMA = """\
{
  "title": "concise descriptive title under 80 chars, include brand and model if visible",
  "category": "plain English category name (e.g. Board Games, Action Figures, Vintage Electronics)",
  "description": "2-4 sentences describing what the item is, what is visible, notable features",
  "condition": "one of: New, Like New, Very Good, Good, Acceptable",
  "brand": "brand or manufacturer name if visible on item or packaging, else null",
  "model": "specific model name or number if visible, else null",
  "manufacturer": "full manufacturer name if different from brand or more specific, else null",
  "mpn": "model/part number printed on item if visible, else null",
  "color": "primary color or color description, else null",
  "material": "primary material (e.g. plastic, metal, fabric, ceramic, paper), else null",
  "country_of_manufacture": "country if visible on item or packaging, else null",
  "upc": "barcode number if clearly legible, else null",
  "item_specifics": "object of any other notable key-value attributes visible on the item (e.g. {\"Size\": \"Large\", \"Style\": \"Vintage\"}), or empty object"
}"""

_USER_PROMPT = f"""\
Look at this item photo and extract as much information as you can.

Respond with JSON matching this schema (null for any field you cannot determine):
{_ITEM_FIELDS_SCHEMA}
"""

_USER_PROMPT_HINTED = (
    """\
Look at this item photo. I already know this item is: {hint}

Using that context together with the photo, extract as much information as you can.

Respond with JSON matching this schema (null for any field you cannot determine):
"""
    + _ITEM_FIELDS_SCHEMA
    + """
"""
)

_USER_PROMPT_ENRICHED = (
    """\
Look at this item photo. Barcode lookup identified this product:
{product_context}

Using that product data together with the photo, extract as much information as you can.
Confirm or refine any fields where the photo gives better information than the lookup data.

Respond with JSON matching this schema (null for any field you cannot determine):
"""
    + _ITEM_FIELDS_SCHEMA
    + """
"""
)


def _encode_resized(img_path: Path, max_px: int = _VISION_MAX_PX) -> tuple[str, int, int]:
    """Return (base64_str, orig_kb, resized_kb) with image resized to max_px longest edge."""
    try:
        from PIL import Image
    except ImportError:
        # Pillow not installed — send raw (slow but functional)
        data = img_path.read_bytes()
        return base64.b64encode(data).decode(), len(data) // 1024, len(data) // 1024

    orig_kb = img_path.stat().st_size // 1024
    with Image.open(img_path) as img:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
    data = buf.getvalue()
    return base64.b64encode(data).decode(), orig_kb, len(data) // 1024


class AIIdentifyWorker(QueueWorker):
    def run(self) -> None:
        provider, model = get_task_model(self.config, "ai_identify")
        if provider == "ollama":
            self._warmup(model)
        super().run()

    def _warmup(self, model: str) -> None:
        """Pre-load the Ollama vision model into memory at startup."""
        import requests as _req

        log.info("warming up %s (cold load may take several minutes)...", model)
        tgw_logging.log_event("ai_identify_warmup_start", model=model)
        try:
            _req.post("http://localhost:11434/api/generate", json={"model": model, "prompt": "", "stream": False}, timeout=1200)
            log.info("%s warm-up complete", model)
            tgw_logging.log_event("ai_identify_warmup_complete", model=model)
        except Exception as exc:
            log.warning("warm-up failed (will retry on first job): %s", exc)

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get("payload_json") or {}
        sku = payload.get("sku", "")
        if not sku:
            raise HardFailure("ai_identify job missing sku in payload")

        sku_dir = _cfg_sku_dir(self.config, sku)
        json_path = sku_dir / f"{sku}.json"

        if not json_path.exists():
            raise HardFailure(f"item JSON not found for {sku}")

        item = json.loads(json_path.read_text(encoding="utf-8"))

        already_identified = bool(item.get("ai_identified"))
        force_reidentify = bool(item.get("ai_reidentify"))

        # Skip only when already identified and not explicitly asked to redo
        if already_identified and not force_reidentify:
            log.info("skipping ai_identify for %s — already identified", sku)
            tgw_logging.log_event("ai_identify_skipped", sku=sku, reason="already_identified")
            return

        # Product lookup — run before Ollama; result cached in item JSON
        product_context = ""
        try:
            from tgw.apis.lookup import lookup_product

            lookup_result = lookup_product(item, self.config)
            if lookup_result:
                item["product_lookup"] = lookup_result.to_dict()
                product_context = lookup_result.prompt_context()
                log.info("ai_identify: product lookup hit for %s via %s — %r", sku, lookup_result.source, product_context[:60])
                tgw_logging.log_event("ai_identify_lookup_hit", sku=sku, source=lookup_result.source, title=lookup_result.title[:60])
        except Exception as exc:
            log.warning("ai_identify: product lookup failed for %s: %s", sku, exc)

        # Derive hint: explicit ai_hint wins; product lookup context; human-set title
        existing_title = str(item.get("title", "")).strip()
        hint = (item.get("ai_hint") or "").strip()
        if not hint and existing_title and existing_title != sku:
            hint = existing_title

        provider, model = get_task_model(self.config, "ai_identify")

        if product_context:
            # Use replace() not format() — the schema contains literal {} JSON braces
            prompt = _USER_PROMPT_ENRICHED.replace('{product_context}', product_context)
        elif hint:
            prompt = _USER_PROMPT_HINTED.replace('{hint}', hint)
        else:
            prompt = _USER_PROMPT

        # Select photos — cloud providers get multiple images; Ollama gets one
        all_photos = _asset_ordered_photos(item, sku_dir)
        if not all_photos:
            raise HardFailure(f"no images found for {sku}")

        if provider in CLOUD_PROVIDERS:
            # Skip -alt. duplicates and cropped- derivatives for the batch;
            # they add tokens without new information. Photo selection UI is PP-TODO.
            candidate_photos = [p for p in all_photos if "-alt." not in p.name and not p.name.startswith("cropped-")][:_MAX_PHOTOS_CLOUD] or all_photos[:1]
        else:
            candidate_photos = all_photos[:1]

        img_path = candidate_photos[0]  # primary — used for cache key + logging

        # pHash cache check on the primary photo only
        from tgw.image_hash import compute_dhash, lookup_hash, store_hash

        img_hash = compute_dhash(img_path)
        # Only use cache for single-photo calls — multi-photo gives richer results
        use_cache = len(candidate_photos) == 1
        cached_result = lookup_hash(img_hash, "ai_identify") if (img_hash and use_cache) else None

        raw: Optional[str] = None
        if cached_result is not None:
            log.info("ai_identify: cache hit for %s (phash %s)", sku, img_hash)
            tgw_logging.log_event("ai_identify_cache_hit", sku=sku, phash=img_hash)
            result = cached_result
        else:
            if provider == "ollama" and not is_available(model):
                raise RuntimeError(f"Ollama unavailable or model {model!r} not found")

            max_px = _VISION_MAX_PX_CLOUD if provider in CLOUD_PROVIDERS else _VISION_MAX_PX
            encoded = [_encode_resized(p, max_px=max_px) for p in candidate_photos]
            img_b64_list = [e[0] for e in encoded]
            total_kb = sum(e[2] for e in encoded)

            photo_names = [p.name for p in candidate_photos]
            log.info("calling %s/%s for %s (%d photos: %s, %dKB total%s)", provider, model, sku, len(candidate_photos), ", ".join(photo_names), total_kb, f", hint={hint!r}" if hint else "")
            tgw_logging.log_event("ai_identify_call", sku=sku, provider=provider, model=model, photos=photo_names, photo_count=len(candidate_photos), total_kb=total_kb, hint=hint or None)

            raw = call_model("ai_identify", _SYSTEM_PROMPT, prompt, self.config, img_b64_list=img_b64_list, sku=sku)

            try:
                result = extract_json(raw)
            except Exception as exc:
                # audit#1143 #1269: 200 chars was too short to tell whether a
                # failure was genuinely malformed or just missing its closing
                # ```fence beyond the cutoff -- same fix as ebay_draft.py's
                # #1249 code-review follow-up.
                raise HardFailure(f"ai_identify: model returned non-JSON for {sku}: {raw[:2000]}") from exc

            if img_hash and use_cache:
                store_hash(img_hash, sku, "ai_identify", result)

        def _str(key: str) -> str:
            v = result.get(key)
            return str(v).strip() if v and str(v).strip().lower() not in ("null", "none", "") else ""

        title = _str("title")
        category = _str("category")
        description = _str("description")
        condition = _str("condition")

        # Extended inventory fields — fill canonical record, never discard
        brand = _str("brand")
        item_model = _str("model")
        manufacturer = _str("manufacturer")
        mpn = _str("mpn")
        color = _str("color")
        material = _str("material")
        country_of_manufacture = _str("country_of_manufacture")
        upc = _str("upc")
        ai_item_specifics = result.get("item_specifics") or {}
        if not isinstance(ai_item_specifics, dict):
            ai_item_specifics = {}

        if not title:
            raise HardFailure(f"ai_identify: empty title in model response for {sku}")

        # Resolve AI category string → eBay categoryId
        ebay_category_id = ebay_category_name = None
        try:
            from tgw.apis.ebay.taxonomy import best_category

            ebay_category_id, ebay_category_name = best_category(self.config, title, category)
        except quota.QuotaBudgetExceeded:
            # code-review follow-up (#1181): best_category() deliberately
            # re-raises this so the job requeues transiently (worker_base's
            # 'quota budget exhausted' classifier) instead of silently
            # writing the item with no category — must not catch it here.
            raise
        except Exception as exc:
            log.warning("taxonomy lookup failed for %r: %s", category, exc)

        # Write results — always overwrite core fields; fill gaps for extended fields
        item["title"] = title
        item["category"] = category
        item["description"] = description
        item["condition"] = condition
        if ebay_category_id:
            item["ebay_category_id"] = ebay_category_id
            item["ebay_category_name"] = ebay_category_name
        item["ai_identified"] = True
        item.pop("ai_reidentify", None)  # clear the force flag

        # Extended canonical inventory fields — fill any empty slot; never wipe existing
        # operator-set values (a re-identify should not clobber what a human corrected)
        _is_reidentify = bool(item.get("ai_identified"))  # True means this is a re-scan
        for _field, _val in [
            ("brand", brand),
            ("model", item_model),
            ("manufacturer", manufacturer),
            ("model_number", mpn),
            ("color", color),
            ("material", material),
            ("country_of_manufacture", country_of_manufacture),
            ("upc", upc),
        ]:
            if _val and not item.get(_field):
                item[_field] = _val

        # Merge AI-extracted item specifics into item_attributes (never overwrite existing keys)
        if ai_item_specifics:
            existing_attrs = item.get("item_attributes") or {}
            merged_attrs = {**ai_item_specifics, **existing_attrs}  # existing wins
            item["item_attributes"] = merged_attrs

        # product_lookup already written above if lookup succeeded

        # Record identification round in history trail
        prior_rounds = sum(1 for e in item.get("identification_history", []) if e.get("event") == "ai_identify")
        if product_context:
            prompt_type = "enriched"
        elif hint:
            prompt_type = "hinted"
        else:
            prompt_type = "plain"
        append_history_event(
            item,
            {
                "event": "ai_identify",
                "round": prior_rounds + 1,
                "model": f"{provider}/{model}",
                "prompt_type": prompt_type,
                "hint": hint or None,
                "lookup_source": item.get("product_lookup", {}).get("source") if product_context else None,
                "title": title,
                "category": category,
                "condition": condition,
                "ebay_category_id": ebay_category_id,
            },
        )

        # Append full scan record to vision_results[] — raw response is the permanent asset.
        # Re-scans append; they never replace prior entries. Derivation reads this list.
        scanned_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        vision_record: Dict[str, Any] = {
            "photo": img_path.name,
            "photos": [p.name for p in candidate_photos],
            "photo_count": len(candidate_photos),
            "photo_hash": img_hash or "",
            "model": f"{provider}/{model}",
            "prompt_type": prompt_type,
            "prompt_context": product_context or hint or "",
            "scanned_at": scanned_at,
            "extracted": {
                "title": title,
                "category": category,
                "description": description,
                "condition": condition,
                "brand": brand or None,
                "model": item_model or None,
                "manufacturer": manufacturer or None,
                "mpn": mpn or None,
                "color": color or None,
                "material": material or None,
                "country_of_manufacture": country_of_manufacture or None,
                "upc": upc or None,
                "item_specifics": ai_item_specifics or None,
            },
        }
        if raw is not None:
            vision_record["raw_response"] = raw
        vision_results: List[Dict[str, Any]] = item.get("vision_results") or []
        vision_results.append(vision_record)
        item["vision_results"] = vision_results

        # Push all changes through fence (single atomic write)
        fence_fields = {
            "title": item["title"],
            "category": item["category"],
            "description": item["description"],
            "condition": item["condition"],
            "ai_identified": item["ai_identified"],
            "vision_results": item["vision_results"],
            "identification_history": item.get("identification_history", []),
        }
        if force_reidentify:
            # audit#1143 #1167: item.pop("ai_reidentify", None) above only
            # clears the in-memory copy — fence_fields is a curated
            # allow-list and never included this key, so the persisted flag
            # never actually cleared. Every subsequent run for this SKU saw
            # ai_reidentify still true on disk and re-triggered a billed
            # vision-AI call forever. _apply_patch() deletes a field from the
            # document when its patched value is None (http_server.py's
            # _apply_patch docstring) — that's the mechanism to use here.
            fence_fields["ai_reidentify"] = None
        if "ebay_category_id" in item:
            fence_fields["ebay_category_id"] = item["ebay_category_id"]
            fence_fields["ebay_category_name"] = item.get("ebay_category_name")
        if "product_lookup" in item:
            fence_fields["product_lookup"] = item["product_lookup"]
        if "free_shipping" in item:
            fence_fields["free_shipping"] = item["free_shipping"]
        # Extended fields — only write non-empty values
        for _f in ("brand", "model", "manufacturer", "model_number", "color", "material", "country_of_manufacture", "upc"):
            if item.get(_f):
                fence_fields[_f] = item[_f]
        if item.get("item_attributes"):
            fence_fields["item_attributes"] = item["item_attributes"]
        fence_patch_item(self.config, sku, fence_fields)

        log.info("ai_identify complete for %s: %r (eBay cat %s)", sku, title, ebay_category_id)
        tgw_logging.log_event(
            "ai_identify_complete", sku=sku, title=title, category=category, condition=condition, hint=hint or None, ebay_category_id=ebay_category_id, ebay_category_name=ebay_category_name
        )

        # Enqueue ebay_draft and alt_text unless this was a catalog-only run
        catalog_only = bool(payload.get("catalog_only"))
        if not catalog_only:
            try:
                state_machine.enqueue_job(
                    queue_name="ebay_draft",
                    payload={"sku": sku},
                    dedupe_key=f"ebay_draft:{sku}",
                    max_attempts=3,
                )
            except psycopg2.errors.UniqueViolation:
                pass
            try:
                state_machine.enqueue_job(
                    queue_name="alt_text",
                    payload={"sku": sku},
                    dedupe_key=f"alt_text:{sku}",
                    max_attempts=3,
                )
            except psycopg2.errors.UniqueViolation:
                pass

        # Enqueue downstream rebuild
        try:
            state_machine.enqueue_job(
                queue_name="catalog_rebuild",
                payload={"reason": f"ai_identify:{sku}"},
                dedupe_key="catalog_rebuild:pending",
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tgw-ai-identify-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = AIIdentifyWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
