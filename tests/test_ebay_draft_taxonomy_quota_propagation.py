"""code-review follow-up to audit#1143 #1181 — ebay_draft.py's best_category()
retry must let quota.QuotaBudgetExceeded propagate, not swallow it in its
pre-existing `except Exception` around the taxonomy retry.

Bug: taxonomy.py's best_category() was fixed (#1181) to re-raise
QuotaBudgetExceeded so the worker requeues transiently — but ebay_draft.py's
own broad `except Exception as exc: log.warning(...)` caught it right back
and fell through to the '99 Everything Else' fallback category, defeating
the fix at this call site.

The taxonomy retry happens very early in handle() (right after loading the
item JSON, before any photo/aspect/pricing work), so no further mocking is
needed to reach it — this keeps the test narrowly scoped to the fix.
"""

from __future__ import annotations

import json

import pytest

import tgw.apis.ebay.taxonomy as taxonomy_mod
from tgw import quota
from tgw.workers.ebay_draft import EbayDraftWorker


def _worker(cfg):
    w = EbayDraftWorker.__new__(EbayDraftWorker)
    w.config = cfg
    return w


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def test_quota_budget_exceeded_from_taxonomy_retry_propagates_out_of_handle(tmp_path, monkeypatch):
    sku = "tgw1"
    _write_item(tmp_path, sku, {"sku": sku, "title": "Real Title"})  # no ebay_category_id

    def _raise_quota(cfg, title, category):
        raise quota.QuotaBudgetExceeded('quota budget exhausted for ebay_taxonomy: 5000/5000 spent')

    monkeypatch.setattr(taxonomy_mod, "best_category", _raise_quota)

    worker = _worker({"itemdata_root": tmp_path})
    with pytest.raises(quota.QuotaBudgetExceeded):
        worker.handle({"payload_json": {"sku": sku}})
