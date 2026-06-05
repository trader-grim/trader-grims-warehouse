"""
tgw_widgets.py — Custom Qtile widgets for Trader Grim's Warehouse.

Three widgets:
  TGWQueueWidget   — polls tgw-http /api/queue/status; shows pending/dead counts
  TGWHealthWidget  — checks systemd unit states; shows active/total worker ratio
  TGWSKUWidget     — watches X11 clipboard for TGW SKU patterns; shows on detection

API key for tgw-http: copy to ~/.config/tgw/api-key (plain text, one line).
See etc/interfaces/qtile/install.sh for setup instructions.
"""

import json
import os
import re
import subprocess
import urllib.request
import urllib.error

from libqtile.widget import base


_SKU_RE = re.compile(r"^(tgw\d{15})$")


class TGWQueueWidget(base.ThreadPoolText):
    """Queue depth indicator — talks to tgw-http REST API."""

    defaults = [
        ("update_interval", 30, "Seconds between API polls"),
        ("api_url", "http://localhost:7373", "tgw-http base URL"),
        ("api_key_file", os.path.expanduser("~/.config/tgw/api-key"), "API key file path"),
        ("color_ok", "#a8e6cf", "Color when all queues clean"),
        ("color_busy", "#ffd3a5", "Color when jobs pending"),
        ("color_error", "#ff6b6b", "Color when dead_letter > 0"),
    ]

    def __init__(self, **config):
        config.setdefault("markup", True)
        super().__init__("Q:?", **config)
        self.add_defaults(TGWQueueWidget.defaults)
        self._api_key: str | None = None

    def _load_key(self) -> str:
        if not self._api_key:
            try:
                with open(self.api_key_file) as fh:
                    self._api_key = fh.read().strip()
            except OSError:
                return ""
        return self._api_key

    def poll(self) -> str:
        try:
            req = urllib.request.Request(f"{self.api_url}/api/queue/status")
            key = self._load_key()
            if key:
                req.add_header("Authorization", f"Bearer {key}")
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read())

            queues = data.get("queues", {})
            pending = sum(
                s.get("queued", 0) + s.get("pending", 0)
                for s in queues.values()
            )
            claimed = sum(s.get("claimed", 0) for s in queues.values())
            dead = sum(s.get("dead_letter", 0) for s in queues.values())

            if dead > 0:
                return f'<span foreground="{self.color_error}">Q:{pending}p {dead}✗</span>'
            if pending > 0:
                suffix = f" {claimed}▶" if claimed else ""
                return f'<span foreground="{self.color_busy}">Q:{pending}p{suffix}</span>'
            if claimed > 0:
                return f'<span foreground="{self.color_busy}">Q:{claimed}▶</span>'
            return f'<span foreground="{self.color_ok}">Q:✓</span>'

        except urllib.error.URLError:
            return '<span foreground="#888888">Q:off</span>'
        except Exception:
            return "Q:?"


class TGWHealthWidget(base.ThreadPoolText):
    """Worker health indicator — counts active tgw systemd units."""

    defaults = [
        ("update_interval", 60, "Seconds between systemctl polls"),
        ("color_ok", "#a8e6cf", "Color when all workers up"),
        ("color_warn", "#ffd3a5", "Color when some workers down"),
    ]

    _UNIT_PATTERNS = ["tgw-worker@*.service", "tgw-http.service"]

    def __init__(self, **config):
        config.setdefault("markup", True)
        super().__init__("W:?", **config)
        self.add_defaults(TGWHealthWidget.defaults)

    def _count_units(self, state_filter: list[str]) -> int:
        args = ["systemctl", "list-units", "--output=json"] + state_filter + self._UNIT_PATTERNS
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=5)
            units = json.loads(result.stdout or "[]")
            return len(units)
        except Exception:
            return -1

    def poll(self) -> str:
        try:
            result = subprocess.run(
                ["systemctl", "list-units", "--all", "--output=json"]
                + self._UNIT_PATTERNS,
                capture_output=True, text=True, timeout=5,
            )
            all_units = json.loads(result.stdout or "[]")
            total = len(all_units)
            active = sum(
                1 for u in all_units
                if u.get("ActiveState") == "active"
            )
            if total == 0:
                return '<span foreground="#888888">W:—</span>'
            color = self.color_ok if active == total else self.color_warn
            return f'<span foreground="{color}">W:{active}/{total}</span>'
        except Exception:
            return "W:?"


class TGWSKUWidget(base.ThreadPoolText):
    """Clipboard watcher — lights up when a TGW SKU is in the X11 clipboard.

    Shows a dimmed placeholder when idle; shows SKU in accent color on detection.
    Stores the last seen SKU so the bar can use it for chord actions.
    """

    defaults = [
        ("update_interval", 2, "Clipboard poll interval (seconds)"),
        ("color_idle", "#444466", "Color of placeholder when no SKU detected"),
        ("color_sku", "#e94560", "Color when SKU detected"),
        ("placeholder", "—", "Text shown when no SKU detected"),
    ]

    def __init__(self, **config):
        config.setdefault("markup", True)
        super().__init__("—", **config)
        self.add_defaults(TGWSKUWidget.defaults)
        self.last_sku: str = ""

    def poll(self) -> str:
        for sel in ("primary", "clipboard"):
            try:
                result = subprocess.run(
                    ["xclip", "-o", "-selection", sel],
                    capture_output=True, text=True, timeout=1,
                )
                m = _SKU_RE.match(result.stdout.strip())
                if m:
                    self.last_sku = m.group(1)
                    return f'<span foreground="{self.color_sku}">⬟ {self.last_sku}</span>'
            except Exception:
                pass
        self.last_sku = ""
        return f'<span foreground="{self.color_idle}">{self.placeholder}</span>'
