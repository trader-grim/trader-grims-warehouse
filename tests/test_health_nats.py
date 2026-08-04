"""Tests for check_nats() — PP-AIOPS-001 Phase 1 audit-mutation stream.

nats-py is fire-and-forget by design (nats_client.py): ItemData writes are
never blocked or failed by NATS availability. Todo #1259 found that a
broker-unreachable connection failure (expected until #1510 stands up the
actual broker) was misreported as a plain health failure, indistinguishable
from a missing dependency. These tests pin the corrected classification:

  - connected                          -> ok=True,  warn=False
  - broker unreachable (no servers,
    connection refused/timeout)        -> ok=True,  warn=True  (informational)
  - anything else (e.g. module missing,
    unexpected probe error)            -> ok=False, warn=True
"""

from __future__ import annotations

import tgw.health as health


def _cfg() -> dict:
    return {"nats_url": "nats://127.0.0.1:4222"}


def test_connected_is_healthy(monkeypatch):
    monkeypatch.setattr(
        "tgw.apis.nats_client.check_nats",
        lambda url=None: {"ok": True, "url": url, "latency_ms": 4.2, "streams": []},
    )
    result = health.check_nats(_cfg())
    assert result["ok"] is True
    assert result["warn"] is False
    assert "connected" in result["detail"]


def test_broker_unreachable_no_servers_is_warned_not_failed(monkeypatch):
    monkeypatch.setattr(
        "tgw.apis.nats_client.check_nats",
        lambda url=None: {"ok": False, "url": url,
                           "error": "nats: no servers available for connection"},
    )
    result = health.check_nats(_cfg())
    assert result["ok"] is True
    assert result["warn"] is True
    assert "#1510" in result["detail"]


def test_broker_unreachable_connection_refused_is_warned_not_failed(monkeypatch):
    monkeypatch.setattr(
        "tgw.apis.nats_client.check_nats",
        lambda url=None: {"ok": False, "url": url,
                           "error": "Connect call failed ('127.0.0.1', 4222): connection refused"},
    )
    result = health.check_nats(_cfg())
    assert result["ok"] is True
    assert result["warn"] is True


def test_broker_unreachable_timeout_is_warned_not_failed(monkeypatch):
    monkeypatch.setattr(
        "tgw.apis.nats_client.check_nats",
        lambda url=None: {"ok": False, "url": url, "error": "connection timeout"},
    )
    result = health.check_nats(_cfg())
    assert result["ok"] is True
    assert result["warn"] is True


def test_unexpected_error_still_fails(monkeypatch):
    monkeypatch.setattr(
        "tgw.apis.nats_client.check_nats",
        lambda url=None: {"ok": False, "url": url, "error": "auth violation"},
    )
    result = health.check_nats(_cfg())
    assert result["ok"] is False
    assert result["warn"] is True


def test_module_missing_still_fails(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "tgw.apis.nats_client":
            raise ImportError("No module named 'nats'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    result = health.check_nats(_cfg())
    assert result["ok"] is False
    assert "not installed" in result["detail"]
