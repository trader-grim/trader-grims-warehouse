"""Small helpers shared by the source parsers."""

from __future__ import annotations

import datetime as _dt


def is_zero_price(value: object) -> bool:
    """OpenRouter and models.dev express prices as strings or numbers.
    Treat missing / '0' / '0.0' / 0 as free."""
    if value is None:
        return True
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False


def is_past(date_str: object, *, today: _dt.date | None = None) -> bool:
    """True when ``date_str`` (ISO ``YYYY-MM-DD`` or full ISO timestamp) is a
    valid date at or before today. Unparseable / empty -> False (not expired)."""
    if not date_str or not isinstance(date_str, str):
        return False
    text = date_str.strip()
    for candidate in (text, text[:10]):
        try:
            parsed = _dt.date.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed <= (today or _dt.date.today())
    return False
