#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""eval_repricer_gemini_grounding.py — PP-REPRICER-001 eval packet (todo #1109).

Standalone eval, NOT wired into production. Compares one of the two
recovered Google pricing options from PP-PRICING-001's design:

  (a) Gemini + Google Search grounding (this script) — free, google_direct key
  (b) SerpApi google_shopping SERP — BLOCKED, no key provisioned (todo #1110)

Scores (a) against the existing, already-in-production BrowseCompsProvider
(live eBay Browse API comps — quota-free, same signal `ebay_price` already
trusts) rather than "Dave's real prices" as the todo originally specified:
no programmatic ground-truth-price dataset exists for arbitrary items.
Flagged as a deviation — Dave can spot-check the sample list below.

Usage:
  sudo -u tgw env LD_LIBRARY_PATH=... python3 scripts/eval_repricer_gemini_grounding.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.config import load_config  # noqa: E402
from tgw.apis.google_genai import load_google_key  # noqa: E402
from tgw.ebay.pricing import suggest_price  # noqa: E402
from tgw import quota  # noqa: E402
from tgw.logging import announce_script_run, setup_logging  # noqa: E402

# Fixed sample — 10 real, sold TGW items (master-catalog.json, status='sold',
# price > $10, title length > 20 chars), chosen with a fixed random seed so
# this eval is reproducible. Deliberately NOT cherry-picked easy brand-name
# electronics — this is TGW's actual long-tail resale mix.
SAMPLE = [
    ("tgw201906021507162", "San Francisco 49ERS Team of The 80s Lapel Pin", 25.87),
    ("tgw201705311343251", "RCA Amplified Indoor HDTV Antenna Model ANT1251", 14.99),
    ("tgw202001171043100", "Flour Sieve Sifter Colander Wood Handle Yellow/Black", 15.75),
    ("tgw202202082238594", "Chinese Restaurant Serving Fork Mid Century Vintage", 29.99),
    ("tgw201601200122345", "Dutch Bros French Press Coffee Maker Stainless Steel Travel Car Mug Cup", 23.55),
    ("tgw201611030012387", "Third Eye Eyeglass Mirror Bicycle Bike Rearview Mount Sun Glasses", 11.99),
    ("tgw202305071950384", "SPEC LIN Class 2 Transformer Lsa-18008 18 Volts Ac 0.8 Amps", 22.48),
    ("tgw202102192115061", "Green Fish Shaped Figure Bottle Opener Bar Tool", 11.85),
    ("tgw201701240011592", "Wooden Carved Figural Salt And Pepper Shaker", 17.99),
    ("tgw201910191109140", "Devils Tower National Monument Wyoming - National Park Service 1984", 12.99),
]

_SYSTEM = (
    "You are a resale pricing analyst. Given an item title and its last "
    "recorded TGW sale price, use web search to find comparable current "
    "asking/sold prices for this exact or very similar item. Respond with "
    "ONLY a JSON object: "
    '{"estimated_price": <number or null>, "confidence": "high"|"medium"|"low", '
    '"rationale": "<one sentence, cite what you found>"}. '
    "Use null for estimated_price if you cannot find any comparable pricing signal."
)


def _call_gemini_grounded(client, model: str, title: str) -> dict:
    response = client.models.generate_content(
        model=f'models/{model}',
        contents=[{'role': 'user', 'parts': [{'text': f'Item title: {title}'}]}],
        config={
            'system_instruction': _SYSTEM,
            'tools': [{'google_search': {}}],
        },
    )
    text = (response.text or '').strip()
    # Strip markdown fences if the model added them despite instructions.
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    try:
        parsed = json.loads(text.strip())
    except Exception as exc:
        parsed = {'estimated_price': None, 'confidence': 'low',
                   'rationale': f'PARSE_ERROR: {exc} — raw: {text[:200]}'}
    grounding = getattr(response.candidates[0], 'grounding_metadata', None) if response.candidates else None
    sources = []
    if grounding and getattr(grounding, 'grounding_chunks', None):
        for chunk in grounding.grounding_chunks:
            web = getattr(chunk, 'web', None)
            if web is not None:
                sources.append(getattr(web, 'uri', None) or getattr(web, 'title', None))
    parsed['sources'] = [s for s in sources if s]
    return parsed


def main() -> int:
    # No prior logging configuration in this script (verified live, todo
    # #1369) — without it, announce_script_run()'s event is silently
    # dropped (default root level WARNING, no handlers).
    try:
        setup_logging('tgw.eval_repricer_gemini_grounding')
    except OSError:
        pass  # no writable log root (e.g. CI/test env) — announce still attempted below
    announce_script_run(
        'eval_repricer_gemini_grounding.py',
        'standalone eval — Gemini+Search-grounding pricing vs production BrowseCompsProvider (PP-REPRICER-001, not wired into production)',
    )
    cfg = load_config(Path('/opt/TGW/config/tgw-api-config.json'))
    from tgw.apis.google_genai import _require_genai
    genai = _require_genai()
    api_key = load_google_key(cfg)
    client = genai.Client(api_key=api_key)
    model = 'gemini-2.5-flash'  # same model already used for ebay_draft

    results = []
    for sku, title, tgw_price in SAMPLE:
        print(f'--- {sku}: {title[:60]} (TGW sold @ ${tgw_price}) ---')

        # (a) Gemini + Search grounding
        try:
            gemini = _call_gemini_grounded(client, model, title)
        except Exception as exc:
            gemini = {'estimated_price': None, 'confidence': 'low',
                       'rationale': f'CALL_ERROR: {exc}', 'sources': []}
        quota.record(cfg, 'llm_google')

        # Baseline: existing production Browse comps (quota-free eBay API,
        # already trusted by the live ebay_price worker)
        try:
            browse = suggest_price(cfg, title)
            comps = browse.get('comps') or {}
        except Exception as exc:
            comps = {'error': str(exc)}

        row = {
            'sku': sku, 'title': title, 'tgw_sold_price': tgw_price,
            'gemini_estimate': gemini.get('estimated_price'),
            'gemini_confidence': gemini.get('confidence'),
            'gemini_rationale': gemini.get('rationale'),
            'gemini_sources': gemini.get('sources'),
            'browse_p25': comps.get('p25'), 'browse_median': comps.get('median'),
            'browse_count': comps.get('count'),
        }
        results.append(row)
        print(f'  gemini: {row["gemini_estimate"]} ({row["gemini_confidence"]}) — {row["gemini_rationale"]}')
        print(f'  browse: p25={row["browse_p25"]} median={row["browse_median"]} n={row["browse_count"]}')

    out_path = Path('/opt/TGW/var/log/repricer-eval-1109.json')
    out_path.write_text(json.dumps(results, indent=2))
    print(f'\nWrote {out_path}')

    # Summary
    n_gemini_hit = sum(1 for r in results if r['gemini_estimate'] is not None)
    n_browse_hit = sum(1 for r in results if r['browse_count'])
    print(f'\nSummary: Gemini produced a price for {n_gemini_hit}/{len(results)}; '
          f'Browse comps had >=1 sample for {n_browse_hit}/{len(results)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
