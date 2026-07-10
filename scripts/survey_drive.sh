#!/usr/bin/env bash
# survey_drive.sh — cheap, read-only catalog of one drive (PP-DRIVE-INDEX-001
# Phase 0.4, todo #1140).
#
# Purpose: scope what's on a drive BEFORE committing to a full recoll index
# or any file operations. Read-only mount, no writes to the drive, no
# hashing/dedup fingerprints yet (that's Phase 1.2, a separate deliberate
# step once a drive looks worth the time) — just identity + file listing +
# type/size breakdown, so Dave can prioritize which drives are worth
# connecting and processing first.
#
# Usage:
#   sudo bash scripts/survey_drive.sh /dev/sdX1 [output-dir]
#   (output-dir defaults to /opt/TGW/data/drive-index/manifests)

set -euo pipefail

DEVICE="${1:?Usage: survey_drive.sh /dev/sdX1 [output-dir]}"
OUTDIR="${2:-/opt/TGW/data/drive-index}"
MANIFESTS="$OUTDIR/manifests"
REPORTS="$OUTDIR/reports"

if [ ! -b "$DEVICE" ]; then
    echo "ERROR: $DEVICE is not a block device" >&2
    exit 1
fi

mkdir -p "$MANIFESTS" "$REPORTS"

LABEL="$(lsblk -no LABEL "$DEVICE" 2>/dev/null || true)"
SERIAL="$(lsblk -no SERIAL "$DEVICE" 2>/dev/null || true)"
DRIVE_ID="${LABEL:-${SERIAL:-$(basename "$DEVICE")}}"
DRIVE_ID="$(echo "$DRIVE_ID" | tr -c 'a-zA-Z0-9_-' '_')"

echo "==> surveying $DEVICE as '$DRIVE_ID'"

# Parent disk (for smartctl — a partition device usually can't be queried
# directly, smartctl wants the whole disk).
PARENT_DISK="/dev/$(lsblk -no PKNAME "$DEVICE" 2>/dev/null || basename "$DEVICE" | sed 's/[0-9]*$//')"

MOUNT="$(mktemp -d /tmp/drive-survey.XXXXXX)"
cleanup() {
    umount "$MOUNT" 2>/dev/null || true
    rmdir "$MOUNT" 2>/dev/null || true
}
trap cleanup EXIT

echo "  mounting read-only at $MOUNT"
mount -o ro "$DEVICE" "$MOUNT"

echo "  drive identity (smartctl)"
smartctl -i "$PARENT_DISK" > "$MANIFESTS/${DRIVE_ID}-smart.txt" 2>&1 || \
    echo "smartctl unavailable/failed for $PARENT_DISK" > "$MANIFESTS/${DRIVE_ID}-smart.txt"

echo "  file listing (this can take a while on large drives)"
find "$MOUNT" -xdev -type f -printf '%T@ %s %p\n' 2>/dev/null | sort -n \
    > "$MANIFESTS/${DRIVE_ID}-files.txt"

FILE_COUNT=$(wc -l < "$MANIFESTS/${DRIVE_ID}-files.txt")
TOTAL_BYTES=$(awk '{sum+=$2} END{print sum+0}' "$MANIFESTS/${DRIVE_ID}-files.txt")

echo "  category breakdown (by extension)"
find "$MOUNT" -xdev -type f 2>/dev/null | sed 's/.*\.//' | tr 'A-Z' 'a-z' \
    | sort | uniq -c | sort -rn > "$MANIFESTS/${DRIVE_ID}-types.txt"

echo "  top-level disk usage"
du -sh "$MOUNT"/*/ 2>/dev/null | sort -rh > "$MANIFESTS/${DRIVE_ID}-du.txt" || true

# One-page summary report — the actual deliverable Dave reads.
REPORT="$REPORTS/${DRIVE_ID}-summary.txt"
{
    echo "=== Drive survey: $DRIVE_ID ($DEVICE) ==="
    echo "Surveyed: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Label: ${LABEL:-none}  Serial: ${SERIAL:-unknown}"
    echo "Files: $FILE_COUNT   Total size: $(numfmt --to=iec "$TOTAL_BYTES" 2>/dev/null || echo "$TOTAL_BYTES bytes")"
    echo
    echo "--- Top 15 file types by count ---"
    head -15 "$MANIFESTS/${DRIVE_ID}-types.txt"
    echo
    echo "--- Top-level directories by size ---"
    cat "$MANIFESTS/${DRIVE_ID}-du.txt"
    echo
    echo "Full manifest: $MANIFESTS/${DRIVE_ID}-files.txt"
} > "$REPORT"

echo "==> done. Report: $REPORT"
cat "$REPORT"
