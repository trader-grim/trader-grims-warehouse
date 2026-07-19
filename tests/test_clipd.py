"""Tests for tgw.clipd — PP-CLIP-001 daemon (offline; no X11 or Wayland required)."""

from __future__ import annotations

import io
import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import tgw.clipd as clipd
from tgw.clip import list_history, record_clip

# ---------------------------------------------------------------------------
# detect_backend
# ---------------------------------------------------------------------------

def test_detect_backend_default_is_x11(monkeypatch):
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.delenv('XDG_SESSION_TYPE', raising=False)
    assert clipd.detect_backend() == 'x11'


def test_detect_backend_wayland_display(monkeypatch):
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    monkeypatch.delenv('DISPLAY', raising=False)
    assert clipd.detect_backend() == 'wayland'


def test_detect_backend_both_when_xwayland(monkeypatch):
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-1')
    monkeypatch.setenv('DISPLAY', ':0')
    assert clipd.detect_backend() == 'both'


def test_detect_backend_xdg_session_type(monkeypatch):
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
    assert clipd.detect_backend() == 'wayland'


def test_detect_backend_xdg_session_type_case_insensitive(monkeypatch):
    monkeypatch.delenv('WAYLAND_DISPLAY', raising=False)
    monkeypatch.setenv('XDG_SESSION_TYPE', 'WAYLAND')
    assert clipd.detect_backend() == 'wayland'


# ---------------------------------------------------------------------------
# _SubscriberRegistry
# ---------------------------------------------------------------------------

def test_subscriber_registry_push_delivers_event():
    reg = clipd._SubscriberRegistry()
    a, b = socket.socketpair()
    try:
        reg.add(a)
        assert reg.count() == 1
        reg.push({'event': 'clip', 'is_sku': True, 'sku': 'tgw202601011200000'})
        data = b.recv(256)
        msg = json.loads(data.decode().strip())
        assert msg['event'] == 'clip'
        assert msg['is_sku'] is True
        assert msg['sku'] == 'tgw202601011200000'
    finally:
        a.close()
        b.close()


def test_subscriber_registry_removes_dead_socket_on_push():
    reg = clipd._SubscriberRegistry()
    a, b = socket.socketpair()
    reg.add(a)
    a.close()
    b.close()
    reg.push({'event': 'test'})  # must not raise
    assert reg.count() == 0


def test_subscriber_registry_add_remove():
    reg = clipd._SubscriberRegistry()
    a, b = socket.socketpair()
    try:
        reg.add(a)
        assert reg.count() == 1
        reg.remove(a)
        assert reg.count() == 0
    finally:
        a.close()
        b.close()


def test_subscriber_registry_remove_nonexistent_is_noop():
    reg = clipd._SubscriberRegistry()
    a, b = socket.socketpair()
    try:
        reg.remove(a)  # never added — must not raise
        assert reg.count() == 0
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# process_change
# ---------------------------------------------------------------------------

