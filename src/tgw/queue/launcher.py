"""
tgw.queue.launcher — RETIRED.

The filesystem-queue launcher has been superseded by systemd templated units
(tgw-worker@<queue>.service). Workers are now kept alive by systemd with
`After=postgresql.service` ordering.

This module exists only so the `tgw-queue-launcher` console script does not
crash if the service unit has not yet been updated. Disable and stop
queue-launcher.service from a real terminal:

    sudo systemctl disable --now queue-launcher.service
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    log.warning(
        'queue-launcher is retired — workers are now managed by '
        'systemd tgw-worker@<queue>.service units. '
        'Disable this service: sudo systemctl disable --now queue-launcher.service'
    )
    # Sleep so systemd does not restart-loop if the unit is still enabled.
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    raise SystemExit(main())
