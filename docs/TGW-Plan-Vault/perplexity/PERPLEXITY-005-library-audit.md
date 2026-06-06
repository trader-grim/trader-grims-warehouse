# PERPLEXITY-005 — TGW Platform Library & API Audit

**Date:** 2026-06-06
**Track:** 3 (Perplexity research)
**Purpose:** Review all libraries and APIs the TGW platform uses or plans to use,
identify improvement opportunities, missed integrations, and better alternatives.

---

## Context

TGW (Trader Grim's Warehouse) is a Python-based resale inventory automation platform.
Key stack: FastAPI, PostgreSQL (psycopg2), SQLite (sqlite3), Ollama (local LLM),
eBay Trading API + Inventory API (OAuth 2.0), Syncthing (file sync), KDE Connect
(mobile relay), rclone (cloud backup), systemd (workers), xclip/wl-copy (clipboard).

---

## Research Questions

### 1. Syncthing Python integration
- Is there a mature Python library for Syncthing's REST API?
  - Known options: `syncthing-py` (PyPI), raw `requests` against `localhost:8384`
  - What does the REST API support: device status, folder sync progress, event stream,
    triggering manual scans, conflict detection?
  - Is there an async client (`httpx` / `aiohttp`) suitable for use in a FastAPI service?
  - What events are available via the `/events` endpoint and are they suitable for
    triggering TGW catalog rebuilds when Syncthing syncs new files?

### 2. KDE Connect Python integration
- Is there a Python library for KDE Connect beyond `kdeconnect-cli`?
  - Known approaches: DBus (`pydbus`, `dbus-python`), REST API (KDE Connect has a
    companion HTTP server plugin), or direct `kdeconnect-cli` subprocess calls
  - What clipboard sharing, notification, and file-send capabilities are available
    via Python?
  - Is there a way to subscribe to KDE Connect clipboard events in Python (push vs. poll)?
  - Any libraries: `kdeconnect` PyPI, or community bindings?

### 3. Database / state machine
- PostgreSQL via psycopg2 (sync). Is there a migration path to psycopg3 or asyncpg
  for the worker base? What are the practical tradeoffs for a low-concurrency,
  mostly-sequential worker pattern?
- SQLite via sqlite3 (stdlib). Is `aiosqlite` worth adopting for async catalog reads
  in the FastAPI service?

### 4. HTTP server
- FastAPI + uvicorn: any recent (2025–2026) concerns or better alternatives for a
  low-traffic single-machine API?

### 5. LLM / AI stack
- Ollama Python client (`ollama` PyPI, v0.x): is this the standard library for
  Ollama REST API, or is raw `requests` better for control?
- `python-xlib`: any active maintenance concerns? Alternatives for X11 clipboard
  events in 2025–2026?
- Whisper.cpp integration: any Python binding newer than `whisper-cpp-python`?

### 6. Product lookup / enrichment
- `discogs_client` (PyPI): is this the official library? Is the personal-access-token
  flow stable?
- Any newer barcode/UPC lookup libraries that outperform upcitemdb free tier?
- PriceCharting API: is there a Python wrapper, or is raw `requests` the approach?

### 7. eBay API libraries
- `ebaysdk-python` (official): is it still maintained in 2025–2026? Does it support
  all the REST APIs (Inventory, Buy, Analytics) or just Trading?
- Any community libraries that better cover eBay's REST Inventory API?

### 8. Missed integrations / opportunities
- For a resale warehouse with 55K+ items, is there anything obvious we're missing?
  Examples: barcode scanner libraries, shipping rate APIs (PirateShip, EasyPost),
  price comparison services, accounting APIs.
- Any Python libraries for USB scale reading (HID) that are more robust than
  shell-based approaches?

---

## Expected Output

A markdown document covering:
1. Best Python library/approach for each question, with citations
2. Anything that has changed significantly in 2025–2026
3. Any obvious gaps or missed opportunities for the TGW stack
4. Prioritized action list (which integrations to add first)

Save result as `PERPLEXITY-005-result.md` in `docs/TGW-Plan-Vault/inbox/` for PM-intake.
