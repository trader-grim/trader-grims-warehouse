"""
tgw.alt_text — generate alt_text + seo_caption via vision model.

Provider and model are configured in tgw-models.json under the "alt_text" key.
Defaults: openrouter / google/gemini-2.5-flash-lite.

Workflow (serial live-API mode):
  1. Find primary image in ItemData/<sku>/
  2. Call vision model → parse {alt_text, seo_caption}
  3. Archive original image to data/history/ItemData/<sku>/ if not already there
  4. Rename production image to <sku>-alt.jpg
  5. Write alt_text + seo_caption to item['draft_listing']

Workflow (Gemini Batch API mode — tgw alt-text --batch --api-mode batch):
  1. Collect all eligible SKUs (no alt_text yet, has primary image)
  2. Skip images already in image_hashes cache
  3. Chunk into BATCH_IMAGES_PER_TASK groups, build Gemini Batch JSONL
  4. Submit to Gemini Batch API, persist state to runtime/state/alt-text-batch-state.json
  5. Poll until COMPLETED, download output JSONL
  6. Parse results, apply via _apply_alt_text_result per SKU
  7. Store hashes; return summary
"""

from __future__ import annotations

import base64
import io
import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.apis.google_genai import (
    BATCH_IMAGES_PER_TASK,
    build_alt_text_task,
    cleanup_input_file,
    download_batch_output,
    parse_batch_results,
    poll_batch,
    submit_batch,
)
from tgw.apis.llm import CLOUD_PROVIDERS, call_model, get_task_model
from tgw.apis.ollama import extract_json, is_available
from tgw.assets import ordered_photos as _asset_ordered_photos
from tgw.assets import primary_photo as _asset_primary_photo

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
_VISION_MAX_PX = 512  # Ollama (memory-constrained CPU)
_OR_MAX_PX = 768      # OpenRouter (quality matters more)
_ALT_STEM_SUFFIX = "-alt"  # final image name: <sku>-alt.jpg
_OPENROUTER_MIN_INTERVAL_S = 3.0  # 60s / 20 req = 3s; stays under free-tier ceiling
# Multi-photo per item (session 41): the batching infra for this already existed
# (build_alt_text_task takes a list of images) but cmd_alt_text only ever sent the
# single primary photo — requested, built, never wired in. Cloud providers can take
# more than one image per call for a richer alt_text/seo_caption; Ollama stays
# single-image (memory-constrained CPU, see feedback-ollama-performance).
_MAX_PHOTOS_CLOUD = 4

