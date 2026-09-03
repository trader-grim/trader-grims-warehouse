#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""requeue_deadletter_001_fixed.py — bulk-requeue dead-letters whose root
cause was fixed by the PP-DEADLETTER-001 8-packet batch (2026-07-14).

Only requeues classes CONFIRMED fixed by a merged packet:
  - ebay_stage  / 'not a leaf category'          -> #1395 (real fix)
  - ebay_upload / 'File dimension limit exceeds' -> #1398 (real fix)
  - ebay_upload / 'XML Parse error'              -> #1399 (real fix)
  - ebay_draft  / 'image file is truncated' or
                  'broken data stream'           -> #1403 (skip+log, not a
                                                    repair -- requeue lets
                                                    the item proceed using
                                                    its other readable
                                                    photos / text fallback)

Deliberately EXCLUDED, do not add without a new fix landing first:
  - ebay_draft 'model returned non-JSON' (#1393) -- confirmed 0/95 fixable
    by the merged change; real fix is #1405, not yet built. Requeuing now
    would just re-fail identically.
  - ebay_stage/ebay_upload KeyError('api_key') + ImageLinks (#1400/#1396)
    -- investigation-only, the single affected item (tgw202605051933258)
    is already PUBLISHED/live; requeuing would be redundant.
  - ebay_publish Brand-missing (#1404) -- investigation-only, item already
    self-resolved and is live.
  - ebay_sync offer-400 (#1397) -- these 9 dead-letters predate the code
    that produced that URL (marketplace_id param since removed) and can't
    be reproduced by current code; the queue's own schedule already runs
    fresh cycles, no stale job_id is meaningful to replay.

Same job_id-derived-dedupe + run-once-marker-file pattern as
scripts/requeue_ebay_draft_402_dead_letters.py (#1265). Default is
dry-run; pass --apply to write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.logging import announce_script_run  # noqa: E402
from tgw.queue import state_machine  # noqa: E402

_MARKER_PATH = Path('/opt/TGW/var/run/requeue_deadletter_001_fixed.done.json')

# (queue_name, error_detail LIKE pattern, reason tag)
_TARGETS: List[Tuple[str, str, str]] = [
    ('ebay_stage', '%not a leaf category%', 'non_leaf_category_fixed_1395'),
    ('ebay_upload', '%File dimension limit exceeds%', 'dimension_limit_fixed_1398'),
    ('ebay_upload', '%XML Parse error%', 'xml_parse_fixed_1399'),
    ('ebay_draft', '%image file is truncated%', 'truncated_image_skip_1403'),
    ('ebay_draft', '%broken data stream%', 'truncated_image_skip_1403'),
]


def _load_already_requeued(marker_path: Path) -> Set[str]:
    if not marker_path.exists():
        return set()
    try:
        return set(json.loads(marker_path.read_text(encoding='utf-8')).get('requeued_job_ids', []))
    except (OSError, ValueError):
        return set()


def _save_already_requeued(marker_path: Path, job_ids: Set[str]) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = marker_path.with_suffix('.tmp')
    tmp_path.write_text(json.dumps({'requeued_job_ids': sorted(job_ids)}, indent=2), encoding='utf-8')
    tmp_path.replace(marker_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                       help='Actually requeue (default: dry-run/report only)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Cap the number of jobs requeued this run (0 = no cap)')
    parser.add_argument('--marker', default=str(_MARKER_PATH),
                       help='Path to the run-once marker file (default: %(default)s)')
    args = parser.parse_args()
    marker_path = Path(args.marker)

    announce_script_run(
        'requeue_deadletter_001_fixed.py',
        'bulk-requeue dead-letters whose root cause was fixed by PP-DEADLETTER-001',
        apply=args.apply, limit=args.limit,
    )

    state_machine.init('dbname=state_machine user=tgw', 'production')

    rows: List[Tuple[str, str, dict, int]] = []
    with state_machine._conn() as con:  # noqa: SLF001 — one-shot script, direct query
        with con.cursor() as cur:
            for queue_name, pattern, reason in _TARGETS:
                cur.execute(
                    """
                    SELECT job_id::text, payload_json, max_attempts
                      FROM queue_jobs
                     WHERE state = 'dead_letter' AND queue_name = %s
                       AND error_detail LIKE %s
                     ORDER BY finished_at
                    """,
                    (queue_name, pattern),
                )
                for job_id, payload, max_attempts in cur.fetchall():
                    rows.append((queue_name, reason, job_id, payload, max_attempts))

    if args.limit:
        rows = rows[:args.limit]

    print(f'{len(rows)} dead-letter job(s) matched a fixed-error pattern:')
    by_reason: dict = {}
    for queue_name, reason, *_ in rows:
        by_reason[(queue_name, reason)] = by_reason.get((queue_name, reason), 0) + 1
    for (queue_name, reason), count in sorted(by_reason.items()):
        print(f'  {queue_name} / {reason}: {count}')

    if not args.apply:
        print('\n[DRY-RUN] pass --apply to requeue. Sample:')
        for queue_name, reason, job_id, payload, _ in rows[:8]:
            print(f'  {queue_name} [{reason}] {job_id}: {payload}')
        return 0

    already_requeued = _load_already_requeued(marker_path)
    if already_requeued:
        print(f'{len(already_requeued)} job(s) already requeued by a prior --apply run '
              f'(marker: {marker_path}) — will be skipped.')

    requeued = 0
    skipped = 0
    already_skipped = 0
    for queue_name, reason, job_id, payload, max_attempts in rows:
        if job_id in already_requeued:
            already_skipped += 1
            continue
        payload = dict(payload or {})
        sku = payload.get('sku') or job_id[:8]
        payload['retried_from_job'] = job_id
        payload['bulk_requeue_reason'] = reason
        new_dedupe = f'{queue_name}:{sku}:requeue:{job_id}'
        try:
            state_machine.enqueue_job(
                queue_name=queue_name,
                payload=payload,
                dedupe_key=new_dedupe,
                max_attempts=max_attempts or 3,
            )
            requeued += 1
            already_requeued.add(job_id)
        except Exception as exc:  # noqa: BLE001 — log and keep going
            print(f'WARN: requeue failed for {queue_name}/{sku} ({job_id}): {exc}', file=sys.stderr)
            skipped += 1

    _save_already_requeued(marker_path, already_requeued)

    print(f'\n[APPLIED] requeued={requeued} skipped={skipped} already_done={already_skipped}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
