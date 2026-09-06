"""A tiny short-TTL on-disk cache for raw source payloads.

One JSON file per source under the cache root (``$MODEL_CURRENCY_CACHE`` or
``~/.cache/model-currency``). Every read and write is best-effort: a missing,
unreadable, or unwritable cache never raises — the caller just gets ``None`` and
goes to the network (or the fallback list).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL_SECONDS = 6 * 60 * 60  # 6 hours


def default_root() -> Path:
    env = os.environ.get("MODEL_CURRENCY_CACHE")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "model-currency"


class Cache:
    def __init__(self, root: str | os.PathLike[str] | None = None, ttl: float = DEFAULT_TTL_SECONDS):
        self.root = Path(root).expanduser() if root is not None else default_root()
        self.ttl = float(ttl)

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
        return self.root / f"{safe}.json"

    def get(self, key: str, *, max_age: float | None = None) -> tuple[Any | None, dict | None]:
        """Return ``(data, meta)``. ``meta`` has ``age``, ``fresh``, ``fetched_at``.
        ``(None, None)`` when there is no usable cache entry at all."""
        path = self._path(key)
        try:
            payload = json.loads(path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None, None
        if not isinstance(payload, dict) or "data" not in payload:
            return None, None
        fetched_at = float(payload.get("fetched_at") or 0.0)
        age = max(time.time() - fetched_at, 0.0)
        limit = self.ttl if max_age is None else max_age
        return payload["data"], {"age": age, "fresh": age <= limit, "fetched_at": fetched_at}

    def put(self, key: str, data: Any) -> bool:
        path = self._path(key)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps({"fetched_at": time.time(), "data": data}), "utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            return False
