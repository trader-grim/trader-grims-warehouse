#!/usr/bin/env python3
"""
alt_text_model_test.py — side-by-side vision model comparison for TGW alt-text.

Tests Ollama (local) and OpenRouter models against a set of product photos.
Measures quality, latency, and cost estimate per model.

Usage:
    sudo -u tgw python scripts/alt_text_model_test.py
    sudo -u tgw python scripts/alt_text_model_test.py --skus tgw202604291954137 tgw202506041049295
    sudo -u tgw python scripts/alt_text_model_test.py --n 12 --include-ollama

Output:
    Prints markdown table to stdout.
    Writes var/log/alt-text-model-test-<ts>.json  (raw results)
    Writes var/log/alt-text-model-test-<ts>.md    (markdown table)
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from tgw.logging import announce_script_run, setup_logging

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ITEMDATA_ROOT = Path('/opt/TGW/data/ItemData')
_SECRETS_ROOT = Path('/opt/TGW/secrets')
_LOG_DIR = Path('/opt/TGW/var/log')
_IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png'}

# ---------------------------------------------------------------------------
# Models to test
# ---------------------------------------------------------------------------

MODELS = [
    {
        'id': 'google/gemini-2.5-flash',
        'label': 'Gemini 2.5 Flash (paid)',
        'provider': 'openrouter',
        'cost_per_1m_input': 0.30,
        'notes': '$0.30/M tokens, fast, excellent quality — confirmed working',
    },
    {
        'id': 'google/gemma-4-31b-it:free',
        'label': 'Gemma4 31B (free)',
        'provider': 'openrouter',
        'cost_per_1m_input': 0.0,
        'notes': 'Top-rated free vision; may 429 if Google AI Studio pool exhausted',
    },
    {
        'id': 'google/gemma-4-26b-a4b-it:free',
        'label': 'Gemma4 26B MoE (free)',
        'provider': 'openrouter',
        'cost_per_1m_input': 0.0,
        'notes': 'Fast MoE; may 429 if pool exhausted',
    },
    {
        'id': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
        'label': 'Nemotron Nano (free)',
        'provider': 'openrouter',
        'cost_per_1m_input': 0.0,
        'notes': 'Scene description, perception-focused',
    },
    {
        'id': 'openrouter/free',
        'label': 'OR auto-free',
        'provider': 'openrouter',
        'cost_per_1m_input': 0.0,
        'notes': 'Routes to shortest-queue free vision model automatically',
    },
    {
        'id': 'meta-llama/llama-3.2-11b-vision-instruct',
        'label': 'Llama3.2 11B Vision (paid)',
        'provider': 'openrouter',
        'cost_per_1m_input': 0.345,
        'notes': '$0.345/M tokens',
    },
]

OLLAMA_MODEL = {
    'id': 'qwen2.5vl:7b',
    'label': 'qwen2.5vl:7b (local)',
    'provider': 'ollama',
    'cost_per_1m_input': 0.0,
    'notes': 'Local CPU-only, slow (~18s warm)',
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = (
    'You are an expert in web accessibility and SEO. '
    'Respond with valid JSON only — no markdown fences, no commentary.'
)
_PROMPT = (
    'Describe this product photo for web accessibility and SEO. '
    'Return JSON with exactly two string fields:\n'
    '  "alt_text": concise description of the main subject (max 150 chars; '
    'do NOT start with "image of" or "picture of"),\n'
    '  "seo_caption": 1-2 sentences including brand, model, and key features.\n'
    'JSON only.'
)

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _primary_image(sku_dir: Path) -> Path | None:
    candidates = sorted(
        p for p in sku_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in _IMAGE_SUFFIXES
        and not p.stem.endswith('-alt')
    )
    return candidates[0] if candidates else None


def _encode_jpeg(img_path: Path, max_px: int = 768) -> str:
    """Resize to max_px longest edge and return base64 JPEG string."""
    try:
        from PIL import Image
        with Image.open(img_path) as img:
            img.thumbnail((max_px, max_px))
            buf = io.BytesIO()
            img.convert('RGB').save(buf, format='JPEG', quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return base64.b64encode(img_path.read_bytes()).decode()


# ---------------------------------------------------------------------------
# Model callers
# ---------------------------------------------------------------------------

def _call_ollama(model_id: str, img_b64: str) -> tuple[str, float]:
    """Return (raw_text, elapsed_seconds)."""
    t0 = time.monotonic()
    resp = requests.post(
        'http://localhost:11434/api/generate',
        json={
            'model': model_id,
            'prompt': _PROMPT,
            'system': _SYSTEM,
            'images': [img_b64],
            'stream': False,
        },
        timeout=600,
    )
    elapsed = time.monotonic() - t0
    resp.raise_for_status()
    return resp.json()['response'], elapsed


def _call_openrouter(api_key: str, model_id: str, img_b64: str,
                     max_retries: int = 3) -> tuple[str, float]:
    """Return (raw_text, elapsed_seconds). Retries 429s with backoff."""
    t0 = time.monotonic()
    payload = {
        'model': model_id,
        'messages': [
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': _SYSTEM + '\n\n' + _PROMPT},
                    {
                        'type': 'image_url',
                        'image_url': {'url': f'data:image/jpeg;base64,{img_b64}'},
                    },
                ],
            }
        ],
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://tgw.local',
        'X-Title': 'TGW alt-text model test',
    }
    for attempt in range(max_retries):
        resp = requests.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers=headers, json=payload, timeout=60,
        )
        if resp.status_code == 429 and attempt < max_retries - 1:
            wait = 10 * (attempt + 1)
            print(f'429 (attempt {attempt+1}/{max_retries}), retrying in {wait}s... ', end='', flush=True)
            time.sleep(wait)
            continue
        break
    elapsed = time.monotonic() - t0
    resp.raise_for_status()
    return resp.json()['choices'][0]['message']['content'], elapsed


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    import re
    # Strip markdown fences if present
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE)
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Find first {...} block
    m = re.search(r'\{.*\}', text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f'no JSON found in: {text[:200]}')


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------

def _check(result: dict) -> list[str]:
    issues = []
    alt = result.get('alt_text', '')
    cap = result.get('seo_caption', '')
    if not alt:
        issues.append('EMPTY_ALT')
    elif len(alt) > 150:
        issues.append(f'TOO_LONG({len(alt)})')
    if alt.lower().startswith(('image of', 'picture of', 'photo of')):
        issues.append('BAD_PREFIX')
    if not cap:
        issues.append('EMPTY_CAPTION')
    return issues


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _load_openrouter_key() -> str:
    cred_path = _SECRETS_ROOT / 'openrouter-credentials.json'
    if cred_path.exists():
        return json.loads(cred_path.read_text())['api_key']
    # Fallback: environment variable
    key = os.environ.get('OPENROUTER_API_KEY', '')
    if key:
        return key
    sys.exit('ERROR: no OpenRouter key found in secrets/openrouter-credentials.json or $OPENROUTER_API_KEY')


def _sample_skus(n: int) -> list[str]:
    """Sample n SKUs from ItemData that have at least one photo."""
    all_skus = [
        d.name for d in _ITEMDATA_ROOT.iterdir()
        if d.is_dir() and d.name.startswith('tgw')
    ]
    random.shuffle(all_skus)
    chosen = []
    for sku in all_skus:
        if len(chosen) >= n:
            break
        if _primary_image(_ITEMDATA_ROOT / sku) is not None:
            chosen.append(sku)
    return chosen


def run(skus: list[str], models: list[dict], openrouter_key: str,
        rate_delay: float = 3.0) -> list[dict]:
    """Run all models against all SKUs. Returns list of result records."""
    results = []
    total_calls = len(skus) * len(models)
    call_n = 0

    for sku in skus:
        sku_dir = _ITEMDATA_ROOT / sku
        img_path = _primary_image(sku_dir)
        if img_path is None:
            print(f'  SKIP {sku}: no primary image', file=sys.stderr)
            continue

        print(f'\n[{sku}] image: {img_path.name}', flush=True)

        # Encode at 768px for cloud models (better quality than 512px Ollama default)
        img_b64 = _encode_jpeg(img_path, max_px=768)
        img_kb = len(base64.b64decode(img_b64)) // 1024

        for model in models:
            call_n += 1
            label = model['label']
            print(f'  ({call_n}/{total_calls}) {label} ... ', end='', flush=True)

            record: dict = {
                'sku': sku,
                'image': img_path.name,
                'image_kb': img_kb,
                'model_id': model['id'],
                'model_label': label,
                'provider': model['provider'],
            }

            try:
                if model['provider'] == 'ollama':
                    raw, elapsed = _call_ollama(model['id'], img_b64)
                else:
                    raw, elapsed = _call_openrouter(openrouter_key, model['id'], img_b64)

                parsed = _extract_json(raw)
                issues = _check(parsed)

                record.update({
                    'ok': True,
                    'elapsed_s': round(elapsed, 2),
                    'alt_text': parsed.get('alt_text', ''),
                    'seo_caption': parsed.get('seo_caption', ''),
                    'issues': issues,
                    'raw': raw,
                })
                status = f'{elapsed:.1f}s'
                if issues:
                    status += f' ISSUES:{",".join(issues)}'
                print(status, flush=True)

            except Exception as exc:
                record.update({'ok': False, 'error': str(exc), 'elapsed_s': None})
                print(f'ERROR: {exc}', flush=True)

            results.append(record)

            # Rate-limit delay between OpenRouter calls
            if model['provider'] == 'openrouter' and call_n < total_calls:
                time.sleep(rate_delay)

    return results


def _markdown_table(results: list[dict], skus: list[str], models: list[dict]) -> str:
    lines = ['# TGW Alt-Text Model Comparison\n']
    lines.append(f'Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')
    lines.append(f'SKUs tested: {len(skus)}  |  Models tested: {len(models)}\n')

    for sku in skus:
        sku_results = [r for r in results if r['sku'] == sku]
        if not sku_results:
            continue
        img = sku_results[0]['image'] if sku_results else '?'
        lines.append(f'## {sku}  (`{img}`)\n')
        lines.append('| Model | Latency | Issues | Alt Text | SEO Caption |')
        lines.append('|-------|---------|--------|----------|-------------|')
        for r in sku_results:
            if not r['ok']:
                lines.append(f'| {r["model_label"]} | — | ERROR | `{r.get("error","?")[:60]}` | — |')
            else:
                issues = ', '.join(r['issues']) if r['issues'] else '✅'
                alt = r['alt_text'].replace('|', '\\|')
                cap = r['seo_caption'].replace('|', '\\|')[:80]
                lines.append(
                    f'| {r["model_label"]} | {r["elapsed_s"]}s | {issues} '
                    f'| {alt} | {cap}… |'
                )
        lines.append('')

    # Summary: average latency per model
    lines.append('## Latency Summary\n')
    lines.append('| Model | Avg Latency | Success | Pass Rate |')
    lines.append('|-------|-------------|---------|-----------|')
    for model in models:
        model_results = [r for r in results if r['model_id'] == model['id']]
        ok_results = [r for r in model_results if r.get('ok')]
        if not ok_results:
            lines.append(f'| {model["label"]} | — | 0/{len(model_results)} | — |')
            continue
        avg_lat = sum(r['elapsed_s'] for r in ok_results) / len(ok_results)
        pass_rate = sum(1 for r in ok_results if not r['issues']) / len(ok_results) * 100
        lines.append(
            f'| {model["label"]} | {avg_lat:.1f}s | {len(ok_results)}/{len(model_results)} '
            f'| {pass_rate:.0f}% |'
        )

    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='TGW alt-text vision model comparison')
    parser.add_argument('--skus', nargs='+', metavar='SKU',
                        help='specific SKUs to test (default: auto-sample)')
    parser.add_argument('--n', type=int, default=8, metavar='N',
                        help='number of SKUs to auto-sample (default: 8)')
    parser.add_argument('--include-ollama', action='store_true',
                        help='include local Ollama model (slow, ~18s/image when warm)')
    parser.add_argument('--rate-delay', type=float, default=3.0, metavar='S',
                        help='seconds between OpenRouter calls (default: 3.0)')
    parser.add_argument('--models', nargs='+', metavar='ID',
                        help='test only these model IDs (subset of defaults)')
    args = parser.parse_args()

    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.alt_text_model_test')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'alt_text_model_test.py',
        'side-by-side vision model comparison for alt-text quality/latency/cost',
        skus=args.skus, n=args.n, include_ollama=args.include_ollama,
        models=args.models,
    )

    openrouter_key = _load_openrouter_key()

    skus = args.skus or _sample_skus(args.n)
    if not skus:
        sys.exit('ERROR: no SKUs with photos found')

    models = list(MODELS)
    if args.include_ollama:
        models.insert(0, OLLAMA_MODEL)
    if args.models:
        filter_ids = set(args.models)
        models = [m for m in models if m['id'] in filter_ids]
        if not models:
            sys.exit('ERROR: none of the requested model IDs matched')

    print(f'Testing {len(models)} models × {len(skus)} SKUs = {len(models)*len(skus)} calls')
    print(f'SKUs: {", ".join(skus)}')
    print(f'Rate delay: {args.rate_delay}s between OpenRouter calls\n')

    results = run(skus, models, openrouter_key, rate_delay=args.rate_delay)

    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    json_path = _LOG_DIR / f'alt-text-model-test-{ts}.json'
    json_path.write_text(json.dumps({
        'ts': ts,
        'skus': skus,
        'models': [m['id'] for m in models],
        'results': results,
    }, indent=2, ensure_ascii=False))
    print(f'\nRaw JSON → {json_path}')

    md = _markdown_table(results, skus, models)
    md_path = _LOG_DIR / f'alt-text-model-test-{ts}.md'
    md_path.write_text(md)
    print(f'Markdown  → {md_path}\n')
    print(md)


if __name__ == '__main__':
    main()
