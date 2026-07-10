## Phase 5 — AI operations layer

### PP-AIOPS-001 — Cat-Herding Platform (adopted 2026-06-19)

**Full spec:** `plan/PP-AIOPS-001-cat-herding-platform.md`
**Status:** PLANNED — Stages 1–4 execute after Stage 1 (API fence); Stage 5 after PP-NIXOS-001.
**Core problem:** The platform operated without an audit trail for data changes. Photo renames,
field regressions, and pipeline failures were discovered by symptoms, not by observing causes.

**Phases 1–4** run on MX Linux (no NixOS dependency):

- **Phase 1 — JetStream + ItemData Audit Stream (M, 1–2 sessions):** Install NATS JetStream
  (single-node native binary). Wire `items._write_field()` + asset management endpoints to
  publish to `ITEMDATA_MUTATIONS` stream (`itemdata.{sku}.{field}` / `itemdata.{sku}.asset.{name}`).
  Every data change has a timestamped, attributed record.

- **Phase 2 — Queue Transition Outbox (S, 1 session):** Wire `QueueWorker` to publish every
  job state transition to `QUEUE_TRANSITIONS` stream. Add `session_id` so Claude/Aider changes
  are individually attributable.

- **Phase 3 — Anomaly Detection Worker (M, 1–2 sessions):** Subscribes to both streams; applies
  rule library. Bad patterns (price→0, primary photo renamed, dead-letter spike, status regressed)
  detected within seconds. Critical anomalies fire desktop notifications.

- **Phase 4 — Litterbox Worker + MCP Audit Tools (L, 2–3 sessions):** Deploy `itemdata_scrub.py`,
  `photo_history_recovery.py`, new `litterbox.py` (auto-fix library: 503 requeue, photo rename
  repair, negative qty, LEASE_EXPIRED requeue, stale template prefix). Extend `tgw-mcp-server`
  with `tgw_audit_trail`, `tgw_session_diff`, `tgw_anomaly_log`, `tgw_litterbox_log`,
  `tgw_mutation_rate`. One-time repair: feed remaining photo-rename victims through recovery.

**Phase 5 — AI Session Isolation (L–XL, after PP-NIXOS-001):**
Each AI task gets a pre-task Btrfs CoW snapshot of `/opt/TGW/src` + `/opt/TGW/data`, runs in
ephemeral nspawn with `--private-network`, communicates results via FIFO pipe. Host supervisor
validates change scope, promotes snapshot or discards. cgroup v2 watchdog kills runaway
containers. After Phase 5: bad agent sessions roll back in one command.

### Ollama job manager
- Serializes model jobs (one model loaded at a time, 32GB CPU-only)
- A queue worker that owns the Ollama lock
- Uninstall redundant models (llava, minicpm-v, moondream, etc.)
### AI work-distribution + usage monitoring
- Priority #2 deliverable
- Track which model did which job, time + token/compute cost
- Interface to see usage across Claude / Perplexity / Gemini / Ollama
- Feeds the "cost per item" and electricity-cost goals
### History merge worker (PP-ADD-003)
- Background queue worker: aggregate, deduplicate, and organize item history by SKU
- Per-SKU event log (event type, timestamp, source, actor, payload)
- Incremental merge on new events; full rebuild on demand
- Prerequisite: PP-ADD-005 SKU normalization complete or running in parallel
### Picklist generator (PP-ADD-009)
- Replace phone-app-based picklist generation
- Input: order IDs → output: pick list sorted by location/bin
- Print-ready PDF + QR code option encoding picklist_line data
- Trigger from GUI app (Phase 6) or standalone web page
- Keep plain-text picklist_line as fallback during transition

