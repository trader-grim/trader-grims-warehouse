"""Tests for tgw.quota — the metered-API budget layer (PP-QUOTA-001, session 42).

Invariant: no code calls a metered API except through a counted choke point;
background callers are halted at the budget threshold BEFORE exhausting a pool
the operator needs; every 429 is an incident with caller identity.
"""

from __future__ import annotations

import json

import pytest

from tgw import quota


@pytest.fixture(autouse=True)
def _interactive_default():
    yield
    quota.set_context('interactive', 'test')


def _cfg(tmp_path, **raw):
    return {'raw': {
        'quota_state_path': str(tmp_path / 'quota-state.json'),
        'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl'),
        **raw,
    }}


class TestPoolMapping:
    def test_taxonomy_pools_split_bulk_from_per_category(self):
        assert quota.pool_for_rest_path(
            '/commerce/taxonomy/v1/category_tree/0/get_item_aspects_for_category'
        ) == 'ebay_taxonomy'
        assert quota.pool_for_rest_path(
            '/commerce/taxonomy/v1/category_tree/0/fetch_item_aspects'
        ) == 'ebay_taxonomy_bulk'

    def test_sell_pools(self):
        assert quota.pool_for_rest_path('/sell/inventory/v1/offer') == 'ebay_inventory'
        assert quota.pool_for_rest_path('/sell/metadata/v1/x') == 'ebay_metadata'
        assert quota.pool_for_rest_path('/sell/marketing/v1/promotions') == 'ebay_marketing'
        assert quota.pool_for_rest_path('/weird/path') == 'ebay_other'


class TestCountingAndState:
    def test_record_accumulates_per_pool_and_caller(self, tmp_path):
        cfg = _cfg(tmp_path)
        quota.set_context('background', 'worker:ebay_sync')
        quota.record(cfg, 'ebay_inventory')
        quota.record(cfg, 'ebay_inventory', 2)
        state = json.loads((tmp_path / 'quota-state.json').read_text())
        pool = state['pools']['ebay_inventory']
        assert pool['spent'] == 3
        assert pool['callers']['worker:ebay_sync'] == 3

    def test_day_rollover_resets_counters(self, tmp_path, monkeypatch):
        cfg = _cfg(tmp_path)
        (tmp_path / 'quota-state.json').write_text(json.dumps({
            'day': '1999-01-01', 'incidents_today': 9,
            'pools': {'ebay_taxonomy': {'spent': 4999, 'last_429': None, 'callers': {}}},
        }))
        quota.record(cfg, 'ebay_taxonomy')
        state = json.loads((tmp_path / 'quota-state.json').read_text())
        assert state['pools']['ebay_taxonomy']['spent'] == 1
        assert state['incidents_today'] == 0


class TestEnforcement:
    def test_background_halted_at_threshold(self, tmp_path):
        cfg = _cfg(tmp_path)
        quota.set_context('background', 'worker:ebay_draft')
        for _ in range(35):  # 70% of the 50-call budget
            quota.record(cfg, 'ebay_taxonomy')
        cfg['raw']['quota_budgets'] = {'ebay_taxonomy': 50}
        with pytest.raises(quota.QuotaBudgetExceeded, match='quota budget exhausted'):
            quota.precheck(cfg, 'ebay_taxonomy')

    def test_interactive_never_halted(self, tmp_path):
        cfg = _cfg(tmp_path, quota_budgets={'ebay_taxonomy': 10})
        quota.set_context('interactive', 'tgw-http')
        for _ in range(50):
            quota.record(cfg, 'ebay_taxonomy')
        quota.precheck(cfg, 'ebay_taxonomy')  # must not raise

    def test_unknown_budget_pool_not_halted(self, tmp_path):
        cfg = _cfg(tmp_path)
        quota.set_context('background', 'worker:ai_identify')
        quota.record(cfg, 'llm_openrouter', 10_000)
        quota.precheck(cfg, 'llm_openrouter')  # count-only pool: must not raise

    def test_llm_google_default_budget_halts_background(self, tmp_path):
        # llm_google is a provisional safety cap (quota.py _DEFAULT_BUDGETS —
        # currently 300, a paid-key value set 2026-07-08; was 20 under the
        # earlier free-tier-only setup) — background callers must halt once
        # the threshold is spent, regardless of the live default's exact
        # value. Override the budget explicitly so this test doesn't drift
        # out of sync with _DEFAULT_BUDGETS again (audit#1143 code-review, #1252).
        cfg = _cfg(tmp_path, quota_budgets={'llm_google': 20})
        quota.set_context('background', 'worker:ai_identify')
        quota.record(cfg, 'llm_google', 20)
        with pytest.raises(quota.QuotaBudgetExceeded):
            quota.precheck(cfg, 'llm_google')

    def test_background_passes_under_threshold(self, tmp_path):
        cfg = _cfg(tmp_path, quota_budgets={'ebay_taxonomy': 100})
        quota.set_context('background', 'worker:ebay_draft')
        quota.record(cfg, 'ebay_taxonomy', 10)
        quota.precheck(cfg, 'ebay_taxonomy')  # 10% spent: must not raise

    def test_halt_message_matches_worker_transient_pattern(self, tmp_path):
        # The halt must requeue-with-delay in workers, never dead-letter.
        from tgw.queue.worker_base import classify_dead_letter
        cfg = _cfg(tmp_path, quota_budgets={'ebay_taxonomy': 10})
        quota.set_context('background', 'worker:ebay_draft')
        quota.record(cfg, 'ebay_taxonomy', 10)
        with pytest.raises(quota.QuotaBudgetExceeded) as exc_info:
            quota.precheck(cfg, 'ebay_taxonomy')
        action, delay = classify_dead_letter(str(exc_info.value))
        assert action == 'requeue'
        assert delay > 0

    def test_http_429_classifies_as_transient_requeue(self):
        from tgw.queue.worker_base import classify_dead_letter
        action, _ = classify_dead_letter(
            '429 Client Error: Too Many Requests for url: https://api.ebay.com/x')
        assert action == 'requeue'

    def test_trading_usage_limit_classifies_as_transient_requeue(self):
        from tgw.queue.worker_base import classify_dead_letter
        action, _ = classify_dead_letter(
            'Trading API GetMyeBaySelling failed: You have exceeded your usage limit.')
        assert action == 'requeue'


