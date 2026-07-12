# PP-EVENTD-001 — TGW Event Server Design ("Radar")

**Status:** Design complete (2026-06-29 sessions 37+38). Not yet implemented.
**Prerequisite: PP-CLIP-001 Phase 2 (rofi picker) — DONE (#1055).** Phase 1
of this doc is now unblocked.
**Implementation language:** Go
**2026-07-11 update:** the long-dormant #1086 unification gate is ratified
this session — see "Ratified two-track split" and "Radar / active-context
requirements" below. This IS "our real Radar O'Reilly" (Dave) — anticipates
before being asked; write a SKU to the clipboard, the system already has an
answer waiting.

---

## Ratified two-track split (2026-07-11, resolves the #1086 conceptual pass)

The #1086 pass (`docs/ai-plans/clipboard-concept.md`, 2026-07-04) found
PP-CLIP-001 Phase 3 and this whole doc described the SAME cross-machine-sync
job twice, and recommended splitting by machine-scope. That recommendation
was never formally ratified — **ratified now**:
1. **tgw-clipd (Python, local-only forever)** — owns local history, SKU/
   location classification, per-machine SQLite. Never grows a cross-machine
   socket.
2. **Rofi picker (#1055, local-only)** — reads only through the stable
   `tgw clip {list,get,last-sku}` CLI contract. Already correctly designed.
3. **Hook sync retargets entirely to `clip-route`** — PP-CLIP-001's Phase 3
   line is retired (see that doc). `lan-mouse enter_hook` calls
   `clip-route --target` directly; `clip-route` reads the clipboard itself
   (`wl-paste -n`), does not route through tgw-clipd.
4. **This doc (event server, cross-machine only)** — owns Postgres
   `clipboard_states`, KDE/Android delivery, git-annex/GDrive, barcode
   fan-out, Flutter HUD WebSocket, pm_intake (→ Tigwa, PP-HERMES-EA-001)
   subscriber.

---

## Core insight

**lan-mouse is a trigger, not the platform.** The real value is the communication
protocol underneath. Swap lan-mouse for a Sway workspace hook, a hardware button, a
barcode scan — the event server doesn't care. This is what makes it portable.

---

## IPC architecture (settled)

```
[lan-mouse enter_hook]
        │
        ▼
[clip-route --target kde]   ← Go CLI binary, sub-millisecond
        │ Unix socket (< 2ms, never blocks mouse tracking)
        ▼
[clip-route --daemon]       ← Go background daemon
        │
        ├──► PostgreSQL state_machine (clipboard_states table)
        │         └──► LISTEN/NOTIFY → sync workers
        ├──► HTTP POST → KDE/a1131 (qdbus → klipper)
        ├──► HTTP POST → Android/Tasker
        ├──► git-annex plumbing (large payloads)
        └──► Recoll index update
```

Key design decision: **hook fires CLI → Unix socket → daemon**. The CLI returns in
under 2ms so lan-mouse screen transitions are never blocked. All network I/O, database
writes, and file operations happen asynchronously in the daemon.

---

## Why Go

- Fast cold-start (no interpreter overhead during instantaneous screen hops)
- Concurrent standard library: goroutines for fan-out, channels for worker pools
- Same codebase will implement git-annex plumbing, Recoll ingestion, Google Drive API
- Single compiled binary: `clip-route` handles both `--daemon` and `--target` modes

---

## PostgreSQL state machine (settled)

**Not SQLite.** TGW already runs PostgreSQL (`state_machine` database). The event
daemon uses the same instance — shares infrastructure, gets LISTEN/NOTIFY, transactional
dedup, and fault-tolerant retry.

```sql
CREATE TYPE delivery_state AS ENUM ('pending', 'processing', 'completed', 'failed');

CREATE TABLE clipboard_states (
    id               BIGSERIAL PRIMARY KEY,
    payload          TEXT NOT NULL,
    hash             VARCHAR(64) UNIQUE,      -- SHA-256 dedup: skip identical consecutive clips
    kde_sync_status  delivery_state DEFAULT 'pending',
    android_sync_status delivery_state DEFAULT 'pending',
    annex_key        TEXT,                    -- set when payload stored in git-annex
    created_at       TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_pending_kde     ON clipboard_states (kde_sync_status)     WHERE kde_sync_status = 'pending';
CREATE INDEX idx_pending_android ON clipboard_states (android_sync_status) WHERE android_sync_status = 'pending';
```

Use **LISTEN/NOTIFY** (`LISTEN clipboard_inserted`) to eliminate polling tickers.
Worker goroutines wake on notification, fetch pending rows with `SELECT ... FOR UPDATE SKIP LOCKED`.

### Fault tolerance
If Android is offline: row stays `pending`, worker retries on reconnect.
If KDE client restarts: same pattern.
Decoupled: lan-mouse hook only writes to DB socket (< 2ms). All network calls are lazy.

---

## Go binary structure (`clip-route`)

```
cmd/clip-route/
  main.go          -- flag parsing: --daemon | --target <kde|android|...>
  daemon/
    daemon.go      -- Unix socket listener, goroutine pool, DB pool
    ingest.go      -- IngestState(): SHA-256 dedup → INSERT ON CONFLICT
    workers.go     -- processPendingKde(), processPendingAndroid()
    annex.go       -- git-annex plumbing for large payloads
    recoll.go      -- Recoll index update via recollindex -e <file>
  client/
    client.go      -- CLI mode: read wl-paste, write to Unix socket
  config/
    config.go      -- target registry: kde addr, android addr, socket path
```

`--daemon` mode: bind Unix socket, open pgx pool, start worker goroutines, block.
`--target` mode: read `wl-paste -n`, hash, write JSON to Unix socket, exit.

---

## lan-mouse config.toml

```toml
[[clients]]
hostname = "a1131"
enter_hook = "/home/db/bin/clip-route --target kde"

[[clients]]
hostname = "android-phone"
enter_hook = "/home/db/bin/clip-route --target android"
```

The enter_hook fires on the machine the cursor just **left** (tgw-prod). That machine
holds the clipboard. `wl-paste` reads it correctly there.

---

## KDE/a1131 delivery

Lightweight Go HTTP microservice on a1131 (port 8080). Receives POST from daemon,
pipes payload into klipper:

```bash
qdbus org.kde.klipper /klipper org.kde.klipper.klipper.setClipboardContents "$PAYLOAD"
```

---

## Android/Tasker delivery

Tasker HTTP Server plugin listens on `http://<phone-ip>:<port>/event`.
Daemon POSTs JSON: `{"action": "set_clip", "data": "..."}`.
Tasker task: parse `%http_data` → Set Clipboard.
Battery: disable Android battery optimizations for Tasker; sub-100ms latency on LAN.
Replaces KDE Connect clipboard (was 1-in-30 success due to Wayland focus isolation).

---

## git-annex + Google Drive data plane

For large payloads (images, scan captures):
- Daemon calls `git annex add <file>` → stores in `~/vault/tgw-annex/`
- Writes `annex_key` back to `clipboard_states` row
- Async push: `git annex copy --to=gdrive --fast &`
- Clients hydrate on demand: `git annex get <file> --from=gdrive`

Events carry annex key only — never binary content over the socket.

Go workers for:
- git-annex plumbing (shell out to `git annex` or use plumbing protocol)
- Recoll index: `recollindex -e <file>` after annex add
- Google Drive Workspace API: direct API option for faster uploads (see below)

---

## Google Drive upload speed

The research session identified that Go-native Google Drive API calls are significantly
faster than the current Python `gdrive_sync.py` approach. Go's concurrent HTTP client
with chunked resumable uploads via the Drive API (`googleapis/google-api-go-client`)
avoids Python interpreter overhead and enables parallel chunk uploads.

**TODO:** Benchmark Go Drive API vs current `gdrive_sync.py` for ItemData photo uploads.
If 3x improvement confirmed, migrate PP-PHOTO-001 sync to the Go daemon as well.

---

## Barcode reader as shared peripheral

Barcode readers are USB HID keyboard devices on tgw-prod. With clip-route daemon:
- Scan → tgw-clipd captures → classified as SKU by regex → clip-route `--target all`
- All subscribers receive SKU: a1131, Android/Tasker, Flutter HUD
- Physical reader becomes cross-platform at zero hardware cost
- Same pattern: RFID readers, label printers, USB stamp events

---

## NixOS packaging

```nix
# In home.nix or sway.nix:
home.packages = [ pkgs.clip-route ];   # built from flake overlay

systemd.user.services.clip-route-daemon = {
  description = "Go Clipboard Router and Knowledge Pipeline Daemon";
  after       = [ "graphical-session.target" ];
  wantedBy    = [ "graphical-session.target" ];
  serviceConfig.ExecStart = "${pkgs.clip-route}/bin/clip-route --daemon";
};
```

---

## Flutter HUD integration

**CORRECTED 2026-07-11 — the "already implemented" claim below was false,
found during a deep architecture review of `apps/tgw_app/` triggered by
Dave asking for a cohesive assessment of PP-PORTABLE-CATALOG-001.**
Exhaustively checked: no `HttpServer`, no `shelf`, no WebSocket/SSE client,
no push-notification setup, no background-task wiring anywhere in the
Flutter app. It is a pure HTTP *client* (Dio, request/response only) —
there is **no listener of any kind**, and zero server-initiated
communication exists today. This was never built; the original text below
overstated the state of the codebase and should not have been trusted
without verification (the same "marked/described as done without checking"
failure mode found elsewhere this session — see PP-PORTABLE-CATALOG-001.md).

Original (aspirational) design intent, still the right shape, just not
built — kept for reference:
- `payload_type: sku` → show item lookup card
- `payload_type: clipboard_image` → show preview (inline or annex fetch)
- `payload_type: pipeline_event` → worker notification

This is now tracked as real, scoped work in `plan/pp/PP-PORTABLE-CATALOG-001.md`
("the backchannel") rather than an assumed-done integration point. Building
it makes the Flutter app a bidirectional event-bus participant, the same
pattern already specified for PP-INTAKE-004's camera app.

---

## pm_intake as event subscriber

When a `.md` file lands in `inbox/`, inotify (or Go `fsnotify`) fires an event →
daemon routes `document_change` to pm_intake process → pm_intake processes immediately
(no queue polling). Result notification routes back via Flutter HUD / Sway notification.

---

## Control plane vs data plane

| Layer | Tool | What it carries |
|-------|------|-----------------|
| Control plane | GitHub (NixOS flake) | State tables, system config, Flutter binary |
| Compute layer | tgw-prod Sway + clip-route daemon | Event routing, ingestion, processing |
| Data plane | git-annex + Google Drive | Encrypted binary blobs (images, large files) |

Near-serverless: GitHub + Google Drive + NixOS flake = no cloud VM required.

---

## Implementation phases

| Phase | Scope | Prereq |
|-------|-------|--------|
| 1 | Go `clip-route` binary: Unix socket IPC, PostgreSQL ingest, KDE + Android HTTP delivery | PP-CLIP-001 P2 |
| 2 | lan-mouse hooks wired to clip-route; cross-machine clipboard sync working | Phase 1 |
| 3 | Barcode/scanner events via clip-route | Phase 2 |
| 4 | git-annex + Google Drive data plane for large payloads | Phase 2 |
| 5 | Flutter HUD WebSocket subscriber | Phase 3 |
| 6 | Recoll index integration | Phase 4 |
| 7 | pm_intake as fsnotify event subscriber | Phase 5 |
| 8 | Google Drive direct API (Go) for ItemData photo uploads — benchmark vs gdrive_sync.py | Phase 4 |

---

## Radar / active-context requirements (2026-07-11)

**The spec — Dave's old `tgw.source`-era macro workflow, now automatic (this
is the acceptance bar):** keyboard macros set the active SKU → symlinks
flipped → item folder + item JSON became defaults for scripts acting on
that SKU → swipe/click bindings for related actions (open the eBay item/
edit page). "We would be making a better, more compact, more direct, more
automatic version of that."

**Ground truth on the old system (confirmed via code archaeology this
session):** `tgw.source`'s `tgwset()` did a non-atomic `rm`+`ln -sf` of
THREE symlinks: `CurrentItem` (item folder/photos), `CurrentItem.json`
(item JSON), `CurrentLocation` (catalog location dir — "all items in that
location"). Already partially rebuilt as **PP-CONTEXT-001** (DONE, session
23): `tgw set-context`/`get-context`/`clear-context`, real state file
(`runtime/state/current-item.json`), `CurrentItem`/`CurrentItem.json` kept
as an atomic derived compat view.

**Regression found this session — restore it:** `CurrentLocation` was
silently dropped when PP-CONTEXT-001 replaced the old symlink dance. One of
the things Dave explicitly named wanting back. Fix in `src/tgw/context.py`.

**Also found, low-priority, not blocking:** `tgwset_selected()` in
`tgw.source` still calls a now-nonexistent `tgw tgwset` subcommand
(orphaned since the `set-context` rebuild) — repoint to `tgw set-context`.

**What's different this time (Dave, 2026-07-11):**
> "Ours, as mentioned, would be event server to clipboard, more direct and
> accurate, and changing clipboard entries brings back context. Makes it
> into a real tool."

I.e. the trigger stops being an explicit macro/command and becomes
**implicit and bidirectional**: any recognized clipboard change
re-evaluates and swaps the active context automatically — copying a
different SKU (or URL/part-number) IS the "set" action. This is
`tgwset_selected()`'s original intent (X selection → context), now wired
through this daemon's clipboard-write event instead of manual invocation.

**Trigger scope — recognized content only:** SKUs, URLs, part/model
numbers, product-like text — reuse tgw-clipd's existing `is_sku`/regex
classification. Plain/incidental copies (passwords, random text) trigger
nothing. Avoids noise.

**Surface — fold into ActionConsole/tgw-http, NOT native per-DE widgets,
NOT the Flutter HUD.** Extends the already-settled "state drives interface,
controls are indicators, platform-wide" design (PP-ACTIONCONSOLE-001) with
an active-item panel (photos, JSON, links, location) — works identically on
tgw-prod (Sway) and a1131 (Plasma) with no DE-specific widget config to
build/maintain twice. (The old Plasma folder-view widget layout itself is
genuinely lost — desktop-side config on the pre-NixOS machine, never
captured in this repo — only the symlink targets it pointed at survive,
above.)

**Surfaced content candidate, not yet confirmed:** Dave floated including
pricing/research links (comps, sold listings) in what the panel surfaces
alongside item data — unreviewed Downloads doc `pricing-research-ui.md`
may be relevant; revisit when read into a session.

**"Research options" (external half) — not yet decided where it lands:**
likely routes through existing `PP-LOOKUP-001-APIs.md` (product enrichment/
barcode lookup) rather than new integrations, with Leotha (PP-HERMES-EA-001)
translating recognized content into a good research query.

## Key decisions not yet made

- **Google Drive account:** same as ItemData photo sync, or separate vault account?
- **Go module path:** `github.com/DaveBuko/clip-route` or internal to tgw repo?
- **Unix socket path:** `/run/user/<uid>/clip-route.sock` (XDG standard)
- **Annex key in events:** include in JSON to all subscribers or only when relevant?
