## Settled architecture
### Canonical NixOS flake (do not relitigate)
- **`~/tgw-flake` is the canonical flake** — production-verified Gen 25, 2026-06-25
- Lives in its **own separate git repo**, independent of the Python source repo
- Used for **nixos-anywhere** (fresh provisioning) and **day-to-day `nixos-rebuild switch`**
- The Python source repo (`trader-grims-warehouse`) no longer contains a flake — Python is deployed via venv (Option B)
- All `.nix` changes go to `~/tgw-flake`, not the Python source repo
- TGW-SNAPSHOT-0 (sda7) mounts automatically at boot via `fileSystems` with `nofail`
- All outstanding sync items ported as of 2026-06-25 (backup timers, home.nix, tgw-clipd, autostart, nix-ld, kitty/clipboard tools, tgw-rebuild alias)

### Do not relitigate
- tgw-api is the fence — all ItemData reads/writes go through it
- One folder per SKU — `ItemData/<SKU>/<SKU>.json` + media
- Python owns data state — tgw.source becomes thin one-line wrappers
- resolve() is the canonical selector engine
- Bulk-first — claim a set, operate on the set, return a summary
- Workers are thin — they ask tgw-api, never construct paths
- Output contract — every call returns one JSON object with an `ok` key
- SKU format `tgwYYYYMMDDHHMMSSs` — **18 chars**: date + time + 1-digit tenths; string-comparison sortable
### Queue decision (settled)
- Pure state-machine model — PostgreSQL is the single work ledger
- No filesystem `.job.json` path — the old launcher/filesystem queue retires
- systemd keeps worker processes alive; PostgreSQL decides what work is done
- Workers are interchangeable hands; intelligence lives in the ledger
- A shared `QueueWorker` base holds claim/lease/complete/fail — no worker hand-rolls SQL
- PostgreSQL is now load-bearing — health, backups, startup ordering matter
### Process liveness (settled)
- systemd templated units `tgw-worker@<queue>.service` — not a custom launcher
- `After=postgresql.service`, `Requires=postgresql.service` on all worker units
### Secrets (settled)
- One canonical `secrets_root` directory, resolved from `tgw-api-config.json`
- Directory lives outside repo tree (`/opt/TGW/secrets/`), `chmod 700`, files `chmod 600`, owned by `tgw`
- Every secret resolves from `secrets_root` — no hardcoded paths anywhere
- Token state, refresh tokens, eBay app/cert credentials, all future marketplace keys live here
### Satellite catalog (compact format now established)
- SQLite is the compact catalog format — master builds `tgwcatalog.db`; satellite carries a filtered subset
- Schema: indexed scalar columns (sku, title, location, status, price, qty, image) + full JSON `data` column
- Thumbnail cache at `catalog_root/thumbnails/<SKU>.jpg` — same path on master and satellite
- PP-ADD-001 (Phase 6) sync return path still needs design: dirty-flag / change-log per row, merge strategy
- Item schema and API fence design should not accidentally preclude a deferred/offline mode
### Control-plane / data-plane separation (settled principle, 2026-06-28)
Lightweight ownership/notification signals must never carry or block on data payloads. When
they are coupled, a data-transfer failure blocks the control channel — hanging the WM, freezing
the KVM bridge, deadlocking the event bus. When decoupled, failures are isolated to the data
path; the control path stays live.

This principle recurs across the stack and should be applied by default:

| Domain | Control plane | Data plane |
|--------|--------------|------------|
| Clipboard (Wayland) | `zwlr-data-control-v1` ownership notification | Content transfer on request |
| KVM (lan-mouse) | Focus/edge-crossing signals | Mouse/keyboard event stream, clipboard bytes |
| clipd.py | Unix socket subscriber push (event: clip changed) | Content pulled via `list`/`get` command |
| TGW event server | NATS lightweight event notification | Worker fetches payload from PostgreSQL/API |
| git-annex (future) | Git tree tracks file existence + SHA key (pointer) | Annex transfers actual file bytes on demand |

**git-annex application:** ItemData photos are the bulk of the dataset (tens of GB). git
tracking the manifest (which SKUs exist, which photos, checksums) without carrying the content
enables: partial clones on a1131, resumable remote backups, deduplication across archives, and
offline-capable satellite builds. git-annex sits above Syncthing in reliability because the
control plane (git) and data plane (annex special remotes) are independently auditable.
Evaluate as replacement or complement to Syncthing for ItemData/ItemArchive once LVM is expanded
(PP-LVM-001) — the date-partitioned annex remote design scales naturally to multi-TB.

### Catalog rebuild (settled pattern)
- Any worker that writes to ItemData enqueues a `catalog-rebuild` job — never calls `build_all_catalogs()` inline
- `catalog-rebuild` worker claims the job → calls `build_all_catalogs()` (JSON + SQLite + location tree) → succeeds
- Thumbnail rebuild is a separate `thumbnail-gen` job: takes a SKU, generates only that item's thumbnail (fast path)
- Full thumbnail sweep (`tgw build-thumbnails`) runs on demand or scheduled; per-SKU job runs after each intake
- Batching: `catalog-rebuild` jobs use `not_before = now + 30s` so rapid successive writes coalesce

