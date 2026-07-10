"""
tgw.apis.nats_client — Synchronous fire-and-forget NATS JetStream publisher.

Wraps the async nats-py library for use from TGW's synchronous workers and
API calls. A single background thread owns the event loop and NATS connection;
sync callers submit messages via a thread-safe queue.

Fire-and-forget semantics: if NATS is unreachable, messages are dropped silently.
ItemData writes are never blocked or failed by NATS availability (hard constraint).

JetStream streams (created once at startup):
  ITEMDATA_MUTATIONS — subject: itemdata.{sku}.{field}
  QUEUE_TRANSITIONS  — subject: queue.{queue_name}.{state}  (Phase 2)

Usage:
  from tgw.apis.nats_client import publish_mutation, init_nats
  init_nats(cfg)   # call once at worker/server startup
  publish_mutation(sku='tgwXXX', field='price', old_value=10, new_value=15,
                   source='worker:ebay_price')
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stream / subject definitions
# ---------------------------------------------------------------------------

STREAM_MUTATIONS = "ITEMDATA_MUTATIONS"
STREAM_TRANSITIONS = "QUEUE_TRANSITIONS"
SUBJECT_MUTATION = "itemdata.{sku}.{field}"
SUBJECT_TRANSITION = "queue.{queue_name}.{state}"

# ---------------------------------------------------------------------------
# Module-level connection state
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_loop: Optional[asyncio.AbstractEventLoop] = None
_async_queue: Optional[asyncio.Queue] = None
_started = False
_url = "nats://127.0.0.1:4222"


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------

def _bg_thread_main(url: str) -> None:
    global _loop, _async_queue

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    q: asyncio.Queue = asyncio.Queue()

    # Make loop and queue visible to sync callers before the first await
    with _lock:
        _loop = loop
        _async_queue = q

    async def _run() -> None:
        try:
            import nats
        except ImportError:
            log.warning("nats-py not installed — mutations will not be audited")
            return

        nc = None
        js = None

        async def _on_error(e: Exception) -> None:
            log.debug("nats error: %s", e)

        async def _on_disconnect() -> None:
            log.debug("nats disconnected")

        async def _on_reconnect() -> None:
            log.info("nats reconnected")

        async def _connect():
            nonlocal nc, js
            try:
                nc = await nats.connect(
                    url,
                    max_reconnect_attempts=3,
                    reconnect_time_wait=2,
                    error_cb=_on_error,
                    disconnected_cb=_on_disconnect,
                    reconnected_cb=_on_reconnect,
                )
                js = nc.jetstream()
                await _ensure_streams(js)
                log.info("nats: connected to %s", url)
                return True
            except Exception as e:
                log.warning("nats: connection failed (%s) — audit stream inactive", e)
                return False

        connected = await _connect()

        while True:
            try:
                item = await asyncio.wait_for(q.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Heartbeat — attempt reconnect if needed
                if not connected and nc is None:
                    connected = await _connect()
                continue

            if item is None:
                break   # shutdown signal

            if not connected or js is None:
                continue  # drop — NATS unavailable

            subject, payload = item
            try:
                await js.publish(subject, json.dumps(payload, default=str).encode())
            except Exception as e:
                log.debug("nats publish failed for %s: %s", subject, e)
                connected = False   # mark for reconnect on next heartbeat
                nc = None

        if nc is not None:
            try:
                await nc.close()
            except Exception:
                pass

    loop.run_until_complete(_run())


async def _ensure_streams(js: Any) -> None:
    """Create JetStream streams if they don't exist yet."""
    # max_age in nats-py is seconds (float); max_bytes -1 = server-side limit from nats-server.conf
    streams = [
        {"name": STREAM_MUTATIONS, "subjects": ["itemdata.>"], "max_age": 90 * 86400.0},
        {"name": STREAM_TRANSITIONS, "subjects": ["queue.>"], "max_age": 30 * 86400.0},
    ]
    for s in streams:
        try:
            await js.stream_info(s["name"])
        except Exception:
            try:
                await js.add_stream(name=s["name"], subjects=s["subjects"],
                                    max_age=s["max_age"])
                log.info("nats: created stream %s", s["name"])
            except Exception as e:
                log.warning("nats: could not create stream %s: %s", s["name"], e)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_nats(cfg: Dict[str, Any]) -> None:
    """Start the NATS background publisher thread. Call once at startup.

    Reads nats_url from config; defaults to nats://127.0.0.1:4222.
    Safe to call multiple times — only starts one thread.
    """
    global _started, _url

    with _lock:
        if _started:
            return
        _started = True
        _url = cfg.get("nats_url", "nats://127.0.0.1:4222")

    t = threading.Thread(
        target=_bg_thread_main,
        args=(_url,),
        daemon=True,
        name="tgw-nats-publisher",
    )
    t.start()
    # Give the thread a moment to set _loop/_async_queue before returning
    for _ in range(20):
        if _loop is not None:
            break
        time.sleep(0.05)


