## PP-EVENTD-001 — TGW Event Server

**Status: UNFROZEN 2026-07-11, #1086 gate cleared** (corrected 2026-07-12,
Fable independent review #1338 — this stub still said frozen pending
PP-CLIP-001 Phase 3, but Phase 3 was retired the same session this was
meant to be unfrozen in). PP-CLIP-001 Phase 3 (cross-machine hook sync) is
RETIRED, not a prerequisite — its scope moved entirely into this PP. Phase 1
here is unblocked now that PP-CLIP-001 Phase 2 (rofi picker) is DONE. See
`reference/PP-EVENTD-001-design.md` for the current, authoritative design —
this stub is history/background only, do not treat it as current status.

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

