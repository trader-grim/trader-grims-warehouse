"""Invariant C7 (docs/invariants.md) — the reprice schedule is computed at
publish from comps + config, never raises across stages, and degrades to
null prices (not exceptions) when price data is missing.

_build_reprice_schedule is a pure function (takes `now`), so these tests run
fully offline.
"""

from datetime import datetime, timedelta, timezone

from tgw.ebay.pricing import to_99
from tgw.workers.ebay_publish import _build_reprice_schedule

STAGES = [
    {'days': 0,  'percentile': 'max', 'label': 'launch'},
    {'days': 3,  'percentile': 'p75', 'label': 'retail'},
    {'days': 17, 'percentile': 'p25', 'label': 'move'},
]
COMPS = {'count': 5, 'min': 5.0, 'p25': 10.0, 'median': 15.0,
         'p75': 20.0, 'max': 30.0}
NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def test_stage_prices_from_percentiles():
    schedule = _build_reprice_schedule(STAGES, COMPS, '', {}, now=NOW)
    assert [s['price'] for s in schedule] == [to_99(30.0), 20.0, 10.0]


def test_launch_is_99_later_stages_plain_rounded():
    schedule = _build_reprice_schedule(STAGES, COMPS, '', {}, now=NOW)
    assert schedule[0]['price'] == 30.99
    assert schedule[1]['price'] == 20.0   # not .99-rounded — markdown stages


def test_due_dates_offset_from_now_and_done_at_unset():
    schedule = _build_reprice_schedule(STAGES, COMPS, '', {}, now=NOW)
    assert [s['due_at'] for s in schedule] == [
        NOW.isoformat(),
        (NOW + timedelta(days=3)).isoformat(),
        (NOW + timedelta(days=17)).isoformat(),
    ]
    assert all(s['done_at'] is None for s in schedule)


def test_prices_never_increase_across_stages():
    # Supports invariant C5: the schedule the reducer executes must itself be
    # non-increasing whenever the comps are well-ordered (p25 <= p75 <= max).
    schedule = _build_reprice_schedule(STAGES, COMPS, '', {}, now=NOW)
    prices = [s['price'] for s in schedule]
    assert prices == sorted(prices, reverse=True)


def test_no_comps_no_default_yields_null_prices_not_errors():
    schedule = _build_reprice_schedule(STAGES, {}, '99999', {}, now=NOW)
    assert [s['price'] for s in schedule] == [None, None, None]
    assert len(schedule) == 3   # structure intact so the reducer can skip cleanly


def test_category_default_fallback_fills_all_stages():
    schedule = _build_reprice_schedule(STAGES, {}, '99999', {'99999': 8.0}, now=NOW)
    assert schedule[0]['price'] == to_99(8.0)
    assert schedule[1]['price'] == 8.0
    assert schedule[2]['price'] == 8.0
