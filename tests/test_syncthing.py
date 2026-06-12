"""Tests for tgw.apis.syncthing — PP-PYIPC-001."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch

import pytest

from tgw.apis.syncthing import (
    _parse_api_key,
    disk_events,
    folder_is_idle,
    folder_status,
    list_folders,
    scan_folder,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CONFIG_XML = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <configuration>
      <gui>
        <address>127.0.0.1:8384</address>
        <apikey>testkey123</apikey>
      </gui>
    </configuration>
""")


@pytest.fixture
def config_xml_file(tmp_path):
    p = tmp_path / "config.xml"
    p.write_text(CONFIG_XML, encoding="utf-8")
    return p


@pytest.fixture
def cfg(config_xml_file):
    return {
        "syncthing_config_path": config_xml_file,
        "syncthing_url": "http://127.0.0.1:8384",
    }


# ---------------------------------------------------------------------------
# _parse_api_key
# ---------------------------------------------------------------------------


def test_parse_api_key(config_xml_file):
    assert _parse_api_key(config_xml_file) == "testkey123"


def test_parse_api_key_missing_raises(tmp_path):
    p = tmp_path / "nokey.xml"
    p.write_text("<configuration><gui></gui></configuration>", encoding="utf-8")
    with pytest.raises(RuntimeError, match="No <apikey>"):
        _parse_api_key(p)


# ---------------------------------------------------------------------------
# folder_status
# ---------------------------------------------------------------------------


def test_folder_status_returns_dict(cfg):
    mock_client = MagicMock()
    mock_client.db.status.return_value = {"state": "idle", "needBytes": 0}
    with patch("tgw.apis.syncthing._get_client", return_value=mock_client):
        result = folder_status(cfg, "new-items")
    mock_client.db.status.assert_called_once_with("new-items")
    assert result["state"] == "idle"


# ---------------------------------------------------------------------------
# folder_is_idle
# ---------------------------------------------------------------------------


def test_folder_is_idle_true(cfg):
    mock_client = MagicMock()
    mock_client.db.status.return_value = {"state": "idle"}
    with patch("tgw.apis.syncthing._get_client", return_value=mock_client):
        assert folder_is_idle(cfg, "new-items") is True


def test_folder_is_idle_false_when_syncing(cfg):
    mock_client = MagicMock()
    mock_client.db.status.return_value = {"state": "syncing"}
    with patch("tgw.apis.syncthing._get_client", return_value=mock_client):
        assert folder_is_idle(cfg, "new-items") is False


# ---------------------------------------------------------------------------
# list_folders
# ---------------------------------------------------------------------------


def test_list_folders_returns_list(cfg):
    mock_client = MagicMock()
    mock_client.config.folders.return_value = [{"id": "new-items"}, {"id": "tgwdocs"}]
    with patch("tgw.apis.syncthing._get_client", return_value=mock_client):
        folders = list_folders(cfg)
    assert len(folders) == 2
    assert folders[0]["id"] == "new-items"


# ---------------------------------------------------------------------------
# scan_folder
# ---------------------------------------------------------------------------


def test_scan_folder_calls_db_scan(cfg):
    mock_client = MagicMock()
    with patch("tgw.apis.syncthing._get_client", return_value=mock_client):
        scan_folder(cfg, "new-items")
    mock_client.db.scan.assert_called_once_with(folder="new-items", sub=None)


def test_scan_folder_with_sub(cfg):
    mock_client = MagicMock()
    with patch("tgw.apis.syncthing._get_client", return_value=mock_client):
        scan_folder(cfg, "new-items", sub="photos/")
    mock_client.db.scan.assert_called_once_with(folder="new-items", sub="photos/")


# ---------------------------------------------------------------------------
# disk_events
# ---------------------------------------------------------------------------


class _Halt(Exception):
    """Test sentinel — stops the disk_events generator from outside."""


def test_disk_events_yields_events(cfg):
    fake_events = [
        {"id": 1, "type": "LocalIndexUpdated", "data": {}},
        {"id": 2, "type": "FolderScanProgress", "data": {}},
    ]
    mock_resp = MagicMock()
    mock_resp.json.return_value = fake_events

    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_resp
        raise _Halt

    with patch("tgw.apis.syncthing.requests.get", side_effect=mock_get):
        gen = disk_events(cfg, since=0, timeout=5)
        events = []
        try:
            events.append(next(gen))
            events.append(next(gen))
            next(gen)  # triggers second requests.get → _Halt
        except _Halt:
            pass

    assert len(events) == 2
    assert events[0]["type"] == "LocalIndexUpdated"
    assert events[1]["id"] == 2


def test_disk_events_resumes_on_timeout(cfg):
    from requests.exceptions import ReadTimeout

    call_count = 0
    fake_events = [{"id": 5, "type": "LocalIndexUpdated", "data": {}}]

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ReadTimeout
        if call_count == 2:
            resp = MagicMock()
            resp.json.return_value = fake_events
            return resp
        raise _Halt

    with patch("tgw.apis.syncthing.requests.get", side_effect=mock_get):
        gen = disk_events(cfg, since=0, timeout=5)
        events = []
        try:
            for e in gen:
                events.append(e)
        except _Halt:
            pass

    assert len(events) == 1
    assert events[0]["id"] == 5
