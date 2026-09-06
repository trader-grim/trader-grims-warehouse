"""Collect models from every source, with cache + offline fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import fallback as _fallback
from . import sources as _sources
from .cache import DEFAULT_TTL_SECONDS, Cache
from .http import SourceUnavailable
from .models import ModelInfo

DEFAULT_SOURCES: tuple[str, ...] = _sources.ALL


@dataclass
class CollectResult:
    """Everything a caller (or the CLI) needs to explain the outcome."""

    models: list[ModelInfo]
    offline: bool = False
    used_fallback: bool = False
    sources_ok: list[str] = field(default_factory=list)          # a live fetch succeeded
    sources_stale: list[str] = field(default_factory=list)       # served from an expired cache
    sources_cached: list[str] = field(default_factory=list)      # served from a fresh cache
    sources_failed: dict[str, str] = field(default_factory=dict)  # name -> reason

    @property
    def degraded(self) -> bool:
        return self.offline or self.used_fallback or bool(self.sources_stale) or bool(self.sources_failed)

    def summary(self) -> str:
        if self.used_fallback:
            return f"as of {_fallback.generated_date()}"
        bits = []
        if self.sources_ok:
            bits.append("live: " + ", ".join(sorted(self.sources_ok)))
        if self.sources_cached:
            bits.append("cache: " + ", ".join(sorted(self.sources_cached)))
        if self.sources_stale:
            bits.append("stale cache: " + ", ".join(sorted(self.sources_stale)))
        if self.sources_failed:
            bits.append("failed: " + ", ".join(sorted(self.sources_failed)))
        return "; ".join(bits) or "no sources"


def _fetch_one(name: str, module, *, openrouter_key, groq_key, models_dev_providers, timeout):
    if name == _sources.openrouter.NAME:
        return module.fetch(openrouter_key, timeout=timeout)
    if name == _sources.groq.NAME:
        return module.fetch(groq_key, timeout=timeout)
    if name == _sources.models_dev.NAME:
        return module.fetch(timeout=timeout)
    raise SourceUnavailable(f"unknown source {name!r}")


def _parse_one(name: str, module, raw, *, models_dev_providers):
    if name == _sources.models_dev.NAME:
        return module.parse(raw, providers=models_dev_providers or _sources.models_dev.DEFAULT_PROVIDERS)
    return module.parse(raw)


def _dedupe(models: list[ModelInfo]) -> list[ModelInfo]:
    merged: dict[tuple[str, str], ModelInfo] = {}
    for m in models:
        key = (m.provider, m.model_id)
        merged[key] = m if key not in merged else merged[key].merge(m)
    return list(merged.values())


def collect(
    *,
    sources: tuple[str, ...] | list[str] = DEFAULT_SOURCES,
    openrouter_key: str | None = None,
    groq_key: str | None = None,
    models_dev_providers: tuple[str, ...] | list[str] | str | None = None,
    cache: Cache | None = None,
    cache_ttl: float = DEFAULT_TTL_SECONDS,
    use_cache: bool = True,
    refresh: bool = False,
    timeout: float = 15.0,
    fallback_on_empty: bool = True,
) -> CollectResult:
    """Query ``sources`` and return a :class:`CollectResult`.

    Per source: use a fresh cache entry if allowed; otherwise fetch live and
    refresh the cache; on failure fall back to a stale cache entry if one
    exists. If every requested source yields nothing and ``fallback_on_empty``
    is set, load the checked-in list.
    """
    openrouter_key = openrouter_key if openrouter_key is not None else os.environ.get("OPENROUTER_API_KEY")
    groq_key = groq_key if groq_key is not None else os.environ.get("GROQ_API_KEY")
    if cache is None and use_cache:
        cache = Cache(ttl=cache_ttl)

    result = CollectResult(models=[])
    collected: list[ModelInfo] = []

    for name in sources:
        module = _sources.BY_NAME.get(name)
        if module is None:
            result.sources_failed[name] = "unknown source"
            continue

        raw = None
        # 1. fresh cache
        if cache is not None and use_cache and not refresh:
            data, meta = cache.get(name, max_age=cache_ttl)
            if data is not None and meta and meta["fresh"]:
                raw = data
                result.sources_cached.append(name)

        # 2. live fetch
        if raw is None:
            try:
                raw = _fetch_one(
                    name, module,
                    openrouter_key=openrouter_key, groq_key=groq_key,
                    models_dev_providers=models_dev_providers, timeout=timeout,
                )
                result.sources_ok.append(name)
                if cache is not None and use_cache:
                    cache.put(name, raw)
            except SourceUnavailable as exc:
                # 3. stale cache
                if cache is not None and use_cache:
                    data, meta = cache.get(name, max_age=float("inf"))
                    if data is not None:
                        raw = data
                        result.sources_stale.append(name)
                if raw is None:
                    result.sources_failed[name] = str(exc)
                    continue

        try:
            collected.extend(_parse_one(name, module, raw, models_dev_providers=models_dev_providers))
        except (KeyError, TypeError, ValueError) as exc:  # malformed payload shape
            result.sources_failed[name] = f"parse error: {exc}"

    # offline == no source produced a live result
    result.offline = not result.sources_ok and not result.sources_cached

    if not collected and fallback_on_empty:
        collected = _fallback.load()
        result.used_fallback = True
        result.offline = True

    result.models = _dedupe(collected)
    return result
