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
from pathlib import Path
from typing import Any, Dict, List, Set

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.assets import ordered_photos
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.upload import upload_photo
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_upload'


class EbayUploadWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_upload job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        # Build set of already-uploaded local paths
        existing: Set[str] = {e['local'] for e in item.get('ebay_photos', [])}

        # Collect photos in photo_order display order
        sku_dir: Path = self.config['itemdata_root'] / sku
        photos: List[Path] = ordered_photos(item, sku_dir)

        if not photos:
            log.warning('no photos found for %s — skipping upload', sku)
            tgw_logging.log_event('ebay_upload_no_photos', sku=sku)
            return

        uploaded: List[Dict[str, str]] = list(item.get('ebay_photos', []))
        errors: List[str] = []

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
                log.warning('network error uploading %s: %s', photo.name, exc)
                errors.append(str(exc))
                # Retry the whole job — don't hard-fail on transient network issues
                raise
            except Exception as exc:
                log.error('failed to upload %s: %s', photo.name, exc)
                errors.append(str(exc))

        if not uploaded:
            raise RuntimeError(f'all photo uploads failed for {sku}: {errors[0] if errors else "no photos"}')

        # Reorder ebay_photos to match photo_order so imageUrls reflects display order
        path_to_entry = {e['local']: e for e in uploaded}
        reordered: List[Dict[str, str]] = []
        for p in ordered_photos(item, sku_dir):
            key = str(p)
            if key in path_to_entry:
                reordered.append(path_to_entry[key])
        # Safety: append any entries not reached via ordered_photos
        seen_keys = {e['local'] for e in reordered}
        for e in uploaded:
            if e['local'] not in seen_keys:
                reordered.append(e)

        # Write reordered photos and propagate imageUrls into draft_listing (deep-merged by fence)
        fence_patch_item(self.config, sku, {
            'ebay_photos': reordered,
            'draft_listing': {'imageUrls': [e['url'] for e in reordered]},
        })

        new_count = len(uploaded) - len(existing)
        log.info('ebay_upload complete for %s: %d total (%d new)',
                 sku, len(uploaded), new_count)
        tgw_logging.log_event('ebay_upload_complete', sku=sku,
                              total=len(uploaded), new=new_count)

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_upload:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


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
