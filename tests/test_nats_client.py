"""
Tests for tgw.apis.nats_client and the mutation audit wiring in items.py.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_item_dir(tmp_path: Path, sku: str, data: Dict[str, Any]) -> Path:
    d = tmp_path / sku
    d.mkdir()
    (d / f"{sku}.json").write_text(json.dumps(data))
    return d


def _make_cfg(tmp_path: Path, sku: str) -> Dict[str, Any]:
    return {
        "itemdata_root": tmp_path,
        "pretty": False,
        "nats_url": "nats://127.0.0.1:4222",
    }


# ---------------------------------------------------------------------------
# nats_client unit tests (no real NATS required)
# ---------------------------------------------------------------------------

class TestNatsClientPublish:
    """publish_mutation is fire-and-forget and never raises."""

    def test_publish_mutation_no_thread(self):
        """publish_mutation before init_nats silently drops the message."""
        from tgw.apis import nats_client
        # Reset module state
        nats_client._loop = None
        nats_client._async_queue = None
        nats_client._started = False

        # Should not raise
        nats_client.publish_mutation(
            sku="tgw123",
            field="price",
            old_value=10,
            new_value=20,
            source="test",
        )

    def test_publish_mutation_with_mocked_loop(self):
        """publish_mutation calls call_soon_threadsafe when loop is available."""
        from tgw.apis import nats_client

        mock_loop = MagicMock()
        mock_queue = MagicMock()

        original_loop = nats_client._loop
        original_queue = nats_client._async_queue
        try:
            nats_client._loop = mock_loop
            nats_client._async_queue = mock_queue

            nats_client.publish_mutation(
                sku="tgwABC",
                field="status",
                old_value="new",
                new_value="staged",
                source="worker:ebay_stage",
            )
            mock_loop.call_soon_threadsafe.assert_called_once()
            call_args = mock_loop.call_soon_threadsafe.call_args[0]
            assert call_args[0] is mock_queue.put_nowait
            subject, payload = call_args[1]
            assert subject == "itemdata.tgwABC.status"
            assert payload["field"] == "status"
            assert payload["old_value"] == "new"
            assert payload["new_value"] == "staged"
            assert payload["source"] == "worker:ebay_stage"
        finally:
            nats_client._loop = original_loop
            nats_client._async_queue = original_queue

    def test_publish_queue_transition(self):
        """publish_queue_transition enqueues correctly."""
        from tgw.apis import nats_client

        mock_loop = MagicMock()
        mock_queue = MagicMock()
        original_loop = nats_client._loop
        original_queue = nats_client._async_queue
        try:
            nats_client._loop = mock_loop
            nats_client._async_queue = mock_queue

            nats_client.publish_queue_transition(
                job_id="42",
                queue_name="ebay_upload",
                old_state="running",
                new_state="succeeded",
            )
            mock_loop.call_soon_threadsafe.assert_called_once()
            call_args = mock_loop.call_soon_threadsafe.call_args[0]
            subject, payload = call_args[1]
            assert subject == "queue.ebay_upload.succeeded"
            assert payload["job_id"] == "42"
        finally:
            nats_client._loop = original_loop
            nats_client._async_queue = original_queue

    def test_publish_never_raises_on_exception(self):
        """Even if call_soon_threadsafe raises, publish_mutation returns cleanly."""
        from tgw.apis import nats_client

        mock_loop = MagicMock()
        mock_loop.call_soon_threadsafe.side_effect = RuntimeError("loop closed")
        mock_queue = MagicMock()
        original_loop = nats_client._loop
        original_queue = nats_client._async_queue
        try:
            nats_client._loop = mock_loop
            nats_client._async_queue = mock_queue
            nats_client.publish_mutation("sku", "f", None, None, "test")  # must not raise
        finally:
            nats_client._loop = original_loop
            nats_client._async_queue = original_queue


# ---------------------------------------------------------------------------
# _ensure_streams: read-only, single-authority (todo #1638)
# ---------------------------------------------------------------------------

class TestEnsureStreamsReadOnly:
    """_ensure_streams() must never create/edit stream config — that's
    nats.nix's declarative job now. It only checks and logs."""

    def test_ensure_streams_never_calls_add_stream_when_present(self):
        from tgw.apis import nats_client

        js = MagicMock()
        js.stream_info = AsyncMock(return_value=MagicMock())
        js.add_stream = AsyncMock()

        asyncio.run(nats_client._ensure_streams(js))

        assert js.stream_info.await_count == 2
        js.add_stream.assert_not_awaited()

    def test_ensure_streams_never_calls_add_stream_when_missing(self, caplog):
        """Even if stream_info fails (stream missing), no add_stream call —
        just a log.error, per #1638 single-authority spec."""
        from tgw.apis import nats_client

        js = MagicMock()
        js.stream_info = AsyncMock(side_effect=Exception("stream not found"))
        js.add_stream = AsyncMock()

        with caplog.at_level("ERROR"):
            asyncio.run(nats_client._ensure_streams(js))

        js.add_stream.assert_not_awaited()
        assert any("not found" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# check_nats / query_mutations: work from inside a running event loop (#1639)
# ---------------------------------------------------------------------------

class TestRunIsolated:
    """check_nats()/query_mutations() must not raise
    'asyncio.run() cannot be called from a running event loop' when called
    from a caller thread that already has a loop running (the MCP path)."""

    def test_check_nats_from_running_loop_does_not_raise(self):
        from tgw.apis import nats_client

        async def _main():
            # Simulates FastMCP's already-running event loop calling into
            # check_nats() synchronously.
            return nats_client.check_nats(url="nats://127.0.0.1:1")

        result = asyncio.run(_main())
        assert isinstance(result, dict)
        assert "ok" in result

    def test_query_mutations_from_running_loop_does_not_raise(self):
        from tgw.apis import nats_client

        async def _main():
            return nats_client.query_mutations(
                "tgw123", url="nats://127.0.0.1:1"
            )

        result = asyncio.run(_main())
        assert isinstance(result, dict)
        assert "ok" in result

    def test_run_isolated_returns_coro_result(self):
        from tgw.apis import nats_client

        async def _coro():
            return {"value": 42}

        assert nats_client._run_isolated(_coro) == {"value": 42}


# ---------------------------------------------------------------------------
# items._write_field mutation publish wiring
# ---------------------------------------------------------------------------

class TestWriteFieldPublish:
    """_write_field publishes a mutation event after each successful write."""

    def test_write_field_publishes_mutation(self, tmp_path):
        sku = "tgw20260101000000000"
        _make_item_dir(tmp_path, sku, {"sku": sku, "price": 10})
        cfg = _make_cfg(tmp_path, sku)

        published = []

        def _fake_publish(sku, field, old_value, new_value, source, session_id=None):
            published.append({"sku": sku, "field": field,
                               "old": old_value, "new": new_value, "source": source})

        with patch("tgw.apis.nats_client.publish_mutation", _fake_publish):
            from tgw.items import _write_field
            result = _write_field(cfg, sku, "price", 25)

        assert result["before"] == 10
        assert result["after"] == 25
        assert len(published) == 1
        assert published[0]["field"] == "price"
        assert published[0]["old"] == 10
        assert published[0]["new"] == 25

    def test_write_field_publish_failure_does_not_break_write(self, tmp_path):
        """If NATS publish raises, the item write still succeeds."""
        sku = "tgw20260101000000001"
        _make_item_dir(tmp_path, sku, {"sku": sku, "title": "old"})
        cfg = _make_cfg(tmp_path, sku)

        with patch("tgw.apis.nats_client.publish_mutation",
                   side_effect=RuntimeError("nats down")):
            from tgw.items import _write_field
            result = _write_field(cfg, sku, "title", "new title")

        assert result["after"] == "new title"
        data = json.loads((tmp_path / sku / f"{sku}.json").read_text())
        assert data["title"] == "new title"

    def test_set_mutation_context_used_in_publish(self, tmp_path):
        """set_mutation_context changes the source attributed to mutations."""
        sku = "tgw20260101000000002"
        _make_item_dir(tmp_path, sku, {"sku": sku, "qty": 1})
        cfg = _make_cfg(tmp_path, sku)

        captured_source = []

        def _capture(sku, field, old_value, new_value, source, session_id=None):
            captured_source.append(source)

        with patch("tgw.apis.nats_client.publish_mutation", _capture):
            from tgw.items import _write_field, set_mutation_context
            set_mutation_context("worker:test_worker")
            _write_field(cfg, sku, "qty", 2)

        assert captured_source == ["worker:test_worker"]
