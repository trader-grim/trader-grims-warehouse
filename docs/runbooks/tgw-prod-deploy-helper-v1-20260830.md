# tgw-prod-deploy — narrow release-install helper (v1, 2026-08-30)

Draft artifact for review. Kills the "copy-paste bridge" on the deploy side:
any tgw-coders harness deploys a completed item release with one command, as
root, with compare-and-swap + full verification + service restart + receipt —
the operator's install gate becomes a notification ("I know it is changing").

## Install (operator, one time, on tgw-prod)

1. Copy to `/usr/local/sbin/tgw-prod-deploy` (root:root, 0755).
2. `/etc/sudoers.d/tgw-prod-deploy`:

   ```
   %tgw-coders ALL=(root) NOPASSWD: /usr/local/sbin/tgw-prod-deploy
   ```

   Names the script and the role group — never actor names.

## Script (`/usr/local/sbin/tgw-prod-deploy`)

```bash
#!/usr/bin/env bash
# stdlib-only; runs as root (sudoers pins the identity). Compare-and-swap
# release install for item-track source-only releases.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "tgw-prod-deploy: must run as root" >&2; exit 3; }
[ "$#" -eq 7 ] || { echo "usage: tgw-prod-deploy <archive> <generation> <commit> <tree> <archive-sha256> <expected-current> <operation-id>" >&2; exit 2; }
archive="$1"; gen="$2"; commit="$3"; tree="$4"; sha="$5"; expected="$6"; op="$7"
INSTALLER="/opt/TGW/.venvironments/tgw/bin/tgw-release-install"
case "$archive" in /home/db/*|/opt/TGW/incoming/*|/var/tmp/*) ;; *) echo "archive path not allowed: $archive" >&2; exit 3 ;; esac
[ -f "$archive" ] || { echo "archive missing: $archive" >&2; exit 3; }
echo "$sha  $archive" | sha256sum -c - >/dev/null || { echo "archive sha256 mismatch" >&2; exit 3; }
case "$gen" in item-workflow-*-*|fix-*-*|codex-*-*) ;; *) echo "generation not allowed: $gen" >&2; exit 3 ;; esac
case "$commit" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) echo "commit not 40-hex" >&2; exit 3 ;; esac
case "$tree" in [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; *) echo "tree not 40-hex" >&2; exit 3 ;; esac
current="$(readlink -f /opt/TGW/current 2>/dev/null || true)"
if [ "$current" != "/opt/TGW/releases/$expected" ]; then
  echo "expected-current mismatch: current=$current expected=$expected" >&2; exit 3
fi
"$INSTALLER" --root /opt/TGW install \
  --archive "$archive" --generation "$gen" --commit "$commit" --tree "$tree" \
  --archive-sha256 "$sha" --expected-current "$expected" --operation-id "$op"
"$INSTALLER" --root /opt/TGW verify "$gen"
# Restart the HTTP shell AND every queue worker (see the deploy-helper
# source note in docs/runbooks/tgw-prod-deploy.sh).
systemctl restart tgw-http.service 'tgw-worker@*.service'
sleep 2
systemctl is-active --quiet tgw-http.service 'tgw-worker@*.service' || { echo "service not active after restart" >&2; exit 1; }
mkdir -p /opt/TGW/receipts
receipt="/opt/TGW/receipts/$op.json"
cp -p "$receipt" "$receipt" 2>/dev/null || true
[ -f "/opt/TGW/receipts/$op.json" ] || { echo "install receipt missing: /opt/TGW/receipts/$op.json" >&2; exit 1; }
echo "tgw-prod-deploy: current=$(readlink -f /opt/TGW/current) receipt=/opt/TGW/receipts/$op.json"
```

## Usage (any tgw-coders harness with the archive on tgw-prod)

```bash
sudo /usr/local/sbin/tgw-prod-deploy \
  /home/db/item-workflow-14e7ec591-20260829.tar \
  item-workflow-14e7ec591-20260829 \
  14e7ec591c49f476afe0f54dbe283da7c3a03379 \
  e0728c43f8184e175d0e993476a8d03cc6da4906 \
  32cb6313d7aa0b25ac7dfcaf920060e677f4f940e881a0cc24e6aa5299d2aa22 \
  item-workflow-13507c886-20260829 \
  operator-item-workflow-14e7ec591-20260829
```

## Refusals (by design)

- Wrong identity, archive path, digest, generation/commit/tree format, or
  expected-current mismatch → exit 3, nothing installed (compare-and-swap held).
- Install/verify failure → exit 1, `current` unchanged, receipt visible.
- Service not active after restart → exit 1 (release selected but flagged).
- Rollback is NOT in this helper — rollback stays an explicit operator action
  (or a future `tgw-prod-deploy rollback` with the same validation shape).
