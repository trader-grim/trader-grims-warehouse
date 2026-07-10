"""audit#1143 #1206 — requeue_ebay_draft_402_dead_letters.py's dedupe key
used a fresh timestamp every invocation with no run-once guard: re-running
--apply requeued the same dead-letter rows again, burning a second full
round of billed AI-drafting cost with no flag.

Fixed: a persistent marker file records every job_id already requeued by a
completed --apply run, checked (and skipped) on subsequent runs; the dedupe
key is also made deterministic (job_id-derived) as defense-in-depth for a
narrower concurrent-run race the active-state unique index still catches.

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

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'requeue_ebay_draft_402_dead_letters.py'
_spec = importlib.util.spec_from_file_location('requeue_ebay_draft_402_dead_letters', _SCRIPT_PATH)
requeue_mod = importlib.util.module_from_spec(_spec)
sys.modules['requeue_ebay_draft_402_dead_letters'] = requeue_mod
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


class TestRunOnceGuard:
    def test_apply_requeues_all_rows_and_writes_marker(self, tmp_path, monkeypatch):
        rows = _rows(3)
        calls = _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls) == 3
        assert marker.exists()
        recorded = json.loads(marker.read_text())['requeued_job_ids']
        assert set(recorded) == {'job-0', 'job-1', 'job-2'}

    def test_second_apply_run_skips_already_requeued_rows(self, tmp_path, monkeypatch):
        # Regression for #1206: a second --apply invocation over the SAME
        # dead-letter rows must not re-requeue (and re-bill) them.
        rows = _rows(3)
        marker = tmp_path / 'marker.json'

        calls_first = _mock_common(monkeypatch, rows)
        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()
        assert len(calls_first) == 3

        calls_second = _mock_common(monkeypatch, rows)
        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls_second) == 0

    def test_new_dead_letter_rows_still_requeued_after_a_prior_run(self, tmp_path, monkeypatch):
        # A second run with genuinely NEW dead-letter rows (not in the
        # marker) must still requeue those — the guard is per-job_id, not
        # a blanket "never run again".
        marker = tmp_path / 'marker.json'

        first_rows = _rows(2)  # job-0, job-1
        calls_first = _mock_common(monkeypatch, first_rows)
        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()
        assert len(calls_first) == 2

        mixed_rows = _rows(2) + [('job-2', {'sku': 'tgw2'}, 3)]  # job-0, job-1 old; job-2 new
        calls_second = _mock_common(monkeypatch, mixed_rows)
        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls_second) == 1
        assert calls_second[0]['dedupe_key'] == 'ebay_draft:tgw2:requeue:job-2'

    def test_dedupe_key_is_deterministic_not_timestamp_based(self, tmp_path, monkeypatch):
        rows = _rows(1)
        calls = _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert calls[0]['dedupe_key'] == 'ebay_draft:tgw0:requeue:job-0'

    def test_dry_run_does_not_write_marker(self, tmp_path, monkeypatch):
        rows = _rows(2)
        _mock_common(monkeypatch, rows)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', ['prog', '--marker', str(marker)])
        requeue_mod.main()

        assert not marker.exists()

    def test_failed_enqueue_is_not_recorded_in_marker(self, tmp_path, monkeypatch):
        rows = _rows(2)

        def _fail_on_job1(kwargs):
            if kwargs['dedupe_key'] == 'ebay_draft:tgw1:requeue:job-1':
                raise RuntimeError('boom')

        calls = _mock_common(monkeypatch, rows, enqueue_side_effect=_fail_on_job1)
        marker = tmp_path / 'marker.json'

        monkeypatch.setattr(sys, 'argv', ['prog', '--apply', '--marker', str(marker)])
        requeue_mod.main()

        assert len(calls) == 2
        recorded = json.loads(marker.read_text())['requeued_job_ids']
        assert recorded == ['job-0']
