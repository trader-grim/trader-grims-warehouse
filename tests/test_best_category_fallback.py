"""audit#1143 #1181 — taxonomy.py's best_category() must continue its
documented fallback chain (title query -> broader category query) when a
query fails, not abort the whole lookup on the first exception.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tgw import quota
from tgw.apis.ebay import taxonomy


def _cfg():
    return {}


class TestBestCategoryFallbackOnError:
    def test_first_query_failure_falls_through_to_second_query(self):
        # Regression: previously an uncaught exception on the first query
        # propagated straight out of best_category(), never trying the
        # second (broader category) query at all.
        calls = []

        def _fake(cfg, query):
            calls.append(query)
            if query == 'Vintage Widget Title':
                raise RuntimeError('500 Internal Server Error')
            return [{'category': {'categoryId': '123', 'categoryName': 'Widgets'}}]

        with patch.object(taxonomy, 'get_category_suggestions', side_effect=_fake):
            result = taxonomy.best_category(_cfg(), 'Vintage Widget Title', 'Widgets')

        assert result == ('123', 'Widgets')
        assert calls == ['Vintage Widget Title', 'Widgets']

    def test_all_queries_failing_returns_none_none_not_raise(self):
        def _fake(cfg, query):
            raise RuntimeError('503 Service Unavailable')

        with patch.object(taxonomy, 'get_category_suggestions', side_effect=_fake):
            result = taxonomy.best_category(_cfg(), 'title', 'category')

        assert result == (None, None)

    def test_quota_budget_exceeded_propagates_instead_of_being_swallowed(self):
        # A second live query would be gated identically — must propagate
        # so the worker requeues transiently rather than silently
        # returning (None, None) as if no category existed.
        def _fake(cfg, query):
            raise quota.QuotaBudgetExceeded('quota budget exhausted for taxonomy: 100/100 spent')

        with patch.object(taxonomy, 'get_category_suggestions', side_effect=_fake):
            with pytest.raises(quota.QuotaBudgetExceeded):
                taxonomy.best_category(_cfg(), 'title', 'category')

    def test_first_query_success_does_not_try_second(self):
        calls = []

        def _fake(cfg, query):
            calls.append(query)
            return [{'category': {'categoryId': '1', 'categoryName': 'First'}}]

        with patch.object(taxonomy, 'get_category_suggestions', side_effect=_fake):
            result = taxonomy.best_category(_cfg(), 'title', 'category')

        assert result == ('1', 'First')
        assert calls == ['title']
