#!/usr/bin/env bash
# tgw-push-config — push a NixOS config update to a running host.
#
# Local mode (TARGET=local): sudo nixos-rebuild switch directly on this machine.
#   Use for tgw-prod (this machine); no SSH needed.
#   Example: bash scripts/tgw-push-config.sh tgw-prod local
#
# Normal mode: build closure locally, copy via SSH, activate remotely.
#   Requires nix.settings.trusted-users = ["root" "@wheel"] on target.
#   Example: bash scripts/tgw-push-config.sh tgw-test 192.168.60.101
#
# Bootstrap mode (--bootstrap): rsync source to target, rebuild there locally.
#   Use the first time on a new host before trusted-users is in the config.
#   Example: bash scripts/tgw-push-config.sh --bootstrap tgw-test 192.168.60.101

set -euo pipefail

BOOTSTRAP=false
if [[ "${1:-}" == "--bootstrap" ]]; then
  BOOTSTRAP=true
  shift
fi

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 [--bootstrap] <nixos-host> <target-ip-or-hostname|local>" >&2
  exit 1
fi

NIXOS_HOST="$1"
TARGET="$2"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# Canonical flake lives at ~/tgw-flake (flake.nix in $REPO is a symlink).
# Using the real path avoids "symlink beyond store" errors during nix copy.
FLAKE="${TGW_FLAKE:-$HOME/tgw-flake}"

echo "==> tgw-push-config: $NIXOS_HOST → ${TARGET}${BOOTSTRAP:+ (bootstrap)}"
echo "    flake: $FLAKE"

. /home/tgw/.nix-profile/etc/profile.d/nix.sh 2>/dev/null || true

if $BOOTSTRAP; then
  # Bootstrap: rsync source to target, rebuild locally there.
  # Avoids the trusted-key catch-22: local builds are always trusted by the
  # local nix daemon.  Run once; after push lands, use normal mode.
  REMOTE_FLAKE="/tmp/tgw-flake-bootstrap"
  echo "    rsyncing flake source to db@$TARGET:$REMOTE_FLAKE ..."
  rsync -av --exclude='.git' --exclude='__pycache__' \
    "$FLAKE/" "db@$TARGET:$REMOTE_FLAKE/"
  echo "    running nixos-rebuild switch on $TARGET ..."
  ssh "db@$TARGET" "sudo nixos-rebuild switch --flake '$REMOTE_FLAKE#$NIXOS_HOST'"
elif [[ "$TARGET" == "local" ]]; then
  # Local mode: this machine IS the target; rebuild directly without SSH.
  sudo nixos-rebuild switch --flake "path:$FLAKE#$NIXOS_HOST"
else
  # Normal mode: build locally, copy closure to target, activate remotely.
  # Requires nix.settings.trusted-users = ["root" "@wheel"] on the target.
  nix run nixpkgs#nixos-rebuild -- switch \
    --flake "path:$FLAKE#$NIXOS_HOST" \
    --target-host "db@$TARGET" \
    --use-remote-sudo \
    --option require-sigs false
fi
