#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""requeue_ebay_draft_402_dead_letters.py — bulk-requeue the ebay_draft
dead-letters caused by the 2026-07-02 OpenRouter billing gap.

2,582 of 2,689 ebay_draft dead-letters failed with "402 Payment Required"
from OpenRouter — not a logic bug. ebay_draft's primary provider is
google_direct (free tier); OpenRouter is only the fallback on a Google
failure. Dave confirmed 2026-07-04 that OpenRouter has had credits and has
seen little use since. "Payment required" is deliberately NOT in
worker_base._TRANSIENT_ERRORS (auto-retrying a billing failure forever
would hide a real problem from the operator) — this one-time bulk requeue
is the correct unblock now that the underlying cause is confirmed resolved.

Background job (no origin='operator' stamp — this is a bulk batch, not an
operator button-press; runs in the background quota context per invariant
C10, so it correctly yields to any interactive work and respects quota
halts). Each requeue gets a fresh dedupe key, mirroring http_server.py's
single-job requeue_job() endpoint. Old dead_letter rows are left in place
(historical record, never deleted).

Default is dry-run (prints what would be requeued); pass --apply to write.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.queue import state_machine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                       help='Actually requeue (default: dry-run/report only)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Cap the number of jobs requeued this run (0 = no cap)')
    args = parser.parse_args()

    state_machine.init('dbname=state_machine user=tgw')

    with state_machine._conn() as con:  # noqa: SLF001 — one-shot script, direct query
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT job_id::text, payload_json, max_attempts
                  FROM queue_jobs
                 WHERE state = 'dead_letter' AND queue_name = 'ebay_draft'
                   AND error_detail LIKE %s
                 ORDER BY finished_at
                """,
                ('%402 Client Error%',),
            )
            rows = cur.fetchall()

    if args.limit:
        rows = rows[:args.limit]

    print(f'{len(rows)} dead-letter ebay_draft job(s) matched the 402 pattern.')

    if not args.apply:
        print('[DRY-RUN] pass --apply to requeue. Sample SKUs:')
        for job_id, payload, _ in rows[:5]:
            print(f'  {job_id}: {payload}')
        return 0

    requeued = 0
    skipped = 0
    for job_id, payload, max_attempts in rows:
        payload = dict(payload or {})
        sku = payload.get('sku') or job_id[:8]
        payload['retried_from_job'] = job_id
        payload['bulk_requeue_reason'] = 'openrouter_402_2026-07-02_resolved'
        new_dedupe = f'ebay_draft:{sku}:requeue:{int(time.time() * 1000)}'
        try:
            state_machine.enqueue_job(
                queue_name='ebay_draft',
                payload=payload,
                dedupe_key=new_dedupe,
                max_attempts=max_attempts or 3,
            )
            requeued += 1
        except Exception as exc:  # noqa: BLE001 — log and keep going
            print(f'WARN: requeue failed for {sku} ({job_id}): {exc}', file=sys.stderr)
            skipped += 1
        if requeued % 200 == 0 and requeued:
            print(f'  ... {requeued}/{len(rows)} requeued so far', flush=True)

    print(f'\n[APPLIED] requeued={requeued} skipped={skipped}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
