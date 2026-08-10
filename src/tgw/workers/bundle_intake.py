"""
tgw.workers.bundle_intake — Camera-bundle intake worker.

Watches incoming/newitems/ for stable bundles, enqueues a job per bundle,
moves photos to ItemData/<SKU>/, writes the canonical item JSON, then
enqueues downstream catalog-rebuild and thumbnail-gen jobs.

Three bundle formats:
  dir   — incoming/newitems/<SKU>/  with <SKU>.json stub + photos
  zip   — incoming/newitems/<SKU>.zip + <SKU>.json stub alongside
  multi — incoming/newitems/multi/<anything>.zip  (extract → staging → manual)

Stability: all files in the bundle must have mtime > STABLE_AFTER_S seconds.
"""

from __future__ import annotations

import logging
import re
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.apis.fence import create_item as fence_create_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.config import sku_dir as _cfg_sku_dir
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME    = 'bundle_intake'
STABLE_AFTER_S = 30
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.JPG', '.JPEG', '.PNG'}
SKU_RE        = re.compile(r'^tgw\d{15}$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_stable(paths: List[Path]) -> bool:
    """True when every path has not been modified in the last STABLE_AFTER_S seconds."""
    cutoff = time.time() - STABLE_AFTER_S
    return all(p.stat().st_mtime < cutoff for p in paths if p.exists())


def _images_in(directory: Path) -> List[Path]:
    return [p for p in directory.iterdir()
            if p.is_file() and p.suffix in IMAGE_SUFFIXES]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enqueue(sku: str, fmt: str, source: str) -> Optional[str]:
    """Enqueue a bundle_intake job. Returns job_id or None if dedupe hit."""
    try:
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={'sku': sku, 'format': fmt, 'source': source},
            entity_type='item',
            entity_id=sku,
            dedupe_key=f'bundle_intake:{sku}',
            max_attempts=3,
        )
        return jid
    except psycopg2.errors.UniqueViolation:
        return None


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_newitems(newitems_dir: Path) -> None:
    """Detect stable bundles in newitems/ and enqueue intake jobs.

    All three formats use a <SKU>/ subdirectory as the bundle unit:
      dir   — newitems/<SKU>/ with <SKU>.json + loose photos
      zip   — newitems/<SKU>/ with <SKU>.json + a *.zip file
      multi — newitems/multi/<SKU>/ with <SKU>.json + a *.zip file
    """
    if not newitems_dir.exists():
        return

    # --- symlink format: newitems/<SKU> → ItemData/<SKU>/ ---
    for entry in newitems_dir.iterdir():
        if not entry.is_symlink():
            continue
        sku = entry.name
        if not SKU_RE.match(sku):
            continue
        jid = _enqueue(sku, 'symlink', str(entry))
        if jid:
            log.info('enqueued bundle symlink %s (job %s)', sku, jid)
            tgw_logging.log_event('bundle_detected', sku=sku, fmt='symlink', job_id=jid)

    # --- dir and zip formats: newitems/<SKU>/ ---
    for entry in newitems_dir.iterdir():
        if not entry.is_dir() or entry.name == 'multi':
            continue
        sku = entry.name
        if not SKU_RE.match(sku):
            continue
        stub = entry / f'{sku}.json'
        if not stub.exists():
            continue

        images = _images_in(entry)
        zips   = list(entry.glob('*.zip'))

        if images:
            # dir format: loose photos alongside stub
            all_files = [stub] + images
            if not _is_stable(all_files):
                continue
            jid = _enqueue(sku, 'dir', str(entry))
            if jid:
                log.info('enqueued bundle dir %s (%d photos, job %s)',
                         sku, len(images), jid)
                tgw_logging.log_event('bundle_detected', sku=sku, fmt='dir',
                                      photos=len(images), job_id=jid)
        elif zips:
            # zip format: single zip inside the SKU dir
            zip_path = zips[0]
            if not _is_stable([stub, zip_path]):
                continue
            jid = _enqueue(sku, 'zip', str(zip_path))
            if jid:
                log.info('enqueued bundle zip %s (%s, job %s)',
                         sku, zip_path.name, jid)
                tgw_logging.log_event('bundle_detected', sku=sku, fmt='zip',
                                      zip_name=zip_path.name, job_id=jid)

    # --- multi format: newitems/multi/<SKU>/ ---
    multi_dir = newitems_dir / 'multi'
    if multi_dir.exists():
        for entry in multi_dir.iterdir():
            if not entry.is_dir():
                continue
            zips = list(entry.glob('*.zip'))
            if not zips:
                continue
            zip_path = zips[0]
            if not _is_stable([zip_path]):
                continue
            dedupe_key = f'multi_intake:{entry.name}'
            try:
                jid = state_machine.enqueue_job(
                    queue_name='multi_intake',
                    payload={'source': str(entry)},  # directory, not the zip
                    dedupe_key=dedupe_key,
                    max_attempts=3,
                )
                log.info('enqueued multi_intake job for %s (job %s)',
                         entry.name, jid)
                tgw_logging.log_event('multi_bundle_detected',
                                      bundle_dir=entry.name, job_id=jid)
            except psycopg2.errors.UniqueViolation:
                pass


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class BundleIntakeWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('bundle_intake worker started: owner=%s', self.owner)

        while not self._stop:
            self._maybe_recover()
            try:
                scan_newitems(self.config['newitems_path'])
            except Exception:
                log.exception('scan_newitems failed')
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku     = payload.get('sku', '')
        fmt     = payload.get('format', '')
        source  = payload.get('source', '')

        if not sku or not fmt or not source:
            raise HardFailure(f'bundle_intake job missing required fields: {payload}')

        if fmt == 'dir':
            self._handle_dir(sku, Path(source))
        elif fmt == 'zip':
            self._handle_zip(sku, Path(source))
        elif fmt == 'symlink':
            self._handle_symlink(sku, Path(source))
        else:
            raise HardFailure(f'unknown bundle format {fmt!r} for {sku}')

    # ------------------------------------------------------------------
    # Format handlers
    # ------------------------------------------------------------------

    def _handle_symlink(self, sku: str, symlink: Path) -> None:
        """Item already in ItemData — just remove symlink and enqueue downstream."""
        itemdata_dir = _cfg_sku_dir(self.config, sku)
        if not itemdata_dir.exists():
            raise HardFailure(f'symlink target ItemData/{sku} does not exist')

        if symlink.is_symlink():
            symlink.unlink()
            log.info('removed symlink for %s', sku)

        self._enqueue_downstream(sku)
        log.info('bundle_intake symlink complete: %s', sku)
        tgw_logging.log_event('bundle_intake_complete', sku=sku, fmt='symlink')

    def _handle_dir(self, sku: str, source_dir: Path) -> None:
        if not source_dir.exists():
            log.info('source dir gone for %s — assuming already processed', sku)
            return

        stub_path = source_dir / f'{sku}.json'
        stub = self._load_stub(stub_path, sku)
        raw_images = _images_in(source_dir)
        if not raw_images:
            raise HardFailure(f'no images in bundle dir for {sku}')

        # photo-integrity mitigation leg 3 (todo #1267): decode-verify at
        # the door, before a corrupt camera file ever gets copied into
        # ItemData. Full PIL im.load() per the plan doc's recommendation —
        # catches tail truncation that header-only verify() would miss
        # (the exact Feb-2022 bulk-copy failure mode this track exists to
        # prevent). Corrupt files never reach ItemData at all.
        images, rejected = self._decode_verify(raw_images)
        if not images:
            self._log_rejected(sku, rejected)
            raise HardFailure(
                f'all {len(raw_images)} image(s) in bundle dir for {sku} '
                f'failed decode-verify: {rejected}'
            )

        dest_dir = self._prepare_dest(sku)
        self._copy_images(images, dest_dir)
        self._write_item_json(sku, stub, dest_dir, images, rejected)
        shutil.rmtree(source_dir)

        self._enqueue_downstream(sku)
        log.info('bundle_intake dir complete: %s (%d photos, %d rejected)',
                  sku, len(images), len(rejected))
        tgw_logging.log_event('bundle_intake_complete', sku=sku, fmt='dir',
                              photos=len(images), rejected=len(rejected))

    def _handle_zip(self, sku: str, zip_path: Path) -> None:
        stub_path = zip_path.parent / f'{sku}.json'
        if not zip_path.exists():
            log.info('zip gone for %s — assuming already processed', sku)
            return

        stub = self._load_stub(stub_path, sku)

        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()

            # Detect multi-item zip: has timestamp-named subdirs
            top_dirs = {Path(n).parts[0] for n in names if '/' in n}
            if any(d.isdigit() and len(d) == 14 for d in top_dirs):
                raise HardFailure(
                    f'zip for {sku} contains timestamp subdirs — '
                    'place in newitems/multi/ for multi_intake processing'
                )

            image_names = [n for n in names
                           if Path(n).suffix in IMAGE_SUFFIXES and not n.startswith('__')]
            if not image_names:
                raise HardFailure(f'no images in zip for {sku}')

            dest_dir = self._prepare_dest(sku)
            for name in image_names:
                dest = dest_dir / Path(name).name
                with zf.open(name) as src, open(dest, 'wb') as dst:
                    shutil.copyfileobj(src, dst)

        # photo-integrity mitigation leg 3 (todo #1267): decode-verify the
        # extracted files before they're treated as real inventory photos —
        # a corrupt file inside the zip (truncated in transit, bad camera
        # write, etc.) is rejected at the door, not discovered at listing
        # time. Rejected files are removed from dest_dir; a persisted
        # finding is recorded on the item, never a silent skip.
        extracted = _images_in(dest_dir)
        images, rejected = self._decode_verify(extracted)
        for bad_path, _err in rejected:
            bad_path.unlink(missing_ok=True)
        if not images:
            self._log_rejected(sku, rejected)
            raise HardFailure(
                f'all {len(extracted)} image(s) in zip for {sku} '
                f'failed decode-verify: {rejected}'
            )

        self._write_item_json(sku, stub, dest_dir, images, rejected)
        # Remove the source SKU dir (contains the zip + stub)
        source_dir = zip_path.parent
        shutil.rmtree(source_dir, ignore_errors=True)

        self._enqueue_downstream(sku)
        log.info('bundle_intake zip complete: %s (%d photos, %d rejected)',
                  sku, len(images), len(rejected))
        tgw_logging.log_event('bundle_intake_complete', sku=sku, fmt='zip',
                              photos=len(images), rejected=len(rejected))

    # ------------------------------------------------------------------
    # Shared utilities
    # ------------------------------------------------------------------

    def _load_stub(self, stub_path: Path, sku: str) -> Dict[str, Any]:
        if not stub_path.exists():
            raise HardFailure(f'stub JSON missing for {sku}: {stub_path}')
        import json
        try:
            return json.loads(stub_path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise HardFailure(f'bad stub JSON for {sku}: {exc}') from exc

    def _prepare_dest(self, sku: str) -> Path:
        dest_dir: Path = _cfg_sku_dir(self.config, sku)
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir

    def _copy_images(self, images: List[Path], dest_dir: Path) -> None:
        for src in images:
            dest = dest_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)

    def _decode_verify(self, images: List[Path]):
        """Split *images* into (good, rejected) via full PIL decode
        (photo-integrity mitigation leg 3, todo #1267). `rejected` is a
        list of (path, error) tuples. Uses im.load() (full decode), not
        im.verify() (header-only) — matches the plan doc's own
        recommendation, since intake is not hot-path and header-only
        verify() misses tail truncation (the Feb-2022 bulk-copy failure
        mode this whole track exists to catch).
        """
        from tgw.integrity import decode_verify_image
        good: List[Path] = []
        rejected: List[Any] = []
        for img in images:
            error = decode_verify_image(img)
            if error is None:
                good.append(img)
            else:
                rejected.append((img, error))
        return good, rejected

    def _log_rejected(self, sku: str, rejected: List[Any]) -> None:
        for path, error in rejected:
            log.warning('bundle_intake decode-verify rejected %s for %s: %s',
                        path.name, sku, error)
        tgw_logging.log_event('bundle_intake_photo_rejected', sku=sku,
                              rejected=[{'file': p.name, 'error': e} for p, e in rejected])

    def _write_item_json(self, sku: str, stub: Dict[str, Any],
                         dest_dir: Path, images: List[Path],
                         rejected: Optional[List[Any]] = None) -> None:
        json_path = dest_dir / f'{sku}.json'
        if json_path.exists():
            return  # already written by a prior attempt
        record: Dict[str, Any] = {
            'sku':      sku,
            'location': stub.get('location', ''),
            'status':   'New',
            'title':    stub.get('title', ''),
            'TEMPLATE': stub.get('TEMPLATE', 'default'),
        }
        # Set image field to first photo (alphabetical) for thumbnail generation
        first_image = sorted(images, key=lambda p: p.name)[0]
        record['image'] = first_image.name

        if rejected:
            # invariant C11: a worker's guard/skip is a finding, not a log
            # line. `pipeline_error` is the existing generic finding field
            # catalog-verify already surfaces (see api.py's `_verify_item`
            # pipeline_error handling) and the same shape used by
            # ebay_stage.py/draft_sync.py's legacy_listing_blocked pattern.
            self._log_rejected(sku, rejected)
            detail = ', '.join(f'{p.name}: {e}' for p, e in rejected)
            record['pipeline_error'] = {
                'code':   'photo_decode_rejected',
                'detail': detail[:500],
                'ts':     _utcnow_iso(),
                'source': 'bundle_intake',
            }
            record['photo_decode_rejected'] = [
                {'file': p.name, 'error': e} for p, e in rejected
            ]

        fence_create_item(self.config, sku, record)

    def _enqueue_downstream(self, sku: str) -> None:
        migration = self.config.get('workflow_migration')
        if migration is None and isinstance(self.config.get('raw'), dict):
            migration = self.config['raw'].get('workflow_migration')
        if migration is None:
            migration = {}
        mode = migration.get('bundle_downstream', 'legacy') if isinstance(migration, dict) else 'legacy'
        if mode not in {'legacy', 'workflow'}:
            raise HardFailure(
                f"invalid workflow_migration.bundle_downstream mode {mode!r}; "
                "expected 'legacy' or 'workflow'"
            )

        # catalog-rebuild: coalesced 30s delay so rapid intakes batch together
        try:
            state_machine.enqueue_catalog_rebuild(f'bundle_intake:{sku}')
        except psycopg2.errors.UniqueViolation:
            pass  # already queued, coalescing

        # thumbnail-gen: one per SKU, immediate
        try:
            state_machine.enqueue_job(
                queue_name='thumbnail_gen',
                payload={'sku': sku},
                entity_type='item',
                entity_id=sku,
                dedupe_key=f'thumbnail_gen:{sku}',
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass

        # ai_identify: vision model identification, immediate.  The default
        # legacy mode preserves the old unconditional fanout.  Workflow mode
        # derives eligibility from the authoritative post-intake item; it does
        # not dispatch any eBay provider treatment.
        workflow_decision = None
        if mode == 'workflow':
            from tgw.workflow.listing_migration import derive_bundle_downstream

            item_path = _cfg_sku_dir(self.config, sku) / f'{sku}.json'
            workflow_decision = derive_bundle_downstream(item_path)
        if workflow_decision is not None and not workflow_decision.enqueue_ai_identify:
            return
        if workflow_decision is not None:
            from tgw.workflow.scheduler import dispatch_treatment

            dispatch = dispatch_treatment(
                disposition=workflow_decision.disposition,
                entity_id=sku,
                graph=workflow_decision.graph,
                payload_extra={
                    'sku': sku,
                    'condition_hash': workflow_decision.graph.condition_hash,
                },
                enqueue_fn=state_machine.enqueue_job,
            )
            if not dispatch.enqueued and dispatch.outcome != 'already_dispatched':
                raise RuntimeError(
                    f'workflow dispatch failed for ai-identify graph '
                    f'{workflow_decision.graph_id}'
                )
            return
        try:
            state_machine.enqueue_job(
                queue_name='ai_identify',
                payload={'sku': sku},
                entity_type='item',
                entity_id=sku,
                dedupe_key=f'ai_identify:{sku}',
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-bundle-intake-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = BundleIntakeWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
