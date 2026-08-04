# Packet #1639 — fix `tgw_health`'s NATS check: nested `asyncio.run()`

**pp_ref:** PP-AIOPS-001
**Size:** XS (single file, no schema/data migration)
**Dispatch together with:** todo #1638's app-code step (same file,
`src/tgw/apis/nats_client.py`) — one `tgw-coder` worktree does both, in
this order: #1638's `_ensure_streams()` change first, then this fix,
since both land in the same file and should be one coherent diff, not
two competing edits.

## Context budget

Read `src/tgw/apis/nats_client.py` in full (348 lines). Read
`src/tgw/health.py` lines 544-599 (`check_nats`) and `src/tgw/mcp_server.py`
lines 249-266 (`tgw_health` MCP tool) — these are the two call paths that
matter, don't read the rest of either file.

## Verified-live root cause (2026-07-22)

`nats_client.check_nats()` (line 261-293) and `nats_client.query_mutations()`
(line 296-347) both call `asyncio.run(...)` unconditionally at their end.
`asyncio.run()` raises `RuntimeError: asyncio.run() cannot be called from
a running event loop` if the calling thread already has one running.

Two call paths reach `check_nats()`:
- **CLI** (`tgw health`) — no event loop running in that process, works
  fine. This is why the bug went unnoticed.
- **MCP** (`mcp_server.py`'s `tgw_health()` tool, decorated `@mcp.tool()`)
  — FastMCP runs tool handlers inside its own async event loop.
  `tgw_health()` calls `health.check_all()` synchronously, which calls
  `health.check_nats()` (`health.py:544`), which calls
  `nats_client.check_nats()` — three sync frames deep inside an
  already-running loop. `asyncio.run()` raises there every time.

Confirmed by Tigwa exercising the MCP path directly; not reproduced via
CLI, which is why this shipped without anyone catching it.

## Spec

Fix both `check_nats()` and `query_mutations()` in `nats_client.py` to
work correctly whether or not the calling thread already has a running
event loop. Do not special-case detect-and-branch on
`asyncio.get_running_loop()` at each call site (fragile, easy to miss a
future third call path) — instead, always run the coroutine on a
dedicated short-lived thread with its own fresh event loop, so neither
function ever depends on whether the *caller's* thread has a loop:

```python
def _run_isolated(coro_fn):
    """Run an async probe/query on its own thread+loop, regardless of
    whether the calling thread already has one running."""
    result = {}
    def _worker():
        result["value"] = asyncio.run(coro_fn())
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    return result["value"]
```

(`threading` is already imported in this file.) Replace the trailing
`try: return asyncio.run(_probe()) except ...` / `try: return
asyncio.run(_fetch()) except ...` blocks in both functions with a call
through this helper, keeping the same outer `try/except Exception as e:
return {"ok": False, ...}` fallback shape each function already has.

Do not touch the background-publisher thread (`_bg_thread_main`,
`init_nats`, `_enqueue`, `publish_mutation`, `publish_queue_transition`)
— those already run correctly on their own dedicated thread+loop and are
unrelated to this bug.

## Verification (live, both call paths — do not accept CLI-only proof)

1. **CLI path unchanged:** `tgw health` still returns a NATS result
   (green/yellow/red per `health.py`'s existing semantics) with no
   exception.
2. **MCP path fixed (the actual bug):** invoke the `tgw_health` MCP tool
   (via the `tgw-http` MCP server, same path Tigwa used) and confirm the
   response's `nats` entry has a real `ok`/`detail` value, not an
   exception string containing "cannot be called from a running event
   loop".
3. Confirm `query_mutations()` (used by any code path that reads mutation
   history back) also works called from inside a running loop — a quick
   throwaway script that calls it from within `asyncio.run(async def
   main(): nats_client.query_mutations(...))` is sufficient to prove the
   fix generalizes to both functions, not just the one Tigwa happened to
   exercise.

## Out of scope

- Any change to the background publisher thread's own event-loop
  handling — already correct, untouched.
- Any change to what `check_nats()`/`query_mutations()` report semantically
  (green/yellow/red thresholds in `health.py`) — this packet only fixes
  the crash, not the health semantics.
