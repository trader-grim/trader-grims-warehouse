"""
tgw.workers.ebay_stage — Push a priced draft to eBay as an UNPUBLISHED offer.

Creates the eBay inventory item and offer without publishing.  The draft
appears immediately in Seller Hub where the operator can review, edit, and
list it.  This is the stopgap publishing interface until the full revision
system (PP-REVISION-001) is built.

Enqueued automatically by ebay_price when a price is successfully set.
Skipped (idempotent) if ebay_offer.offer_id already exists.

Guards:
  - draft_listing.price or ebay_offer.price must be set
  - ebay_photos must be populated (photos on eBay EPS)
  If photos aren't uploaded yet the job retries — ebay_upload runs concurrently.

Queue name: ebay_stage
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.sync import stage_draft
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_stage'


def _format_ebay_error(body: str, status: int) -> str:
    """Extract human-readable messages from eBay error JSON."""
    try:
        errs = json.loads(body).get('errors', [])
        msgs = [e.get('longMessage') or e.get('message', '') for e in errs if e.get('longMessage') or e.get('message')]
        if msgs:
            return '; '.join(msgs)
    except Exception:
        pass
    return f'HTTP {status}: {body[:300]}'


class EbayStageWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        force = bool(payload.get('force'))  # bypass guards — update a live listing in place
        if not sku:
            raise HardFailure('ebay_stage job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        # Ordering guard (session 42, same class as ebay_publish's): a directly-
        # triggered stage must not push while draft/price/upload for the same SKU
        # are still in flight — it would stage the OLD draft.
        upstream = state_machine.active_jobs_for_sku(
            sku, ['ebay_draft', 'ebay_price', 'ebay_upload'])
        if upstream:
            raise RuntimeError(
                f'{sku}: pipeline steps still running ({", ".join(upstream)}) '
                f'— stage waits for them (will retry)')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        # Guard (invariant C9, session 42): uninspected AI-regenerated content
        # never goes live automatically. A force update of a LIVE listing is
        # only executed when the job carries origin='operator' (set by the UI /
        # CLI paths where a human pushed the button). Pipeline-internal force
        # jobs against live listings are refused here regardless of who
        # enqueued them — Dave: "we cannot have uninspected AI changes going
        # live automatically yet. They are rarely correct so far."
        existing_listing = item.get('ebay_listing', {})
        _live = (existing_listing.get('status') == 'Active'
                 or item.get('ebay_offer', {}).get('status') == 'PUBLISHED')
        if force and _live and payload.get('origin') != 'operator':
            log.warning('ebay_stage: %s force-update of LIVE listing blocked — '
                        'no operator origin (uninspected AI content, C9)', sku)
            tgw_logging.log_event('ebay_stage_blocked_uninspected', sku=sku)
            return
        if existing_listing.get('status') == 'Active':
            listing_id = existing_listing.get('listing_id', '')
            if not force:
                log.warning(
                    'ebay_stage: %s is already live (listingId=%s) — skipping '
                    '(use force=True to update in place)',
                    sku, listing_id,
                )
                tgw_logging.log_event('ebay_stage_skipped', sku=sku,
                                      reason='already_active_listing',
                                      listing_id=str(listing_id))
                return
            log.info('ebay_stage: %s is live (listingId=%s) — updating in place (force)',
                     sku, listing_id)
            tgw_logging.log_event('ebay_stage_update', sku=sku, listing_id=str(listing_id))

        # Guard: item was previously listed via Trading API — must not create a
        # duplicate Inventory API offer until the legacy listing is resolved.
        legacy_item_number = item.get('Item number') or item.get('item_number')
        if legacy_item_number and not item.get('legacy_listing_resolved'):
            log.warning(
                'ebay_stage: %s has legacy eBay Item# %s — skipping to avoid '
                'duplicate listing; resolve via relist workflow first',
                sku, legacy_item_number,
            )
            tgw_logging.log_event('ebay_stage_skipped', sku=sku,
                                  reason='legacy_trading_api_listing',
                                  item_number=str(legacy_item_number))
            return

        # Idempotent: already staged — skip unless force (update)
        existing_offer_id = item.get('ebay_offer', {}).get('offer_id')
        if existing_offer_id and not force:
            log.info('ebay_stage: %s already staged (offerId=%s) — skipping',
                     sku, existing_offer_id)
            tgw_logging.log_event('ebay_stage_skipped', sku=sku,
                                  offer_id=existing_offer_id)
            return

        draft = item.get('draft_listing', {})
        if not draft:
            # Retryable — item may still be working through ai_identify/ebay_draft
            raise RuntimeError(
                f'{sku}: no draft_listing yet — waiting for pipeline to complete'
            )

        # Price must be set (either by ebay_price or manually)
        price = draft.get('price') or item.get('ebay_offer', {}).get('price')
        if price is None:
            # Retryable — ebay_price may still be running
            raise RuntimeError(
                f'{sku}: no price yet — waiting for ebay_price or manual price set'
            )

        # Never-raise guard (invariant C5 extended, session 42 incident): a force
        # re-stage of a live/published offer must not RAISE the price eBay already
        # has. Reductions made before the s41 reducer fix never persisted to
        # draft_listing.price, so stale-higher draft prices silently reverted live
        # markdowns when re-staged (5 confirmed live, 2026-07-02). Deliberate
        # operator raises pass `allow_price_raise` in the payload. The clamp is
        # also persisted, healing the stale draft as we touch it.
        offer_price = item.get('ebay_offer', {}).get('price')
        offer_live = (item.get('ebay_offer', {}).get('status') == 'PUBLISHED'
                      or existing_listing.get('status') == 'Active')
        if (force and offer_live and offer_price is not None
                and float(price) > float(offer_price)
                and not payload.get('allow_price_raise')):
            log.warning('ebay_stage: %s never-raise clamp: draft $%s > live $%s — '
                        'pushing live price (pass allow_price_raise to override)',
                        sku, price, offer_price)
            stale_draft_price = float(price)
            tgw_logging.log_event('ebay_stage_never_raise_clamp', sku=sku,
                                  draft_price=stale_draft_price,
                                  live_price=float(offer_price))
            price = offer_price
            draft['price'] = float(offer_price)
            item.setdefault('price_history', []).append({
                'ts': datetime.now(timezone.utc).isoformat(),
                'price': float(offer_price), 'previous_price': stale_draft_price,
                'stage': None, 'label': 'never_raise_clamp',
                'source': 'ebay_stage_guard',
            })
            fence_patch_item(self.config, sku, {
                'draft_listing': {'price': float(offer_price)},
                'price_history': item['price_history'],
            })

        # Photos must be uploaded — retryable if ebay_upload hasn't finished yet
        image_urls = draft.get('imageUrls') or [e['url'] for e in item.get('ebay_photos', [])]
        if not image_urls:
            raise RuntimeError(
                f'{sku}: no eBay photo URLs yet — waiting for ebay_upload (will retry)'
            )
        image_urls = image_urls[:24]  # eBay max is 24 images per listing

        # Phase 3 — EPID association: look up eBay Catalog EPID for barcoded items.
        # Scope commerce.catalog.readonly required; silently skipped if not granted.
        if not item.get('epid'):
            from tgw.apis.ebay.catalog import lookup_epid
            from tgw.apis.lookup.base import barcode_from_item
            barcode, _btype = barcode_from_item(item)
            if barcode:
                epid = lookup_epid(self.config, barcode)
                if epid:
                    item['epid'] = epid
                    fence_patch_item(self.config, sku, {'epid': epid})
                    log.info('%s: EPID %s cached (barcode %s)', sku, epid, barcode)
                    tgw_logging.log_event('ebay_epid_found', sku=sku,
                                          epid=epid, barcode=barcode)

        log.info('ebay_stage: staging %s as UNPUBLISHED offer (price=$%s)', sku, price)
        tgw_logging.log_event('ebay_stage_start', sku=sku, price=price)

        try:
            result = stage_draft(self.config, sku, item)
        except ValueError as exc:
            raise HardFailure(str(exc)) from exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                raw = exc.response.text if exc.response is not None else ''
                msg = _format_ebay_error(raw, status)
                pipeline_error = {
                    'worker': 'ebay_stage',
                    'error': msg,
                    'raw': raw[:800],
                    'at': datetime.now(timezone.utc).isoformat(),
                }
                fence_patch_item(self.config, sku, {'pipeline_error': pipeline_error})
                raise HardFailure(f'{sku}: eBay rejected staging: {msg}') from exc
            raise  # transient — base class retries

        # Merge into ebay_offer block (preserves price_comps etc from ebay_price)
        ebay_offer = dict(item.get('ebay_offer', {}))
        ebay_offer['offer_id']    = result['offer_id']
        # Preserve PUBLISHED status on force-updates of live listings — the offer
        # remains live on eBay; only our content changed.
        if item.get('ebay_listing', {}).get('status') == 'Active':
            ebay_offer['status'] = 'PUBLISHED'
        else:
            ebay_offer['status'] = 'UNPUBLISHED'
        ebay_offer['staged_at']   = datetime.now(timezone.utc).isoformat()
        ebay_offer['staged_price'] = float(price)  # what was actually submitted to eBay

        item['ebay_offer'] = ebay_offer

        # PP-EBAY-SNAPSHOT-001: snapshot what we PUT so photo verify and repush
        # have a ground-truth reference for what eBay should be showing.
        ebay_submitted = {
            'inventory_item': result['inventory_item'],
            'staged_at': ebay_offer['staged_at'],
        }
        item['ebay_submitted'] = ebay_submitted
        fence_ebay_write(self.config, sku, ebay_offer=ebay_offer, ebay_submitted=ebay_submitted)

        log.info('ebay_stage: %s staged → offerId=%s (visible in Seller Hub)',
                 sku, result['offer_id'])
        tgw_logging.log_event('ebay_stage_complete', sku=sku,
                              offer_id=result['offer_id'])

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_stage:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass

        # If the item was previously published, republish after staging.
        # eBay sets the offer back to UNPUBLISHED on any updateOffer call
        # (including category changes), so we must re-publish to restore live status.
        if item.get('ebay_listing', {}).get('listing_id'):
            try:
                # Invariant C10: propagate operator provenance down the chain.
                state_machine.enqueue_job(
                    queue_name='ebay_publish',
                    payload={'sku': sku,
                             **({'origin': 'operator'}
                                if payload.get('origin') == 'operator' else {})},
                    dedupe_key=f'ebay_publish:{sku}',
                    max_attempts=3,
                )
                log.info('%s: was published — queued ebay_publish to restore live status', sku)
            except psycopg2.errors.UniqueViolation:
                pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-stage-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayStageWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
