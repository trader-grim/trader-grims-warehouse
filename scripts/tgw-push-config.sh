#!/usr/bin/env bash
# tgw-push-config — push a NixOS config update to a remote host.
#
# Evaluates the flake locally (on MX) and transfers the Nix store closure to
# the target machine over SSH.  The remote host does not need a copy of the
# flake source — this is the standard distribution mechanism.
#
# Requirements:
#   - SSH access to the target (key-based; db user with sudo)
#   - Nix installed locally with flakes enabled
#   - Target reachable by IP (Tailscale IP preferred after pairing)
#
# Usage:
#   bash scripts/tgw-push-config.sh tgw-test 100.x.y.z
#   bash scripts/tgw-push-config.sh tgw-prod 100.x.y.z
#   bash scripts/tgw-push-config.sh tgw-test <hostname>.local   # local LAN
#
# The flake is read from the current directory (the git repo root).
# Use --dry-run to see what would be built without applying:
#   bash scripts/tgw-push-config.sh tgw-test 100.x.y.z --dry-run

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <hostname> <ip-or-hostname>" >&2
  echo "  hostname: NixOS config name (e.g. tgw-test, tgw-prod)" >&2
  echo "  ip-or-hostname: SSH target (Tailscale IP, local IP, or .local name)" >&2
  exit 1
fi

NIXOS_HOST="$1"
TARGET="$2"
DRY_RUN_FLAG=""
if [[ "${3:-}" == "--dry-run" ]]; then
  DRY_RUN_FLAG="--dry-run"
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> tgw-push-config: $NIXOS_HOST → db@$TARGET"
echo "    flake: path:$REPO"

nixos-rebuild switch \
  --flake "path:$REPO#$NIXOS_HOST" \
  --target-host "db@$TARGET" \
  --use-remote-sudo \
  $DRY_RUN_FLAG
