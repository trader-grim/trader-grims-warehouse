#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_link(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f'missing link: {path}')
    return path.resolve()


def find_queue_dirs(root: Path) -> list[Path]:
    out = []
    if not root.exists():
        return out
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / '.queue_worker').exists() and (child / '.queue_worker_config').exists():
            out.append(child)
    return out


def start_queue(queue_dir: Path) -> int:
    worker_path = resolve_link(queue_dir / '.queue_worker')
    config_path = resolve_link(queue_dir / '.queue_worker_config')
    logger.info('starting queue=%s worker=%s config=%s', queue_dir.name, worker_path, config_path)
    cmd = [sys.executable, str(worker_path), '--config', str(config_path)]

    # Use Popen to launch workers asynchronously in the background.
    # This prevents blocking and isolates worker runtime lifecycles.
    try:
        subprocess.Popen(cmd, cwd=str(queue_dir))
        return 0
    except Exception as e:
        logger.error(f'Failed to spawn background process for {queue_dir.name}: {e}')
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description='TGW queue launcher')
    parser.add_argument('--queues-root', default='/opt/TGW/runtime/state/queues')
    parser.add_argument('--queue', action='append', help='start only named queue(s)')
    parser.add_argument('--log-level', default='INFO')
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO), format='%(asctime)s %(levelname)s %(name)s: %(message)s', force=True)

    root = Path(args.queues_root)
    if args.queue:
        queues = [root / name for name in args.queue]
    else:
        queues = find_queue_dirs(root)

    if not queues:
        logger.warning('no valid queue dirs found under %s', root)
        return 0

    for queue_dir in queues:
        try:
            start_queue(queue_dir)
        except Exception:
            logger.exception('failed queue=%s', queue_dir)

    # Always keep the main launcher thread alive to monitor the service group
    import time
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        logger.info("Queue launcher shutting down.")

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
