"""
tgw.clipd — TGW clipboard daemon (PP-CLIP-001).

Dual-backend clipboard watcher:
  X11/XFixes (default, stable) — push events via python-xlib xfixes extension
  Wayland                      — wl-paste --watch subprocess per selection

Session-type autodetect at startup:
  $WAYLAND_DISPLAY set OR $XDG_SESSION_TYPE == 'wayland' → wayland backend
  else → x11 backend

Watches both PRIMARY and CLIPBOARD selections.
Feeds the tgw.clip SQLite store via record_clip().

Unix socket at ~/.local/share/tgw-clip/clipd.sock (newline-delimited JSON):
  → {"cmd": "ping"}
  ← {"ok": true, "pong": true}
  → {"cmd": "last-sku"}
  ← {"ok": true, "sku": "tgwXXX" | null}
  → {"cmd": "list", "limit": 20}
  ← {"ok": true, "rows": [...]}
  → {"cmd": "subscribe"}
  ← {"ok": true, "subscribed": true}
  ← {"event": "clip", "content": "...", "selection": "...", "is_sku": bool, "sku": str|null}
"""

from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.clip import last_sku, list_history, record_clip

log = logging.getLogger(__name__)

_DEFAULT_CLIP_DIR = Path.home() / '.local' / 'share' / 'tgw-clip'
SOCKET_NAME = 'clipd.sock'


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

def detect_backend() -> str:
    """Return 'x11', 'wayland', or 'both' based on the current session environment."""
    has_wayland = bool(os.environ.get('WAYLAND_DISPLAY'))
    has_x11 = bool(os.environ.get('DISPLAY'))
    if has_wayland and has_x11:
        return 'both'   # XWayland mixed session — watch both clipboards
    if has_wayland or os.environ.get('XDG_SESSION_TYPE', '').lower() == 'wayland':
        return 'wayland'
    return 'x11'


# ---------------------------------------------------------------------------
# Subscriber registry — thread-safe push to connected sockets
# ---------------------------------------------------------------------------

class _SubscriberRegistry:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: List[socket.socket] = []

    def add(self, sock: socket.socket) -> None:
        with self._lock:
            self._subs.append(sock)

    def remove(self, sock: socket.socket) -> None:
        with self._lock:
            try:
                self._subs.remove(sock)
            except ValueError:
                pass

    def push(self, event: Dict[str, Any]) -> None:
        """Broadcast event JSON to all subscribers; drop dead connections."""
        msg = (json.dumps(event) + '\n').encode()
        with self._lock:
            snapshot = list(self._subs)
        dead: List[socket.socket] = []
        for s in snapshot:
            try:
                s.sendall(msg)
            except OSError:
                dead.append(s)
        for s in dead:
            self.remove(s)

    def count(self) -> int:
        with self._lock:
            return len(self._subs)


# ---------------------------------------------------------------------------
# Core change handler — backend-agnostic
# ---------------------------------------------------------------------------

_last_content: Optional[str] = None
_last_content_lock = threading.Lock()