def test_process_change_records_sku(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    result = clipd.process_change('tgw202601011200000', 'clipboard', reg, db_path=db)
    assert result['ok'] is True
    assert result['is_sku'] is True
    assert result['sku'] == 'tgw202601011200000'


def test_process_change_records_nonsku(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    result = clipd.process_change('some random text', 'primary', reg, db_path=db)
    assert result['ok'] is True
    assert result['is_sku'] is False


def test_process_change_pushes_event_to_subscriber(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    a, b = socket.socketpair()
    try:
        reg.add(a)
        clipd.process_change('tgw202601011200000', 'clipboard', reg, db_path=db)
        data = b.recv(512)
        evt = json.loads(data.decode().strip())
        assert evt['event'] == 'clip'
        assert evt['is_sku'] is True
        assert evt['sku'] == 'tgw202601011200000'
        assert evt['selection'] == 'clipboard'
    finally:
        a.close()
        b.close()


def test_process_change_records_selection(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    a, b = socket.socketpair()
    try:
        reg.add(a)
        clipd.process_change('hello', 'primary', reg, db_path=db)
        evt = json.loads(b.recv(512).decode().strip())
        assert evt['selection'] == 'primary'
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------------
# process_change — sensitive-content exclusion (todo #1565/PP-CLIP-001)
# ---------------------------------------------------------------------------

def test_process_change_skips_password_hint(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    result = clipd.process_change('hunter2', 'clipboard', reg, db_path=db, password_hint=True)
    assert result == {'ok': True, 'skipped': True, 'reason': 'password_hint'}
    assert list_history(db_path=db) == []


def test_process_change_password_hint_still_updates_dedup(tmp_path):
    """A password-hinted copy is not persisted, but dedup tracking still
    advances — a real subsequent non-sensitive copy is not itself treated as
    a duplicate of the (unpersisted) password content."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    clipd.process_change('hunter2', 'clipboard', reg, db_path=db, password_hint=True)
    result = clipd.process_change('normal text', 'clipboard', reg, db_path=db)
    assert result['ok'] is True
    assert result.get('skipped') is not True
    rows = list_history(db_path=db)
    assert len(rows) == 1
    assert rows[0]['content'] == 'normal text'


def test_process_change_no_password_hint_persists_normally(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    result = clipd.process_change('ordinary clip text', 'clipboard', reg, db_path=db,
                                   password_hint=False)
    assert result['ok'] is True
    assert result.get('skipped') is not True
    rows = list_history(db_path=db)
    assert len(rows) == 1
    assert rows[0]['content'] == 'ordinary clip text'


def test_process_change_skips_secret_shaped_content(tmp_path):
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    secret = 'ghp_' + 'aB3xQ9zT1kLmN7pR5sV8wY0cD2fH4jK6' * 1  # ghp_-prefixed token shape
    result = clipd.process_change(secret, 'clipboard', reg, db_path=db)
    assert result == {'ok': True, 'skipped': True, 'reason': 'secret_pattern'}
    assert list_history(db_path=db) == []


# ---------------------------------------------------------------------------
# handle_command
# ---------------------------------------------------------------------------

def test_handle_command_ping(tmp_path):
    result = clipd.handle_command({'cmd': 'ping'}, db_path=tmp_path / 'h.db')
    assert result == {'ok': True, 'pong': True}


def test_handle_command_last_sku_empty(tmp_path):
    db = tmp_path / 'h.db'
    result = clipd.handle_command({'cmd': 'last-sku'}, db_path=db)
    assert result['ok'] is True
    assert result['sku'] is None


def test_handle_command_last_sku_after_record(tmp_path):
    db = tmp_path / 'h.db'
    record_clip('tgw202601011200000', db_path=db)
    result = clipd.handle_command({'cmd': 'last-sku'}, db_path=db)
    assert result['ok'] is True
    assert result['sku'] == 'tgw202601011200000'


def test_handle_command_list(tmp_path):
    db = tmp_path / 'h.db'
    record_clip('hello', db_path=db)
    record_clip('tgw202601011200000', db_path=db)
    result = clipd.handle_command({'cmd': 'list', 'limit': 10}, db_path=db)
    assert result['ok'] is True
    assert len(result['rows']) == 2


def test_handle_command_list_default_limit(tmp_path):
    db = tmp_path / 'h.db'
    result = clipd.handle_command({'cmd': 'list'}, db_path=db)
    assert result['ok'] is True
    assert result['rows'] == []


def test_handle_command_unknown(tmp_path):
    result = clipd.handle_command({'cmd': 'bogus'}, db_path=tmp_path / 'h.db')
    assert result['ok'] is False
    assert 'unknown command' in result['error']


# ---------------------------------------------------------------------------
# ClipSocketServer — Unix socket roundtrip
# ---------------------------------------------------------------------------

@pytest.fixture
def running_server(tmp_path):
    sock_path = tmp_path / 'test.sock'
    db_path = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    server = clipd.ClipSocketServer(sock_path, db_path, reg)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server, sock_path, db_path, reg
    server.shutdown()
    server.server_close()
    t.join(timeout=2)


def _sock_send_recv(sock_path: Path, cmd: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(sock_path))
        s.sendall((json.dumps(cmd) + '\n').encode())
        data = b''
        while not data.endswith(b'\n'):
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    return json.loads(data.decode().strip())


def test_socket_ping(running_server):
    _, sock_path, _, _ = running_server
    assert _sock_send_recv(sock_path, {'cmd': 'ping'}) == {'ok': True, 'pong': True}


def test_socket_last_sku(running_server):
    _, sock_path, db_path, _ = running_server
    record_clip('tgw202601011200000', db_path=db_path)
    result = _sock_send_recv(sock_path, {'cmd': 'last-sku'})
    assert result['ok'] is True
    assert result['sku'] == 'tgw202601011200000'


def test_socket_invalid_json_returns_error(running_server):
    _, sock_path, _, _ = running_server
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(sock_path))
        s.sendall(b'not valid json\n')
        data = s.recv(256)
    resp = json.loads(data.decode().strip())
    assert resp['ok'] is False
    assert 'invalid JSON' in resp['error']


def test_socket_cleans_up_socket_file_on_server_close(tmp_path):
    sock_path = tmp_path / 'cleanup.sock'
    db_path = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    server = clipd.ClipSocketServer(sock_path, db_path, reg)
    assert sock_path.exists()
    server.server_close()
    assert not sock_path.exists()


def test_socket_subscribe_receives_push(running_server):
    """Subscribe client receives process_change events via the socket."""
    _, sock_path, db_path, reg = running_server

    received = []
    done = threading.Event()

    def subscriber_thread():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(sock_path))
            s.sendall(b'{"cmd": "subscribe"}\n')
            # Drain the subscribe ack
            ack = b''
            while not ack.endswith(b'\n'):
                ack += s.recv(256)
            # Wait for one event (2 second timeout)
            s.settimeout(2.0)
            try:
                event_data = b''
                while not event_data.endswith(b'\n'):
                    chunk = s.recv(256)
                    if not chunk:
                        break
                    event_data += chunk
                received.append(json.loads(event_data.decode().strip()))
            except Exception:
                pass
            done.set()

    t = threading.Thread(target=subscriber_thread, daemon=True)
    t.start()

    # Wait for subscriber to register (poll the registry)
    for _ in range(20):
        if reg.count() > 0:
            break
        time.sleep(0.05)

    clipd.process_change('tgw202601011200000', 'clipboard', reg, db_path=db_path)

    done.wait(timeout=3)
    t.join(timeout=2)

    assert len(received) == 1
    assert received[0]['event'] == 'clip'
    assert received[0]['is_sku'] is True
    assert received[0]['sku'] == 'tgw202601011200000'


# ---------------------------------------------------------------------------
# WaylandBackend
# ---------------------------------------------------------------------------

def _make_proc(events: list) -> MagicMock:
    """Create a mock Popen for _run_watcher tests.

    Each entry in *events* is a clipboard content string; _run_watcher reads
    raw bytes and splits on null-byte sentinels (wl-paste --watch … printf "\\0").
    An empty BytesIO (events=[]) signals immediate EOF so the watcher exits.
    """
    proc = MagicMock()
    data = b"".join(e.encode() + b"\x00" for e in events)
    proc.stdout = io.BytesIO(data)
    return proc


def test_wayland_backend_processes_clipboard_line(tmp_path):
    """_run_watcher calls process_change for each non-empty stdout line."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    stop = threading.Event()
    backend = clipd.WaylandBackend(reg, db_path=db, stop_event=stop, restart_delay=0)

    proc = _make_proc(['tgw202601011200000\n'])
    changes = []

    def fake_process_change(content, selection, subscribers, db_path=None, password_hint=False):
        changes.append((content, selection))
        stop.set()  # prevent restart loop
        return {'ok': True, 'is_sku': True, 'sku': content}

    with patch.object(backend, '_spawn', return_value=proc):
        with patch.object(backend, '_has_password_hint', return_value=False):
            with patch.object(clipd, 'process_change', side_effect=fake_process_change):
                backend._run_watcher('clipboard')

    assert changes == [('tgw202601011200000', 'clipboard')]


def test_wayland_backend_uses_primary_flag(tmp_path):
    """_run_watcher passes --primary in argv for the primary selection."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    stop = threading.Event()
    backend = clipd.WaylandBackend(reg, db_path=db, stop_event=stop, restart_delay=0)

    spawned_args: list = []

    def fake_spawn(args, **kw):
        spawned_args.extend(args)
        stop.set()
        return _make_proc([])

    with patch.object(backend, '_spawn', side_effect=fake_spawn):
        backend._run_watcher('primary')

    assert spawned_args[0] == 'wl-paste'
    assert '--primary' in spawned_args
    assert '--watch' in spawned_args


def test_wayland_backend_clipboard_has_no_primary_flag(tmp_path):
    """_run_watcher does not pass --primary for the clipboard selection."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    stop = threading.Event()
    backend = clipd.WaylandBackend(reg, db_path=db, stop_event=stop, restart_delay=0)

    spawned_args: list = []

    def fake_spawn(args, **kw):
        spawned_args.extend(args)
        stop.set()
        return _make_proc([])

    with patch.object(backend, '_spawn', side_effect=fake_spawn):
        backend._run_watcher('clipboard')

    assert '--primary' not in spawned_args


def test_wayland_backend_skips_empty_lines(tmp_path):
    """_run_watcher does not call process_change for blank/newline-only lines."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    stop = threading.Event()
    backend = clipd.WaylandBackend(reg, db_path=db, stop_event=stop, restart_delay=0)

    call_count = [0]

    def fake_spawn(args, **kw):
        call_count[0] += 1
        if call_count[0] >= 2:
            stop.set()
        return _make_proc(['\n', '\n'])

    with patch.object(backend, '_spawn', side_effect=fake_spawn):
        backend._run_watcher('clipboard')

    # No records should exist in the DB
    assert list_history(db_path=db) == []


def test_wayland_backend_handles_missing_executable(tmp_path):
    """_run_watcher returns without raising if wl-paste is not found."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    stop = threading.Event()
    backend = clipd.WaylandBackend(reg, db_path=db, stop_event=stop, restart_delay=0)

    with patch.object(backend, '_spawn', side_effect=FileNotFoundError('wl-paste')):
        backend._run_watcher('clipboard')  # must return cleanly


# ---------------------------------------------------------------------------
# WaylandBackend._has_password_hint (todo #1565/PP-CLIP-001)
# ---------------------------------------------------------------------------

def test_wayland_has_password_hint_true_when_offered():
    reg = clipd._SubscriberRegistry()
    backend = clipd.WaylandBackend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'text/plain\nx-kde-passwordManagerHint\n'

    with patch('tgw.clipd.subprocess.run', return_value=result) as mock_run:
        assert backend._has_password_hint('clipboard') is True

    args = mock_run.call_args[0][0]
    assert args == ['wl-paste', '--list-types']


def test_wayland_has_password_hint_false_when_absent():
    reg = clipd._SubscriberRegistry()
    backend = clipd.WaylandBackend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'text/plain\nUTF8_STRING\n'

    with patch('tgw.clipd.subprocess.run', return_value=result):
        assert backend._has_password_hint('clipboard') is False


def test_wayland_has_password_hint_uses_primary_flag():
    reg = clipd._SubscriberRegistry()
    backend = clipd.WaylandBackend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'x-kde-passwordManagerHint\n'

    with patch('tgw.clipd.subprocess.run', return_value=result) as mock_run:
        assert backend._has_password_hint('primary') is True

    args = mock_run.call_args[0][0]
    assert args == ['wl-paste', '--list-types', '--primary']


def test_wayland_has_password_hint_false_on_missing_executable():
    reg = clipd._SubscriberRegistry()
    backend = clipd.WaylandBackend(reg)

    with patch('tgw.clipd.subprocess.run', side_effect=FileNotFoundError('wl-paste')):
        assert backend._has_password_hint('clipboard') is False


def test_wayland_run_watcher_skips_persisting_password_hinted_content(tmp_path):
    """End-to-end: a password-hinted copy reaches _run_watcher and is not
    persisted, while a normal copy in the same stream still is."""
    db = tmp_path / 'h.db'
    reg = clipd._SubscriberRegistry()
    stop = threading.Event()
    backend = clipd.WaylandBackend(reg, db_path=db, stop_event=stop, restart_delay=0)

    proc = _make_proc(['hunter2\n', 'normal text\n'])

    hint_result = MagicMock()
    hint_result.returncode = 0
    hint_result.stdout = 'x-kde-passwordManagerHint\n'

    call_count = [0]

    def fake_run(args, **kw):
        call_count[0] += 1
        if call_count[0] == 2:  # after 'normal text' triggers stop
            stop.set()
        return hint_result if call_count[0] == 1 else MagicMock(returncode=0, stdout='')

    with patch.object(backend, '_spawn', return_value=proc):
        with patch('tgw.clipd.subprocess.run', side_effect=fake_run):
            backend._run_watcher('clipboard')

    rows = list_history(db_path=db)
    assert len(rows) == 1
    assert rows[0]['content'] == 'normal text'


# ---------------------------------------------------------------------------
# X11Backend._has_password_hint (todo #1565/PP-CLIP-001)
# ---------------------------------------------------------------------------

def test_x11_has_password_hint_true_when_offered():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'TARGETS\nx-kde-passwordManagerHint\nUTF8_STRING\n'

    with patch('tgw.clipd.subprocess.run', return_value=result) as mock_run:
        assert backend._has_password_hint('clipboard') is True

    args = mock_run.call_args[0][0]
    assert 'xclip' in args
    assert 'TARGETS' in args
    assert 'clipboard' in args


def test_x11_has_password_hint_false_when_absent():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'TARGETS\nUTF8_STRING\n'

    with patch('tgw.clipd.subprocess.run', return_value=result):
        assert backend._has_password_hint('clipboard') is False


def test_x11_has_password_hint_false_on_missing_executable():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    with patch('tgw.clipd.subprocess.run', side_effect=FileNotFoundError('xclip')):
        assert backend._has_password_hint('clipboard') is False


# ---------------------------------------------------------------------------
# X11Backend._read_selection_content
# ---------------------------------------------------------------------------

def test_x11_read_selection_clipboard():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'tgw202601011200000'

    with patch('tgw.clipd.subprocess.run', return_value=result) as mock_run:
        content = backend._read_selection_content('clipboard')

    assert content == 'tgw202601011200000'
    args = mock_run.call_args[0][0]
    assert 'xclip' in args
    assert 'clipboard' in args


def test_x11_read_selection_primary():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    result = MagicMock()
    result.returncode = 0
    result.stdout = 'highlighted text'

    with patch('tgw.clipd.subprocess.run', return_value=result) as mock_run:
        content = backend._read_selection_content('primary')

    assert content == 'highlighted text'
    args = mock_run.call_args[0][0]
    assert 'primary' in args


def test_x11_read_selection_returns_none_on_file_not_found():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    with patch('tgw.clipd.subprocess.run', side_effect=FileNotFoundError('xclip')):
        assert backend._read_selection_content('clipboard') is None


def test_x11_read_selection_returns_none_on_nonzero_exit():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    result = MagicMock()
    result.returncode = 1
    result.stdout = ''

    with patch('tgw.clipd.subprocess.run', return_value=result):
        assert backend._read_selection_content('clipboard') is None


def test_x11_read_selection_returns_none_on_timeout():
    reg = clipd._SubscriberRegistry()
    backend = clipd.X11Backend(reg)

    with patch('tgw.clipd.subprocess.run', side_effect=subprocess.TimeoutExpired(['xclip'], 2.0)):
        assert backend._read_selection_content('clipboard') is None


# ---------------------------------------------------------------------------
# launch_rofi_picker (todo #1292/#1293 — queried nonexistent 'clips' table
# and double cursor.fetchone() call; regression coverage against the real
# clip_history schema)
# ---------------------------------------------------------------------------

def _fake_rofi_process(select_value):
    """Build a fake Popen result that 'selects' select_value from rofi's stdin feed."""
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stdout.read.return_value = select_value + '\n'
    proc.wait.return_value = 0
    return proc


def test_launch_rofi_picker_returns_full_content_not_truncated(tmp_path):
    db_path = tmp_path / 'history.db'
    long_content = 'A' * 500 + ' END-OF-CONTENT-MARKER'
    record_clip('short one', db_path=db_path)
    record_clip(long_content, db_path=db_path)  # most recent -> first in ORDER BY id DESC

    truncated_selection = long_content[:120]
    with patch('tgw.clipd.subprocess.Popen', return_value=_fake_rofi_process(truncated_selection)):
        result = clipd.launch_rofi_picker(db_path)

    assert result == long_content


def test_launch_rofi_picker_falls_back_to_raw_selection_on_no_match(tmp_path):
    db_path = tmp_path / 'history.db'
    record_clip('something else entirely', db_path=db_path)

    with patch('tgw.clipd.subprocess.Popen', return_value=_fake_rofi_process('NO-MATCH-XYZ')):
        result = clipd.launch_rofi_picker(db_path)

    assert result == 'NO-MATCH-XYZ'


def test_launch_rofi_picker_queries_clip_history_table(tmp_path):
    db_path = tmp_path / 'history.db'
    record_clip('a clip', db_path=db_path)

    # No mocking of Popen internals needed beyond stdout/stdin — this just
    # proves the SELECT against clip_history (not the nonexistent 'clips'
    # table) succeeds instead of being swallowed by the except-and-return-None.
    with patch('tgw.clipd.subprocess.Popen', return_value=_fake_rofi_process('a clip')):
        result = clipd.launch_rofi_picker(db_path)

    assert result == 'a clip'
