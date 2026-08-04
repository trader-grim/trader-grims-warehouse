## PP-CLIP-001 — TGW-Aware Clipboard Manager (local-only, ratified 2026-07-11)

### Status: Phase 1 + Phase 2 COMPLETE. Local-only scope RATIFIED 2026-07-11 — Phase 3 RETIRED, see below.

**Phase 1 delivered:**
- `tgw-clipd` daemon: dual-backend (X11/XFixes + Wayland/wl-paste), mixed-session 'both' mode,
  SQLite history, `tgw clip {list,last-sku,search,wipe,get}` CLI, systemd user service
- SKU regex fixed to match 15- and 17-digit SKUs
- `tgw` fish wrapper bypasses sudo for `clip` subcommand (DB is per-user in /home/db)
- `nix/qtile/autostart.sh` imports session env on login so service always has DISPLAY/WAYLAND_DISPLAY

**Phase 2 delivered:** rofi/dmenu history picker (classic clipboard manager UI),
`DONE-1055-clip-picker.md`.

**GATE (Dave, 2026-07-02 session 40) — CLEARED 2026-07-11:** the conceptual
planning pass (todo #1086) that had to come before Phase 2/further clipboard
tooling has run and RATIFIED the local-only split (see Status line above and
`docs/ai-plans/clipboard-concept.md`) — cross-machine sync moved to
PP-EVENTD-001, tgw-clipd/rofi stay local-only forever. Todo #1055 (rofi
picker) is DONE, no longer gated.

**Decisions (session 28, revised 2026-06-28 session 33):**
- **Wayland primary; X11 compatibility if we are lucky.** Original decision held X11 as the
  stable platform. Reversed after nine hours of X11/Wayland clipboard debugging — the cost of
  straddling both planes exceeds any maturity advantage X11 still holds. Wayland-native paths
  (wl-paste, zwlr-data-control, libei, lan-mouse) are the design target going forward.
  X11/XFixes backend remains in clipd.py for XWayland fallback but is no longer the default.
- **Dual-backend watcher architecture retained.** The daemon core (on change → classify → write
  SQLite → socket push) is backend-agnostic; watcher backends: (a) **Wayland** via `wl-paste
  --watch` subprocess (primary), (b) **X11/XFixes** via python-xlib (XWayland fallback).
  Session-type detection at startup (`$WAYLAND_DISPLAY` / `XDG_SESSION_TYPE`) selects backend.
- Phase-1 open questions answered: watch **both PRIMARY and CLIPBOARD** (highlight-capture of
  SKUs is the stated use case); DB at `~/.local/share/tgw-clip/` (per-user). The SQLite store +
  `tgw clip` CLI already shipped (session 15 R18) — the daemon feeds the existing store.
