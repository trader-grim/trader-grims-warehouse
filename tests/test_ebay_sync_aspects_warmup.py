"""Tests for the opportunistic aspects-cache warm-up wired into ebay_sync's full
periodic run (session 39, Dave's idea): "utilize leftover API calls just before
[quota] expiration to update our cache... we crawl it at the end of every day,
then our limit resets." Implemented as a best-effort pass over the category IDs
already seen while syncing offers — no new worker/service needed, self-throttling
(stops at the first failure), scoped to categories actually sold in.

All eBay API calls and state_machine/fence writes are mocked — tests pass
completely offline.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

import tgw.workers.ebay_sync as ebay_sync_mod
from tgw.workers.ebay_sync import EbaySyncWorker


@pytest.fixture(autouse=True)
def _mock_dependencies(monkeypatch):
    monkeypatch.setattr(ebay_sync_mod, "fence_ebay_write", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ebay_sync_mod, "backfill_canonical_from_live", lambda item: {})
    monkeypatch.setattr(ebay_sync_mod.state_machine, "enqueue_job", lambda **k: 1)
    # _sync_one does real work (reads item JSON, live GETs) — not the focus here,
    # stub it out so these tests isolate the warm-up wiring in handle().
    monkeypatch.setattr(EbaySyncWorker, "_sync_one", lambda self, offer, sku: 0)
    monkeypatch.setattr(EbaySyncWorker, "_reschedule", lambda self: None)


def _cfg(tmp_path) -> Dict[str, Any]:
    return {"itemdata_root": tmp_path, "catalog_root": tmp_path, "raw": {}, "api_key": "test-api-key"}


def _worker(cfg: Dict[str, Any]) -> EbaySyncWorker:
    w = EbaySyncWorker.__new__(EbaySyncWorker)
    w.config = cfg
    return w


def _job() -> Dict[str, Any]:
    return {"payload_json": {}}  # no sku → full-fetch path


def test_warm_up_called_with_seen_category_ids(tmp_path, monkeypatch):
    offers = [
        {"sku": "tgw1", "categoryId": "111"},
        {"sku": "tgw2", "categoryId": "222"},
        {"sku": "tgw3", "categoryId": "111"},  # duplicate — dedup is warm_missing_aspects' job
    ]
    monkeypatch.setattr(ebay_sync_mod, "fetch_all_offers", lambda cfg: offers)
    calls = []
    monkeypatch.setattr(
        "tgw.apis.ebay.specifics.warm_missing_aspects",
        lambda cfg, category_ids, **k: calls.append(list(category_ids)) or len(category_ids),
    )

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job())

    assert len(calls) == 1
    assert calls[0] == ["111", "222", "111"]


def test_warm_up_skipped_when_no_categories_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_sync_mod, "fetch_all_offers", lambda cfg: [{"sku": "tgw1"}])  # no categoryId
    calls = []
    monkeypatch.setattr(
        "tgw.apis.ebay.specifics.warm_missing_aspects",
        lambda cfg, category_ids, **k: calls.append(category_ids) or 0,
    )

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job())

    assert calls == []


def test_warm_up_failure_does_not_break_the_sync_run(tmp_path, monkeypatch):
    """The sync run's own success (dead-letter avoidance) must not depend on the
    warm-up succeeding — it's a nice-to-have, never a hard requirement."""
    offers = [{"sku": "tgw1", "categoryId": "111"}]
    monkeypatch.setattr(ebay_sync_mod, "fetch_all_offers", lambda cfg: offers)
    monkeypatch.setattr(
        "tgw.apis.ebay.specifics.warm_missing_aspects",
        lambda cfg, category_ids, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job())  # must not raise
