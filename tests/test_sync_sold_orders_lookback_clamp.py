"""audit#1143 #1153: sync_sold_orders() fed GetOrders a scan_from far
outside its rolling 90-day CreateTimeFrom limit on a first sync (a since-
removed SOLD_INITIAL_LOOKBACK_DAYS=365 constant), so the very first call
failed with "Invalid dates in CreateTimeFrom -- orders older than 90 days
cannot be retrieved." Chunking the window narrower didn't help since the
START date was already too old -- scan_from itself must be clamped.

All eBay API calls are mocked -- tests pass completely offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import tgw.ebay.pull as pull_mod


def _cfg(gap_log_path=None):
    if gap_log_path is None:
        return {}
    return {'raw': {'sold_order_gap_log': str(gap_log_path)}}


def test_first_sync_clamps_scan_from_within_90_days(monkeypatch, tmp_path):
    """No state file (first-ever sync): scan_from would default to
    now - 365 days, which must be clamped to within _MAX_ORDER_LOOKBACK_DAYS."""
    calls = []

    def _fake_get_orders(cfg, window_start, window_end, marketplace_id=None):
        calls.append((window_start, window_end))
        return iter([])

    monkeypatch.setattr(pull_mod, 'get_orders', _fake_get_orders)

    state_path = tmp_path / 'sold-sync-state.json'
    pull_mod.sync_sold_orders(_cfg(), {}, 'now', state_path)

    assert len(calls) == 1
    window_start, window_end = calls[0]
    now = datetime.now(timezone.utc)
    earliest_allowed = now - timedelta(days=pull_mod._MAX_ORDER_LOOKBACK_DAYS)
    # window_start must never be earlier than the clamp boundary
    assert window_start >= earliest_allowed - timedelta(seconds=5)
    # and must NOT still be close to the unclamped 365-day default
    assert window_start > now - timedelta(days=100)


def test_stale_incremental_resume_also_clamped(monkeypatch, tmp_path):
    """A resume from a state file whose last_synced_at is itself older than
    the 90-day limit (e.g. sync hasn't run in months) must also be clamped,
    not just the first-sync path."""
    calls = []

    def _fake_get_orders(cfg, window_start, window_end, marketplace_id=None):
        calls.append((window_start, window_end))
        return iter([])

    monkeypatch.setattr(pull_mod, 'get_orders', _fake_get_orders)

    state_path = tmp_path / 'sold-sync-state.json'
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    state_path.write_text('{"last_synced_at": "%s"}' % stale_ts, encoding='utf-8')

    gap_log = tmp_path / 'sold-order-history-gaps.jsonl'
    pull_mod.sync_sold_orders(_cfg(gap_log), {}, 'now', state_path)

    assert len(calls) == 1
    window_start, _ = calls[0]
    now = datetime.now(timezone.utc)
    earliest_allowed = now - timedelta(days=pull_mod._MAX_ORDER_LOOKBACK_DAYS)
    assert window_start >= earliest_allowed - timedelta(seconds=5)


# ---------------------------------------------------------------------------
# Durable finding on a real (incremental-resume) history gap (todo #1270,
# code-review follow-up: this used to be logged at INFO only, not recorded
# anywhere durable/queryable per invariant C11)
# ---------------------------------------------------------------------------

def test_stale_resume_gap_recorded_as_durable_finding(monkeypatch, tmp_path):
    monkeypatch.setattr(pull_mod, 'get_orders', lambda *a, **k: iter([]))

    state_path = tmp_path / 'sold-sync-state.json'
    stale_ts = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    state_path.write_text('{"last_synced_at": "%s"}' % stale_ts, encoding='utf-8')

    gap_log = tmp_path / 'sold-order-history-gaps.jsonl'
    pull_mod.sync_sold_orders(_cfg(gap_log), {}, 'now', state_path)

    assert gap_log.exists()
    lines = gap_log.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record['gap_days'] > 100  # 200-day-stale resume clamped to 89 days
    assert 'requested_from' in record
    assert 'clamped_from' in record


def test_first_sync_does_not_record_a_gap(monkeypatch, tmp_path):
    """First-ever sync clamping to the 89-day ceiling is expected/routine
    -- there was never a previously-tracked sync point to lose, so this is
    NOT an incident and must not be recorded as one."""
    monkeypatch.setattr(pull_mod, 'get_orders', lambda *a, **k: iter([]))

    state_path = tmp_path / 'sold-sync-state.json'  # does not exist -> first sync
    gap_log = tmp_path / 'sold-order-history-gaps.jsonl'
    pull_mod.sync_sold_orders(_cfg(gap_log), {}, 'now', state_path)

    assert not gap_log.exists()


def test_recent_resume_does_not_record_a_gap(monkeypatch, tmp_path):
    monkeypatch.setattr(pull_mod, 'get_orders', lambda *a, **k: iter([]))

    state_path = tmp_path / 'sold-sync-state.json'
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    state_path.write_text('{"last_synced_at": "%s"}' % recent_ts, encoding='utf-8')

    gap_log = tmp_path / 'sold-order-history-gaps.jsonl'
    pull_mod.sync_sold_orders(_cfg(gap_log), {}, 'now', state_path)

    assert not gap_log.exists()


def test_recent_incremental_resume_not_clamped(monkeypatch, tmp_path):
    """A normal, recent resume (last_synced_at a few hours ago) must be
    unaffected by the clamp -- it should query from close to that time,
    not get pulled forward to the 90-day boundary."""
    calls = []

    def _fake_get_orders(cfg, window_start, window_end, marketplace_id=None):
        calls.append((window_start, window_end))
        return iter([])

    monkeypatch.setattr(pull_mod, 'get_orders', _fake_get_orders)

    state_path = tmp_path / 'sold-sync-state.json'
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    state_path.write_text('{"last_synced_at": "%s"}' % recent_ts, encoding='utf-8')

    pull_mod.sync_sold_orders(_cfg(), {}, 'now', state_path)

    assert len(calls) == 1
    window_start, _ = calls[0]
    # should be ~8 hours ago (6h + the 2h rewind), nowhere near the 89-day clamp
    assert window_start > datetime.now(timezone.utc) - timedelta(days=1)


def test_failed_sold_mark_does_not_advance_incremental_cursor(
    monkeypatch, tmp_path,
):
    """A local write failure must leave the order inside the next scan.

    Production found eight completed orders, failed every ItemData write on
    an HTTP timeout, and nevertheless advanced ``last_synced_at``.  The next
    run started after those orders and permanently presented sold inventory
    as an unpublished offer.
    """
    order = {
        'order_id': '22-14981-45837',
        'buyer': 'buyer',
        'transactions': [{
            'listing_id': '227446147105',
            'sale_price': 33.99,
            'quantity': 1,
            'sale_date': '2026-08-08T01:24:47.000Z',
        }],
    }
    monkeypatch.setattr(
        pull_mod, 'get_orders', lambda *args, **kwargs: iter([order]),
    )
    monkeypatch.setattr(
        pull_mod, 'mark_item_sold',
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError('write timed out')),
    )

    item_path = tmp_path / 'tgw1.json'
    item_path.write_text('{}', encoding='utf-8')
    state_path = tmp_path / 'sold-sync-state.json'
    original_cursor = (
        datetime.now(timezone.utc) - timedelta(hours=6)
    ).isoformat()
    state_path.write_text(
        json.dumps({'last_synced_at': original_cursor}), encoding='utf-8',
    )

    stats = pull_mod.sync_sold_orders(
        _cfg(), {'227446147105': item_path}, 'now', state_path,
    )

    assert stats == {'orders_fetched': 1, 'sold_marked': 0, 'errors': 1}
    assert json.loads(state_path.read_text(encoding='utf-8')) == {
        'last_synced_at': original_cursor,
    }
