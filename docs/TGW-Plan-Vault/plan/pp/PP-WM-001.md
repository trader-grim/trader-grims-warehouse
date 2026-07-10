## PP-WM-001 — Sway Tiling Window Manager

### Vision
Sway as the primary operator workstation shell — wlroots Wayland compositor, i3-compatible
config, best stability record of any Wayland compositor. TGW dashboard lives in **waybar** as
custom JSON modules polling `tgw health` and `tgw queue status` — cleaner separation than
embedding logic in the WM process. **Wayland primary; X11/XWayland retained where it comes for
free, not chased** (decision 2026-06-28: nine hours of clipboard debugging made straddling both
planes clearly not worth it).

**lan-mouse** replaces Input Leap as KVM/clipboard bridge — Wayland-native, wlroots peer-to-peer.
Sway uses the layer-shell + pointer-constraints capture path (no ext-input-capture-v1 yet).

Chosen over: Qtile (retired 2026-06-28 — Wayland clipboard instability, ran in XWayland under
its own compositor), Hyprland (good IPC, has ext-input-capture-v1 for lan-mouse, but churn risk
for a production workstation — revisit if it matures before Sway gains ext-capture), AwesomeWM
(X11-only), XMonad (Haskell overhead).

### Files
| File | Purpose |
|------|---------|
| `etc/interfaces/qtile/config.py` | Main Qtile config — layouts, keybindings, bar, hooks |
| `etc/interfaces/qtile/tgw_widgets.py` | Custom widgets: TGWQueueWidget, TGWHealthWidget, TGWSKUWidget |
| `etc/interfaces/qtile/install.sh` | User-level installer (run as desktop user, not root) |

### Phase 1 — Base config ✅ DONE (2026-06-05)
- **TGWQueueWidget** — polls `GET /api/queue/status` via tgw-http REST; shows pending/dead with
  color coding; click opens health terminal; API key from `~/.config/tgw/api-key`
- **TGWHealthWidget** — `systemctl list-units` for all `tgw-worker@*` + `tgw-http`; shows
  active/total ratio; color: green=all up, amber=some down; click opens unit list
- **TGWSKUWidget** — polls clipboard every 2s via `wl-paste` (Wayland primary) or xclip fallback;
  pattern matches `tgw[0-9]{15}`; shows SKU in accent color when detected; click or Super+T→c triggers lookup action
- **Super+T chord mode** — TGW command layer (bar shows `[ TGW ]`); keys: h=health, q=queue
  depths, s=staged, t=todo, v=velocity-report, c=clipboard SKU action, o=open ItemData in
  Dolphin, 1-2=pipeline triggers, F2/F4=workspace jump, Escape=exit mode
- **F12 scratchpad** — floating konsole (55% height, 85% width); always-available TGW shell
- **5 named workspaces**: shell / tgw / ebay / agents / media
- **Layouts**: MonadTall (default, 55% main), MonadWide, Columns(3), Max
- **autostart hook** — runs `~/.config/qtile/autostart.sh` on first launch (compositor stub)
- **Install**: `bash etc/interfaces/qtile/install.sh` (as desktop user); symlinks configs from
  repo; apt installs qtile + xclip + dmenu; copies API key

### Phase 2 — TGW integration depth (future)
- TGW-mode key `c` + SKU action menu: kdialog for choice (lookup / re-enqueue / open photos)
- Clipboard SKU watcher: emit `notify-send` on first detection of new SKU
- `tgw-notify` hook: workers emit `notify-send` on completion → Qtile `net_wm_state` hook
  catches notification window → updates a notification counter widget in bar
- Workspace 2 (tgw): auto-launch MC on startup, or a tgw dashboard tmux session
- Workspace 4 (agents): auto-launch Claude Code on startup

### Phase 3 — Workflow automation (future)
- Macroboard `[tgw_layer]` integration: once macroboard is live, key chord in config should
  mirror macroboard layout so both inputs do the same thing
- Quiet-queue hook: when all workers idle, surface `tgw todo claude` in a notification or
  dedicated scratchpad (connects PP-CAPTURE-001 quiet-queue concept)
- Photo intake workspace auto-route: when Gwenview or camera tool opens, auto-assign to ws5

---

- Phase 2 (TGW integration depth) — Sway TGW-ify: env imports, permissions, a1131 setup. Flutter app startup fix (portal bypass). Full details in `dev-workflow/research/RESEARCH-sway-flutter-startup.md`.
