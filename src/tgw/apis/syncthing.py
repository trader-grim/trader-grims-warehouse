"""
tgw.apis.syncthing — Syncthing REST API wrapper (PP-PYIPC-001).

Uses pyncthing for status/control operations and a direct requests-based
long-polling loop for disk events (/rest/events/disk).

Usage:
    from tgw.apis.syncthing import folder_status, folder_is_idle, disk_events

    status = folder_status(cfg, 'new-items')
    if folder_is_idle(cfg, 'new-items'):
        ...

    for event in disk_events(cfg, timeout=30):
        print(event['type'], event['data'])
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import requests
from requests.exceptions import ReadTimeout

_DEFAULT_CONFIG_PATH = Path('/opt/TGW/.local/syncthing/config.xml')
_DEFAULT_URL = 'http://127.0.0.1:8384'


def _parse_api_key(config_path: Path) -> str:
    """Extract <apikey> from Syncthing config.xml."""
    tree = ET.parse(config_path)
    node = tree.find('.//gui/apikey')
    if node is None or not node.text:
        raise RuntimeError(f'No <apikey> element in {config_path}')
    return node.text.strip()


def _get_base_url(cfg: Dict[str, Any]) -> str:
    return str(cfg.get('syncthing_url', _DEFAULT_URL)).rstrip('/')


def _get_api_key(cfg: Dict[str, Any]) -> str:
    config_path = Path(cfg.get('syncthing_config_path', _DEFAULT_CONFIG_PATH))
    return _parse_api_key(config_path)


def _get_client(cfg: Dict[str, Any]):
    """Return a configured pyncthing.Syncthing client."""
    from pyncthing import Syncthing  # type: ignore[import-untyped]

    base_url = _get_base_url(cfg)
    client = Syncthing(host=base_url)
    client.set_api_key(_get_api_key(cfg))
    return client


# ---------------------------------------------------------------------------
# Status / control
# ---------------------------------------------------------------------------


def folder_status(cfg: Dict[str, Any], folder_id: str) -> Dict[str, Any]:
    """Return folder status dict from /rest/db/status.

    Key fields: state ('idle'/'scanning'/'syncing'), needBytes, needFiles,
    globalFiles, localFiles, errors.
    """
    client = _get_client(cfg)
    return client.db.status(folder_id)


def folder_is_idle(cfg: Dict[str, Any], folder_id: str) -> bool:
    """Return True if the given folder's state is 'idle'."""
    return folder_status(cfg, folder_id).get('state') == 'idle'


def list_folders(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return list of configured folder objects from /rest/config/folders."""
    client = _get_client(cfg)
    return client.config.folders()


def scan_folder(cfg: Dict[str, Any], folder_id: str, sub: Optional[str] = None) -> None:
    """Trigger a manual rescan of folder_id (optionally just a sub-path)."""
    client = _get_client(cfg)
    client.db.scan(folder=folder_id, sub=sub)


# ---------------------------------------------------------------------------
# Disk event streaming
# ---------------------------------------------------------------------------


def disk_events(
    cfg: Dict[str, Any],
    since: int = 0,
    timeout: int = 30,
) -> Generator[Dict[str, Any], None, None]:
    """Yield disk-related Syncthing events via long-polling /rest/events/disk.

    Runs indefinitely — intended for use in a dedicated watcher loop.
    Automatically resumes on timeout (no events); re-raises on connection
    errors so the caller can decide whether to retry or stop.

    Args:
        since: Start from this event ID. 0 = only new events from now.
        timeout: Long-poll timeout in seconds (Syncthing default ~60s).
    """
    base_url = _get_base_url(cfg)
    api_key = _get_api_key(cfg)
    headers = {'X-API-Key': api_key}
    last_id = since

    while True:
        try:
            resp = requests.get(
                f'{base_url}/rest/events/disk',
                headers=headers,
                params={'since': last_id, 'timeout': timeout},
                timeout=timeout + 5,
            )
            resp.raise_for_status()
            events = resp.json()
            for event in events:
                yield event
            if events:
                last_id = events[-1]['id']
        except ReadTimeout:
            continue
