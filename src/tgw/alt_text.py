"""
tgw.alt_text — generate alt_text + seo_caption via vision model.

Provider and model are configured in tgw-models.json under the "alt_text" key.
Defaults: openrouter / google/gemini-2.5-flash.

Workflow:
  1. Find primary image in ItemData/<sku>/
  2. Call vision model → parse {alt_text, seo_caption}
  3. Archive original image to data/history/ItemData/<sku>/ if not already there
  4. Rename production image to <sku>-alt.jpg
  5. Write alt_text + seo_caption to item['draft_listing']
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.apis.llm import call_model, get_task_model
from tgw.apis.ollama import extract_json, is_available

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_VISION_MAX_PX = 512  # Ollama (memory-constrained CPU)
_OR_MAX_PX = 768      # OpenRouter (quality matters more)
_ALT_STEM_SUFFIX = "-alt"  # final image name: <sku>-alt.jpg
_OPENROUTER_MIN_INTERVAL_S = 3.0  # 60s / 20 req = 3s; stays under free-tier ceiling

_SYSTEM_PROMPT = "You are an expert in web accessibility and SEO. Respond with valid JSON only — no markdown fences, no commentary."
_USER_PROMPT = (
    "Describe this product photo for web accessibility and SEO. "
    "Return JSON with exactly two string fields:\n"
    '  "alt_text": concise description of the main subject (max 150 chars; '
    'do NOT start with "image of" or "picture of"),\n'
    '  "seo_caption": 1-2 sentences including brand, model, and key features.\n'
    "JSON only."
)


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _primary_image(sku_dir: Path) -> Optional[Path]:
    """Return the first non-alt image in the SKU directory, sorted by name."""
    candidates = sorted(
        p for p in sku_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES and not p.stem.endswith(_ALT_STEM_SUFFIX)
    )
    return candidates[0] if candidates else None


def _encode_resized(img_path: Path, max_px: int = _VISION_MAX_PX) -> str:
    """Return base64 JPEG, resized to max_px on the longest edge."""
    try:
        from PIL import Image
    except ImportError:
        data = img_path.read_bytes()
        return base64.b64encode(data).decode()

    with Image.open(img_path) as img:
        img.thumbnail((max_px, max_px), Image.LANCZOS)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def _history_sku_dir(cfg: Dict[str, Any], sku: str) -> Path:
    """Return /opt/TGW/data/history/ItemData/<sku>/ derived from itemdata_root."""
    itemdata_root = Path(cfg["itemdata_root"])
    return itemdata_root.parent / "history" / "ItemData" / sku


# ---------------------------------------------------------------------------
# Main command
# ---------------------------------------------------------------------------


def cmd_alt_text(
    cfg: Dict[str, Any],
    sku: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Generate alt_text + seo_caption for one item and rename its primary image."""
    from .items import atomic_write_json

    # Resolve provider/model (CLI overrides → models config → _DEFAULTS)
    resolved_provider, resolved_model = get_task_model(cfg, 'alt_text')
    provider = provider or resolved_provider
    model = model or resolved_model

    itemdata_root = Path(cfg["itemdata_root"])
    sku_dir = itemdata_root / sku
    json_path = sku_dir / f"{sku}.json"

    if not json_path.exists():
        return {"ok": False, "error": f"item JSON not found: {json_path}"}

    item = json.loads(json_path.read_text(encoding="utf-8"))

    # Idempotency: skip if already fully processed
    alt_path = sku_dir / f"{sku}{_ALT_STEM_SUFFIX}.jpg"
    if alt_path.exists() and item.get("draft_listing", {}).get("alt_text"):
        return {
            "ok": True,
            "sku": sku,
            "skipped": True,
            "reason": "alt_text already set and alt image already exists",
        }

    img_path = _primary_image(sku_dir)
    if img_path is None:
        return {"ok": False, "error": f"no primary image found in {sku_dir}"}

    if dry_run:
        history_sku_dir = _history_sku_dir(cfg, sku)
        history_path = history_sku_dir / img_path.name
        return {
            "ok": True,
            "dry_run": True,
            "sku": sku,
            "provider": provider,
            "model": model,
            "image": img_path.name,
            "alt_path_would_be": str(alt_path),
            "archive_needed": not history_path.exists(),
            "history_path": str(history_path),
        }

    # Fail-fast: check Ollama availability before expensive encoding
    if provider != "openrouter" and not is_available(model):
        return {"ok": False, "error": f"Ollama unavailable or model {model!r} not found"}

    max_px = _OR_MAX_PX if provider == "openrouter" else _VISION_MAX_PX
    img_b64 = _encode_resized(img_path, max_px=max_px)

    try:
        raw = call_model('alt_text', _SYSTEM_PROMPT, _USER_PROMPT, cfg,
                         img_b64=img_b64, provider=provider, model=model)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    try:
        result = extract_json(raw)
    except Exception:
        return {"ok": False, "error": f"model returned non-JSON: {raw[:200]}"}

    alt_text = str(result.get("alt_text", "")).strip()[:150]
    seo_caption = str(result.get("seo_caption", "")).strip()

    if not alt_text:
        return {"ok": False, "error": "model returned empty alt_text"}

    # Archive original to history before renaming production copy
    history_sku_dir = _history_sku_dir(cfg, sku)
    history_path = history_sku_dir / img_path.name
    if not history_path.exists():
        history_sku_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img_path, history_path)
        archived = True
    else:
        archived = False

    # Rename production image to <sku>-alt.jpg
    img_path.rename(alt_path)

    # Write fields to draft_listing
    if "draft_listing" not in item:
        item["draft_listing"] = {}
    item["draft_listing"]["alt_text"] = alt_text
    item["draft_listing"]["seo_caption"] = seo_caption

    atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))

    return {
        "ok": True,
        "sku": sku,
        "provider": provider,
        "model": model,
        "alt_text": alt_text,
        "seo_caption": seo_caption,
        "image_renamed": f"{img_path.name} → {alt_path.name}",
        "archived_to_history": archived,
        "history_path": str(history_path),
    }


