"""
tgw.apis.gdrive_sync — read the ItemData→GDrive sync status file.

Workers use this to decide whether local files are already mirrored to Drive
and therefore safe to reference by GDrive URL (PP-PHOTO-001).

Usage:
    from tgw.apis.gdrive_sync import sync_status, files_synced_by

    status = sync_status()          # raw dict
    if files_synced_by(mtime):      # True if Drive is at least as fresh as mtime
        pass_gdrive_url(...)
    else:
        fall_back_to_base64(...)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

_STATUS_FILE = Path("/opt/TGW/var/log/rclone-itemdata-sync-status.json")


def sync_status() -> Dict[str, Any]:
    """
    Return the current sync status dict.  Keys:
        state        — "running" | "idle" | "skipped"
        cycle        — int, cycle counter since service start
        started_at   — ISO-8601 str, when current/last cycle began
        completed_at — ISO-8601 str, when last cycle finished (None if running/skipped)
        pid          — int, sync service PID

    Returns {"state": "unknown"} if the status file is absent or unreadable.
    """
    try:
        return json.loads(_STATUS_FILE.read_text())
    except Exception:
        return {"state": "unknown"}


def last_completed_at() -> Optional[datetime]:
    """Return the UTC datetime of the last successful sync completion, or None."""
    completed = sync_status().get("completed_at")
    if not completed:
        return None
    try:
        return datetime.fromisoformat(completed).astimezone(timezone.utc)
    except ValueError:
        return None


def files_synced_by(mtime: float) -> bool:
    """
    Return True if the last completed sync finished AFTER `mtime` (a POSIX
    timestamp), meaning any file written before that time is guaranteed to
    be on Drive.

    Args:
        mtime: POSIX timestamp (e.g. os.path.getmtime(photo_path))
    """
    completed = last_completed_at()
    if completed is None:
        return False
    file_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return completed > file_dt
