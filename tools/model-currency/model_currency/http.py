"""Minimal JSON GET over the standard library. No third-party HTTP client."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

__version__ = "0.1.0"

DEFAULT_HEADERS = {
    "User-Agent": f"model-currency/{__version__} (+standalone model catalogue tool)",
    "Accept": "application/json",
}


class SourceUnavailable(Exception):
    """A source could not be reached or returned something unusable.

    Raised for every network failure, HTTP error, timeout, and malformed body,
    so callers have a single thing to catch when deciding to fall back to
    cache or the checked-in list.
    """


def get_json(url: str, *, headers: dict[str, str] | None = None, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={**DEFAULT_HEADERS, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
            raw = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as exc:
        raise SourceUnavailable(f"{url}: {exc}") from exc
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SourceUnavailable(f"{url}: invalid JSON response: {exc}") from exc