class TestIncidents:
    def test_429_logged_with_caller_identity(self, tmp_path):
        cfg = _cfg(tmp_path)
        quota.set_context('background', 'worker:ebay_sync')
        quota.record_429(cfg, 'ebay_taxonomy', 'GET /commerce/taxonomy/...')
        lines = (tmp_path / 'quota-incidents.jsonl').read_text().splitlines()
        rec = json.loads(lines[-1])
        assert rec['pool'] == 'ebay_taxonomy'
        assert rec['caller'] == 'worker:ebay_sync'
        assert quota.status(cfg)['incidents_today'] == 1

    def test_status_reports_fraction_and_last_429(self, tmp_path):
        cfg = _cfg(tmp_path, quota_budgets={'ebay_taxonomy': 100})
        quota.record(cfg, 'ebay_taxonomy', 50)
        quota.record_429(cfg, 'ebay_taxonomy', 'x')
        st = quota.status(cfg)
        pool = st['pools']['ebay_taxonomy']
        assert pool['fraction'] == 0.5
        assert pool['last_429'] is not None


class TestFailOpen:
    def test_unwritable_state_path_never_raises(self):
        cfg = {'raw': {'quota_state_path': '/nonexistent-root/nope/quota.json',
                       'quota_incident_log': '/nonexistent-root/nope/inc.jsonl'}}
        quota.record(cfg, 'ebay_inventory')
        quota.record_429(cfg, 'ebay_inventory', 'x')
        quota.precheck(cfg, 'ebay_inventory')  # no state → no halt
        assert quota.status(cfg)['pools'] == {}


class Test429Cooldown:
    def test_background_stands_down_after_recent_429(self, tmp_path):
        cfg = _cfg(tmp_path)
        quota.set_context('background', 'worker:ebay_draft')
        quota.record_429(cfg, 'ebay_taxonomy', 'x')
        with pytest.raises(quota.QuotaBudgetExceeded, match='stand-down'):
            quota.precheck(cfg, 'ebay_taxonomy')

    def test_interactive_still_passes_during_cooldown(self, tmp_path):
        cfg = _cfg(tmp_path)
        quota.record_429(cfg, 'ebay_taxonomy', 'x')
        quota.set_context('interactive', 'tgw-http')
        quota.precheck(cfg, 'ebay_taxonomy')  # must not raise

    def test_stale_429_does_not_block(self, tmp_path):
        import json as _json
        cfg = _cfg(tmp_path)
        (tmp_path / 'quota-state.json').write_text(_json.dumps({
            'day': quota._day_key(), 'incidents_today': 1,
            'pools': {'ebay_taxonomy': {
                'spent': 1, 'callers': {},
                'last_429': '2020-01-01T00:00:00+00:00'}},
        }))
        quota.set_context('background', 'worker:ebay_draft')
        quota.precheck(cfg, 'ebay_taxonomy')  # old 429: must not raise
