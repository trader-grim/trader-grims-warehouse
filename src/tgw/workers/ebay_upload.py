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

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.upload import upload_photo
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_upload'

_PHOTO_EXTS: Set[str] = {'.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff',
                          '.JPG', '.JPEG', '.PNG'}


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

        # Collect photos from the SKU directory
        sku_dir: Path = self.config['itemdata_root'] / sku
        photos: List[Path] = sorted(
            p for p in sku_dir.iterdir()
            if p.is_file() and p.suffix in _PHOTO_EXTS
        )

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

        item['ebay_photos'] = uploaded

        # Propagate eBay-hosted URLs into draft_listing if present
        if 'draft_listing' in item:
            item['draft_listing']['imageUrls'] = [e['url'] for e in uploaded]

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

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
