## PP-EVENTD-001 — TGW Event Server

**Status:** Design research complete 2026-06-29. Future item — do not implement until
PP-CLIP-001 Phase 3 (simple hook sync) is running and the truncation bug is resolved.

Full design: `reference/PP-EVENTD-001-design.md`

**What this is:** The long-horizon replacement for the simple Phase 3 hook approach.
Once we have working cross-machine clipboard sync and understand the real bottlenecks,
PP-EVENTD-001 evolves tgw-clipd into a proper event server. The Phase 3 hook informs
whether a compiled hook binary (Go/Rust) is actually needed or if shell is fast enough.

**Core concept:** lan-mouse is a trigger, not the platform. A central event router
receives events from any producer (lan-mouse hooks, barcode scanners, pipeline workers,
eBay webhooks) and distributes to any consumer (Flutter HUD, Android/Tasker, pm_intake).

**Key architectural decisions settled in research (sessions 37+38):**
- PostgreSQL (`state_machine` db) for event queue — not SQLite; LISTEN/NOTIFY for workers
- Unix socket IPC: hook CLI → daemon (< 2ms, never blocks mouse tracking)
- Implementation language for hook binary: Go or Rust (Python too slow for hook CLI cold-start)
- Daemon itself can stay Python (long-running, startup cost paid once) or move to Go/Rust
- git-annex + Google Drive data plane for large payloads; events carry hashes only
- Near-serverless: GitHub (control plane) + Google Drive (data plane) + NixOS flake

**Key capabilities when eventually built:**
- Barcode reader shared across all platforms at zero hardware cost
- Android/Tasker clipboard via HTTP (replaces KDE Connect; store-and-forward for offline)
- Flutter HUD via WebSocket
- pm_intake as fsnotify subscriber (event-driven, not queue-polling)
- Google Drive direct API in Go — potential 3x photo upload speed vs current gdrive_sync.py

---