# ---------------------------------------------------------------------------
# Batch command (direct execution with rate-limiting; not queue-based)
# ---------------------------------------------------------------------------


def cmd_alt_text_batch(
    cfg: Dict[str, Any],
    *,
    limit: int = 0,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Run alt-text generation directly on all eligible items.

    Eligible = has a primary image, no existing alt_text in draft_listing, no
    <sku>-alt.jpg yet.  For OpenRouter provider, enforces ~20 req/min rate
    limit.  Fail-soft: per-item errors are collected, not raised.  Resumable:
    idempotency check inside cmd_alt_text skips already-done items.
    """
    itemdata_root = Path(cfg["itemdata_root"])

    eligible_skus: List[str] = []
    for sku_dir in sorted(itemdata_root.iterdir()):
        if not sku_dir.is_dir():
            continue
        sku = sku_dir.name
        json_path = sku_dir / f"{sku}.json"
        if not json_path.exists():
            continue

        try:
            item = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        if item.get("draft_listing", {}).get("alt_text"):
            continue

        alt_path = sku_dir / f"{sku}{_ALT_STEM_SUFFIX}.jpg"
        if alt_path.exists():
            continue

        if _primary_image(sku_dir) is None:
            continue

        eligible_skus.append(sku)
        if limit and len(eligible_skus) >= limit:
            break

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "eligible": len(eligible_skus),
            "skus_preview": eligible_skus[:20],
            "note": "run without --dry-run to process",
        }

    resolved_provider, resolved_model = get_task_model(cfg, "alt_text")
    effective_provider = provider or resolved_provider
    effective_model = model or resolved_model

    processed = 0
    skipped_idempotent = 0
    error_details: List[Dict[str, Any]] = []
    t_last_call: Optional[float] = None

    for sku in eligible_skus:
        if effective_provider == "openrouter" and t_last_call is not None:
            elapsed = time.time() - t_last_call
            if elapsed < _OPENROUTER_MIN_INTERVAL_S:
                time.sleep(_OPENROUTER_MIN_INTERVAL_S - elapsed)

        t_last_call = time.time()
        try:
            r = cmd_alt_text(cfg, sku=sku, provider=effective_provider, model=effective_model)
        except Exception as exc:
            error_details.append({"sku": sku, "error": str(exc)})
            continue

        if r.get("skipped"):
            skipped_idempotent += 1
        elif r.get("ok"):
            processed += 1
        else:
            error_details.append({"sku": sku, "error": r.get("error", "unknown")})

    return {
        "ok": True,
        "provider": effective_provider,
        "model": effective_model,
        "eligible": len(eligible_skus),
        "processed": processed,
        "skipped_idempotent": skipped_idempotent,
        "errors": len(error_details),
        "error_details": error_details,
    }
