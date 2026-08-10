"""
tgw.workers.ebay_publish — publish a priced draft listing to eBay.

Triggered manually after a human reviews draft_listing and sets price.
Guards: draft_listing must exist, price must be non-null, photos must be uploaded.

On success: writes ebay_listing block to item JSON and enqueues catalog_rebuild.

Queue name: ebay_publish
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.ebay import conditions
from tgw.apis.ebay.client import ebay_get, ebay_put
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.draft_sync import baseline_fields
from tgw.ebay.pricing import to_99
from tgw.ebay.sync import enqueue_post_push_sync, publish_offer
from tgw.ebay.sync import extract_ebay_error_field as _extract_ebay_error_field
from tgw.ebay.sync import format_ebay_error as _format_ebay_error
from tgw.errors import TreatmentFailure
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_publish'


def _build_reprice_schedule(stages: List[Dict[str, Any]],
                             comps: Dict[str, Any],
                             category_id: str,
                             category_defaults: Dict[str, float],
                             now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Compute the full reprice schedule from reprice_stages config.

    Each entry: {stage, label, price, due_at, done_at}
    Prices come from comps[percentile] or category_defaults fallback.
    Launch price (stage 0) is rounded up to next .99.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    schedule = []
    for i, stage in enumerate(stages):
        pct_key = stage.get('percentile', 'p25')
        label   = stage.get('label', f'stage{i}')
        days    = int(stage.get('days', 0))

        raw_price: Optional[float] = None
        if comps and pct_key in comps and comps[pct_key]:
            raw_price = float(comps[pct_key])
        elif category_id and category_id in category_defaults:
            raw_price = float(category_defaults[category_id])

        if raw_price is not None:
            price = to_99(raw_price) if days == 0 else round(raw_price, 2)
        else:
            price = None
            log.warning('reprice_schedule: no price data for stage %r (pct=%s)',
                        label, pct_key)

        due_at = (now + timedelta(days=days)).isoformat()
        schedule.append({
            'stage':   i,
            'label':   label,
            'price':   price,
            'due_at':  due_at,
            'done_at': None,
        })
    return schedule


class EbayPublishWorker(QueueWorker):

    def _provider_effect_mode(self) -> str:
        migration = self.config.get('workflow_migration')
        if migration is None and isinstance(self.config.get('raw'), dict):
            migration = self.config['raw'].get('workflow_migration')
        migration = migration if isinstance(migration, dict) else {}
        mode = migration.get('ebay_publish_provider_effect', 'legacy')
        if mode not in {'legacy', 'workflow'}:
            raise HardFailure(
                f'invalid workflow_migration.ebay_publish_provider_effect mode {mode!r}'
            )
        return mode

    def _provider_identity(self) -> str:
        migration = self.config.get('workflow_migration')
        if migration is None and isinstance(self.config.get('raw'), dict):
            migration = self.config['raw'].get('workflow_migration')
        migration = migration if isinstance(migration, dict) else {}
        value = migration.get('ebay_provider_identity')
        return value if isinstance(value, str) else ''

    @staticmethod
    def _require_provider_binding(payload: Dict[str, Any]) -> None:
        required = (
            'treatment_id', 'treatment_version', 'graph_id', 'goal_profile_id',
            'goal_profile_version', 'object_generation', 'condition_hash',
        )
        missing = [key for key in required
                   if not isinstance(payload.get(key), str) or not payload[key].strip()]
        if missing:
            raise HardFailure('workflow provider effect missing binding: ' + ', '.join(missing))

    def _publish_with_provider_effect(
        self, payload: Dict[str, Any], sku: str, offer_id: str,
        item: Dict[str, Any],
    ) -> Dict[str, Any]:
        from tgw.provider_effects import (
            ProviderEffectConflict,
            ProviderEffectReconciliationRequired,
            finish_provider_effect,
            reserve_and_begin_authorized_effect,
        )
        from tgw.workflow.operator_authority import listing_content_identity

        provider_identity = self._provider_identity()
        try:
            effect = reserve_and_begin_authorized_effect(
                authority_id=payload.get('operator_authority_id', ''),
                authority_scope='publish', authority_binding={
                    'entity_id': sku, 'goal_profile_id': payload['goal_profile_id'],
                    'goal_profile_version': payload['goal_profile_version'],
                    'object_generation': payload['object_generation'],
                    'pre_authority_condition_hash': payload.get(
                        'pre_authority_condition_hash', ''),
                    'content_identity': listing_content_identity(item),
                    'provider_identity': provider_identity or '',
                }, provider='ebay', operation='publish-offer', entity_type='item',
                entity_id=sku, object_generation=payload['object_generation'],
                graph_id=payload['graph_id'], treatment_id=payload['treatment_id'],
                treatment_version=payload['treatment_version'],
                condition_hash=payload['condition_hash'], request={'offer_id': offer_id},
            )
        except ProviderEffectConflict as exc:
            raise HardFailure(f'{sku}: provider effect admission failed: {exc}') from exc
        except ProviderEffectReconciliationRequired as exc:
            raise TreatmentFailure(
                f'{sku}: prior provider dispatch requires reconciliation',
                self._provider_effect_receipt(
                    payload, sku, exc.record.effect_id, 'reconciliation_required',
                    'PROVIDER_EFFECT_UNFINISHED', exc.record.result,
                ),
            ) from exc
        if effect.state == 'succeeded' and effect.result:
            return {**effect.result, '_provider_effect_id': effect.effect_id}
        if effect.state == 'rejected':
            raise TreatmentFailure(
                f'{sku}: prior provider publish was definitively rejected',
                self._provider_effect_receipt(
                    payload, sku, effect.effect_id, 'failed',
                    'PROVIDER_EFFECT_REJECTED', effect.result,
                ),
            )
        try:
            result = publish_offer(self.config, offer_id)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                rejected = finish_provider_effect(
                    effect.effect_id, state='rejected',
                    error_detail=f'HTTP {status}: {exc}',
                )
                raise TreatmentFailure(
                    f'{sku}: provider definitively rejected publish',
                    self._provider_effect_receipt(
                        payload, sku, rejected.effect_id, 'failed',
                        'PROVIDER_EFFECT_REJECTED', None,
                    ),
                ) from exc
            ambiguous = finish_provider_effect(
                effect.effect_id, state='ambiguous',
                error_detail=f'{type(exc).__name__}: {exc}',
            )
            raise TreatmentFailure(
                f'{sku}: provider publish outcome ambiguous; reconciliation required',
                self._provider_effect_receipt(
                    payload, sku, ambiguous.effect_id, 'ambiguous',
                    'PROVIDER_EFFECT_AMBIGUOUS', None,
                ),
            ) from exc
        except Exception as exc:
            ambiguous = finish_provider_effect(
                effect.effect_id, state='ambiguous',
                error_detail=f'{type(exc).__name__}: {exc}',
            )
            raise TreatmentFailure(
                f'{sku}: provider publish outcome ambiguous; reconciliation required',
                self._provider_effect_receipt(
                    payload, sku, ambiguous.effect_id, 'ambiguous',
                    'PROVIDER_EFFECT_AMBIGUOUS', None,
                ),
            ) from exc
        finish_provider_effect(effect.effect_id, state='succeeded', result=result)
        return {**result, '_provider_effect_id': effect.effect_id}

    @staticmethod
    def _provider_effect_receipt(
        payload: Dict[str, Any], sku: str, effect_id: str, outcome: str,
        reason_code: str, provider_result: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        return {
            'receipt_schema_id': 'treatment-receipt/v1',
            'treatment_id': payload['treatment_id'],
            'treatment_version': payload['treatment_version'],
            'graph_id': payload['graph_id'], 'outcome': outcome,
            'established_conditions': [], 'artifacts': [f'item:{sku}'],
            'evidence': {
                'reason_code': reason_code, 'provider': 'ebay',
                'provider_effect_id': effect_id,
                'provider_result': provider_result,
                'operator_origin': payload.get('origin') == 'operator',
            },
        }

    @staticmethod
    def _governed_success_receipt(
        payload: Dict[str, Any], sku: str,
    ) -> Dict[str, Any] | None:
        """Return a workflow-bound receipt, or legacy-compatible ``None``."""
        required = (
            'treatment_id', 'treatment_version', 'graph_id',
            'goal_profile_id', 'goal_profile_version', 'object_generation',
        )
        if not all(isinstance(payload.get(key), str) and payload[key].strip()
                   for key in required):
            return None
        return {
            'receipt_schema_id': 'treatment-receipt/v1',
            'treatment_id': payload['treatment_id'],
            'treatment_version': payload['treatment_version'],
            'graph_id': payload['graph_id'],
            'outcome': 'satisfied',
            'established_conditions': ['published'],
            'artifacts': [f'item:{sku}'],
        }

    @staticmethod
    def _projection_reconciliation_failure(
        payload: Dict[str, Any], sku: str, item: Dict[str, Any],
        reason_code: str, detail: str,
    ) -> TreatmentFailure:
        listing = item.get('ebay_listing') or {}
        offer = item.get('ebay_offer') or {}
        return TreatmentFailure(
            f'{sku}: provider publish confirmed but post-publish projection '
            f'is incomplete; reconciliation required: {detail}',
            {
                'receipt_schema_id': 'treatment-receipt/v1',
                'treatment_id': payload.get('treatment_id', 'ebay-publish'),
                'treatment_version': payload.get('treatment_version', '1'),
                'graph_id': payload.get('graph_id'),
                'outcome': 'reconciliation_required',
                'established_conditions': [],
                'artifacts': [f'item:{sku}'],
                'evidence': {
                    'reason_code': reason_code,
                    'provider': 'ebay',
                    'offer_id': offer.get('offer_id') or listing.get('offer_id'),
                    'listing_id': listing.get('listing_id'),
                    'listing_url': listing.get('listing_url'),
                    'provider_status': offer.get('status') or listing.get('status'),
                    'provider_effect_id': listing.get('provider_effect_id'),
                    'projection_error': detail,
                    'operator_origin': payload.get('origin') == 'operator',
                },
            },
        )

    def _refresh_photo_verify(self, sku: str, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """PP-EBAY-SNAPSHOT-001 Phase 2 / PP-PHOTOSYNC-001 P1: verify photos
        actually live on eBay match what we submitted. One extra GET; logged
        but never blocks the caller. Must run on EVERY path that pushes new
        photo content live — not just first publish — or photo_verify goes
        stale the moment an already-Active item gets a photo update (found
        s43: an operator ebay_update pushed 24 photos live but photo_verify
        kept showing the original publish's 9/9 until a manual ebay-pull)."""
        try:
            live = ebay_get(self.config, f'/sell/inventory/v1/inventory_item/{sku}')
            confirmed = live.get('product', {}).get('imageUrls', [])
            submitted = (
                item.get('ebay_submitted', {})
                    .get('inventory_item', {})
                    .get('product', {})
                    .get('imageUrls')
                or item.get('draft_listing', {}).get('imageUrls', [])
            )
            photo_verify = {
                'submitted_count': len(submitted),
                'confirmed_count': len(confirmed),
                'verified_at':     datetime.now(timezone.utc).isoformat(),
            }
            if len(confirmed) < len(submitted):
                log.warning('%s: photo count mismatch — submitted=%d confirmed=%d',
                            sku, len(submitted), len(confirmed))
                tgw_logging.log_event('ebay_photo_verify_mismatch', sku=sku,
                                      submitted=len(submitted), confirmed=len(confirmed))
            else:
                log.info('%s: photo verify OK — %d/%d confirmed', sku, len(confirmed), len(submitted))
                tgw_logging.log_event('ebay_photo_verify_ok', sku=sku, confirmed=len(confirmed))
            return photo_verify
        except Exception as exc:
            log.warning('%s: photo verify GET failed (non-fatal): %s', sku, exc)
            return None

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_publish job missing sku in payload')
        if job.get('entity_type') not in (None, 'item'):
            raise HardFailure('ebay_publish job entity_type must be item')
        if job.get('entity_id') not in (None, sku):
            raise HardFailure('ebay_publish job entity_id does not match payload sku')
        effect_mode = self._provider_effect_mode()
        if effect_mode == 'workflow':
            self._require_provider_binding(payload)

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        # Ordering guard (session 42): 'List on eBay' enqueues publish alongside
        # the draft chain — publish once went live with the OLD staged offer while
        # the fresh draft was still generating. Wait for upstream stages to drain.
        upstream = state_machine.active_jobs_for_sku(
            sku, ['ebay_draft', 'ebay_price', 'ebay_upload', 'ebay_stage'])
        if upstream:
            raise RuntimeError(
                f'{sku}: pipeline steps still running ({", ".join(upstream)}) '
                f'— publish waits for them (will retry)')

        item = json.loads(json_path.read_text(encoding='utf-8'))
        if item.get('sku') != sku:
            raise HardFailure('ebay_publish canonical item sku does not match payload sku')

        # Idempotent: a replayed/directly-enqueued job for a live item must not
        # re-publish or overwrite the reprice_schedule (markdown clock).
        existing_listing = item.get('ebay_listing', {})
        if existing_listing.get('status') == 'Active':
            governed = self._governed_success_receipt(payload, sku) is not None
            published_at = existing_listing.get('published_at')
            projection_complete = (
                item.get('draft_listing_state') == 'baseline'
                and isinstance(published_at, str)
                and bool(published_at.strip())
                and isinstance(item.get('baseline_at'), str)
                and item['baseline_at'] == published_at
                and (item.get('ebay_offer') or {}).get('published_at') == published_at
            )
            if governed and not projection_complete:
                raise self._projection_reconciliation_failure(
                    payload, sku, item,
                    'POST_PUBLISH_PROJECTION_INCOMPLETE',
                    'canonical listing is Active but its listing/offer publication '
                    'timestamp is not exactly bound to the baseline completion marker',
                )
            if governed and effect_mode == 'workflow':
                from tgw.provider_effects import (
                    ProviderEffectConflict,
                    ProviderEffectReconciliationRequired,
                    validate_succeeded_authorized_effect,
                )
                effect_id = existing_listing.get('provider_effect_id')
                authority_id = payload.get('operator_authority_id')
                try:
                    effect = validate_succeeded_authorized_effect(
                        effect_id=effect_id or '', authority_id=authority_id or '',
                        authority_scope='publish', authority_binding={
                            'entity_id': sku,
                            'goal_profile_id': payload['goal_profile_id'],
                            'goal_profile_version': payload['goal_profile_version'],
                            'object_generation': payload['object_generation'],
                            'pre_authority_condition_hash': payload.get(
                                'pre_authority_condition_hash', ''),
                            'provider_identity': self._provider_identity(),
                        }, expected_binding={
                            'provider': 'ebay', 'operation': 'publish-offer',
                            'entity_type': 'item', 'entity_id': sku,
                            'object_generation': payload['object_generation'],
                            'graph_id': payload['graph_id'],
                            'treatment_id': payload['treatment_id'],
                            'treatment_version': payload['treatment_version'],
                            'condition_hash': payload['condition_hash'],
                            'request': {'offer_id': (item.get('ebay_offer') or {}).get(
                                'offer_id')},
                        },
                    )
                    if effect.result.get('listing_id') != existing_listing.get('listing_id'):
                        raise ProviderEffectConflict('provider listing evidence mismatch')
                except (ProviderEffectConflict,
                        ProviderEffectReconciliationRequired) as exc:
                    raise self._projection_reconciliation_failure(
                        payload, sku, item, 'PROVIDER_EFFECT_REPLAY_INVALID', str(exc),
                    ) from exc
            log.info('ebay_publish: %s already published (listingId=%s) — skipping',
                     sku, existing_listing.get('listing_id', ''))
            tgw_logging.log_event('ebay_publish_skipped', sku=sku,
                                  reason='already_active',
                                  listing_id=str(existing_listing.get('listing_id', '')))
            photo_verify = self._refresh_photo_verify(sku, item)
            if photo_verify is not None:
                existing_listing['photo_verify'] = photo_verify
                fence_ebay_write(self.config, sku, ebay_listing=existing_listing)
            enqueue_post_push_sync(
                sku, config=self.config,
                source_provider_effect_id=str(existing_listing.get('provider_effect_id') or ''),
            )
            return self._governed_success_receipt(payload, sku)

        if effect_mode == 'workflow':
            from tgw.item_mutation import item_generation
            observed_generation = item_generation(item)
            if observed_generation != payload['object_generation']:
                raise HardFailure(
                    f'{sku}: provider effect generation conflict: expected '
                    f"{payload['object_generation']}, observed {observed_generation}"
                )
            # Authority validation and dispatch admission are atomic inside
            # reserve_and_begin_authorized_effect().

        ebay_offer = item.get('ebay_offer', {})
        offer_id = ebay_offer.get('offer_id')
        if not offer_id:
            # Retryable — ebay_stage may still be in flight when publish was
            # queued as part of the automated chain (upload → stage → publish).
            raise RuntimeError(
                f'{sku}: not staged on eBay yet — waiting for ebay_stage'
            )

        # Guard: if the operator set a manual price in draft_listing that hasn't
        # been staged yet, publishing would go live at the old offer price.
        # Wait for ebay_stage to run with the current price first.
        draft_price = (item.get('draft_listing') or {}).get('price')
        staged_price = ebay_offer.get('staged_price')
        if draft_price is not None and staged_price is not None:
            if abs(float(draft_price) - float(staged_price)) > 0.001:
                # Session 41: ebay_stage's idempotency guard (existing offer_id →
                # skip) has no price-drift check, so nothing was ever forcing a
                # re-stage here — this used to just retry forever waiting for a
                # correction that would never come (see tgw202605060201087, stuck
                # since 2026-07-01 with staged price $340.99 vs draft $29.99).
                # Break the deadlock by requesting the force-restage ourselves.
                try:
                    # Invariant C10: the forced re-stage keeps the publish job's
                    # operator provenance (also satisfies C9's inspection gate
                    # when the operator pressed the button).
                    state_machine.enqueue_job(
                        queue_name='ebay_stage',
                        payload={'sku': sku, 'force': True,
                                 **({'origin': 'operator'}
                                    if payload.get('origin') == 'operator' else {})},
                        entity_type='item',
                        entity_id=sku,
                        dedupe_key=f'ebay_stage:force:{sku}',
                        max_attempts=3,
                    )
                except psycopg2.errors.UniqueViolation:
                    pass
                raise RuntimeError(
                    f'{sku}: draft price ${draft_price} != staged price ${staged_price} '
                    f'— requested a forced ebay_stage re-sync, will retry'
                )

        # Reprice-schedule minting is DISABLED by default (session 42): schedules
        # were built from Browse-API asking-price "comps", which produced
        # fire-sale floors on 6 of the first 8 pipeline-published items
        # ($309.99 launch → $4.79 floor; one floor was literally $0.00). Dave
        # ended all 6 listings and ruled: the pipeline does not change prices
        # unsupervised. Re-enable via `reprice_schedule_enabled: true` in config
        # ONLY after pricing is rebuilt on real sold-price data
        # (PP-REPRICER-001, blocked on the buy.marketplace_insights scope).
        _sched_enabled = bool(
            self.config.get('reprice_schedule_enabled',
                            self.config.get('raw', {}).get('reprice_schedule_enabled', False)))
        if _sched_enabled:
            stages       = self.config.get('reprice_stages', [])
            comps        = ebay_offer.get('price_comps', {})
            cat_id       = str(item.get('ebay_category_id', ''))
            cat_defaults = self.config.get('category_price_defaults', {})
            schedule     = _build_reprice_schedule(stages, comps, cat_id, cat_defaults)
        else:
            schedule = []
            log.info('%s: reprice schedule NOT minted (disabled — manual pricing only)', sku)
        launch_entry = next((s for s in schedule if s['label'] == 'launch'), None)

        log.info('publishing %s (offerId=%s)', sku, offer_id)
        tgw_logging.log_event('ebay_publish_start', sku=sku, offer_id=offer_id)

        try:
            result = (
                self._publish_with_provider_effect(
                    payload, sku, offer_id, item,
                )
                if effect_mode == 'workflow'
                else publish_offer(self.config, offer_id)
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                body_text = exc.response.text if exc.response is not None else ''
                errors = []
                try:
                    errors = json.loads(body_text).get('errors', [])
                except Exception:
                    pass
                if any(e.get('errorId') == 25021 for e in errors):
                    # Category rejects granular condition — fall back to USED_EXCELLENT
                    # (conditionId 3000, accepted universally for used-item categories)
                    log.warning('%s: condition rejected by category at publish — '
                                'retrying with USED_EXCELLENT', sku)
                    ebay_put(self.config,
                             f'/sell/inventory/v1/inventory_item/{sku}',
                             {'condition': 'USED_EXCELLENT'})
                    result = publish_offer(self.config, offer_id)
                    # audit#1143 #1168: this succeeded on eBay, but draft_listing's
                    # condition_enum was left at the rejected granular value —
                    # the next ebay_stage re-stage would resubmit that same
                    # value, get 25021 again, and re-apply this same fallback
                    # forever (local record permanently disagreeing with what's
                    # actually live). Persisted below via the function's own
                    # end-of-run fence_patch_item(draft_listing=...) write.
                    if item.get('draft_listing'):
                        # code-review follow-up: use conditions.py's canonical
                        # mapping instead of hardcoding the enum/label, so this
                        # never drifts from the same source of truth
                        # ebay_draft.py uses. The label is the category's own
                        # eBay-returned description for conditionId 3000 (can
                        # legitimately differ per category — e.g. "Used" vs
                        # "Pre-owned - Good"), not a fixed string; 'Used' is
                        # only the fallback if that lookup is unavailable.
                        label = 'Used'
                        cat_id_for_condition = str(item.get('ebay_category_id', ''))
                        try:
                            for cond in conditions.allowed_conditions_for_category(
                                    self.config, cat_id_for_condition):
                                if cond['condition_id'] == '3000':
                                    label = cond['condition_label']
                                    break
                        except Exception as exc:
                            log.warning('%s: could not look up canonical label for '
                                        'conditionId 3000 (%s) — using default %r',
                                        sku, exc, label)
                        item['draft_listing']['condition_id']    = '3000'
                        item['draft_listing']['condition_label'] = label
                        item['draft_listing']['condition_enum']  = conditions.condition_enum('3000')
                else:
                    msg = _format_ebay_error(body_text, status)
                    # Canonical pipeline_error schema (broker B1b) — see
                    # ebay_stage.py; legacy schema still rendered by shim.
                    pipeline_error = {
                        'code':   'ebay_rejected',
                        'detail': msg,
                        'raw':    body_text[:800],
                        'ts':     datetime.now(timezone.utc).isoformat(),
                        'source': 'ebay_publish',
                        # PP-CONDITION-ENUM-001 / todo #1562 — see ebay_stage.py's
                        # identical field for rationale.
                        'field':  _extract_ebay_error_field(body_text),
                    }
                    fence_patch_item(self.config, sku, {'pipeline_error': pipeline_error})
                    raise HardFailure(f'{sku}: eBay rejected publish: {msg}') from exc
            else:
                raise  # transient — base class retries

        now = datetime.now(timezone.utc)
        item['ebay_listing'] = {
            'offer_id':     offer_id,
            'listing_id':   result['listing_id'],
            'listing_url':  result['listing_url'],
            'status':       'Active',
            'api':          'inventory',
            'published_at': now.isoformat(),
        }
        if result.get('_provider_effect_id'):
            item['ebay_listing']['provider_effect_id'] = result['_provider_effect_id']
        ebay_offer['status']       = 'PUBLISHED'
        ebay_offer['published_at'] = now.isoformat()
        # ebay_offer.price = what is actually live on eBay = what ebay_stage PUT there.
        # The reprice schedule has its own per-stage price fields; don't overwrite the
        # live price with schedule data here.
        actual_price = ebay_offer.get('staged_price')
        if actual_price is not None:
            ebay_offer['price'] = float(actual_price)
        launch_price = launch_entry['price'] if launch_entry and launch_entry['price'] is not None else None
        item['ebay_offer'] = ebay_offer

        # Stamp launch entry done_at and store full schedule
        for s in schedule:
            if s['label'] == 'launch':
                s['done_at'] = now.isoformat()
                s['due_at']  = now.isoformat()
        item['reprice_schedule'] = schedule

        # Record the publish price as the first price_history entry so the full
        # price trail is complete and auditable. Session 42 fix: record the
        # price that is ACTUALLY live on eBay (staged_price), not the schedule's
        # launch figure — the old version wrote $309.99 into history while the
        # listing was live at $29.99 (Dave caught it on tgw202605060201087).
        recorded_price = actual_price if actual_price is not None else launch_price
        if recorded_price is not None:
            item.setdefault('price_history', []).append({
                'ts':             now.isoformat(),
                'price':          float(recorded_price),
                'previous_price': item.get('price'),
                'stage':          'launch',
                'label':          'Published to eBay',
                'source':         'ebay_publish',
            })

        # Refresh picklist line in draft description now that listing_id is known
        from tgw.ebay.description import build_listing_description
        if item.get('draft_listing'):
            item['draft_listing']['listing_description'] = build_listing_description(
                item, self.config)

        # PP-EBAY-SNAPSHOT-001 Phase 2 / PP-PHOTOSYNC-001 P1: verify photos
        # survived publish. One extra GET; logged but never blocks completion.
        photo_verify = self._refresh_photo_verify(sku, item)
        if photo_verify is not None:
            item['ebay_listing']['photo_verify'] = photo_verify

        try:
            fence_ebay_write(self.config, sku,
                             ebay_listing=item.get('ebay_listing'),
                             ebay_offer=item.get('ebay_offer'))
        except Exception as exc:
            # The provider has confirmed publication, but the canonical Active
            # guard did not land.  An ordinary worker retry would call publish
            # again.  Preserve the exact provider evidence as a terminal
            # reconciliation requirement instead; no blind replay is safe.
            raise TreatmentFailure(
                f'{sku}: provider publish confirmed but canonical projection '
                f'failed; reconciliation required: {exc}',
                {
                    'receipt_schema_id': 'treatment-receipt/v1',
                    'treatment_id': payload.get('treatment_id', 'ebay-publish'),
                    'treatment_version': payload.get('treatment_version', '1'),
                    'graph_id': payload.get('graph_id'),
                    'outcome': 'reconciliation_required',
                    'established_conditions': [],
                    'artifacts': [f'item:{sku}'],
                    'evidence': {
                        'reason_code': 'CANONICAL_PROJECTION_AFTER_PUBLISH_FAILED',
                        'provider': 'ebay',
                        'offer_id': offer_id,
                        'listing_id': result['listing_id'],
                        'listing_url': result['listing_url'],
                        'provider_status': result.get('status', 'PUBLISHED'),
                        'provider_effect_id': result.get('_provider_effect_id'),
                        'projection_error': f'{type(exc).__name__}: {exc}',
                        'operator_origin': payload.get('origin') == 'operator',
                    },
                },
            ) from exc
        # Broker B1a (M1/M2): the draft→offer push is complete — the offer
        # now holds exactly what the draft specified, so the manager marks
        # the draft re-baselined. The next manipulation (AI or operator)
        # starts from a correct base, and drift is detectable again.
        try:
            fence_patch_item(self.config, sku, {
                'reprice_schedule': item.get('reprice_schedule'),
                'price_history':    item.get('price_history', []),
                'draft_listing':    item.get('draft_listing'),
                **baseline_fields(now),
            })
        except Exception as exc:
            raise self._projection_reconciliation_failure(
                payload, sku, item,
                'POST_PUBLISH_PROJECTION_FAILED',
                f'{type(exc).__name__}: {exc}',
            ) from exc

        log.info('published %s → %s', sku, result['listing_url'])
        tgw_logging.log_event('ebay_listing_published', sku=sku,
                              listing_id=result['listing_id'],
                              offer_id=offer_id,
                              listing_url=result['listing_url'])

        try:
            state_machine.enqueue_catalog_rebuild(f'ebay_publish:{sku}')
        except psycopg2.errors.UniqueViolation:
            pass

        enqueue_post_push_sync(
            sku, config=self.config,
            source_provider_effect_id=str(result.get('_provider_effect_id') or ''),
        )

        # A provider response alone is not treatment success.  Emit the
        # satisfied receipt only after the canonical listing/offer projection,
        # history/baseline projection, and both downstream invalidations have
        # completed.  Any exception above remains a truthful failed attempt;
        # once the first canonical write has landed, the already-Active guard
        # makes its repair replay provider-idempotent.
        return self._governed_success_receipt(payload, sku)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-publish-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayPublishWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
