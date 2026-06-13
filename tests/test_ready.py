"""Tests for the ready state + dole-out (PP-EDITOR-001) and the
dead-letter T/H split + zero-work watchdog (PP-DEADLETTER-001 remainder)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tgw.ready import cmd_ready, dole_batch_size, ready_pool, set_ready, unset_ready

# ---------------------------------------------------------------------------
# fixtures — minimal ItemData tree
# ---------------------------------------------------------------------------

def _write_item(root, sku, offer=None, **fields):
    d = root / sku
    d.mkdir()
    doc = {'sku': sku, 'title': f'Title {sku}', 'location': 'A1', **fields}
    if offer is not None:
        doc['ebay_offer'] = offer
    (d / f'{sku}.json').write_text(json.dumps(doc), encoding='utf-8')


@pytest.fixture
def cfg(tmp_path):
    root = tmp_path / 'ItemData'
    root.mkdir()
    # staged, not ready
    _write_item(root, 'tgw20260101000000001',
                offer={'offer_id': 'o1', 'status': 'UNPUBLISHED', 'price': 10})
    # staged + ready (older)
    _write_item(root, 'tgw20260101000000002',
                offer={'offer_id': 'o2', 'status': 'UNPUBLISHED', 'price': 20,
                       'ready_at': '2026-06-10T00:00:00+00:00'})
    # staged + ready (newer)
    _write_item(root, 'tgw20260101000000003',
                offer={'offer_id': 'o3', 'status': 'UNPUBLISHED', 'price': 30,
                       'ready_at': '2026-06-11T00:00:00+00:00'})
    # published — never in pool even with a stale ready_at
    _write_item(root, 'tgw20260101000000004',
                offer={'offer_id': 'o4', 'status': 'PUBLISHED',
                       'ready_at': '2026-06-09T00:00:00+00:00'})
    # no offer at all
    _write_item(root, 'tgw20260101000000005')
    return {'itemdata_root': root, 'dole_divisor': 60, 'dole_interval_s': 3600}


# ---------------------------------------------------------------------------
# ready_pool / dole_batch_size
# ---------------------------------------------------------------------------

def test_ready_pool_filters_and_sorts_oldest_first(cfg):
    pool = ready_pool(cfg)
    assert [p['sku'] for p in pool] == ['tgw20260101000000002', 'tgw20260101000000003']


def test_dole_batch_size():
    assert dole_batch_size(0, 60) == 0
    assert dole_batch_size(1, 60) == 1     # non-empty pool always doles at least 1
    assert dole_batch_size(59, 60) == 1
    assert dole_batch_size(120, 60) == 2
    assert dole_batch_size(600, 60) == 10
    assert dole_batch_size(5, 0) == 5      # degenerate divisor clamped to 1


# ---------------------------------------------------------------------------
# set_ready / unset_ready
# ---------------------------------------------------------------------------

def test_set_ready_marks_staged_item(cfg):
    result = set_ready(cfg, ['tgw20260101000000001'])
    assert result['ok'] is True
    assert result['marked'] == ['tgw20260101000000001']
    doc = json.loads((cfg['itemdata_root'] / 'tgw20260101000000001' /
                      'tgw20260101000000001.json').read_text())
    assert doc['ebay_offer']['ready_at']
    assert doc['ebay_offer']['status'] == 'UNPUBLISHED'  # eBay's field untouched
    assert len(ready_pool(cfg)) == 3


def test_set_ready_rejects_unstaged_and_published(cfg):
    result = set_ready(cfg, ['tgw20260101000000004', 'tgw20260101000000005', 'nope'])
    assert result['marked'] == []
    assert any('not UNPUBLISHED' in s for s in result['skipped'])
    assert any('no offer_id' in e for e in result['errors'])
    assert any('not found' in e for e in result['errors'])


def test_set_ready_idempotent(cfg):
    result = set_ready(cfg, ['tgw20260101000000002'])
    assert result['marked'] == []
    assert any('already ready' in s for s in result['skipped'])


def test_unset_ready_pulls_item_back(cfg):
    result = unset_ready(cfg, ['tgw20260101000000002'])
    assert result['cleared'] == ['tgw20260101000000002']
    assert [p['sku'] for p in ready_pool(cfg)] == ['tgw20260101000000003']
    result = unset_ready(cfg, ['tgw20260101000000002'])
    assert any('not in ready state' in s for s in result['skipped'])


# ---------------------------------------------------------------------------
# cmd_ready
# ---------------------------------------------------------------------------

def test_cmd_ready_list_default(cfg, capsys):
    result = cmd_ready(cfg, 'list', [])
    assert result['ok'] is True
    assert result['count'] == 2
    assert result['per_cycle'] == 1
    out = capsys.readouterr().out
    assert 'tgw20260101000000002' in out
    assert 'dole rate 1/cycle' in out


def test_cmd_ready_set_requires_skus(cfg):
    result = cmd_ready(cfg, 'set', [])
    assert result['ok'] is False


# ---------------------------------------------------------------------------
# cmd_staged excludes the ready queue
# ---------------------------------------------------------------------------

def test_cmd_staged_excludes_ready_items(cfg):
    from tgw.api import cmd_staged
    result = cmd_staged(cfg)
    assert result['ok'] is True
    assert [i['sku'] for i in result['items']] == ['tgw20260101000000001']
    assert result['ready_count'] == 2


# ---------------------------------------------------------------------------
# ebay_dole worker cycle
# ---------------------------------------------------------------------------

def _dole_worker(cfg):
    from tgw.workers.ebay_dole import EbayDoleWorker
    with patch.object(EbayDoleWorker, '__init__', lambda self: None):
        w = EbayDoleWorker()
    w.config = cfg
    return w


def test_dole_cycle_publishes_oldest_slice(cfg):
    w = _dole_worker(cfg)
    with patch('tgw.api.cmd_publish',
               return_value={'enqueued': ['tgw20260101000000002'], 'skipped': [], 'errors': []}) as pub, \
         patch.object(w, '_reschedule') as resched:
        w.handle({'payload_json': {'reason': 'test'}})
    pub.assert_called_once_with(cfg, ['tgw20260101000000002'])  # pool=2 → 1, oldest first
    resched.assert_called_once()


def test_dole_cycle_empty_pool_still_reschedules(cfg, tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    w = _dole_worker({**cfg, 'itemdata_root': empty})
    with patch('tgw.api.cmd_publish') as pub, \
         patch.object(w, '_reschedule') as resched:
        w.handle({'payload_json': {}})
    pub.assert_not_called()
    resched.assert_called_once()


# ---------------------------------------------------------------------------
# PP-DEADLETTER-001 — T/H split + zero-work watchdog (task #94)
# ---------------------------------------------------------------------------

def test_classify_dead_letter_errors_splits_buckets():
    from tgw.health import classify_dead_letter_errors
    rows = [
        {'queue_name': 'ebay_draft', 'error_detail': 'token is expired'},        # transient
        {'queue_name': 'ebay_draft', 'error_detail': 'HardFailure: no category'},
        {'queue_name': 'ebay_price', 'error_detail': 'ReadTimeout from api'},     # transient
    ]
    out = classify_dead_letter_errors(rows)
    assert out['ebay_draft'] == {'transient': 1, 'hard': 1}
    assert out['ebay_price'] == {'transient': 1, 'hard': 0}


def test_check_postgres_detail_shows_th_split_and_stalls():
    from tgw import health
    sm = 'tgw.queue.state_machine'
    with patch(f'{sm}.init'), \
         patch(f'{sm}.queue_depths', return_value={'ebay_draft': 2}), \
         patch(f'{sm}.dead_letter_count', return_value=3), \
         patch(f'{sm}.dead_letter_errors', return_value=[
             {'queue_name': 'ebay_draft', 'error_detail': 'token is expired'},
             {'queue_name': 'ebay_draft', 'error_detail': 'HardFailure: rejected'},
             {'queue_name': 'ebay_price', 'error_detail': 'HardFailure: rejected'},
         ]), \
         patch(f'{sm}.zero_work_queues', return_value=[
             {'queue_name': 'ebay_sku_migrate', 'waiting': 12,
              'oldest_wait_h': 9.5, 'hours_since_done': None},
         ]):
        result = health.check_postgres({'zero_work_stall_hours': 4.0})
    assert result['ok'] is True
    assert result['warn'] is True
    assert result['dead_letter_transient'] == 1
    assert result['dead_letter_hard'] == 2
    assert 'T1/H2' in result['detail']
    assert 'ebay_draft:2(T1/H1)' in result['detail']
    assert 'zero-work stall: ebay_sku_migrate' in result['detail']


def test_check_postgres_clean_no_warn():
    from tgw import health
    sm = 'tgw.queue.state_machine'
    with patch(f'{sm}.init'), \
         patch(f'{sm}.queue_depths', return_value={}), \
         patch(f'{sm}.dead_letter_count', return_value=0), \
         patch(f'{sm}.dead_letter_errors', return_value=[]), \
         patch(f'{sm}.zero_work_queues', return_value=[]):
        result = health.check_postgres({})
    assert result['ok'] is True
    assert 'warn' not in result
    assert 'dead_letter=0' in result['detail']