def process_change(
    content: str,
    selection: str,
    subscribers: _SubscriberRegistry,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Record a clipboard change and push an event to all subscribers.

    Deduplicates: skips if content is identical to the last recorded entry
    (prevents dual-backend double-writes when both X11 and Wayland fire for the
    same clipboard event in a mixed XWayland session).
    """
    global _last_content
    with _last_content_lock:
        if content == _last_content:
            return {'ok': True, 'skipped': True}
        _last_content = content
    result = record_clip(content, selection=selection, db_path=db_path)
    subscribers.push({
        'event': 'clip',
        'content': content[:200],
        'selection': selection,
        'is_sku': result.get('is_sku', False),
        'sku': result.get('sku'),
    })
    log.debug('clip: selection=%s is_sku=%s', selection, result.get('is_sku'))
    return result


# ---------------------------------------------------------------------------
# Socket command handler
# ---------------------------------------------------------------------------

def handle_command(cmd: Dict[str, Any], db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Dispatch a single socket command dict; return response dict."""
    action = cmd.get('cmd', '')
    if action == 'ping':
        return {'ok': True, 'pong': True}
    if action == 'last-sku':
        return {'ok': True, 'sku': last_sku(db_path=db_path)}
    if action == 'list':
        limit = int(cmd.get('limit', 20))
        return {'ok': True, 'rows': list_history(limit=limit, db_path=db_path)}
    if action == 'pick':
        return {'ok': True, 'action': 'launch_rofi'}
    return {'ok': False, 'error': f'unknown command: {action!r}'}

def launch_rofi_picker(db_path: Path) -> Optional[str]:
    """Launch rofi to pick from clipboard history. Returns selected content or None."""
    import sqlite3
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute('SELECT content FROM clip_history ORDER BY id DESC LIMIT 200')
        items = [row[0][:120] for row in cursor.fetchall()]
        conn.close()

        if not items:
            return None

        proc = subprocess.Popen(
            ['rofi', '-dmenu', '-p', 'Clip', '-i'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert proc.stdin is not None
        proc.stdin.write('\n'.join(items))
        proc.stdin.close()
        
        selected = proc.stdout.read().strip() if proc.stdout else None
        proc.wait()
        if not selected:
            return None

        # Look up full content (not truncated) from db
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            'SELECT content FROM clip_history WHERE content LIKE ? LIMIT 1',
            (f'{selected}%',)
        )
        row = cursor.fetchone()
        full_content = row[0] if row else selected
        conn.close()
        return full_content

    except Exception as e:
        log.warning('rofi picker failed: %s', e)
        return None


# ---------------------------------------------------------------------------
# Unix socket server
# ---------------------------------------------------------------------------

class _ClipRequestHandler(socketserver.StreamRequestHandler):

    def handle(self) -> None:
        db_path: Optional[Path] = self.server.db_path  # type: ignore[attr-defined]
        subs: _SubscriberRegistry = self.server.subscribers  # type: ignore[attr-defined]
        try:
            for raw in self.rfile:
                line = raw.strip()
                if not line:
                    continue
                try:
                    cmd = json.loads(line)
                except json.JSONDecodeError:
                    self._send({'ok': False, 'error': 'invalid JSON'})
                    continue
                if cmd.get('cmd') == 'subscribe':
                    subs.add(self.request)
                    self._send({'ok': True, 'subscribed': True})
                    try:
                        self.rfile.read()  # hold open until client disconnects
                    except OSError:
                        pass
                    finally:
                        subs.remove(self.request)
                    return
                self._send(handle_command(cmd, db_path=db_path))
        except OSError:
            pass

    def _send(self, obj: Dict[str, Any]) -> None:
        try:
            self.wfile.write((json.dumps(obj) + '\n').encode())
            self.wfile.flush()
        except OSError:
            pass


class ClipSocketServer(socketserver.ThreadingUnixStreamServer):
    """Threaded Unix socket server for tgw-clipd."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        socket_path: Path,
        db_path: Optional[Path],
        subscribers: _SubscriberRegistry,
    ) -> None:
        self.db_path = db_path
        self.subscribers = subscribers
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()
        super().__init__(str(socket_path), _ClipRequestHandler)

    def server_close(self) -> None:
        super().server_close()
        try:
            Path(self.server_address).unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# X11/XFixes backend
# ---------------------------------------------------------------------------

class X11Backend:
    """
    X11/XFixes clipboard watcher. Registers for XFixes SelectionOwner events
    on CLIPBOARD and PRIMARY via python-xlib (python3-xlib system package);
    reads content via xclip subprocess on each change.
    """

    def __init__(
        self,
        subscribers: _SubscriberRegistry,
        db_path: Optional[Path] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        self._subscribers = subscribers
        self._db_path = db_path
        self._stop = stop_event or threading.Event()

    def _open_display(self):
        from Xlib import display as xdisplay
        return xdisplay.Display()

    def _read_selection_content(self, selection: str) -> Optional[str]:
        """Read X11 clipboard content via xclip (reads X11 selection, not Wayland)."""
        sel_arg = 'primary' if selection == 'primary' else 'clipboard'
        try:
            r = subprocess.run(
                ['xclip', '-o', '-selection', sel_arg],
                capture_output=True, text=True, timeout=2.0,
            )
            if r.returncode == 0:
                return r.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return None

    def run(self) -> None:
        """Block until stop_event, processing XFixes SelectionOwner events."""
        import select as _select

        from Xlib import Xatom
        from Xlib.ext.xfixes import XFixesSetSelectionOwnerNotifyMask

        dpy = self._open_display()
        screen = dpy.screen()
        root = screen.root

        ext_info = dpy.query_extension('XFIXES')
        if ext_info is None:
            log.error('XFixes extension not available — X11 backend disabled')
            dpy.close()
            return
        first_event = ext_info.first_event

        dpy.xfixes_query_version()

        atom_clipboard = dpy.intern_atom('CLIPBOARD')
        atom_primary = Xatom.PRIMARY

        dpy.xfixes_select_selection_input(root, atom_clipboard, XFixesSetSelectionOwnerNotifyMask)
        dpy.xfixes_select_selection_input(root, atom_primary, XFixesSetSelectionOwnerNotifyMask)
        dpy.flush()

        log.info('X11/XFixes backend started (event_base=%d)', first_event)
        fd = dpy.fileno()

        while not self._stop.is_set():
            r, _, _ = _select.select([fd], [], [], 0.2)
            if not r:
                continue
            while dpy.pending_events():
                evt = dpy.next_event()
                # XFixes SelectionNotify: type = first_event + 0; sub_code 0 = SetOwner
                if getattr(evt, 'type', None) != first_event:
                    continue
                if getattr(evt, 'sub_code', -1) != 0:
                    continue
                sel_atom = getattr(evt, 'selection', None)
                if sel_atom == atom_primary:
                    selection = 'primary'
                elif sel_atom == atom_clipboard:
                    selection = 'clipboard'
                else:
                    continue
                content = self._read_selection_content(selection)
                if content is not None:
                    process_change(content, selection, self._subscribers, self._db_path)

        dpy.close()
        log.info('X11/XFixes backend stopped')


# ---------------------------------------------------------------------------
# Wayland backend
# ---------------------------------------------------------------------------

class WaylandBackend:
    """
    Wayland clipboard watcher. Spawns two wl-paste --watch cat subprocesses,
    one per selection (CLIPBOARD and PRIMARY). Reads stdout line-by-line;
    each non-empty line is treated as a new clipboard content snapshot.
    """

    def __init__(
        self,
        subscribers: _SubscriberRegistry,
        db_path: Optional[Path] = None,
        stop_event: Optional[threading.Event] = None,
        restart_delay: float = 1.0,
    ) -> None:
        self._subscribers = subscribers
        self._db_path = db_path
        self._stop = stop_event or threading.Event()
        self._restart_delay = restart_delay

    def _spawn(self, args: list, **kwargs) -> subprocess.Popen:
        return subprocess.Popen(args, **kwargs)

    def _run_watcher(self, selection: str) -> None:
        """Watch one selection in a loop; restart if the process exits."""
        args = ['wl-paste']
        if selection == 'primary':
            args.append('--primary')
        # printf '\0' after cat writes a null-byte sentinel after each event so we can
        # recover complete multi-line clipboard content from the concatenated stdout stream.
        # Reading line-by-line would split multi-paragraph clips at every newline.
        args += ['--watch', 'sh', '-c', r'cat; printf "\0"']

        while not self._stop.is_set():
            try:
                proc = self._spawn(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            except FileNotFoundError:
                log.error('wl-paste not found; Wayland backend unavailable')
                return

            try:
                assert proc.stdout is not None
                buf = b''
                while not self._stop.is_set():
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b'\x00' in buf:
                        raw, buf = buf.split(b'\x00', 1)
                        content = raw.decode('utf-8', errors='replace').rstrip('\n')
                        if content.strip():
                            process_change(content, selection, self._subscribers, self._db_path)
            except OSError:
                pass
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()

            if not self._stop.is_set():
                log.warning('wl-paste --watch exited; restarting in %.1fs', self._restart_delay)
                self._stop.wait(self._restart_delay)

    def run(self) -> None:
        """Start watchers for clipboard (and primary if X11 not also active); block until stop_event."""
        # In a mixed XWayland session ('both' backend), X11/XFixes already watches PRIMARY.
        # Only watch PRIMARY via wl-paste when there's no X11 backend running alongside.
        selections = ['clipboard']
        if not os.environ.get('DISPLAY'):
            selections.append('primary')
        threads = [
            threading.Thread(
                target=self._run_watcher,
                args=(sel,),
                daemon=True,
                name=f'wl-watch-{sel}',
            )
            for sel in selections
        ]
        for t in threads:
            t.start()
        self._stop.wait()
        for t in threads:
            t.join(timeout=3)
        log.info('Wayland backend stopped')


# ---------------------------------------------------------------------------
# Daemon orchestrator
# ---------------------------------------------------------------------------

class ClipDaemon:
    """Orchestrates the backend watcher and Unix socket server."""

    def __init__(
        self,
        backend: str = 'auto',
        clip_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self._clip_dir = Path(clip_dir) if clip_dir else _DEFAULT_CLIP_DIR
        self._db_path = db_path
        self._backend_name = backend if backend != 'auto' else detect_backend()
        self._stop_event = threading.Event()
        self._subscribers = _SubscriberRegistry()
        self._socket_server: Optional[ClipSocketServer] = None
        self._threads: List[threading.Thread] = []

    @property
    def socket_path(self) -> Path:
        return self._clip_dir / SOCKET_NAME

    def start(self) -> None:
        """Start socket server and backend watcher threads (non-blocking)."""
        server = ClipSocketServer(self.socket_path, self._db_path, self._subscribers)
        self._socket_server = server
        st = threading.Thread(target=server.serve_forever, daemon=True, name='clipd-socket')
        st.start()
        self._threads.append(st)

        backends: List[Any] = []
        if self._backend_name in ('wayland', 'both'):
            backends.append(('wayland', WaylandBackend(self._subscribers, self._db_path, self._stop_event)))
        if self._backend_name in ('x11', 'both'):
            backends.append(('x11', X11Backend(self._subscribers, self._db_path, self._stop_event)))

        for name, bk in backends:
            bt = threading.Thread(target=bk.run, daemon=True, name=f'clipd-{name}')
            bt.start()
            self._threads.append(bt)
        log.info('ClipDaemon started: backend=%s socket=%s', self._backend_name, self.socket_path)

    def stop(self) -> None:
        """Signal all threads to stop and wait."""
        self._stop_event.set()
        if self._socket_server:
            self._socket_server.shutdown()
            self._socket_server.server_close()
        for t in self._threads:
            t.join(timeout=5)
        log.info('ClipDaemon stopped')


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    import signal

    parser = argparse.ArgumentParser(prog='tgw-clipd')
    # Bare invocation (no subcommand) is the real-world case — the systemd
    # unit calls `tgw-clipd` with no args — so --backend/--verbose must exist
    # on the top-level parser too, not just the 'daemon' subparser, or the
    # no-subcommand path below crashes on args.verbose/args.backend every run.
    parser.add_argument('--backend', choices=['auto', 'x11', 'wayland'], default='auto')
    parser.add_argument('--verbose', '-v', action='store_true')
    subparsers = parser.add_subparsers(dest='command', required=False)

    # Daemon mode (default)
    daemon_parser = subparsers.add_parser('daemon')
    daemon_parser.add_argument('--backend', choices=['auto', 'x11', 'wayland'], default='auto')
    daemon_parser.add_argument('--verbose', '-v', action='store_true')

    # Pick command
    pick_parser = subparsers.add_parser('pick')
    pick_parser.add_argument('--db-path', type=Path, default=_DEFAULT_CLIP_DIR / 'clip.db')

    args = parser.parse_args()

    if not args.command or args.command == 'daemon':
        # Original daemon behavior
        logging.basicConfig(
            level=logging.DEBUG if args.verbose else logging.INFO,
            format='%(asctime)s %(name)s %(levelname)s %(message)s',
        )
        daemon = ClipDaemon(backend=args.backend)
        daemon.start()
        stop = threading.Event()
        def _sig(_signum, _frame) -> None:
            log.info('signal received — stopping')
            stop.set()
        signal.signal(signal.SIGINT, _sig)
        signal.signal(signal.SIGTERM, _sig)
        stop.wait()
        daemon.stop()
        return 0

    if args.command == 'pick':
        import sys
        selected = launch_rofi_picker(args.db_path)
        if not selected:
            return 1

        # Set clipboard using best available method
        try:
            if os.environ.get('WAYLAND_DISPLAY'):
                subprocess.run(['wl-copy'], input=selected, text=True, check=True)
            else:
                subprocess.run(
                    ['xclip', '-selection', 'clipboard'],
                    input=selected, text=True, check=True
                )
        except Exception as e:
            print(f"Failed to set clipboard: {e}", file=sys.stderr)
            return 1

        print(selected)
        return 0

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    daemon = ClipDaemon(backend=args.backend)
    daemon.start()

    stop = threading.Event()

    def _sig(_signum, _frame) -> None:
        log.info('signal received — stopping')
        stop.set()

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    stop.wait()
    daemon.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
