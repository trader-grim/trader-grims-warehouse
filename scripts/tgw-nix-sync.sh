#!/usr/bin/env bash
# tgw-nix-sync — populate ~/tgw-flake/ from the git repo.
#
# Run after any commit that touches flake.nix, flake.lock, or nix/.
# Syncthing then distributes ~/tgw-flake/ to all paired hosts.
#
# Only the files NixOS needs to run nixos-rebuild are copied:
#   flake.nix, flake.lock, nix/
#
# Usage:
#   bash scripts/tgw-nix-sync.sh           # sync and report
#   bash scripts/tgw-nix-sync.sh --check   # dry-run, show what would change

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${TGW_NIX_FLAKE_DIR:-$HOME/tgw-flake}"
DRY_RUN=false

if [[ "${1:-}" == "--check" ]]; then
  DRY_RUN=true
fi

RSYNC_OPTS=(-a --delete --exclude=".git")
if $DRY_RUN; then
  RSYNC_OPTS+=(--dry-run --itemize-changes)
  echo "==> dry-run: changes that would be applied to $DEST"
else
  mkdir -p "$DEST"
fi

rsync "${RSYNC_OPTS[@]}" \
  "$REPO/flake.nix" \
  "$REPO/flake.lock" \
  "$DEST/"

rsync "${RSYNC_OPTS[@]}" \
  "$REPO/nix/" \
  "$DEST/nix/"

if ! $DRY_RUN; then
  FILE_COUNT=$(find "$DEST" -type f | wc -l)
  echo "==> tgw-nix-sync: $DEST updated ($FILE_COUNT files, $(date -Iseconds))"
  echo "    Syncthing will distribute to paired hosts automatically."
fi
