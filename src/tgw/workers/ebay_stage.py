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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.sync import enqueue_post_push_sync, stage_draft
from tgw.ebay.sync import extract_ebay_error_field as _extract_ebay_error_field
from tgw.ebay.sync import format_ebay_error as _format_ebay_error
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_stage'


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
        existing_listing = item.get('ebay_listing', {})

        # Guard: item was previously listed via Trading API — must not create a
        # duplicate Inventory API offer until the legacy listing is resolved.
        #
        # PP-PHOTOSYNC-001 P10 (session 43): runs BEFORE the C9 gate below on
        # purpose. The original ordering put this after C9's return, so a
        # background/no-origin job against an Active legacy listing never
        # reached this code at all — it was silently absorbed by the generic
        # "uninspected content blocked" log line, and the legacy-specific fact
        # (which needs the relist workflow, not just a human pushing a button)
        # was never recorded anywhere durable. That's the exact bug Dave
        # flagged live (session 43): "we ignored and did not record the error
        # message... this would have been resolved if we had been collecting
        # and using all of the data" — plus a standing instruction to
        # regularly check for and repair instances of it. Detection now runs
        # unconditionally and persists durably regardless of who triggered the
        # job; only the ACTUAL REPAIR (a live write to eBay) stays gated on
        # operator origin, same as every other C9-covered action.
        legacy_item_number = item.get('Item number') or item.get('item_number')
        if legacy_item_number and not item.get('legacy_listing_resolved'):
            now_iso = datetime.now(timezone.utc).isoformat()
            log.warning(
                'ebay_stage: %s has legacy eBay Item# %s — checking for a '
                'genuine duplicate listing before proceeding',
                sku, legacy_item_number,
            )
            tgw_logging.log_event('ebay_stage_skipped', sku=sku,
                                  reason='legacy_trading_api_listing',
                                  item_number=str(legacy_item_number))

            legacy_blocked = {
                'listing_id':      existing_listing.get('listing_id', ''),
                'item_number':     str(legacy_item_number),
                'detected_at':     now_iso,
                'duplicate_check': None,
            }

            # PP-PHOTOSYNC-001 P10 (session 43): Dave — a month-long gap in
            # our own Inventory tooling meant occasionally managing listings
            # directly via Seller Hub, "a known consequence of my actions...
            # it could happen again and needs an auto repair path. Check for
            # both specifically, then resolve." Only an operator-triggered
            # force update runs the live check (C9: no eBay-state-changing
            # decision from an uninspected background job); a background hit
            # only ever records the finding above and stops here.
            if force and payload.get('origin') == 'operator':
                from tgw.ebay.pull import check_legacy_duplicate_listing
                dup = check_legacy_duplicate_listing(
                    self.config, sku, legacy_blocked['listing_id'])
                legacy_blocked['duplicate_check'] = dup

                if dup.get('ok') and dup.get('match'):
                    # Confirmed: one listing, addressable via both APIs — not
                    # a duplicate. Safe to resolve and fall through to the
                    # standard Inventory-API staging below (the guard's
                    # premise — "this must be a separate classic listing" —
                    # is false for this SKU).
                    item['legacy_listing_resolved'] = True
                    log.info('ebay_stage: %s legacy listing %s confirmed NOT a '
                            'duplicate (Inventory offer matches) — resolved, '
                            'proceeding via standard path', sku, legacy_item_number)
                    tgw_logging.log_event('ebay_stage_legacy_resolved_no_duplicate',
                                          sku=sku, listing_id=legacy_blocked['listing_id'])
                    fence_patch_item(self.config, sku, {
                        'legacy_listing_blocked':  legacy_blocked,
                        'legacy_listing_resolved': True,
                    })
                    # Deliberately no `return` — falls through to the normal
                    # staging path below with legacy_listing_resolved now set.
                else:
                    log.warning('ebay_stage: %s legacy listing %s — duplicate '
                               'risk detected or unverifiable, NOT resolving: %s',
                               sku, legacy_item_number, dup)
                    tgw_logging.log_event('ebay_stage_legacy_duplicate_risk',
                                          sku=sku, detail=dup)
                    fence_patch_item(self.config, sku, {'legacy_listing_blocked': legacy_blocked})
                    return
            else:
                fence_patch_item(self.config, sku, {'legacy_listing_blocked': legacy_blocked})
                return

        # Guard (invariant C9, session 42): uninspected AI-regenerated content
        # never goes live automatically. A force update of a LIVE listing is
        # only executed when the job carries origin='operator' (set by the UI /
        # CLI paths where a human pushed the button). Pipeline-internal force
        # jobs against live listings are refused here regardless of who
        # enqueued them — Dave: "we cannot have uninspected AI changes going
        # live automatically yet. They are rarely correct so far."
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

        # Non-leaf category guard (todo #1395 / PP-DEADLETTER-001): ebay_draft
        # falls back to category_id '99' ("Everything Else") when it can't
        # resolve a real category — that fallback is explicitly non-leaf
        # (ebay_draft.py comment: "eBay will prompt"). eBay's Inventory API
        # unconditionally rejects createOrReplaceOffer/publish for a non-leaf
        # category ("The category selected is not a leaf category."), so
        # staging with '99' always burns a live API call for a guaranteed
        # HardFailure and lands in dead_letter with no actionable trail (17
        # confirmed instances, 2026-07-05 batch). Block it here — before the
        # API call — and persist a durable, queryable finding (invariant
        # C11) instead, same shape as the price/title guards below.
        category_id = draft.get('category_id')
        if category_id == '99':
            fence_patch_item(self.config, sku, {'pipeline_error': {
                'code':   'category_not_leaf',
                'detail': ("draft_listing.category_id is the '99' Everything "
                           "Else fallback (non-leaf) — eBay always rejects "
                           "staging/publishing with it. Operator must select "
                           "a real leaf category in the editor before listing."),
                'ts':     datetime.now(timezone.utc).isoformat(),
                'source': 'ebay_stage',
            }})
            tgw_logging.log_event('ebay_stage_category_not_leaf', sku=sku)
            raise HardFailure(
                f"{sku}: draft_listing.category_id is fallback '99' (Everything "
                f"Else, non-leaf) — operator must select a leaf category "
                f"before staging"
            )

        # Price must be set in draft_listing — the operator-reviewed surface.
        # Session 45 (tgw202605052336026): the old fallback to ebay_offer.price
        # published a STALE machine price ($40.99, browse-comps, stamped weeks
        # earlier by the pricing system Dave disabled in s42) while the editor
        # showed an empty price — the operator clicked List and went live with
        # data nobody had reviewed. ebay_price writes draft.price when it runs,
        # so draft.price is the only legitimate source; a bare ebay_offer.price
        # is by definition un-reviewed leftovers (C9: uninspected machine
        # content never reaches a live listing — prices are content).
        price = draft.get('price')
        if price is None:
            stale = (item.get('ebay_offer') or {}).get('price')
            if payload.get('origin') == 'operator':
                # Operator pressed List on an unpriced item: fail loudly and
                # persist the finding so the editor can render "needs price"
                # (invariant C11 — a finding, not a log line).
                fence_patch_item(self.config, sku, {'pipeline_error': {
                    'code':   'no_price_set',
                    'detail': ('draft_listing.price is empty — set a price in '
                               'the editor before listing'
                               + (f' (ignored stale ebay_offer.price={stale}'
                                  f' from disabled auto-pricer)' if stale is not None else '')),
                    'ts':     datetime.now(timezone.utc).isoformat(),
                    'source': 'ebay_stage',
                }})
                raise HardFailure(
                    f'{sku}: no price set in draft_listing — operator must price '
                    f'the item in the editor (stale ebay_offer.price={stale} ignored)'
                )
            # Background chain — ebay_price may still be running
            raise RuntimeError(
                f'{sku}: no price yet — waiting for ebay_price or manual price set'
            )

        # Title length guard (2026-07-10): eBay rejects any title over 80 chars
        # outright (errorId 25718, "title should be between 1 and 80
        # characters"). tgw202605051752520/051913468/051936445 all reached
        # eBay's API before finding this out, dead-lettering after burning a
        # real API call for something knowable locally. seo/title.py's
        # enhance_title() deliberately does NOT auto-truncate (Dave: eBay's
        # own bulk-CSV editor loads the full title and lets the operator
        # trim it by double-click-deleting words — faster and more accurate
        # than an automated word-boundary chop) — the full title stays in
        # draft_listing.title so the editor can offer exactly that. This
        # guard is what actually stops the wasted API round-trip: same
        # C11/no_price_set shape, blocks staging until the operator trims it.
        title = draft.get('title') or ''
        if len(title) > 80:
            fence_patch_item(self.config, sku, {'pipeline_error': {
                'code':   'title_too_long',
                'detail': (f'draft_listing.title is {len(title)} chars — eBay '
                           f'allows at most 80. Trim it in the editor before listing.'),
                'ts':     datetime.now(timezone.utc).isoformat(),
                'source': 'ebay_stage',
            }})
            raise HardFailure(
                f'{sku}: title is {len(title)} chars, over eBay\'s 80-char limit — '
                f'operator must trim it in the editor'
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
                # Canonical pipeline_error schema (broker B1b): guard findings
                # and rejections share {code, detail, ts, source}; rejections
                # add the raw eBay body. Reader shim in http_server still
                # renders the legacy {worker, error, raw, at} on old items.
                pipeline_error = {
                    'code':   'ebay_rejected',
                    'detail': msg,
                    'raw':    raw[:800],
                    'ts':     datetime.now(timezone.utc).isoformat(),
                    'source': 'ebay_stage',
                    # PP-CONDITION-ENUM-001 / todo #1562: best-effort field
                    # attribution (e.g. "condition_enum") so the item detail
                    # page can flag exactly the offending draft field red on
                    # load instead of leaving the operator to guess from the
                    # generic wrapper text. None when eBay's body doesn't
                    # name a field — never forced.
                    'field':  _extract_ebay_error_field(raw),
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
        fence_ebay_write(self.config, sku, ebay_offer=ebay_offer, ebay_submitted=ebay_submitted,
                          allow_protected=["staged_at"])

        log.info('ebay_stage: %s staged → offerId=%s (visible in Seller Hub)',
                 sku, result['offer_id'])
        tgw_logging.log_event('ebay_stage_complete', sku=sku,
                              offer_id=result['offer_id'])

        try:
            state_machine.enqueue_catalog_rebuild(f'ebay_stage:{sku}')
        except psycopg2.errors.UniqueViolation:
            pass

        # Invariant C14 (2026-07-16 incident): this worker runs on nearly
        # every real operator edit ("Update Listing" on an already-live
        # item enqueues ebay_stage directly, never ebay_publish) yet never
        # refreshed the local ebay_live mirror itself — only ebay_publish
        # did (todo #1445), and only when this worker's own already-live
        # republish trigger below happens to fire. Call it unconditionally
        # here so the mirror refreshes on every successful stage, not just
        # the subset that also happens to republish.
        enqueue_post_push_sync(sku)

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
                    entity_type='item',
                    entity_id=sku,
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
