## PP-MACRO-001 — keyd Macroboard

### Vision
A dedicated keyboard (one of the four identical Dell USB keyboards) acts as a
single-touch macro board for TGW and eBay operations. Highlight a SKU, location,
or any identifier anywhere on screen, then press the matching macro key — no
Ctrl+C, no command typing. If nothing is highlighted, macros fall back to the
current item (`/opt/TGW/CurrentItem` symlink).

The TGW layer on the macroboard is the **canonical definition** for the eventual
all-keyboard sub-layer: once wired and proven here, the same `[tgw_layer]` block
gets added to `default.conf` and bound to a chord on all four keyboards.

### Files (all committed, ready to install)
| File | Purpose |
|------|---------|
| `etc/interfaces/keyd/tgw-macroboard.conf` | keyd config — device target + layer definition |
| `/opt/TGW/bin/tgw-macro` | Macro dispatcher — all action logic |
| `/opt/TGW/bin/tm` | Thin launcher — `runuser -u tgw` + env setup |

### ⚠ Install blocked — waiting for second keyboard
Cannot install until a second keyboard is connected so the macroboard keyboard
can be safely dedicated without losing console access. When ready:

```bash
# 1. Connect the second (normal use) keyboard first.

# NOTE: On Debian the keyd binary is named keyd.rvaiya (naming conflict with
# an unrelated Debian package). Package and service are still "keyd".
#   Binary:   /usr/bin/keyd.rvaiya
#   Service:  keyd.service  (systemctl start/stop/reload keyd)
#   Config:   /etc/keyd/

# 2. Identify the macroboard's unique device ID:
keyd.rvaiya list-devices
# Look for "Dell Dell USB Keyboard" entries. Both show as 413c:2105.
# The one on the dedicated USB port will have a distinct path/serial hash.
# Example output line: "413c:2105:a1b2c3d4e5f6  Dell Dell USB Keyboard"

# 3. Edit the config to target the correct device:
sudo nano /opt/TGW/src/trader-grims-warehouse/etc/interfaces/keyd/tgw-macroboard.conf
# Replace "413c:2105" in [ids] with the full unique ID from step 2.

# 4. Install and reload:
sudo cp /opt/TGW/src/trader-grims-warehouse/etc/interfaces/keyd/tgw-macroboard.conf /etc/keyd/
sudo systemctl reload keyd
# OR use the unified installer:
# sudo bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/install.sh

# 5. Test: press Caps Lock on the macroboard → LED behaviour changes.
#    Highlight a SKU in any window → press g → notification should appear.
```

### Key map — TGW layer (Caps Lock to enter, ESC or Caps Lock to exit)

```
ITEM INFO & FIELDS          OPEN / VIEW
  g  Get summary → notify     o  Open folder (Dolphin)
  t  Title update (prompt)    i  Images (gwenview)
  l  Location update (prompt) j  JSON edit (konsole)
  v  Verified (mark In Stock)
  h  Hint → requeue identify  EBAY BROWSER
  u  set cUrrent item          e  eBay search by SKU
                               b  Browse listing (ebay.com/itm)
PIPELINE (in order)          S-e  Edit/revise listing
  1  ai_identify               f  Find sold comparables
  2  ebay_draft              S-s  Seller Hub overview
  3  ebay_price
  4  ebay_stage              ADMIN / SYSTEM
  5  publish                  k  health checK → notify
  p  Publish (same as 5)      q  Queue depths → notify
                              c  Catalog rebuild
PICKLIST                      d  stageD items list
  a  Add picklist line        w  Weight (USB scale → clipboard)
S-a  Add question line        y  whisper dictation (15s)
                              z  short dictation (7s)
LOCATION BULK                 x  suggest (plan inbox)
  m  Move all in location      r  Requeue --no-draft count
S-l  Open location folder
```
`S-` = Shift held. Navigation keys (Enter, arrows, F-keys, Backspace) pass through normally.

### Clipboard / fallback behaviour
- **Highlighted text** → used directly as the item argument (Wayland primary
  selection via `wl-paste --primary` — no Ctrl+C needed)
- **X11 fallback** → `xsel -o --primary`
- **Nothing selected** → `basename $(readlink /opt/TGW/CurrentItem)`
- Actions that need a value (title, location, hint) open a `kdialog` prompt
- Actions that produce output use `notify-send` (5s timeout)
- Actions that open things (Dolphin, browser, konsole) just open them

### Future: all-keyboard sub-layer
Once the macroboard layer is proven:
1. Copy the `[tgw_layer]` block from `tgw-macroboard.conf` into `default.conf`
2. Bind a chord on `[main]` to `swap(tgw_layer)` — e.g. `rightalt+space`
3. All four keyboards get single-chord TGW access; macroboard stays always-on

---

