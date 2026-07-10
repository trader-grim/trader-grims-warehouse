"""Tests for http_server._local_ts (session 41).

Postgres's session timezone is GMT, so every queue_jobs timestamp comes back
as UTC. Every timestamp display site in http_server.py used to truncate the
raw ISO string (e.g. str(ts)[:16]), which strips the +00:00 offset and
displays a bare value that looks like local time but is actually UTC —
confirmed live: an operator read a job's timestamp as several hours in the
future. _local_ts must always convert to America/Los_Angeles for display.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tgw.http_server import _local_ts


def test_empty_input_returns_empty_string():
    assert _local_ts(None) == ""
    assert _local_ts("") == ""


def test_utc_iso_string_converts_to_pacific():
    # 2026-07-02T18:19:24+00:00 is 11:19 PDT (UTC-7 in summer), not 18:19.
    result = _local_ts("2026-07-02T18:19:24+00:00")
    assert result == "2026-07-02 11:19"


def test_naive_iso_string_assumed_utc():
    # No offset at all (naive) — must still be treated as UTC, not local.
    result = _local_ts("2026-07-02T18:19:24")
    assert result == "2026-07-02 11:19"


def test_datetime_object_input():
    dt = datetime(2026, 7, 2, 18, 19, 24, tzinfo=timezone.utc)
    assert _local_ts(dt) == "2026-07-02 11:19"


def test_winter_date_uses_pst_not_pdt():
    # January is PST (UTC-8), not PDT (UTC-7) — must follow DST correctly.
    result = _local_ts("2026-01-15T20:00:00+00:00")
    assert result == "2026-01-15 12:00"


def test_unparseable_string_falls_back_to_truncation():
    assert _local_ts("not-a-timestamp") == "not-a-timestamp"[:16]
