"""PP-STATEMACHINE-001 (todo #1608) — job manifest contract tests.

All DB calls are mocked (same convention as tests/test_agent_trace.py /
tests/test_ai_usage.py) — no real PostgreSQL connection needed.

Covers:
  - Phase 1: a duplicate `_reschedule()`-style debounce call collapses into
    ON CONFLICT DO UPDATE against the same dedupe_key (not two INSERTs).
  - Phase 2: resolve_priority() config lookup, including fallback to
    'normal' when no config entry / no config file exists.
  - Phase 3: supersede=True atomically cancels the existing pending row
    under the same dedupe_key before inserting the fresh one.
  - Phase 4: the enforcer rejects a call missing dedupe_key / missing
    entity_id for entity_type='item'.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _mock_conn_cursor():
    """Build a MagicMock (con, cur) pair matching state_machine._conn()'s
    context-manager shape (same helper as tests/test_agent_trace.py)."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = ('job-id-123',)

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    return mock_con, mock_cur


# ---------------------------------------------------------------------------
# Phase 1 — debounce collapse (SQL shape, not a live-DB integration test:
# the ON CONFLICT ... DO UPDATE clause IS the collapse mechanism, so
# asserting it's present + wired to the right dedupe_key is the correct
# offline proxy for "two calls collapse into one row").
# ---------------------------------------------------------------------------

def test_debounce_reschedule_uses_on_conflict_same_key():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        sm.enqueue_job(
            queue_name='ebay_legacy_sync',
            payload={'reason': 'startup'},
            dedupe_key='ebay_legacy_sync:pending',
            debounce=True,
        )
        sm.enqueue_job(
            queue_name='ebay_legacy_sync',
            payload={'reason': 'scheduled'},
            not_before=123456.0,
            dedupe_key='ebay_legacy_sync:pending',
            debounce=True,
        )

    assert mock_cur.execute.call_count == 2
    for call in mock_cur.execute.call_args_list:
        sql, params = call.args
        assert 'ON CONFLICT (dedupe_key)' in sql
        assert 'DO UPDATE' in sql
        assert 'GREATEST' in sql
        assert params[8] == 'ebay_legacy_sync:pending'  # dedupe_key positional slot


def test_non_debounce_call_has_no_on_conflict():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        sm.enqueue_job(
            queue_name='ebay_upload',
            payload={'sku': 'tgw123'},
            entity_type='item',
            entity_id='tgw123',
            dedupe_key='ebay_upload:tgw123',
        )
    sql, _ = mock_cur.execute.call_args.args
    assert 'ON CONFLICT' not in sql


# ---------------------------------------------------------------------------
# Phase 2 — priority config resolution
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_priority_cache():
    from tgw.queue import state_machine as sm
    sm._reset_queue_priorities_cache()
    yield
    sm._reset_queue_priorities_cache()


def test_resolve_priority_falls_back_to_normal_when_file_absent(tmp_path):
    from tgw.queue import state_machine as sm

    missing = tmp_path / 'does-not-exist.json'
    with patch.object(sm, '_QUEUE_PRIORITIES_PATH', missing):
        assert sm.resolve_priority('some_queue', 'run') == 100


def test_resolve_priority_falls_back_to_normal_when_key_absent(tmp_path):
    from tgw.queue import state_machine as sm

    cfg_path = tmp_path / 'tgw-queue-priorities.json'
    cfg_path.write_text(json.dumps({
        'defaults': {'urgent': 10, 'high': 30, 'normal': 100, 'low': 200},
        'token_refresh:run': {'use_default': 'high'},
    }))
    with patch.object(sm, '_QUEUE_PRIORITIES_PATH', cfg_path):
        assert sm.resolve_priority('unmapped_queue', 'run') == 100


def test_resolve_priority_resolves_use_default_tier(tmp_path):
    from tgw.queue import state_machine as sm

    cfg_path = tmp_path / 'tgw-queue-priorities.json'
    cfg_path.write_text(json.dumps({
        'defaults': {'urgent': 10, 'high': 30, 'normal': 100, 'low': 200},
        'ebay_publish:run': {'use_default': 'urgent'},
    }))
    with patch.object(sm, '_QUEUE_PRIORITIES_PATH', cfg_path):
        assert sm.resolve_priority('ebay_publish', 'run') == 10


def test_resolve_priority_accepts_explicit_int_entry(tmp_path):
    from tgw.queue import state_machine as sm

    cfg_path = tmp_path / 'tgw-queue-priorities.json'
    cfg_path.write_text(json.dumps({
        'defaults': {'urgent': 10, 'high': 30, 'normal': 100, 'low': 200},
        'weird_queue:run': 5,
    }))
    with patch.object(sm, '_QUEUE_PRIORITIES_PATH', cfg_path):
        assert sm.resolve_priority('weird_queue', 'run') == 5


