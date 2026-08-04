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
halts). Old dead_letter rows are left in place (historical record, never
deleted).

Run-once guard (audit#1143 #1206): unlike http_server.py's single-job
requeue_job() endpoint (an intentional operator button-press that should
always succeed on every click), this bulk script processes ~2,582 rows in
one shot and must not silently re-bill them on a second --apply invocation
(operator confusion, an accidental re-run, a cron mistake). A fresh-
timestamp dedupe key would let that happen every time — worse, even a
job_id-derived dedupe key alone isn't enough, because
uq_queue_jobs_dedupe_key_active is a PARTIAL unique index scoped to
non-terminal states only (schema.sql), so it stops re-blocking a dedupe key
the moment the first requeued job reaches a terminal state (succeeded or
dead_letter again) — which, for ~2,582 billed AI-drafting jobs, is very
plausibly hours before anyone would think to re-run this script. The real
guard is the persistent marker file below, recording every job_id already
requeued by a completed --apply run; the dedupe key is additionally made
deterministic (job_id-based, not a timestamp) as defense-in-depth for the
narrower concurrent-run race the partial index does still catch.

Default is dry-run (prints what would be requeued); pass --apply to write.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from tgw.queue import state_machine  # noqa: E402
from tgw.logging import announce_script_run  # noqa: E402

_MARKER_PATH = Path('/opt/TGW/var/run/requeue_ebay_draft_402_dead_letters.done.json')

# Second guard layer, todo #1250: job_id changes on every re-dead-letter (a
# requeued job that fails again gets a brand-new job_id), so the job_id-keyed
# marker above only stops re-billing the SAME failed row twice -- it does NOT
# stop an endlessly-retrying SKU whose requeued job keeps dying and getting
# matched + requeued again on every future run under a fresh job_id. Cap
# total requeue attempts per SKU (persisted in the same marker file) so a
# persistently-failing item stops being auto-requeued after N tries instead
# of looping forever across runs.
DEFAULT_MAX_ATTEMPTS_PER_SKU = 3


def _make_dedupe_key(sku: str, job_id: str) -> str:
    """Deterministic, content-derived dedupe key -- NOT time-based. Same
    (sku, job_id) input always produces the same key, so repeated calls
    (including across process runs) collide on purpose and the active-state
    unique index can do its job. See #1206 / the 07-04/05 storm incident."""
    return f'ebay_draft:{sku}:requeue:{job_id}'


def _load_marker_state(marker_path: Path) -> Tuple[Set[str], Dict[str, int]]:
    """Returns (already_requeued_job_ids, sku_attempt_counts). Tolerates the
    pre-attempt-cap marker format (bare 'requeued_job_ids' list, no counts)
    written by earlier runs of this script."""
    if not marker_path.exists():
        return set(), {}
    try:
        data = json.loads(marker_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return set(), {}
    already = set(data.get('requeued_job_ids', []))
    counts = {str(k): int(v) for k, v in dict(data.get('sku_attempt_counts', {})).items()}
    return already, counts


def _save_marker_state(marker_path: Path, job_ids: Set[str], sku_attempt_counts: Dict[str, int]) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = marker_path.with_suffix('.tmp')
    tmp_path.write_text(json.dumps({
        'requeued_job_ids': sorted(job_ids),
        'sku_attempt_counts': dict(sorted(sku_attempt_counts.items())),
    }, indent=2), encoding='utf-8')
    tmp_path.replace(marker_path)


def _attempt_cap_reached(sku_attempt_counts: Dict[str, int], sku: str, max_attempts_per_sku: int) -> bool:
    """True once a SKU has already been requeued max_attempts_per_sku times
    by this script across all runs -- a persistently-failing item stops
    being auto-requeued instead of looping forever (todo #1250)."""
    if max_attempts_per_sku <= 0:
        return False  # 0 = uncapped, explicit opt-out
    return sku_attempt_counts.get(sku, 0) >= max_attempts_per_sku



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true',
                       help='Actually requeue (default: dry-run/report only)')
    parser.add_argument('--limit', type=int, default=0,
                       help='Cap the number of jobs requeued this run (0 = no cap)')
    parser.add_argument('--marker', default=str(_MARKER_PATH),
                       help='Path to the run-once marker file (default: %(default)s)')
    parser.add_argument('--max-attempts-per-sku', type=int, default=DEFAULT_MAX_ATTEMPTS_PER_SKU,
                       help='Stop auto-requeuing a SKU after this many total requeue '
                            'attempts by this script, across all runs (0 = uncapped; '
                            'default: %(default)s). Prevents an endlessly re-dead-lettering '
                            'item from looping forever (todo #1250).')
    args = parser.parse_args()
    marker_path = Path(args.marker)

    announce_script_run(
        'requeue_ebay_draft_402_dead_letters.py',
        'bulk-requeue ebay_draft dead-letters matching a 402 error pattern',
        apply=args.apply, limit=args.limit, max_attempts_per_sku=args.max_attempts_per_sku,
    )

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

    already_requeued, sku_attempt_counts = _load_marker_state(marker_path)
    if already_requeued:
        print(f'{len(already_requeued)} job(s) already requeued by a prior --apply run '
              f'(marker: {marker_path}) — will be skipped.')

    requeued = 0
    skipped = 0
    already_skipped = 0
    capped_skipped = 0
    for job_id, payload, max_attempts in rows:
        if job_id in already_requeued:
            already_skipped += 1
            continue
        payload = dict(payload or {})
        sku = payload.get('sku') or job_id[:8]
        if _attempt_cap_reached(sku_attempt_counts, sku, args.max_attempts_per_sku):
            print(f'WARN: {sku} has already been requeued '
                  f'{sku_attempt_counts.get(sku, 0)}/{args.max_attempts_per_sku} times by this '
                  f'script -- skipping job {job_id} (attempt cap, todo #1250).', file=sys.stderr)
            capped_skipped += 1
            continue
        payload['retried_from_job'] = job_id
        payload['bulk_requeue_reason'] = 'openrouter_402_2026-07-02_resolved'
        # Deterministic (job_id-derived, not a timestamp) so a concurrent
        # second invocation racing this one is still caught by the active-
        # state unique index — the marker file above is the durable guard
        # once the first batch has finished and the index no longer helps.
        new_dedupe = _make_dedupe_key(sku, job_id)
        try:
            state_machine.enqueue_job(
                queue_name='ebay_draft',
                payload=payload,
                dedupe_key=new_dedupe,
                max_attempts=max_attempts or 3,
            )
            requeued += 1
            already_requeued.add(job_id)
            sku_attempt_counts[sku] = sku_attempt_counts.get(sku, 0) + 1
        except Exception as exc:  # noqa: BLE001 — log and keep going
            print(f'WARN: requeue failed for {sku} ({job_id}): {exc}', file=sys.stderr)
            skipped += 1
        if requeued % 200 == 0 and requeued:
            print(f'  ... {requeued}/{len(rows)} requeued so far', flush=True)

    _save_marker_state(marker_path, already_requeued, sku_attempt_counts)

    print(f'\n[APPLIED] requeued={requeued} skipped={skipped} already_done={already_skipped} '
          f'attempt_cap_skipped={capped_skipped}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