- Build timing: after the Qtile install (admin #20) so the daemon has its consumer. Round 7 todo #113.

### Background
Identified during PP-WM-001 (Qtile) session (2026-06-05). Immediate need is met by
**Clipster** (flat-file history, long buffer) installed today. PP-CLIP-001 is the
next-generation replacement: a TGW-specific clipboard daemon that understands SKUs,
is event-driven, and exposes its history to the rest of the system.

### Problem with existing tools
- **Polling-based** (xclip, Clipster): 1–2s lag; CPU burn; TGWSKUWidget misses rapid copies
- **Not TGW-aware**: no concept of SKU vs. random text; history is undifferentiated
- **No queryable API**: macroboard and chord actions can't reliably ask "what was the last SKU?"
  — if you've since copied something else, the SKU is gone from live clipboard
- **No persistence across sessions**: most tools lose history on logout

### Core concept
An X11 event-driven daemon (`tgw-clipd`) written in Python that:
1. Receives push notifications from X11 when clipboard ownership changes
   (XFixes `select_selection_input` — zero polling, instant response)
2. Fetches the new clipboard content and classifies it
3. Writes to a local SQLite database with TGW-aware tagging
4. Exposes a Unix socket so Qtile widgets and CLI tools can subscribe/query

### X11 event mechanism
```python
from Xlib import X, display
from Xlib.ext import fixes

dpy = display.Display()
screen = dpy.screen()
fixes.query_version(dpy)

# XFixes sends XFixesSelectionNotifyEvent when clipboard owner changes
fixes.select_selection_input(
    dpy, screen.root, dpy.get_atom('CLIPBOARD'),
    fixes.SetSelectionOwnerNotify
)
# Main loop: dpy.next_event() blocks until clipboard changes — no polling
```
`python-xlib` package. Similar for PRIMARY selection (highlight-to-copy).

### SQLite schema
```sql
CREATE TABLE clip_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL NOT NULL,          -- Unix timestamp
    content     TEXT NOT NULL,
    content_len INTEGER NOT NULL,
    selection   TEXT NOT NULL,          -- 'clipboard' or 'primary'
    is_sku      BOOLEAN DEFAULT 0,      -- matched tgw\d{15}
    sku         TEXT,                   -- extracted SKU if is_sku
    app_name    TEXT,                   -- _NET_WM_NAME of clipboard owner (X11)
    dismissed   BOOLEAN DEFAULT 0       -- user-dismissed from history
);
CREATE INDEX idx_sku      ON clip_history (sku) WHERE is_sku = 1;
CREATE INDEX idx_captured ON clip_history (captured_at DESC);
```
DB path: `~/.local/share/tgw-clip/history.db`
Retention: configurable max rows (default 10,000); SKU rows never auto-expire.

### CLI surface
```
tgw-clip list [--limit N] [--sku-only]   # show history
tgw-clip last-sku                         # most recent SKU, regardless of current clipboard
tgw-clip search <pattern>                 # grep history
tgw-clip wipe                            # clear non-SKU history
tgw-clip daemon [--foreground]           # start/stop daemon
```

### Clipboard action surface (session 9 additions)
Requested actions to expose from clipboard context (via macroboard, Qtile chord, or tgw-clip CLI):

| Action | Description |
|--------|-------------|
| edit | Open current clip content in $EDITOR |
| send-to-suggest | Append current clip as `tgw suggest "..."` entry |
| sku-actions | If clip matches SKU: lookup, locate, open photos, add to picklist, set-template |
| location-actions | If clip matches location format: open folder, move all, view items |
| save-to-research | Tag and save clip to a "research" bucket (PERPLEXITY brief material) |
| save-to-personal | Save clip to personal notes (outside TGW pipeline) |
| save-to-sku | Associate clip with current SKU's item JSON (e.g. a URL, note, or reference) |
| combine-clips | Merge recent N clips into one buffer (for building multi-field entries) |
| split-clips | Split current clip by delimiter (line, comma, tab) into individual history entries |
| snippets | Named snippet storage + recall ("shipping boilerplate", "common titles", etc.) |
| long-history | Full history browser with search; backup to file; restore on login |

Design note: these actions are best exposed as a tgw-clip action menu (dmenu/rofi) triggered
from the macroboard `C` key or Qtile chord. The daemon provides the history; the action menu
provides the surface. SKU and location detection gates which actions are shown.

### Qtile integration (replaces polling in TGWSKUWidget)
- Daemon exposes a Unix socket at `~/.local/run/tgw-clipd.sock`
- `TGWSKUWidget` connects to socket on startup; receives push events (JSON lines)
- No more 2-second poll loop; widget updates instantly on clipboard change
- Fallback: if daemon not running, widget falls back to xclip polling (current behavior)

### tgw-macro / chord integration
- Super+T → c: calls `tgw-clip last-sku` instead of reading live clipboard
  → SKU persists across subsequent copies; chord action is reliable even after clipboard changes
- macroboard `g` / `h` / `c` keys: same `tgw-clip last-sku` fallback
- `tgw suggest "$(tgw-clip last-sku)"` pattern: capture SKU to plan inbox

### App-name tagging (Phase 2 idea)
X11 allows reading `_NET_WM_NAME` of the focused window at copy time. This means:
- "copied from Gwenview" → likely a file path → tag as `source=media`
- "copied from terminal" → likely a command or SKU → higher SKU detection priority
- "copied from Firefox" → likely a URL → tag for future eBay browse integration

### systemd unit
```ini
[Unit]
Description=TGW clipboard daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/tgw-clipd
Restart=on-failure

[Install]
WantedBy=default.target
```
User service: `systemctl --user enable --now tgw-clipd`

### Dependencies
- `python-xlib` (apt: `python3-xlib`)
- `python3-sqlite3` (stdlib)
- PP-WM-001 (Qtile) — the widget integration is Qtile-specific

### Settled architecture decisions (updated 2026-06-29 sessions 37+38)

**WM/KVM stack: Sway + lan-mouse (migration COMPLETE session 36)**
- Qtile → Sway (DONE). Input Leap → lan-mouse (DONE).
- Input Leap nix modules removed from flake (session 38); orphan process killed.
- lan-mouse is **true peer-to-peer** — no master/server node. Both machines can use either
  keyboard or mouse freely. Both machines have config files listing the other as a client.
- Clipboard is intentionally excluded from lan-mouse's core (keeps it lean/fast).
- Clipboard sync via **lan-mouse hooks** — `enter_hook` fires on the machine the cursor just
  left, so `wl-paste` gets the right clipboard.

**Clipboard truncation bug — FIXED 2026-06-29 session 38**
- Root cause: Firefox running under XWayland; XWayland clipboard bridge truncates
  multi-paragraph content at double newlines.
- Fix: `MOZ_ENABLE_WAYLAND=1` in `environment.sessionVariables` in `sway.nix`.
  Confirmed working on a1131. tgw-prod rebuild still pending.
- Secondary cause found during investigation: Input Leap orphan process (zombie from
  prior activation) was also intercepting clipboard. Removed from flake; process killed.

**Cross-machine clipboard sync — Phase 3 RETIRED 2026-07-11, superseded by PP-EVENTD-001**
This section described extending tgw-clipd itself with a Unix socket +
cross-machine fan-out — that plan is now the SAME job PP-EVENTD-001's
`clip-route` daemon does, described twice. Ratified split (#1086 pass,
2026-07-04, formally confirmed 2026-07-11): **tgw-clipd stays local-only
forever.** `lan-mouse enter_hook` calls `clip-route --target` directly;
`clip-route` reads the clipboard itself (`wl-paste -n`) and never routes
through tgw-clipd. See `reference/PP-EVENTD-001-design.md` for the actual
design (Go binary, Postgres `clipboard_states`, KDE/Android/GDrive/Recoll
fan-out) and the "Radar" active-context requirements layered on it
2026-07-11.

**Barcode reader as shared peripheral (insight 2026-06-29)**
- Barcode readers are USB HID keyboard devices physically on tgw-prod.
- Via the event endpoint in tgw-clipd: scan → classify as SKU → push to all endpoints.
- Tasker receives SKU → triggers lookup workflow without manual copy/paste.
- Effectively makes the physical reader a shared cross-platform peripheral at zero hardware cost.

**tgw-eventd (PP-EVENTD-001) — UNFROZEN 2026-07-11, #1086 gate cleared, owns all cross-machine sync**
- Full event server with PostgreSQL state machine (LISTEN/NOTIFY, not NATS —
  see design doc), typed event schema, git-annex data plane, WebSocket
  Flutter HUD, pm_intake (→ Tigwa) event subscriber, "Radar" active-context
  automation. Design in `reference/PP-EVENTD-001-design.md`.
- Prerequisite (this doc's Phase 2) is DONE — Phase 1 there is now
  unblocked. This doc's own former Phase 3 is retired in its favor, not a
  future informant of it.

**lan-mouse hook config (both machines, symmetric):**
```toml
[[clients]]
hostname = "<other-machine>"
enter_hook = "~/.config/lan-mouse/hooks/push-clipboard.sh"
```

### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Daemon: dual-backend, SQLite, SKU tagging, CLI, systemd service | ✅ DONE 2026-06-24 |
| 2 | rofi/dmenu history picker | ✅ DONE (todo #1055) |
| 2.5 | Diagnose + fix multi-paragraph clipboard truncation on Sway | ✅ DONE 2026-06-29 (MOZ_ENABLE_WAYLAND; Input Leap removed) |
| 3 | ~~Unix socket endpoint in tgw-clipd + lan-mouse hook scripts~~ | **RETIRED 2026-07-11 — superseded by PP-EVENTD-001, see above** |
| 4 | Tasker Android integration | now owned by PP-EVENTD-001 (Android/Tasker delivery leg) |
| 5 | App-name tagging; macroboard `last-sku` fallback | local-only, still valid here, unscheduled |
| 6 | eBay URL detection → auto-link to item JSON | now overlaps PP-EVENTD-001's Radar quick-actions |

### Phase 2 design — rofi history picker
Keybind (e.g. Super+V or macroboard key) launches a rofi menu showing clipboard history.
Selecting an entry copies it back to clipboard and optionally pastes immediately.

```bash
tgw clip rofi   # or: rofi -dmenu fed from tgw clip list output
```

Options:
- **rofi** — full fuzzy search, previews, custom theme; most capable
- **dmenu** — lightweight, no dependencies beyond dmenu; simpler

Entry format in picker: `[SKU] tgw20260624...  |  2026-06-24 18:09` or plain content preview.
SKU entries pinned to top or visually distinguished.
On select: `tgw clip get --id N --copy` (already implemented) puts content back in clipboard.
Optional immediate paste: `xdotool key ctrl+v` or `wl-paste` after copy.

### Open design questions (decide before Phase 1)
- PRIMARY vs CLIPBOARD selection: watch both or just CLIPBOARD? Primary = highlight-select,
  clipboard = explicit Ctrl+C. For SKU capture, PRIMARY is more useful (highlight in terminal).
  Cost: twice the events to process.
- DB location: `~/.local/share/tgw-clip/` or alongside the TGW data tree? Lean toward
  `~/.local` since this is per-user, not per-installation.
- Daemon restart on config change: reload via SIGHUP or restart unit?
- Max content length to store: truncate at 10KB? Avoids storing accidental large pastes.
- Notify on new SKU detection: `notify-send` from daemon, or let Qtile widget handle it?

---

