#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""requeue_deadletter.py — generic, parameterized bulk-requeue of dead-letter
jobs matching an error pattern, for a given queue.

Generalizes `scripts/requeue_ebay_draft_402_dead_letters.py` (#1265) so a
transient-only dead-letter bucket (quota exhaustion, lease expiry, expired
token, waiting-on-another-queue — all conditions that clear themselves with
time, not a code bug) can be verified-and-requeued with one shared tool
instead of a bespoke script per queue (PP-DEADLETTER-001, todo #1402).

**This tool does NOT decide what's transient vs a real bug — that
classification lives in `docs/TGW-Plan-Vault/plan/pp/PP-DEADLETTER-001.md`
and is the caller's responsibility.** Always dry-run first and read the
matched rows before passing --apply, and pick an --error-like pattern
narrow enough to exclude any real-bug rows sharing the same queue (e.g.
ebay_sync's transient "Lease expired"/"token is expired" rows share a queue
with 9 real-bug "400 Client Error... offer" rows that must NOT match).

Same job_id-dedupe + run-once-marker safety as #1265's original script:
  - dedupe_key is job_id-derived (`{queue}:{sku}:requeue:{job_id}`), never a
    fresh timestamp, so a concurrent second invocation racing this one is
    still caught by the active-state partial unique index.
  - a persistent marker file (default keyed by queue+pattern, override with
    --marker) records every job_id already requeued by a completed --apply
    run of this exact (queue, pattern) combination, so a second --apply
    invocation over the same rows does not re-requeue (and in ebay_draft's
    case, re-bill) them once the index-based guard alone would no longer
    catch it (see #1206 / DONE-1206-requeue-402-dedupe-guard.md).

Default is dry-run (prints what would be requeued); pass --apply to write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Set

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.queue import state_machine  # noqa: E402
from tgw.logging import announce_script_run  # noqa: E402

_MARKER_DIR = Path('/opt/TGW/var/run')


def _default_marker_path(queue_name: str, error_like: str) -> Path:
    # Marker is scoped per (queue, pattern) so unrelated buckets never
    # share (and never collide on) a run-once guard.
    digest = hashlib.sha256(f'{queue_name}:{error_like}'.encode('utf-8')).hexdigest()[:16]
    return _MARKER_DIR / f'requeue_deadletter.{queue_name}.{digest}.done.json'


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue', required=True,
                       help='queue_name to match dead-letter rows in (e.g. ebay_legacy_sync)')
    parser.add_argument('--error-like', required=True, dest='error_like',
                       help="SQL LIKE pattern against error_detail (e.g. '%%Lease expired%%'). "
                            'Must be narrow enough to exclude any real-bug rows in the same queue.')
    parser.add_argument('--reason', default='',
                       help='short tag recorded on each requeued payload as bulk_requeue_reason '
                            '(default: derived from --error-like)')
    parser.add_argument('--apply', action='store_true',
                       help='Actually requeue (default: dry-run/report only)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Cap the number of jobs requeued this run (0 = no cap)')
    parser.add_argument('--marker', default=None,
                       help='Path to the run-once marker file '
                            '(default: derived from --queue + --error-like, scoped per pattern)')
    args = parser.parse_args()

    marker_path = Path(args.marker) if args.marker else _default_marker_path(args.queue, args.error_like)
    reason = args.reason or f'bulk_requeue_{args.queue}_{"".join(c if c.isalnum() else "_" for c in args.error_like)[:40]}'

    announce_script_run(
        'requeue_deadletter.py',
        'generic bulk-requeue of dead-letter jobs matching an error pattern',
        queue=args.queue, error_like=args.error_like, apply=args.apply, limit=args.limit,
    )

    state_machine.init('dbname=state_machine user=tgw')

    with state_machine._conn() as con:  # noqa: SLF001 — one-shot script, direct query
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT job_id::text, payload_json, max_attempts
                  FROM queue_jobs
                 WHERE state = 'dead_letter' AND queue_name = %s
                   AND error_detail LIKE %s
                 ORDER BY finished_at
                """,
                (args.queue, args.error_like),
            )
            rows = cur.fetchall()

    if args.limit:
        rows = rows[:args.limit]

    print(f'{len(rows)} dead-letter {args.queue!r} job(s) matched pattern {args.error_like!r}.')

    if not args.apply:
        print('[DRY-RUN] pass --apply to requeue. Sample SKUs:')
        for job_id, payload, _ in rows[:5]:
            print(f'  {job_id}: {payload}')
        return 0

    already_requeued = _load_already_requeued(marker_path)
    if already_requeued:
        print(f'{len(already_requeued)} job(s) already requeued by a prior --apply run of this '
              f'(queue, pattern) (marker: {marker_path}) — will be skipped.')

    requeued = 0
    skipped = 0
    already_skipped = 0
    for job_id, payload, max_attempts in rows:
        if job_id in already_requeued:
            already_skipped += 1
            continue
        payload = dict(payload or {})
        sku = payload.get('sku') or job_id[:8]
        payload['retried_from_job'] = job_id
        payload['bulk_requeue_reason'] = reason
        # Deterministic (job_id-derived, not a timestamp) so a concurrent
        # second invocation racing this one is still caught by the active-
        # state unique index — the marker file above is the durable guard
        # once the first batch has finished and the index no longer helps.
        new_dedupe = f'{args.queue}:{sku}:requeue:{job_id}'
        try:
            state_machine.enqueue_job(
                queue_name=args.queue,
                payload=payload,
                dedupe_key=new_dedupe,
                max_attempts=max_attempts or 3,
            )
            requeued += 1
            already_requeued.add(job_id)
        except Exception as exc:  # noqa: BLE001 — log and keep going
            print(f'WARN: requeue failed for {sku} ({job_id}): {exc}', file=sys.stderr)
            skipped += 1
        if requeued % 200 == 0 and requeued:
            print(f'  ... {requeued}/{len(rows)} requeued so far', flush=True)

    _save_already_requeued(marker_path, already_requeued)

    print(f'\n[APPLIED] requeued={requeued} skipped={skipped} already_done={already_skipped}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
