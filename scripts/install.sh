#!/usr/bin/env bash
# TGW install script
# Run from the repo root as root or with sudo where needed.
# Installs the package into the TGW venv and deploys service files.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="/opt/TGW/.venvironments/tgw"
SYSTEMD_DIR="/etc/systemd/system"

echo "==> Installing trader-grims-warehouse into $VENV"
"$VENV/bin/pip" install -e "$REPO_DIR"

echo "==> Deploying systemd units"
for unit in \
    queue-launcher.service \
    queue-workers-startup.timer \
    queue-workers.target \
    trader-grims-backup.service; do
    src="$REPO_DIR/systemd/$unit"
    if [[ -f "$src" ]]; then
        cp "$src" "$SYSTEMD_DIR/$unit"
        echo "    installed $unit"
    fi
done

echo "==> Reloading systemd"
systemctl daemon-reload

echo "==> Enabling units"
systemctl enable queue-workers-startup.timer
systemctl enable trader-grims-backup.service

echo ""
echo "Done. Start services with:"
echo "  systemctl start queue-workers-startup.timer"
echo "  systemctl start trader-grims-backup.service"
echo ""
echo "Check status with:"
echo "  systemctl status queue-launcher.service"
echo "  systemctl status trader-grims-backup.service"
