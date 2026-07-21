"""todo #1618 / PP-STATEMACHINE-001 — live-Postgres regression coverage for
the debounce self-collision bug.

Every other state_machine test in this suite mocks psycopg2 (see
test_statemachine_manifest.py's module docstring) — deliberately, so the
suite runs without a real database. That convention is not enough to catch
*this* bug: it's a real Postgres partial-unique-index/ON-CONFLICT-arbiter
interaction (verified live against PostgreSQL 17.10 while building this fix
— see the #1618 result manifest for the full investigation trail), which a
mocked cursor cannot reproduce. This module is the live-DB proof; it is
gated to SKIP cleanly (not fail) if no reachable test database is
configured, so `pytest -q` still passes offline everywhere else.

To run these tests, point TGW_TEST_STATE_MACHINE_DSN at a throwaway
Postgres database (never production `state_machine`) with schema.sql
already applied, e.g.:

    createdb state_machine_test  # as a role with CREATEDB
    psql state_machine_test -f src/tgw/queue/schema.sql
    export TGW_TEST_STATE_MACHINE_DSN='dbname=state_machine_test user=tgw'
    pytest tests/test_debounce_selfcollision_live.py -q

Every test here starts by truncating queue_jobs — do NOT point this at any
database that holds data you care about.
"""

from __future__ import annotations

import os
import time

import psycopg2
import pytest

_DSN = os.environ.get('TGW_TEST_STATE_MACHINE_DSN', 'dbname=state_machine_test user=tgw')


def _live_db_available() -> bool:
    try:
        con = psycopg2.connect(_DSN)
        con.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _live_db_available(),
    reason=(
        f'no reachable Postgres test DB at {_DSN!r} — set '
        'TGW_TEST_STATE_MACHINE_DSN to a throwaway DB with schema.sql '
        'applied to run this live regression coverage'
    ),
)


@pytest.fixture(autouse=True)
def _clean_db():
    con = psycopg2.connect(_DSN)
    try:
        with con.cursor() as cur:
            cur.execute('TRUNCATE queue_jobs CASCADE')
        con.commit()
    finally:
        con.close()
    yield


@pytest.fixture(autouse=True)
def _init_state_machine():
    from tgw.queue import state_machine as sm
    sm.init(_DSN)
    yield


def _row(dedupe_key):
    con = psycopg2.connect(_DSN)
    try:
        with con.cursor() as cur:
            cur.execute(
                'SELECT job_id::text, state, not_before FROM queue_jobs '
                'WHERE dedupe_key = %s ORDER BY created_at',
                (dedupe_key,),
            )
            return cur.fetchall()
    finally:
        con.close()


def _all_rows_for_queue(queue_name):
    con = psycopg2.connect(_DSN)
    try:
        with con.cursor() as cur:
            cur.execute(
                'SELECT job_id::text, state, not_before, dedupe_key '
                'FROM queue_jobs WHERE queue_name = %s ORDER BY created_at',
                (queue_name,),
            )
            return cur.fetchall()
    finally:
        con.close()


def test_self_reschedule_while_running_creates_distinct_row():
    """The exact live incident: a worker calls enqueue_job(debounce=True,
    dedupe_key=<its own key>) from inside its own handle() while its own
    job is still 'running'. Must return a DIFFERENT job_id, must leave a
    genuinely 'queued' fresh row, and mark_succeeded() on the original must
    not affect the fresh row at all."""
    from tgw.queue import state_machine as sm

    jid1 = sm.enqueue_job(
        queue_name='test_token_refresh',
        payload={'reason': 'startup'},
        dedupe_key='test_token_refresh:pending',
        debounce=True,
    )
    claimed = sm.claim_queue_jobs('test_token_refresh', 'worker-1', limit=1)
    assert len(claimed) == 1 and claimed[0]['job_id'] == jid1
    sm.mark_running(jid1, 'worker-1')

    future_not_before = time.time() + 3600
    jid2 = sm.enqueue_job(
        queue_name='test_token_refresh',
        payload={'reason': 'self-reschedule'},
        dedupe_key='test_token_refresh:pending',
        not_before=future_not_before,
        debounce=True,
    )

    # Core assertion — this is what the original bug got wrong.
    assert jid2 != jid1, (
        'debounce self-reschedule returned the SAME job_id as the '
        'in-flight running job — this is the #1618 self-collision bug'
    )

    rows = _all_rows_for_queue('test_token_refresh')
    by_id = {r[0]: r for r in rows}
    assert by_id[jid1][1] == 'running'
    assert by_id[jid2][1] == 'queued'

    # mark_succeeded() on the original must not touch the fresh row.
    sm.mark_succeeded(jid1, 'worker-1')
    rows_after = _all_rows_for_queue('test_token_refresh')
    by_id_after = {r[0]: r for r in rows_after}
    assert by_id_after[jid1][1] == 'succeeded'
    assert by_id_after[jid2][1] == 'queued', (
        'fresh reschedule row was corrupted/finalized by mark_succeeded() '
        'on the unrelated original job — orphaned exactly like the live '
        'incident'
    )
    # not_before on the fresh row must be the value we asked for, not lost.
    got_nb = by_id_after[jid2][2]
    assert abs(got_nb.timestamp() - future_not_before) < 2


def test_debounce_coalescing_still_works_when_genuinely_pending():
    """The pre-existing, correct behavior (e.g. catalog_rebuild:pending
    coalescing a burst of writes) must be unchanged: two debounce calls
    while the only existing row is genuinely 'queued' (not started) must
    collapse onto ONE row via GREATEST(not_before, ...)."""
    from tgw.queue import state_machine as sm

    jid1 = sm.enqueue_job(
        queue_name='test_catalog_rebuild',
        payload={'reason': 'write1'},
        dedupe_key='test_catalog_rebuild:pending',
        not_before=100.0,
        debounce=True,
    )
    jid2 = sm.enqueue_job(
        queue_name='test_catalog_rebuild',
        payload={'reason': 'write2'},
        dedupe_key='test_catalog_rebuild:pending',
        not_before=200.0,
        debounce=True,
    )

    assert jid1 == jid2, 'two coalescing debounce calls must collapse onto one row'
    rows = _row('test_catalog_rebuild:pending')
    assert len(rows) == 1
    job_id, state, not_before = rows[0]
    assert state == 'queued'
    assert not_before.timestamp() == 200.0  # GREATEST pushed forward, not overwritten


def test_plain_reject_insert_still_blocked_by_active_row():
    """Non-debounce, non-supersede reject semantics (e.g. ebay_stage:{sku})
    must be completely unaffected by the #1618 fix: a second plain enqueue
    while one is actively running (or queued) under the same dedupe_key
    must still raise a unique-violation, never silently create a
    duplicate."""
    from tgw.queue import state_machine as sm

    jid1 = sm.enqueue_job(
        queue_name='test_ebay_stage',
        payload={'sku': 'tgwLIVE1'},
        entity_type='item',
        entity_id='tgwLIVE1',
        dedupe_key='test_ebay_stage:tgwLIVE1',
    )
    sm.claim_queue_jobs('test_ebay_stage', 'worker-2', limit=1)
    sm.mark_running(jid1, 'worker-2')

    with pytest.raises(psycopg2.errors.UniqueViolation):
        sm.enqueue_job(
            queue_name='test_ebay_stage',
            payload={'sku': 'tgwLIVE1'},
            entity_type='item',
            entity_id='tgwLIVE1',
            dedupe_key='test_ebay_stage:tgwLIVE1',
        )
