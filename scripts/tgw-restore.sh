#!/bin/bash
set -e

# Print header
echo "============================================"
echo "TGW Restore Script — $(date)"
echo "============================================"

# Parse arguments
SOURCE="local"
DRY_RUN=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --source)
            SOURCE="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        *)
            echo "Unknown argument: $1"
            exit 1
            ;;
    esac
done

# Validate source
case "$SOURCE" in
    local|gdrive|usb)
        ;;
    *)
        echo "Invalid --source: must be local, gdrive, or usb"
        exit 1
        ;;
esac

echo "=== Step 1: Stopping TGW workers ==="
if [ "$DRY_RUN" = true ]; then
    echo "[DRY RUN] Would run: systemctl stop 'tgw-worker@*.service'"
else
    echo "Stopping all tgw-worker services..."
    systemctl stop 'tgw-worker@*.service'
fi

echo "=== Step 2: Fetching backup ==="
BACKUP_DIR="/opt/TGW/var/backups"
case "$SOURCE" in
    gdrive)
        if [ "$DRY_RUN" = true ]; then
            echo "[DRY RUN] Would run: rclone copy gdrive-tgw:TGW-Backups/ $BACKUP_DIR/ --progress"
        else
            echo "Downloading from gdrive-tgw remote..."
            rclone copy gdrive-tgw:TGW-Backups/ "$BACKUP_DIR/" --progress
        fi
        ;;
    usb)
        USB_PATH=$(find /run/media/ -maxdepth 2 -name "TGW-VAULT" -type d | head -n 1)
        if [ -z "$USB_PATH" ]; then
            echo "Error: No TGW-VAULT found in /run/media/"
            exit 1
        fi
        if [ "$DRY_RUN" = true ]; then
            echo "[DRY RUN] Would copy from: $USB_PATH to $BACKUP_DIR"
        else
            echo "Copying from USB vault at $USB_PATH..."
            cp -rv "$USB_PATH/"* "$BACKUP_DIR/"
        fi
        ;;
    local)
        echo "Using existing local backups in $BACKUP_DIR"
        ;;
esac

echo "=== Step 3: Restoring PostgreSQL ==="
PGDUMP="$BACKUP_DIR/tgw-state-machine.pgdump"
if [ ! -f "$PGDUMP" ]; then
    echo "Error: PostgreSQL dump not found at $PGDUMP"
    exit 1
fi

echo "Found pgdump: $(ls -lh "$PGDUMP")"
if [ "$DRY_RUN" = true ]; then
    echo "[DRY RUN] Would run: sudo -u postgres pg_restore --clean --if-exists -d state_machine $PGDUMP"
else
    echo "Restoring PostgreSQL database..."
    sudo -u postgres pg_restore --clean --if-exists -d state_machine "$PGDUMP"
fi

echo "=== Step 4: Complete ==="
echo "Restore complete!"
echo "Next steps:"
echo "1. Verify restore with: sudo tgw health"
echo "2. Restart workers when ready: systemctl start 'tgw-worker@*.service'"
