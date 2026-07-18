#!/usr/bin/env python3
"""
Vision model test — compare Gemini 2.5 Flash-Lite vs 3.1 Flash-Lite via OpenRouter
against real TGW product photos using the same prompts as ai_identify.

Usage:
    python3 scripts/vision_test.py [--model MODEL] [--photos N] [--compare]

    --model   openrouter model id (default: google/gemini-2.5-flash-lite)
    --photos  number of random photos to test (default: 8)
    --compare run both 2.5-flash-lite AND 3.1-flash-lite side by side

Requires OPENROUTER_API_KEY in environment or /home/tgw/.env
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Missing deps: pip install httpx")
    sys.exit(1)

# Import the production encode helper so this script always matches what ai_identify sends.
# ai_identify uses max_px=768 for OpenRouter providers (vs 512 for Ollama).
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tgw.workers.ai_identify import _encode_resized  # noqa: E402
from tgw.logging import announce_script_run, setup_logging  # noqa: E402

# ── config ─────────────────────────────────────────────────────────────────────

ITEM_DATA = Path("/opt/TGW/data/ItemData")
OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_OPENROUTER_MAX_PX = 768  # matches ai_identify's OpenRouter branch

SYSTEM_PROMPT = (
    "You are an eBay listing assistant. You will be shown a photo of an item for sale.\n"
    "Respond with valid JSON only — no prose, no markdown fences."
)

USER_PROMPT = """\
Look at this item photo and provide:
- A concise, descriptive eBay-style title (under 80 characters)
- The most likely eBay category name (plain English, e.g. "Board Games", "Action Figures")
- A 1-2 sentence description of what the item appears to be
- Your best guess at condition: "New", "Like New", "Very Good", "Good", "Acceptable"
- Note any shadow or background issues that affected your assessment (or "none")

Respond with JSON:
{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "...",
  "background_note": "..."
}"""

# ── helpers ────────────────────────────────────────────────────────────────────

def load_env():
    env_file = Path("/home/tgw/.env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def get_primary_photos(n: int) -> list[Path]:
    """Pick n random SKU folders and return their primary (alphabetically first) image."""
    folders = [d for d in ITEM_DATA.iterdir() if d.is_dir()]
    random.shuffle(folders)
    photos = []
    for folder in folders:
        imgs = sorted(folder.glob("*.jpg")) + sorted(folder.glob("*.JPG"))
        if imgs:
            photos.append(imgs[0])
        if len(photos) >= n:
            break
    return photos


def encode_image(path: Path) -> str:
    """Encode image using the same resize/quality settings as ai_identify (OpenRouter path)."""
    b64, _orig_kb, _resized_kb = _encode_resized(path, max_px=_OPENROUTER_MAX_PX)
    return b64


def query_model(model: str, b64: str, api_key: str) -> dict:
    """Send image to OpenRouter and return parsed JSON result."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://tgw.local",
        "X-Title": "TGW vision test",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": USER_PROMPT},
            ]},
        ],
        "max_tokens": 400,
        "temperature": 0.0,
    }
    t0 = time.time()
    r = httpx.post(f"{OPENROUTER_BASE}/chat/completions",
                   headers=headers, json=payload, timeout=30)
    elapsed = time.time() - t0
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"].strip()
    # strip accidental markdown fences
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        result = {"raw": content, "parse_error": True}
    result["_elapsed_s"] = round(elapsed, 2)
    usage = data.get("usage", {})
    result["_tokens_in"]  = usage.get("prompt_tokens", "?")
    result["_tokens_out"] = usage.get("completion_tokens", "?")
    return result


def print_result(photo: Path, model: str, result: dict):
    sku = photo.parent.name
    print(f"\n{'─'*70}")
    print(f"SKU:   {sku}")
    print(f"Photo: {photo.name}")
    print(f"Model: {model}  [{result.get('_elapsed_s','?')}s  "
          f"in={result.get('_tokens_in','?')} out={result.get('_tokens_out','?')}]")
    if result.get("parse_error"):
        print(f"  ⚠ JSON parse failed — raw:\n  {result.get('raw','')}")
        return
    print(f"  Title:      {result.get('title','?')}")
    print(f"  Category:   {result.get('category','?')}")
    print(f"  Condition:  {result.get('condition','?')}")
    print(f"  Desc:       {result.get('description','?')}")
    bg = result.get("background_note", "")
    if bg and bg.lower() not in ("none", "n/a", ""):
        print(f"  Background: ⚠ {bg}")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    load_env()
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY not set")
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("--model",   default="google/gemini-2.5-flash-lite")
    parser.add_argument("--photos",  type=int, default=8)
    parser.add_argument("--compare", action="store_true",
                        help="Run both gemini-2.5-flash-lite AND gemini-3.1-flash-lite")
    args = parser.parse_args()

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.vision_test')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'vision_test.py',
        'compare vision models against real TGW product photos using ai_identify prompts (OpenRouter quota)',
        model=args.model, photos=args.photos, compare=args.compare,
    )

    models = [args.model]
    if args.compare:
        models = ["google/gemini-2.5-flash-lite", "google/gemini-3.1-flash-lite"]

    photos = get_primary_photos(args.photos)
    print(f"Testing {len(photos)} photos × {len(models)} model(s)\n")

    totals = {m: {"in": 0, "out": 0, "t": 0.0, "ok": 0, "err": 0} for m in models}

    for photo in photos:
        b64 = encode_image(photo)
        for model in models:
            try:
                result = query_model(model, b64, api_key)
                print_result(photo, model, result)
                t = totals[model]
                t["in"]  += result.get("_tokens_in", 0) if isinstance(result.get("_tokens_in"), int) else 0
                t["out"] += result.get("_tokens_out", 0) if isinstance(result.get("_tokens_out"), int) else 0
                t["t"]   += result.get("_elapsed_s", 0)
                t["ok" if not result.get("parse_error") else "err"] += 1
            except Exception as e:
                print(f"\n  ERROR on {photo.name} / {model}: {e}")
                totals[model]["err"] += 1

    print(f"\n{'═'*70}")
    print("SUMMARY")
    for model, t in totals.items():
        cost_in  = t["in"]  / 1_000_000 * 0.10
        cost_out = t["out"] / 1_000_000 * 0.40
        print(f"\n  {model}")
        print(f"    OK/Err:  {t['ok']}/{t['err']}")
        print(f"    Tokens:  {t['in']} in / {t['out']} out")
        print(f"    Cost:    ${cost_in + cost_out:.4f}  "
              f"(in ${cost_in:.4f} + out ${cost_out:.4f})")
        print(f"    Time:    {t['t']:.1f}s total  "
              f"({t['t']/max(t['ok']+t['err'],1):.1f}s avg)")


if __name__ == "__main__":
    main()
