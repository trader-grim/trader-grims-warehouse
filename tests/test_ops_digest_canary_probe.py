"""PP-PHOTOSYNC-001 P8 canary probe (todo #1124) — ops-digest wiring.

Covers _canary_probe_summary() reading the status file
scripts/photosync_canary_probe.py writes, and render_text's rendering of
both a green (PASS) and a red (mismatch/journal-hit) run. The red path is
verified via a mocked status file rather than deliberately corrupting a
live eBay listing -- the real HTTP-action/diff/journal-scan logic was
already live-verified against a real designated canary item
(tgw201501021970068) on 2026-07-04.
"""

import json
from unittest import mock

from tgw.ops_digest import _canary_probe_summary, render_text


def _write_status(tmp_path, **overrides):
    payload = {
        'sku': 'tgw201501021970068', 'actions': 'sync_from_ebay',
        'ran_at': '2026-07-04T16:57:14Z', 'duration_s': 2.1,
        'mismatches': [], 'journal_hits': [], 'passed': True,
    }
    payload.update(overrides)
    path = tmp_path / 'canary-probe-status.json'
    path.write_text(json.dumps(payload))
    return path


def test_canary_probe_summary_missing_file_returns_none():
    with mock.patch('tgw.ops_digest.Path') as m:
        m.return_value.read_text.side_effect = OSError('no such file')
        assert _canary_probe_summary() is None


def test_canary_probe_summary_parses_a_real_status_file(tmp_path, monkeypatch):
    path = _write_status(tmp_path)
    with mock.patch('tgw.ops_digest.Path', return_value=path):
        result = _canary_probe_summary()
    assert result['sku'] == 'tgw201501021970068'
    assert result['passed'] is True
    assert result['age_hours'] is not None


def test_render_text_shows_pass_line():
    d = {'checks_flagged': [], 'quota': {}, 'dead_letters': {}, 'dead_letter_delta': {},
        'restart_flags': {}, 'oldest_inbox_note': None, 'dataset_growth': None,
        'canary_probe': {'sku': 'tgw1', 'passed': True, 'age_hours': 1.0,
                         'mismatches': [], 'journal_hits': []},
        'queues': {}, 'generated_at': '2026-07-04T12:00:00+00:00', 'previous_run': None,
        'health_ok': True, 'restarts': {}, 'retry_wait': [], 'morning_exposure': []}
    text = render_text(d)
    assert 'CANARY PROBE — PASS' in text
    assert 'tgw1' in text


def test_render_text_shows_red_fail_with_mismatches():
    d = {'checks_flagged': [], 'quota': {}, 'dead_letters': {}, 'dead_letter_delta': {},
        'restart_flags': {}, 'oldest_inbox_note': None, 'dataset_growth': None,
        'canary_probe': {'sku': 'tgw1', 'passed': False, 'age_hours': 0.5,
                         'mismatches': ["title: intent='A' live='B'"], 'journal_hits': []},
        'queues': {}, 'generated_at': '2026-07-04T12:00:00+00:00', 'previous_run': None,
        'health_ok': True, 'restarts': {}, 'retry_wait': [], 'morning_exposure': []}
    text = render_text(d)
    assert 'CANARY PROBE — RED FAIL' in text
    assert "mismatch: title: intent='A' live='B'" in text
