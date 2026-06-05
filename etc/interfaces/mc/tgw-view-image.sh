#!/bin/bash
# tgw-view-image — render image/video with chafa for MC's %view{ascii} viewer.
#
# MC's viewer captures stdout and renders text + ANSI colors — it does NOT
# render sixel or kitty protocols.  We force --format=symbols so chafa outputs
# Unicode half-block art with ANSI truecolor regardless of terminal capabilities.
#
# Size detection: stdout is a pipe here so chafa can't ioctl it for size.
# Probe order: MC env vars → stty via /dev/tty → tput → 80x24 default.
#
# TERM/COLORTERM: MC may not export these into the viewer subprocess.
# Force them so chafa uses full ANSI truecolor output.

export TERM="${TERM:-xterm-256color}"
export COLORTERM="${COLORTERM:-truecolor}"

COLS=0
ROWS=0

# MC sets COLUMNS/LINES in the viewer environment
if [[ -n "${COLUMNS:-}" && "${COLUMNS:-0}" -gt 10 ]]; then
    COLS=$COLUMNS
    ROWS=${LINES:-24}
elif read -r ROWS COLS < <(stty size </dev/tty 2>/dev/null); then
    :
elif COLS=$(tput cols 2>/dev/null) && ROWS=$(tput lines 2>/dev/null); then
    :
fi

[[ $COLS -lt 10 ]] && COLS=80
[[ $ROWS -lt 6  ]] && ROWS=24
ROWS=$(( ROWS - 4 ))

exec chafa --format=symbols --colors=full --size="${COLS}x${ROWS}" "$@"
