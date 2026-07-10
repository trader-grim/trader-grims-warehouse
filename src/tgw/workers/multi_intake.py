"""
tgw.workers.multi_intake — Multi-item bundle splitting worker.

Handles zips that contain multiple items, each in a timestamp-named subdir:
  multi/<SKU>/
    <SKU>.json          ← parent stub (base SKU, location, template)
    data_<ts>.zip       ← zip with subdirs: YYYYMMDDHHMMSS/1.jpg, 2.jpg ...

Child SKUs are derived by incrementing the parent SKU's trailing number:
  parent tgw202605020017010 → children tgw202605020017010, ...011, ...012

Each subdir becomes a dir-format bundle in newitems/ that bundle_intake
picks up through the normal path.
"""

from __future__ import annotations

import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config

# PP-FENCE-001: atomic_write_json kept for one remaining gap not yet in fence:
#   Stub writes go to newitems_path (not itemdata_root) — outside fence scope.
# (The second gap — a direct ItemData key-deletion write — was removed in
# session 48; see the SKU-collision handling below.)
from tgw.items import atomic_write_json
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME     = 'multi_intake'
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.webp',
                  '.JPG', '.JPEG', '.PNG'}
# App-generated artifacts that are not item photos
_SKIP_NAMES    = {'film.jpg', 'singleShot.jpg', 'exportGif.gif'}

# audit#1143 #1246 (deferred #1245 finding): dedup registry for the SKU-
# collision notify() below. Child SKUs are derived deterministically from
# base_sku (_child_skus above), so a batch re-drop of the identical zip
# reproduces the exact same collision on the exact same SKU every time —
# without this, notify() would spam the same external channel once per
# re-drop instead of once ever per SKU.
_COLLISION_NOTIFY_REGISTRY = Path('/opt/TGW/var/multi-intake-collision-notified.json')


def _already_notified_collision(sku: str) -> bool:
    try:
        if not _COLLISION_NOTIFY_REGISTRY.exists():
            return False
        registry = json.loads(_COLLISION_NOTIFY_REGISTRY.read_text(encoding='utf-8'))
        return sku in registry
    except (OSError, ValueError) as exc:
        log.warning('multi_intake: collision-notify registry unreadable (%s) — notifying anyway', exc)
        return False


def _record_notified_collision(sku: str, base_sku: str) -> None:
    try:
        registry: Dict[str, Any] = {}
        if _COLLISION_NOTIFY_REGISTRY.exists():
            registry = json.loads(_COLLISION_NOTIFY_REGISTRY.read_text(encoding='utf-8'))
        from datetime import datetime, timezone
        registry[sku] = {'base_sku': base_sku, 'notified_at': datetime.now(timezone.utc).isoformat()}
        _COLLISION_NOTIFY_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        tmp = _COLLISION_NOTIFY_REGISTRY.with_suffix('.tmp')
        tmp.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding='utf-8')
        tmp.replace(_COLLISION_NOTIFY_REGISTRY)
    except Exception as exc:
        log.warning('multi_intake: could not update collision-notify registry: %s', exc)


def _is_timestamp_dir(name: str) -> bool:
    """True for YYYYMMDDHHMMSS subdir names (14 digits)."""
    return name.isdigit() and len(name) == 14


def _child_skus(base_sku: str, count: int) -> List[str]:
    """
    Generate `count` child SKUs by incrementing the numeric suffix of base_sku.
    tgw202605020017010, count=3 → ['tgw202605020017010', '...011', '...012']
    """
    prefix = 'tgw'
    num_str = base_sku[len(prefix):]
    base_num = int(num_str)
    width = len(num_str)
    return [f'{prefix}{str(base_num + i).zfill(width)}' for i in range(count)]


class MultiIntakeWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload    = job.get('payload_json') or {}
        source_str = payload.get('source', '')
        if not source_str:
            raise HardFailure('multi_intake job missing source in payload')

        source_dir = Path(source_str)
        if not source_dir.exists():
            log.info('multi source dir gone — assuming already processed: %s',
                     source_dir)
            return

        zips = list(source_dir.glob('*.zip'))
        if not zips:
            raise HardFailure(f'no zip found in multi bundle dir: {source_dir}')
        zip_path = zips[0]

        stub_data: Dict[str, Any] = {}
        stub_files = list(source_dir.glob('*.json'))
        if stub_files:
            try:
                stub_data = json.loads(stub_files[0].read_text(encoding='utf-8'))
            except Exception:
                pass

        base_sku = stub_data.get('sku', source_dir.name)
        location = stub_data.get('location', '')
        template = stub_data.get('TEMPLATE', 'default')
        newitems_dir: Path = self.config['newitems_path']

        children = self._extract_items(zip_path, newitems_dir,
                                       base_sku, location, template)
        shutil.rmtree(source_dir)

        log.info('multi_intake split %s into %d items: %s',
                 zip_path.name, len(children), children)
        tgw_logging.log_event('multi_intake_complete',
                              zip_name=zip_path.name,
                              items=len(children),
                              skus=children)

    def _extract_items(self, zip_path: Path, newitems_dir: Path,
                       base_sku: str, location: str,
                       template: str) -> List[str]:
        """Extract each timestamp subdir as a separate newitems dir bundle."""

        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Group members by top-level timestamp subdir, sorted by dir name
            subdirs: Dict[str, List[str]] = {}
            for name in zf.namelist():
                parts = Path(name).parts
                if len(parts) < 2:
                    continue
                top = parts[0]
                if _is_timestamp_dir(top):
                    subdirs.setdefault(top, []).append(name)

            if not subdirs:
                raise HardFailure(
                    f'no timestamp subdirs in {zip_path.name} — cannot auto-split'
                )

            sorted_dirs = sorted(subdirs.keys())
            skus = _child_skus(base_sku, len(sorted_dirs))
            children: List[str] = []

            for ts_dir, sku in zip(sorted_dirs, skus):
                members = subdirs[ts_dir]
                image_members = [
                    m for m in members
                    if Path(m).suffix.lower() in {s.lower() for s in IMAGE_SUFFIXES}
                    and Path(m).name not in _SKIP_NAMES
                ]
                if not image_members:
                    log.warning('no images in subdir %s — skipping', ts_dir)
                    continue

                dest = newitems_dir / sku
                dest.mkdir(parents=True, exist_ok=True)

                for member in image_members:
                    dest_file = dest / Path(member).name
                    with zf.open(member) as src, open(dest_file, 'wb') as dst:
                        shutil.copyfileobj(src, dst)

                stub_path = dest / f'{sku}.json'
                atomic_write_json(stub_path, {
                    'sku':        sku,
                    'location':   location,
                    'TEMPLATE':   template,
                    'title':      '',
                    'source_sku': base_sku,
                }, pretty=self.config.get('pretty', True))

                # audit#1143 #1235 follow-up (session 48): a derived child SKU
                # can collide with an already-catalogued ItemData record (e.g.
                # a re-drop of the same multi-zip after a prior partial run).
                # bundle_intake's own idempotency already handles this safely
                # — _write_item_json()/_copy_images() no-op on an existing
                # SKU, never touching its fields — so this worker used to
                # ALSO directly patch the existing record (strip 'Item
                # number', bypassing the fence) is both redundant and
                # unverified: one confirmed case in production
                # (tgw202604130911246) turned out to be a currently-Active
                # live eBay listing with no sibling children and no archived
                # pre-strip snapshot, i.e. never actually validated safe.
                # Removed. Only surface the collision for operator awareness;
                # let the normal newitems_dir path (already written above)
                # handle it.
                existing_json = (self.config['itemdata_root']
                                 / sku / f'{sku}.json')
                if existing_json.exists():
                    log.warning(
                        'multi_intake: derived child sku %s (from base %s) '
                        'already has an ItemData record — leaving it untouched, '
                        'new photos will merge in via the normal newitems_dir path',
                        sku, base_sku,
                    )
                    tgw_logging.log_event(
                        'multi_intake_sku_collision', sku=sku, base_sku=base_sku,
                    )
                    # audit#1143 #1246 (deferred #1245 finding): the durable
                    # per-item finding (log line + log_event above) always
                    # records every hit, but the external notify() channel
                    # is deduped per-SKU — a batch re-drop of the identical
                    # zip reproduces the exact same collision on the exact
                    # same SKU every time (_child_skus is deterministic), so
                    # without this the same channel gets spammed once per
                    # re-drop instead of once ever.
                    if not _already_notified_collision(sku):
                        from tgw.notify import notify
                        notify(
                            'multi_intake SKU collision',
                            f'Derived child {sku} (base {base_sku}) already has an '
                            f'ItemData record — leaving it untouched. Verify it is not '
                            f'a mistaken duplicate; if it turns out to be one, run an '
                            f'operator-forced ebay_stage duplicate-check pass on {sku} '
                            f'before publishing/updating it.',
                            level='warning',
                        )
                        _record_notified_collision(sku, base_sku)

                children.append(sku)
                log.info('child bundle %s: %d photos (from zip subdir %s)',
                         sku, len(image_members), ts_dir)

        return children


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-multi-intake-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = MultiIntakeWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
