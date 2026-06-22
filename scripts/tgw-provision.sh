#!/usr/bin/env bash
# tgw-provision — provision a NixOS host from scratch via nixos-anywhere.
#
# Runs from any machine on the local network that has Nix available.
# The flake is read from the current directory (the git repo root).
# Does NOT require internet — all traffic is LAN or Tailscale.
#
# Usage:
#   bash scripts/tgw-provision.sh <nixos-host> <target-ip>
#   bash scripts/tgw-provision.sh tgw-test 192.168.1.50
#   bash scripts/tgw-provision.sh tgw-prod 192.168.1.10
#
# Optional — inject a Tailscale auth key so the host joins the Tailnet
# automatically on first boot (key must be reusable/ephemeral):
#   TS_AUTHKEY=tskey-auth-... bash scripts/tgw-provision.sh tgw-test 192.168.1.50
#
# What nixos-anywhere does:
#   1. SSH into the target (must accept root login — NixOS live ISO does by default)
#   2. kexec into a RAM-based NixOS installer (no USB swap needed)
#   3. Disko partitions and formats the disk per nix/hosts/<host>-disko.nix
#   4. NixOS installs from the flake config
#   5. Machine reboots into the new system
#
# After provisioning:
#   ssh db@<ip>          — log in as operator
#   systemctl status syncthing tgw-http  — confirm services
#   df -h /opt/TGW       — confirm data partition mounted

set -euo pipefail

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <nixos-host> <target-ip>" >&2
  echo "  nixos-host:  config name in flake (e.g. tgw-test, tgw-prod)" >&2
  echo "  target-ip:   LAN or Tailscale IP of the target machine" >&2
  exit 1
fi

NIXOS_HOST="$1"
TARGET_IP="$2"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> tgw-provision: $NIXOS_HOST → root@$TARGET_IP"
echo "    flake: path:$REPO"

# ---------------------------------------------------------------------------
# Optional Tailscale auth key injection
# ---------------------------------------------------------------------------
EXTRA_FILES_ARGS=()
if [[ -n "${TS_AUTHKEY:-}" ]]; then
  TS_TMPDIR="$(mktemp -d)"
  trap 'rm -rf "$TS_TMPDIR"' EXIT
  mkdir -p "$TS_TMPDIR/run/secrets"
  printf '%s' "$TS_AUTHKEY" > "$TS_TMPDIR/run/secrets/tailscale-key"
  chmod 600 "$TS_TMPDIR/run/secrets/tailscale-key"
  EXTRA_FILES_ARGS=(--extra-files "$TS_TMPDIR")
  echo "    Tailscale auth key will be injected"
fi

# ---------------------------------------------------------------------------
# Provision
# ---------------------------------------------------------------------------
nix run github:nix-community/nixos-anywhere -- \
  --flake "path:$REPO#$NIXOS_HOST" \
  "${EXTRA_FILES_ARGS[@]}" \
  "root@$TARGET_IP"
