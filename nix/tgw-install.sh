#!/usr/bin/env bash
# tgw-install — run FROM the A1131 to install NixOS on the production machine.
#
# Prerequisites (on production machine, booted from ISO):
#   service ssh start
#   passwd root
#   # IP: 192.168.60.100
#
# Usage (on A1131):
#   bash tgw-install.sh [--dry-run]

set -euo pipefail

PROD_IP="192.168.60.100"
VAULT_LABEL="TGW-VAULT"
MOUNT_DIR="/mnt/tgw-vault"
FLAKE_DIR="/tmp/tgw-flake"
HW_OUT="/tmp/tgw-prod-hardware.nix"
DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

log()  { echo "==> $*"; }
info() { echo "    $*"; }

# ── 1. get the flake ──────────────────────────────────────────────────────────

if [[ -d "$FLAKE_DIR/.git" ]]; then
  log "flake already at $FLAKE_DIR — skipping clone"
else
  log "looking for TGW-VAULT USB..."
  VAULT_DEV="$(blkid -L "$VAULT_LABEL" 2>/dev/null || true)"

  if [[ -n "$VAULT_DEV" ]]; then
    info "found $VAULT_LABEL at $VAULT_DEV"
    mkdir -p "$MOUNT_DIR"
    mount -o noatime "$VAULT_DEV" "$MOUNT_DIR"
    log "cloning flake bundle → $FLAKE_DIR"
    git clone "$MOUNT_DIR/flake/tgw.bundle" "$FLAKE_DIR"
    umount "$MOUNT_DIR"
  else
    log "TGW-VAULT not found — trying GitHub..."
    git clone https://github.com/trader-grim/trader-grims-warehouse "$FLAKE_DIR"
  fi
fi

# ── 2. sanity-check the disko config ─────────────────────────────────────────

DISKO="$FLAKE_DIR/nix/hosts/tgw-prod-disko.nix"
DEVICE="$(grep 'device' "$DISKO" | grep -v '^#' | head -1)"
info "disko device line: $DEVICE"
if ! echo "$DEVICE" | grep -q "nvme0n1"; then
  echo "ERROR: disko still has wrong device (expected nvme0n1). Edit $DISKO and re-run." >&2
  exit 1
fi
if grep -q '"500G"' "$DISKO"; then
  echo "ERROR: disko LVM size is still 500G — must be 200G for a ~477G disk." >&2
  exit 1
fi
info "disko config looks correct"

# ── 3. verify SSH reachability ────────────────────────────────────────────────

log "checking SSH to root@$PROD_IP ..."
if ! ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
         -o BatchMode=yes root@"$PROD_IP" true 2>/dev/null; then
  echo ""
  echo "Cannot reach root@$PROD_IP. On the production machine (ISO boot), run:"
  echo "  service ssh start   (or: systemctl start ssh)"
  echo "  passwd root"
  echo "  ip addr show | grep inet"
  echo ""
  echo "Then re-run this script."
  exit 1
fi
info "SSH OK"

# ── 4. run nixos-anywhere ─────────────────────────────────────────────────────

log "starting nixos-anywhere → root@$PROD_IP"
info "this will WIPE nvme0n1 on the production machine"
info "hardware config will be written to $HW_OUT"
echo ""

if $DRY_RUN; then
  info "[dry-run] would run:"
  echo "  nix run github:nix-community/nixos-anywhere -- \\"
  echo "    --generate-hardware-config nixos-generate-config $HW_OUT \\"
  echo "    --flake path:$FLAKE_DIR#tgw-prod \\"
  echo "    root@$PROD_IP"
  exit 0
fi

nix run github:nix-community/nixos-anywhere -- \
  --generate-hardware-config nixos-generate-config "$HW_OUT" \
  --flake "path:$FLAKE_DIR#tgw-prod" \
  root@"$PROD_IP"

# ── 5. commit hardware config ─────────────────────────────────────────────────

log "nixos-anywhere complete"

if [[ -f "$HW_OUT" ]]; then
  DEST="$FLAKE_DIR/nix/hardware/tgw-prod-hardware.nix"
  cp "$HW_OUT" "$DEST"
  info "hardware config written to $DEST"
  echo ""
  echo "Commit and push the hardware config:"
  echo "  cd $FLAKE_DIR"
  echo "  git add nix/hardware/tgw-prod-hardware.nix"
  echo "  git commit -m 'feat: tgw-prod hardware config'"
  echo "  git push"
else
  info "WARNING: hardware config not found at $HW_OUT — check nixos-anywhere output"
fi

echo ""
log "production machine is rebooting into NixOS"
log "next: Phase H — post-install restore"
log "see: docs/TGW-Plan-Vault/reference/runbooks/nixos-prod-cutover-runbook.md"
