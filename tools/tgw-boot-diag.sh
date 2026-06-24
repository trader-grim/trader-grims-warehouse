#!/bin/bash
# Run from inside MX after login (as root or with sudo).
# Saves boot diagnostics to /opt/TGW/var/log/boot-diag.txt
# Readable from April ISO at /media/db/TGW/var/log/boot-diag.txt

OUT=/opt/TGW/var/log/boot-diag.txt
mkdir -p "$(dirname "$OUT")"
exec > "$OUT" 2>&1

echo "=== TGW BOOT DIAGNOSTICS $(date) ==="
echo

echo "=== /proc/cmdline (kernel boot params) ==="
cat /proc/cmdline
echo

echo "=== /proc/mounts (root mount options) ==="
grep -E "^/dev| / " /proc/mounts
echo

echo "=== fstab ==="
cat /etc/fstab
echo

echo "=== systemd-remount-fs status ==="
systemctl status systemd-remount-fs.service --no-pager -l
echo

echo "=== local-fs.target status ==="
systemctl status local-fs.target --no-pager -l
echo

echo "=== opt-TGW.mount status ==="
systemctl status opt-TGW.mount --no-pager -l
echo

echo "=== dmesg: ext4 / remount / errors ==="
dmesg | grep -iE "ext4|remount|read.?only|error|fail" | head -60
echo

echo "=== journal: boot errors/warnings ==="
journalctl -b -p warning --no-pager | head -80
echo

echo "=== failed units ==="
systemctl --failed --no-pager
echo

echo "=== tgw health (if /opt/TGW mounted) ==="
if mountpoint -q /opt/TGW; then
    sudo -u tgw /opt/TGW/.venvironments/tgw/bin/tgw health 2>&1 || echo "tgw health failed"
else
    echo "/opt/TGW not mounted — run 'mount -a' first"
fi

echo
echo "=== DONE ==="
