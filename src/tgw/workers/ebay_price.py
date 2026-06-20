"""
tgw.workers.ebay_price — Suggest and auto-fill a price for a draft listing.

Enqueued by ebay_draft after the draft_listing block is written.  Queries
eBay Browse API for active listing comps, computes the 25th-percentile price,
and writes:
  - ebay_offer.price        (the suggested price)
  - ebay_offer.price_source (how it was derived)
  - ebay_offer.price_comps  (count, min, p25, median, max)
  - ebay_offer.priced_at    (ISO timestamp)
  - draft_listing.price     (same value, so ebay_publish can read it)

Skips items that already have ebay_offer.price set (idempotent).
If comps are insufficient (< 3 results) price is left null and flagged.

Queue name: ebay_price
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pricing import freeship_price, suggest_price, to_99
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_price'


class EbayPriceWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_price job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        draft = item.get('draft_listing')
        if not draft:
            raise HardFailure(f'{sku}: no draft_listing — run ebay_draft first')

        # Idempotent: skip if already priced
        existing = item.get('ebay_offer', {})
        if existing.get('price') is not None:
            log.info('ebay_price: %s already priced at $%.2f — skipping',
                     sku, existing['price'])
            tgw_logging.log_event('ebay_price_skipped', sku=sku,
                                  reason='already_priced',
                                  price=existing['price'])
            return

        title          = draft.get('title') or item.get('title', '')
        category_name  = draft.get('category_name') or item.get('ebay_category_name', '')
        category_id    = str(draft.get('category_id') or item.get('ebay_category_id', ''))
        item_condition = str(item.get('condition', '')).strip()
        product_lookup = item.get('product_lookup') or {}

        if not title or title == sku:
            raise HardFailure(f'{sku}: no title — run ai_identify first')

        log.info('ebay_price: querying comps for %r (condition=%r)', title[:60], item_condition)
        tgw_logging.log_event('ebay_price_start', sku=sku, title=title[:60])

        result = suggest_price(
            self.config, title, category_name, category_id,
            item_condition=item_condition,
            product_lookup=product_lookup,
        )

        ebay_offer = dict(existing)
        ebay_offer['price_source'] = result['source']
        ebay_offer['price_comps']  = result['comps']
        if result.get('comp_items'):
            ebay_offer['price_comps']['items'] = result['comp_items']
        ebay_offer['priced_at']    = result['queried_at']

        suggested = result['price']
        if suggested is not None:
            # Launch price: max comp rounded up to next .99 — this is the initial
            # listed price, creating a visible "discount" when the repricer lowers
            # it to target (p25) after the configured period.
            comps = result['comps']
            launch = to_99(comps['max'] * 1.10) if comps.get('max') else suggested
            if launch < suggested:
                # The floor can push the target (p25) above a launch derived from
                # raw junk comps — never launch below the markdown target.
                launch = to_99(suggested)

            # PP-FREESHIP-001: when free_shipping_enabled, absorb shipping cost
            # into the listing price and mark the item for a free-shipping policy.
            _ship_cost_used = 0.0
            if self.config.get('free_shipping_enabled'):
                _item_ship = item.get('shipping_cost')
                ship_cost = float(
                    _item_ship if _item_ship not in (None, '')
                    else self.config.get('default_shipping_cost', 0.0)
                )
                if ship_cost > 0:
                    _ship_cost_used = ship_cost
                    base_launch = launch
                    launch = freeship_price(launch, ship_cost)
                    item['free_shipping'] = True
                    log.info('ebay_price: %s freeship → $%.2f (base=$%.2f + ship=$%.2f)',
                             sku, launch, base_launch, ship_cost)

            ebay_offer['price']        = launch
            # target_price (repricer floor) must absorb the same shipping cost so the
            # repricer never marks down to a price that leaves shipping uncovered.
            ebay_offer['target_price'] = (
                freeship_price(suggested, _ship_cost_used) if _ship_cost_used > 0
                else suggested
            )
            draft['price']             = launch      # staged at launch price

            # PP-STRIKE-001: record MSRP as originalRetailPrice when it exceeds
            # the launch price, so the offer body gets a strikethrough display.
            msrp_raw = product_lookup.get('msrp')
            if msrp_raw:
                try:
                    msrp_float = float(msrp_raw)
                    if msrp_float > launch:
                        draft['original_retail_price'] = round(msrp_float, 2)
                        log.info('%s: original_retail_price=%.2f from product_lookup.msrp',
                                 sku, msrp_float)
                except (TypeError, ValueError):
                    pass

            log.info('ebay_price: %s → launch=$%.2f target=$%.2f (%d comps, %s, conf=%s)',
                     sku, launch, suggested,
                     comps.get('count', 0), result['source'],
                     result.get('price_confidence', '?'))
            tgw_logging.log_event('ebay_price_set', sku=sku,
                                  price=launch,
                                  target_price=suggested,
                                  source=result['source'],
                                  price_confidence=result.get('price_confidence'),
                                  comps=comps)
        else:
            ebay_offer['price'] = None
            log.warning('ebay_price: %s — insufficient comps, price left null', sku)
            tgw_logging.log_event('ebay_price_no_comps', sku=sku, title=title[:60])

        draft['price_confidence'] = result.get('price_confidence', 'low')

        item['ebay_offer']    = ebay_offer
        item['draft_listing'] = draft

        # Re-score quality now that price_comps are present (comp_pts were 0 at draft time)
        try:
            from tgw.listing_quality import score_draft
            draft['quality'] = score_draft(item).to_dict()
        except Exception as exc:
            log.warning('ebay_price: quality rescore failed for %s: %s', sku, exc)

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_price:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass

        # Only stage when we have a price — no point creating an offer with no price
        if suggested is not None:
            try:
                state_machine.enqueue_job(
                    queue_name='ebay_stage',
                    payload={'sku': sku},
                    dedupe_key=f'ebay_stage:{sku}',
                    max_attempts=5,
                )
            except psycopg2.errors.UniqueViolation:
                pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-price-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayPriceWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
