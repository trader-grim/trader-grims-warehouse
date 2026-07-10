"""Shared locking helper for eBay API disk caches.

audit#1143 #1239 (merged #1179+#1180): specifics.py's per-category aspects
cache and taxonomy.py's category-tree caches both did unlocked, non-atomic
disk read-modify-write — a crash mid-write could corrupt the whole cache
file (forcing every subsequent read to fall back to a live API call, the
exact quota-exhaustion failure mode these caches exist to prevent), and for
specifics.py's accumulating per-category dict cache specifically, two
concurrent cache-miss writers could race and silently drop each other's
newly-cached entries (classic read-modify-write lost update).

Code-review follow-up: the first version of this module rolled its own
tmp+rename atomic write instead of reusing tgw.catalog.atomic_write_json(),
which already exists for exactly this (non-item, catalog/cache) class of
file. That duplication caused a real regression — the hand-rolled version
didn't preserve the target's existing permission mode, silently narrowing
the real production caches from 0644 to 0600 on first write (NamedTemporaryFile
always creates at 0600; a plain rename carries the temp file's mode, not the
destination's). Delegating to the existing helper fixes that for free and
removes a fourth independent tmp+rename implementation from the tree.

One entry point:
  locked_merge_cache_json(path, merge)
      Read-modify-write for accumulating dict caches (one entry per
      category) — holds an exclusive flock across the read+merge+write so
      two concurrent writers merge instead of racing to overwrite each
      other. `merge(current_dict) -> updated_dict` should be cheap (no live
      API call) — do any slow work (the live fetch) BEFORE calling this,
      outside the lock, so callers aren't serialized on it.

For single-value caches (tree ID, tree data) that don't need locking (each
write fully overwrites with a freshly fetched value — no merge, so
last-write-wins is safe), call tgw.catalog.atomic_write_json(path, data,
pretty=False) directly instead of adding a second entry point here.
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path
from typing import Any, Callable, Dict

from tgw.catalog import atomic_write_json as _atomic_write_json


def locked_merge_cache_json(
    path: Path, merge: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> Dict[str, Any]:
    """Read-modify-write a dict cache file under an exclusive flock held on
    a `<path>.lock` sidecar file, so concurrent writers never race the
    read-modify-write cycle against each other."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + '.lock')
    with open(lock_path, 'a+') as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            current: Dict[str, Any] = {}
            if path.exists():
                try:
                    current = json.loads(path.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    current = {}
            updated = merge(current)
            _atomic_write_json(path, updated, pretty=False)
            return updated
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)
