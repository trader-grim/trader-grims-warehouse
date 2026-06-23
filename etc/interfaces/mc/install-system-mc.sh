#!/bin/bash
# install-system-mc.sh — install TGW VFS/menu integration system-wide.
# Canonical location: etc/interfaces/mc/install-system-mc.sh (inside TGW repo)
# Run as:  sudo bash /opt/TGW/mc/install-system-mc.sh
# (or:     sudo bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/mc/install-system-mc.sh)
set -euo pipefail

MC_ETC=/etc/mc
MC_EXTFS=/usr/lib/mc/extfs.d
# Resolve STAGE relative to this script so it works whether invoked from
# /opt/TGW/mc/ (symlink) or directly from the repo path.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAGE="$SCRIPT_DIR/system"
DATE=$(date +%Y%m%d-%H%M%S)

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must be run as root (sudo bash $0)" >&2
    exit 1
fi

echo "=== TGW MC system-wide install ==="
echo

# ── Pre-flight: validate ALL inputs before touching /etc/mc ──────────────────
# Fail fast so a missing/renamed source file can't leave a half-installed state
# (mc.ext.ini/mc.menu already overwritten but extfs scripts absent).
WANT_SHEBANG='#!/opt/TGW/.venvironments/tgw/bin/python3'
EXTFS_SCRIPTS=(tgwitem tgwcatalog tgwqueue tgwhealth tgwservices tgwlogs)
for required in "$STAGE/mc.ext.ini" "$STAGE/mc.menu"; do
    [[ -f "$required" ]] || { echo "ERROR: missing $required" >&2; exit 1; }
done
for f in "${EXTFS_SCRIPTS[@]}"; do
    src="$STAGE/extfs.d/$f"
    [[ -f "$src" ]] || { echo "ERROR: missing extfs script $src" >&2; exit 1; }
    got=$(head -1 "$src")
    if [[ "$got" != "$WANT_SHEBANG" ]]; then
        echo "ERROR: $src has wrong shebang: $got" >&2
        echo "  Expected: $WANT_SHEBANG" >&2
        exit 1
    fi
done

# ── Backup originals ────────────────────────────────────────────────────────
echo "Backing up originals..."
cp -v "$MC_ETC/mc.ext.ini" "$MC_ETC/mc.ext.ini.bak-$DATE"
cp -v "$MC_ETC/mc.menu"    "$MC_ETC/mc.menu.bak-$DATE"

# ── Install extension rules ──────────────────────────────────────────────────
echo
echo "Installing mc.ext.ini..."
install -m 644 -o root -g root "$STAGE/mc.ext.ini" "$MC_ETC/mc.ext.ini"

# ── Install user menu ────────────────────────────────────────────────────────
echo "Installing mc.menu..."
install -m 644 -o root -g root "$STAGE/mc.menu" "$MC_ETC/mc.menu"

# ── Install extfs scripts (validated in pre-flight above) ────────────────────
echo
echo "Installing extfs scripts..."
for f in "${EXTFS_SCRIPTS[@]}"; do
    install -m 755 -o root -g root "$STAGE/extfs.d/$f" "$MC_EXTFS/$f"
    echo "  installed: $MC_EXTFS/$f"
done

# ── Remove user-level overrides that would shadow the system files ───────────
TGW_USER=tgw
USER_MC="/home/$TGW_USER/.config/mc"

echo
echo "Cleaning up user-level overrides for $TGW_USER..."
for f in menu mc.menu mc.ext.ini; do
    target="$USER_MC/$f"
    if [[ -f "$target" ]]; then
        echo "  removing: $target"
        rm -f "$target"
    fi
done
# Remove empty user extfs.d (scripts are now in the system location)
if [[ -d "/home/$TGW_USER/.local/share/mc/extfs.d" ]]; then
    echo "  removing: /home/$TGW_USER/.local/share/mc/extfs.d/"
    rm -rf "/home/$TGW_USER/.local/share/mc/extfs.d"
fi

echo
echo "=== Done. ==="
echo
echo "TGW VFS/menu is now active system-wide."
echo "Start a new MC session to pick up the changes."
echo
echo "Sentinel files:  /opt/TGW/mc/"
echo "Status script:   /opt/TGW/mc/tgw-mc-status.py"
echo "Extfs scripts:   $MC_EXTFS/tgw{item,catalog,queue,health,services}"
echo "Backups:         $MC_ETC/mc.ext.ini.bak-$DATE"
echo "                 $MC_ETC/mc.menu.bak-$DATE"
