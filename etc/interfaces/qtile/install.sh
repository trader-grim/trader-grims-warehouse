#!/bin/bash
# etc/interfaces/qtile/install.sh — TGW Qtile window manager installer.
#
# Run as your DESKTOP USER (not root) from any directory:
#   bash /opt/TGW/current/etc/interfaces/qtile/install.sh
#
# What this does:
#   1. Checks that system packages (qtile, xclip, dmenu) are installed; prints
#      install command if any are missing (requires you to run it separately).
#   2. Creates ~/.config/qtile/ and symlinks config.py + tgw_widgets.py from repo.
#   3. Copies tgw-http API key to ~/.config/tgw/api-key (needed by bar widgets).
#   4. Creates ~/.config/qtile/autostart.sh stub.
#
# After install:
#   Log out → select "Qtile" at the login screen (SDDM / LightDM session list)
#   First-run keybindings: Super+Enter=terminal, Super+D=launcher, F12=scratchpad,
#     Super+T=TGW mode, Super+1-5=workspaces, Super+W=close window.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
QTILE_SRC="$REPO_ROOT/etc/interfaces/qtile"
QTILE_CFG="$HOME/.config/qtile"
TGW_CFG="$HOME/.config/tgw"
SECRETS="/opt/TGW/secrets"
SYSPY=/usr/bin/python3

echo "=== TGW Qtile installer ==="
echo "Repo:   $REPO_ROOT"
echo "Config: $QTILE_CFG"
echo "User:   $(whoami)"
echo

# ── 1. System package check (no sudo — just advise if missing) ─────────────
echo "── System packages ───────────────────────────────────────────────────"
# Check by binary, not package name — on Debian dmenu ships in suckless-tools.
MISSING_BINS=()
for bin in qtile xclip dmenu; do
    command -v "$bin" &>/dev/null || MISSING_BINS+=("$bin")
done

if [[ ${#MISSING_BINS[@]} -gt 0 ]]; then
    echo "  Missing binaries: ${MISSING_BINS[*]}"
    echo "  On Debian/MX Linux, dmenu is in the suckless-tools package:"
    echo "    sudo apt-get install -y qtile xclip suckless-tools"
    exit 1
fi
echo "  qtile, xclip, dmenu: found in PATH."

# libqtile must be importable by the system Python (not a venv).
if "$SYSPY" -c "import libqtile" 2>/dev/null; then
    echo "  libqtile: importable via $SYSPY. OK."
else
    echo "  libqtile not importable via $SYSPY. Run:"
    echo "    sudo apt-get install -y python3-qtile"
    echo "  Then re-run this script."
    exit 1
fi
echo

# ── 2. Qtile config directory (no sudo needed) ─────────────────────────────
echo "── Qtile config (~/.config/qtile/) ──────────────────────────────────"
mkdir -p "$QTILE_CFG"

for f in config.py tgw_widgets.py; do
    TARGET="$QTILE_CFG/$f"
    SOURCE="$QTILE_SRC/$f"
    if [[ -L "$TARGET" && "$(readlink -f "$TARGET")" == "$(readlink -f "$SOURCE")" ]]; then
        echo "  $f already symlinked from repo."
    elif [[ -f "$TARGET" && ! -L "$TARGET" ]]; then
        echo "  $f: existing non-symlink found; backing up to ${f}.bak"
        mv "$TARGET" "${TARGET}.bak"
        ln -s "$SOURCE" "$TARGET"
        echo "  $f symlinked from repo."
    else
        ln -sf "$SOURCE" "$TARGET"
        echo "  $f symlinked from repo."
    fi
done

# ── 3. TGW API key (no sudo needed — reads from secrets if accessible) ─────
echo
echo "── TGW API key (~/.config/tgw/api-key) ──────────────────────────────"
mkdir -p "$TGW_CFG"
KEY_JSON="$SECRETS/tgw-api-key.json"

if [[ -r "$KEY_JSON" ]]; then
    "$SYSPY" -c "
import json
with open('$KEY_JSON') as f:
    print(json.load(f).get('api_key', ''), end='')
" > "$TGW_CFG/api-key"
    chmod 600 "$TGW_CFG/api-key"
    echo "  API key written to $TGW_CFG/api-key"
else
    echo "  WARNING: $KEY_JSON not readable by $(whoami)."
    echo "  Bar queue widget will show Q:? until the key is available."
    echo "  Fix option A: sudo chmod g+r $KEY_JSON && sudo chgrp $(whoami) $KEY_JSON"
    echo "  Fix option B: copy the key manually —"
    echo "    sudo cat $KEY_JSON | python3 -c \"import sys,json; print(json.load(sys.stdin)['api_key'])\" > $TGW_CFG/api-key"
    echo "    chmod 600 $TGW_CFG/api-key"
fi

# ── 4. autostart.sh stub ───────────────────────────────────────────────────
echo
echo "── autostart.sh ──────────────────────────────────────────────────────"
AUTOSTART="$QTILE_CFG/autostart.sh"
if [[ -f "$AUTOSTART" ]]; then
    echo "  $AUTOSTART already exists — not overwriting."
else
    cat > "$AUTOSTART" <<'AUTOSTART_EOF'
#!/bin/bash
# Qtile autostart — runs once on WM startup.
# Add compositor, notification daemon, etc. here.

# Compositor (picom) — optional; reduces screen tearing
# picom --daemon 2>/dev/null &

# Notification daemon — uncomment one:
# dunst &
# /usr/lib/x86_64-linux-gnu/libexec/polkit-kde-authentication-agent-1 2>/dev/null &

# Wallpaper (pick one):
# feh --bg-scale ~/.config/qtile/wallpaper.jpg &
# nitrogen --restore &

true
AUTOSTART_EOF
    chmod +x "$AUTOSTART"
    echo "  Created $AUTOSTART stub."
fi

# ── 5. Verify config syntax ────────────────────────────────────────────────
echo
echo "── Verify ────────────────────────────────────────────────────────────"
if "$SYSPY" -c "
import ast, sys
with open('$QTILE_CFG/config.py') as f:
    ast.parse(f.read())
print('  config.py: syntax OK')
"; then
    :
else
    echo "  ERROR: config.py has a syntax error — check the file."
    exit 1
fi

echo
echo "=== Done. ==="
echo
echo "Next steps:"
echo "  1. Log out and select 'Qtile' at the SDDM/LightDM login screen."
echo "  2. Super+Enter = terminal | Super+D = launcher | Super+W = close window"
echo "  3. Super+T = TGW mode (bar shows [ TGW ]); Escape exits TGW mode."
echo "  4. F12 = scratchpad terminal toggle."
echo "  5. Super+Ctrl+R = reload config (after editing files in repo)."
echo
echo "Config files live in repo; changes take effect on Super+Ctrl+R:"
echo "  $QTILE_CFG/config.py -> $QTILE_SRC/config.py"
echo "  $QTILE_CFG/tgw_widgets.py -> $QTILE_SRC/tgw_widgets.py"
