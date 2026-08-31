#!/usr/bin/env bash
# tgw-prod-deploy v1 (2026-08-30) — narrow compare-and-swap release-install
# helper for item-track source-only releases. Runs as root (sudoers pins the
# identity). stdlib-only.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "tgw-prod-deploy: must run as root" >&2; exit 3; }
[ "$#" -eq 7 ] || { echo "usage: tgw-prod-deploy <archive> <generation> <commit> <tree> <archive-sha256> <expected-current> <operation-id>" >&2; exit 2; }
archive="$1"; gen="$2"; commit="$3"; tree="$4"; sha="$5"; expected="$6"; op="$7"
INSTALLER="/opt/TGW/.venvironments/tgw/bin/tgw-release-install"
case "$archive" in /home/db/*|/opt/TGW/incoming/*|/var/tmp/*) ;; *) echo "archive path not allowed: $archive" >&2; exit 3 ;; esac
[ -f "$archive" ] || { echo "archive missing: $archive" >&2; exit 3; }
echo "$sha  $archive" | sha256sum -c - >/dev/null || { echo "archive sha256 mismatch" >&2; exit 3; }
case "$gen" in item-workflow-*-*|fix-*-*|codex-*-*) ;; *) echo "generation not allowed: $gen" >&2; exit 3 ;; esac
echo "$commit" | grep -Eq "^[0-9a-f]{40}$" || { echo "commit not 40-hex" >&2; exit 3; }
echo "$tree" | grep -Eq "^[0-9a-f]{40}$" || { echo "tree not 40-hex" >&2; exit 3; }
current="$(readlink -f /opt/TGW/current 2>/dev/null || true)"
[ "$current" = "/opt/TGW/releases/$expected" ] || { echo "expected-current mismatch: current=$current expected=$expected" >&2; exit 3; }
"$INSTALLER" --root /opt/TGW install \
  --archive "$archive" --generation "$gen" --commit "$commit" --tree "$tree" \
  --archive-sha256 "$sha" --expected-current "$expected" --operation-id "$op"
"$INSTALLER" --root /opt/TGW verify "$gen"
# Restart the HTTP shell AND every queue worker. Restarting only http +
# ebay_sync left ebay_upload/ebay_draft/ebay_stage/ebay_publish running the
# PREVIOUS release's code in memory — the root cause of the live
# "provider photo upload succeeded but canonical projection conflicted"
# dead-letter (tgw202505111148158) that persisted across a source-only
# release. The glob restarts the whole queue-worker fleet atomically.
systemctl restart tgw-http.service 'tgw-worker@*.service'
sleep 2
systemctl is-active --quiet tgw-http.service 'tgw-worker@*.service' || { echo "service not active after restart" >&2; exit 1; }
[ -f "/opt/TGW/receipts/$op.json" ] || { echo "install receipt missing: /opt/TGW/receipts/$op.json" >&2; exit 1; }
echo "tgw-prod-deploy: current=$(readlink -f /opt/TGW/current) receipt=/opt/TGW/receipts/$op.json"