_SYSTEM_PROMPT = "You are an expert in web accessibility and SEO. Respond with valid JSON only — no markdown fences, no commentary."
_USER_PROMPT = (
    "Describe this product photo for web accessibility and SEO. "
    "Return JSON with exactly two string fields:\n"
    '  "alt_text": concise description of the main subject (max 150 chars; '
    'do NOT start with "image of" or "picture of"),\n'
    '  "seo_caption": 1-2 sentences including brand, model, and key features.\n'
    "JSON only."
)
_USER_PROMPT_MULTI = (
    "These photos all show the same product from different angles. Describe it for "
    "web accessibility and SEO, using all the photos together to identify details "
    "(brand, model, material, condition) that might not be visible in any single shot. "
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


def repair_renamed_originals(item_data_root: Path) -> list[str]:
    """One-time repair: restore originals that were incorrectly renamed to <sku>-alt.*.

    Walks ItemData/<SKU>/ directories. If the only image present is <sku>-alt.*
    (i.e. the original was renamed and the bare <sku>.* is missing), renames
    <sku>-alt.* back to <sku>.*.

    Returns a list of SKUs that were repaired.
    """
    repaired: list[str] = []
    for sku_dir in sorted(item_data_root.iterdir()):
        if not sku_dir.is_dir():
            continue
        sku = sku_dir.name
        images = [
            p for p in sku_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
        ]
        # Check: no bare <sku>.* exists but a <sku>-alt.* does
        bare = [p for p in images if p.stem == sku]
        alt_companions = [p for p in images if p.stem == f"{sku}{_ALT_STEM_SUFFIX}"]
        if bare:
            continue  # original still present, nothing to repair
        if not alt_companions:
            continue  # no images at all, skip
        # Restore: rename <sku>-alt.<ext> → <sku>.<ext>
        for alt_file in alt_companions:
            restored = sku_dir / f"{sku}{alt_file.suffix}"
            alt_file.rename(restored)
        repaired.append(sku)
    return repaired


def sorted_gallery(sku: str, sku_dir: Path) -> list[Path]:
    """Return image paths for *sku* sorted for gallery display.

    Order:
      1. Files named exactly ``<sku>.<ext>`` (canonical originals), oldest mtime first.
      2. Files named ``<sku>-alt.<ext>`` (companions), oldest mtime first.
      3. All other images, oldest mtime first.
    """
    images = [
        p for p in sku_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    ]

    def _sort_key(p: Path) -> tuple[int, float]:
        if p.stem == sku:
            group = 0
        elif p.stem == f"{sku}{_ALT_STEM_SUFFIX}":
            group = 1
        else:
            group = 2
        return (group, p.stat().st_mtime)

    return sorted(images, key=_sort_key)


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

    # Resolve provider/model (CLI overrides → tgw-models.json — the only source)
    resolved_provider, resolved_model = get_task_model(cfg, 'alt_text')
    provider = provider or resolved_provider
    model = model or resolved_model

    itemdata_root = Path(cfg["itemdata_root"])
    sku_dir = itemdata_root / sku
    json_path = sku_dir / f"{sku}.json"

    if not json_path.exists():
        return {"ok": False, "error": f"item JSON not found: {json_path}"}

    item = json.loads(json_path.read_text(encoding="utf-8"))

    # Idempotency: check for any existing -alt.* companion
    alt_path = next(
        (p for p in sku_dir.iterdir()
         if p.stem == f"{sku}{_ALT_STEM_SUFFIX}" and p.suffix.lower() in _IMAGE_SUFFIXES),
        None,
    )
    if alt_path is not None and item.get("draft_listing", {}).get("alt_text"):
        return {
            "ok": True,
            "sku": sku,
            "skipped": True,
            "reason": "alt_text already set and alt image already exists",
        }

    img_path = _asset_primary_photo(item, sku_dir)
    # Skip the -alt companion if it ended up first (shouldn't happen in practice)
    if img_path is not None and img_path.stem.endswith(_ALT_STEM_SUFFIX):
        img_path = _primary_image(sku_dir)
    if img_path is None:
        return {"ok": False, "error": f"no primary image found in {sku_dir}"}

    # Multi-photo selection for cloud providers (session 41 — see _MAX_PHOTOS_CLOUD).
    # img_path stays the single primary for cache key / rename / archive bookkeeping;
    # candidate_photos is what actually gets sent to the model.
    if provider in CLOUD_PROVIDERS:
        all_photos = _asset_ordered_photos(item, sku_dir)
        candidate_photos = [
            p for p in all_photos
            if "-alt." not in p.name and not p.name.startswith("cropped-")
        ][:_MAX_PHOTOS_CLOUD] or [img_path]
    else:
        candidate_photos = [img_path]

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
            "photos_used": [p.name for p in candidate_photos],
            "alt_path_would_be": str(sku_dir / f"{sku}{_ALT_STEM_SUFFIX}{img_path.suffix.lower()}"),
            "archive_needed": not history_path.exists(),
            "history_path": str(history_path),
        }

    # pHash cache check — skip API call if we've processed this image before.
    # Only cache single-photo calls — multi-photo gives richer results and isn't
    # keyed sanely on one image's hash (mirrors ai_identify's same rule).
    from tgw.image_hash import compute_dhash, lookup_hash, store_hash

    img_hash = compute_dhash(img_path)
    use_cache = len(candidate_photos) == 1
    cached = lookup_hash(img_hash, "alt_text") if (img_hash and use_cache) else None

    raw: Optional[str] = None  # raw LLM response, only set on an actual (non-cached) call

    if cached is not None:
        result = cached
    else:
        # Fail-fast: check Ollama availability before expensive encoding
        if provider == "ollama" and not is_available(model):
            return {"ok": False, "error": f"Ollama unavailable or model {model!r} not found"}

        max_px = _OR_MAX_PX if provider in CLOUD_PROVIDERS else _VISION_MAX_PX
        img_b64_list = [_encode_resized(p, max_px=max_px) for p in candidate_photos]
        user_prompt = _USER_PROMPT_MULTI if len(candidate_photos) > 1 else _USER_PROMPT

        try:
            raw = call_model('alt_text', _SYSTEM_PROMPT, user_prompt, cfg,
                             img_b64_list=img_b64_list, provider=provider, model=model, sku=sku)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        try:
            result = extract_json(raw)
        except Exception:
            return {"ok": False, "error": f"model returned non-JSON: {raw[:200]}"}

        if img_hash and use_cache:
            store_hash(img_hash, sku, "alt_text", result)

    alt_text = str(result.get("alt_text", "")).strip()[:150]
    seo_caption = str(result.get("seo_caption", "")).strip()

    if not alt_text:
        return {"ok": False, "error": "model returned empty alt_text"}

    # Archive original to history before creating companion
    history_sku_dir = _history_sku_dir(cfg, sku)
    history_path = history_sku_dir / img_path.name
    if not history_path.exists():
        history_sku_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img_path, history_path)
        archived = True
    else:
        archived = False

    # Write companion derivative <sku>-alt.<ext> — original is NOT moved
    alt_path = sku_dir / f"{sku}{_ALT_STEM_SUFFIX}{img_path.suffix.lower()}"
    if not alt_path.exists():
        shutil.copy2(img_path, alt_path)

    # Write fields to draft_listing
    if "draft_listing" not in item:
        item["draft_listing"] = {}
    item["draft_listing"]["alt_text"] = alt_text
    item["draft_listing"]["seo_caption"] = seo_caption

    # Data Charter raw-preservation rule (Prime Directive 1): the raw LLM
    # response is the permanent asset, the parsed alt_text/seo_caption are
    # recomputable derivations of it. Mirrors ai_identify's vision_results[]
    # pattern — append-only, one record per actual (non-cached) model call,
    # never overwritten. A cache hit reuses a prior call's already-recorded
    # raw response, so nothing new to append here.
    if raw is not None:
        alt_text_results: List[Dict[str, Any]] = item.get("alt_text_results") or []
        alt_text_results.append({
            "photo": img_path.name,
            "photos": [p.name for p in candidate_photos],
            "photo_count": len(candidate_photos),
            "photo_hash": img_hash or "",
            "provider": provider,
            "model": model,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "extracted": {"alt_text": alt_text, "seo_caption": seo_caption},
            "raw_response": raw,
        })
        item["alt_text_results"] = alt_text_results

    atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))

    return {
        "ok": True,
        "sku": sku,
        "provider": provider,
        "model": model,
        "cache_hit": cached is not None,
        "alt_text": alt_text,
        "seo_caption": seo_caption,
        "image_copied_to": alt_path.name,
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


# ---------------------------------------------------------------------------
# Gemini Batch API path (tgw alt-text --batch --api-mode batch)
# ---------------------------------------------------------------------------

_BATCH_DEFAULT_MODEL = "gemini-2.5-flash-lite"
_BATCH_POLL_INTERVAL_S = 60
_BATCH_TIMEOUT_S = 3600 * 4


def _batch_state_path(cfg: Dict[str, Any]) -> Path:
    """Canonical path for the in-flight batch job state file."""
    raw = cfg.get("raw", {})
    runtime_root = Path(raw.get("runtime_root", "/opt/TGW/runtime") if isinstance(raw, dict) else "/opt/TGW/runtime")
    return runtime_root / "state" / "alt-text-batch-state.json"


def _apply_alt_text_result(
    cfg: Dict[str, Any],
    sku: str,
    alt_text_str: str,
    seo_caption: str,
    img_path: Path,
    img_hash: str,
    *,
    model: str = "",
    raw_response: Optional[str] = None,
) -> Dict[str, Any]:
    """Write one batch result back through the alt_text ledger.

    Mirrors cmd_alt_text's write path: archive original, rename to -alt.jpg,
    write draft_listing fields, store hash.  Idempotent.

    Data Charter raw-preservation rule (Prime Directive 1): when raw_response
    is given (an actual, non-cached Batch API call), append an
    alt_text_results[] record mirroring cmd_alt_text's pattern — a cache hit
    (raw_response is None) reuses a prior call's already-recorded raw
    response, so nothing new to append.
    """
    from datetime import datetime, timezone

    from .items import atomic_write_json

    itemdata_root = Path(cfg["itemdata_root"])
    sku_dir = itemdata_root / sku
    json_path = sku_dir / f"{sku}.json"

    if not json_path.exists():
        return {"ok": False, "sku": sku, "error": f"item JSON not found: {json_path}"}

    item = json.loads(json_path.read_text(encoding="utf-8"))

    alt_text_str = alt_text_str.strip()[:150]
    if not alt_text_str:
        return {"ok": False, "sku": sku, "error": "batch result has empty alt_text"}

    # Archive original before creating companion
    history_sku_dir = _history_sku_dir(cfg, sku)
    history_path = history_sku_dir / img_path.name
    if not history_path.exists():
        history_sku_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(img_path, history_path)
        archived = True
    else:
        archived = False

    # Write companion derivative — original is NOT moved
    alt_path = sku_dir / f"{sku}{_ALT_STEM_SUFFIX}{img_path.suffix.lower()}"
    if img_path.exists() and not alt_path.exists():
        shutil.copy2(img_path, alt_path)

    # Write fields
    if "draft_listing" not in item:
        item["draft_listing"] = {}
    item["draft_listing"]["alt_text"] = alt_text_str
    item["draft_listing"]["seo_caption"] = seo_caption.strip()

    if raw_response is not None:
        alt_text_results: List[Dict[str, Any]] = item.get("alt_text_results") or []
        alt_text_results.append({
            "photo": img_path.name,
            "photos": [img_path.name],
            "photo_count": 1,
            "photo_hash": img_hash or "",
            "provider": "google_direct",
            "model": model,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "extracted": {"alt_text": alt_text_str, "seo_caption": seo_caption.strip()},
            "raw_response": raw_response,
        })
        item["alt_text_results"] = alt_text_results

    atomic_write_json(json_path, item, pretty=cfg.get("pretty", True))

    # Cache result
    if img_hash:
        from tgw.image_hash import store_hash
        store_hash(img_hash, sku, "alt_text", {"alt_text": alt_text_str, "seo_caption": seo_caption.strip()})

    return {
        "ok": True,
        "sku": sku,
        "alt_text": alt_text_str,
        "seo_caption": seo_caption.strip(),
        "archived_to_history": archived,
    }


def cmd_alt_text_gemini_batch(
    cfg: Dict[str, Any],
    *,
    limit: int = 0,
    dry_run: bool = False,
    model: Optional[str] = None,
    poll_interval: int = _BATCH_POLL_INTERVAL_S,
    timeout_s: int = _BATCH_TIMEOUT_S,
) -> Dict[str, Any]:
    """Full-catalog alt-text sweep via Gemini Batch API (async, resumable).

    Chunks eligible SKUs into groups of BATCH_IMAGES_PER_TASK primary images,
    submits one Gemini Batch job, polls to completion, and writes results back
    through the existing alt_text ledger.  Resumable: a state file in
    runtime/state/alt-text-batch-state.json tracks the in-flight job.

    Requires: google-genai SDK + Google API key (todo #153).
    """
    from tgw.image_hash import compute_dhash, lookup_hash

    effective_model = model or _BATCH_DEFAULT_MODEL
    state_path = _batch_state_path(cfg)
    itemdata_root = Path(cfg["itemdata_root"])

    # ------------------------------------------------------------------
    # Phase 1 — collect eligible SKUs (same filter as cmd_alt_text_batch)
    # ------------------------------------------------------------------
    eligible: List[Dict[str, Any]] = []  # [{sku, img_path}]
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

        img_path = _primary_image(sku_dir)
        if img_path is None:
            continue

        eligible.append({"sku": sku, "img_path": img_path})
        if limit and len(eligible) >= limit:
            break

    if not eligible:
        return {
            "ok": True,
            "mode": "gemini_batch",
            "model": effective_model,
            "eligible": 0,
            "submitted": 0,
            "processed": 0,
            "skipped_cached": 0,
            "errors": 0,
            "note": "no eligible items found",
        }

    # ------------------------------------------------------------------
    # Phase 2 — skip images already in the image_hashes cache
    # ------------------------------------------------------------------
    to_process: List[Dict[str, Any]] = []
    skipped_cached = 0
    for entry in eligible:
        img_hash = compute_dhash(entry["img_path"])
        cached = lookup_hash(img_hash, "alt_text") if img_hash else None
        if cached is not None:
            skipped_cached += 1
            if not dry_run:
                _apply_alt_text_result(
                    cfg,
                    entry["sku"],
                    cached.get("alt_text", ""),
                    cached.get("seo_caption", ""),
                    entry["img_path"],
                    img_hash,
                )
        else:
            entry["img_hash"] = img_hash
            to_process.append(entry)

    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "mode": "gemini_batch",
            "model": effective_model,
            "eligible": len(eligible),
            "skipped_cached": skipped_cached,
            "would_submit": len(to_process),
            "chunk_count": (len(to_process) + BATCH_IMAGES_PER_TASK - 1) // BATCH_IMAGES_PER_TASK,
            "note": "run without --dry-run to submit batch",
        }

    if not to_process:
        return {
            "ok": True,
            "mode": "gemini_batch",
            "model": effective_model,
            "eligible": len(eligible),
            "submitted": 0,
            "processed": 0,
            "skipped_cached": skipped_cached,
            "errors": 0,
            "note": "all eligible items already cached",
        }

    # ------------------------------------------------------------------
    # Phase 3 — check for in-flight job (resume path)
    # ------------------------------------------------------------------
    chunks: List[List[str]] = []   # chunk_index → [sku, sku, ...]
    chunk_img_paths: List[List[Path]] = []
    chunk_img_hashes: List[List[str]] = []
    job_name: Optional[str] = None
    input_file_name: Optional[str] = None

    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("status") == "PROCESSING" and state.get("job_name"):
                job_name = state["job_name"]
                input_file_name = state.get("input_file_name", "")
                chunks = state.get("chunks", [])
                # Rebuild img_path + hash lookups from the saved SKU lists
                sku_to_entry = {e["sku"]: e for e in to_process}
                chunk_img_paths = [
                    [sku_to_entry[s]["img_path"] for s in chunk if s in sku_to_entry]
                    for chunk in chunks
                ]
                chunk_img_hashes = [
                    [sku_to_entry[s].get("img_hash", "") for s in chunk if s in sku_to_entry]
                    for chunk in chunks
                ]
        except Exception:
            # State file corrupt — start fresh
            job_name = None
            state_path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Phase 4 — build chunks + submit if no in-flight job
    # ------------------------------------------------------------------
    if job_name is None:
        # Build chunks
        for i in range(0, len(to_process), BATCH_IMAGES_PER_TASK):
            chunk_entries = to_process[i: i + BATCH_IMAGES_PER_TASK]
            chunks.append([e["sku"] for e in chunk_entries])
            chunk_img_paths.append([e["img_path"] for e in chunk_entries])
            chunk_img_hashes.append([e.get("img_hash", "") for e in chunk_entries])

        # Build JSONL tasks (stream one chunk at a time to avoid memory spike)
        tasks: List[Dict[str, Any]] = []
        for img_paths in chunk_img_paths:
            images_b64 = [_encode_resized(p, max_px=_OR_MAX_PX) for p in img_paths]
            tasks.append(build_alt_text_task(images_b64, model=effective_model))

        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            job_name, input_file_name = submit_batch(tasks, effective_model, cfg, Path(tmpdir))

        # Persist state for resumability
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({
                "job_name": job_name,
                "input_file_name": input_file_name,
                "model": effective_model,
                "status": "PROCESSING",
                "chunks": chunks,
            }),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Phase 5 — poll to completion
    # ------------------------------------------------------------------
    final_state = poll_batch(
        job_name, cfg,
        poll_interval_s=poll_interval,
        timeout_s=timeout_s,
    )

    if "COMPLETED" not in final_state:
        return {
            "ok": False,
            "mode": "gemini_batch",
            "job_name": job_name,
            "error": f"batch job ended in state {final_state!r}",
        }

    # ------------------------------------------------------------------
    # Phase 6 — download and apply results
    # ------------------------------------------------------------------
    raw_bytes = download_batch_output(job_name, cfg)
    task_results = parse_batch_results(raw_bytes)

    processed = 0
    error_details: List[Dict[str, Any]] = []

    for task_idx, (chunk_skus, img_paths, img_hashes) in enumerate(
        zip(chunks, chunk_img_paths, chunk_img_hashes)
    ):
        items_for_task = task_results[task_idx] if task_idx < len(task_results) else None
        if items_for_task is None:
            for sku in chunk_skus:
                error_details.append({"sku": sku, "error": f"task {task_idx} failed or parse error"})
            continue

        # Build index → result map (model may not return in strict order)
        by_index: Dict[int, Dict[str, Any]] = {}
        for item in items_for_task:
            if isinstance(item, dict):
                idx = item.get("index")
                if isinstance(idx, int):
                    by_index[idx] = item

        for j, (sku, img_path, img_hash) in enumerate(zip(chunk_skus, img_paths, img_hashes)):
            result_item = by_index.get(j)
            if result_item is None:
                # Fall back to positional order if index key is absent
                result_item = items_for_task[j] if j < len(items_for_task) else None

            if result_item is None:
                error_details.append({"sku": sku, "error": f"no result at index {j} in task {task_idx}"})
                continue

            alt_text_val = str(result_item.get("alt_text", "")).strip()
            seo_caption_val = str(result_item.get("seo_caption", "")).strip()
            raw_response_val = result_item.get("raw_response")

            if not alt_text_val:
                error_details.append({"sku": sku, "error": "model returned empty alt_text"})
                continue

            write_result = _apply_alt_text_result(
                cfg, sku, alt_text_val, seo_caption_val, img_path, img_hash,
                model=effective_model, raw_response=raw_response_val,
            )
            if write_result.get("ok"):
                processed += 1
            else:
                error_details.append({"sku": sku, "error": write_result.get("error", "write failed")})

    # ------------------------------------------------------------------
    # Phase 7 — cleanup
    # ------------------------------------------------------------------
    if input_file_name:
        cleanup_input_file(input_file_name, cfg)

    state_path.unlink(missing_ok=True)

    return {
        "ok": True,
        "mode": "gemini_batch",
        "model": effective_model,
        "job_name": job_name,
        "eligible": len(eligible),
        "submitted": len(to_process),
        "processed": processed,
        "skipped_cached": skipped_cached,
        "errors": len(error_details),
        "error_details": error_details,
    }
