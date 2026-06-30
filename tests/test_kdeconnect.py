"""Tests for tgw.apis.kdeconnect — PP-PYIPC-001."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from tgw.apis.kdeconnect import (
    get_device_id,
    list_devices,
    ping,
    push_clipboard,
    send_file,
    send_text,
)

_LIST_OUTPUT = """\
- Galaxy Tab A9+ 5G: 1aca783f36064322985e9de4536b831b (paired and reachable)
- KFMAWI: 1b9473a97d6b4668b2273eb6b69bd85f (paired and reachable)
- A53: 6390dda00c7e4a249368c23437b9d5dc (paired and reachable)
- ShopTab: 8159f46e12d849ab9ae59f410f7cfc5d (paired)
4 devices found
"""


def _mock_run(stdout="", returncode=0):
    r = MagicMock()
    r.stdout = stdout
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


def test_list_devices_parses_reachable(tmp_path):
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(_LIST_OUTPUT)):
        devices = list_devices(reachable_only=True)
    assert len(devices) == 4
    reachable = [d for d in devices if d["reachable"] == "True"]
    assert len(reachable) == 3


def test_list_devices_names_and_ids(tmp_path):
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(_LIST_OUTPUT)):
        devices = list_devices()
    assert devices[0]["name"] == "Galaxy Tab A9+ 5G"
    assert devices[0]["id"] == "1aca783f36064322985e9de4536b831b"
    assert devices[3]["name"] == "ShopTab"
    assert devices[3]["reachable"] == "False"


def test_list_devices_empty_output():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run("0 devices found\n")):
        devices = list_devices()
    assert devices == []


# ---------------------------------------------------------------------------
# get_device_id
# ---------------------------------------------------------------------------


def test_get_device_id_passes_through_valid_id():
    result = get_device_id("1aca783f36064322985e9de4536b831b")
    assert result == "1aca783f36064322985e9de4536b831b"


def test_get_device_id_resolves_name():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(_LIST_OUTPUT)):
        result = get_device_id("Galaxy Tab A9+ 5G")
    assert result == "1aca783f36064322985e9de4536b831b"


def test_get_device_id_not_found():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(_LIST_OUTPUT)):
        result = get_device_id("NonExistentDevice")
    assert result is None


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


def test_ping_returns_true_on_success():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(returncode=0)):
        assert ping("1aca783f36064322985e9de4536b831b") is True


def test_ping_with_msg_uses_ping_msg_flag():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run()) as mock_run:
        ping("abc123", msg="hello")
    args = mock_run.call_args[0][0]
    assert "--ping-msg" in args
    assert "hello" in args


def test_ping_returns_false_on_failure():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(returncode=1)):
        assert ping("abc") is False


# ---------------------------------------------------------------------------
# send_text
# ---------------------------------------------------------------------------


def test_send_text_returns_true_on_success():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(returncode=0)):
        assert send_text("abc", "hello") is True


def test_send_text_passes_text():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run()) as mock_run:
        send_text("abc", "my message")
    args = mock_run.call_args[0][0]
    assert "--share-text" in args
    assert "my message" in args


# ---------------------------------------------------------------------------
# send_file
# ---------------------------------------------------------------------------


def test_send_file_passes_path():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run()) as mock_run:
        send_file("abc", Path("/tmp/test.jpg"))
    args = mock_run.call_args[0][0]
    assert "--share" in args
    assert "/tmp/test.jpg" in args


def test_send_file_returns_false_on_failure():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(returncode=2)):
        assert send_file("abc", Path("/tmp/f")) is False


# ---------------------------------------------------------------------------
# push_clipboard
# ---------------------------------------------------------------------------


def test_push_clipboard_sends_clipboard_flag():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run()) as mock_run:
        push_clipboard("abc")
    args = mock_run.call_args[0][0]
    assert "--send-clipboard" in args
    assert "--device" in args


def test_push_clipboard_returns_true_on_success():
    with patch("tgw.apis.kdeconnect._run", return_value=_mock_run(returncode=0)):
        assert push_clipboard("abc") is True
