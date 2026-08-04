"""PP-DEADLETTER-001 / todo #1402 — generic, parameterized version of
scripts/requeue_ebay_draft_402_dead_letters.py (#1265), so the transient-
only dead-letter buckets (ebay_legacy_sync, ebay_sync, ebay_sku_migrate,
ebay_publish quota/lease/token-expired rows) can be verified-and-requeued
without a bespoke script per queue.

Preserves the exact job_id-dedupe + run-once-marker safety pattern #1265
established (regression class: #1206 — a fresh-timestamp dedupe with no
run-once guard silently re-billed/re-ran the same dead-letter rows on a
second --apply invocation).

All state_machine DB calls are mocked — tests pass completely offline, no
real Postgres connection made.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'requeue_deadletter.py'
_spec = importlib.util.spec_from_file_location('requeue_deadletter', _SCRIPT_PATH)
requeue_mod = importlib.util.module_from_spec(_spec)
sys.modules['requeue_deadletter'] = requeue_mod
_spec.loader.exec_module(requeue_mod)


def _rows(n):
    return [(f'job-{i}', {'sku': f'tgw{i}'}, 3) for i in range(n)]


@contextmanager
def _fake_conn(rows):
    con = MagicMock()
    cur = MagicMock()
    cur.fetchall.return_value = rows
    con.cursor.return_value.__enter__.return_value = cur
    yield con


def _mock_common(monkeypatch, rows, enqueue_side_effect=None):
    monkeypatch.setattr(requeue_mod.state_machine, 'init', lambda dsn: None)
    monkeypatch.setattr(requeue_mod.state_machine, '_conn', lambda: _fake_conn(rows))
    calls = []

    def _fake_enqueue(**kwargs):
        calls.append(kwargs)
        if enqueue_side_effect:
            enqueue_side_effect(kwargs)
        return 'new-job-id'

    monkeypatch.setattr(requeue_mod.state_machine, 'enqueue_job', _fake_enqueue)
    return calls


_BASE_ARGV = ['prog', '--queue', 'ebay_legacy_sync', '--error-like', '%Lease expired%']


class TestGenericQueueAndPattern:
    def test_apply_requeues_all_rows_with_queue_scoped_dedupe(self, tmp_path, monkeypatch):
        rows = _rows(3)
        calls = _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls) == 3
        assert calls[0]['queue_name'] == 'ebay_legacy_sync'
        assert calls[0]['dedupe_key'] == 'ebay_legacy_sync:tgw0:requeue:job-0'
        assert marker.exists()
        recorded = json.loads(marker.read_text())['requeued_job_ids']
        assert set(recorded) == {'job-0', 'job-1', 'job-2'}

    def test_reason_defaults_from_error_like_when_not_given(self, tmp_path, monkeypatch):
        rows = _rows(1)
        calls = _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert 'ebay_legacy_sync' in calls[0]['payload']['bulk_requeue_reason']

    def test_explicit_reason_is_used_verbatim(self, tmp_path, monkeypatch):
        rows = _rows(1)
        calls = _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(
            sys, 'argv',
            _BASE_ARGV + ['--apply', '--marker', str(marker), '--reason', 'transient_lease_cleared'],
        )
        requeue_mod.main()

        assert calls[0]['payload']['bulk_requeue_reason'] == 'transient_lease_cleared'


class TestRunOnceGuard:
    def test_second_apply_run_skips_already_requeued_rows(self, tmp_path, monkeypatch):
        # Regression for #1206's class: a second --apply invocation over
        # the SAME dead-letter rows must not re-requeue them.
        rows = _rows(3)
        marker = tmp_path / 'marker.json'

        calls_first = _mock_common(monkeypatch, rows)
        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()
        assert len(calls_first) == 3

        calls_second = _mock_common(monkeypatch, rows)
        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls_second) == 0

    def test_new_dead_letter_rows_still_requeued_after_a_prior_run(self, tmp_path, monkeypatch):
        marker = tmp_path / 'marker.json'

        first_rows = _rows(2)  # job-0, job-1
        calls_first = _mock_common(monkeypatch, first_rows)
        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()
        assert len(calls_first) == 2

        mixed_rows = _rows(2) + [('job-2', {'sku': 'tgw2'}, 3)]  # job-0, job-1 old; job-2 new
        calls_second = _mock_common(monkeypatch, mixed_rows)
        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls_second) == 1
        assert calls_second[0]['dedupe_key'] == 'ebay_legacy_sync:tgw2:requeue:job-2'

    def test_dedupe_key_is_deterministic_not_timestamp_based(self, tmp_path, monkeypatch):
        rows = _rows(1)
        calls = _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert calls[0]['dedupe_key'] == 'ebay_legacy_sync:tgw0:requeue:job-0'

    def test_dry_run_does_not_write_marker(self, tmp_path, monkeypatch):
        rows = _rows(2)
        _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--marker', str(marker)])
        requeue_mod.main()

        assert not marker.exists()

    def test_failed_enqueue_is_not_recorded_in_marker(self, tmp_path, monkeypatch):
        rows = _rows(2)

        def _fail_on_job1(kwargs):
            if kwargs['dedupe_key'] == 'ebay_legacy_sync:tgw1:requeue:job-1':
                raise RuntimeError('boom')

        calls = _mock_common(monkeypatch, rows, enqueue_side_effect=_fail_on_job1)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', _BASE_ARGV + ['--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls) == 2
        recorded = json.loads(marker.read_text())['requeued_job_ids']
        assert recorded == ['job-0']


class TestMarkerScoping:
    def test_default_marker_path_differs_by_queue_and_pattern(self):
        p1 = requeue_mod._default_marker_path('ebay_sync', '%Lease expired%')
        p2 = requeue_mod._default_marker_path('ebay_sync', '%token is expired%')
        p3 = requeue_mod._default_marker_path('ebay_sku_migrate', '%Lease expired%')

        assert p1 != p2
        assert p1 != p3
        assert 'ebay_sync' in str(p1)
        assert 'ebay_sku_migrate' in str(p3)

    def test_default_marker_path_is_stable_for_same_args(self):
        p1 = requeue_mod._default_marker_path('ebay_publish', '%waiting for ebay_stage%')
        p2 = requeue_mod._default_marker_path('ebay_publish', '%waiting for ebay_stage%')
        assert p1 == p2
