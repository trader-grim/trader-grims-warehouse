## PP-MC-001 — Midnight Commander Admin Interface

### Vision
MC is the primary console administration tool for TGW — on the master machine, over SSH,
and on LTSP/satellite nodes. The half-height layout (catalog/item panes top, Claude Code
bottom) is the target working environment. MC was chosen for its Norton Commander lineage,
universal availability, zero-friction install, and suitability as both a primary interface
and a fallback when graphical tools aren't present. It is the first app installed on any new
system in this operation.

All writes go through `tgw-http` (the FastAPI service, PP-EDITOR-001) when available.
Reads use the local SQLite catalog and ItemData directly — MC works offline on any node.

### What exists (as of 2026-06-03)
**Built and installed (`/opt/TGW/mc/` + `~/.config/mc/`):**
- `tgwitem` extfs — browse SKU JSON as VFS: `meta.json`, `fields/` (one .txt per field), `photos/` (images/video). Implements list + copyout + run.
- `tgwcatalog` extfs — 55K+ items organised by location as a navigable VFS. Reads search-catalog.json.
- `tgwqueue` extfs — live PostgreSQL queue snapshot; subdirs per state, one file per job.
- `tgwhealth` extfs — platform health checks as named OK_/FAIL_ files.
- `tgwservices` extfs — systemd TGW service status.
- `tgw-mc-status.py` — F2 menu viewer: health, queue, services, catalog stats, item summary.
- `tgw-view-image.sh` — chafa renderer; forces `--format=symbols` for MC's ascii viewer.
- `mc.ext.ini` — file associations: SKU JSON → tgwitem VFS; sentinels → VFS; images/video → chafa.
- `mc.menu` — F2 menu: `v`=VFS guide, `h`=health, `q`=queue, `s`=services, `l`=catalog, `i`=item summary, `p`=image preview.
- `install-system-mc.sh` — system-wide installer (ext, menu, extfs scripts).

### Phase 1 — Fix what's broken ✅ COMPLETE (2026-06-03)
- ✅ `tgwitem cmd_run` for fields fixed: temp file → less shows field value (not raw archive JSON)
- ✅ `tgwcatalog` migrated to SQLite (`tgwcatalog.db`): list call now ~0.8s vs multi-second JSON load; falls back to search-catalog.json if DB absent
- ✅ `tgwservices` now enumerates all `tgw-worker@*` units dynamically via `systemctl list-units --output=json`; fixed infra list includes `tgw-http`
- ✅ `tgw-view-image.sh`: TERM/COLORTERM forced for MC viewer context; COLUMNS/LINES detection improved; chafa `--format=symbols` already correct
- ✅ `tgwitem cmd_run` for photos: added `--format=symbols --colors=full` to force Unicode half-block art (prevents sixel/kitty auto-detect)
- ✅ `tgwitem` copyout for photos: serves full ItemData JSON (richer than catalog row)
- Remaining known gap: **No copyin on tgwitem** — fields still read-only; `copyin` not implemented (Phase 2)
- Note: image viewing in MC's `%view{ascii}` may still need interactive tuning — chafa+MC ANSI rendering is terminal-dependent

### Phase 2 — Item editing
- Implement `copyin` in `tgwitem` — save edited field file back to item JSON; enqueue `catalog_rebuild`
- Add `ebay/` subdir to `tgwitem` VFS — `draft_listing/` and `ebay_offer/` fields; read-only first
- Add `pipeline/` subdir to `tgwitem` — current job state per queue for this SKU (live PG query)
- F2 menu actions inside `tgwitem` VFS: re-identify, re-draft, re-price, re-stage, set-hint — enqueues jobs via `tgw-http` API or direct state_machine call

### Phase 3 — eBay form + gallery
- `ebay/` subdir fields become editable via copyin (price, condition, aspects, title)
- Image gallery mode: inside `photos/`, F3 renders image with chafa; arrow keys navigate
- `tgwcatalog` → Enter on item → jump to `tgwitem` VFS for that SKU (via real path)
- Thumbnail preview in catalog listing (chafa in narrow column — feasibility TBD)

### Phase 4 — Universal admin extensions
- Queue action menu: from `tgwqueue` VFS, F2 on a dead_letter job → re-queue or cancel
- Health drill-down: from `tgwhealth` VFS, Enter on FAIL_ → show detail + suggested fix
- Log viewer: `tgwlogs` VFS — recent journalctl output per worker, filterable
- SSH-clean: all operations work with no X11 forwarding, no GUI dependencies

### PP-MC-002 — LTSP / satellite console nodes (later)
- Package MC config + sentinels + extfs scripts for deployment to LTSP fat clients
- Read-only satellite mode: reads local synced `tgwcatalog.db` + thumbnails; writes queue to master via `tgw-http` when reachable
- Installation playbook (Ansible or shell) for new node bootstrap
- **LTSP RemoteApps** (session 9 addition): expose TGW admin tools as LTSP RemoteApp sessions —
  single-application remote sessions that appear as local apps on thin clients and tablets.
  Use case: content admin on remote display stations without full Linux install. Evaluate:
  xrdp's RemoteApp mode, FreeRDP, or X2Go published applications as the transport layer.

---

