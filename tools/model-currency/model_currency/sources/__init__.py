"""Upstream model-list sources.

Each module exposes:

* ``NAME``  — the ``source`` string stamped onto every ``ModelInfo``.
* ``fetch(...) -> object``  — one network call, returns the raw decoded body.
  Raises :class:`model_currency.http.SourceUnavailable` on any failure.
* ``parse(raw, ...) -> list[ModelInfo]``  — pure, no I/O.
"""

from __future__ import annotations

from . import groq, models_dev, openrouter

BY_NAME = {
    openrouter.NAME: openrouter,
    groq.NAME: groq,
    models_dev.NAME: models_dev,
}

ALL = tuple(BY_NAME)

__all__ = ["openrouter", "groq", "models_dev", "BY_NAME", "ALL"]
