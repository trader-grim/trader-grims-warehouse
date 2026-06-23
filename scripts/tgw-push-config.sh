#!/usr/bin/env bash
# tgw-push-config — push a NixOS config update to a running host.
#
# Normal mode: evaluates the flake locally on MX, transfers the closure via SSH,
# activates remotely.  Requires nix.settings.trusted-users to include @wheel on
# the target so unsigned MX builds are accepted.
#
# Bootstrap mode (--bootstrap): rsync the flake source to the target and run
# nixos-rebuild switch locally.  Use this the FIRST time, before trusted-users
# is in the target's config — local builds are always trusted by the local daemon.
# After the bootstrap push lands, normal mode works for all future updates.
#
# Usage:
#   bash scripts/tgw-push-config.sh <nixos-host> <target-ip-or-hostname>
#   bash scripts/tgw-push-config.sh tgw-test 192.168.60.101
#   bash scripts/tgw-push-config.sh --bootstrap tgw-test 192.168.60.101

set -euo pipefail

BOOTSTRAP=false
if [[ "${1:-}" == "--bootstrap" ]]; then
  BOOTSTRAP=true
  shift
fi

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 [--bootstrap] <nixos-host> <target-ip-or-hostname>" >&2
  exit 1
fi

NIXOS_HOST="$1"
TARGET="$2"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> tgw-push-config: $NIXOS_HOST → db@$TARGET${BOOTSTRAP:+ (bootstrap)}"
echo "    flake: $REPO"

. /home/tgw/.nix-profile/etc/profile.d/nix.sh 2>/dev/null || true

if $BOOTSTRAP; then
  # Bootstrap: rsync source to target, rebuild locally.
  # Avoids the trusted-key catch-22: local builds are always trusted by the
  # local nix daemon.  Run this once; after the push lands, use normal mode.
  REMOTE_FLAKE="/tmp/tgw-flake-bootstrap"
  echo "    rsyncing flake source to db@$TARGET:$REMOTE_FLAKE ..."
  rsync -av --exclude='.git' --exclude='__pycache__' \
    "$REPO/" "db@$TARGET:$REMOTE_FLAKE/"
  echo "    running nixos-rebuild switch on $TARGET ..."
  ssh "db@$TARGET" "sudo nixos-rebuild switch --flake '$REMOTE_FLAKE#$NIXOS_HOST'"
else
  # Normal mode: build locally on MX, copy closure to target, activate remotely.
  # Requires nix.settings.trusted-users = ["root" "@wheel"] on the target.
  # nixos-rebuild is a NixOS tool; invoke via nix run on non-NixOS hosts (MX).
  nix run nixpkgs#nixos-rebuild -- switch \
    --flake "path:$REPO#$NIXOS_HOST" \
    --target-host "db@$TARGET" \
    --use-remote-sudo \
    --option require-sigs false
fi
