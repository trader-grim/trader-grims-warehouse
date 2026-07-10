"""audit#1143 #1202 — acquire_ollama_lock()'s DSN fallback must track live
state_machine.init(dsn) overrides, not a stale import-time snapshot.

Bug: `from tgw.queue.state_machine import _DSN` bound the value at import
time. A caller whose cfg lacked 'postgres_dsn' would fall back to whatever
_DSN was when ollama_lock.py was first imported — never reflecting a later
state_machine.init(dsn) call, silently connecting to a stale/wrong DB target.

All psycopg2 connections are mocked — tests pass completely offline, no
real Postgres connection is made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tgw.queue import ollama_lock, state_machine


def _fake_connect(captured):
    def _connect(dsn):
        captured.append(dsn)
        con = MagicMock()
        con.cursor.return_value.__enter__.return_value = MagicMock()
        return con
    return _connect


class TestAcquireOllamaLockDsnFallback:
    def test_cfg_postgres_dsn_is_used_when_present(self):
        captured = []
        with patch.object(ollama_lock.psycopg2, 'connect', side_effect=_fake_connect(captured)):
            with ollama_lock.acquire_ollama_lock({'postgres_dsn': 'dbname=explicit user=tgw'}):
                pass
        assert captured == ['dbname=explicit user=tgw']

    def test_missing_cfg_dsn_falls_back_to_live_state_machine_dsn_not_stale_snapshot(self):
        # Regression for #1202: override state_machine._DSN AFTER
        # ollama_lock has already been imported (exactly the real-world
        # sequence — modules import at process start, init() runs later
        # during worker startup) and confirm the fallback picks up the
        # NEW value, not whatever _DSN was at import time.
        original = state_machine._DSN
        try:
            state_machine.init('dbname=overridden_live_dsn user=tgw')
            captured = []
            with patch.object(ollama_lock.psycopg2, 'connect', side_effect=_fake_connect(captured)):
                with ollama_lock.acquire_ollama_lock({}):
                    pass
            assert captured == ['dbname=overridden_live_dsn user=tgw']
        finally:
            state_machine._DSN = original

    def test_cfg_dsn_takes_precedence_over_live_state_machine_dsn(self):
        original = state_machine._DSN
        try:
            state_machine.init('dbname=some_other_live_dsn user=tgw')
            captured = []
            with patch.object(ollama_lock.psycopg2, 'connect', side_effect=_fake_connect(captured)):
                with ollama_lock.acquire_ollama_lock({'postgres_dsn': 'dbname=explicit user=tgw'}):
                    pass
            assert captured == ['dbname=explicit user=tgw']
        finally:
            state_machine._DSN = original
