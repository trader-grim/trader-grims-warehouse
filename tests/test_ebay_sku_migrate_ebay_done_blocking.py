"""audit#1143 #1169 — ebay_sku_migrate.py's handle() must permanently block
an item when eBay-side migration succeeded but the local folder rename
failed (result['ebay_done']=True), even though the local-rename error text
never matches any of the hardcoded eBay-errorId permanent-failure signals.

Before this fix, _is_permanent_failure(error_text) was the ONLY gate for
blocking — a local filesystem rename failure never contains an eBay errorId
substring, so it always fell through un-blocked and the item was silently
reprocessed every cycle forever (eBay already has the new custom label;
local still thinks it's old_sku), with no alert and no way for an operator
to discover it.

Code-review follow-up: the first version of this fix was too broad — it
blocked on ANY ebay_done=True, including the transitional partial-migration
state (old offer already deleted, new offer publish failed) that
_recover_partial() exists specifically to auto-heal on a later run. Only a
result WITHOUT 'recoverable': True should permanently block; a recoverable
one must stay retryable or _recover_partial never gets a chance to run.

All eBay/state_machine calls are mocked; item JSON and the blocked-registry
file are real temp files — tests pass completely offline.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import tgw.workers.ebay_sku_migrate as migrate_mod
from tgw.workers.ebay_sku_migrate import EbaySkuMigrateWorker


def _cfg(tmp_path) -> Dict[str, Any]:
    return {
        'itemdata_root': tmp_path,
        'ebay_sku_migrate': {'enabled': True, 'batch_size': 5, 'interval_hours': 1},
    }


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')
    return d / f'{sku}.json'


def _worker(cfg: Dict[str, Any]) -> EbaySkuMigrateWorker:
    w = EbaySkuMigrateWorker.__new__(EbaySkuMigrateWorker)
    w.config = cfg
    return w


def _mock_common(monkeypatch, tmp_path):
    monkeypatch.setattr(migrate_mod, '_BLOCKED_REGISTRY', tmp_path / 'migrate-blocked.json')
    monkeypatch.setattr(migrate_mod.state_machine, 'enqueue_job', lambda **k: 'job-1')


def test_ebay_done_local_rename_failure_blocks_even_without_known_error_signal(tmp_path, monkeypatch):
    old_sku = 'tgw1'
    _write_item(tmp_path, old_sku, {'sku': old_sku})
    _mock_common(monkeypatch, tmp_path)

    monkeypatch.setattr(migrate_mod, 'find_batch', lambda cfg, n: [(old_sku, 'tgw2')])
    monkeypatch.setattr(migrate_mod, 'migrate_one', lambda cfg, o, n: {
        'ok': False, 'old_sku': o,
        'error': 'local rename failed (eBay already done): [Errno 13] Permission denied',
        'ebay_done': True,
    })

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {}})

    doc = json.loads((tmp_path / old_sku / f'{old_sku}.json').read_text())
    assert doc.get('sku_migrate_skip') is True
    assert doc.get('sku_migrate_blocked', {}).get('ebay_done') is True
    assert doc.get('review_block', {}).get('reason_code') == 'LOCAL_RENAME_FAILED_AFTER_EBAY_DONE'

    registry = json.loads((tmp_path / 'migrate-blocked.json').read_text())
    assert old_sku in registry


def test_recoverable_ebay_done_publish_failure_is_not_blocked(tmp_path, monkeypatch):
    # Regression: old offer already deleted, new offer created but publish
    # failed transiently — _recover_partial() auto-heals this on the next
    # scheduled run. Blocking it here would silently disable that recovery
    # path forever on the very first transient publish error.
    old_sku = 'tgw3'
    _write_item(tmp_path, old_sku, {'sku': old_sku})
    _mock_common(monkeypatch, tmp_path)

    monkeypatch.setattr(migrate_mod, 'find_batch', lambda cfg, n: [(old_sku, 'tgw4')])
    monkeypatch.setattr(migrate_mod, 'migrate_one', lambda cfg, o, n: {
        'ok': False, 'old_sku': o,
        'error': 'publish offer off-123: 503 Service Unavailable',
        'ebay_done': True, 'recoverable': True,
    })

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {}})

    doc = json.loads((tmp_path / old_sku / f'{old_sku}.json').read_text())
    assert 'sku_migrate_skip' not in doc
    assert not (tmp_path / 'migrate-blocked.json').exists()


def test_ordinary_transient_failure_without_ebay_done_is_not_blocked(tmp_path, monkeypatch):
    old_sku = 'tgw2'
    _write_item(tmp_path, old_sku, {'sku': old_sku})
    _mock_common(monkeypatch, tmp_path)

    monkeypatch.setattr(migrate_mod, 'find_batch', lambda cfg, n: [(old_sku, 'tgw3')])
    monkeypatch.setattr(migrate_mod, 'migrate_one', lambda cfg, o, n: {
        'ok': False, 'old_sku': o, 'error': 'network timeout',
    })

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {}})

    doc = json.loads((tmp_path / old_sku / f'{old_sku}.json').read_text())
    assert 'sku_migrate_skip' not in doc
    assert not (tmp_path / 'migrate-blocked.json').exists()
