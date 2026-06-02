"""
tgw.workers.multi_intake — Multi-item bundle splitting worker.

Handles zips that contain multiple items, each in a timestamp-named subdir:
  multi/<SKU>/
    <SKU>.json          ← parent stub (location, template inherited by children)
    data_<ts>.zip       ← zip with subdirs: YYYYMMDDHHMMSS/1.jpg, 2.jpg ...

Each subdir becomes a separate dir-format bundle in newitems/:
  newitems/<child_SKU>/
    <child_SKU>.json    ← stub with inherited location/template
    1.jpg, 2.jpg ...    ← photos

The bundle_intake worker then picks these up through the normal path.

Child SKU format: tgw + YYYYMMDDHHMMSS + 000  (matches tgwYYYYMMDDHHMMSSmmm)
"""

from __future__ import annotations

import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, List

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import atomic_write_json
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME     = 'multi_intake'
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.gif', '.webp',
                  '.JPG', '.JPEG', '.PNG'}


def _is_timestamp_dir(name: str) -> bool:
    """True for YYYYMMDDHHMMSS subdir names (14 digits)."""
    return name.isdigit() and len(name) == 14


def _child_sku(ts_dirname: str) -> str:
    """Convert a YYYYMMDDHHMMSS dirname to a tgwYYYYMMDDHHMMSSmmm SKU."""
    return f'tgw{ts_dirname}000'


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

        # Find stub and zip inside source_dir
        zips = list(source_dir.glob('*.zip'))
        if not zips:
            raise HardFailure(f'no zip found in multi bundle dir: {source_dir}')
        zip_path = zips[0]

        stub_candidates = [f for f in source_dir.glob('*.json')]
        stub_data: Dict[str, Any] = {}
        if stub_candidates:
            import json
            try:
                stub_data = json.loads(
                    stub_candidates[0].read_text(encoding='utf-8')
                )
            except Exception:
                pass

        location = stub_data.get('location', '')
        template = stub_data.get('TEMPLATE', 'default')
        newitems_dir: Path = self.config['newitems_path']

        children = self._extract_items(zip_path, newitems_dir, location, template)

        shutil.rmtree(source_dir)

        log.info('multi_intake split %s into %d items', zip_path.name, len(children))
        tgw_logging.log_event('multi_intake_complete',
                              zip_name=zip_path.name,
                              items=len(children),
                              skus=children)

    def _extract_items(self, zip_path: Path, newitems_dir: Path,
                       location: str, template: str) -> List[str]:
        """Extract each timestamp subdir as a separate newitems bundle."""
        children: List[str] = []

        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Group entries by their top-level subdir
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
                    f'no timestamp subdirs found in {zip_path.name}; '
                    'cannot split automatically'
                )

            for ts_dir, members in sorted(subdirs.items()):
                sku = _child_sku(ts_dir)
                dest = newitems_dir / sku
                dest.mkdir(parents=True, exist_ok=True)

                # Extract images only (skip mp4, gif, singleShot, film)
                image_members = [
                    m for m in members
                    if Path(m).suffix.lower() in {s.lower() for s in IMAGE_SUFFIXES}
                    and Path(m).name not in {'film.jpg', 'singleShot.jpg',
                                             'exportGif.gif'}
                ]
                for member in image_members:
                    dest_file = dest / Path(member).name
                    with zf.open(member) as src, open(dest_file, 'wb') as dst:
                        import shutil as _sh
                        _sh.copyfileobj(src, dst)

                if not any(dest.iterdir()):
                    # No images extracted — remove the empty dir
                    dest.rmdir()
                    log.warning('no images in subdir %s, skipping', ts_dir)
                    continue

                # Write stub JSON for this child
                stub_path = dest / f'{sku}.json'
                atomic_write_json(stub_path, {
                    'sku':      sku,
                    'location': location,
                    'TEMPLATE': template,
                    'title':    '',
                }, pretty=self.config.get('pretty', True))

                children.append(sku)
                log.info('created child bundle %s (%d photos)',
                         sku, len(image_members))

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
