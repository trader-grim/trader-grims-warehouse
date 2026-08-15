"""Read-only mixed-version inventory for targeted eBay sync queue rows."""
from __future__ import annotations

import json
from contextlib import nullcontext

from tgw.ebay.sync import classify_targeted_sync_payload
from tgw.queue import state_machine


def targeted_sync_queue_inventory(*, connection=None) -> dict:
    context = nullcontext(connection) if connection is not None else state_machine._conn()
    with context as con, con.cursor() as cur:
        cur.execute(
            """SELECT job_id::text, state, payload_json FROM queue_jobs
                 WHERE queue_name='ebay_sync'
                   AND state IN
                       ('queued','leased','running','retry_wait','failed','dead_letter','cancelled')
                 ORDER BY created_at, job_id"""
        )
        rows = cur.fetchall()
    counts = {"periodic": 0, "legacy": 0, "governed": 0, "ambiguous": 0}
    jobs = []
    for job_id, state, payload in rows:
        shape = classify_targeted_sync_payload(payload)
        counts[shape] += 1
        jobs.append({"job_id": job_id, "state": state, "shape": shape})
    return {"counts": counts, "jobs": jobs}


def main() -> int:
    print(json.dumps(targeted_sync_queue_inventory(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
