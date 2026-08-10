"""
tgw.workers.ebay_upload — Upload item photos to eBay EPS.

Enqueued by ebay_draft after a draft listing is written.  For each photo
in the SKU directory that hasn't been uploaded yet, calls upload_photo()
and stores the result in ebay_photos.  Idempotent — already-uploaded
photos are skipped.

Queue name: ebay_upload
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Set

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.assets import ordered_photos
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.config import sku_dir as _cfg_sku_dir
from tgw.config import sku_json as _cfg_sku_json
from tgw.ebay.upload import upload_photo
from tgw.errors import TreatmentFailure
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
from tgw.quota import QuotaBudgetExceeded

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_upload'

# PP-PHOTOSYNC-001 P1: a quota wall may legitimately block a job for days in a
# row (background halt only lifts at reset); after this many quota-blocked
# passes the job goes to dead_letter WITH a notify() instead of re-arming
# forever — the exact class of immortal backlog that burned 3 days of budget
# in s43. Visible-and-stuck beats invisible-and-recurring.
QUOTA_RETRY_LIMIT = 3


class EbayUploadWorker(QueueWorker):

    def _current_workflow_binding(self, sku: str) -> Dict[str, str]:
        """Rebuild the authoritative identity after partial progress writes."""
        from tgw.workflow.evaluator import evaluate
        from tgw.workflow.item_snapshot import build_item_snapshot
        from tgw.workflow.profiles import TGW_EBAY_LISTABLE
        from tgw.workflow.treatments import TGW_TREATMENTS

        snapshot = build_item_snapshot(
            _cfg_sku_json(self.config, sku), TGW_EBAY_LISTABLE,
            treatments=TGW_TREATMENTS,
        )
        graph = evaluate(
            snapshot=snapshot, goal=TGW_EBAY_LISTABLE,
            treatments=TGW_TREATMENTS,
            evaluator_version='ebay-upload-quota-timer/v1',
        )
        return {
            'graph_id': graph.graph_id,
            'object_generation': graph.object_generation,
            'condition_hash': graph.condition_hash,
            'goal_profile_id': graph.goal_profile_id,
            'goal_profile_version': graph.goal_profile_version,
        }

    def _persist_partial(self, sku: str, uploaded: List[Dict[str, str]],
                         photos: List[Path]) -> List[Dict[str, str]]:
        """Reorder `uploaded` to match photo_order and fence-patch it — called
        at every exit point (success, quota block, network error, per-photo
        failure) so partial progress is never lost to a retry re-uploading
        photos that already succeeded this run or a prior one."""
        path_to_entry = {e['local']: e for e in uploaded}
        reordered: List[Dict[str, str]] = []
        for p in photos:
            key = str(p)
            if key in path_to_entry:
                reordered.append(path_to_entry[key])
        seen_keys = {e['local'] for e in reordered}
        for e in uploaded:
            if e['local'] not in seen_keys:
                reordered.append(e)
        if reordered:
            fence_patch_item(self.config, sku, {
                'ebay_photos': reordered,
                'draft_listing': {'imageUrls': [e['url'] for e in reordered]},
            })
        return reordered

    def _quota_timer_mode(self) -> str:
        migration = self.config.get('workflow_migration')
        if migration is None and isinstance(self.config.get('raw'), dict):
            migration = self.config['raw'].get('workflow_migration')
        if migration is None:
            migration = {}
        mode = migration.get('ebay_upload_quota_timer', 'legacy') \
            if isinstance(migration, dict) else 'legacy'
        if mode not in {'legacy', 'workflow'}:
            raise HardFailure(
                f"invalid workflow_migration.ebay_upload_quota_timer mode {mode!r}; "
                "expected 'legacy' or 'workflow'"
            )
        return mode

    def handle(self, job: Dict[str, Any]) -> Dict[str, Any] | None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_upload job missing sku in payload')
        quota_timer_mode = self._quota_timer_mode()
        if quota_timer_mode == 'workflow':
            required = ('treatment_id', 'treatment_version', 'graph_id',
                        'object_generation', 'condition_hash')
            missing = [key for key in required if not payload.get(key)]
            if missing:
                raise HardFailure(
                    'workflow quota timer missing bound identity: '
                    + ', '.join(missing)
                )

        json_path = _cfg_sku_json(self.config, sku)
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        # Build set of already-uploaded local paths
        existing: Set[str] = {e['local'] for e in item.get('ebay_photos', [])}

        # Collect photos in photo_order display order
        sku_dir: Path = _cfg_sku_dir(self.config, sku)
        all_photos: List[Path] = ordered_photos(item, sku_dir)
        photos: List[Path] = all_photos

        if not photos:
            # Invariant C11 (session 43): a skip/guard is a finding, not a log
            # line. Without photos on disk, this job used to just log+return
            # SUCCEEDED, leaving the item silently stalled forever with no
            # record anywhere that anything is wrong. Persist durably —
            # mirrors ebay_stage.py's legacy_listing_blocked pattern — so
            # catalog-verify can surface and an operator can find/repair it.
            # Job status itself stays SUCCEEDED (matches ebay_stage's
            # equivalent guard: this is a recognized, recorded stall, not a
            # transient failure worth retry/backoff churn).
            log.warning('no uploadable photos found for %s — skipping upload', sku)
            tgw_logging.log_event('ebay_upload_no_photos', sku=sku)
            fence_patch_item(self.config, sku, {
                'ebay_upload_blocked': {
                    'reason': 'no_photos_on_disk',
                    'detected_at': datetime.now(timezone.utc).isoformat(),
                },
            })
            return

        expected_total = len(photos)
        to_attempt = expected_total - len(existing & {str(p) for p in photos})
        log.info('ebay_upload: %s — %d photos to upload (%d on disk, %d already uploaded)',
                 sku, to_attempt, expected_total, len(existing))

        uploaded: List[Dict[str, str]] = list(item.get('ebay_photos', []))
        errors: List[str] = []
        quota_blocked = False
        quota_detail = ''

        for photo in photos:
            if str(photo) in existing:
                log.debug('already uploaded: %s', photo.name)
                continue
            try:
                url = upload_photo(self.config, photo)
                uploaded.append({'local': str(photo), 'url': url})
                tgw_logging.log_event('ebay_photo_uploaded', sku=sku,
                                      photo=photo.name, url=url)
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as exc:
                # Whole-job network blip — persist what we have and let
                # worker_base's normal retry re-run the job (existing/on-disk
                # comparison at the top means we won't re-upload what already
                # succeeded this pass).
                log.warning('network error uploading %s: %s', photo.name, exc)
                self._persist_partial(sku, uploaded, photos)
                raise
            except QuotaBudgetExceeded as exc:
                # Client-side halt (background reserve protected) — no eBay
                # call was made. Further attempts this pass will hit the same
                # wall immediately; stop rather than spam every remaining
                # photo (this is exactly what masked the s43 incident: the
                # old code kept looping, then reported "complete, 0 new").
                quota_blocked = True
                quota_detail = str(exc)
                log.warning('ebay_upload: quota wall hit for %s after %d/%d new photos this pass — %s',
                            sku, len(uploaded) - len(existing), to_attempt, exc)
                break
            except Exception as exc:
                err_str = str(exc)
                # EPS reports its OWN quota exhaustion as Ack=Failure text,
                # not an exception type — same wall, different vocabulary.
                if 'usage limit' in err_str.lower() or 'call usage limit' in err_str.lower():
                    quota_blocked = True
                    quota_detail = err_str
                    log.warning('ebay_upload: EPS usage-limit hit for %s after %d/%d new photos this pass',
                                sku, len(uploaded) - len(existing), to_attempt)
                    break
                log.error('failed to upload %s: %s', photo.name, exc)
                errors.append(err_str)

        if quota_blocked:
            self._persist_partial(sku, uploaded, photos)
            quota_retries = int(payload.get('quota_retries', 0)) + 1
            _origin = ({'origin': 'operator'} if payload.get('origin') == 'operator' else {})
            if quota_retries > QUOTA_RETRY_LIMIT:
                from tgw.notify import notify
                notify(
                    f'ebay_upload: quota-blocked {QUOTA_RETRY_LIMIT}x, giving up: {sku}',
                    f'{len(uploaded) - len(existing)}/{to_attempt} new photos uploaded — {quota_detail[:150]}',
                    level='error',
                )
                message = (
                    f'{sku}: quota-blocked {quota_retries - 1} times in a row — '
                    f'dead-lettering instead of re-arming forever (see notify): '
                    f'{quota_detail[:200]}'
                )
                if quota_timer_mode == 'workflow':
                    raise TreatmentFailure(message, {
                        'receipt_schema_id': 'treatment-receipt/v1',
                        'treatment_id': payload.get('treatment_id', 'ebay-upload'),
                        'treatment_version': payload.get('treatment_version', '1'),
                        'graph_id': payload.get('graph_id'),
                        'outcome': 'failed',
                        'established_conditions': [],
                        'artifacts': [f'item:{sku}'],
                        'evidence': {
                            'reason_code': 'EBAY_UPLOAD_QUOTA_RETRY_LIMIT',
                            'operator_attention_required': True,
                            'operator_origin': payload.get('origin') == 'operator',
                            'quota_retries': quota_retries - 1,
                            'uploaded_this_attempt': len(uploaded) - len(existing),
                            'uploaded_total': len(uploaded),
                        },
                    })
                raise HardFailure(message)
            log.warning('ebay_upload: %s quota-blocked (retry %d/%d) — saving %d uploaded so far, requeueing remainder',
                        sku, quota_retries, QUOTA_RETRY_LIMIT, len(uploaded))
            tgw_logging.log_event('ebay_upload_quota_blocked', sku=sku,
                                  quota_retries=quota_retries, uploaded_so_far=len(uploaded))
            if quota_timer_mode == 'workflow':
                timer_payload = dict(payload)
                timer_payload.update({
                    'sku': sku,
                    'reason': 'quota_timer_elapsed',
                    'quota_retries': quota_retries,
                    **self._current_workflow_binding(sku),
                })
                return {
                    'receipt_schema_id': 'treatment-wait-receipt/v1',
                    'treatment_id': payload['treatment_id'],
                    'treatment_version': payload['treatment_version'],
                    'graph_id': payload['graph_id'],
                    'outcome': 'transient_backoff',
                    'established_conditions': [],
                    'artifacts': [f'item:{sku}'],
                    'evidence': {
                        'reason_code': 'EBAY_UPLOAD_QUOTA_BLOCKED',
                        'quota_retries': quota_retries,
                        'uploaded_this_attempt': len(uploaded) - len(existing),
                        'uploaded_total': len(uploaded),
                        'operator_origin': payload.get('origin') == 'operator',
                        'changed': len(uploaded) != len(existing),
                    },
                    'timer': {
                        'queue_name': QUEUE_NAME,
                        'not_before': time.time() + 6 * 3600,
                        'payload': timer_payload,
                        'dedupe_key': (
                            f"workflow-timer:{timer_payload['graph_id']}:"
                            f'ebay-upload:quota:{quota_retries}'
                        ),
                        'max_attempts': 3,
                    },
                }
            # Requeue for 6 hours from now (EPS resets daily at midnight eBay time).
            # Invariant C10: the requeue keeps the job's operator provenance.
            try:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'sku': sku, 'reason': 'quota_retry',
                             'quota_retries': quota_retries, **_origin},
                    entity_type='item',
                    entity_id=sku,
                    not_before=time.time() + 6 * 3600,
                    max_attempts=3,
                    dedupe_key=f'ebay_upload:{sku}',
                )
            except psycopg2.errors.UniqueViolation:
                pass
            return

        if errors:
            # A genuine (non-quota) per-photo failure. Never report success on
            # a shortfall (PP-PHOTOSYNC-001 P1) — persist whatever succeeded
            # and raise so worker_base's normal backoff retries the rest.
            self._persist_partial(sku, uploaded, photos)
            raise RuntimeError(
                f'{sku}: {len(uploaded) - len(existing)}/{to_attempt} new photos uploaded, '
                f'{len(errors)} failed: {errors[0]}'
            )

        if not uploaded:
            raise RuntimeError(f'no photos uploaded for {sku} and none pre-existing')

        reordered = self._persist_partial(sku, uploaded, photos)

        # Self-healing: a full success means photos are no longer missing —
        # clear any prior no-photos finding so catalog-verify stops flagging
        # an item that's since been repaired (mirrors legacy_listing_resolved
        # suppressing legacy_listing_unrepaired once dealt with).
        if item.get('ebay_upload_blocked'):
            fence_patch_item(self.config, sku, {'ebay_upload_blocked': None})

        new_count = len(uploaded) - len(existing)
        log.info('ebay_upload complete for %s: %d total (%d new)',
                 sku, len(reordered), new_count)
        # to_attempt travels with the event so PP-PHOTOSYNC-001 P7's
        # success_count_contradiction rule can detect a regression of the s43
        # bug (new==0 while to_attempt>0 logged as complete) from the
        # structured event alone, without parsing free-text log messages.
        tgw_logging.log_event('ebay_upload_complete', sku=sku,
                              total=len(reordered), new=new_count,
                              to_attempt=to_attempt)


        return {
            "treatment_id": "ebay-upload",
            "outcome": "satisfied",
            "established_conditions": ("photos_uploaded",),
            "artifacts": (f"item:{sku}",),
        }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-upload-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayUploadWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
