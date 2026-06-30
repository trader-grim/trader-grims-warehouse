#!/usr/bin/env bash
# TGW uninstall script
# Stops services, removes systemd units, uninstalls the package.
# Does NOT touch /opt/TGW/data or config — live data is never removed.

set -euo pipefail

VENV="/opt/TGW/.venvironments/tgw"
SYSTEMD_DIR="/etc/systemd/system"

echo "==> Stopping services"
systemctl stop queue-workers-startup.timer  2>/dev/null || true
systemctl stop queue-launcher.service       2>/dev/null || true

echo "==> Disabling units"
systemctl disable queue-workers-startup.timer 2>/dev/null || true
systemctl disable trader-grims-backup.service 2>/dev/null || true

echo "==> Removing systemd units"
for unit in \
    queue-launcher.service \
    queue-workers-startup.timer \
    queue-workers.target; do
    rm -f "$SYSTEMD_DIR/$unit"
    echo "    removed $unit"
done

echo "==> Reloading systemd"
systemctl daemon-reload

echo "==> Uninstalling package from $VENV"
"$VENV/bin/pip" uninstall -y trader-grims-warehouse 2>/dev/null || true

echo ""
echo "Done. Live data in /opt/TGW/data and /opt/TGW/config is untouched."
echo "trader-grims-backup.service was not removed — manage it separately."