def _enqueue(subject: str, payload: Dict[str, Any]) -> None:
    """Thread-safe enqueue. Drops silently if NATS thread not running."""
    with _lock:
        loop = _loop
        q = _async_queue

    if loop is None or q is None:
        return
    try:
        loop.call_soon_threadsafe(q.put_nowait, (subject, payload))
    except Exception:
        pass


def publish_mutation(
    sku: str,
    field: str,
    old_value: Any,
    new_value: Any,
    source: str,
    *,
    session_id: Optional[str] = None,
) -> None:
    """Publish one ItemData field mutation to ITEMDATA_MUTATIONS stream.

    Fire-and-forget — never raises. If NATS is down the call returns immediately.
    """
    subject = SUBJECT_MUTATION.format(sku=sku, field=field)
    payload: Dict[str, Any] = {
        "sku": sku,
        "field": field,
        "old_value": old_value,
        "new_value": new_value,
        "source": source,
        "ts": time.time(),
    }
    if session_id:
        payload["session_id"] = session_id
    _enqueue(subject, payload)


def publish_queue_transition(
    job_id: str,
    queue_name: str,
    old_state: str,
    new_state: str,
    *,
    entity_id: Optional[str] = None,
    error_code: Optional[str] = None,
    session_id: Optional[str] = None,
) -> None:
    """Publish one queue state transition to QUEUE_TRANSITIONS stream (Phase 2)."""
    subject = SUBJECT_TRANSITION.format(queue_name=queue_name, state=new_state)
    payload: Dict[str, Any] = {
        "job_id": job_id,
        "queue_name": queue_name,
        "old_state": old_state,
        "new_state": new_state,
        "ts": time.time(),
    }
    if entity_id:
        payload["entity_id"] = entity_id
    if error_code:
        payload["error_code"] = error_code
    if session_id:
        payload["session_id"] = session_id
    _enqueue(subject, payload)


def check_nats(url: Optional[str] = None) -> Dict[str, Any]:
    """Probe NATS connectivity. Returns {ok, url, latency_ms} or {ok=False, error}."""
    target = url or _url or "nats://127.0.0.1:4222"

    async def _probe():
        try:
            import nats as _nats
            t0 = time.monotonic()
            nc = await asyncio.wait_for(
                _nats.connect(target, max_reconnect_attempts=1), timeout=3.0
            )
            latency = round((time.monotonic() - t0) * 1000, 1)
            js = nc.jetstream()
            streams = []
            for name in (STREAM_MUTATIONS, STREAM_TRANSITIONS):
                try:
                    info = await js.stream_info(name)
                    streams.append({"name": name, "ok": True,
                                    "messages": info.state.messages if info.state else None})
                except Exception:
                    streams.append({"name": name, "ok": False})
            await nc.close()
            return {"ok": True, "url": target, "latency_ms": latency,
                    "streams": streams}
        except asyncio.TimeoutError:
            return {"ok": False, "url": target, "error": "connection timeout"}
        except Exception as e:
            return {"ok": False, "url": target, "error": str(e)}

    try:
        return asyncio.run(_probe())
    except Exception as e:
        return {"ok": False, "url": target, "error": str(e)}


def query_mutations(
    sku: str,
    field: Optional[str] = None,
    limit: int = 100,
    url: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull the mutation history for a SKU from JetStream. Sync wrapper."""
    target = url or _url or "nats://127.0.0.1:4222"

    async def _fetch():
        try:
            import nats as _nats
            nc = await asyncio.wait_for(
                _nats.connect(target, max_reconnect_attempts=1), timeout=5.0
            )
            js = nc.jetstream()
            subject_filter = (
                SUBJECT_MUTATION.format(sku=sku, field=field)
                if field
                else f"itemdata.{sku}.>"
            )
            try:
                sub = await js.subscribe(
                    subject_filter,
                    durable=None,
                    stream=STREAM_MUTATIONS,
                    deliver_policy="all",
                    config=None,
                )
                msgs = []
                deadline = time.monotonic() + 3.0
                while len(msgs) < limit and time.monotonic() < deadline:
                    try:
                        msg = await asyncio.wait_for(sub.next_msg(), timeout=0.5)
                        data = json.loads(msg.data.decode())
                        msgs.append(data)
                        await msg.ack()
                    except asyncio.TimeoutError:
                        break
                await sub.unsubscribe()
                await nc.close()
                return {"ok": True, "sku": sku, "count": len(msgs), "mutations": msgs}
            except Exception as e:
                await nc.close()
                return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    try:
        return asyncio.run(_fetch())
    except Exception as e:
        return {"ok": False, "error": str(e)}