def test_enqueue_job_uses_config_priority_when_caller_omits_it(tmp_path):
    from tgw.queue import state_machine as sm

    cfg_path = tmp_path / 'tgw-queue-priorities.json'
    cfg_path.write_text(json.dumps({
        'defaults': {'urgent': 10, 'high': 30, 'normal': 100, 'low': 200},
        'ebay_publish:run': {'use_default': 'urgent'},
    }))
    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_QUEUE_PRIORITIES_PATH', cfg_path), \
         patch.object(sm, '_conn', return_value=mock_con):
        sm.enqueue_job(
            queue_name='ebay_publish',
            payload={'sku': 'tgw1'},
            entity_type='item',
            entity_id='tgw1',
            dedupe_key='ebay_publish:tgw1',
        )
    _, params = mock_cur.execute.call_args.args
    assert params[5] == 10  # priority positional slot


def test_enqueue_job_explicit_priority_overrides_config(tmp_path):
    from tgw.queue import state_machine as sm

    cfg_path = tmp_path / 'tgw-queue-priorities.json'
    cfg_path.write_text(json.dumps({
        'defaults': {'urgent': 10, 'high': 30, 'normal': 100, 'low': 200},
        'ebay_publish:run': {'use_default': 'urgent'},
    }))
    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_QUEUE_PRIORITIES_PATH', cfg_path), \
         patch.object(sm, '_conn', return_value=mock_con):
        sm.enqueue_job(
            queue_name='ebay_publish',
            payload={'sku': 'tgw1'},
            entity_type='item',
            entity_id='tgw1',
            dedupe_key='ebay_publish:tgw1',
            priority=42,
        )
    _, params = mock_cur.execute.call_args.args
    assert params[5] == 42


# ---------------------------------------------------------------------------
# Phase 3 — supersede
# ---------------------------------------------------------------------------

def test_supersede_cancels_existing_pending_row_before_insert():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        sm.enqueue_job(
            queue_name='token_refresh',
            payload={'reason': 'manual_restart'},
            dedupe_key='token_refresh:pending',
            supersede=True,
        )

    assert mock_cur.execute.call_count == 2
    cancel_sql, cancel_params = mock_cur.execute.call_args_list[0].args
    assert 'UPDATE queue_jobs' in cancel_sql
    assert "SET state = 'cancelled'" in cancel_sql
    assert cancel_params == ('token_refresh:pending',)

    insert_sql, insert_params = mock_cur.execute.call_args_list[1].args
    assert 'INSERT INTO queue_jobs' in insert_sql
    assert 'ON CONFLICT' not in insert_sql
    assert insert_params[8] == 'token_refresh:pending'


def test_supersede_and_debounce_mutually_exclusive():
    from tgw.queue import state_machine as sm

    with pytest.raises(ValueError):
        sm.enqueue_job(
            queue_name='token_refresh',
            payload={},
            dedupe_key='token_refresh:pending',
            debounce=True,
            supersede=True,
        )


def test_supersede_without_dedupe_key_is_plain_insert():
    """supersede=True with no dedupe_key is a no-op flag — nothing to
    supersede, falls through to a normal insert (only one execute call).
    Uses dedupe_key_exempt=True since Phase 4 enforcement (this same test
    module) now requires a dedupe_key or an explicit opt-out on every call."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        sm.enqueue_job(
            queue_name='token_refresh',
            payload={},
            supersede=True,
            dedupe_key_exempt=True,
        )
    assert mock_cur.execute.call_count == 1


# ---------------------------------------------------------------------------
# Phase 4 — enforcer
# ---------------------------------------------------------------------------

def test_enforcer_rejects_missing_dedupe_key():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        with pytest.raises(sm.MissingManifestFieldError):
            sm.enqueue_job(
                queue_name='some_queue',
                payload={},
                entity_type='generic',
            )


def test_enforcer_rejects_missing_entity_id_for_item():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        with pytest.raises(sm.MissingManifestFieldError):
            sm.enqueue_job(
                queue_name='some_queue',
                payload={'sku': 'tgw1'},
                entity_type='item',
                entity_id='',
                dedupe_key='some_queue:tgw1',
            )


def test_enforcer_allows_complete_manifest():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        jid = sm.enqueue_job(
            queue_name='some_queue',
            payload={'sku': 'tgw1'},
            entity_type='item',
            entity_id='tgw1',
            dedupe_key='some_queue:tgw1',
        )
    assert jid == 'job-id-123'


def test_enforcer_exempt_opt_out_bypasses_dedupe_key_check():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    with patch.object(sm, '_conn', return_value=mock_con):
        jid = sm.enqueue_job(
            queue_name='some_queue',
            payload={},
            dedupe_key_exempt=True,
        )
    assert jid == 'job-id-123'
