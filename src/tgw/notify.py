"""
tgw.notify — Notification interface for the TGW platform.

Callers never know or care how notifications are delivered.
Backends are configured in tgw-api-config.json under 'notifications'.

Usage:
    from tgw.notify import notify
    notify('eBay upload complete', '12 items listed successfully')
    notify('Worker failed', 'itemdata-scrub crashed', level='error')
    notify('Low stock', 'Location FF0779 has 2 items remaining', level='warning')

Backends (configured, not hardcoded):
    desktop   — notify-send (Linux desktop notifications)
    log       — always active, writes to tgw.notify logger
    file      — appends to a notification log file
    webhook   — HTTP POST to a configured URL (future: Slack, ntfy, etc.)

Config example (tgw-api-config.json):
    "notifications": {
        "enabled": true,
        "backends": ["desktop", "log", "file"],
        "min_level": "info",
        "file": "/opt/TGW/var/log/notifications.jsonl",
        "webhook_url": null,
        "desktop_timeout": 5000
    }
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger('tgw.notify')

# ---------------------------------------------------------------------------
# Levels
# ---------------------------------------------------------------------------

LEVELS = {'debug': 0, 'info': 1, 'warning': 2, 'error': 3, 'critical': 4}

_URGENCY = {
    'debug':    'low',
    'info':     'normal',
    'warning':  'normal',
    'error':    'critical',
    'critical': 'critical',
}

_NOTIFY_ICON = {
    'debug':    'dialog-information',
    'info':     'dialog-information',
    'warning':  'dialog-warning',
    'error':    'dialog-error',
    'critical': 'dialog-error',
}


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------

def _backend_log(title: str, message: str, level: str,
                 cfg: Dict[str, Any]) -> None:
    """Always-on backend — writes to the tgw.notify logger."""
    log_fn = getattr(log, level, log.info)
    log_fn('%s: %s', title, message)


def _backend_file(title: str, message: str, level: str,
                  cfg: Dict[str, Any]) -> None:
    """Append structured notification to a JSONL file."""
    path = Path(cfg.get('file', '/opt/TGW/var/log/notifications.jsonl'))
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        'ts':      time.strftime('%Y-%m-%dT%H:%M:%S'),
        'level':   level,
        'title':   title,
        'message': message,
    }
    with path.open('a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')


def _backend_desktop(title: str, message: str, level: str,
                     cfg: Dict[str, Any]) -> None:
    """
    Send a desktop notification via notify-send.
    Silently skips if notify-send is not available or DISPLAY/WAYLAND is not set.
    """
    # Check display is available
    if not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')
            or os.environ.get('DBUS_SESSION_BUS_ADDRESS')):
        return
    try:
        timeout = str(cfg.get('desktop_timeout', 5000))
        urgency = _URGENCY.get(level, 'normal')
        icon    = _NOTIFY_ICON.get(level, 'dialog-information')
        subprocess.run(
            ['notify-send',
             '--urgency', urgency,
             '--icon', icon,
             '--expire-time', timeout,
             f'TGW: {title}',
             message],
            timeout=3,
            capture_output=True,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    except Exception as e:
        log.debug('desktop notify failed: %s', e)


def _backend_webhook(title: str, message: str, level: str,
                     cfg: Dict[str, Any]) -> None:
    """
    POST a notification to a webhook URL.
    Supports generic webhooks; extend for Slack/ntfy/etc.
    """
    url = cfg.get('webhook_url')
    if not url:
        return
    try:
        import urllib.request
        payload = json.dumps({
            'title':   title,
            'message': message,
            'level':   level,
            'ts':      time.strftime('%Y-%m-%dT%H:%M:%S'),
        }).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception as e:
        log.debug('webhook notify failed: %s', e)


def _backend_smtp(title: str, message: str, level: str,
                  cfg: Dict[str, Any]) -> None:
    """
    Send a notification by email via stdlib SMTP (PP-EMAIL-001).

    Credential-free foundation: inert until the operator populates smtp_* keys in
    the 'notifications' config block (host + an app password). Fails soft so a
    missing/misconfigured block is a no-op; keep 'smtp' out of the default
    backends so headless workers don't attempt mail unless explicitly enabled.
    """
    host = cfg.get('smtp_host')
    if not host:
        return
    from_addr = cfg.get('smtp_from') or cfg.get('smtp_username') or ''
    to_addr   = cfg.get('smtp_to') or from_addr
    if not to_addr:
        return
    try:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg['Subject'] = f'TGW [{level}]: {title}'
        msg['From']    = from_addr or 'tgw@localhost'
        msg['To']      = to_addr
        msg.set_content(message or title)

        port    = int(cfg.get('smtp_port', 587))
        use_tls = cfg.get('smtp_use_tls', True)
        timeout = cfg.get('smtp_timeout', 5)
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if use_tls:
                smtp.starttls()
            user = cfg.get('smtp_username')
            pw   = cfg.get('smtp_password')
            if user and pw:
                smtp.login(user, pw)
            smtp.send_message(msg)
    except Exception as e:
        log.debug('smtp notify failed: %s', e)


_BACKENDS = {
    'log':     _backend_log,
    'file':    _backend_file,
    'desktop': _backend_desktop,
    'webhook': _backend_webhook,
    'smtp':    _backend_smtp,
    'email':   _backend_smtp,  # alias
}


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

class Notifier:
    """
    Central notification dispatcher.

    Instantiate once per process (or use the module-level `notify` function).
    """

    def __init__(self, cfg: Optional[Dict[str, Any]] = None) -> None:
        self._cfg: Dict[str, Any] = cfg or {}
        self._enabled:   bool      = self._cfg.get('enabled', True)
        self._min_level: int       = LEVELS.get(
            self._cfg.get('min_level', 'info').lower(), 1
        )
        self._backends: List[str] = self._cfg.get('backends', ['log', 'file'])

    @classmethod
    def from_api_config(cls, config_path: Path) -> 'Notifier':
        """Build a Notifier from the main tgw-api-config.json."""
        try:
            raw = json.loads(config_path.read_text(encoding='utf-8'))
            return cls(raw.get('notifications', {}))
        except Exception as e:
            log.warning('Could not load notify config from %s: %s', config_path, e)
            return cls()

    def send(self, title: str, message: str = '',
             level: str = 'info') -> None:
        """
        Send a notification through all configured backends.

        Args:
            title:   Short summary (shown prominently in desktop/webhook)
            message: Detail text
            level:   'debug' | 'info' | 'warning' | 'error' | 'critical'
        """
        if not self._enabled:
            return
        level = level.lower()
        if LEVELS.get(level, 1) < self._min_level:
            return
        for backend_name in self._backends:
            fn = _BACKENDS.get(backend_name)
            if fn is None:
                log.debug('unknown notification backend: %s', backend_name)
                continue
            try:
                fn(title, message, level, self._cfg)
            except Exception as e:
                log.debug('backend %s failed: %s', backend_name, e)

    def __call__(self, title: str, message: str = '',
                 level: str = 'info') -> None:
        """Allow notifier instance to be called directly."""
        self.send(title, message, level)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Default notifier — uses log + file backends, no config needed
# Call configure() once at startup to attach your real config.
_default: Notifier = Notifier({'backends': ['log', 'file'], 'enabled': True})


def configure(cfg: Dict[str, Any]) -> None:
    """Replace the default notifier with one built from config dict."""
    global _default
    _default = Notifier(cfg)


def configure_from_api_config(config_path: Path) -> None:
    """Replace the default notifier using the main tgw-api-config.json."""
    global _default
    _default = Notifier.from_api_config(config_path)


def notify(title: str, message: str = '', level: str = 'info') -> None:
    """
    Send a notification via the default (module-level) notifier.

    This is the primary call site for all TGW components:
        from tgw.notify import notify
        notify('Job complete', f'Processed {n} items', level='info')
    """
    _default.send(title, message, level)
