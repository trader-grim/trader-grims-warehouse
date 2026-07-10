"""audit#1143 #1246 (deferred #1245 finding) — ebay_sku_migrate.py's
interval_hours config lookup was duplicated verbatim between handle() and
_on_terminal_failure(); a future change to the config key or its default
could easily be applied in one place and missed in the other. Factored
into a single _interval_hours() helper both call sites now share.

All eBay/state_machine calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from typing import Any, Dict

import tgw.workers.ebay_sku_migrate as migrate_mod
from tgw.workers.ebay_sku_migrate import EbaySkuMigrateWorker


def _worker(cfg: Dict[str, Any]) -> EbaySkuMigrateWorker:
    w = EbaySkuMigrateWorker.__new__(EbaySkuMigrateWorker)
    w.config = cfg
    return w


def test_interval_hours_reads_configured_value():
    worker = _worker({'ebay_sku_migrate': {'interval_hours': 4}})
    assert worker._interval_hours() == 4.0


def test_interval_hours_defaults_to_one_when_unconfigured():
    worker = _worker({})
    assert worker._interval_hours() == 1.0


def test_handle_and_on_terminal_failure_use_the_same_interval(tmp_path, monkeypatch):
    cfg = {
        'itemdata_root': tmp_path,
        'ebay_sku_migrate': {'enabled': True, 'batch_size': 5, 'interval_hours': 7},
    }
    reschedules = []
    monkeypatch.setattr(migrate_mod, 'find_batch', lambda cfg, n: [])
    monkeypatch.setattr(EbaySkuMigrateWorker, '_reschedule',
                        lambda self, interval_hours: reschedules.append(interval_hours))

    worker = _worker(cfg)
    worker.handle({'payload_json': {}})
    # No batch found → handle() returns early without rescheduling (migration
    # finished); confirm _interval_hours() itself matches what
    # _on_terminal_failure would independently compute.
    assert reschedules == []
    assert worker._interval_hours() == 7.0

    worker._on_terminal_failure({'payload_json': {}}, 'some dead-letter error')
    assert reschedules == [7.0]
