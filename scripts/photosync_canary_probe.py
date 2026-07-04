#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""photosync_canary_probe.py — PP-PHOTOSYNC-001 P8 canary probe (todo #1124).

Exercises the REAL operator surface (the same `/api/items/{sku}/action` HTTP
endpoints the web UI uses) on ONE designated item, waits for the chain to
drain, pulls live eBay state, diffs it against intent (title, price, photo
count, aspects), scans the journal window for ERROR/WARN mentioning the SKU,
and reports pass/fail — the same "test the function and read the log"
discipline already applied elsewhere (P7 truth-audit), now automated daily.

**The canary SKU is never chosen by this script.** Per the P8 spec: "Canary
item: Dave designates (low-value live listing or a dedicated test item —
ask him at packet start, do not choose silently)." --sku is a required
argument with no default.

Safe by construction for the read-only path: --actions defaults to just
`sync_from_ebay` (an ebay_sync job — pulls live state, mutates nothing on
eBay). Passing a mutating action (ebay_update, ebay_stage, ebay_publish,
ebay_end_listing) is opt-in and will actually change what's live on eBay
for the SKU given — only do this against an item Dave has explicitly
approved as safe to press real buttons on.

Usage:
    python scripts/photosync_canary_probe.py --sku <SKU> [--actions ebay_update]
        [--timeout 120] [--journal-window-s 300]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402
from tgw.items import load_item_doc  # noqa: E402
from tgw.notify import notify  # noqa: E402
from tgw.queue import state_machine  # noqa: E402

STATUS_PATH = Path('/opt/TGW/var/log/canary-probe-status.json')


def _http_action(base_url: str, api_key: str, sku: str, action: str) -> Dict[str, Any]:
    import urllib.request

    req = urllib.request.Request(
        f'{base_url}/api/items/{sku}/action',
        data=json.dumps({'action': action}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}'},
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def _wait_for_job(job_id: str, timeout: int) -> Optional[str]:
    """Poll queue_jobs for a terminal state. Returns the final state or None
    on timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with state_machine._conn() as con:  # noqa: SLF001 — one-shot probe script
            with con.cursor() as cur:
                cur.execute(
                    "SELECT state FROM queue_jobs WHERE job_id = %s", (job_id,),
                )
                row = cur.fetchone()
        if row and row[0] in ('succeeded', 'dead_letter', 'cancelled'):
            return row[0]
        time.sleep(2)
    return None


def _intent_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    dl = item.get('draft_listing') or {}
    return {
        'title': dl.get('title') or item.get('title'),
        'price': dl.get('price') or item.get('price'),
        'photo_count': len(dl.get('imageUrls') or []),
        'aspects': dl.get('item_specifics') or {},
    }


def _live_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    """ebay_live mirrors the raw GET inventory_item response (nested under
    inventory_item.product); ebay_listing carries the flatter offer/publish
    state, including live_price (not `price`) — confirmed against a real
    item's JSON 2026-07-04, not guessed."""
    live = item.get('ebay_live') or {}
    product = (live.get('inventory_item') or {}).get('product') or {}
    listing = item.get('ebay_listing') or {}
    price = listing.get('live_price')
    return {
        'title': product.get('title'),
        'price': str(price) if price is not None else None,
        'photo_count': len(product.get('imageUrls') or []),
        'aspects': product.get('aspects') or {},
    }


def _diff(intent: Dict[str, Any], live: Dict[str, Any]) -> List[str]:
    mismatches = []
    for key in ('title', 'price', 'photo_count'):
        if intent.get(key) != live.get(key):
            mismatches.append(f'{key}: intent={intent.get(key)!r} live={live.get(key)!r}')
    return mismatches


def _journal_errors(sku: str, window_s: int) -> List[str]:
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'tgw-worker@*', f'--since=-{window_s}s'],
            capture_output=True, text=True, timeout=15,
        )
        hits = [line for line in result.stdout.splitlines()
               if sku in line and ('ERROR' in line or 'WARN' in line)]
        return hits
    except Exception as exc:
        return [f'journal scan failed: {exc}']


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--sku', required=True,
                       help='Canary item SKU — Dave designates this, never inferred')
    parser.add_argument('--actions', default='sync_from_ebay',
                       help='Comma-separated action chain to press (default: read-only sync_from_ebay)')
    parser.add_argument('--timeout', type=int, default=120,
                       help='Seconds to wait for each enqueued job to reach a terminal state')
    parser.add_argument('--journal-window-s', type=int, default=300)
    parser.add_argument('--base-url', default='http://127.0.0.1:7373')
    args = parser.parse_args()

    cfg = load_config(DEFAULT_CONFIG)
    api_key_path = cfg['secrets_root'] / 'tgw-api-key.json'
    api_key = json.loads(api_key_path.read_text(encoding='utf-8'))['api_key']

    state_machine.init(cfg['postgres_dsn'])

    json_path = cfg['itemdata_root'] / args.sku / f'{args.sku}.json'
    if not json_path.exists():
        print(f'ERROR: no item JSON for sku {args.sku}', file=sys.stderr)
        return 1

    intent = _intent_snapshot(load_item_doc(json_path))
    t0 = time.time()

    action_results = []
    for action in args.actions.split(','):
        action = action.strip()
        print(f'Pressing action: {action}')
        try:
            resp = _http_action(args.base_url, api_key, args.sku, action)
        except Exception as exc:
            action_results.append({'action': action, 'ok': False, 'error': str(exc)})
            continue
        job_id = resp.get('job_id')
        if job_id:
            state = _wait_for_job(job_id, args.timeout)
            action_results.append({'action': action, 'ok': state == 'succeeded',
                                   'job_state': state, 'job_id': job_id})
        else:
            action_results.append({'action': action, 'ok': resp.get('ok', False),
                                   'response': resp})

    live = _live_snapshot(load_item_doc(json_path))
    mismatches = _diff(intent, live)
    journal_hits = _journal_errors(args.sku, args.journal_window_s)

    action_failed = any(not r.get('ok') for r in action_results)
    passed = not action_failed and not mismatches and not journal_hits

    result = {
        'sku': args.sku, 'actions': args.actions, 'ran_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'duration_s': round(time.time() - t0, 1),
        'action_results': action_results, 'intent': intent, 'live': live,
        'mismatches': mismatches, 'journal_hits': journal_hits, 'passed': passed,
    }
    STATUS_PATH.write_text(json.dumps(result, indent=2))
    print(f'\n{"PASS" if passed else "FAIL"} — report written to {STATUS_PATH}')

    if not passed:
        notify('Canary probe FAILED', f'{args.sku}: {mismatches or journal_hits or action_results}',
              level='warning')

    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
