"""audit#1143 #1153: sync_sold_orders() fed GetOrders a scan_from far
outside its rolling 90-day CreateTimeFrom limit on a first sync (a since-
removed SOLD_INITIAL_LOOKBACK_DAYS=365 constant), so the very first call
failed with "Invalid dates in CreateTimeFrom -- orders older than 90 days
cannot be retrieved." Chunking the window narrower didn't help since the
START date was already too old -- scan_from itself must be clamped.

All eBay API calls are mocked -- tests pass completely offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import tgw.ebay.pull as pull_mod


def _cfg():
    return {}


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

    pull_mod.sync_sold_orders(_cfg(), {}, 'now', state_path)

    assert len(calls) == 1
    window_start, _ = calls[0]
    now = datetime.now(timezone.utc)
    earliest_allowed = now - timedelta(days=pull_mod._MAX_ORDER_LOOKBACK_DAYS)
    assert window_start >= earliest_allowed - timedelta(seconds=5)


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
