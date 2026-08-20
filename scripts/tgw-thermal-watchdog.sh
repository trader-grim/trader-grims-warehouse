#!/run/current-system/sw/bin/bash
# tgw-thermal-watchdog — NVMe temperature monitor with staged throttle + shutdown.
#
# Stages:
#   NORMAL   < 75°C  — silent
#   WARM     75-79°C — journal only
#   HOT      80-83°C — wall warn, write status file
#   THROTTLE 84-86°C — kill grep/find, stop queue workers, wall warn
#   SHUTDOWN >= 87°C — clean poweroff
#
# Status file: /opt/TGW/var/run/thermal.status
#   Format: LEVEL|TEMP_C|EPOCH
#   Claude and Aider should check this before disk-intensive operations.

set -euo pipefail
export PATH="/run/current-system/sw/bin:/run/wrappers/bin:$PATH"

SMARTCTL="/nix/store/k7271ap9fibhc9crsr1fnrcn411d5hrb-smartmontools-7.5/bin/smartctl"
STATUS_FILE="/opt/TGW/var/run/thermal.status"
INTERVAL=30
DEVICE="/dev/nvme0"

log() { local p=$1; shift; echo "[tgw-thermal] $*" | systemd-cat -t tgw-thermal -p "$p"; echo "[tgw-thermal] $*" >&2; }

get_temp() {
    $SMARTCTL -A "$DEVICE" 2>/dev/null \
        | awk '/^Temperature:/ { print $2; exit }'
}

write_status() {
    local level=$1 temp=$2
    echo "${level}|${temp}|$(date +%s)" > "$STATUS_FILE"
    chmod 644 "$STATUS_FILE"
}

take_snapshot() {
    local reason=$1
    log notice "THERMAL: taking emergency snapshot (reason: ${reason})"
    /opt/tgw-releases/current/bin/tgw-snapshot >> /opt/TGW/var/log/tgw-snapshot.log 2>&1 \
        && log notice "THERMAL: snapshot complete" \
        || log err "THERMAL: snapshot failed -- check tgw-snapshot.log"
}

kill_disk_hogs() {
    local pids
    pids=$(pgrep -f "grep.*ItemData\|find.*ItemData\|grep.*ItemCatalog" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        echo "$pids" | xargs kill -9 2>/dev/null || true
        log warning "THERMAL: killed disk-hammering processes: $pids"
        wall $'\n⚠️  TGW THERMAL: Killed runaway grep/find (NVMe too hot). Check thermal.status.\n'
    fi
}

stop_workers() {
    systemctl stop 'tgw-worker@*.service' 2>/dev/null || true
    log warning "THERMAL: stopped all tgw-worker services"
}

prev_level="NORMAL"

mkdir -p "$(dirname "$STATUS_FILE")"
write_status "NORMAL" "0"

while true; do
    temp=$(get_temp)

    if [[ -z "$temp" ]]; then
        sleep "$INTERVAL"
        continue
    fi

    if   (( temp >= 87 )); then level="SHUTDOWN"
    elif (( temp >= 84 )); then level="THROTTLE"
    elif (( temp >= 80 )); then level="HOT"
    elif (( temp >= 75 )); then level="WARM"
    else                        level="NORMAL"
    fi

    write_status "$level" "$temp"

    case "$level" in
        WARM)
            [[ "$prev_level" != "WARM" ]] && \
                log notice "NVMe ${temp}C WARM (75+). Monitor closely."
            ;;
        HOT)
            if [[ "$prev_level" != "HOT" ]]; then
                log warning "NVMe ${temp}C HOT (80+). Slow down disk operations."
                take_snapshot "HOT-${temp}C"
                wall $'\nTGW THERMAL WARNING: NVMe '"${temp}"'C (HOT)\nSlow down -- avoid grep/find on ItemData.\nStatus: /opt/TGW/var/run/thermal.status\n'
            fi
            ;;
        THROTTLE)
            log err "NVMe ${temp}C THROTTLE (84+). Killing disk hogs, stopping workers."
            [[ "$prev_level" != "THROTTLE" ]] && take_snapshot "THROTTLE-${temp}C"
            kill_disk_hogs
            stop_workers
            wall $'\nTGW THERMAL THROTTLE: NVMe '"${temp}"'C -- workers STOPPED.\nClaude/Aider: STOP grep/find immediately. Check thermal.status.\n'
            ;;
        SHUTDOWN)
            log emerg "NVMe ${temp}C CRITICAL (87+). Clean shutdown."
            kill_disk_hogs
            stop_workers
            sync
            wall $'\nTGW THERMAL SHUTDOWN: NVMe '"${temp}"'C -- powering off.\n'
            sleep 3
            systemctl poweroff
            ;;
        NORMAL)
            if [[ "$prev_level" != "NORMAL" ]]; then
                log notice "NVMe ${temp}C back to NORMAL."
                wall $'\nTGW THERMAL: NVMe '"${temp}"'C -- temperature normal.\n'
            fi
            ;;
    esac

    prev_level="$level"
    sleep "$INTERVAL"
done
