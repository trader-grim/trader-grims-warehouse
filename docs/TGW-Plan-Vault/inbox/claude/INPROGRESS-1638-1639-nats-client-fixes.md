# In progress: todo #1638 (app-code step) + #1639 combined — nats_client.py

Worktree: `/opt/TGW/var/worktrees/1638-1639-nats-client-fixes`
Branch: `todo/1638-1639-nats-client-fixes`

Working on `src/tgw/apis/nats_client.py`:
1. #1638 step 2: make `_ensure_streams()` read-only (stream_info() checks
   only, log.error() if missing, no add_stream()), remove stale
   max_bytes -1 comment. Flake side (nats.nix QUEUE_TRANSITIONS stream) is
   a separate agent's concern, not touched here.
2. #1639: fix nested asyncio.run() crash in check_nats()/query_mutations()
   via a dedicated-thread `_run_isolated()` helper.

Not touching background-publisher thread machinery. Will verify via
worker restart + throwaway nested-loop repro script (announced per E9,
not committed).
