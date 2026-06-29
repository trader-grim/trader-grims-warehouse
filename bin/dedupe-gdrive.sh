#!/bin/bash

# Configuration
LOG_DIR="/opt/TGW/var/log/dedupe"
TPS_LIMIT=2

# Help Function
show_help() {
    cat << EOF
Usage: $(basename "$0") [--basedir <dir>] <folder1> [folder2 ...]

Deduplicate specified folders on Google Drive using rclone.

Options:
  --basedir <dir>  Base directory on GDrive (e.g., 'dedupe'). If not provided,
                   dedupe will run on folders at the root.
  -h, --help       Show this help message.

Example:
  $(basename "$0") --basedir dedupe history ItemArchive
EOF
}

# Parse Arguments
BASE_DIR=""
FOLDERS=()

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --basedir) BASE_DIR="$2"; shift ;;
        -h|--help) show_help; exit 0 ;;
        *) FOLDERS+=("$1") ;;
    esac
    shift
done

# Validate Input
if [ ${#FOLDERS[@]} -eq 0 ]; then
    echo "Error: No folders specified."
    show_help
    exit 1
fi

mkdir -p "$LOG_DIR"

# Execution
for folder in "${FOLDERS[@]}"; do
    REMOTE_PATH="dbukove:${BASE_DIR:+${BASE_DIR}/}${folder}"
    
    echo "--------------------------------------------------------"
    echo "Deduping: $REMOTE_PATH"
    echo "--------------------------------------------------------"
    
    rclone dedupe --by-hash --dedupe-mode first "$REMOTE_PATH" \
      --tpslimit "$TPS_LIMIT" \
      --log-file "$LOG_DIR/dedupe-${BASE_DIR//\//-}-${folder}.log"
      
    if [ $? -eq 0 ]; then
        echo "Successfully deduped $folder"
    else
        echo "Error deduping $folder. Check logs: $LOG_DIR/dedupe-${BASE_DIR//\//-}-${folder}.log"
    fi
done
