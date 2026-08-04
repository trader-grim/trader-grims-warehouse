#!/opt/TGW/.venvironments/tgw/bin/python3.12
"""R1.8 — full eBay dataset snapshot through the capture fence (session 42).

Dave: "that is the first thing that should have been done the same day we got
the token running." Walks every inventory item (paged) and every offer
(per-SKU) through ebay_get(); the fence's capture layer (invariant E7) lands
every raw response in /opt/TGW/incoming/ebay/YYYY-MM-DD.jsonl.gz. This script
keeps no state of its own — the capture archive IS the output.

Quota: sell.inventory pool, 2,000,000/day (probed live 2026-07-02); a full run
is ~100 paged-item calls + ~19,500 offer calls. Runs as a background caller so
the budgeter supervises it. Safe to re-run (idempotent; capture appends).

Run:  sudo -u tgw env LD_LIBRARY_PATH=<prod libs> scripts/ebay_snapshot_all.py
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from tgw import quota
from tgw.apis.ebay.client import ebay_get
from tgw.config import load_config
from tgw.logging import announce_script_run

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('ebay_snapshot_all')


def main() -> int:
    announce_script_run(
        'ebay_snapshot_all.py',
        'full eBay dataset snapshot (every inventory item + offer) through the capture fence (R1.8)',
    )
    cfg = load_config(Path('/opt/TGW/config/tgw-api-config.json'))
    quota.set_context('background', 'r1.8-snapshot')

    # Phase 1 — every inventory item, paged (each page captured raw)
    skus = []
    offset, limit = 0, 200
    while True:
        page = ebay_get(cfg, '/sell/inventory/v1/inventory_item',
                        params={'limit': limit, 'offset': offset})
        items = page.get('inventoryItems', [])
        skus.extend(i.get('sku') for i in items if i.get('sku'))
        total = page.get('total', 0)
        log.info('inventory page offset=%d: %d items (total %d)', offset, len(items), total)
        offset += limit
        if offset >= total or not items:
            break
        time.sleep(0.2)
    log.info('phase 1 complete: %d SKUs inventoried', len(skus))

    # Phase 2 — every offer, per SKU (each response captured raw)
    errors = 0
    for n, sku in enumerate(skus, 1):
        try:
            ebay_get(cfg, '/sell/inventory/v1/offer', params={'sku': sku})
        except Exception as exc:  # noqa: BLE001 — record and continue; 404 = no offer
            errors += 1
            if '404' not in str(exc):
                log.warning('offer fetch failed for %s: %s', sku, str(exc)[:120])
        if n % 500 == 0:
            log.info('offers: %d/%d (%d errors)', n, len(skus), errors)
        time.sleep(0.15)

    log.info('SNAPSHOT COMPLETE: %d SKUs, %d offer-fetch errors — raw data in incoming/ebay/', len(skus), errors)
    return 0


if __name__ == '__main__':
    sys.exit(main())
