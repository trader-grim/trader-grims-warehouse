#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""migrate_field_set_envelope.py — wrap Set A / Set B field-sets in the
self-describing envelope shape (todo #1418, PP-LISTEDITOR-001).

Wraps every item's `item_attributes` (Set A — "inventory_record") and
`draft_listing.item_specifics` (Set B — "ebay_draft") bare dicts in the new
envelope:

    {"_set": "inventory_record", "version": 1, "updated_at": "...",
     "updated_at_backfilled": true, "fields": {<old bare dict, unchanged>}}

No data is discarded or reshaped beyond the wrap — every value inside the
old bare dict lands unchanged inside the new envelope's `fields` key.
History arrays (`item_attributes_history` / `draft_listing.
item_specifics_history`) start EMPTY on migrated items — that data was
never captured for pre-existing edits, and Prime Directive 1 forbids
fabricating retroactive history.

`updated_at` cannot be a real "when was this last edited" timestamp for
migrated items (that information was never recorded) — it is always a
best-effort proxy (see `_best_known_timestamp`) or, failing that, the
migration run time, and is ALWAYS marked `updated_at_backfilled: true` so
no future reader mistakes it for a real edit timestamp (Prime Directive 1:
never claim false precision).

=============================================================================
REAL MIGRATION RISK — ~55,000 live items. Read before running.
=============================================================================
Per invariant E5, every write goes through `atomic_write_json(...,
archive_root=cfg['archive_root'])`, which archives the pre-migration JSON
to `archive_root/<sku>.zip` BEFORE the overwrite. Nothing is unrecoverable.

Default is DRY RUN (report only, zero writes). `--apply` performs real
writes. `--limit N` caps how many items are touched in one run — the
packet's Acceptance is a 50-100 item sample + dry-run only; running this
against the FULL catalog is a SEPARATE, EXPLICIT decision for Dave, not
something this script or this packet auto-executes. This script does not
enforce that ceiling itself (it is a general-purpose, re-runnable tool,
per "recompile, not one-shot" — see `recompile_category_backfill.py`) —
the operator is responsible for the go/no-go on scope each time it's run
with `--apply`.

Safe to re-run: already-enveloped items (`_set` present) are skipped
(idempotent no-op), so a partial/interrupted run can simply be re-invoked.

Usage:
    python scripts/migrate_field_set_envelope.py [--apply] [--limit N]
        [--report PATH] [--sample-only]
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import tgw.logging as tgw_logging  # noqa: E402
from tgw.config import DEFAULT_CONFIG, load_config  # noqa: E402
from tgw.ebay.draft_specifics import is_envelope as is_specifics_envelope  # noqa: E402
from tgw.ebay.draft_specifics import wrap_ebay_specifics  # noqa: E402
from tgw.inventory_record import is_envelope as is_attributes_envelope  # noqa: E402
from tgw.inventory_record import wrap_inventory_attributes  # noqa: E402
from tgw.items import atomic_write_json  # noqa: E402
from tgw.resolver import find_item_jsons, load_item_doc  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _best_known_timestamp(doc: Dict[str, Any]) -> Optional[str]:
    """Best-effort proxy for "most recently known modification" — never a
    real edit timestamp for item_attributes/item_specifics themselves (that
    was never recorded pre-migration), just the least-wrong guess available.
    Checked in priority order; first hit wins.
    """
    for candidate in (
        doc.get('baseline_at'),
        (doc.get('ebay_listing') or {}).get('synced_at'),
        (doc.get('ebay_offer') or {}).get('staged_at'),
    ):
        if candidate:
            return str(candidate)
    price_hist = doc.get('price_history') or []
    if price_hist and isinstance(price_hist, list):
        last = price_hist[-1]
        if isinstance(last, dict) and last.get('ts'):
            return str(last['ts'])
    return None


def plan_item(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Return the migration plan for one item (no I/O, no mutation).

    {needs_a, needs_b, patch} — patch is the set of top-level fields to
    write if needs_a or needs_b is True. round_trip verification data is
    also included so a dry-run can prove old values survive unchanged.
    """
    ts = _now_iso()
    proxy_ts = _best_known_timestamp(doc)
    used_ts = proxy_ts or ts

    needs_a = False
    a_before: Dict[str, Any] = {}
    raw_a = doc.get('item_attributes')
    if isinstance(raw_a, dict) and raw_a and not is_attributes_envelope(raw_a):
        needs_a = True
        a_before = dict(raw_a)

    dl = doc.get('draft_listing')
    needs_b = False
    b_before: Dict[str, Any] = {}
    if isinstance(dl, dict):
        raw_b = dl.get('item_specifics')
        if isinstance(raw_b, dict) and raw_b and not is_specifics_envelope(raw_b):
            needs_b = True
            b_before = dict(raw_b)

    patch: Dict[str, Any] = {}
    if needs_a:
        patch['item_attributes'] = wrap_inventory_attributes(
            a_before, updated_at=used_ts, backfilled=True)
        patch.setdefault('item_attributes_history', doc.get('item_attributes_history') or [])
    if needs_b:
        new_dl = dict(dl)
        new_dl['item_specifics'] = wrap_ebay_specifics(
            b_before, updated_at=used_ts, backfilled=True)
        new_dl.setdefault('item_specifics_history', dl.get('item_specifics_history') or [])
        patch['draft_listing'] = new_dl

    return {
        'needs_a': needs_a,
        'needs_b': needs_b,
        'a_before': a_before,
        'b_before': b_before,
        'used_timestamp': used_ts,
        'timestamp_is_proxy': proxy_ts is not None,
        'patch': patch,
    }


def _round_trip_ok(plan: Dict[str, Any]) -> bool:
    """Confirm the old bare-dict values are byte-for-byte recoverable from
    the new envelope's `fields` key — the packet's explicit Acceptance
    requirement before any --apply run."""
    ok = True
    if plan['needs_a']:
        ok = ok and plan['patch']['item_attributes']['fields'] == plan['a_before']
    if plan['needs_b']:
        ok = ok and plan['patch']['draft_listing']['item_specifics']['fields'] == plan['b_before']
    return ok


def run(cfg: Dict[str, Any], *, apply: bool, limit: int,
        report_path: Optional[Path]) -> Dict[str, Any]:
    started = time.time()
    paths = find_item_jsons(cfg)

    planned: List[Tuple[Path, Dict[str, Any]]] = []
    skipped_already_enveloped = 0
    skipped_no_data = 0
    round_trip_failures: List[str] = []

    for path in paths:
        try:
            doc = load_item_doc(path)
        except Exception as exc:
            round_trip_failures.append(f'{path.parent.name}: load failed: {exc}')
            continue
        plan = plan_item(doc)
        if not plan['needs_a'] and not plan['needs_b']:
            has_any = bool(doc.get('item_attributes')) or bool(
                (doc.get('draft_listing') or {}).get('item_specifics'))
            if has_any:
                skipped_already_enveloped += 1
            else:
                skipped_no_data += 1
            continue
        if not _round_trip_ok(plan):
            round_trip_failures.append(f'{path.parent.name}: round-trip verification failed')
            continue
        planned.append((path, plan))
        if limit and len(planned) >= limit:
            break

    sample = [{
        'sku': p.parent.name,
        'needs_a': plan['needs_a'],
        'needs_b': plan['needs_b'],
        'timestamp_is_proxy': plan['timestamp_is_proxy'],
        'used_timestamp': plan['used_timestamp'],
        'a_field_count': len(plan['a_before']),
        'b_field_count': len(plan['b_before']),
    } for p, plan in planned[:20]]

    result: Dict[str, Any] = {
        'ok': len(round_trip_failures) == 0,
        'dry_run': not apply,
        'total_items_scanned': len(paths),
        'planned': len(planned),
        'skipped_already_enveloped': skipped_already_enveloped,
        'skipped_no_data': skipped_no_data,
        'round_trip_failures': round_trip_failures[:20],
        'round_trip_failure_count': len(round_trip_failures),
        'sample': sample,
    }

    if not apply:
        result['elapsed_seconds'] = round(time.time() - started, 2)
        if report_path:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            report_path.write_text(_json.dumps(result, indent=2, default=str) + '\n',
                                    encoding='utf-8')
        return result

    # --apply: real writes, archive-before-overwrite (invariant E5)
    written = 0
    errors: List[str] = []
    for path, plan in planned:
        try:
            doc = load_item_doc(path)
            doc.update(plan['patch'])
            atomic_write_json(path, doc, pretty=cfg.get('pretty', True),
                              archive_root=cfg.get('archive_root'))
            written += 1
        except Exception as exc:
            errors.append(f'{path.parent.name}: write failed: {exc}')

    result['written'] = written
    result['errors'] = errors[:20]
    result['error_count'] = len(errors)
    result['elapsed_seconds'] = round(time.time() - started, 2)
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        import json as _json
        report_path.write_text(_json.dumps(result, indent=2, default=str) + '\n',
                                encoding='utf-8')
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true',
                        help='Perform real writes (default: dry-run, report only)')
    parser.add_argument('--limit', type=int, default=0,
                        help='Max items to migrate in this run (0 = unlimited)')
    parser.add_argument('--report', type=Path, default=None,
                        help='Write the JSON result to this path')
    args = parser.parse_args()

    cfg = load_config(DEFAULT_CONFIG)

    # Invariant E9: every one-off script announces itself before touching data.
    tgw_logging.announce_script_run(
        'migrate_field_set_envelope.py',
        'wrap item_attributes/draft_listing.item_specifics in the self-describing '
        'field-set envelope (todo #1418)',
        apply=args.apply, limit=args.limit,
    )

    result = run(cfg, apply=args.apply, limit=args.limit, report_path=args.report)

    print(f"dry_run={result['dry_run']} ok={result['ok']} "
          f"scanned={result['total_items_scanned']} planned={result['planned']} "
          f"skipped_already_enveloped={result['skipped_already_enveloped']} "
          f"skipped_no_data={result['skipped_no_data']} "
          f"round_trip_failures={result['round_trip_failure_count']}")
    if not args.apply:
        print('Sample (first 20 planned items):')
        for row in result['sample']:
            print(f"  {row}")
    else:
        print(f"written={result.get('written')} errors={result.get('error_count')}")
    if result['round_trip_failure_count']:
        print('ROUND-TRIP FAILURES (not migrated):')
        for line in result['round_trip_failures']:
            print(f'  {line}')

    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
