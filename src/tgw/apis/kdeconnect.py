"""
tgw.apis.kdeconnect — KDE Connect CLI wrapper (PP-PYIPC-001).

All operations use kdeconnect-cli via subprocess. pydbus/D-Bus is the
ideal long-term approach but requires gi.repository (system Python);
subprocess calls are sufficient for TGW's current use patterns.

Usage:
    from tgw.apis.kdeconnect import list_devices, send_text, ping

    devices = list_devices(reachable_only=True)
    send_text('1aca783f36064322985e9de4536b831b', 'Hello from TGW')
    ping('1aca783f36064322985e9de4536b831b', msg='Worker done')
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

_CLI = 'kdeconnect-cli'

# Pattern for lines like: "- Galaxy Tab A9+ 5G: <id> (paired and reachable)"
_DEVICE_RE = re.compile(
    r'^\s*-\s+(?P<name>.+?):\s+(?P<id>[0-9a-f]{32})\s+\((?P<status>[^)]+)\)\s*$'
)


def _run(args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([_CLI, *args], capture_output=True, text=True, check=check)


def list_devices(reachable_only: bool = True) -> List[Dict[str, str]]:
    """Return list of KDE Connect devices.

    Each entry: {id, name, status, reachable}.
    """
    flag = '--list-available' if reachable_only else '--list-devices'
    result = _run([flag], check=False)
    devices: List[Dict[str, str]] = []
    for line in result.stdout.splitlines():
        m = _DEVICE_RE.match(line)
        if m:
            reachable = 'reachable' in m.group('status')
            devices.append({
                'id': m.group('id'),
                'name': m.group('name').strip(),
                'status': m.group('status').strip(),
                'reachable': str(reachable),
            })
    return devices


def get_device_id(name_or_id: str, reachable_only: bool = True) -> Optional[str]:
    """Resolve a device name or id to a canonical 32-char id.

    Returns None if not found.
    """
    if re.fullmatch(r'[0-9a-f]{32}', name_or_id):
        return name_or_id
    for dev in list_devices(reachable_only=reachable_only):
        if dev['name'].lower() == name_or_id.lower():
            return dev['id']
    return None


def ping(device_id: str, msg: str = '') -> bool:
    """Send a ping (with optional message) to a device. Returns True on success."""
    args = ['--ping', '--device', device_id]
    if msg:
        args = ['--ping-msg', msg, '--device', device_id]
    result = _run(args, check=False)
    return result.returncode == 0


def send_text(device_id: str, text: str) -> bool:
    """Share text to a device (appears as a share notification). Returns True on success."""
    result = _run(['--share-text', text, '--device', device_id], check=False)
    return result.returncode == 0


def send_file(device_id: str, path: Path) -> bool:
    """Send a file to a device. Returns True on success."""
    result = _run(['--share', str(path), '--device', device_id], check=False)
    return result.returncode == 0


def push_clipboard(device_id: str) -> bool:
    """Push the current desktop clipboard content to a device. Returns True on success.

    Note: sends whatever is currently in the X11 clipboard — set it first via
    subprocess xclip/xdotool if you need to push specific text.
    """
    result = _run(['--send-clipboard', '--device', device_id], check=False)
    return result.returncode == 0
