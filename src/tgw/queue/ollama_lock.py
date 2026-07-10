"""
tgw.queue.ollama_lock — Postgres advisory lock for Ollama model access.

On a 32GB CPU-only machine, loading two Ollama models concurrently
causes memory contention and thrashing.  This lock serializes all
Ollama calls so only one worker runs inference at a time.

Usage:
    from tgw.queue.ollama_lock import acquire_ollama_lock

    with acquire_ollama_lock(cfg):
        resp = requests.post('http://localhost:11434/...', ...)

The lock is a session-level Postgres advisory lock, held for the duration
of the inference call and released immediately after.  Workers that cannot
acquire the lock block until it is free — they do not fail or skip.

Lock ID: 8472 (arbitrary, unique to TGW Ollama serialization)
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator

import psycopg2

from tgw.queue import state_machine

log = logging.getLogger(__name__)

_LOCK_ID = 8472   # arbitrary 32-bit int, unique to Ollama serialization


@contextmanager
def acquire_ollama_lock(cfg: Dict[str, Any]) -> Generator[None, None, None]:
    """
    Block until the Ollama advisory lock is acquired, yield, then release.

    Opens a dedicated connection for the lock so it doesn't interfere
    with the worker's normal state-machine connection.
    """
    # audit#1143 #1202: `from tgw.queue.state_machine import _DSN` used to
    # bind this by value at import time, never reflecting a later
    # state_machine.init(dsn) override — a caller whose cfg was missing
    # postgres_dsn would silently connect to a stale/wrong DB target instead
    # of the live configured one. Read the module attribute at call time
    # instead, so it always sees whatever init() last set.
    dsn = cfg.get('postgres_dsn', state_machine._DSN)
    t0  = time.monotonic()

    con = psycopg2.connect(dsn)
    try:
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute('SELECT pg_advisory_lock(%s)', (_LOCK_ID,))

        waited = time.monotonic() - t0
        if waited > 0.5:
            log.info('ollama_lock: acquired after %.1fs wait', waited)

        yield

    finally:
        try:
            with con.cursor() as cur:
                cur.execute('SELECT pg_advisory_unlock(%s)', (_LOCK_ID,))
        except Exception:
            pass
        con.close()
        elapsed = time.monotonic() - t0
        log.debug('ollama_lock: released after %.1fs total', elapsed)
