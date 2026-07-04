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
mkdir -p "$BACKUP_DIR"
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
        # TGW-VAULT layout (scripts/tgw-usb-stamp.sh): secrets/, dumps/, flake/
        # subvolumes at the partition root — NOT a flat directory.
        USB_PATH=$(find /run/media/ -maxdepth 2 -iname "TGW-VAULT" -type d 2>/dev/null | head -n 1)
        if [ -z "$USB_PATH" ]; then
            USB_PATH=$(findmnt -n -o TARGET -S LABEL=TGW-VAULT 2>/dev/null || true)
        fi
        if [ -z "$USB_PATH" ]; then
            echo "Error: No TGW-VAULT partition found mounted (checked /run/media and findmnt)"
            exit 1
        fi
        if [ "$DRY_RUN" = true ]; then
            echo "[DRY RUN] Would copy secrets/ dumps/ flake/ from $USB_PATH to $BACKUP_DIR"
        else
            echo "Copying from USB vault at $USB_PATH..."
            cp -rv "$USB_PATH/dumps" "$BACKUP_DIR/"
            cp -rv "$USB_PATH/secrets" "$BACKUP_DIR/"
        fi
        ;;
    local)
        echo "Using existing local backups in $BACKUP_DIR"
        ;;
esac

echo "=== Step 3: Restoring secrets (if present) ==="
if [ -d "$BACKUP_DIR/secrets" ]; then
    if [ "$DRY_RUN" = true ]; then
        echo "[DRY RUN] Would run: rsync -a --chmod=D700,F600 $BACKUP_DIR/secrets/ /opt/TGW/secrets/"
    else
        rsync -a --chmod=D700,F600 "$BACKUP_DIR/secrets/" /opt/TGW/secrets/
        chown -R tgw:tgw /opt/TGW/secrets/
    fi
else
    echo "No secrets/ in $BACKUP_DIR — skipping (expected for --source local if secrets already in place)"
fi

echo "=== Step 4: Restoring PostgreSQL ==="
# tgw-usb-stamp.sh writes dumps/state_machine-<STAMP>.pgdump and a
# dumps/latest.pgdump symlink pointing at the newest one — always follow
# the symlink so this stays correct across stamp runs.
PGDUMP="$BACKUP_DIR/dumps/latest.pgdump"
if [ ! -e "$PGDUMP" ]; then
    # local-source fallback: newest *.pgdump directly under BACKUP_DIR
    PGDUMP=$(ls -t "$BACKUP_DIR"/*.pgdump 2>/dev/null | head -n 1 || true)
fi
if [ -z "$PGDUMP" ] || [ ! -e "$PGDUMP" ]; then
    echo "Error: no PostgreSQL dump found under $BACKUP_DIR (checked dumps/latest.pgdump and *.pgdump)"
    exit 1
fi

echo "Found pgdump: $(ls -lhL "$PGDUMP")"
if [ "$DRY_RUN" = true ]; then
    echo "[DRY RUN] Would run: sudo -u postgres pg_restore --clean --if-exists -d state_machine $PGDUMP"
else
    echo "Restoring PostgreSQL database..."
    sudo -u postgres pg_restore --clean --if-exists -d state_machine "$PGDUMP"
fi

echo "=== Step 5: Complete ==="
echo "Restore complete!"
echo "Next steps:"
echo "1. Verify restore with: sudo -u tgw tgw health"
echo "2. Verify the queue round-trips: sudo -u tgw tgw enqueue-sku --queue echo <any-sku>"
echo "   then: journalctl -u tgw-worker@echo.service -n 20"
echo "3. Restart workers when ready: systemctl start 'tgw-worker@*.service'"
echo ""
echo "For a full bare-metal rebuild (no existing NixOS host), see:"
echo "  docs/TGW-Plan-Vault/reference/TGW-VAULT-RESTORE.md"
