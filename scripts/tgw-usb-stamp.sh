#!/usr/bin/env bash
# tgw-usb-stamp — stamp current TGW state onto the TGW-VAULT btrfs USB partition.
#
# Copies secrets, pg dump, and flake source to the TGW-VAULT partition so the
# stick is a self-contained cold-start kit.  Safe to run while the system is live.
#
# What lands on the stick:
#   secrets/   — /opt/TGW/secrets/ (age keys, eBay tokens, API keys)
#   dumps/     — pg_dump of state_machine (dated; previous dump kept as .prev)
#   flake/     — git bundle of the flake repo (pull with: git clone tgw.bundle)
#
# Usage (run as root or with sudo; needs access to tgw secrets):
#   sudo bash scripts/tgw-usb-stamp.sh
#   sudo bash scripts/tgw-usb-stamp.sh --dry-run   # show what would happen

set -euo pipefail

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

VAULT_LABEL="TGW-VAULT"
MOUNT_DIR="/mnt/tgw-vault-stamp"
SECRETS_SRC="/opt/TGW/secrets"
REPO="/opt/TGW/src/trader-grims-warehouse"
DB_NAME="state_machine"
DB_USER="tgw"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# ── helpers ──────────────────────────────────────────────────────────────────

log()  { echo "==> $*"; }
info() { echo "    $*"; }
run()  { $DRY_RUN && echo "    [dry-run] $*" || "$@"; }

# ── find the vault partition ──────────────────────────────────────────────────

VAULT_DEV="$(blkid -L "$VAULT_LABEL" 2>/dev/null || true)"
if [[ -z "$VAULT_DEV" ]]; then
  echo "ERROR: no partition with label '$VAULT_LABEL' found." >&2
  echo "       Insert the TGW USB stick and retry." >&2
  exit 1
fi
info "found $VAULT_LABEL at $VAULT_DEV"

# ── mount ────────────────────────────────────────────────────────────────────

MOUNTED_HERE=false
if ! mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
  run mkdir -p "$MOUNT_DIR"
  run mount -o noatime "$VAULT_DEV" "$MOUNT_DIR"
  MOUNTED_HERE=true
fi

cleanup() {
  if $MOUNTED_HERE && mountpoint -q "$MOUNT_DIR" 2>/dev/null; then
    umount "$MOUNT_DIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ── secrets ──────────────────────────────────────────────────────────────────

log "stamping secrets → $MOUNT_DIR/secrets/"
run rsync -a --delete --chmod=D700,F600 \
  "$SECRETS_SRC/" "$MOUNT_DIR/secrets/"

# ── pg dump ──────────────────────────────────────────────────────────────────

DUMP_FILE="$MOUNT_DIR/dumps/state_machine-$STAMP.pgdump"
PREV_LINK="$MOUNT_DIR/dumps/latest.pgdump"

log "dumping $DB_NAME → dumps/state_machine-$STAMP.pgdump"
run mkdir -p "$MOUNT_DIR/dumps"

if ! $DRY_RUN; then
  sudo -u "$DB_USER" pg_dump -Fc "$DB_NAME" > "$DUMP_FILE"
  # keep only the two most recent dumps (current + previous)
  ls -t "$MOUNT_DIR/dumps/"*.pgdump 2>/dev/null | tail -n +3 | xargs -r rm --
  ln -sf "$(basename "$DUMP_FILE")" "$PREV_LINK"
fi

# ── flake bundle ─────────────────────────────────────────────────────────────

log "bundling flake repo → flake/tgw.bundle"
run mkdir -p "$MOUNT_DIR/flake"
run git -C "$REPO" bundle create "$MOUNT_DIR/flake/tgw.bundle" --all

# ── sync and report ──────────────────────────────────────────────────────────

log "syncing to disk..."
run sync

log "done — TGW-VAULT stamped at $STAMP"
if ! $DRY_RUN; then
  df -h "$MOUNT_DIR" | tail -1 | awk '{print "    used " $3 " of " $2 " (" $5 " full)"}'
fi
