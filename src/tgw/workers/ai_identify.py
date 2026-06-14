"""
tgw.workers.ai_identify — Vision-model item identification worker.

Provider and model are configured in tgw-models.json under the "ai_identify" key.
Defaults: openrouter / google/gemini-2.5-flash (fast, cheap).
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
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.apis.llm import call_model, get_task_model
from tgw.apis.ollama import extract_json, is_available
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import append_history_event, atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = "ai_identify"
_OLLAMA_FALLBACK_MODEL = "qwen2.5vl:7b"

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"}
_VISION_MAX_PX = 512  # 56KB resized; model loads in ~10 min cold, ~18s warm

_SYSTEM_PROMPT = """\
You are an eBay listing assistant. You will be shown a photo of an item for sale.
Respond with valid JSON only — no prose, no markdown fences.
"""

_USER_PROMPT = """\
Look at this item photo and provide:
- A concise, descriptive eBay-style title (under 80 characters)
- The most likely eBay category name (plain English, e.g. "Board Games", "Action Figures")
- A 1-2 sentence description of what the item appears to be
- Your best guess at condition: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}
"""

_USER_PROMPT_HINTED = """\
Look at this item photo. I already know this item is: {hint}

Using that context together with the photo, provide:
- A concise, descriptive eBay-style title (under 80 characters) that builds on what I told you
- The most likely eBay category name (plain English, e.g. "Thimbles", "Miniature Bottles")
- A 1-2 sentence description covering what is visible (quantity, materials, notable markings)
- Your best guess at condition: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}}
"""

_USER_PROMPT_ENRICHED = """\
Look at this item photo. Barcode lookup identified this product: {product_context}

Using that product data together with the photo:
- Confirm or refine the title to be eBay-ready (under 80 characters, include brand/model)
- The most likely eBay category name (plain English)
- A 1-2 sentence description focusing on condition and any notable visible details
- Condition based on what you see: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}}
"""


def _primary_image(sku_dir: Path) -> Optional[Path]:
    candidates = sorted(p for p in sku_dir.iterdir() if p.is_file() and p.suffix in _IMAGE_SUFFIXES)
    return candidates[0] if candidates else None


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
        provider, model = get_task_model(self.config, 'ai_identify')
        if provider == 'ollama':
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

        sku_dir = self.config["itemdata_root"] / sku
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

        img_path = _primary_image(sku_dir)
        if img_path is None:
            raise HardFailure(f"no images found for {sku}")

        provider, model = get_task_model(self.config, 'ai_identify')

        if product_context:
            prompt = _USER_PROMPT_ENRICHED.format(product_context=product_context)
        elif hint:
            prompt = _USER_PROMPT_HINTED.format(hint=hint)
        else:
            prompt = _USER_PROMPT

        # pHash cache check — skip API call if we've processed this image before
        from tgw.image_hash import compute_dhash, lookup_hash, store_hash

        img_hash = compute_dhash(img_path)
        cached_result = lookup_hash(img_hash, "ai_identify") if img_hash else None

        if cached_result is not None:
            log.info("ai_identify: cache hit for %s (phash %s)", sku, img_hash)
            tgw_logging.log_event("ai_identify_cache_hit", sku=sku, phash=img_hash)
            result = cached_result
        else:
            # Fail-fast for Ollama before the slow encoding step
            if provider == 'ollama' and not is_available(model):
                raise RuntimeError(f"Ollama unavailable or model {model!r} not found")

            # Use higher resolution for cloud providers; keep low for CPU-bound Ollama
            max_px = 768 if provider == 'openrouter' else _VISION_MAX_PX
            img_b64, orig_kb, resized_kb = _encode_resized(img_path, max_px=max_px)

            log.info("calling %s/%s for %s (image: %s, %dKB→%dKB%s)", provider, model, sku, img_path.name, orig_kb, resized_kb, f", hint={hint!r}" if hint else "")
            tgw_logging.log_event("ai_identify_call", sku=sku, provider=provider, model=model, image=img_path.name, orig_kb=orig_kb, resized_kb=resized_kb, hint=hint or None)

            raw = call_model('ai_identify', _SYSTEM_PROMPT, prompt, self.config, img_b64=img_b64)

            try:
                result = extract_json(raw)
            except Exception as exc:
                raise HardFailure(f"ai_identify: model returned non-JSON for {sku}: {raw[:200]}") from exc

            if img_hash:
                store_hash(img_hash, sku, "ai_identify", result)

        title = str(result.get("title", "")).strip()
        category = str(result.get("category", "")).strip()
        description = str(result.get("description", "")).strip()
        condition = str(result.get("condition", "")).strip()

        if not title:
            raise HardFailure(f"ai_identify: empty title in model response for {sku}")

        # Resolve AI category string → eBay categoryId
        ebay_category_id = ebay_category_name = None
        try:
            from tgw.apis.ebay.taxonomy import best_category

            ebay_category_id, ebay_category_name = best_category(self.config, title, category)
        except Exception as exc:
            log.warning("taxonomy lookup failed for %r: %s", category, exc)

        # Write results — always overwrite on re-identify, fill gaps on first pass
        item["title"] = title
        item["category"] = category
        item["description"] = description
        item["condition"] = condition
        if ebay_category_id:
            item["ebay_category_id"] = ebay_category_id
            item["ebay_category_name"] = ebay_category_name
        item["ai_identified"] = True
        item.pop("ai_reidentify", None)  # clear the force flag
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

        atomic_write_json(json_path, item, pretty=self.config.get("pretty", True))

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
