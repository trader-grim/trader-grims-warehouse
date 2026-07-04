"""PP-PHOTOSYNC-001 P2 — ops-digest retry_wait liability + morning-exposure lines.

Scoped to render_text only: state_machine.retry_wait_breakdown() and
morning_exposure() are plain SQL against queue_jobs, exercised live via
`tgw ops-digest` (needs the real DB); here we lock the render contract given
a digest dict shaped the way collect() produces it.
"""

from tgw.ops_digest import render_text


def _base_digest(**overrides):
    d = {
        'generated_at': '2026-07-04T05:00:00+00:00',
        'previous_run': None,
        'health_ok': True,
        'checks_flagged': [],
        'queues': {'queued': 0, 'processing': 0},
        'dead_letters': {},
        'dead_letter_delta': {},
        'restarts': {},
        'restart_flags': {},
        'quota': {'incidents_today': 0, 'pools': {}},
        'oldest_inbox_note': None,
        'catalog_verify': None,
        'retry_wait': [],
        'morning_exposure': [],
    }
    d.update(overrides)
    return d


def test_no_retry_wait_or_exposure_renders_clean():
    text = render_text(_base_digest())
    assert 'RETRY_WAIT — none' in text
    assert 'MORNING EXPOSURE — nothing scheduled before 06:00 PST' in text


def test_retry_wait_below_threshold_not_flagged():
    d = _base_digest(retry_wait=[{'queue_name': 'ebay_upload', 'count': 3, 'oldest_age_hours': 1.2}])
    text = render_text(d)
    assert 'RETRY_WAIT — 3 job(s) pending retry' in text
    assert 'ebay_upload' in text
    assert 'RED' not in text


def test_retry_wait_over_count_threshold_flagged_red():
    d = _base_digest(retry_wait=[{'queue_name': 'ebay_sync', 'count': 51, 'oldest_age_hours': 1.0}])
    text = render_text(d)
    assert 'RED' in text
    assert 'ebay_sync' in text


def test_retry_wait_over_age_threshold_flagged_red():
    d = _base_digest(retry_wait=[{'queue_name': 'ebay_price', 'count': 2, 'oldest_age_hours': 30.0}])
    text = render_text(d)
    assert 'RED' in text
    assert 'oldest 30.0h' in text


def test_morning_exposure_lists_queues_with_counts():
    d = _base_digest(morning_exposure=[
        {'queue_name': 'ebay_stage', 'count': 400},
        {'queue_name': 'ebay_publish', 'count': 120},
    ])
    text = render_text(d)
    assert 'MORNING EXPOSURE — 520 job(s) scheduled to fire before 06:00 PST' in text
    assert 'ebay_stage' in text
    assert 'ebay_publish' in text
