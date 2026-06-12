#!/bin/bash
# etc/interfaces/install.sh — unified TGW interface config installer.
#
# Deploys all operator interface configs from the repo to their system locations.
# Run as root from any directory:
#   sudo bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/install.sh
#
# What this installs (system-level, run as root):
#   MC VFS        — /opt/TGW/mc/ symlink → repo; system extfs + menu configs
#   keyd          — /etc/keyd/tgw-macroboard.conf
#   Shell         — /opt/TGW/bin/tgw.source + tgw-dev.source (backup + cutover)
#
# Qtile WM (user-level — run separately as your desktop user, NOT root):
#   bash etc/interfaces/qtile/install.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
INTERFACES="$REPO_ROOT/etc/interfaces"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (sudo bash $0)" >&2
    exit 1
fi

echo "=== TGW interface installer ==="
echo "Repo root: $REPO_ROOT"
echo

# ── MC VFS sentinel symlink ────────────────────────────────────────────────
echo "── Midnight Commander ────────────────────────────────────────────────"
MC_LINK=/opt/TGW/mc
MC_TARGET="$INTERFACES/mc"

if [[ -L "$MC_LINK" ]]; then
    echo "  $MC_LINK already a symlink → $(readlink "$MC_LINK")"
    if [[ "$(readlink -f "$MC_LINK")" == "$(readlink -f "$MC_TARGET")" ]]; then
        echo "  already points to repo. OK."
    else
        echo "  updating symlink..."
        ln -sfn "$MC_TARGET" "$MC_LINK"
        echo "  $MC_LINK → $MC_TARGET"
    fi
elif [[ -d "$MC_LINK" ]]; then
    echo "  $MC_LINK is a real directory (not yet migrated)."
    echo "  To migrate: mv $MC_LINK ${MC_LINK}.bak && ln -s $MC_TARGET $MC_LINK"
    echo "  Skipping symlink creation — run manually after backup."
else
    ln -s "$MC_TARGET" "$MC_LINK"
    echo "  created $MC_LINK → $MC_TARGET"
fi

echo "  Installing MC system files..."
bash "$INTERFACES/mc/install-system-mc.sh"

# ── keyd macroboard ────────────────────────────────────────────────────────
echo
echo "── keyd macroboard ───────────────────────────────────────────────────"
KEYD_CONF="$INTERFACES/keyd/tgw-macroboard.conf"
KEYD_DEST=/etc/keyd/tgw-macroboard.conf

install -m 644 -o root -g root "$KEYD_CONF" "$KEYD_DEST"
echo "  installed: $KEYD_DEST"

if systemctl is-active --quiet keyd 2>/dev/null; then
    echo "  reloading keyd..."
    systemctl reload keyd
    echo "  keyd reloaded."
else
    echo "  keyd not running — start with: sudo systemctl enable --now keyd"
fi

# ── Shell interface (tgw.source / tgw-dev.source) ──────────────────────────
# Operator-gated cutover: the version-controlled copies in etc/interfaces/shell/
# are deployed to /opt/TGW/bin/.  Existing live files are backed up first and
# only replaced when they actually differ (idempotent).
echo
echo "── Shell interface ───────────────────────────────────────────────────"
SHELL_SRC="$INTERFACES/shell"
SHELL_DEST=/opt/TGW/bin
TGW_OWNER="${TGW_OWNER:-tgw}"
DATE=$(date +%Y%m%d-%H%M%S)
mkdir -p "$SHELL_DEST"
for f in tgw.source tgw-dev.source; do
    src="$SHELL_SRC/$f"
    dest="$SHELL_DEST/$f"
    [[ -f "$src" ]] || { echo "  WARN: missing $src — skipping"; continue; }
    if [[ -f "$dest" ]] && cmp -s "$src" "$dest"; then
        echo "  $dest already current. OK."
        continue
    fi
    if [[ -f "$dest" ]]; then
        cp -p "$dest" "$dest.bak-$DATE"
        echo "  backed up: $dest.bak-$DATE"
    fi
    install -m 644 -o "$TGW_OWNER" -g "$TGW_OWNER" "$src" "$dest"
    echo "  installed: $dest"
done
echo "  (re-source in open shells: 'source $SHELL_DEST/tgw.source')"

echo
echo "=== Done. ==="
